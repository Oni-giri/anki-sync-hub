from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest
from anki import sync_pb2
from anki.collection import Collection
from anki.errors import SyncError

from anki_sync_hub.automation import AutomationClient
from anki_sync_hub.db import Database, MCPPrincipal
from anki_sync_hub.security import derive_sync_host_key, hash_sync_password

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_NETWORK_TESTS") != "1",
    reason="set RUN_NETWORK_TESTS=1 to permit local loopback servers",
)


def _available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(port: int) -> None:
    health_url = f"http://127.0.0.1:{port}/health"
    for _ in range(100):
        try:
            if urllib.request.urlopen(health_url, timeout=0.2).status == 200:
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError("Official sync server did not become healthy")


@contextmanager
def _sync_server(tmp_path: Path):
    port = _available_port()
    password = "a long sync password"
    password_hash = hash_sync_password(password)
    env = os.environ.copy()
    env.update(
        {
            "SYNC_HOST": "127.0.0.1",
            "SYNC_PORT": str(port),
            "SYNC_BASE": str(tmp_path / "server"),
            "SYNC_USER1": f"alice:{password_hash}",
            "PASSWORDS_HASHED": "1",
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "anki.syncserver"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_health(port)
        yield port, password, password_hash
    finally:
        process.terminate()
        process.wait(timeout=10)


def _resolve_initial_sync(collection: Collection, auth) -> None:
    output = collection.sync_collection(auth, sync_media=False)
    if output.required == sync_pb2.SyncCollectionResponse.FULL_DOWNLOAD:
        collection.full_upload_or_download(
            auth=auth, server_usn=output.server_media_usn, upload=False
        )
    elif output.required == sync_pb2.SyncCollectionResponse.FULL_UPLOAD:
        collection.full_upload_or_download(
            auth=auth, server_usn=output.server_media_usn, upload=True
        )
    else:
        assert output.required in {
            sync_pb2.SyncCollectionResponse.NO_CHANGES,
            sync_pb2.SyncCollectionResponse.NORMAL_SYNC,
        }


def test_generated_phc_hash_authenticates_with_official_server(tmp_path: Path) -> None:
    with _sync_server(tmp_path) as (port, password, _password_hash):
        collection = Collection(str(tmp_path / "client.anki2"))
        try:
            auth = collection.sync_login("alice", password, f"http://127.0.0.1:{port}/")
            assert auth.hkey
        finally:
            collection.close()


def test_automation_client_mutates_through_sync_protocol(tmp_path: Path) -> None:
    with _sync_server(tmp_path) as (port, password, password_hash):
        endpoint = f"http://127.0.0.1:{port}/"
        seed = Collection(str(tmp_path / "seed.anki2"))
        auth = seed.sync_login("alice", password, endpoint)
        model = seed.models.by_name("Basic")
        assert model is not None
        note = seed.new_note(model)
        note["Front"] = "Seed question"
        note["Back"] = "Seed answer"
        deck_id = seed.decks.id("Seed", create=True)
        assert deck_id is not None
        seed.add_note(note, deck_id)
        seed.save()
        _resolve_initial_sync(seed, auth)
        seed.close()

        database = Database(tmp_path / "control" / "control.db")
        database.migrate()
        host_key = derive_sync_host_key("alice", password_hash)
        user = database.create_sync_user("alice", password_hash, host_key)
        principal = MCPPrincipal(1, user.id, user.username, host_key, frozenset({"read", "write"}))
        automation = AutomationClient(tmp_path / "automation", endpoint, database)

        assert "Seed" in {deck["name"] for deck in automation.list_decks(principal)}
        created = automation.create_note(
            principal,
            "Seed",
            {"Front": "Automation question", "Back": "Automation answer"},
            tags=["mcp"],
        )
        assert created["cardCount"] == 1

        verifier = Collection(str(tmp_path / "verifier.anki2"))
        try:
            verifier_auth = verifier.sync_login("alice", password, endpoint)
            _resolve_initial_sync(verifier, verifier_auth)
            note_ids = verifier.find_notes('"Automation question"')
            assert len(note_ids) == 1
            assert verifier.get_note(note_ids[0])["Back"] == "Automation answer"
        finally:
            verifier.close()


def test_automation_client_bootstraps_from_empty_server(tmp_path: Path) -> None:
    with _sync_server(tmp_path) as (port, _password, password_hash):
        endpoint = f"http://127.0.0.1:{port}/"
        database = Database(tmp_path / "control" / "control.db")
        database.migrate()
        host_key = derive_sync_host_key("alice", password_hash)
        user = database.create_sync_user("alice", password_hash, host_key)
        principal = MCPPrincipal(1, user.id, user.username, host_key, frozenset({"read", "write"}))
        automation = AutomationClient(tmp_path / "automation", endpoint, database)

        assert "Default" in {deck["name"] for deck in automation.list_decks(principal)}
        assert automation.create_deck(principal, "Created through API")["name"] == (
            "Created through API"
        )


def test_supervisor_reloads_password_without_container_restart(tmp_path: Path) -> None:
    port = _available_port()
    data_dir = tmp_path / "hub"
    database = Database(data_dir / "control" / "control.db")
    database.migrate()
    old_password = "the first long password"
    old_hash = hash_sync_password(old_password)
    user = database.create_sync_user("alice", old_hash, derive_sync_host_key("alice", old_hash))
    env = os.environ.copy()
    env.update(
        {
            "ANKI_HUB_DATA_DIR": str(data_dir),
            "ANKI_HUB_SYNC_PORT": str(port),
        }
    )
    supervisor = subprocess.Popen(
        [sys.executable, "-m", "anki_sync_hub.cli", "sync-supervisor"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_health(port)
        first_client = Collection(str(tmp_path / "first-client.anki2"))
        try:
            assert first_client.sync_login("alice", old_password, f"http://127.0.0.1:{port}/").hkey
        finally:
            first_client.close()

        new_password = "the replacement long password"
        new_hash = hash_sync_password(new_password)
        assert database.update_sync_password(
            user.id, new_hash, derive_sync_host_key("alice", new_hash)
        )

        for attempt in range(50):
            candidate = Collection(str(tmp_path / f"reload-client-{attempt}.anki2"))
            try:
                candidate.sync_login("alice", new_password, f"http://127.0.0.1:{port}/")
                break
            except Exception:
                time.sleep(0.1)
            finally:
                candidate.close()
        else:
            raise AssertionError("Supervisor did not reload the new password")

        rejected = Collection(str(tmp_path / "rejected-client.anki2"))
        try:
            with pytest.raises(SyncError):
                rejected.sync_login("alice", old_password, f"http://127.0.0.1:{port}/")
        finally:
            rejected.close()
    finally:
        supervisor.terminate()
        supervisor.wait(timeout=15)
