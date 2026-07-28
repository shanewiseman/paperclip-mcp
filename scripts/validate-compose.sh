#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_dir"

secret_dir=$(mktemp -d /tmp/paperclip-mcp-compose.XXXXXX)
trap 'rm -r -- "$secret_dir"' EXIT HUP INT TERM
printf '%s\n' compose-validation-inbound-token >"$secret_dir/mcp-token"
printf '%s\n' compose-validation-upstream-token >"$secret_dir/api-token"
chmod 600 "$secret_dir/mcp-token" "$secret_dir/api-token"

export PAPERCLIP_MCP_TOKEN_FILE="$secret_dir/mcp-token"
docker compose config --quiet
export PAPERCLIP_API_TOKEN_FILE="$secret_dir/api-token"
docker compose -f compose.yaml -f compose.auth.yaml config --quiet
echo "Validated local_mode and authenticated Compose contracts"
