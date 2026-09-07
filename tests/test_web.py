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

        duplicate = client.post(
            "/api/sync-users",
            headers=origin,
            json={"username": "alice@example.com", "password": "another long password"},
        )
        assert duplicate.status_code == 409
        assert duplicate.json() == {"detail": "That sync username already exists."}

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


def test_admin_credentials_bootstrap_owner_and_are_not_reapplied(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        listen_host="127.0.0.1",
        listen_port=8080,
        sync_internal_url="http://127.0.0.1:9",
        session_ttl_seconds=3600,
        cookie_secure=False,
        admin_username="admin",
        admin_password="umbrel-generated-password",
    )
    app = create_app(settings)
    origin = {"Origin": "http://testserver"}

    with TestClient(app) as client:
        assert client.get("/api/setup/status").json() == {"configured": True}
        assert (
            client.post(
                "/api/setup",
                headers=origin,
                json={"username": "other", "password": "another long password"},
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/api/login",
                headers=origin,
                json={"username": "admin", "password": "umbrel-generated-password"},
            ).status_code
            == 200
        )

    changed_settings = Settings(
        data_dir=tmp_path,
        listen_host="127.0.0.1",
        listen_port=8080,
        sync_internal_url="http://127.0.0.1:9",
        session_ttl_seconds=3600,
        cookie_secure=False,
        admin_username="admin",
        admin_password="a-different-generated-password",
    )
    restarted_app = create_app(changed_settings)
    with TestClient(restarted_app) as client:
        assert (
            client.post(
                "/api/login",
                headers=origin,
                json={"username": "admin", "password": "umbrel-generated-password"},
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/login",
                headers=origin,
                json={"username": "admin", "password": "a-different-generated-password"},
            ).status_code
            == 401
        )


def test_partial_admin_bootstrap_configuration_fails(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        listen_host="127.0.0.1",
        listen_port=8080,
        sync_internal_url="http://127.0.0.1:9",
        session_ttl_seconds=3600,
        cookie_secure=False,
        admin_username="admin",
    )

    try:
        create_app(settings)
    except RuntimeError as error:
        assert "must be set together" in str(error)
    else:
        raise AssertionError("Partial administrator bootstrap must fail closed.")


def test_original_icon_is_served_as_svg(tmp_path: Path) -> None:
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
        page = client.get("/")
        icon = client.get("/assets/icon.svg")

    assert page.status_code == 200
    assert 'rel="icon" href="/assets/icon.svg"' in page.text
    assert 'id="sync-user-message"' in page.text
    assert 'id="mcp-guide"' in page.text
    assert "Streamable HTTP endpoint" in page.text
    assert "Authorization header" in page.text
    assert icon.status_code == 200
    assert icon.headers["content-type"].startswith("image/svg+xml")
    assert "Anki Sync Hub" in icon.text
