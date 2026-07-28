from __future__ import annotations

import runpy
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from paperclip_mcp import cli


def test_token_generation_stdout_and_owner_only_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli._write_token(None, force=False)
    generated = capsys.readouterr().out.strip()
    assert len(generated) >= 64

    destination = tmp_path / "nested" / "token"
    cli._write_token(destination, force=False)
    assert destination.read_text(encoding="utf-8").strip()
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    with pytest.raises(ValueError, match="already exists"):
        cli._write_token(destination, force=False)
    cli._write_token(destination, force=True)


def test_validate_and_generate_docs_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["paperclip-mcp", "validate-openapi"])
    cli.main()
    assert "validated 590 operations across 36 tags" in capsys.readouterr().out

    output = tmp_path / "generated"
    monkeypatch.setattr(
        sys,
        "argv",
        ["paperclip-mcp", "generate-docs", "--output", str(output)],
    )
    cli.main()
    assert (output / "mcp-tools.md").is_file()


def test_http_and_stdio_commands_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel_runtime = object()
    uvicorn_calls: list[dict[str, Any]] = []
    coroutine_closed: list[bool] = []

    monkeypatch.setattr(cli.Runtime, "build", lambda _: sentinel_runtime)
    monkeypatch.setattr(cli, "create_app", lambda runtime: ("app", runtime))
    monkeypatch.setattr(
        cli.uvicorn,
        "run",
        lambda app, **kwargs: uvicorn_calls.append({"app": app, **kwargs}),
    )
    monkeypatch.setattr(sys, "argv", ["paperclip-mcp", "http"])
    cli.main()
    assert uvicorn_calls[0]["app"] == ("app", sentinel_runtime)
    assert uvicorn_calls[0]["host"] == "127.0.0.1"

    async def fake_stdio(_: object) -> None:
        coroutine_closed.append(True)

    monkeypatch.setattr(cli, "_run_stdio", fake_stdio)
    monkeypatch.setattr(sys, "argv", ["paperclip-mcp", "stdio"])
    cli.main()
    assert coroutine_closed == [True]


def test_main_reports_domain_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    destination = tmp_path / "token"
    destination.write_text("existing", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["paperclip-mcp", "token", "--output", str(destination)],
    )
    with pytest.raises(SystemExit) as raised:
        cli.main()
    assert raised.value.code == 2
    assert "already exists" in capsys.readouterr().err


def test_package_main_module_dispatches(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["paperclip-mcp", "validate-openapi"])
    runpy.run_module("paperclip_mcp.__main__", run_name="__main__")
    assert "validated 590 operations" in capsys.readouterr().out
