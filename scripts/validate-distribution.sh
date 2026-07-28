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

output_dir=$(mktemp -d /tmp/paperclip-mcp-dist.XXXXXX)
trap 'rm -r -- "$output_dir"' EXIT HUP INT TERM

"$uv_command" build --offline --wheel --sdist --out-dir "$output_dir"
wheel=$(find "$output_dir" -name 'paperclip_mcp-*.whl' -print -quit)
sdist=$(find "$output_dir" -name 'paperclip_mcp-*.tar.gz' -print -quit)
test -n "$wheel" && test -n "$sdist"

WHEEL="$wheel" SDIST="$sdist" .venv/bin/python - <<'PY'
import os
import tarfile
import zipfile

with zipfile.ZipFile(os.environ["WHEEL"]) as archive:
    names = archive.namelist()
    assert "paperclip_mcp/paperclip-openapi.json" in names
    assert "paperclip_mcp/server.py" in names
with tarfile.open(os.environ["SDIST"]) as archive:
    names = archive.getnames()
    assert any(name.endswith("/paperclip-openapi.json") for name in names)
PY

"$uv_command" pip install \
  --python .venv/bin/python \
  --no-deps \
  --target "$output_dir/installed" \
  "$wheel"
INSTALL_ROOT="$output_dir/installed" .venv/bin/python - <<'PY'
import os
import sys
from pathlib import Path

root = Path(os.environ["INSTALL_ROOT"]).resolve()
sys.path.insert(0, str(root))
import paperclip_mcp  # noqa: E402
from paperclip_mcp.openapi import OperationRegistry  # noqa: E402

assert Path(paperclip_mcp.__file__).resolve().is_relative_to(root)
assert len(OperationRegistry.load().operations) == 590
PY

echo "Validated wheel and sdist contents"
