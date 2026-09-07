from __future__ import annotations

import argparse
import logging

import uvicorn

from .config import Settings
from .db import Database
from .supervisor import SyncSupervisor


def main() -> int:
    parser = argparse.ArgumentParser(prog="anki-sync-hub")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("web", help="run the browser control plane and gateway")
    subparsers.add_parser("sync-supervisor", help="run the official sync service supervisor")
    subparsers.add_parser("mcp", help="run the authenticated MCP service")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings.from_env()

    if args.command == "web":
        from .main import create_app

        uvicorn.run(
            create_app(settings),
            host=settings.listen_host,
            port=settings.listen_port,
            proxy_headers=True,
            forwarded_allow_ips="*",
        )
        return 0

    if args.command == "mcp":
        from .mcp_server import create_mcp_app

        uvicorn.run(
            create_mcp_app(settings),
            host=settings.listen_host,
            port=settings.mcp_port,
            proxy_headers=False,
        )
        return 0

    database = Database(settings.database_path)
    return SyncSupervisor(settings, database).run()


if __name__ == "__main__":
    raise SystemExit(main())
