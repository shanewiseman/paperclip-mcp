#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_dir"

scripts/validate-static.sh
scripts/test-native.sh
scripts/validate-generated-docs.sh
scripts/validate-distribution.sh
scripts/validate-compose.sh
echo "Completed Paperclip MCP native validation"
