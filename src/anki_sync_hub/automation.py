from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from anki import sync_pb2
from anki.collection import Collection
from anki.sync import SyncAuth

from .db import Database, MCPPrincipal

T = TypeVar("T")


class AutomationConflict(RuntimeError):
    pass


class AutomationClient:
    """Operate on isolated client collections and synchronize via Anki's protocol."""

    def __init__(self, data_dir: Path, sync_endpoint: str, database: Database):
        self.data_dir = data_dir
        self.sync_endpoint = f"{sync_endpoint.rstrip('/')}/"
        self.database = database
        self._locks: dict[int, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def list_decks(self, principal: MCPPrincipal) -> list[dict[str, str | int]]:
        def operation(collection: Collection) -> list[dict[str, str | int]]:
            return [
                {"id": deck.id, "name": deck.name}
                for deck in collection.decks.all_names_and_ids(include_filtered=False)
            ]

        return self._run(principal, "list_decks", operation, mutate=False)

    def create_deck(self, principal: MCPPrincipal, name: str) -> dict[str, str | int]:
        name = name.strip()
        if not name or len(name) > 250:
            raise ValueError("Deck name must contain 1-250 characters.")

        def operation(collection: Collection) -> dict[str, str | int]:
            deck_id = collection.decks.id(name, create=True)
            if deck_id is None:
                raise RuntimeError("Anki did not create the deck.")
            return {"id": deck_id, "name": collection.decks.name(deck_id)}

        return self._run(principal, "create_deck", operation, mutate=True)

    def search_notes(
        self, principal: MCPPrincipal, query: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("Limit must be between 1 and 100.")

        def operation(collection: Collection) -> list[dict[str, Any]]:
            note_ids = collection.find_notes(query)[:limit]
            notes = []
            for note_id in note_ids:
                note = collection.get_note(note_id)
                notes.append(
                    {
                        "id": note.id,
                        "fields": dict(note.items()),
                        "tags": list(note.tags),
                    }
                )
            return notes

        return self._run(principal, "search_notes", operation, mutate=False)

    def create_note(
        self,
        principal: MCPPrincipal,
        deck: str,
        fields: dict[str, str],
        note_type: str = "Basic",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        if not fields:
            raise ValueError("At least one note field is required.")
        if len(fields) > 100 or any(len(value) > 100_000 for value in fields.values()):
            raise ValueError("The note exceeds the supported field limits.")

        def operation(collection: Collection) -> dict[str, Any]:
            model = collection.models.by_name(note_type)
            if model is None:
                raise ValueError(f"Unknown note type: {note_type}")
            deck_id = collection.decks.id(deck.strip(), create=True)
            if deck_id is None:
                raise RuntimeError("Anki did not create or resolve the deck.")
            note = collection.new_note(model)
            unknown = set(fields) - set(note.keys())
            if unknown:
                raise ValueError(f"Unknown fields for {note_type}: {', '.join(sorted(unknown))}")
            for field_name, value in fields.items():
                note[field_name] = value
            note.tags = tags or []
            changes = collection.add_note(note, deck_id)
            return {"noteId": note.id, "cardCount": changes.count, "deck": deck}

        return self._run(principal, "create_note", operation, mutate=True)

    def _run(
        self,
        principal: MCPPrincipal,
        action: str,
        operation: Callable[[Collection], T],
        *,
        mutate: bool,
    ) -> T:
        lock = self._lock_for(principal.sync_user_id)
        with lock:
            user_dir = self.data_dir / str(principal.sync_user_id)
            user_dir.mkdir(parents=True, exist_ok=True)
            collection_path = user_dir / "collection.anki2"
            collection = Collection(str(collection_path))
            auth = SyncAuth(hkey=principal.host_key, endpoint=self.sync_endpoint)
            try:
                collection = self._synchronize(collection, collection_path, auth)
                result = operation(collection)
                if mutate:
                    collection.save()
                    collection = self._synchronize(collection, collection_path, auth)
                    self.database.add_audit_log(
                        principal.sync_user_id,
                        f"mcp-token:{principal.token_id}",
                        action,
                        json.dumps({"result": result}, separators=(",", ":"), default=str),
                    )
                return result
            finally:
                collection.close()

    @staticmethod
    def _synchronize(collection: Collection, collection_path: Path, auth: SyncAuth) -> Collection:
        output = collection.sync_collection(auth, sync_media=False)
        if output.required in {
            sync_pb2.SyncCollectionResponse.NO_CHANGES,
            sync_pb2.SyncCollectionResponse.NORMAL_SYNC,
        }:
            return collection
        if output.required == sync_pb2.SyncCollectionResponse.FULL_DOWNLOAD:
            collection.full_upload_or_download(
                auth=auth, server_usn=output.server_media_usn, upload=False
            )
            collection.close()
            return Collection(str(collection_path))
        if output.required == sync_pb2.SyncCollectionResponse.FULL_UPLOAD:
            collection.full_upload_or_download(
                auth=auth, server_usn=output.server_media_usn, upload=True
            )
            return collection
        raise AutomationConflict(
            "Anki requires a full-sync choice. Resolve it in the browser before retrying."
        )

    def _lock_for(self, sync_user_id: int) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(sync_user_id, threading.Lock())
