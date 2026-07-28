from __future__ import annotations

import stat
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from paperclip_mcp.config import Settings, read_secret
from paperclip_mcp.errors import ConfigurationError


def test_local_mode_needs_no_upstream_token(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()

    assert settings.resolved_upstream_token is None
    assert settings.resolved_mcp_token is None


def test_non_loopback_http_requires_inbound_token(
    settings_factory: Callable[..., Settings],
) -> None:
    with pytest.raises(ValidationError, match="required for a non-loopback bind"):
        settings_factory(bind_host="0.0.0.0")

    settings = settings_factory(
        bind_host="0.0.0.0",
        mcp_token="gateway-token",
    )
    assert settings.resolved_mcp_token == "gateway-token"


def test_inbound_and_upstream_tokens_must_be_separate(
    settings_factory: Callable[..., Settings],
) -> None:
    with pytest.raises(ValidationError, match="must be different"):
        settings_factory(
            mcp_token="same-token",
            upstream_token="same-token",
        )


def test_secret_file_precedence_and_empty_rejection(
    tmp_path: Path,
    settings_factory: Callable[..., Settings],
) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("from-file\n", encoding="utf-8")
    token_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    settings = settings_factory(
        upstream_token="from-environment",
        upstream_token_file=token_file,
    )
    assert settings.resolved_upstream_token == "from-file"

    token_file.write_text("", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="empty"):
        read_secret("fallback-is-not-used", token_file)


@pytest.mark.parametrize(
    "base_url",
    [
        "ftp://paperclip.test",
        "https://user:pass@paperclip.test",
        "https://paperclip.test?token=bad",
        "/relative",
    ],
)
def test_base_url_rejects_unsafe_forms(base_url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, base_url=base_url)
