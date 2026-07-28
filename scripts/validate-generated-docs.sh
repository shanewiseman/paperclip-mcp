#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_dir"

output_dir=$(mktemp -d /tmp/paperclip-mcp-docs.XXXXXX)
trap 'rm -r -- "$output_dir"' EXIT HUP INT TERM

.venv/bin/paperclip-mcp generate-docs --output "$output_dir" >/dev/null
diff -u docs/generated/mcp-tools.md "$output_dir/mcp-tools.md"
echo "Validated generated MCP tool reference"
