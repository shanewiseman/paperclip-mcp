from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from paperclip_mcp.config import Settings
from paperclip_mcp.openapi import OperationRegistry


@pytest.fixture(scope="session")
def registry() -> OperationRegistry:
    return OperationRegistry.load()


@pytest.fixture
def settings_factory() -> Callable[..., Settings]:
    def factory(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "_env_file": None,
            "base_url": "https://paperclip.test",
        }
        values.update(overrides)
        return Settings(**values)

    return factory
