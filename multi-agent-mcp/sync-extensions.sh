#!/usr/bin/env bash
# Copy shared UI/JS from chrome-extension to edge, firefox, safari.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
FILES=(shared.js popup.js popup.html content.js styles.css content.css brand.css)
for browser in edge firefox safari; do
  for f in "${FILES[@]}"; do
    cp "${ROOT}/chrome-extension/${f}" "${ROOT}/${browser}-extension/${f}"
  done
  python3 -c "
import json
from pathlib import Path
p = Path('${ROOT}/${browser}-extension/manifest.json')
m = json.loads(p.read_text())
m['version'] = json.loads(Path('${ROOT}/chrome-extension/manifest.json').read_text())['version']
p.write_text(json.dumps(m, indent=2) + '\n')
"
  echo "==> synced ${browser}-extension"
done
