#!/usr/bin/env bash
# ZANA development server runner.
#
# Starts the Core backend on a chosen port with a dev token.
# The desktop lane provides its own dev flow under apps/desktop.
set -euo pipefail

HOST="${ZANA_CORE_HOST:-127.0.0.1}"
PORT="${ZANA_CORE_PORT:-8000}"
TOKEN="${ZANA_CORE_TOKEN:-zana-dev-token}"

cd "$(dirname "$0")/.."

echo "Starting ZANA Core dev server on ${HOST}:${PORT}"
uv run --project core zana-core serve \
  --host "$HOST" \
  --port "$PORT" \
  --token "$TOKEN"
