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

"$uv_command" sync --frozen --all-extras
mkdir -p secrets
chmod 700 secrets
if [ ! -s secrets/mcp_token ]; then
  .venv/bin/paperclip-mcp token --output secrets/mcp_token
fi
echo "Bootstrap complete. Omit PAPERCLIP_API_TOKEN for Paperclip local_mode."
