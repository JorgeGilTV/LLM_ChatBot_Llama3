#!/usr/bin/env bash
# Package GocView Chatbot browser extensions (Chrome + Firefox).
#
# Usage:
#   ./package-extension.sh           # both browsers
#   ./package-extension.sh chrome    # Chrome only
#   ./package-extension.sh firefox   # Firefox only
#   ./package-extension.sh both 2.1.0
#
# Output:
#   dist/gocview-chatbot-chrome-<version>.zip
#   dist/gocview-chatbot-firefox-<version>.zip
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="${ROOT}/dist"

TARGET="${1:-both}"
VERSION="${2:-}"

if [[ "$TARGET" == "both" || "$TARGET" == "chrome" || "$TARGET" == "firefox" ]] && [[ "$TARGET" =~ ^[0-9] ]]; then
  VERSION="$TARGET"
  TARGET="both"
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
  chrome)  package_one chrome ;;
  firefox) package_one firefox ;;
  both)
    package_one chrome
    package_one firefox
    ;;
  *)
    echo "Usage: $0 [chrome|firefox|both] [version]" >&2
    exit 1
    ;;
esac

echo ""
echo "Chrome install:"
echo "  chrome://extensions → Developer mode → Load unpacked → chrome-extension/"
echo ""
echo "Firefox install:"
echo "  about:debugging#/runtime/this-firefox → Load Temporary Add-on → firefox-extension/manifest.json"
echo "  (or unzip gocview-chatbot-firefox-*.zip and load manifest.json)"
echo ""
echo "Server default: https://gocview.arlocloud.com"
