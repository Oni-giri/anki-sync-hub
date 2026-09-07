from __future__ import annotations

import time
from collections.abc import AsyncIterator

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from .db import Database

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def _filtered_headers(headers: httpx.Headers) -> dict[str, str]:
    return {
        name: value for name, value in headers.items() if name.lower() not in HOP_BY_HOP_HEADERS
    }


async def proxy_sync_request(
    request: Request,
    namespace: str,
    path: str,
    client: httpx.AsyncClient,
    database: Database,
    upstream_base: str,
) -> StreamingResponse:
    started = time.monotonic()
    route = f"/{namespace}"
    suffix = f"/{path}" if path else ""
    upstream_url = f"{upstream_base}/{namespace}{suffix}"
    if request.url.query:
        upstream_url += f"?{request.url.query}"

    request_bytes = [0]
    response_bytes = [0]

    async def incoming_body() -> AsyncIterator[bytes]:
        async for chunk in request.stream():
            request_bytes[0] += len(chunk)
            yield chunk

    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() not in HOP_BY_HOP_HEADERS | {"host"}
    }
    upstream_request = client.build_request(
        request.method,
        upstream_url,
        headers=headers,
        content=incoming_body(),
    )
    try:
        upstream_response = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as error:
        latency_ms = int((time.monotonic() - started) * 1000)
        database.record_proxy_metric(route, 502, request_bytes[0], 0, latency_ms)
        raise HTTPException(status_code=502, detail="The sync service is unavailable.") from error

    async def outgoing_body() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream_response.aiter_raw():
                response_bytes[0] += len(chunk)
                yield chunk
        finally:
            await upstream_response.aclose()
            latency_ms = int((time.monotonic() - started) * 1000)
            database.record_proxy_metric(
                route,
                upstream_response.status_code,
                request_bytes[0],
                response_bytes[0],
                latency_ms,
            )

    return StreamingResponse(
        outgoing_body(),
        status_code=upstream_response.status_code,
        headers=_filtered_headers(upstream_response.headers),
    )
