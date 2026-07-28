"""Runtime configuration and separate inbound/outbound credential handling."""

from __future__ import annotations

import ipaddress
import ssl
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from paperclip_mcp.errors import ConfigurationError


def read_secret(value: str | None, file_path: Path | None) -> str | None:
    """Prefer a mounted secret file and reject explicitly configured empty secrets."""
    if file_path is not None:
        try:
            secret = file_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigurationError(f"cannot read configured secret file: {file_path}") from exc
        if not secret:
            raise ConfigurationError(f"configured secret file is empty: {file_path}")
        return secret
    return value.strip() if value and value.strip() else None


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.casefold() == "localhost"


class Settings(BaseSettings):
    """Settings loaded from explicit `PAPERCLIP_*` environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    base_url: str = Field(
        default="http://127.0.0.1:3100",
        validation_alias=AliasChoices("PAPERCLIP_BASE_URL", "PAPERCLIP_MCP_BASE_URL"),
    )
    upstream_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PAPERCLIP_API_TOKEN", "PAPERCLIP_MCP_UPSTREAM_TOKEN"),
    )
    upstream_token_file: Path | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "PAPERCLIP_API_TOKEN_FILE",
            "PAPERCLIP_MCP_UPSTREAM_TOKEN_FILE",
        ),
    )
    mcp_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PAPERCLIP_MCP_TOKEN", "PAPERCLIP_MCP_API_TOKEN"),
    )
    mcp_token_file: Path | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "PAPERCLIP_MCP_TOKEN_FILE",
            "PAPERCLIP_MCP_API_TOKEN_FILE",
        ),
    )
    bind_host: str = Field(
        default="127.0.0.1",
        validation_alias="PAPERCLIP_MCP_BIND_HOST",
    )
    bind_port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        validation_alias="PAPERCLIP_MCP_BIND_PORT",
    )
    request_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        le=300,
        validation_alias="PAPERCLIP_MCP_REQUEST_TIMEOUT_SECONDS",
    )
    max_response_bytes: int = Field(
        default=8 * 1024 * 1024,
        ge=1024,
        le=128 * 1024 * 1024,
        validation_alias="PAPERCLIP_MCP_MAX_RESPONSE_BYTES",
    )
    max_request_bytes: int = Field(
        default=8 * 1024 * 1024,
        ge=1024,
        le=128 * 1024 * 1024,
        validation_alias="PAPERCLIP_MCP_MAX_REQUEST_BYTES",
    )
    ca_bundle: Path | None = Field(
        default=None,
        validation_alias="PAPERCLIP_MCP_CA_BUNDLE",
    )
    openapi_path: Path | None = Field(
        default=None,
        validation_alias="PAPERCLIP_MCP_OPENAPI_PATH",
    )
    enabled_tags: str | None = Field(
        default=None,
        validation_alias="PAPERCLIP_MCP_ENABLED_TAGS",
    )
    allowed_hosts: str = Field(
        default=(
            "127.0.0.1,127.0.0.1:*,localhost,localhost:*,"
            "[::1],[::1]:*,paperclip-mcp,paperclip-mcp:*"
        ),
        validation_alias="PAPERCLIP_MCP_ALLOWED_HOSTS",
    )
    allowed_origins: str = Field(
        default="http://127.0.0.1:*,http://localhost:*,http://[::1]:*",
        validation_alias="PAPERCLIP_MCP_ALLOWED_ORIGINS",
    )
    log_level: str = Field(default="INFO", validation_alias="PAPERCLIP_MCP_LOG_LEVEL")

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Paperclip base URL must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Paperclip base URL cannot contain credentials, query, or fragment")
        return value.rstrip("/")

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("unsupported log level")
        return normalized

    @model_validator(mode="after")
    def validate_security_boundary(self) -> Settings:
        inbound = read_secret(self.mcp_token, self.mcp_token_file)
        outbound = read_secret(self.upstream_token, self.upstream_token_file)
        if not _is_loopback(self.bind_host) and inbound is None:
            raise ValueError("PAPERCLIP_MCP_TOKEN is required for a non-loopback bind")
        if inbound is not None and outbound is not None and inbound == outbound:
            raise ValueError("inbound MCP and upstream Paperclip tokens must be different")
        if self.ca_bundle is not None and not self.ca_bundle.is_file():
            raise ValueError("configured CA bundle is not a readable file")
        return self

    @property
    def resolved_mcp_token(self) -> str | None:
        return read_secret(self.mcp_token, self.mcp_token_file)

    @property
    def resolved_upstream_token(self) -> str | None:
        """Return `None` for Paperclip local mode, where upstream auth is disabled."""
        return read_secret(self.upstream_token, self.upstream_token_file)

    @property
    def enabled_tag_set(self) -> frozenset[str] | None:
        if self.enabled_tags is None:
            return None
        tags = frozenset(part.strip() for part in self.enabled_tags.split(",") if part.strip())
        return tags or None

    @property
    def allowed_host_patterns(self) -> list[str]:
        return [item.strip() for item in self.allowed_hosts.split(",") if item.strip()]

    @property
    def allowed_origin_patterns(self) -> list[str]:
        return [item.strip() for item in self.allowed_origins.split(",") if item.strip()]

    @property
    def httpx_verify(self) -> bool | ssl.SSLContext:
        if self.ca_bundle is None:
            return True
        return ssl.create_default_context(cafile=str(self.ca_bundle))

    @classmethod
    def from_overrides(cls, **overrides: Any) -> Settings:
        """Typed construction helper used by integrations and tests."""
        return cls(**overrides)
