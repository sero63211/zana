#!/usr/bin/env bash
# ZANA Core development server.
#
# Runs the loopback-only Core with a fresh per-launch bearer token unless the
# caller explicitly supplies ZANA_CORE_TOKEN. The token reaches Core through
# the environment only and is never printed or passed as a command argument.
set -euo pipefail

HOST="${ZANA_CORE_HOST:-127.0.0.1}"
PORT="${ZANA_CORE_PORT:-8000}"

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

case "$HOST" in
  127.0.0.1|localhost|::1) ;;
  *) fail "ZANA_CORE_HOST must be a loopback address (127.0.0.1, localhost, or ::1)." ;;
esac

case "$PORT" in
  ''|*[!0-9]*|0[0-9]*)
    fail "ZANA_CORE_PORT must be a numeric port between 1 and 65535."
    ;;
esac
if (( PORT < 1 || PORT > 65535 )); then
  fail "ZANA_CORE_PORT must be a numeric port between 1 and 65535."
fi

command -v uv >/dev/null 2>&1 ||
  fail "uv is required to run ZANA Core (see README setup)."

TOKEN="${ZANA_CORE_TOKEN:-}"
if [[ -z "$TOKEN" ]]; then
  command -v python3 >/dev/null 2>&1 ||
    fail "python3 is required to generate a per-run token when ZANA_CORE_TOKEN is not set."
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32), end="")')"
fi

cd "$(dirname "$0")/.."
printf 'Starting ZANA Core dev server on %s:%s\n' "$HOST" "$PORT"
ZANA_CORE_TOKEN="$TOKEN" exec uv run --project core zana-core serve --host "$HOST" --port "$PORT"
