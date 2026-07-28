#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_dir"
export UV_CACHE_DIR=${UV_CACHE_DIR:-"$repository_dir/.ci-cache/uv"}

uv_command=uv
if ! command -v "$uv_command" >/dev/null 2>&1; then
  uv_command=.venv/bin/uv
fi
if [ ! -x "$uv_command" ] && ! command -v "$uv_command" >/dev/null 2>&1; then
  echo "uv is required" >&2
  exit 1
fi

"$uv_command" lock --check --offline
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src
.venv/bin/python -m compileall -q src tests
echo "Validated lock, lint, formatting, strict typing, and compilation"
