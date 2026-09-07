# Security policy

## Intended deployment

Anki Sync Hub is intended for a trusted local network or private Tailscale
network. Do not expose its plain HTTP endpoint directly to the public internet.
Use an authenticated private network and HTTPS for remote access.

The Umbrel browser proxy protects the UI, but `/sync/*`, `/msync/*`, and
`/mcp` must remain reachable by non-browser clients. Those routes therefore
use Anki sync credentials and scoped MCP bearer tokens respectively.

## Reporting

Do not open a public issue containing an exploit, token, collection content,
or personal information. Contact the maintainers privately through the
repository security-advisory feature once the canonical repository is
published.

## Secrets

- MCP tokens are displayed once and stored only as SHA-256 hashes.
- Owner passwords use salted scrypt hashes.
- Umbrel's generated app password is passed only to the web service and is
  hashed into the database when the owner is created on first start.
- Sync passwords use salted PBKDF2 PHC hashes compatible with Anki's official
  sync server.
- Request bodies and card content are not recorded in proxy metrics.
