#!/usr/bin/env bash
# Package GocView Chatbot Chrome extension for internal distribution.
#
# Usage:
#   ./package-extension.sh
#   ./package-extension.sh 2.1.0
#
# Output: dist/gocview-chatbot-<version>.zip (unpacked load in Chrome)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
EXT_DIR="${ROOT}/chrome-extension"
OUT_DIR="${ROOT}/dist"

VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
  VERSION="$(python3 - <<'PY'
import json
from pathlib import Path
m = json.loads(Path("chrome-extension/manifest.json").read_text())
print(m.get("version", "0.0.0"))
PY
)"
fi

ZIP_NAME="gocview-chatbot-${VERSION}.zip"
mkdir -p "$OUT_DIR"

if [[ ! -f "${EXT_DIR}/manifest.json" ]]; then
  echo "Error: ${EXT_DIR}/manifest.json not found" >&2
  exit 1
fi

# Zip contents of chrome-extension/ (not the parent folder name) so unzip → load unpacked works.
rm -f "${OUT_DIR}/${ZIP_NAME}"
(
  cd "$EXT_DIR"
  zip -r "${OUT_DIR}/${ZIP_NAME}" . \
    -x "*.DS_Store" -x "__MACOSX/*" -x "*.pem" -x "*.crx"
)

echo "==> Created: ${OUT_DIR}/${ZIP_NAME}"
echo ""
echo "Share with colleagues:"
echo "  1. Unzip the file"
echo "  2. chrome://extensions → Developer mode ON"
echo "  3. Load unpacked → select the unzipped folder"
echo "  4. Server: https://gocview.arlocloud.com (default)"
