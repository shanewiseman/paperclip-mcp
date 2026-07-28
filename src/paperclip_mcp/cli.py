"""Command-line entry points for HTTP, stdio, credentials, and generated docs."""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
from pathlib import Path

import uvicorn
from mcp.server.stdio import stdio_server

from paperclip_mcp.config import Settings
from paperclip_mcp.docs import generate_references
from paperclip_mcp.errors import PaperclipMCPError
from paperclip_mcp.openapi import OperationRegistry
from paperclip_mcp.server import Runtime, create_app


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="paperclip-mcp",
        description="Schema-driven MCP gateway for Paperclip",
    )
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("http", aliases=["serve"], help="run Streamable HTTP MCP")
    commands.add_parser("stdio", help="run MCP over stdio")
    token = commands.add_parser("token", help="generate an inbound MCP bearer token")
    token.add_argument("--output", type=Path)
    token.add_argument("--force", action="store_true", help="replace an existing output file")
    docs = commands.add_parser("generate-docs", help="regenerate committed MCP references")
    docs.add_argument("--output", type=Path, default=Path("docs/generated"))
    commands.add_parser("validate-openapi", help="parse and validate the operation registry")
    return root


def main() -> None:
    args = parser().parse_args()
    try:
        if args.command == "token":
            _write_token(args.output, force=args.force)
            return
        settings = Settings()
        if args.command == "generate-docs":
            registry = OperationRegistry.load(
                settings.openapi_path,
                enabled_tags=settings.enabled_tag_set,
            )
            destination = generate_references(args.output, registry)
            print(destination)
            return
        if args.command == "validate-openapi":
            registry = OperationRegistry.load(
                settings.openapi_path,
                enabled_tags=settings.enabled_tag_set,
            )
            print(
                f"validated {len(registry.operations)} operations across {len(registry.tags)} tags"
            )
            return

        runtime = Runtime.build(settings)
        if args.command == "stdio":
            asyncio.run(_run_stdio(runtime))
            return
        if args.command in {"http", "serve"}:
            uvicorn.run(
                create_app(runtime),
                host=settings.bind_host,
                port=settings.bind_port,
                log_level=settings.log_level.casefold(),
            )
            return
    except (PaperclipMCPError, ValueError) as exc:
        print(f"paperclip-mcp: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    raise SystemExit(2)


async def _run_stdio(runtime: Runtime) -> None:
    try:
        async with stdio_server() as (read_stream, write_stream):
            await runtime.server.run(
                read_stream,
                write_stream,
                runtime.server.create_initialization_options(),
            )
    finally:
        await runtime.client.close()


def _write_token(output: Path | None, *, force: bool) -> None:
    token = secrets.token_urlsafe(48)
    if output is None:
        print(token)
        return
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if force else os.O_EXCL)
    try:
        descriptor = os.open(output, flags, 0o600)
    except FileExistsError as exc:
        raise ValueError(f"token file already exists: {output}; use --force to rotate") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(f"{token}\n")
    output.chmod(0o600)
    print(output)
