#!/bin/sh
set -e

# 1) Compose/K8s: full .env-style file mounted as a secret
if [ -f /run/secrets/app_env ]; then
    set -a
    # shellcheck disable=SC1091
    . /run/secrets/app_env
    set +a
fi

# 2) Individual secrets (one line per file). Filename = known key below.
#    Useful when there is no .env on disk but secrets exist in Docker/Kubernetes/Portainer.
#    Does not overwrite variables already set (e.g. -e or orchestrator UI).
_read_secret() {
    _ev="$1"
    _fn="$2"
    if [ ! -f "/run/secrets/$_fn" ]; then
        return 0
    fi
    eval "_cur=\${$_ev:-}"
    if [ -n "$_cur" ]; then
        return 0
    fi
    _val=$(tr -d '\r\n' < "/run/secrets/$_fn")
    export "$_ev=$_val"
}

_read_secret SLACK_WEBHOOK_URL slack_webhook
_read_secret SLACK_WEBHOOK_URL slack_webhook_url
_read_secret BEDROCK_API_KEY bedrock_api_key
_read_secret DATADOG_API_KEY datadog_api_key
_read_secret DATADOG_APP_KEY datadog_app_key
_read_secret SPLUNK_TOKEN splunk_token
_read_secret PAGERDUTY_API_TOKEN pagerduty_api_token
_read_secret ATLASSIAN_EMAIL atlassian_email
_read_secret CONFLUENCE_TOKEN confluence_token

# /app/.env mounted as a volume is read by app.py (load_dotenv); do not source here to avoid
# overwriting injected secrets or breaking values with special characters.

# Helpful on EC2/docker run: confirm how config reached the process (never print secret values).
if [ -f /app/.env ]; then
    echo "[docker-entrypoint] /app/.env exists — app.py will load_dotenv() it." >&2
else
    echo "[docker-entrypoint] No /app/.env in image (expected). Pass vars via: docker compose env_file, docker run --env-file, -e, /run/secrets/app_env, or AWS_SECRETS_MANAGER_SECRET_ID + IAM." >&2
fi
_have=0
for _k in BEDROCK_API_KEY DATADOG_API_KEY SPLUNK_TOKEN PAGERDUTY_API_TOKEN SLACK_WEBHOOK_URL; do
    eval "_v=\${$_k:-}"
    if [ -n "$_v" ]; then _have=1; break; fi
done
if [ "$_have" -eq 1 ]; then
    echo "[docker-entrypoint] At least one API token env var is set (BEDROCK/DATADOG/SPLUNK/PD/Slack)." >&2
else
    echo "[docker-entrypoint] WARNING: No common API tokens in environment yet — chat/tools may be empty until you inject credentials." >&2
fi

exec "$@"
