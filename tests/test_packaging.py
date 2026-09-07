from pathlib import Path


def test_umbrel_proxy_bypasses_app_auth_for_natively_authenticated_routes() -> None:
    compose = (
        Path(__file__).parents[1] / "packaging/umbrel/anki-sync-hub/docker-compose.yml"
    ).read_text()
    whitelist_line = next(line for line in compose.splitlines() if "PROXY_AUTH_WHITELIST:" in line)
    routes = set(whitelist_line.split('"', 2)[1].split(","))

    assert routes == {"/api/*", "/sync/*", "/msync/*", "/mcp", "/mcp/*"}
