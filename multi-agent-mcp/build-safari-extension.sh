#!/usr/bin/env bash
# Build Safari Xcode wrapper for GocView Chatbot (macOS + Xcode required).
#
# Usage:
#   ./build-safari-extension.sh
#
# Output:
#   dist/safari-xcode/GocView Chatbot/  (open .xcodeproj, Run once, enable in Safari)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SRC="${ROOT}/safari-extension"
OUT="${ROOT}/dist/safari-xcode"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Error: Safari extension build requires macOS." >&2
  exit 1
fi

if ! xcrun --find safari-web-extension-converter >/dev/null 2>&1; then
  echo "Error: safari-web-extension-converter not found." >&2
  echo "Install Xcode from the App Store, then run:" >&2
  echo "  xcode-select --install" >&2
  exit 1
fi

mkdir -p "$OUT"
rm -rf "${OUT}/GocView Chatbot" 2>/dev/null || true

xcrun safari-web-extension-converter "$SRC" \
  --app-name "GocView Chatbot" \
  --bundle-identifier "com.arlo.gocview.chatbot" \
  --swift \
  --force \
  --copy-resources \
  --project-location "$OUT"

echo ""
echo "==> Xcode project: ${OUT}/GocView Chatbot/GocView Chatbot.xcodeproj"
echo ""
echo "Next steps (each Mac user):"
echo "  1. Open the .xcodeproj in Xcode"
echo "  2. Select target 'GocView Chatbot (macOS)' → Run (⌘R)"
echo "  3. Safari → Settings → Extensions → enable GocView Chatbot"
echo "  4. Allow on websites when prompted"
