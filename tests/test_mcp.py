from pathlib import Path

from starlette.testclient import TestClient

from anki_sync_hub.config import Settings
from anki_sync_hub.db import Database
from anki_sync_hub.mcp_server import create_mcp_app
from anki_sync_hub.security import hash_token


def test_mcp_requires_token_and_lists_tools(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        listen_host="127.0.0.1",
        listen_port=8080,
        sync_internal_url="http://127.0.0.1:9",
        session_ttl_seconds=3600,
        cookie_secure=False,
    )
    database = Database(settings.database_path)
    database.migrate()
    user = database.create_sync_user("alice", "hash", "host-key")
    raw_token = "ash_test_token"
    database.create_mcp_token(
        user.id, "test", raw_token[:12], hash_token(raw_token), {"read", "write"}
    )
    app = create_mcp_app(settings)
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "0"},
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        },
    }
    headers = {
        "Accept": "application/json",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": "tools/list",
    }
    with TestClient(app) as client:
        assert client.post("/mcp", headers=headers, json=body).status_code == 401
        response = client.post(
            "/mcp",
            headers={**headers, "Authorization": f"Bearer {raw_token}"},
            json=body,
        )

    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert names == {"list_decks", "create_deck", "search_notes", "create_note"}
