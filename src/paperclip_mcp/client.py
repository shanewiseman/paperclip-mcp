"""Bounded HTTP client for allowlisted Paperclip OpenAPI operations."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Mapping
from itertools import pairwise
from typing import Any
from urllib.parse import quote

import httpx

from paperclip_mcp import __version__
from paperclip_mcp.config import Settings
from paperclip_mcp.errors import OpenAPIContractError, UpstreamError
from paperclip_mcp.openapi import Operation, Parameter

SAFE_RESPONSE_HEADERS = frozenset(
    {
        "content-length",
        "content-type",
        "etag",
        "last-modified",
        "location",
        "retry-after",
        "x-request-id",
    }
)
SENSITIVE_FIELD = re.compile(
    r'(?i)(["\']?[a-z0-9_-]*'
    r"(?:authorization|password|passwd|secret|token|credential|api[_-]?key|private[_-]?key)"
    r'[a-z0-9_-]*["\']?\s*[:=]\s*)'
    r'(["\'][^"\']*["\']|[^\s,;}]+)'
)
CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
SENSITIVE_KEY_PARTS = frozenset(
    {"authorization", "credential", "credentials", "password", "passwd", "secret", "token"}
)
SENSITIVE_KEY_SUFFIXES = (
    "authorization",
    "credential",
    "credentials",
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "privatekey",
)


class PaperclipClient:
    """Execute only registered operations against one configured upstream origin."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            verify=settings.httpx_verify,
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            follow_redirects=False,
            headers={
                "User-Agent": f"paperclip-mcp/{__version__}",
                "Accept": "application/json, application/octet-stream;q=0.8, text/plain;q=0.7",
            },
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def execute(
        self,
        operation: Operation,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Serialize, execute, bound, and normalize one registered operation."""
        supplied = dict(arguments or {})
        path = self._render_path(operation, supplied.get("path"))
        query = self._render_query(operation, supplied.get("query"))
        headers: dict[str, str] = {}
        upstream_token = self.settings.resolved_upstream_token
        if operation.requires_auth and upstream_token is not None:
            headers["Authorization"] = f"Bearer {upstream_token}"

        request_kwargs: dict[str, Any] = {
            "params": query,
            "headers": headers,
        }
        if "body" in supplied:
            request_kwargs["json"] = supplied["body"]
        if "multipart" in supplied:
            request_kwargs["files"] = self._multipart_parts(supplied["multipart"])

        url = f"{self.settings.base_url}{path}"
        try:
            async with self._client.stream(operation.method, url, **request_kwargs) as response:
                payload = await self._bounded_body(response)
        except httpx.TimeoutException as exc:
            raise UpstreamError(
                f"Paperclip request timed out: {operation.key}",
            ) from exc
        except httpx.RequestError as exc:
            raise UpstreamError(
                f"Paperclip request failed: {operation.key}: {type(exc).__name__}",
            ) from exc

        request_id = response.headers.get("x-request-id")
        if not response.is_success and response.status_code != 304:
            excerpt = _safe_error_excerpt(payload, upstream_token)
            message = f"Paperclip returned HTTP {response.status_code} for {operation.key}"
            if excerpt:
                message = f"{message}: {excerpt}"
            raise UpstreamError(
                message,
                status_code=response.status_code,
                request_id=request_id,
            )

        content_type = response.headers.get("content-type")
        body, encoding = _decode_success(payload, content_type)
        safe_headers = {
            key.casefold(): value
            for key, value in response.headers.items()
            if key.casefold() in SAFE_RESPONSE_HEADERS
        }
        return {
            "status_code": response.status_code,
            "content_type": content_type,
            "headers": safe_headers,
            "body": body,
            "encoding": encoding,
        }

    def _render_path(self, operation: Operation, raw: Any) -> str:
        values = _mapping_or_empty(raw, "path")
        rendered = operation.path
        for parameter in operation.path_parameters:
            if parameter.name not in values:
                raise OpenAPIContractError(
                    f"{operation.key} requires path parameter {parameter.name!r}"
                )
            serialized = _serialize_path_value(values[parameter.name], parameter)
            rendered = rendered.replace(
                "{" + parameter.name + "}",
                quote(serialized, safe=""),
            )
        if "{" in rendered or "}" in rendered:
            raise OpenAPIContractError(f"unresolved path template in {operation.key}")
        return rendered

    def _render_query(self, operation: Operation, raw: Any) -> list[tuple[str, str]]:
        values = _mapping_or_empty(raw, "query")
        result: list[tuple[str, str]] = []
        declared = {parameter.name for parameter in operation.query_parameters}
        unknown = set(values) - declared
        if unknown:
            names = ", ".join(sorted(unknown))
            raise OpenAPIContractError(f"{operation.key} received unknown query fields: {names}")
        for parameter in operation.query_parameters:
            if parameter.name not in values or values[parameter.name] is None:
                if parameter.required:
                    raise OpenAPIContractError(
                        f"{operation.key} requires query parameter {parameter.name!r}"
                    )
                continue
            result.extend(_serialize_query_value(parameter, values[parameter.name]))
        return result

    async def _bounded_body(self, response: httpx.Response) -> bytes:
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                declared_length = -1
            if declared_length > self.settings.max_response_bytes:
                raise UpstreamError(
                    "Paperclip response exceeds configured maximum",
                    status_code=response.status_code,
                    request_id=response.headers.get("x-request-id"),
                )

        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > self.settings.max_response_bytes:
                raise UpstreamError(
                    "Paperclip response exceeds configured maximum",
                    status_code=response.status_code,
                    request_id=response.headers.get("x-request-id"),
                )
        return bytes(content)

    def _multipart_parts(self, raw: Any) -> list[tuple[str, Any]]:
        values = _mapping_or_empty(raw, "multipart")
        parts: list[tuple[str, Any]] = []
        total_bytes = 0

        def reserve(size: int) -> None:
            nonlocal total_bytes
            if size > self.settings.max_request_bytes - total_bytes:
                raise OpenAPIContractError("multipart payload exceeds configured request maximum")
            total_bytes += size

        def reserve_text(value: str) -> None:
            remaining = self.settings.max_request_bytes - total_bytes
            if len(value) > remaining:
                raise OpenAPIContractError("multipart payload exceeds configured request maximum")
            reserve(len(value.encode("utf-8")))

        for name, value in values.items():
            reserve_text(name)
            if isinstance(value, Mapping):
                filename = value.get("filename")
                media_type = value.get("media_type", "application/octet-stream")
                encoded = value.get("data_base64")
                if not isinstance(filename, str) or not filename:
                    raise OpenAPIContractError(f"multipart field {name!r} needs a filename")
                if not isinstance(media_type, str) or not media_type:
                    raise OpenAPIContractError(f"multipart field {name!r} has invalid media_type")
                if not isinstance(encoded, str):
                    raise OpenAPIContractError(f"multipart field {name!r} needs data_base64")
                reserve_text(filename)
                reserve_text(media_type)
                remaining = self.settings.max_request_bytes - total_bytes
                max_encoded_length = 4 * ((remaining + 2) // 3)
                if len(encoded) > max_encoded_length:
                    raise OpenAPIContractError(
                        "multipart payload exceeds configured request maximum"
                    )
                try:
                    data = base64.b64decode(encoded, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise OpenAPIContractError(
                        f"multipart field {name!r} contains invalid base64"
                    ) from exc
                reserve(len(data))
                parts.append((name, (filename, data, media_type)))
            elif isinstance(value, bool):
                text = "true" if value else "false"
                reserve_text(text)
                parts.append((name, (None, text)))
            elif isinstance(value, (str, int, float)):
                text = str(value)
                reserve_text(text)
                parts.append((name, (None, text)))
            else:
                raise OpenAPIContractError(f"multipart field {name!r} has unsupported value")
        return parts


def _mapping_or_empty(value: Any, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise OpenAPIContractError(f"{label} arguments must be an object")
    return {str(key): item for key, item in value.items()}


def _serialize_path_value(value: Any, parameter: Parameter) -> str:
    style = parameter.style or "simple"
    if style != "simple":
        raise OpenAPIContractError(
            f"path parameter {parameter.name!r} uses unsupported style {style!r}"
        )
    if isinstance(value, list):
        return ",".join(_scalar(item) for item in value)
    if isinstance(value, Mapping):
        if parameter.explode:
            return ",".join(f"{key}={_scalar(item)}" for key, item in value.items())
        flattened = [
            part for pair in value.items() for part in (_scalar(pair[0]), _scalar(pair[1]))
        ]
        return ",".join(flattened)
    return _scalar(value)


def _serialize_query_value(parameter: Parameter, value: Any) -> list[tuple[str, str]]:
    style = parameter.style or "form"
    explode = parameter.explode if parameter.explode is not None else style == "form"
    name = parameter.name
    if not isinstance(value, (list, Mapping)):
        return [(name, _scalar(value))]
    if isinstance(value, list):
        if style == "form" and explode:
            return [(name, _scalar(item)) for item in value]
        delimiter = {"form": ",", "spaceDelimited": " ", "pipeDelimited": "|"}.get(style)
        if delimiter is None:
            raise OpenAPIContractError(f"query parameter {name!r} uses unsupported style {style!r}")
        return [(name, delimiter.join(_scalar(item) for item in value))]
    if style == "deepObject":
        return [(f"{name}[{key}]", _scalar(item)) for key, item in value.items()]
    if style != "form":
        raise OpenAPIContractError(f"query parameter {name!r} uses unsupported style {style!r}")
    if explode:
        return [(str(key), _scalar(item)) for key, item in value.items()]
    flattened = [part for pair in value.items() for part in (_scalar(pair[0]), _scalar(pair[1]))]
    return [(name, ",".join(flattened))]


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value)
    raise OpenAPIContractError(f"cannot serialize {type(value).__name__} as an HTTP parameter")


def _decode_success(payload: bytes, content_type: str | None) -> tuple[Any, str]:
    if not payload:
        return None, "empty"
    media_type = (content_type or "").partition(";")[0].strip().casefold()
    if media_type == "application/json" or media_type.endswith("+json"):
        try:
            return json.loads(payload), "json"
        except (UnicodeDecodeError, json.JSONDecodeError):
            return payload.decode("utf-8", errors="replace"), "text"
    if media_type.startswith("text/") or media_type in {
        "application/jsonl",
        "application/x-ndjson",
        "image/svg+xml",
    }:
        return payload.decode("utf-8", errors="replace"), "text"
    return base64.b64encode(payload).decode("ascii"), "base64"


def _safe_error_excerpt(payload: bytes, token: str | None) -> str:
    if not payload:
        return ""
    excerpt = payload[:2048].decode("utf-8", errors="replace")
    try:
        parsed = json.loads(excerpt)
    except json.JSONDecodeError:
        parsed = None
    if parsed is not None:
        excerpt = json.dumps(
            _redact_json_credentials(parsed, token),
            ensure_ascii=False,
            separators=(",", ":"),
        )[:2048]
        token = None
    if token:
        excerpt = excerpt.replace(token, "[REDACTED]")
    return SENSITIVE_FIELD.sub(r"\1[REDACTED]", excerpt).strip()


def _redact_json_credentials(value: Any, token: str | None) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            safe_key = key.replace(token, "[REDACTED]") if token else key
            redacted[safe_key] = (
                "[REDACTED]" if _is_sensitive_key(key) else _redact_json_credentials(item, token)
            )
        return redacted
    if isinstance(value, list):
        return [_redact_json_credentials(item, token) for item in value]
    if isinstance(value, str) and token:
        return value.replace(token, "[REDACTED]")
    return value


def _is_sensitive_key(key: str) -> bool:
    separated = CAMEL_CASE_BOUNDARY.sub("_", key)
    parts = [part for part in re.split(r"[^a-z0-9]+", separated.casefold()) if part]
    if any(part in SENSITIVE_KEY_PARTS for part in parts):
        return True
    normalized = "".join(parts)
    if normalized.endswith(SENSITIVE_KEY_SUFFIXES):
        return True
    pairs = set(pairwise(parts))
    return ("api", "key") in pairs or ("private", "key") in pairs
