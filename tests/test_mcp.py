from pathlib import Path

from starlette.testclient import TestClient

from anki_sync_hub.automation import AutomationClient
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


def test_rest_api_uses_access_token_scopes(tmp_path: Path, monkeypatch) -> None:
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
    read_token = "ash_read_token"
    write_token = "ash_write_token"
    database.create_mcp_token(user.id, "reader", read_token[:12], hash_token(read_token), {"read"})
    database.create_mcp_token(
        user.id,
        "writer",
        write_token[:12],
        hash_token(write_token),
        {"read", "write"},
    )

    monkeypatch.setattr(
        AutomationClient,
        "list_decks",
        lambda self, principal: [{"id": 1, "name": f"Deck for {principal.username}"}],
    )
    monkeypatch.setattr(
        AutomationClient,
        "create_deck",
        lambda self, principal, name: {"id": 2, "name": name},
    )
    monkeypatch.setattr(
        AutomationClient,
        "search_notes",
        lambda self, principal, query, limit: [
            {"id": 3, "fields": {"Front": query}, "tags": [str(limit)]}
        ],
    )
    monkeypatch.setattr(
        AutomationClient,
        "create_note",
        lambda self, principal, deck, fields, note_type, tags: {
            "noteId": 4,
            "cardCount": 1,
            "deck": deck,
        },
    )

    app = create_mcp_app(settings)
    read_headers = {"Authorization": f"Bearer {read_token}"}
    write_headers = {"Authorization": f"Bearer {write_token}"}
    with TestClient(app) as client:
        assert client.get("/api/v1/decks").status_code == 401
        decks = client.get("/api/v1/decks", headers=read_headers)
        forbidden = client.post("/api/v1/decks", headers=read_headers, json={"name": "Languages"})
        created_deck = client.post(
            "/api/v1/decks", headers=write_headers, json={"name": "Languages"}
        )
        notes = client.get(
            "/api/v1/notes",
            headers=read_headers,
            params={"query": "deck:Languages", "limit": 10},
        )
        created_note = client.post(
            "/api/v1/notes",
            headers=write_headers,
            json={
                "deck": "Languages",
                "fields": {"Front": "Hello", "Back": "Bonjour"},
                "tags": ["api"],
            },
        )
        openapi = client.get("/api/v1/openapi.json", headers=read_headers)

    assert decks.status_code == 200
    assert decks.json() == [{"id": 1, "name": "Deck for alice"}]
    assert forbidden.status_code == 403
    assert created_deck.status_code == 201
    assert created_deck.json() == {"id": 2, "name": "Languages"}
    assert notes.status_code == 200
    assert notes.json()[0]["fields"]["Front"] == "deck:Languages"
    assert created_note.status_code == 201
    assert created_note.json() == {"noteId": 4, "cardCount": 1, "deck": "Languages"}
    assert openapi.status_code == 200
    assert set(openapi.json()["paths"]) == {"/", "/decks", "/notes"}
