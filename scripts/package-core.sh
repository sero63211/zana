#!/usr/bin/env bash
# Package ZANA Core as the target-qualified binary Tauri requires.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TARGET_TRIPLE="${TAURI_ENV_TARGET_TRIPLE:-$(rustc -vV | awk '/^host:/ { print $2 }')}"
DESTINATION="$ROOT_DIR/apps/desktop/src-tauri/binaries/zana-core-$TARGET_TRIPLE"

if [[ -z "$TARGET_TRIPLE" ]]; then
  echo "Unable to determine the Rust target triple." >&2
  exit 1
fi

cd "$ROOT_DIR"
uv run --project core pyinstaller \
  --noconfirm \
  --clean \
  --onefile \
  --name zana-core \
  --paths core \
  --distpath core/dist \
  --workpath core/build/pyinstaller \
  --specpath core/build \
  core/zana_core/main.py

install -m 755 "$ROOT_DIR/core/dist/zana-core" "$DESTINATION"
echo "Packaged ZANA Core sidecar: $DESTINATION"
