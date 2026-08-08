#!/usr/bin/env bash
# Package ZANA Core as a distributable sidecar binary using PyInstaller.
#
# Not required for M0; placeholder for future distribution.
# The packaged binary is launched by Tauri as a sidecar process.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "Packaging ZANA Core is a future milestone (post-M0)."
echo "In dev, run with: uv run --project core zana-core serve"
exit 1
