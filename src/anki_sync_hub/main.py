from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import Settings
from .db import Database
from .proxy import proxy_sync_request
from .schemas import (
    LoginRequest,
    MCPTokenCreate,
    OwnerSetup,
    SyncPasswordUpdate,
    SyncUserCreate,
    SyncUserEnabledUpdate,
)
from .security import (
    derive_sync_host_key,
    hash_owner_password,
    hash_sync_password,
    hash_token,
    new_api_token,
    new_session_token,
    validate_password,
    validate_sync_username,
    verify_owner_password,
)

SESSION_COOKIE = "anki_hub_session"
STATIC_DIR = Path(__file__).with_name("static")


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _bootstrap_owner(database: Database, settings: Settings) -> None:
    username = settings.admin_username
    password = settings.admin_password
    if (username is None) != (password is None):
        raise RuntimeError(
            "ANKI_HUB_ADMIN_USERNAME and ANKI_HUB_ADMIN_PASSWORD must be set together."
        )
    if username is None or database.owner_exists():
        return

    username = username.strip()
    if not username or len(username) > 128:
        raise RuntimeError("ANKI_HUB_ADMIN_USERNAME must contain 1-128 characters.")
    try:
        password_hash = hash_owner_password(password)
        database.create_owner(username, password_hash)
    except ValueError as error:
        raise RuntimeError(f"Invalid bootstrap administrator credentials: {error}") from error
    except sqlite3.IntegrityError:
        # Another web process may have completed first-run initialization.
        pass


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.migrate()
    _bootstrap_owner(database, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.http = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=None, write=None, pool=10),
            follow_redirects=False,
        )
        yield
        await app.state.http.aclose()

    app = FastAPI(
        title="Anki Sync Hub",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.database = database

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.url.path.startswith(
            "/api/"
        ):
            origin = request.headers.get("origin")
            host = request.headers.get("host")
            if not origin or not host or urlsplit(origin).netloc != host:
                return Response("Origin does not match this server.", status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    def require_owner(session: str | None = Cookie(default=None, alias=SESSION_COOKIE)) -> None:
        if not session or not database.session_valid(hash_token(session)):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in required."
            )

    @app.get("/api/setup/status")
    async def setup_status() -> dict[str, bool]:
        return {"configured": database.owner_exists()}

    @app.post("/api/setup", status_code=201)
    async def setup(payload: OwnerSetup, response: Response) -> dict[str, str]:
        if database.owner_exists():
            raise HTTPException(status_code=409, detail="Setup is already complete.")
        username = payload.username.strip()
        if not username or len(username) > 128:
            raise HTTPException(status_code=422, detail="Invalid owner username.")
        try:
            password_hash = hash_owner_password(payload.password)
            database.create_owner(username, password_hash)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except sqlite3.IntegrityError as error:
            raise HTTPException(status_code=409, detail="Setup is already complete.") from error
        token, token_hash = new_session_token()
        database.create_session(token_hash, settings.session_ttl_seconds)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="strict",
            max_age=settings.session_ttl_seconds,
            path="/",
        )
        return {"status": "configured"}

    @app.post("/api/login")
    async def login(payload: LoginRequest, response: Response) -> dict[str, str]:
        row = database.owner_credentials(payload.username.strip())
        if row is None or not verify_owner_password(payload.password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid username or password.")
        token, token_hash = new_session_token()
        database.create_session(token_hash, settings.session_ttl_seconds)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="strict",
            max_age=settings.session_ttl_seconds,
            path="/",
        )
        return {"status": "authenticated"}

    @app.post("/api/logout", dependencies=[Depends(require_owner)])
    async def logout(
        response: Response,
        session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, str]:
        if session:
            database.delete_session(hash_token(session))
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"status": "signed-out"}

    @app.get("/api/status", dependencies=[Depends(require_owner)])
    async def application_status() -> dict[str, object]:
        sync_state = database.runtime_state("sync_service")
        return {
            "version": __version__,
            "syncService": sync_state[0] if sync_state else "unknown",
            "syncStateUpdatedAt": sync_state[1] if sync_state else None,
            "syncUsers": [asdict(user) for user in database.list_sync_users()],
            "storage": {
                "syncBytes": _directory_size(settings.sync_dir),
                "automationBytes": _directory_size(settings.automation_dir),
            },
            "metrics": database.metrics(),
        }

    @app.post("/api/sync-users", status_code=201, dependencies=[Depends(require_owner)])
    async def create_sync_user(payload: SyncUserCreate) -> dict[str, object]:
        try:
            username = validate_sync_username(payload.username)
            password_hash = hash_sync_password(payload.password)
            host_key = derive_sync_host_key(username, password_hash)
            user = database.create_sync_user(username, password_hash, host_key)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except sqlite3.IntegrityError as error:
            raise HTTPException(
                status_code=409, detail="That sync username already exists."
            ) from error
        return asdict(user)

    @app.put(
        "/api/sync-users/{user_id}/password",
        dependencies=[Depends(require_owner)],
    )
    async def update_sync_password(user_id: int, payload: SyncPasswordUpdate) -> dict[str, str]:
        username = database.sync_username(user_id)
        if username is None:
            raise HTTPException(status_code=404, detail="Sync user not found.")
        try:
            validate_password(payload.password)
            password_hash = hash_sync_password(payload.password)
            host_key = derive_sync_host_key(username, password_hash)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if not database.update_sync_password(user_id, password_hash, host_key):
            raise HTTPException(status_code=404, detail="Sync user not found.")
        return {"status": "password-updated"}

    @app.put(
        "/api/sync-users/{user_id}/enabled",
        dependencies=[Depends(require_owner)],
    )
    async def set_sync_user_enabled(user_id: int, payload: SyncUserEnabledUpdate) -> dict[str, str]:
        if not database.set_sync_user_enabled(user_id, payload.enabled):
            raise HTTPException(status_code=404, detail="Sync user not found.")
        return {"status": "enabled" if payload.enabled else "disabled"}

    @app.get("/api/mcp-tokens", dependencies=[Depends(require_owner)])
    async def list_mcp_tokens() -> list[dict[str, int | str | None]]:
        return database.list_mcp_tokens()

    @app.post("/api/mcp-tokens", status_code=201, dependencies=[Depends(require_owner)])
    async def create_mcp_token(payload: MCPTokenCreate) -> dict[str, object]:
        raw_token, prefix, token_hash = new_api_token()
        try:
            token_id = database.create_mcp_token(
                payload.sync_user_id,
                payload.name.strip(),
                prefix,
                token_hash,
                set(payload.scopes),
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {
            "id": token_id,
            "token": raw_token,
            "prefix": prefix,
            "scopes": sorted(payload.scopes),
        }

    @app.delete("/api/mcp-tokens/{token_id}", dependencies=[Depends(require_owner)])
    async def revoke_mcp_token(token_id: int) -> dict[str, str]:
        if not database.revoke_mcp_token(token_id):
            raise HTTPException(status_code=404, detail="Active MCP token not found.")
        return {"status": "revoked"}

    async def forward_sync(namespace: str, path: str, request: Request):
        return await proxy_sync_request(
            request,
            namespace,
            path,
            app.state.http,
            database,
            settings.sync_internal_url,
        )

    @app.api_route("/sync/{path:path}", methods=["GET", "POST"])
    async def collection_sync_proxy(path: str, request: Request):
        return await forward_sync("sync", path, request)

    @app.api_route("/msync/{path:path}", methods=["GET", "POST"])
    async def media_sync_proxy(path: str, request: Request):
        return await forward_sync("msync", path, request)

    async def forward_mcp(path: str, request: Request):
        return await proxy_sync_request(
            request,
            "mcp",
            path,
            app.state.http,
            database,
            settings.mcp_internal_url,
        )

    @app.api_route("/mcp", methods=["GET", "POST", "DELETE"])
    async def mcp_proxy_root(request: Request):
        return await forward_mcp("", request)

    @app.api_route("/mcp/{path:path}", methods=["GET", "POST", "DELETE"])
    async def mcp_proxy(path: str, request: Request):
        return await forward_mcp(path, request)

    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/{path:path}", include_in_schema=False)
    async def web_app(path: str) -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app
