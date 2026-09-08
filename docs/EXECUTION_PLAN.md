# Execution plan

## Current progress

- Completed: repository foundation, execution plan, portable packaging,
  database migrations, owner sessions, sync-user management, supervised sync
  reloads, streaming gateway, metrics dashboard, MCP bearer tokens, and the
  initial read/write MCP tools.
- Verified: generated password hashes against Anki 26.8.1, credential reloads
  without container restarts, and MCP-originated note creation through the
  real sync protocol into a second client collection.
- In progress: the remaining note/card/tag tools, idempotency controls,
  destructive-operation confirmation, richer audit views, and device-level
  Docker/Umbrel validation.

## Outcome

Deliver a maintainable, multi-architecture self-hosted application that runs
normally with Docker Compose and can be packaged for the Umbrel App Store. An
owner installs it, opens a browser, creates an account and Anki sync user, and
can then connect Anki clients or scoped MCP clients without editing files or
using SSH.

## Architectural constraints

1. Use the official `anki` package for sync compatibility and collection
   operations. Pin releases and update deliberately when stable Anki releases
   change the sync protocol.
2. Never write directly to the authoritative sync server collection from the
   UI or MCP process. Automation uses a separate headless Anki collection and
   synchronizes through the normal protocol.
3. Keep all durable state under one configurable data root. Startup and schema
   migrations must be idempotent and safe across skipped releases.
4. Keep Umbrel-specific hostnames, paths, secrets, and proxy wiring in the
   Umbrel package. The upstream container remains portable.
5. Expose one browser origin. The web process streams Anki sync routes to the
   internal sync service so it can record health and request metrics.
6. Assume local HTTP is used only on a trusted LAN. Recommend Tailscale HTTPS
   for access outside that LAN. Never advertise direct public HTTP exposure.

## Milestone 1: runnable control plane

- Initialize the Python package and repository hygiene.
- Add an idempotent SQLite migration layer.
- Add first-run owner creation, optional Umbrel credential provisioning, login,
  logout, and session expiry.
- Add sync-user create/list/disable/password-reset operations.
- Store owner passwords with scrypt and sync passwords as PBKDF2 PHC hashes
  accepted by the official server.
- Validate sync usernames so they cannot escape their storage directory.

Acceptance:

- Fresh startup reaches an actionable setup page.
- No plaintext generated password is present in the image, Dockerfile, or
  Compose file; Umbrel injects its per-app password at runtime.
- Authentication and database unit tests pass.

## Milestone 2: sync runtime and observability

- Add a supervisor that reads enabled users from the control database and
  starts `python -m anki.syncserver` with hashed credentials.
- Wait safely when no sync user exists.
- Detect credential-generation changes and gracefully reload the child process.
- Add streaming reverse proxy routes for `/sync/*` and `/msync/*`.
- Record request count, status, latency, and byte totals without storing body
  content or credentials.
- Show service state, configured users, storage usage, and recent failures.

Acceptance:

- Current Anki Desktop, AnkiDroid, and AnkiMobile can complete initial upload,
  normal sync, media sync, and full download.
- A large collection request is streamed rather than buffered in memory.
- Changing a password through the UI works without restarting the container.

## Milestone 3: MCP and automation client

- Mount a Streamable HTTP MCP server at `/mcp`.
- Expose a versioned REST API at `/api/v1` using the same scoped access tokens.
- Add per-user, hashed API tokens with `read`, `write`, and `destructive`
  scopes.
- Maintain a separate local automation collection and host key per sync user.
- Serialize automation work per user: pull, mutate, commit, push.
- Add idempotency keys and an audit log for all mutations.
- Start with deck listing/creation, note search/read/create/update, tags, moving
  cards, suspend/unsuspend, deletion, and explicit sync/status tools.
- Add bounded batches and confirmation tokens for destructive tools.

Acceptance:

- An MCP-created note appears on a separately configured Anki client after
  sync.
- Replayed write requests do not duplicate notes.
- Conflicts and full-sync requirements are surfaced instead of silently
  choosing a side.

## Milestone 4: container and Umbrel distribution

- Build one versioned application image with `web` and `sync-supervisor`
  commands for `linux/amd64` and `linux/arm64`.
- Provide portable `compose.yaml` with bind-mounted data and no fixed user
  credentials.
- Add an Umbrel package with `umbrel-app.yml`, `docker-compose.yml`, persistent
  `${APP_DATA_DIR}/data` mounts, and `app_proxy`.
- Keep Umbrel authentication enabled for the UI. Whitelist only `/sync/*`,
  `/msync/*`, and `/mcp`; each remains protected by its native credentials.
- Pin the image tag and multi-architecture digest in the App Store package.
- Add release automation, dependency updates, SBOM, container scanning, and
  release notes.

Acceptance:

- The Umbrel package linter passes.
- Install, onboarding, restart, update, skipped-version migration, backup, and
  restore pass on amd64 and Raspberry Pi arm64.
- No Docker socket, host networking, privileged mode, broad host mounts, or
  manual lifecycle hooks are required.

## Milestone 5: hardening and release

- Add origin/host validation, login throttling, security headers, secret
  redaction, structured logs, and audit retention controls.
- Exercise malformed and oversized sync/MCP requests.
- Document LAN and Tailscale access, disaster recovery, version compatibility,
  and safe upgrade/rollback procedures.
- Run a private community App Store beta before an official Umbrel submission.

## Release gates

- Unit, integration, end-to-end, and container smoke tests are green.
- Every release is tested against the stable Anki version it embeds.
- The image manifest contains both supported Linux architectures.
- Persistent data remains owned by UID/GID 1000 and survives container
  recreation.
- Destructive UI and MCP paths require deliberate confirmation.
