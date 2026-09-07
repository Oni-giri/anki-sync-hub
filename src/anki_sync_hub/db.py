from __future__ import annotations

import fcntl
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class SyncUser:
    id: int
    username: str
    enabled: bool
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class MCPPrincipal:
    token_id: int
    sync_user_id: int
    username: str
    host_key: str
    scopes: frozenset[str]


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 15000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with (self.path.parent / ".migration.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations "
                    "(version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL)"
                )
                current = db.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                ).fetchone()[0]
                if current > SCHEMA_VERSION:
                    raise RuntimeError(
                        f"Database schema {current} is newer than supported {SCHEMA_VERSION}."
                    )
                if current < 1:
                    self._migration_1(db)
                    self._record_migration(db, 1)
                    current = 1
                if current < 2:
                    self._migration_2(db)
                    self._record_migration(db, 2)

    @staticmethod
    def _record_migration(db: sqlite3.Connection, version: int) -> None:
        db.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (version, int(time.time())),
        )

    @staticmethod
    def _migration_1(db: sqlite3.Connection) -> None:
        statements = (
            """CREATE TABLE owners (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )""",
            """CREATE TABLE sessions (
                token_hash TEXT PRIMARY KEY,
                owner_id INTEGER NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            )""",
            "CREATE INDEX sessions_expiry_idx ON sessions(expires_at)",
            """CREATE TABLE sync_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )""",
            """CREATE TABLE runtime_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )""",
            """INSERT INTO runtime_state(key, value, updated_at)
            VALUES ('credential_generation', '0', unixepoch())""",
            """CREATE TABLE proxy_metrics (
                route TEXT NOT NULL,
                status_class INTEGER NOT NULL,
                requests INTEGER NOT NULL DEFAULT 0,
                request_bytes INTEGER NOT NULL DEFAULT 0,
                response_bytes INTEGER NOT NULL DEFAULT 0,
                latency_ms_total INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(route, status_class)
            )""",
        )
        for statement in statements:
            db.execute(statement)

    @staticmethod
    def _migration_2(db: sqlite3.Connection) -> None:
        statements = (
            "ALTER TABLE sync_users ADD COLUMN host_key TEXT NOT NULL DEFAULT ''",
            """CREATE TABLE mcp_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sync_user_id INTEGER NOT NULL REFERENCES sync_users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                token_prefix TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                scopes TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                last_used_at INTEGER,
                revoked_at INTEGER
            )""",
            """CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sync_user_id INTEGER REFERENCES sync_users(id) ON DELETE SET NULL,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '{}',
                created_at INTEGER NOT NULL
            )""",
            "CREATE INDEX audit_log_created_idx ON audit_log(created_at DESC)",
        )
        for statement in statements:
            db.execute(statement)

    def owner_exists(self) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM owners WHERE id = 1").fetchone() is not None

    def create_owner(self, username: str, password_hash: str) -> None:
        now = int(time.time())
        with self.connect() as db:
            db.execute(
                "INSERT INTO owners(id, username, password_hash, created_at) VALUES (1, ?, ?, ?)",
                (username, password_hash, now),
            )

    def owner_credentials(self, username: str) -> sqlite3.Row | None:
        with self.connect() as db:
            return db.execute(
                "SELECT id, username, password_hash FROM owners WHERE username = ?",
                (username,),
            ).fetchone()

    def create_session(self, token_hash: str, ttl_seconds: int) -> None:
        now = int(time.time())
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            db.execute(
                "INSERT INTO sessions(token_hash, owner_id, created_at, expires_at) "
                "VALUES (?, 1, ?, ?)",
                (token_hash, now, now + ttl_seconds),
            )

    def session_valid(self, token_hash: str) -> bool:
        now = int(time.time())
        with self.connect() as db:
            row = db.execute(
                "SELECT 1 FROM sessions WHERE token_hash = ? AND expires_at > ?",
                (token_hash, now),
            ).fetchone()
            return row is not None

    def delete_session(self, token_hash: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def list_sync_users(self) -> list[SyncUser]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT id, username, enabled, created_at, updated_at "
                "FROM sync_users ORDER BY username COLLATE NOCASE"
            ).fetchall()
        return [
            SyncUser(
                id=row["id"],
                username=row["username"],
                enabled=bool(row["enabled"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def enabled_sync_credentials(self) -> list[tuple[str, str]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT username, password_hash FROM sync_users WHERE enabled = 1 ORDER BY id"
            ).fetchall()
        return [(row["username"], row["password_hash"]) for row in rows]

    def sync_username(self, user_id: int) -> str | None:
        with self.connect() as db:
            row = db.execute("SELECT username FROM sync_users WHERE id = ?", (user_id,)).fetchone()
        return row["username"] if row else None

    def create_sync_user(self, username: str, password_hash: str, host_key: str = "") -> SyncUser:
        now = int(time.time())
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO sync_users(username, password_hash, host_key, enabled, "
                "created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
                (username, password_hash, host_key, now, now),
            )
            self._bump_credentials(db, now)
            user_id = cursor.lastrowid
        return SyncUser(user_id, username, True, now, now)

    def update_sync_password(self, user_id: int, password_hash: str, host_key: str = "") -> bool:
        now = int(time.time())
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE sync_users SET password_hash = ?, host_key = ?, updated_at = ? "
                "WHERE id = ?",
                (password_hash, host_key, now, user_id),
            )
            if cursor.rowcount:
                self._bump_credentials(db, now)
            return bool(cursor.rowcount)

    def set_sync_user_enabled(self, user_id: int, enabled: bool) -> bool:
        now = int(time.time())
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE sync_users SET enabled = ?, updated_at = ? WHERE id = ?",
                (int(enabled), now, user_id),
            )
            if cursor.rowcount:
                self._bump_credentials(db, now)
            return bool(cursor.rowcount)

    @staticmethod
    def _bump_credentials(db: sqlite3.Connection, now: int) -> None:
        db.execute(
            "UPDATE runtime_state SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT), "
            "updated_at = ? WHERE key = 'credential_generation'",
            (now,),
        )

    def credential_generation(self) -> int:
        with self.connect() as db:
            row = db.execute(
                "SELECT value FROM runtime_state WHERE key = 'credential_generation'"
            ).fetchone()
        return int(row["value"])

    def set_runtime_state(self, key: str, value: str) -> None:
        now = int(time.time())
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO runtime_state(key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, now),
            )

    def runtime_state(self, key: str) -> tuple[str, int] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT value, updated_at FROM runtime_state WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        return row["value"], row["updated_at"]

    def record_proxy_metric(
        self,
        route: str,
        status_code: int,
        request_bytes: int,
        response_bytes: int,
        latency_ms: int,
    ) -> None:
        status_class = status_code // 100
        now = int(time.time())
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO proxy_metrics(
                    route, status_class, requests, request_bytes,
                    response_bytes, latency_ms_total, updated_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?)
                ON CONFLICT(route, status_class) DO UPDATE SET
                    requests = requests + 1,
                    request_bytes = request_bytes + excluded.request_bytes,
                    response_bytes = response_bytes + excluded.response_bytes,
                    latency_ms_total = latency_ms_total + excluded.latency_ms_total,
                    updated_at = excluded.updated_at
                """,
                (route, status_class, request_bytes, response_bytes, latency_ms, now),
            )

    def metrics(self) -> list[dict[str, int | str]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT route, status_class, requests, request_bytes, response_bytes, "
                "latency_ms_total, updated_at FROM proxy_metrics "
                "ORDER BY route, status_class"
            ).fetchall()
        return [dict(row) for row in rows]

    def create_mcp_token(
        self,
        sync_user_id: int,
        name: str,
        token_prefix: str,
        token_hash: str,
        scopes: set[str],
    ) -> int:
        now = int(time.time())
        with self.connect() as db:
            user = db.execute("SELECT 1 FROM sync_users WHERE id = ?", (sync_user_id,)).fetchone()
            if user is None:
                raise LookupError("Sync user not found")
            cursor = db.execute(
                "INSERT INTO mcp_tokens(sync_user_id, name, token_prefix, token_hash, scopes, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (sync_user_id, name, token_prefix, token_hash, " ".join(sorted(scopes)), now),
            )
            return int(cursor.lastrowid)

    def list_mcp_tokens(self) -> list[dict[str, int | str | None]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT t.id, t.sync_user_id, u.username, t.name, t.token_prefix, t.scopes, "
                "t.created_at, t.last_used_at, t.revoked_at FROM mcp_tokens t "
                "JOIN sync_users u ON u.id = t.sync_user_id ORDER BY t.created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def revoke_mcp_token(self, token_id: int) -> bool:
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE mcp_tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (int(time.time()), token_id),
            )
            return bool(cursor.rowcount)

    def authenticate_mcp_token(self, token_hash: str) -> MCPPrincipal | None:
        now = int(time.time())
        with self.connect() as db:
            row = db.execute(
                "SELECT t.id token_id, t.sync_user_id, u.username, u.host_key, t.scopes "
                "FROM mcp_tokens t JOIN sync_users u ON u.id = t.sync_user_id "
                "WHERE t.token_hash = ? AND t.revoked_at IS NULL AND u.enabled = 1",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE mcp_tokens SET last_used_at = ? WHERE id = ?", (now, row["token_id"])
            )
        return MCPPrincipal(
            token_id=row["token_id"],
            sync_user_id=row["sync_user_id"],
            username=row["username"],
            host_key=row["host_key"],
            scopes=frozenset(row["scopes"].split()),
        )

    def add_audit_log(self, sync_user_id: int, actor: str, action: str, detail: str = "{}") -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO audit_log(sync_user_id, actor, action, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (sync_user_id, actor, action, detail, int(time.time())),
            )
