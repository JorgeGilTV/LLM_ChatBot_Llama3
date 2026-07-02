#!/usr/bin/env bash
# Commit con ticket Jira en el mensaje (lo lee deploy-ecs-gocview.sh).
#
# Usage:
#   ./scripts/commit-with-ticket.sh SRE-1234 "deploy gocview: secrets UI"
#   ./scripts/commit-with-ticket.sh GOC-99 "fix MCP URL"   # añade solo al stage si ya hay cambios
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 TICKET \"commit message\"" >&2
  echo "Example: $0 SRE-1234 \"deploy gocview: update ECS tags\"" >&2
  exit 1
fi

TICKET="$(echo "$1" | tr '[:lower:]' '[:upper:]')"
shift
MSG="$*"

if [[ ! "$TICKET" =~ ^[A-Z][A-Z0-9]+-[0-9]+$ ]]; then
  echo "Error: ticket debe ser como SRE-1234, got: $TICKET" >&2
  exit 1
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Error: no hay repo git en $ROOT" >&2
  exit 1
fi

if git diff --cached --quiet; then
  echo "Nothing staged. Run: git add ..." >&2
  exit 1
fi

git commit -m "[${TICKET}] ${MSG}"
echo "Committed with ticket ${TICKET}. Deploy with:"
echo "  CHANGE_TICKET=${TICKET} ./deploy-ecs-gocview.sh"
echo "  # o solo ./deploy-ecs-gocview.sh (lee el ticket del commit)"
