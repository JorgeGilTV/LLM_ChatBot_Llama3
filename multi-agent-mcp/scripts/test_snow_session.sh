#!/usr/bin/env bash
# Test ServiceNow REST with session cookie from .env (Okta — basic auth usually fails).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

load_env() {
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "${line//[[:space:]]/}" || "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" != *"="* ]] && continue
    k="${line%%=*}"
    k="$(echo "$k" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    v="${line#*=}"
    v="$(echo "$v" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
    case "$k" in
      SNOW_*) export "$k=$v" ;;
    esac
  done < .env
}

load_env
INST="${SNOW_INSTANCE:-https://arlo.service-now.com}"
INST="${INST%/}"

echo "Instance: $INST"
echo "SNOW_SESSION_COOKIE set: $([[ -n "${SNOW_SESSION_COOKIE:-}" ]] && echo yes || echo no)"
echo "SNOW_USER_TOKEN set: $([[ -n "${SNOW_USER_TOKEN:-${SNOW_G_CK:-}}" ]] && echo yes || echo no)"

if [[ -n "${SNOW_SESSION_COOKIE:-}" ]]; then
  HDR=(-H "Cookie: ${SNOW_SESSION_COOKIE}")
  TOKEN="${SNOW_USER_TOKEN:-${SNOW_G_CK:-}}"
  if [[ -n "$TOKEN" ]]; then
    HDR+=(-H "X-UserToken: ${TOKEN}")
  fi
  echo "==> curl Table API (session cookie)"
  curl -sS -m 30 "${HDR[@]}" \
    "${INST}/api/now/table/incident?sysparm_limit=1&sysparm_fields=number" \
    -w "\nHTTP:%{http_code}\n"
elif [[ -n "${SNOW_USER:-}" && -n "${SNOW_PASSWORD:-}" ]]; then
  echo "==> curl Table API (basic auth — often 401 with Okta SSO)"
  curl -sS -m 30 -u "${SNOW_USER}:${SNOW_PASSWORD}" \
    "${INST}/api/now/table/incident?sysparm_limit=1&sysparm_fields=number" \
    -w "\nHTTP:%{http_code}\n"
else
  echo "Set SNOW_SESSION_COOKIE (+ SNOW_USER_TOKEN) in .env, then redeploy."
  exit 1
fi

echo "==> GocView probe (if running locally): curl -s http://127.0.0.1:8080/api/servicenow/probe | python3 -m json.tool"
