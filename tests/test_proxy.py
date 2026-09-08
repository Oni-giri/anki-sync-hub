from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from anki_sync_hub.config import Settings
from anki_sync_hub.main import create_app


def test_sync_gateway_streams_and_records_metrics(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        listen_host="127.0.0.1",
        listen_port=8080,
        sync_internal_url="http://sync.internal:8081",
        session_ttl_seconds=3600,
        cookie_secure=False,
    )
    app = create_app(settings)

    class UpstreamStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"upstream:payload"

    async def upstream(request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        assert request.url == "http://sync.internal:8081/sync/hostKey?test=1"
        assert body == b"payload"
        return httpx.Response(201, stream=UpstreamStream())

    with TestClient(app) as client:
        original_client = app.state.http
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        response = client.post("/sync/hostKey?test=1", content=b"payload")
        client.portal.call(original_client.aclose)

    assert response.status_code == 201
    assert response.content == b"upstream:payload"
    metrics = app.state.database.metrics()
    assert metrics[0]["route"] == "/sync"
    assert metrics[0]["requests"] == 1
    assert metrics[0]["request_bytes"] == 7
    assert metrics[0]["response_bytes"] == 16


def test_rest_api_gateway_allows_bearer_writes_without_browser_origin(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        listen_host="127.0.0.1",
        listen_port=8080,
        sync_internal_url="http://sync.internal:8081",
        session_ttl_seconds=3600,
        cookie_secure=False,
        mcp_internal_url="http://automation.internal:8082",
    )
    app = create_app(settings)

    class UpstreamStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"id":2,"name":"Languages"}'

    async def upstream(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://automation.internal:8082/api/v1/decks"
        assert request.headers["authorization"] == "Bearer ash_test"
        assert await request.aread() == b'{"name":"Languages"}'
        return httpx.Response(
            201,
            headers={"Content-Type": "application/json"},
            stream=UpstreamStream(),
        )

    with TestClient(app) as client:
        original_client = app.state.http
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        response = client.post(
            "/api/v1/decks",
            headers={"Authorization": "Bearer ash_test"},
            content=b'{"name":"Languages"}',
        )
        client.portal.call(original_client.aclose)

    assert response.status_code == 201
    assert response.json() == {"id": 2, "name": "Languages"}
    metrics = app.state.database.metrics()
    assert metrics[0]["route"] == "/api/v1"
