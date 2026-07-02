#!/usr/bin/env bash
# Package GocView Chatbot browser extensions (Chrome, Edge, Firefox, Safari source).
#
# Usage:
#   ./package-extension.sh              # all zip packages
#   ./package-extension.sh chrome
#   ./package-extension.sh edge
#   ./package-extension.sh firefox
#   ./package-extension.sh safari
#   ./package-extension.sh all 2.2.0
#
# Output (in dist/):
#   gocview-chatbot-chrome-<version>.zip
#   gocview-chatbot-edge-<version>.zip
#   gocview-chatbot-firefox-<version>.zip
#   gocview-chatbot-safari-<version>.zip   (source for Xcode; run build-safari-extension.sh on Mac)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="${ROOT}/dist"

TARGET="${1:-all}"
VERSION="${2:-}"

BROWSERS=(chrome edge firefox safari)

if [[ "$TARGET" =~ ^[0-9] ]]; then
  VERSION="$TARGET"
  TARGET="all"
fi

package_one() {
  local browser="$1"
  local ext_dir="${ROOT}/${browser}-extension"
  local manifest="${ext_dir}/manifest.json"

  if [[ ! -f "$manifest" ]]; then
    echo "Error: ${manifest} not found" >&2
    exit 1
  fi

  local ver="$VERSION"
  if [[ -z "$ver" ]]; then
    ver="$(python3 -c "import json; print(json.load(open('${manifest}'))['version'])")"
  fi

  local zip_name="gocview-chatbot-${browser}-${ver}.zip"
  mkdir -p "$OUT_DIR"
  rm -f "${OUT_DIR}/${zip_name}"
  (
    cd "$ext_dir"
    zip -r "${OUT_DIR}/${zip_name}" . \
      -x "*.DS_Store" -x "__MACOSX/*" -x "*.pem" -x "*.crx" -x "*.xpi"
  )
  echo "==> Created: ${OUT_DIR}/${zip_name}"
}

case "$TARGET" in
  chrome|edge|firefox|safari) package_one "$TARGET" ;;
  all|both)
    for b in "${BROWSERS[@]}"; do
      package_one "$b"
    done
    ;;
  *)
    echo "Usage: $0 [chrome|edge|firefox|safari|all] [version]" >&2
    exit 1
    ;;
esac

echo ""
echo "Chrome:  chrome://extensions → Developer mode → Load unpacked → chrome-extension/"
echo "Edge:    edge://extensions → Developer mode → Load unpacked → edge-extension/"
echo "Firefox: about:debugging → Load Temporary Add-on → firefox-extension/manifest.json"
echo "Safari:  ./build-safari-extension.sh (macOS + Xcode) → Run in Xcode → enable in Safari Settings"
echo ""
echo "Server default: https://gocview.arlocloud.com"
