#!/usr/bin/env bash
# ZANA test runner.
#
# Runs Core backend tests via pytest.
# Frontend tests are delegated to apps/desktop (owned by T004-desktop).
set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== ZANA Core tests ==="
uv run --project core pytest core/tests -v "$@"

echo ""
echo "=== ZANA Core lint ==="
uv run --project core ruff check core

echo ""
echo "=== ZANA Core type check ==="
uv run --project core pyright core/zana_core
