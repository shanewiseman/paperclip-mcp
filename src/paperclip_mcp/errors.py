"""Domain errors with deliberately credential-free messages."""

from __future__ import annotations

from typing import Any


class PaperclipMCPError(Exception):
    """Base class for expected service failures."""


class ConfigurationError(PaperclipMCPError):
    """Runtime configuration is invalid."""


class OpenAPIContractError(PaperclipMCPError):
    """The bundled OpenAPI document cannot produce a safe operation registry."""


class UpstreamError(PaperclipMCPError):
    """A Paperclip API request failed."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id


JsonObject = dict[str, Any]
