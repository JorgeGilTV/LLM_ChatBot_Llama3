#!/usr/bin/env bash
# Arranca app.py con el Python del proyecto (.venv). Uso: ./run.sh   o   bash run.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
PY="${ROOT}/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "Creando .venv e instalando requirements.txt (solo la primera vez)..." >&2
  python3 -m venv "${ROOT}/.venv"
  "${ROOT}/.venv/bin/pip" install -q --upgrade pip
  "${ROOT}/.venv/bin/pip" install -q -r "${ROOT}/requirements.txt"
fi
exec "$PY" "${ROOT}/app.py" "$@"
