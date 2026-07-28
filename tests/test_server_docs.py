from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import types
from pydantic import AnyUrl

from paperclip_mcp.client import PaperclipClient
from paperclip_mcp.docs import render_tool_reference
from paperclip_mcp.openapi import OperationRegistry
from paperclip_mcp.server import Runtime, _bearer_token, _list_operations, create_app


def _runtime(
    registry: OperationRegistry,
    settings: Any,
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[Runtime, httpx.AsyncClient]:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = PaperclipClient(settings, http_client=http_client)
    return Runtime.build(settings, client=client), http_client


def test_bearer_parser_is_strict_and_case_insensitive() -> None:
    assert _bearer_token("Bearer value") == "value"
    assert _bearer_token("bEaReR value") == "value"
    assert _bearer_token("Basic value") is None
    assert _bearer_token("Bearer") is None
    assert _bearer_token("Bearer one two") is None


@pytest.mark.asyncio
async def test_health_and_readiness_work_with_upstream_local_mode(
    registry: OperationRegistry,
    settings_factory: Callable[..., Any],
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "ok"})

    runtime, upstream = _runtime(registry, settings_factory(), handler)
    app = create_app(runtime)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1",
        ) as client,
    ):
        health = await client.get("/healthz")
        ready = await client.get("/readyz")
    await upstream.aclose()

    assert health.status_code == 200
    assert health.json()["operations"] == 590
    assert ready.status_code == 200
    assert seen[0].url.path == "/api/health"
    assert "authorization" not in seen[0].headers


@pytest.mark.asyncio
async def test_http_mcp_rejects_missing_or_wrong_bearer(
    registry: OperationRegistry,
    settings_factory: Callable[..., Any],
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    settings = settings_factory(mcp_token="gateway-secret")
    runtime, upstream = _runtime(registry, settings, handler)
    app = create_app(runtime)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1",
        ) as client,
    ):
        missing = await client.post("/mcp", json={})
        wrong = await client.post(
            "/mcp",
            json={},
            headers={"authorization": "Bearer wrong"},
        )
    await upstream.aclose()

    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert wrong.status_code == 401


@pytest.mark.asyncio
async def test_http_request_limit_rejects_chunked_bodies(
    registry: OperationRegistry,
    settings_factory: Callable[..., Any],
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    runtime, upstream = _runtime(
        registry,
        settings_factory(max_request_bytes=1024),
        handler,
    )
    app = create_app(runtime)

    async def chunks() -> AsyncIterator[bytes]:
        yield b"x" * 600
        yield b"x" * 600

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1",
        ) as client,
    ):
        response = await client.post("/mcp", content=chunks())
    await upstream.aclose()

    assert response.status_code == 413


@pytest.mark.asyncio
async def test_http_mcp_initializes_with_valid_bearer(
    registry: OperationRegistry,
    settings_factory: Callable[..., Any],
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    runtime, upstream = _runtime(
        registry,
        settings_factory(mcp_token="gateway-secret"),
        handler,
    )
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        },
    }
    app = create_app(runtime)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1",
        ) as client,
    ):
        response = await client.post(
            "/mcp",
            content=json.dumps(payload),
            headers={
                "authorization": "Bearer gateway-secret",
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
            },
        )
    await upstream.aclose()

    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "paperclip-mcp"


def test_operation_discovery_is_filtered_and_paginated(
    registry: OperationRegistry,
) -> None:
    first = _list_operations(registry, {"tag": "issues", "search": "comment", "limit": 2})

    assert first["total"] > 2
    assert len(first["operations"]) == 2
    assert first["next_cursor"] == 2
    assert all(row["tag"] == "issues" for row in first["operations"])


def test_generated_reference_is_current(registry: OperationRegistry) -> None:
    expected = render_tool_reference(registry)
    committed = Path("docs/generated/mcp-tools.md").read_text(encoding="utf-8")

    assert committed == expected
    assert committed.count("| `pc_") == 590


@pytest.mark.asyncio
async def test_protocol_handlers_list_call_and_read_resources(
    registry: OperationRegistry,
    settings_factory: Callable[..., Any],
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    runtime, upstream = _runtime(registry, settings_factory(), handler)
    list_handler = runtime.server.request_handlers[types.ListToolsRequest]
    listed = await list_handler(types.ListToolsRequest())
    assert len(listed.root.tools) == 592

    call_handler = runtime.server.request_handlers[types.CallToolRequest]
    discovery = await call_handler(
        types.CallToolRequest(
            params=types.CallToolRequestParams(
                name="paperclip_list_operations",
                arguments={"tag": "health", "limit": 1},
            )
        )
    )
    assert discovery.root.structuredContent is not None
    assert discovery.root.structuredContent["total"] >= 2

    generic = await call_handler(
        types.CallToolRequest(
            params=types.CallToolRequestParams(
                name="paperclip_call_operation",
                arguments={"operation": "GET /api/health"},
            )
        )
    )
    direct = await call_handler(
        types.CallToolRequest(
            params=types.CallToolRequestParams(name="pc_get_health", arguments={})
        )
    )
    assert generic.root.isError is False
    assert direct.root.isError is False

    resources_handler = runtime.server.request_handlers[types.ListResourcesRequest]
    resources = await resources_handler(types.ListResourcesRequest())
    assert {str(item.uri) for item in resources.root.resources} == {
        "paperclip://openapi",
        "paperclip://operations",
    }
    read_handler = runtime.server.request_handlers[types.ReadResourceRequest]
    manifest = await read_handler(
        types.ReadResourceRequest(
            params=types.ReadResourceRequestParams(uri=AnyUrl("paperclip://operations"))
        )
    )
    assert '"operation": "GET /api/health"' in manifest.root.contents[0].text
    await upstream.aclose()
