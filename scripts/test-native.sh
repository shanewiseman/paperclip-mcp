#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_dir"

mkdir -p artifacts
export COVERAGE_FILE=artifacts/.coverage

exec .venv/bin/python -m pytest \
  --basetemp=.ci-cache/pytest \
  --junitxml=artifacts/junit.xml \
  --cov=paperclip_mcp \
  --cov-branch \
  --cov-report=term-missing \
  --cov-report=xml:artifacts/coverage.xml \
  --cov-fail-under=85
