from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    listen_host: str
    listen_port: int
    sync_internal_url: str
    session_ttl_seconds: int
    cookie_secure: bool
    mcp_internal_url: str = "http://mcp:8082"
    mcp_port: int = 8082
    admin_username: str | None = None
    admin_password: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(os.environ.get("ANKI_HUB_DATA_DIR", "./data")).resolve()
        return cls(
            data_dir=data_dir,
            listen_host=os.environ.get("ANKI_HUB_HOST", "0.0.0.0"),
            listen_port=int(os.environ.get("ANKI_HUB_PORT", "8080")),
            sync_internal_url=os.environ.get("ANKI_HUB_SYNC_URL", "http://sync:8081").rstrip("/"),
            session_ttl_seconds=int(
                os.environ.get("ANKI_HUB_SESSION_TTL_SECONDS", str(12 * 60 * 60))
            ),
            cookie_secure=os.environ.get("ANKI_HUB_COOKIE_SECURE", "false").lower()
            in {"1", "true", "yes"},
            mcp_internal_url=os.environ.get("ANKI_HUB_MCP_URL", "http://mcp:8082").rstrip("/"),
            mcp_port=int(os.environ.get("ANKI_HUB_MCP_PORT", "8082")),
            admin_username=os.environ.get("ANKI_HUB_ADMIN_USERNAME"),
            admin_password=os.environ.get("ANKI_HUB_ADMIN_PASSWORD"),
        )

    @property
    def control_dir(self) -> Path:
        return self.data_dir / "control"

    @property
    def database_path(self) -> Path:
        return self.control_dir / "control.db"

    @property
    def sync_dir(self) -> Path:
        return self.data_dir / "sync"

    @property
    def automation_dir(self) -> Path:
        return self.data_dir / "automation"

    def ensure_directories(self) -> None:
        for path in (self.control_dir, self.sync_dir, self.automation_dir):
            path.mkdir(parents=True, exist_ok=True)
