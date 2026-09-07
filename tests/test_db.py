from pathlib import Path

from anki_sync_hub.db import Database


def test_database_migration_and_credential_generation(tmp_path: Path) -> None:
    database = Database(tmp_path / "control.db")
    database.migrate()
    database.migrate()

    assert not database.owner_exists()
    assert database.credential_generation() == 0

    user = database.create_sync_user("alice", "hash")
    assert user.enabled
    assert database.credential_generation() == 1
    assert database.enabled_sync_credentials() == [("alice", "hash")]

    assert database.set_sync_user_enabled(user.id, False)
    assert database.credential_generation() == 2
    assert database.enabled_sync_credentials() == []


def test_mcp_token_authentication_and_revocation(tmp_path: Path) -> None:
    database = Database(tmp_path / "control.db")
    database.migrate()
    user = database.create_sync_user("alice", "hash", "host-key")
    token_id = database.create_mcp_token(
        user.id, "agent", "ash_prefix", "token-hash", {"read", "write"}
    )

    principal = database.authenticate_mcp_token("token-hash")
    assert principal is not None
    assert principal.host_key == "host-key"
    assert principal.scopes == {"read", "write"}
    assert database.revoke_mcp_token(token_id)
    assert database.authenticate_mcp_token("token-hash") is None


def test_proxy_metrics_are_aggregated(tmp_path: Path) -> None:
    database = Database(tmp_path / "control.db")
    database.migrate()
    database.record_proxy_metric("/sync", 200, 10, 20, 5)
    database.record_proxy_metric("/sync", 204, 30, 40, 7)

    assert database.metrics() == [
        {
            "route": "/sync",
            "status_class": 2,
            "requests": 2,
            "request_bytes": 40,
            "response_bytes": 60,
            "latency_ms_total": 12,
            "updated_at": database.metrics()[0]["updated_at"],
        }
    ]
