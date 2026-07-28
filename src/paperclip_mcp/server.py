"""Low-level MCP registry plus authenticated Streamable HTTP composition."""

from __future__ import annotations

import hmac
import json
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, cast

import jsonschema
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from mcp import types
from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyUrl
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from paperclip_mcp import __version__
from paperclip_mcp.client import PaperclipClient
from paperclip_mcp.config import Settings
from paperclip_mcp.errors import OpenAPIContractError, UpstreamError
from paperclip_mcp.openapi import Operation, OperationRegistry, response_envelope_schema

LIST_OPERATIONS_TOOL = "paperclip_list_operations"
CALL_OPERATION_TOOL = "paperclip_call_operation"


@dataclass(slots=True)
class Runtime:
    """Lifecycle-owned registry, upstream client, and MCP protocol server."""

    settings: Settings
    registry: OperationRegistry
    health_operation: Operation
    client: PaperclipClient
    server: Server[Any]

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        client: PaperclipClient | None = None,
    ) -> Runtime:
        complete_registry = OperationRegistry.load(settings.openapi_path)
        registry = (
            complete_registry
            if settings.enabled_tag_set is None
            else OperationRegistry(
                complete_registry.document,
                enabled_tags=settings.enabled_tag_set,
            )
        )
        upstream = client or PaperclipClient(settings)
        protocol = _create_protocol_server(registry, upstream)
        return cls(
            settings=settings,
            registry=registry,
            health_operation=complete_registry.operation("GET /api/health"),
            client=upstream,
            server=protocol,
        )


class StreamableHTTPApp:
    """Small public-ASGI adapter around the MCP session manager."""

    def __init__(self, manager: StreamableHTTPSessionManager) -> None:
        self.manager = manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.manager.handle_request(scope, receive, send)


class RequestBodyLimitMiddleware:
    """Bound both fixed-length and chunked HTTP bodies before protocol parsing."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        buffered: deque[Message] = deque()
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            buffered.append(message)
            total += len(message.get("body", b""))
            if total > self.max_bytes:
                response = JSONResponse(
                    status_code=413,
                    content={"detail": "request is too large"},
                )
                await response(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        async def replay() -> Message:
            if buffered:
                return buffered.popleft()
            return {"type": "http.disconnect"}

        await self.app(scope, replay, send)


def _create_protocol_server(
    registry: OperationRegistry,
    client: PaperclipClient,
) -> Server[Any]:
    server: Server[Any] = Server(
        "paperclip-mcp",
        version=__version__,
        instructions=(
            "Paperclip API operations generated from the bundled OpenAPI contract. "
            "Use paperclip_list_operations to discover exact METHOD/path keys. Direct pc_* "
            "tools provide schema validation. Treat non-GET operations as consequential."
        ),
    )

    @server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
    async def list_tools() -> list[types.Tool]:
        direct_tools = [operation.as_tool() for operation in registry.operations]
        return [_list_operations_tool(), _call_operation_tool(), *direct_tools]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == LIST_OPERATIONS_TOOL:
            return _list_operations(registry, arguments)
        if name == CALL_OPERATION_TOOL:
            identifier = arguments.get("operation")
            if not isinstance(identifier, str):
                raise OpenAPIContractError("operation must be a METHOD /path key or pc_* tool name")
            operation = registry.operation(identifier)
            operation_arguments = {
                key: value
                for key, value in arguments.items()
                if key in {"path", "query", "body", "multipart"}
            }
            _validate_operation_arguments(operation.input_schema, operation_arguments)
            return await client.execute(operation, operation_arguments)
        operation = registry.operation(name)
        _validate_operation_arguments(operation.input_schema, arguments)
        return await client.execute(operation, arguments)

    @server.list_resources()  # type: ignore[no-untyped-call,untyped-decorator]
    async def list_resources() -> list[types.Resource]:
        return [
            types.Resource(
                uri=AnyUrl("paperclip://openapi"),
                name="Paperclip OpenAPI",
                title="Canonical Paperclip OpenAPI document",
                description="The exact source document used to generate MCP operations.",
                mimeType="application/json",
            ),
            types.Resource(
                uri=AnyUrl("paperclip://operations"),
                name="Paperclip operation manifest",
                title="Paperclip MCP operation manifest",
                description="Stable operation key to generated direct-tool mapping.",
                mimeType="application/json",
            ),
        ]

    @server.read_resource()  # type: ignore[no-untyped-call,untyped-decorator]
    async def read_resource(uri: AnyUrl) -> list[ReadResourceContents]:
        value = str(uri)
        if value == "paperclip://openapi":
            content = json.dumps(registry.document, indent=2, sort_keys=True)
        elif value == "paperclip://operations":
            content = json.dumps(registry.manifest(), indent=2, sort_keys=True)
        else:
            raise OpenAPIContractError(f"unknown resource: {value}")
        return [ReadResourceContents(content=content, mime_type="application/json")]

    return server


def create_app(runtime: Runtime) -> FastAPI:
    """Create health/readiness endpoints and the protected MCP transport."""
    settings = runtime.settings
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=settings.allowed_host_patterns,
        allowed_origins=settings.allowed_origin_patterns,
    )
    manager = StreamableHTTPSessionManager(
        app=runtime.server,
        json_response=True,
        stateless=True,
        security_settings=transport_security,
    )
    mcp_app = StreamableHTTPApp(manager)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            async with manager.run():
                yield
        finally:
            await runtime.client.close()

    app = FastAPI(
        title="Paperclip MCP",
        summary="Schema-driven MCP gateway for Paperclip",
        description=(
            "The Paperclip API is exposed through Model Context Protocol at /mcp. "
            "Only local health and readiness probes are conventional HTTP endpoints."
        ),
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    expected_token = settings.resolved_mcp_token
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=settings.max_request_bytes)

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Any) -> Response:
        if request.url.path in {"/healthz", "/readyz"}:
            return cast(Response, await call_next(request))
        if expected_token is None:
            return cast(Response, await call_next(request))
        supplied = _bearer_token(request.headers.get("authorization", ""))
        if supplied is None or not hmac.compare_digest(expected_token, supplied):
            return JSONResponse(
                status_code=401,
                content={"detail": "invalid bearer credential"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return cast(Response, await call_next(request))

    @app.get("/healthz", tags=["operations"])
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "openapi_version": runtime.registry.api_version,
            "operations": len(runtime.registry.operations),
            "tags": len(runtime.registry.tags),
        }

    @app.get("/readyz", tags=["operations"])
    async def ready(response: Response) -> dict[str, Any]:
        try:
            result = await runtime.client.execute(runtime.health_operation)
        except UpstreamError as exc:
            response.status_code = 503
            return {
                "status": "not-ready",
                "paperclip": False,
                "detail": str(exc),
            }
        response.status_code = 200
        return {
            "status": "ok",
            "paperclip": True,
            "upstream_status": result["status_code"],
        }

    app.router.routes.append(Route("/mcp", endpoint=mcp_app, methods=["GET", "POST", "DELETE"]))
    app.state.paperclip_runtime = runtime
    return app


def _bearer_token(authorization: str) -> str | None:
    fields = authorization.split()
    if len(fields) != 2:
        return None
    scheme, token = fields
    return token if scheme.casefold() == "bearer" and token else None


def _list_operations_tool() -> types.Tool:
    return types.Tool(
        name=LIST_OPERATIONS_TOOL,
        title="List Paperclip operations",
        description=(
            "Discover stable METHOD /path operation keys and their generated pc_* tools. "
            "Results are bounded and can be filtered by tag or text."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "tag": {"type": "string"},
                "search": {"type": "string"},
                "cursor": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
            },
            "additionalProperties": False,
        },
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )


def _call_operation_tool() -> types.Tool:
    return types.Tool(
        name=CALL_OPERATION_TOOL,
        title="Call a Paperclip operation",
        description=(
            "Call one exact operation from paperclip_list_operations. The path is allowlisted; "
            "caller-provided URLs, headers, cookies, and authorization values are never accepted."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "operation": {"type": "string", "minLength": 1},
                "path": {"type": "object"},
                "query": {"type": "object"},
                "body": {},
                "multipart": {"type": "object"},
            },
            "required": ["operation"],
            "additionalProperties": False,
        },
        outputSchema=response_envelope_schema(),
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )


def _list_operations(
    registry: OperationRegistry,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    tag = arguments.get("tag")
    search = arguments.get("search")
    cursor = arguments.get("cursor", 0)
    limit = arguments.get("limit", 50)
    if tag is not None and not isinstance(tag, str):
        raise OpenAPIContractError("tag must be a string")
    if search is not None and not isinstance(search, str):
        raise OpenAPIContractError("search must be a string")
    if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
        raise OpenAPIContractError("cursor must be a non-negative integer")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise OpenAPIContractError("limit must be between 1 and 100")

    normalized = search.casefold() if search else None
    selected = [
        row
        for row in registry.manifest()
        if (tag is None or row["tag"] == tag)
        and (
            normalized is None
            or normalized in str(row["operation"]).casefold()
            or normalized in str(row["summary"]).casefold()
        )
    ]
    page = selected[cursor : cursor + limit]
    next_cursor = cursor + len(page)
    return {
        "operations": page,
        "next_cursor": next_cursor if next_cursor < len(selected) else None,
        "total": len(selected),
    }


def _validate_operation_arguments(
    schema: dict[str, Any],
    arguments: dict[str, Any],
) -> None:
    try:
        jsonschema.validate(instance=arguments, schema=schema)
    except jsonschema.ValidationError as exc:
        raise OpenAPIContractError(f"operation input validation failed: {exc.message}") from exc
