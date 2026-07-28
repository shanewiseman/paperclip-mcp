from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from paperclip_mcp.client import (
    PaperclipClient,
    _serialize_path_value,
    _serialize_query_value,
)
from paperclip_mcp.errors import OpenAPIContractError, UpstreamError
from paperclip_mcp.openapi import OperationRegistry, Parameter


@pytest.mark.asyncio
async def test_local_mode_sends_authenticated_operation_without_header(
    registry: OperationRegistry,
    settings_factory: Callable[..., Any],
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = PaperclipClient(settings_factory(), http_client=http_client)
        result = await client.execute(registry.operation("GET /api/companies"))

    assert result["body"] == []
    assert result["encoding"] == "json"
    assert "authorization" not in seen[0].headers


@pytest.mark.asyncio
async def test_upstream_token_is_only_attached_to_secured_operations(
    registry: OperationRegistry,
    settings_factory: Callable[..., Any],
) -> None:
    headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        headers.append(request.headers)
        return httpx.Response(200, json={"status": "ok"})

    settings = settings_factory(upstream_token="upstream-token")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = PaperclipClient(settings, http_client=http_client)
        await client.execute(registry.operation("GET /api/companies"))
        await client.execute(registry.operation("GET /api/health"))

    assert headers[0]["authorization"] == "Bearer upstream-token"
    assert "authorization" not in headers[1]


@pytest.mark.asyncio
async def test_path_query_arrays_and_json_body_are_serialized(
    registry: OperationRegistry,
    settings_factory: Callable[..., Any],
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = PaperclipClient(settings_factory(), http_client=http_client)
        await client.execute(
            registry.operation("GET /api/companies/{companyId}/workspace-overview"),
            {
                "path": {"companyId": "company / one"},
                "query": {"status": ["running", "paused"], "limit": 25},
            },
        )
        await client.execute(
            registry.operation("POST /api/companies/{companyId}/issues"),
            {
                "path": {"companyId": "company-1"},
                "body": {"title": "Example"},
            },
        )

    assert requests[0].url.raw_path.startswith(
        b"/api/companies/company%20%2F%20one/workspace-overview"
    )
    assert requests[0].url.params.get_list("status") == ["running", "paused"]
    assert requests[0].url.params["limit"] == "25"
    assert json.loads(requests[1].content) == {"title": "Example"}


@pytest.mark.asyncio
async def test_json_text_binary_and_empty_results(
    registry: OperationRegistry,
    settings_factory: Callable[..., Any],
) -> None:
    responses = iter(
        [
            httpx.Response(200, json={"ok": True}),
            httpx.Response(200, text="hello", headers={"content-type": "text/plain"}),
            httpx.Response(
                200,
                content=b"\x00\xff",
                headers={"content-type": "application/octet-stream"},
            ),
            httpx.Response(204),
        ]
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return next(responses)

    operation = registry.operation("GET /api/health")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = PaperclipClient(settings_factory(), http_client=http_client)
        json_result = await client.execute(operation)
        text_result = await client.execute(operation)
        binary_result = await client.execute(operation)
        empty_result = await client.execute(operation)

    assert (json_result["body"], json_result["encoding"]) == ({"ok": True}, "json")
    assert (text_result["body"], text_result["encoding"]) == ("hello", "text")
    assert binary_result["body"] == base64.b64encode(b"\x00\xff").decode()
    assert binary_result["encoding"] == "base64"
    assert (empty_result["body"], empty_result["encoding"]) == (None, "empty")


@pytest.mark.asyncio
async def test_upstream_errors_redact_credentials_and_are_bounded(
    registry: OperationRegistry,
    settings_factory: Callable[..., Any],
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            text='{"token":"upstream-secret","password":"also-secret"}',
            headers={"x-request-id": "request-1"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = PaperclipClient(
            settings_factory(upstream_token="upstream-secret"),
            http_client=http_client,
        )
        with pytest.raises(UpstreamError) as raised:
            await client.execute(registry.operation("GET /api/companies"))

    assert raised.value.status_code == 403
    assert raised.value.request_id == "request-1"
    assert "upstream-secret" not in str(raised.value)
    assert "also-secret" not in str(raised.value)
    assert "[REDACTED]" in str(raised.value)


@pytest.mark.asyncio
async def test_response_and_multipart_size_limits(
    registry: OperationRegistry,
    settings_factory: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 1025)

    settings = settings_factory(max_response_bytes=1024, max_request_bytes=1024)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = PaperclipClient(settings, http_client=http_client)
        with pytest.raises(UpstreamError, match="exceeds configured maximum"):
            await client.execute(registry.operation("GET /api/health"))

        def unexpected_decode(*_: Any, **__: Any) -> bytes:
            raise AssertionError("oversized base64 was decoded")

        monkeypatch.setattr(base64, "b64decode", unexpected_decode)
        with pytest.raises(OpenAPIContractError, match="request maximum"):
            await client.execute(
                registry.operation("POST /api/companies/{companyId}/assets/images"),
                {
                    "path": {"companyId": "company-1"},
                    "multipart": {
                        "file": {
                            "filename": "large.bin",
                            "data_base64": base64.b64encode(b"x" * 1025).decode(),
                        }
                    },
                },
            )


@pytest.mark.asyncio
async def test_multipart_limit_counts_text_and_file_metadata(
    registry: OperationRegistry,
    settings_factory: Callable[..., Any],
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"id": "asset-1"})

    settings = settings_factory(max_request_bytes=1024)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = PaperclipClient(settings, http_client=http_client)
        operation = registry.operation("POST /api/companies/{companyId}/assets/images")
        with pytest.raises(OpenAPIContractError, match="request maximum"):
            await client.execute(
                operation,
                {
                    "path": {"companyId": "company-1"},
                    "multipart": {"purpose": "x" * 1024},
                },
            )
        with pytest.raises(OpenAPIContractError, match="request maximum"):
            await client.execute(
                operation,
                {
                    "path": {"companyId": "company-1"},
                    "multipart": {
                        "file": {
                            "filename": "x" * 1024,
                            "data_base64": "eA==",
                        }
                    },
                },
            )

    assert requests == []


@pytest.mark.asyncio
async def test_multipart_fallback_builds_file_and_text_parts(
    registry: OperationRegistry,
    settings_factory: Callable[..., Any],
) -> None:
    captured: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.content)
        return httpx.Response(201, json={"id": "asset-1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = PaperclipClient(settings_factory(), http_client=http_client)
        result = await client.execute(
            registry.operation("POST /api/companies/{companyId}/assets/images"),
            {
                "path": {"companyId": "company-1"},
                "multipart": {
                    "file": {
                        "filename": "note.txt",
                        "media_type": "text/plain",
                        "data_base64": base64.b64encode(b"hello").decode(),
                    },
                    "purpose": "issue",
                },
            },
        )

    assert result["status_code"] == 201
    assert b'filename="note.txt"' in captured[0]
    assert b"hello" in captured[0]
    assert b"purpose" in captured[0]


@pytest.mark.asyncio
async def test_network_failures_and_declared_oversize_are_safe_errors(
    registry: OperationRegistry,
    settings_factory: Callable[..., Any],
) -> None:
    failures = iter(["timeout", "connect"])

    def failing(request: httpx.Request) -> httpx.Response:
        if next(failures) == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        raise httpx.ConnectError("offline", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(failing)) as http_client:
        client = PaperclipClient(settings_factory(), http_client=http_client)
        with pytest.raises(UpstreamError, match="timed out"):
            await client.execute(registry.operation("GET /api/health"))
        with pytest.raises(UpstreamError, match="ConnectError"):
            await client.execute(registry.operation("GET /api/health"))

    def oversize(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": "2048"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(oversize)) as http_client:
        client = PaperclipClient(
            settings_factory(max_response_bytes=1024),
            http_client=http_client,
        )
        with pytest.raises(UpstreamError, match="exceeds configured maximum"):
            await client.execute(registry.operation("GET /api/health"))

    owned = PaperclipClient(settings_factory())
    await owned.close()


@pytest.mark.asyncio
async def test_argument_shape_and_multipart_validation_fail_closed(
    registry: OperationRegistry,
    settings_factory: Callable[..., Any],
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))
    ) as http_client:
        client = PaperclipClient(settings_factory(), http_client=http_client)
        operation = registry.operation("GET /api/companies/{companyId}/workspace-overview")
        with pytest.raises(OpenAPIContractError, match="requires path parameter"):
            await client.execute(operation)
        with pytest.raises(OpenAPIContractError, match="unknown query fields"):
            await client.execute(
                operation,
                {"path": {"companyId": "one"}, "query": {"unknown": True}},
            )
        upload = registry.operation("POST /api/companies/{companyId}/assets/images")
        with pytest.raises(OpenAPIContractError, match="invalid base64"):
            await client.execute(
                upload,
                {
                    "path": {"companyId": "one"},
                    "multipart": {"file": {"filename": "bad.bin", "data_base64": "not-base64"}},
                },
            )
        with pytest.raises(OpenAPIContractError, match="unsupported value"):
            await client.execute(
                upload,
                {"path": {"companyId": "one"}, "multipart": {"file": ["bad"]}},
            )


def test_parameter_style_serializers_cover_openapi_shapes() -> None:
    def parameter(
        *,
        location: str,
        style: str | None = None,
        explode: bool | None = None,
    ) -> Parameter:
        return Parameter(
            name="value",
            location=location,
            required=False,
            schema={},
            style=style,
            explode=explode,
        )

    assert _serialize_path_value(["a", 2], parameter(location="path")) == "a,2"
    assert (
        _serialize_path_value(
            {"a": 1, "b": 2},
            parameter(location="path", explode=True),
        )
        == "a=1,b=2"
    )
    assert (
        _serialize_path_value(
            {"a": 1},
            parameter(location="path", explode=False),
        )
        == "a,1"
    )
    with pytest.raises(OpenAPIContractError, match="unsupported style"):
        _serialize_path_value("a", parameter(location="path", style="label"))

    assert _serialize_query_value(parameter(location="query"), ["a", "b"]) == [
        ("value", "a"),
        ("value", "b"),
    ]
    assert _serialize_query_value(
        parameter(location="query", style="pipeDelimited"),
        ["a", "b"],
    ) == [("value", "a|b")]
    assert _serialize_query_value(
        parameter(location="query", style="deepObject"),
        {"a": True},
    ) == [("value[a]", "true")]
    assert _serialize_query_value(
        parameter(location="query", explode=False),
        {"a": 1},
    ) == [("value", "a,1")]
    with pytest.raises(OpenAPIContractError, match="unsupported style"):
        _serialize_query_value(
            parameter(location="query", style="matrix"),
            ["a"],
        )
