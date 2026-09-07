from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from .config import Settings
from .db import Database

LOGGER = logging.getLogger(__name__)
SYNC_USER_ENV = re.compile(r"^SYNC_USER\d+$")


class SyncSupervisor:
    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database
        self.child: subprocess.Popen[bytes] | None = None
        self.stopping = False
        self.generation = -1

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self._handle_stop)
        signal.signal(signal.SIGINT, self._handle_stop)
        self.settings.ensure_directories()
        self.database.migrate()
        backoff = 1

        while not self.stopping:
            generation = self.database.credential_generation()
            credentials = self.database.enabled_sync_credentials()

            if not credentials:
                self._stop_child()
                self.database.set_runtime_state("sync_service", "waiting-for-user")
                self.generation = generation
                self._pause(2)
                continue

            if self.child is not None and generation != self.generation:
                LOGGER.info("Sync credentials changed; reloading the sync service")
                self.database.set_runtime_state("sync_service", "reloading")
                self._stop_child()

            if self.child is None:
                self.child = self._start_child(credentials)
                self.generation = generation
                self.database.set_runtime_state("sync_service", "starting")
                self._pause(1)

            exit_code = self.child.poll()
            if exit_code is None:
                self.database.set_runtime_state("sync_service", "running")
                backoff = 1
                self._pause(2)
                continue

            LOGGER.error("Sync service exited with code %s", exit_code)
            self.database.set_runtime_state("sync_service", f"error:{exit_code}")
            self.child = None
            self._pause(backoff)
            backoff = min(backoff * 2, 30)

        self._stop_child()
        self.database.set_runtime_state("sync_service", "stopped")
        return 0

    def _start_child(self, credentials: list[tuple[str, str]]) -> subprocess.Popen[bytes]:
        env = {key: value for key, value in os.environ.items() if not SYNC_USER_ENV.match(key)}
        env.update(
            {
                "SYNC_HOST": "0.0.0.0",
                "SYNC_PORT": os.environ.get("ANKI_HUB_SYNC_PORT", "8081"),
                "SYNC_BASE": str(self.settings.sync_dir),
                "PASSWORDS_HASHED": "1",
            }
        )
        for index, (username, password_hash) in enumerate(credentials, start=1):
            env[f"SYNC_USER{index}"] = f"{username}:{password_hash}"

        command = os.environ.get("ANKI_HUB_SYNC_COMMAND")
        args = command.split() if command else [sys.executable, "-m", "anki.syncserver"]
        LOGGER.info("Starting official Anki sync service for %d user(s)", len(credentials))
        return subprocess.Popen(args, env=env, cwd=Path.cwd())

    def _stop_child(self) -> None:
        if self.child is None:
            return
        if self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                LOGGER.warning("Sync service did not stop gracefully; killing it")
                self.child.kill()
                self.child.wait(timeout=5)
        self.child = None

    def _handle_stop(self, _signal_number: int, _frame: object) -> None:
        self.stopping = True

    def _pause(self, seconds: int) -> None:
        deadline = time.monotonic() + seconds
        while not self.stopping and time.monotonic() < deadline:
            time.sleep(min(0.25, deadline - time.monotonic()))
