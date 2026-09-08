from __future__ import annotations

import contextvars
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any

import anyio
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount
from starlette.types import ASGIApp, Receive, Scope, Send

from . import __version__
from .automation import AutomationClient, AutomationConflict
from .config import Settings
from .db import Database, MCPPrincipal
from .schemas import DeckCreate, NoteCreate
from .security import hash_token

CURRENT_PRINCIPAL: contextvars.ContextVar[MCPPrincipal | None] = contextvars.ContextVar(
    "mcp_principal", default=None
)
BEARER_SCHEME = HTTPBearer(auto_error=False)


class BearerTokenMiddleware:
    def __init__(self, app: ASGIApp, database: Database):
        self.app = app
        self.database = database

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        scheme, _, raw_token = authorization.partition(" ")
        principal = None
        if scheme.lower() == "bearer" and raw_token:
            principal = self.database.authenticate_mcp_token(hash_token(raw_token))
        if principal is None:
            response = JSONResponse(
                {"error": "A valid access-token bearer token is required."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        token = CURRENT_PRINCIPAL.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            CURRENT_PRINCIPAL.reset(token)


def _principal(scope: str) -> MCPPrincipal:
    principal = CURRENT_PRINCIPAL.get()
    if principal is None:
        raise PermissionError("MCP authentication context is missing.")
    if scope not in principal.scopes:
        raise PermissionError(f"This token does not have the {scope!r} scope.")
    return principal


def _rest_principal(scope: str) -> MCPPrincipal:
    try:
        return _principal(scope)
    except PermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error


def _require_read(
    _credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(BEARER_SCHEME)],
) -> MCPPrincipal:
    return _rest_principal("read")


def _require_write(
    _credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(BEARER_SCHEME)],
) -> MCPPrincipal:
    return _rest_principal("write")


ReadPrincipal = Annotated[MCPPrincipal, Depends(_require_read)]
WritePrincipal = Annotated[MCPPrincipal, Depends(_require_write)]


async def _run_automation[T](operation: Callable[..., T], *args: object) -> T:
    try:
        return await anyio.to_thread.run_sync(operation, *args)
    except AutomationConflict as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error


def _create_rest_api(automation: AutomationClient) -> FastAPI:
    api = FastAPI(
        title="Anki Sync Hub REST API",
        description="Manage Anki decks and notes through the normal synchronization protocol.",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url="/openapi.json",
    )

    @api.get("/")
    async def api_index(principal: ReadPrincipal) -> dict[str, object]:
        return {
            "name": "Anki Sync Hub REST API",
            "version": __version__,
            "syncUser": principal.username,
            "openapi": "/api/v1/openapi.json",
            "endpoints": ["GET /decks", "POST /decks", "GET /notes", "POST /notes"],
        }

    @api.get("/decks")
    async def list_decks(
        principal: ReadPrincipal,
    ) -> list[dict[str, str | int]]:
        return await _run_automation(automation.list_decks, principal)

    @api.post("/decks", status_code=status.HTTP_201_CREATED)
    async def create_deck(
        payload: DeckCreate,
        principal: WritePrincipal,
    ) -> dict[str, str | int]:
        return await _run_automation(automation.create_deck, principal, payload.name)

    @api.get("/notes")
    async def search_notes(
        query: Annotated[str, Query(min_length=1)],
        principal: ReadPrincipal,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[dict[str, Any]]:
        return await _run_automation(automation.search_notes, principal, query, limit)

    @api.post("/notes", status_code=status.HTTP_201_CREATED)
    async def create_note(
        payload: NoteCreate,
        principal: WritePrincipal,
    ) -> dict[str, Any]:
        return await _run_automation(
            automation.create_note,
            principal,
            payload.deck,
            payload.fields,
            payload.note_type,
            payload.tags,
        )

    return api


def create_mcp_app(settings: Settings | None = None) -> ASGIApp:
    settings = settings or Settings.from_env()
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.migrate()
    automation = AutomationClient(settings.automation_dir, settings.sync_internal_url, database)
    server = MCPServer(
        name="anki-sync-hub",
        title="Anki Sync Hub",
        description="Safely manage a self-hosted Anki collection.",
        instructions=(
            "Read before changing data. Keep batches small. Never delete notes unless the user "
            "explicitly asked for deletion. Changes are synchronized through the Anki protocol."
        ),
        version=__version__,
    )

    @server.tool(
        annotations=ToolAnnotations(
            title="List Anki decks",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )
    )
    async def list_decks() -> list[dict[str, str | int]]:
        """List normal decks after pulling the latest collection state."""
        principal = _principal("read")
        return await anyio.to_thread.run_sync(automation.list_decks, principal)

    @server.tool(
        annotations=ToolAnnotations(
            title="Create Anki deck",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )
    )
    async def create_deck(name: str) -> dict[str, str | int]:
        """Create a deck, or return the existing deck with the same name."""
        principal = _principal("write")
        return await anyio.to_thread.run_sync(automation.create_deck, principal, name)

    @server.tool(
        annotations=ToolAnnotations(
            title="Search Anki notes",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )
    )
    async def search_notes(query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search notes using Anki's search syntax, returning at most 100 notes."""
        principal = _principal("read")
        return await anyio.to_thread.run_sync(automation.search_notes, principal, query, limit)

    @server.tool(
        annotations=ToolAnnotations(
            title="Create Anki note",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        )
    )
    async def create_note(
        deck: str,
        fields: dict[str, str],
        note_type: str = "Basic",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create one note, generate its cards, and synchronize the change."""
        principal = _principal("write")
        return await anyio.to_thread.run_sync(
            automation.create_note, principal, deck, fields, note_type, tags
        )

    mcp_app = server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        host="0.0.0.0",
        max_request_body_size=1024 * 1024,
    )

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = Starlette(
        routes=[
            Mount("/api/v1", app=_create_rest_api(automation)),
            Mount("/", app=mcp_app),
        ],
        lifespan=lifespan,
    )
    return BearerTokenMiddleware(app, database)
