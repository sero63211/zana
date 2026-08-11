#!/usr/bin/env bash
# Package the Rust ZANA Core as the target-qualified sidecar binary expected
# by Tauri.
#
# The build runs through the repository's Cargo workspace without syncing or
# installing dependencies, stages output inside the ZANA checkout, and
# atomically publishes the final binary under
# apps/desktop/src-tauri/binaries/. It never runs Python, downloads models,
# writes outside the ZANA repository, or prints secrets or absolute paths.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"
BINARIES_DIR="$ROOT_DIR/apps/desktop/src-tauri/binaries"
MANIFEST="$ROOT_DIR/Cargo.toml"
TARGET_TRIPLE="${TAURI_ENV_TARGET_TRIPLE:-}"

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

# Strict repository validation: only the canonical ZANA checkout is accepted.
[[ -f "$MANIFEST" ]] ||
  fail "the Rust workspace manifest is missing; restore Cargo.toml before packaging."
[[ -f "$ROOT_DIR/Cargo.lock" ]] ||
  fail "Cargo.lock is missing; run a workspace check once before packaging."
[[ -d "$BINARIES_DIR" ]] ||
  fail "apps/desktop/src-tauri/binaries is missing; restore the sidecar directory."
[[ -f "$ROOT_DIR/scripts/package-core.sh" ]] ||
  fail "the packaging script is not running from the ZANA checkout."

case "$ROOT_DIR" in
  */zana) ;;
  *) fail "refusing to package from a non-ZANA repository checkout." ;;
esac

BINARIES_RESOLVED="$(cd "$BINARIES_DIR" && pwd -P)" ||
  fail "could not resolve the sidecar directory inside the repository."

case "$BINARIES_RESOLVED" in
  "$ROOT_DIR"/*) ;;
  *) fail "refusing to write outside the ZANA repository." ;;
esac
[[ -L "$BINARIES_DIR" ]] &&
  fail "the sidecar directory must not be a symlink."

# Reproducible target triple: default to the Rust host and never mislabel a
# cross-architecture binary.
command -v cargo >/dev/null 2>&1 ||
  fail "cargo is required to build the ZANA Core sidecar."
command -v rustc >/dev/null 2>&1 ||
  fail "rustc is required to determine the host target triple."
HOST_TRIPLE="$(rustc -vV | awk '/^host:/ { print $2 }')"
if [[ -z "$TARGET_TRIPLE" ]]; then
  TARGET_TRIPLE="$HOST_TRIPLE"
fi
if [[ "$TARGET_TRIPLE" != "$HOST_TRIPLE" ]]; then
  fail "TAURI_ENV_TARGET_TRIPLE does not match the host target triple; Rust Core cannot be cross-packaged by this script."
fi
if [[ ! "$TARGET_TRIPLE" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]]; then
  fail "invalid Rust target triple."
fi

SUFFIX=""
case "$TARGET_TRIPLE" in
  *windows*) SUFFIX=".exe" ;;
esac
DESTINATION="$BINARIES_RESOLVED/zana-core-$TARGET_TRIPLE$SUFFIX"
SOURCE_BINARY="$ROOT_DIR/target/$TARGET_TRIPLE/release/zana-core$SUFFIX"

cd "$ROOT_DIR"

TEMP_DESTINATION=""
cleanup() {
  if [[ -n "$TEMP_DESTINATION" ]]; then
    rm -f -- "$TEMP_DESTINATION"
  fi
}
trap cleanup EXIT
trap 'cleanup; exit 1' INT TERM HUP

if ! cargo build \
  --release \
  --locked \
  --manifest-path "$MANIFEST" \
  --target "$TARGET_TRIPLE" \
  -p zana-core-server; then
  printf 'error: Cargo failed to build ZANA Core.\n' >&2
  printf 'error: see the Cargo output above, fix the Rust workspace, then rerun packaging.\n' >&2
  exit 1
fi

[[ -f "$SOURCE_BINARY" ]] ||
  fail "Cargo finished without producing the expected sidecar binary."
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
printf 'Packaged ZANA Core sidecar: apps/desktop/src-tauri/binaries/zana-core-%s%s\n' \
  "$TARGET_TRIPLE" "$SUFFIX"
