from __future__ import annotations

import contextvars
from typing import Any

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from . import __version__
from .automation import AutomationClient
from .config import Settings
from .db import Database, MCPPrincipal
from .security import hash_token

CURRENT_PRINCIPAL: contextvars.ContextVar[MCPPrincipal | None] = contextvars.ContextVar(
    "mcp_principal", default=None
)


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
                {"error": "A valid MCP bearer token is required."},
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
    return BearerTokenMiddleware(mcp_app, database)
