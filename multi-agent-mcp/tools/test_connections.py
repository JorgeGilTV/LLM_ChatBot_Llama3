"""
Structured connectivity checks for external integrations (Datadog, Splunk, etc.).
Used by scripts/verify_tool_connections.py and the /testconnections UI.
"""

from __future__ import annotations

import os
from typing import Any

import requests


def _row(
    *,
    id: str,
    name: str,
    key_ok: bool | None,
    user_ok: bool | None,
    connection_ok: bool | None,
    detail: str = "",
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "id": id,
        "name": name,
        "key_ok": key_ok,
        "user_ok": user_ok,
        "connection_ok": connection_ok,
        "detail": detail or "",
        "error": error,
    }


def run_connection_checks() -> dict[str, Any]:
    """
    Run all checks against current os.environ (caller should load_dotenv first).

    Returns:
      { "items": [ {...}, ... ], "all_ok": bool, "checked_at": iso8601-ish str }
    """
    from datetime import datetime

    items: list[dict[str, Any]] = []

    # --- Datadog ---
    dd_key = (os.getenv("DATADOG_API_KEY") or "").strip()
    dd_app = (os.getenv("DATADOG_APP_KEY") or "").strip()
    dd_site = (os.getenv("DATADOG_SITE") or "datadoghq.com").strip()
    k_ok = bool(dd_key and dd_app)
    if not k_ok:
        items.append(
            _row(
                id="datadog",
                name="Datadog",
                key_ok=False,
                user_ok=None,
                connection_ok=None,
                detail="Faltan DATADOG_API_KEY y/o DATADOG_APP_KEY",
                error=None,
            )
        )
    else:
        try:
            from tools.datadog_dashboards import datadog_rest_api_base

            base = datadog_rest_api_base(dd_site)
            url = f"{base}/api/v1/validate"
            r = requests.get(
                url,
                headers={"DD-API-KEY": dd_key, "DD-APPLICATION-KEY": dd_app},
                timeout=(10, 15),
            )
            ok = r.status_code == 200
            items.append(
                _row(
                    id="datadog",
                    name="Datadog",
                    key_ok=True,
                    user_ok=None,
                    connection_ok=ok,
                    detail=f"{base} validate → HTTP {r.status_code}",
                    error=None if ok else (r.text[:300] if r.text else "HTTP error"),
                )
            )
        except Exception as e:
            items.append(
                _row(
                    id="datadog",
                    name="Datadog",
                    key_ok=True,
                    user_ok=None,
                    connection_ok=False,
                    detail="",
                    error=f"{type(e).__name__}: {e}",
                )
            )

    # --- Splunk ---
    sp_host = (os.getenv("SPLUNK_HOST") or "").strip()
    sp_tok = (os.getenv("SPLUNK_TOKEN") or "").strip()
    k_ok = bool(sp_host and sp_tok)
    if not k_ok:
        items.append(
            _row(
                id="splunk",
                name="Splunk REST",
                key_ok=bool(sp_tok),
                user_ok=None,
                connection_ok=None,
                detail="Falta SPLUNK_HOST o SPLUNK_TOKEN",
                error=None,
            )
        )
    else:
        try:
            from tools.splunk_tool import splunk_ipv4_rest_scope, splunk_mgmt_port

            port = splunk_mgmt_port()
            url = f"https://{sp_host}:{port}/services/server/info"
            mode = (os.getenv("SPLUNK_AUTH_MODE") or "bearer").strip().lower()
            auth = (
                f"Splunk {sp_tok}"
                if mode in ("splunk", "session", "splunk-session")
                else f"Bearer {sp_tok}"
            )
            with splunk_ipv4_rest_scope():
                r = requests.get(
                    url,
                    headers={"Authorization": auth},
                    params={"output_mode": "json"},
                    timeout=(12, 25),
                )
            ok = r.status_code == 200
            items.append(
                _row(
                    id="splunk",
                    name="Splunk REST",
                    key_ok=True,
                    user_ok=None,
                    connection_ok=ok,
                    detail=f"{sp_host}:{port} server/info → HTTP {r.status_code}",
                    error=None if ok else (r.text[:300] if r.text else "HTTP error"),
                )
            )
        except Exception as e:
            items.append(
                _row(
                    id="splunk",
                    name="Splunk REST",
                    key_ok=True,
                    user_ok=None,
                    connection_ok=False,
                    detail="",
                    error=f"{type(e).__name__}: {e}",
                )
            )

    # --- PagerDuty ---
    pd = (os.getenv("PAGERDUTY_API_TOKEN") or "").strip()
    if not pd:
        items.append(
            _row(
                id="pagerduty",
                name="PagerDuty",
                key_ok=False,
                user_ok=None,
                connection_ok=None,
                detail="Falta PAGERDUTY_API_TOKEN",
                error=None,
            )
        )
    else:
        try:
            r = requests.get(
                "https://api.pagerduty.com/users",
                headers={
                    "Authorization": f"Token token={pd}",
                    "Accept": "application/vnd.pagerduty+json;version=2",
                },
                params={"limit": 1},
                timeout=(10, 15),
            )
            ok = r.status_code == 200
            items.append(
                _row(
                    id="pagerduty",
                    name="PagerDuty",
                    key_ok=True,
                    user_ok=None,
                    connection_ok=ok,
                    detail=f"users → HTTP {r.status_code}",
                    error=None if ok else (r.text[:200] if r.text else "HTTP error"),
                )
            )
        except Exception as e:
            items.append(
                _row(
                    id="pagerduty",
                    name="PagerDuty",
                    key_ok=True,
                    user_ok=None,
                    connection_ok=False,
                    detail="",
                    error=f"{type(e).__name__}: {e}",
                )
            )

    # --- Slack Bot ---
    slack_bot = (os.getenv("SLACK_BOT_TOKEN") or "").strip()
    if not slack_bot:
        items.append(
            _row(
                id="slack_bot",
                name="Slack Bot API",
                key_ok=False,
                user_ok=None,
                connection_ok=None,
                detail="Falta SLACK_BOT_TOKEN",
                error=None,
            )
        )
    else:
        try:
            r = requests.post(
                "https://slack.com/api/auth.test",
                headers={"Authorization": f"Bearer {slack_bot}"},
                timeout=(10, 15),
            )
            data = r.json() if r.text else {}
            ok = r.status_code == 200 and data.get("ok")
            err = None if ok else (data.get("error") or r.text[:120])
            items.append(
                _row(
                    id="slack_bot",
                    name="Slack Bot API",
                    key_ok=True,
                    user_ok=None,
                    connection_ok=bool(ok),
                    detail="auth.test",
                    error=str(err) if err else None,
                )
            )
        except Exception as e:
            items.append(
                _row(
                    id="slack_bot",
                    name="Slack Bot API",
                    key_ok=True,
                    user_ok=None,
                    connection_ok=False,
                    detail="",
                    error=f"{type(e).__name__}: {e}",
                )
            )

    # --- Slack Webhook (optional POST) ---
    hook = (os.getenv("SLACK_WEBHOOK_URL") or "").strip()
    if not hook:
        items.append(
            _row(
                id="slack_webhook",
                name="Slack Incoming Webhook",
                key_ok=False,
                user_ok=None,
                connection_ok=None,
                detail="Falta SLACK_WEBHOOK_URL",
                error=None,
            )
        )
    elif not hook.startswith("https://hooks.slack.com/"):
        items.append(
            _row(
                id="slack_webhook",
                name="Slack Incoming Webhook",
                key_ok=True,
                user_ok=None,
                connection_ok=None,
                detail="URL no es hooks.slack.com (no probada)",
                error=None,
            )
        )
    elif (os.getenv("VERIFY_SLACK_WEBHOOK_POST") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        try:
            r = requests.post(
                hook,
                json={"text": "[testconnections] ping"},
                timeout=(10, 20),
            )
            body = (r.text or "")[:200]
            ok = r.status_code == 200 and "ok" in body.lower()
            items.append(
                _row(
                    id="slack_webhook",
                    name="Slack Incoming Webhook",
                    key_ok=True,
                    user_ok=None,
                    connection_ok=ok,
                    detail=f"POST → HTTP {r.status_code}",
                    error=None if ok else body,
                )
            )
        except Exception as e:
            items.append(
                _row(
                    id="slack_webhook",
                    name="Slack Incoming Webhook",
                    key_ok=True,
                    user_ok=None,
                    connection_ok=False,
                    detail="",
                    error=f"{type(e).__name__}: {e}",
                )
            )
    else:
        items.append(
            _row(
                id="slack_webhook",
                name="Slack Incoming Webhook",
                key_ok=True,
                user_ok=None,
                connection_ok=None,
                detail="URL configurada (sin POST; VERIFY_SLACK_WEBHOOK_POST=1 para probar envío)",
                error=None,
            )
        )

    # --- Confluence ---
    email = (os.getenv("ATLASSIAN_EMAIL") or "").strip()
    ctoken = (os.getenv("CONFLUENCE_TOKEN") or "").strip()
    chost = (os.getenv("CONFLUENCE_ATLASSIAN_HOST") or "https://arlo.atlassian.net").strip().rstrip("/")
    u_ok = bool(email)
    k_ok = bool(ctoken)
    if not k_ok or not u_ok:
        items.append(
            _row(
                id="confluence",
                name="Confluence / Atlassian",
                key_ok=k_ok,
                user_ok=u_ok,
                connection_ok=None,
                detail="Falta ATLASSIAN_EMAIL y/o CONFLUENCE_TOKEN",
                error=None,
            )
        )
    else:
        try:
            url = f"{chost}/wiki/rest/api/search?cql=type=page&limit=1"
            r = requests.get(url, auth=(email, ctoken), timeout=(15, 25))
            ok = r.status_code == 200
            items.append(
                _row(
                    id="confluence",
                    name="Confluence / Atlassian",
                    key_ok=True,
                    user_ok=True,
                    connection_ok=ok,
                    detail=f"{chost} → HTTP {r.status_code}",
                    error=None if ok else (r.text[:200] if r.text else "HTTP error"),
                )
            )
        except Exception as e:
            items.append(
                _row(
                    id="confluence",
                    name="Confluence / Atlassian",
                    key_ok=True,
                    user_ok=True,
                    connection_ok=False,
                    detail="",
                    error=f"{type(e).__name__}: {e}",
                )
            )

    # --- Bedrock ---
    absk = (os.getenv("BEDROCK_API_KEY") or "").strip()
    region = (os.getenv("AWS_REGION") or "us-east-1").strip()
    if not absk:
        items.append(
            _row(
                id="bedrock",
                name="AWS Bedrock",
                key_ok=False,
                user_ok=None,
                connection_ok=None,
                detail="Falta BEDROCK_API_KEY",
                error=None,
            )
        )
    else:
        try:
            import boto3
            from botocore.config import Config

            os.environ["AWS_BEARER_TOKEN_BEDROCK"] = absk
            cfg = Config(connect_timeout=15, read_timeout=25)
            client = boto3.client("bedrock", region_name=region, config=cfg)
            client.list_foundation_models(byOutputModality="TEXT")
            items.append(
                _row(
                    id="bedrock",
                    name="AWS Bedrock",
                    key_ok=True,
                    user_ok=None,
                    connection_ok=True,
                    detail=f"list_foundation_models ({region})",
                    error=None,
                )
            )
        except Exception as e:
            items.append(
                _row(
                    id="bedrock",
                    name="AWS Bedrock",
                    key_ok=True,
                    user_ok=None,
                    connection_ok=False,
                    detail="",
                    error=f"{type(e).__name__}: {e}",
                )
            )

    # --- Grafana ---
    gurl = (os.getenv("GRAFANA_URL") or "").strip().rstrip("/")
    gkey = (os.getenv("GRAFANA_API_KEY") or "").strip()
    if not gurl:
        items.append(
            _row(
                id="grafana",
                name="Grafana",
                key_ok=False,
                user_ok=None,
                connection_ok=None,
                detail="GRAFANA_URL no definida",
                error=None,
            )
        )
    elif not gkey:
        items.append(
            _row(
                id="grafana",
                name="Grafana",
                key_ok=False,
                user_ok=None,
                connection_ok=None,
                detail="GRAFANA_API_KEY vacía",
                error=None,
            )
        )
    else:
        try:
            r = requests.get(
                f"{gurl}/api/health",
                headers={"Authorization": f"Bearer {gkey}"},
                timeout=(10, 15),
            )
            ok = r.status_code == 200
            items.append(
                _row(
                    id="grafana",
                    name="Grafana",
                    key_ok=True,
                    user_ok=None,
                    connection_ok=ok,
                    detail=f"/api/health → HTTP {r.status_code}",
                    error=None if ok else r.text[:120],
                )
            )
        except Exception as e:
            items.append(
                _row(
                    id="grafana",
                    name="Grafana",
                    key_ok=True,
                    user_ok=None,
                    connection_ok=False,
                    detail="",
                    error=f"{type(e).__name__}: {e}",
                )
            )

    # --- AWS STS ---
    ak = (os.getenv("AWS_ACCESS_KEY_ID") or "").strip()
    sk = (os.getenv("AWS_SECRET_ACCESS_KEY") or "").strip()
    if not ak or not sk:
        items.append(
            _row(
                id="aws_sts",
                name="AWS IAM (STS)",
                key_ok=False,
                user_ok=None,
                connection_ok=None,
                detail="Sin AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (opcional)",
                error=None,
            )
        )
    else:
        try:
            import boto3

            sts = boto3.client("sts", region_name=region)
            ident = sts.get_caller_identity()
            aid = ident.get("Account", "?")
            items.append(
                _row(
                    id="aws_sts",
                    name="AWS IAM (STS)",
                    key_ok=True,
                    user_ok=None,
                    connection_ok=True,
                    detail=f"cuenta {aid}",
                    error=None,
                )
            )
        except Exception as e:
            items.append(
                _row(
                    id="aws_sts",
                    name="AWS IAM (STS)",
                    key_ok=True,
                    user_ok=None,
                    connection_ok=False,
                    detail="",
                    error=f"{type(e).__name__}: {e}",
                )
            )

    # --- Amplitude (SHM chart CSV) ---
    amp_key = (os.getenv("AMPLITUDE_API_KEY") or "").strip()
    amp_secret = (os.getenv("AMPLITUDE_SECRET_KEY") or "").strip()
    amp_chart_url = (
        (os.getenv("AMPLITUDE_CHART_URL") or "").strip()
        or "https://app.amplitude.com/analytics/arlo/chart/thy5dan3/edit/dxa32pza?sharingId=0GNQ827B"
    )
    amp_base = (os.getenv("AMPLITUDE_API_BASE") or "https://amplitude.com").strip().rstrip("/")
    amp_k_ok = bool(amp_key and amp_secret)
    if not amp_k_ok:
        items.append(
            _row(
                id="amplitude",
                name="Amplitude",
                key_ok=False,
                user_ok=None,
                connection_ok=None,
                detail="Faltan AMPLITUDE_API_KEY y/o AMPLITUDE_SECRET_KEY",
                error=None,
            )
        )
    else:
        import re

        cid = None
        m = re.search(r"/chart/([^/?#]+)", amp_chart_url)
        if m:
            cid = m.group(1)
        if not cid:
            items.append(
                _row(
                    id="amplitude",
                    name="Amplitude",
                    key_ok=True,
                    user_ok=None,
                    connection_ok=False,
                    detail="AMPLITUDE_CHART_URL sin chart id reconocible",
                    error=None,
                )
            )
        else:
            try:
                url = f"{amp_base}/api/3/chart/{cid}/csv"
                r = requests.get(url, auth=(amp_key, amp_secret), timeout=(10, 45))
                ok = r.status_code == 200 and bool((r.text or "").strip())
                items.append(
                    _row(
                        id="amplitude",
                        name="Amplitude",
                        key_ok=True,
                        user_ok=None,
                        connection_ok=ok,
                        detail=f"Chart CSV → HTTP {r.status_code}",
                        error=None if ok else (r.text[:200] if r.text else "HTTP error"),
                    )
                )
            except Exception as e:
                items.append(
                    _row(
                        id="amplitude",
                        name="Amplitude",
                        key_ok=True,
                        user_ok=None,
                        connection_ok=False,
                        detail="",
                        error=f"{type(e).__name__}: {e}",
                    )
                )

    # --- Tableau / Firebase (HTTPS probe) ---
    for probe_id, probe_name, env_key, default_url in (
        ("tableau", "Tableau", "TABLEAU_PROBE_URL", "https://www.tableau.com"),
        ("firebase", "Firebase", "FIREBASE_PROBE_URL", "https://console.firebase.google.com"),
    ):
        probe_url = (os.getenv(env_key) or "").strip() or default_url
        try:
            r = requests.get(probe_url, timeout=(10, 20), allow_redirects=True)
            ok = r.status_code < 500
            items.append(
                _row(
                    id=probe_id,
                    name=probe_name,
                    key_ok=True,
                    user_ok=None,
                    connection_ok=ok,
                    detail=f"{probe_url} → HTTP {r.status_code}",
                    error=None if ok else f"HTTP {r.status_code}",
                )
            )
        except Exception as e:
            items.append(
                _row(
                    id=probe_id,
                    name=probe_name,
                    key_ok=True,
                    user_ok=None,
                    connection_ok=False,
                    detail=probe_url,
                    error=f"{type(e).__name__}: {e}",
                )
            )

    # --- Databricks (workspace REST) ---
    db_host = (os.getenv("DATABRICKS_HOST") or "").strip().rstrip("/")
    db_token = (os.getenv("DATABRICKS_TOKEN") or "").strip()
    db_k_ok = bool(db_host and db_token)
    if not db_k_ok:
        items.append(
            _row(
                id="databricks",
                name="Databricks",
                key_ok=False,
                user_ok=None,
                connection_ok=None,
                detail="Faltan DATABRICKS_HOST y/o DATABRICKS_TOKEN",
                error=None,
            )
        )
    else:
        try:
            url = f"{db_host}/api/2.0/clusters/list"
            r = requests.get(
                url,
                headers={"Authorization": f"Bearer {db_token}"},
                timeout=(10, 25),
            )
            ok = r.status_code == 200
            items.append(
                _row(
                    id="databricks",
                    name="Databricks",
                    key_ok=True,
                    user_ok=None,
                    connection_ok=ok,
                    detail=f"{db_host} → HTTP {r.status_code}",
                    error=None if ok else (r.text[:200] if r.text else "HTTP error"),
                )
            )
        except Exception as e:
            items.append(
                _row(
                    id="databricks",
                    name="Databricks",
                    key_ok=True,
                    user_ok=None,
                    connection_ok=False,
                    detail=db_host,
                    error=f"{type(e).__name__}: {e}",
                )
            )

    # --- Gemini / Anthropic (solo presencia) ---
    gem = bool((os.getenv("GEMINI_API_KEY") or "").strip())
    ant = bool((os.getenv("ANTHROPIC_API_KEY") or "").strip())
    items.append(
        _row(
            id="gemini",
            name="Gemini (clave)",
            key_ok=gem,
            user_ok=None,
            connection_ok=None if not gem else None,
            detail="No se invoca la API (evita coste)",
            error=None,
        )
    )
    items.append(
        _row(
            id="anthropic",
            name="Anthropic (clave)",
            key_ok=ant,
            user_ok=None,
            connection_ok=None if not ant else None,
            detail="No se invoca la API (evita coste)",
            error=None,
        )
    )

    OPTIONAL_INCOMPLETE = frozenset({"grafana", "aws_sts", "amplitude", "databricks", "tableau", "firebase"})

    def _item_all_ok(it: dict[str, Any]) -> bool:
        oid = it.get("id")
        if it.get("error"):
            return False
        conn = it.get("connection_ok")
        if conn is False:
            return False
        if oid in OPTIONAL_INCOMPLETE and it.get("key_ok") is False:
            return True
        if it.get("user_ok") is False:
            return False
        if it.get("key_ok") is False:
            return False
        if oid in ("gemini", "anthropic"):
            return bool(it.get("key_ok"))
        if (
            oid == "slack_webhook"
            and conn is None
            and "URL configurada" in (it.get("detail") or "")
        ):
            return True
        if conn is True:
            return True
        return False

    for it in items:
        it["row_ok"] = _item_all_ok(it)

    all_ok = all(it.get("row_ok") for it in items)
    return {
        "items": items,
        "all_ok": all_ok,
        "checked_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
