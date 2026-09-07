from pathlib import Path

from fastapi.testclient import TestClient

from anki_sync_hub.config import Settings
from anki_sync_hub.main import SESSION_COOKIE, create_app


def test_first_run_login_and_sync_user(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        listen_host="127.0.0.1",
        listen_port=8080,
        sync_internal_url="http://127.0.0.1:9",
        session_ttl_seconds=3600,
        cookie_secure=False,
    )
    app = create_app(settings)
    origin = {"Origin": "http://testserver"}

    with TestClient(app) as client:
        assert client.get("/api/setup/status").json() == {"configured": False}
        assert client.get("/api/status").status_code == 401

        setup = client.post(
            "/api/setup",
            headers=origin,
            json={"username": "owner", "password": "correct horse battery staple"},
        )
        assert setup.status_code == 201
        assert SESSION_COOKIE in client.cookies

        created = client.post(
            "/api/sync-users",
            headers=origin,
            json={"username": "alice@example.com", "password": "another long password"},
        )
        assert created.status_code == 201
        assert created.json()["username"] == "alice@example.com"

        token = client.post(
            "/api/mcp-tokens",
            headers=origin,
            json={
                "sync_user_id": created.json()["id"],
                "name": "test agent",
                "scopes": ["read", "write"],
            },
        )
        assert token.status_code == 201
        assert token.json()["token"].startswith("ash_")
        assert len(client.get("/api/mcp-tokens").json()) == 1

        status_response = client.get("/api/status")
        assert status_response.status_code == 200
        assert status_response.json()["syncUsers"][0]["username"] == "alice@example.com"

        assert client.post("/api/logout", headers=origin, json={}).status_code == 200
        assert client.get("/api/status").status_code == 401


def test_admin_api_rejects_cross_origin_request(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        listen_host="127.0.0.1",
        listen_port=8080,
        sync_internal_url="http://127.0.0.1:9",
        session_ttl_seconds=3600,
        cookie_secure=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/setup",
            headers={"Origin": "http://attacker.invalid"},
            json={"username": "owner", "password": "correct horse battery staple"},
        )
    assert response.status_code == 403
