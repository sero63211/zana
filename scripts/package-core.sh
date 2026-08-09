#!/usr/bin/env bash
# Package ZANA Core as the target-qualified sidecar binary expected by Tauri.
#
# The build runs through the repository's existing uv/PyInstaller toolchain
# without syncing or installing dependencies, stages output inside the ZANA
# checkout, and atomically publishes the final binary under
# apps/desktop/src-tauri/binaries/. It never downloads models, writes outside
# the ZANA repository, or prints secrets or absolute paths.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"
BINARIES_DIR="$ROOT_DIR/apps/desktop/src-tauri/binaries"
CORE_ENTRY="$ROOT_DIR/core/zana_core/main.py"
TARGET_TRIPLE="${TAURI_ENV_TARGET_TRIPLE:-}"

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

canonical_path() {
  local path="$1"
  local dir
  dir="$(cd "$(dirname "$path")" && pwd -P)" || return 1
  printf '%s/%s\n' "$dir" "$(basename "$path")"
}

# Strict repository validation: only the canonical ZANA checkout is accepted.
[[ -f "$CORE_ENTRY" ]] ||
  fail "core/zana_core/main.py is missing; restore the Core source before packaging."
[[ -d "$BINARIES_DIR" ]] ||
  fail "apps/desktop/src-tauri/binaries is missing; restore the sidecar directory."
[[ -f "$ROOT_DIR/scripts/package-core.sh" ]] ||
  fail "the packaging script is not running from the ZANA checkout."

case "$ROOT_DIR" in
  */zana) ;;
  *) fail "refusing to package from a non-ZANA repository checkout." ;;
esac

CORE_RESOLVED="$(canonical_path "$CORE_ENTRY")" ||
  fail "could not resolve the Core entry inside the repository."
BINARIES_RESOLVED="$(cd "$BINARIES_DIR" && pwd -P)" ||
  fail "could not resolve the sidecar directory inside the repository."

case "$CORE_RESOLVED" in
  "$ROOT_DIR"/*) ;;
  *) fail "refusing to read Core outside the ZANA repository." ;;
esac
case "$BINARIES_RESOLVED" in
  "$ROOT_DIR"/*) ;;
  *) fail "refusing to write outside the ZANA repository." ;;
esac
[[ -L "$CORE_ENTRY" ]] &&
  fail "the Core entry must not be a symlink."
[[ -L "$BINARIES_DIR" ]] &&
  fail "the sidecar directory must not be a symlink."

# Reproducible target triple: default to the Rust host and never mislabel a
# cross-architecture binary.
command -v rustc >/dev/null 2>&1 ||
  fail "rustc is required to determine the host target triple."
HOST_TRIPLE="$(rustc -vV | awk '/^host:/ { print $2 }')"
if [[ -z "$TARGET_TRIPLE" ]]; then
  TARGET_TRIPLE="$HOST_TRIPLE"
fi
if [[ "$TARGET_TRIPLE" != "$HOST_TRIPLE" ]]; then
  fail "TAURI_ENV_TARGET_TRIPLE does not match the host target triple; PyInstaller cannot cross-compile ZANA Core."
fi
if [[ ! "$TARGET_TRIPLE" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]]; then
  fail "invalid Rust target triple."
fi

DESTINATION="$BINARIES_RESOLVED/zana-core-$TARGET_TRIPLE"

cd "$ROOT_DIR"

# Existing toolchain only; fail honestly before doing any build work.
command -v uv >/dev/null 2>&1 ||
  fail "uv is required to build the Core sidecar."
if ! uv run --project core --no-sync python -c 'import PyInstaller' >/dev/null 2>&1; then
  fail "PyInstaller is not available in the Core environment. Run 'uv sync --project core' once, then rerun package-core.sh."
fi

STAGING_DIR=""
TEMP_DESTINATION=""
cleanup() {
  if [[ -n "$STAGING_DIR" ]]; then
    rm -rf -- "$STAGING_DIR"
  fi
  if [[ -n "$TEMP_DESTINATION" ]]; then
    rm -f -- "$TEMP_DESTINATION"
  fi
}
trap cleanup EXIT
trap 'cleanup; exit 1' INT TERM HUP

STAGING_DIR="$(mktemp -d "$ROOT_DIR/.package-core-staging.XXXXXX")" ||
  fail "could not create a staging directory."

BUILD_LOG="$STAGING_DIR/pyinstaller.log"
if ! uv run --project core --no-sync pyinstaller \
  --noconfirm \
  --clean \
  --onefile \
  --name zana-core \
  --paths core \
  --distpath "$STAGING_DIR/dist" \
  --workpath "$STAGING_DIR/work" \
  --specpath "$STAGING_DIR" \
  "$CORE_ENTRY" >"$BUILD_LOG" 2>&1; then
  printf 'error: PyInstaller failed to build ZANA Core.\n' >&2
  printf 'error: the build log is not exposed; verify the Core environment, then rerun packaging.\n' >&2
  exit 1
fi

SOURCE_BINARY="$STAGING_DIR/dist/zana-core"
[[ -f "$SOURCE_BINARY" ]] ||
  fail "PyInstaller finished without producing the expected sidecar binary."
[[ -x "$SOURCE_BINARY" ]] ||
  fail "The built sidecar is not executable; packaging cannot continue."

mkdir -p "$BINARIES_RESOLVED"
TEMP_DESTINATION="$(mktemp "$DESTINATION.tmp.XXXXXX")" ||
  fail "could not create a staging file for publication."
if ! install -m 755 "$SOURCE_BINARY" "$TEMP_DESTINATION"; then
  printf 'error: could not stage the sidecar binary for publication.\n' >&2
  exit 1
fi
if ! mv -f "$TEMP_DESTINATION" "$DESTINATION"; then
  printf 'error: could not publish the sidecar binary at the Tauri location.\n' >&2
  exit 1
fi
TEMP_DESTINATION=""
printf 'Packaged ZANA Core sidecar: apps/desktop/src-tauri/binaries/zana-core-%s\n' "$TARGET_TRIPLE"
