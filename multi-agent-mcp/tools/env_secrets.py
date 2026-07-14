"""
Read and update individual keys in the project ``.env`` (masked in API responses).
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.dev_admin import DEFAULT_SECRETS_PIN, app_root

_ENV_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=")

SECRET_FIELD_GROUPS: list[dict[str, Any]] = [
    {
        "id": "bedrock",
        "title": "AWS Bedrock",
        "fields": [
            {"key": "BEDROCK_API_KEY", "label": "Bedrock API Key", "type": "password", "hint": "ABSK… (consola Bedrock)"},
            {"key": "AWS_REGION", "label": "AWS Region", "type": "text", "hint": "ej. us-east-1"},
        ],
    },
    {
        "id": "aws_iam",
        "title": "AWS IAM (opcional)",
        "fields": [
            {"key": "AWS_ACCESS_KEY_ID", "label": "Access Key ID", "type": "password"},
            {"key": "AWS_SECRET_ACCESS_KEY", "label": "Secret Access Key", "type": "password"},
            {"key": "AWS_SESSION_TOKEN", "label": "Session Token", "type": "password"},
            {"key": "AWS_SECRETS_MANAGER_SECRET_ID", "label": "Secrets Manager ID", "type": "text"},
        ],
    },
    {
        "id": "datadog",
        "title": "Datadog",
        "fields": [
            {"key": "DATADOG_API_KEY", "label": "API Key", "type": "password"},
            {"key": "DATADOG_APP_KEY", "label": "Application Key", "type": "password"},
            {"key": "DATADOG_SITE", "label": "Site", "type": "text", "hint": "datadoghq.com"},
        ],
    },
    {
        "id": "splunk",
        "title": "Splunk",
        "fields": [
            {"key": "SPLUNK_HOST", "label": "Host", "type": "text"},
            {"key": "SPLUNK_TOKEN", "label": "Token", "type": "password"},
            {"key": "SPLUNK_AUTH_MODE", "label": "Auth mode", "type": "text", "hint": "bearer o splunk"},
        ],
    },
    {
        "id": "pagerduty",
        "title": "PagerDuty",
        "fields": [{"key": "PAGERDUTY_API_TOKEN", "label": "API Token", "type": "password"}],
    },
    {
        "id": "slack",
        "title": "Slack",
        "fields": [
            {"key": "SLACK_BOT_TOKEN", "label": "Bot Token", "type": "password"},
            {"key": "SLACK_WEBHOOK_URL", "label": "Incoming Webhook URL", "type": "password"},
        ],
    },
    {
        "id": "confluence",
        "title": "Confluence / Atlassian",
        "fields": [
            {"key": "ATLASSIAN_EMAIL", "label": "Email", "type": "email"},
            {"key": "CONFLUENCE_TOKEN", "label": "API Token", "type": "password"},
            {"key": "CONFLUENCE_ATLASSIAN_HOST", "label": "Host URL", "type": "text"},
        ],
    },
    {
        "id": "grafana",
        "title": "Grafana",
        "fields": [
            {"key": "GRAFANA_URL", "label": "URL", "type": "text"},
            {"key": "GRAFANA_API_KEY", "label": "API Key", "type": "password"},
        ],
    },
    {
        "id": "mintmcp",
        "title": "MintMCP (Arlo Engineering)",
        "fields": [
            {"key": "MINTMCP_URL", "label": "MintMCP URL", "type": "text", "hint": "https://app.mintmcp.com/o/arlo/s/arlo/mcp"},
            {"key": "MINTMCP_API_KEY", "label": "MintMCP API Key", "type": "password", "hint": "gkey_… (Bearer)"},
        ],
    },
    {
        "id": "amplitude",
        "title": "Amplitude (SHM)",
        "fields": [
            {"key": "AMPLITUDE_API_KEY", "label": "API Key", "type": "password"},
            {"key": "AMPLITUDE_SECRET_KEY", "label": "Secret Key", "type": "password"},
            {"key": "AMPLITUDE_API_BASE_URL", "label": "API Base URL", "type": "text", "hint": "https://amplitude.com"},
            {"key": "AMPLITUDE_DASHBOARD_URL", "label": "Dashboard chart URL", "type": "text"},
            {"key": "AMPLITUDE_HOME_URL", "label": "Amplitude home URL", "type": "text"},
            {"key": "AMPLITUDE_UI_READY_CHART_URL_IOS", "label": "App Launch chart (iOS)", "type": "text"},
            {"key": "AMPLITUDE_UI_READY_CHART_URL_ANDROID", "label": "App Launch chart (Android)", "type": "text"},
            {"key": "AMPLITUDE_UI_READY_CHART_ID_ANDROID", "label": "App Launch chart id (Android)", "type": "text"},
            {"key": "AMPLITUDE_HTTP_TIMEOUT", "label": "HTTP timeout (sec)", "type": "text"},
        ],
    },
    {
        "id": "tableau",
        "title": "Tableau Cloud (SHM)",
        "fields": [
            {"key": "TABLEAU_SERVER_URL", "label": "Server URL", "type": "text", "hint": "https://10ay.online.tableau.com"},
            {"key": "TABLEAU_SITE_CONTENT_URL", "label": "Site content URL", "type": "text"},
            {"key": "TABLEAU_PAT_NAME", "label": "PAT name", "type": "text"},
            {"key": "TABLEAU_PAT_SECRET", "label": "PAT secret", "type": "password"},
            {"key": "TABLEAU_PROBE_URL", "label": "HTTPS probe URL", "type": "text", "hint": "https://tableau.arlo.com"},
        ],
    },
    {
        "id": "firebase",
        "title": "Firebase / GA4 (SHM)",
        "fields": [
            {"key": "FIREBASE_PROJECT_ID", "label": "Project ID", "type": "text"},
            {"key": "FIREBASE_IOS_APP_ID", "label": "iOS App ID", "type": "text"},
            {"key": "FIREBASE_IOS_BUNDLE_ID", "label": "iOS Bundle ID", "type": "text"},
            {"key": "FIREBASE_GCM_SENDER_ID", "label": "GCM Sender ID", "type": "text"},
            {"key": "FIREBASE_GA4_PROPERTY_ID_IOS", "label": "GA4 Property ID (iOS)", "type": "text"},
            {"key": "FIREBASE_GA4_PROPERTY_ID_ANDROID", "label": "GA4 Property ID (Android)", "type": "text"},
            {"key": "FIREBASE_GA4_SERVICE_ACCOUNT_PATH_IOS", "label": "Service account JSON (iOS)", "type": "text", "hint": "/app/config/shm/…"},
            {"key": "FIREBASE_GA4_SERVICE_ACCOUNT_PATH_ANDROID", "label": "Service account JSON (Android)", "type": "text"},
            {"key": "FIREBASE_GOOGLE_SERVICES_JSON", "label": "google-services.json path", "type": "text"},
            {"key": "FIREBASE_GOOGLE_SERVICE_INFO_PLIST", "label": "GoogleService-Info.plist path", "type": "text"},
            {"key": "FIREBASE_PROBE_URL", "label": "HTTPS probe URL", "type": "text"},
        ],
    },
    {
        "id": "shm",
        "title": "SHM Dashboards",
        "fields": [
            {"key": "SHM_VIEW_API_BASE", "label": "SHM View API", "type": "text", "hint": "https://shmview.arlocloud.com"},
            {"key": "SHM_DAILY_API_BASE", "label": "SHM Daily API", "type": "text", "hint": "https://shmdaily.arlocloud.com"},
        ],
    },
    {
        "id": "other",
        "title": "Otros",
        "fields": [
            {"key": "GEMINI_API_KEY", "label": "Gemini API Key", "type": "password"},
            {"key": "ANTHROPIC_API_KEY", "label": "Anthropic API Key", "type": "password"},
            {"key": "SNOW_USER", "label": "ServiceNow User", "type": "text"},
            {"key": "SNOW_PASSWORD", "label": "ServiceNow Password", "type": "password"},
        ],
    },
]

ALL_SECRET_KEYS: frozenset[str] = frozenset(
    f["key"] for g in SECRET_FIELD_GROUPS for f in g["fields"]
) | frozenset({"ADMIN_TOKEN"})


def mask_secret(value: str | None) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if len(s) <= 10:
        return "••••••••"
    return f"{s[:4]}…{s[-4:]}"


def _dotenv_path(root: Path | None = None) -> Path:
    return (root or app_root()) / ".env"


def _read_env_values(root: Path | None = None) -> dict[str, str]:
    """Merge ``.env`` with process env (ECS task definition injects secrets as env vars)."""
    out: dict[str, str] = {}
    for key in ALL_SECRET_KEYS:
        val = (os.getenv(key) or "").strip()
        if val:
            out[key] = val

    path = _dotenv_path(root)
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        k = k.strip()
        if k not in ALL_SECRET_KEYS:
            continue
        v = v.strip().strip('"').strip("'")
        if v:
            out[k] = v
    return out


def _format_env_line(key: str, value: str) -> str:
    if re.search(r'[\s#"\\]', value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'{key}="{escaped}"'
    return f"{key}={value}"


def list_secrets_state(root: Path | None = None) -> dict[str, Any]:
    values = _read_env_values(root)
    groups: list[dict[str, Any]] = []
    configured = 0
    for g in SECRET_FIELD_GROUPS:
        fields_out = []
        for f in g["fields"]:
            key = f["key"]
            raw = values.get(key, "")
            is_set = bool(raw)
            if is_set:
                configured += 1
            fields_out.append(
                {
                    "key": key,
                    "label": f["label"],
                    "type": f.get("type", "password"),
                    "hint": f.get("hint"),
                    "is_set": is_set,
                    "masked": mask_secret(raw) if is_set else None,
                }
            )
        groups.append({"id": g["id"], "title": g["title"], "fields": fields_out})
    path = _dotenv_path(root)
    return {
        "groups": groups,
        "env_path": str(path),
        "env_exists": path.is_file(),
        "configured_count": configured,
        "total_fields": len(ALL_SECRET_KEYS),
    }


def update_secrets_in_dotenv(updates: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    root = root or app_root()
    path = _dotenv_path(root)

    cleaned: dict[str, str] = {}
    for key, val in (updates or {}).items():
        if key not in ALL_SECRET_KEYS:
            continue
        if val is None:
            continue
        s = str(val).strip()
        if s:
            cleaned[key] = s

    if not cleaned:
        return {"success": False, "error": "No hay valores nuevos para guardar (campos vacíos se ignoran)."}

    cleaned["ADMIN_TOKEN"] = DEFAULT_SECRETS_PIN

    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()

    touched: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        m = _ENV_KEY_RE.match(line.strip())
        if m and m.group(1) in cleaned:
            key = m.group(1)
            new_lines.append(_format_env_line(key, cleaned[key]))
            touched.add(key)
        else:
            new_lines.append(line)

    if not path.is_file() and not new_lines:
        new_lines.append("# OneView GOC — secrets (generado desde /secrets)")

    for key, val in cleaned.items():
        if key not in touched:
            new_lines.append(_format_env_line(key, val))
            touched.add(key)

    text = "\n".join(new_lines)
    if text and not text.endswith("\n"):
        text += "\n"

    backup = None
    if path.is_file():
        backup = path.parent / f".env.bak.{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        try:
            shutil.copy2(path, backup)
        except OSError:
            backup = None

    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass

    os.environ.setdefault("ADMIN_TOKEN", "ONEVIEW")
    for key, val in cleaned.items():
        os.environ[key] = val

    try:
        from dotenv import load_dotenv

        load_dotenv(path, override=True)
    except ImportError:
        pass

    ecs_result: dict[str, Any] = {}
    try:
        from tools.ecs_secrets_sync import sync_task_env_and_redeploy

        ecs_result = sync_task_env_and_redeploy()
    except Exception as e:
        ecs_result = {"ecs_sync": "error", "error": str(e)}

    hint = "Variables recargadas en este proceso."
    if ecs_result.get("ecs_sync") == "ok":
        hint = f"ECS redeploy iniciado ({ecs_result.get('task_definition')}). Espera ~2 min."
    elif ecs_result.get("ecs_sync") == "skipped":
        hint += " ECS sync omitido (solo local o sin credenciales ECS)."

    return {
        "success": True,
        "path": str(path),
        "updated_keys": sorted(touched),
        "backup": str(backup) if backup else None,
        "restart_hint": hint,
        "ecs": ecs_result,
    }
