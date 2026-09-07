# Anki Sync Hub

Anki Sync Hub is a self-hosted control plane for Anki's official sync server.
It is designed for a one-click Umbrel installation on a trusted local network
or a private Tailscale network.

The project is under active development. The first milestone provides:

- browser-based first-run setup and sync-user management;
- Anki's official, version-matched sync implementation;
- a same-origin gateway for `/sync/*` and `/msync/*`;
- basic health, storage, and request metrics;
- persistent configuration and collection storage;
- an MCP endpoint whose deck operations use a separate headless Anki client.

No user-specific values are built into the image. Accounts, sync credentials,
and MCP tokens are created in the browser and stored in the mounted data
directory.

## Development status

See [docs/EXECUTION_PLAN.md](docs/EXECUTION_PLAN.md) for the staged execution
plan and acceptance criteria.

The current vertical slice includes owner onboarding, sync-account management,
live credential reload, a streaming sync gateway, request/storage metrics, MCP
token management, and the first four MCP tools: `list_decks`, `create_deck`,
`search_notes`, and `create_note`.

## Local development

Python 3.12 or newer is required.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/ruff check .
.venv/bin/pytest
```

Run the portable container stack with:

```bash
docker compose up --build
```

Then open `http://localhost:8080`. Create an owner and a sync account in the
browser. Configure Anki's self-hosted sync server as
`http://localhost:8080/`.

For MCP, create a token in the UI and connect a Streamable HTTP client to
`http://localhost:8080/mcp` with this header:

```text
Authorization: Bearer ash_your_token
```

Loopback integration tests start a real official sync server and are opt-in:

```bash
RUN_NETWORK_TESTS=1 .venv/bin/pytest tests/test_sync_integration.py
```

## Security boundary

The application is intended for LAN or Tailscale access. The browser UI has
its own authentication and may additionally be protected by Umbrel's app
proxy. Sync and MCP routes use application-level credentials because Anki and
MCP clients cannot supply an Umbrel browser session.

Do not publish the HTTP service directly to the public internet. Put HTTPS,
Tailscale, or another authenticated private network in front of it.

When using Tailscale, connect through the Umbrel device's tailnet address or
MagicDNS name. Prefer Tailscale HTTPS when the client supports it. The app does
not need host networking or access to the Docker socket.

## Trademark

Anki is a registered trademark of Ankitects Pty Ltd. This project is not
affiliated with or endorsed by Ankitects.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).
