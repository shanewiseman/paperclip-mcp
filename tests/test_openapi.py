from __future__ import annotations

from typing import Any

import pytest

from paperclip_mcp.errors import OpenAPIContractError
from paperclip_mcp.openapi import HTTP_METHODS, OperationRegistry


def test_canonical_contract_has_one_unique_tool_per_operation(
    registry: OperationRegistry,
) -> None:
    document_operations = [
        (method.upper(), path)
        for path, item in registry.document["paths"].items()
        for method in HTTP_METHODS
        if method in item
    ]

    assert len(registry.document["paths"]) == 475
    assert len(document_operations) == 590
    assert len(registry.operations) == 590
    assert len(registry.tags) == 36
    assert len(registry.by_name) == len(registry.by_key) == 590
    assert max(map(len, registry.by_name)) <= 64
    assert set(registry.by_key) == {f"{method} {path}" for method, path in document_operations}
    assert all(
        "operationId" not in registry.document["paths"][operation.path][operation.method.casefold()]
        for operation in registry.operations
    )


def test_direct_tools_preserve_schema_groups_and_safety_hints(
    registry: OperationRegistry,
) -> None:
    create_issue = registry.operation("POST /api/companies/{companyId}/issues")
    schema = create_issue.input_schema

    assert schema["required"] == ["path", "body"]
    assert schema["properties"]["path"]["required"] == ["companyId"]
    assert schema["properties"]["body"]["type"] == "object"
    assert create_issue.as_tool().annotations is not None
    assert create_issue.as_tool().annotations.readOnlyHint is False

    list_companies = registry.operation("GET /api/companies").as_tool()
    delete_company = registry.operation("DELETE /api/companies/{companyId}").as_tool()
    assert list_companies.annotations is not None
    assert list_companies.annotations.readOnlyHint is True
    assert delete_company.annotations is not None
    assert delete_company.annotations.destructiveHint is True


def test_oas_dialect_features_are_translated_for_mcp(
    registry: OperationRegistry,
) -> None:
    def objects(value: Any) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        if isinstance(value, dict):
            found.append(value)
            for child in value.values():
                found.extend(objects(child))
        elif isinstance(value, list):
            for child in value:
                found.extend(objects(child))
        return found

    input_objects = [
        item for operation in registry.operations for item in objects(operation.input_schema)
    ]
    assert not any("nullable" in item for item in input_objects)
    assert not any(
        isinstance(item.get("exclusiveMinimum"), bool)
        or isinstance(item.get("exclusiveMaximum"), bool)
        for item in input_objects
    )
    assert any(
        item.get("anyOf") == [{"type": "string"}, {"type": "null"}] for item in input_objects
    )


def test_upload_schema_exposes_bounded_compatibility_multipart(
    registry: OperationRegistry,
) -> None:
    operation = registry.operation("POST /api/companies/{companyId}/assets/images")

    assert operation.supports_multipart_fallback is True
    assert "multipart" in operation.input_schema["properties"]
    assert "source specification omits" in operation.description


def test_tag_filter_is_opt_in_and_unknown_operation_fails(
    registry: OperationRegistry,
) -> None:
    issues = OperationRegistry(registry.document, enabled_tags=frozenset({"issues"}))

    assert issues.operations
    assert {operation.tag for operation in issues.operations} == {"issues"}
    assert len(issues.operations) < len(registry.operations)
    with pytest.raises(OpenAPIContractError, match="unknown Paperclip operation"):
        issues.operation("GET /api/companies")
