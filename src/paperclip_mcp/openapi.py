"""Load Paperclip OpenAPI and derive the complete MCP operation registry."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from mcp import types

from paperclip_mcp.errors import OpenAPIContractError

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
MAX_TOOL_NAME_LENGTH = 64
DESTRUCTIVE_WORDS = frozenset(
    {
        "archive",
        "cancel",
        "decline",
        "delete",
        "demote",
        "disable",
        "force",
        "pause",
        "reject",
        "release",
        "reset",
        "restore",
        "revoke",
        "rollback",
        "stop",
        "terminate",
        "unarchive",
        "unlink",
        "unstar",
    }
)


@dataclass(frozen=True, slots=True)
class Parameter:
    """One inline OpenAPI path or query parameter."""

    name: str
    location: str
    required: bool
    schema: dict[str, Any]
    style: str | None
    explode: bool | None


@dataclass(frozen=True, slots=True)
class Operation:
    """A single allowlisted Paperclip operation and its MCP representation."""

    key: str
    tool_name: str
    method: str
    path: str
    tag: str
    summary: str
    description: str
    actor: str
    requires_auth: bool
    parameters: tuple[Parameter, ...]
    body_schema: dict[str, Any] | None
    body_required: bool
    supports_multipart_fallback: bool
    input_schema: dict[str, Any]

    @property
    def path_parameters(self) -> tuple[Parameter, ...]:
        return tuple(item for item in self.parameters if item.location == "path")

    @property
    def query_parameters(self) -> tuple[Parameter, ...]:
        return tuple(item for item in self.parameters if item.location == "query")

    @property
    def destructive(self) -> bool:
        if self.method == "DELETE":
            return True
        words = set(re.findall(r"[a-z]+", self.summary.casefold()))
        return bool(words & DESTRUCTIVE_WORDS)

    def as_tool(self) -> types.Tool:
        """Build the MCP tool declaration, including conservative safety hints."""
        read_only = self.method == "GET" and self.path != "/api/tools/oauth/callback"
        return types.Tool(
            name=self.tool_name,
            title=self.summary,
            description=self.description,
            inputSchema=self.input_schema,
            outputSchema=response_envelope_schema(),
            annotations=types.ToolAnnotations(
                title=self.summary,
                readOnlyHint=read_only,
                destructiveHint=self.destructive,
                idempotentHint=self.method in {"GET", "PUT", "DELETE"},
                openWorldHint=True,
            ),
            _meta={
                "paperclip": {
                    "operation": self.key,
                    "tag": self.tag,
                    "authorization": self.actor,
                }
            },
        )


class OperationRegistry:
    """Validated, deterministic registry generated from the canonical document."""

    def __init__(
        self,
        document: dict[str, Any],
        *,
        enabled_tags: frozenset[str] | None = None,
    ) -> None:
        self.document = document
        self.version = _require_string(document.get("openapi"), "openapi version")
        info = _require_mapping(document.get("info"), "info")
        self.api_version = _require_string(info.get("version"), "info.version")
        self.title = _require_string(info.get("title"), "info.title")
        self.operations = tuple(_build_operations(document, enabled_tags=enabled_tags))
        if not self.operations:
            raise OpenAPIContractError("OpenAPI selection produced no operations")

        self.by_name = {operation.tool_name: operation for operation in self.operations}
        self.by_key = {operation.key: operation for operation in self.operations}
        if len(self.by_name) != len(self.operations):
            raise OpenAPIContractError("generated MCP tool names are not unique")
        if len(self.by_key) != len(self.operations):
            raise OpenAPIContractError("OpenAPI method/path operation keys are not unique")
        self.tags = tuple(sorted({operation.tag for operation in self.operations}))

    @classmethod
    def load(
        cls,
        path: Path | None = None,
        *,
        enabled_tags: frozenset[str] | None = None,
    ) -> OperationRegistry:
        return cls(load_document(path), enabled_tags=enabled_tags)

    def operation(self, identifier: str) -> Operation:
        try:
            return self.by_name.get(identifier) or self.by_key[identifier]
        except KeyError as exc:
            raise OpenAPIContractError(f"unknown Paperclip operation: {identifier}") from exc

    def manifest(self) -> list[dict[str, Any]]:
        return [
            {
                "operation": operation.key,
                "tool": operation.tool_name,
                "tag": operation.tag,
                "summary": operation.summary,
                "authorization": operation.actor,
                "read_only": operation.method == "GET",
                "destructive": operation.destructive,
            }
            for operation in self.operations
        ]


def load_document(path: Path | None = None) -> dict[str, Any]:
    """Load an override, installed package resource, or source-checkout document."""
    if path is not None:
        candidate = path
        try:
            payload = candidate.read_text(encoding="utf-8")
        except OSError as exc:
            raise OpenAPIContractError(f"cannot read OpenAPI document: {candidate}") from exc
    else:
        packaged = files("paperclip_mcp").joinpath("paperclip-openapi.json")
        if packaged.is_file():
            payload = packaged.read_text(encoding="utf-8")
        else:
            candidate = Path(__file__).resolve().parents[2] / "paperclip-openapi.json"
            try:
                payload = candidate.read_text(encoding="utf-8")
            except OSError as exc:
                raise OpenAPIContractError(
                    "paperclip-openapi.json is absent from the package and source checkout"
                ) from exc
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise OpenAPIContractError(f"OpenAPI document is invalid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise OpenAPIContractError("OpenAPI document root must be an object")
    return document


def response_envelope_schema() -> dict[str, Any]:
    """Stable wrapper because Paperclip success responses are mostly open schemas."""
    return {
        "type": "object",
        "properties": {
            "status_code": {"type": "integer", "minimum": 200, "maximum": 399},
            "content_type": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
            },
            "headers": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
            "body": {},
            "encoding": {
                "type": "string",
                "enum": ["json", "text", "base64", "empty"],
            },
        },
        "required": ["status_code", "content_type", "headers", "body", "encoding"],
        "additionalProperties": False,
    }


def _build_operations(
    document: dict[str, Any],
    *,
    enabled_tags: frozenset[str] | None,
) -> list[Operation]:
    paths = _require_mapping(document.get("paths"), "paths")
    global_security = document.get("security")
    operations: list[Operation] = []

    for path, raw_path_item in paths.items():
        if not isinstance(path, str) or not path.startswith("/"):
            raise OpenAPIContractError(f"invalid OpenAPI path key: {path!r}")
        path_item = _require_mapping(raw_path_item, f"path item {path}")
        path_parameters = _parameter_list(path_item.get("parameters", []), f"{path} parameters")

        for method in HTTP_METHODS:
            raw_operation = path_item.get(method)
            if raw_operation is None:
                continue
            operation_data = _require_mapping(raw_operation, f"{method.upper()} {path}")
            tags = operation_data.get("tags")
            if not isinstance(tags, list) or len(tags) != 1 or not isinstance(tags[0], str):
                raise OpenAPIContractError(f"{method.upper()} {path} must declare exactly one tag")
            tag = tags[0]
            if enabled_tags is not None and tag not in enabled_tags:
                continue

            summary = _require_string(
                operation_data.get("summary"),
                f"{method.upper()} {path} summary",
            )
            parameters = _merge_parameters(
                path_parameters,
                _parameter_list(
                    operation_data.get("parameters", []),
                    f"{method.upper()} {path} parameters",
                ),
            )
            body_schema, body_required = _request_body(operation_data, method.upper(), path)
            authorization = _require_mapping(
                operation_data.get("x-paperclip-authorization"),
                f"{method.upper()} {path} x-paperclip-authorization",
            )
            actor = _require_string(
                authorization.get("actor"),
                f"{method.upper()} {path} authorization actor",
            )
            if authorization.get("instanceAdmin") is True:
                actor = "instance_admin"
            security = operation_data.get("security", global_security)
            if not isinstance(security, list):
                raise OpenAPIContractError(f"{method.upper()} {path} security must be an array")

            supports_multipart = (
                body_schema is None
                and method == "post"
                and summary.casefold().startswith("upload ")
            )
            input_schema = _input_schema(
                parameters,
                body_schema,
                body_required,
                allow_unspecified_body=body_schema is None
                and method in {"post", "put", "patch", "delete"},
                supports_multipart=supports_multipart,
            )
            key = f"{method.upper()} {path}"
            description = _tool_description(operation_data, key, actor, supports_multipart)
            operations.append(
                Operation(
                    key=key,
                    tool_name=_tool_name(method, path),
                    method=method.upper(),
                    path=path,
                    tag=tag,
                    summary=summary,
                    description=description,
                    actor=actor,
                    requires_auth=bool(security),
                    parameters=parameters,
                    body_schema=body_schema,
                    body_required=body_required,
                    supports_multipart_fallback=supports_multipart,
                    input_schema=input_schema,
                )
            )
    return operations


def _parameter_list(raw: Any, context: str) -> tuple[Parameter, ...]:
    if not isinstance(raw, list):
        raise OpenAPIContractError(f"{context} must be an array")
    result: list[Parameter] = []
    for value in raw:
        item = _require_mapping(value, context)
        if "$ref" in item:
            raise OpenAPIContractError(f"{context} contains an unsupported parameter reference")
        location = _require_string(item.get("in"), f"{context} parameter location")
        if location not in {"path", "query"}:
            raise OpenAPIContractError(
                f"{context} uses unsupported parameter location {location!r}"
            )
        name = _require_string(item.get("name"), f"{context} parameter name")
        required = item.get("required", False)
        if not isinstance(required, bool):
            raise OpenAPIContractError(f"{context} parameter {name!r} has non-boolean required")
        if location == "path" and not required:
            raise OpenAPIContractError(f"{context} path parameter {name!r} must be required")
        schema = _oas_to_json_schema(
            _require_mapping(item.get("schema"), f"{context} parameter {name!r} schema")
        )
        if "description" in item and "description" not in schema:
            schema["description"] = str(item["description"])
        explode = item.get("explode")
        if explode is not None and not isinstance(explode, bool):
            raise OpenAPIContractError(f"{context} parameter {name!r} has invalid explode")
        style = item.get("style")
        if style is not None and not isinstance(style, str):
            raise OpenAPIContractError(f"{context} parameter {name!r} has invalid style")
        result.append(
            Parameter(
                name=name,
                location=location,
                required=required,
                schema=schema,
                style=style,
                explode=explode,
            )
        )
    return tuple(result)


def _merge_parameters(
    path_parameters: tuple[Parameter, ...],
    operation_parameters: tuple[Parameter, ...],
) -> tuple[Parameter, ...]:
    merged: dict[tuple[str, str], Parameter] = {
        (item.location, item.name): item for item in path_parameters
    }
    for item in operation_parameters:
        merged[(item.location, item.name)] = item
    return tuple(merged.values())


def _request_body(
    operation: dict[str, Any],
    method: str,
    path: str,
) -> tuple[dict[str, Any] | None, bool]:
    request_body = operation.get("requestBody")
    if request_body is None:
        return None, False
    body = _require_mapping(request_body, f"{method} {path} requestBody")
    content = _require_mapping(body.get("content"), f"{method} {path} requestBody.content")
    unsupported = set(content) - {"application/json"}
    if unsupported:
        names = ", ".join(sorted(unsupported))
        raise OpenAPIContractError(f"{method} {path} uses unsupported request media: {names}")
    media = _require_mapping(
        content.get("application/json"),
        f"{method} {path} application/json request",
    )
    schema = _oas_to_json_schema(
        _require_mapping(media.get("schema"), f"{method} {path} request schema")
    )
    required = body.get("required", False)
    if not isinstance(required, bool):
        raise OpenAPIContractError(f"{method} {path} requestBody.required must be boolean")
    return schema, required


def _input_schema(
    parameters: tuple[Parameter, ...],
    body_schema: dict[str, Any] | None,
    body_required: bool,
    *,
    allow_unspecified_body: bool,
    supports_multipart: bool,
) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for location in ("path", "query"):
        selected = [item for item in parameters if item.location == location]
        if not selected:
            continue
        nested_required = [item.name for item in selected if item.required]
        properties[location] = {
            "type": "object",
            "properties": {item.name: copy.deepcopy(item.schema) for item in selected},
            "additionalProperties": False,
        }
        if nested_required:
            properties[location]["required"] = nested_required
        if location == "path" or nested_required:
            required.append(location)

    if body_schema is not None:
        properties["body"] = copy.deepcopy(body_schema)
        if body_required:
            required.append("body")
    elif allow_unspecified_body:
        properties["body"] = {
            "description": (
                "Optional JSON compatibility payload. The source OpenAPI operation does not "
                "declare a request schema."
            )
        }

    if supports_multipart:
        properties["multipart"] = {
            "type": "object",
            "description": (
                "Compatibility fallback for an upload omitted from the source OpenAPI schema. "
                "Use a field value object with filename, media_type, and data_base64 for files."
            ),
            "additionalProperties": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "number"},
                    {"type": "boolean"},
                    {
                        "type": "object",
                        "properties": {
                            "filename": {"type": "string", "minLength": 1},
                            "media_type": {"type": "string", "minLength": 1},
                            "data_base64": {"type": "string", "minLength": 1},
                        },
                        "required": ["filename", "data_base64"],
                        "additionalProperties": False,
                    },
                ]
            },
        }

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    if supports_multipart and "body" in properties:
        schema["allOf"] = [{"not": {"required": ["body", "multipart"]}}]
    return schema


def _oas_to_json_schema(value: dict[str, Any]) -> dict[str, Any]:
    """Translate the OAS 3.0 schema dialect features used by Paperclip."""

    def convert(item: Any) -> Any:
        if isinstance(item, list):
            return [convert(child) for child in item]
        if not isinstance(item, dict):
            return copy.deepcopy(item)

        converted = {key: convert(child) for key, child in item.items() if key != "nullable"}
        for keyword in ("exclusiveMinimum", "exclusiveMaximum"):
            flag = item.get(keyword)
            boundary = "minimum" if keyword == "exclusiveMinimum" else "maximum"
            if flag is True and boundary in converted:
                converted[keyword] = converted.pop(boundary)
            elif flag is False:
                converted.pop(keyword, None)
        if item.get("nullable") is True:
            return {"anyOf": [converted, {"type": "null"}]}
        return converted

    result = convert(value)
    if not isinstance(result, dict):  # pragma: no cover - guarded by input type
        raise OpenAPIContractError("converted schema is not an object")
    return result


def _tool_name(method: str, path: str) -> str:
    parts: list[str] = []
    for raw_part in path.strip("/").split("/"):
        if raw_part == "api" and not parts:
            continue
        if raw_part.startswith("{") and raw_part.endswith("}"):
            parts.append(f"by_{_snake(raw_part[1:-1])}")
        else:
            parts.append(_snake(raw_part))
    base = "_".join(filter(None, ("pc", method.casefold(), *parts)))
    if len(base) <= MAX_TOOL_NAME_LENGTH:
        return base
    digest = hashlib.sha256(f"{method.upper()} {path}".encode()).hexdigest()[:10]
    prefix_length = MAX_TOOL_NAME_LENGTH - len(digest) - 1
    return f"{base[:prefix_length].rstrip('_')}_{digest}"


def _snake(value: str) -> str:
    with_boundaries = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[^a-zA-Z0-9]+", "_", with_boundaries).strip("_").casefold()


def _tool_description(
    operation: dict[str, Any],
    key: str,
    actor: str,
    supports_multipart: bool,
) -> str:
    summary = str(operation["summary"]).strip()
    details = str(operation.get("description", "")).strip()
    chunks = [summary, f"Paperclip operation: {key}. Authorization class: {actor}."]
    if details:
        chunks.append(details)
    if supports_multipart:
        chunks.append(
            "The source specification omits the upload body; this gateway provides a "
            "documented multipart compatibility input."
        )
    return "\n\n".join(chunks)


def _require_mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OpenAPIContractError(f"{context} must be an object")
    return value


def _require_string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise OpenAPIContractError(f"{context} must be a non-empty string")
    return value
