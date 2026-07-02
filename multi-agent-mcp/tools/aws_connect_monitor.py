"""
Amazon Connect operational monitoring for the AWS Change Tracker UI.

Covers:
- Critical: agent count drop, login-failure spike (CloudTrail), user delete/modify events
- Proactive: roster reconciliation, instance health probe, queue staffing anomalies
- Dashboard: live agent/queue metrics + admin audit trail
- Genesis (Care platform): AzureADSync Lambda, Azure AD SSO, sync/deletion correlation
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import boto3
from botocore.exceptions import ClientError

_CONNECT_ADMIN_EVENTS = frozenset(
    {
        "CreateUser",
        "DeleteUser",
        "UpdateUser",
        "UpdateUserIdentityInfo",
        "UpdateUserPhoneConfig",
        "UpdateUserRoutingProfile",
        "UpdateUserSecurityProfiles",
        "UpdateUserHierarchy",
        "DeleteUserHierarchyGroup",
        "CreateUserHierarchyGroup",
        "UpdateUserHierarchyGroup",
        "AssociateUserProficiencies",
        "DisassociateUserProficiencies",
    }
)

_LOGIN_FAILURE_EVENT_HINTS = frozenset(
    {
        "AuthenticateUser",
        "InitiateLogin",
        "Login",
        "FederateUser",
    }
)

_LAMBDA_CONFIG_EVENTS = frozenset(
    {
        "UpdateFunctionCode",
        "UpdateFunctionConfiguration",
        "PublishVersion",
        "CreateFunction",
        "DeleteFunction",
    }
)

_IAM_SSO_EVENTS = frozenset(
    {
        "AssumeRoleWithSAML",
        "AssumeRole",
        "GetSAMLProvider",
        "CreateSAMLProvider",
        "UpdateSAMLProvider",
        "DeleteSAMLProvider",
    }
)

_agent_count_history: list[tuple[float, int]] = []
_history_lock = threading.Lock()
_snapshot_cache: dict[str, Any] = {"ts": 0.0, "payload": None}
_CACHE_TTL_SEC = 45


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int((os.getenv(name) or str(default)).strip())))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float((os.getenv(name) or str(default)).strip())))
    except (TypeError, ValueError):
        return default


def connect_monitor_config() -> dict[str, Any]:
    """Non-secret defaults for the UI (from environment)."""
    region = (os.getenv("AWS_CONNECT_REGION") or os.getenv("AWS_REGION") or "us-east-1").strip()
    instance_id = (os.getenv("AWS_CONNECT_INSTANCE_ID") or "").strip()
    queue_ids = [
        q.strip()
        for q in (os.getenv("AWS_CONNECT_QUEUE_IDS") or "").split(",")
        if q.strip()
    ]
    expected_raw = (os.getenv("AWS_CONNECT_EXPECTED_USERS") or "").strip()
    expected_users = [u.strip() for u in expected_raw.split(",") if u.strip()]
    genesis_enabled = (os.getenv("GENESIS_MONITOR") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    return {
        "region": region,
        "instance_id": instance_id,
        "instance_configured": bool(instance_id),
        "queue_ids": queue_ids,
        "agent_baseline": _env_int("AWS_CONNECT_AGENT_BASELINE_COUNT", 0, 0, 50000),
        "agent_drop_pct": _env_float("AWS_CONNECT_AGENT_DROP_PCT", 7.5, 1.0, 50.0),
        "login_fail_threshold": _env_int("AWS_CONNECT_LOGIN_FAIL_THRESHOLD", 10, 1, 500),
        "login_fail_window_min": _env_int("AWS_CONNECT_LOGIN_FAIL_WINDOW_MIN", 5, 1, 60),
        "expected_users_count": len(expected_users),
        "business_hours_start": _env_int("AWS_CONNECT_BUSINESS_HOURS_START", 8, 0, 23),
        "business_hours_end": _env_int("AWS_CONNECT_BUSINESS_HOURS_END", 18, 1, 24),
        "business_tz": (os.getenv("AWS_CONNECT_BUSINESS_TZ") or "America/New_York").strip(),
        "staffing_min_available": _env_int("AWS_CONNECT_STAFFING_MIN_AVAILABLE", 5, 0, 5000),
        "genesis": {
            "enabled": genesis_enabled,
            "instance_alias": (os.getenv("GENESIS_CONNECT_INSTANCE_ALIAS") or "genesis-connect").strip(),
            "azuread_sync_lambda": (os.getenv("GENESIS_AZUREAD_SYNC_LAMBDA") or "AzureADSync").strip(),
            "sso_idp": (os.getenv("GENESIS_SSO_IDP_NAME") or "Azure_Genesis_SSO").strip(),
            "sso_role": (os.getenv("GENESIS_SSO_ROLE_NAME") or "Azure_Genesis_SSO_Role").strip(),
            "lambda_error_threshold": _env_int("GENESIS_LAMBDA_ERROR_THRESHOLD", 1, 0, 500),
            "sync_delete_correlation_min": _env_int("GENESIS_SYNC_DELETE_CORRELATION_MIN", 30, 5, 240),
        },
    }


def _parse_event_detail(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}


def _connect_client(region: str):
    return boto3.client("connect", region_name=region)


def _connect_probe_regions(primary: str) -> list[str]:
    """Regions to try when auto-discovering a Connect instance."""
    extra = (os.getenv("AWS_CONNECT_PROBE_REGIONS") or "us-east-1,us-west-2").split(",")
    seen: set[str] = set()
    out: list[str] = []
    for r in [primary, *extra]:
        reg = (r or "").strip().lower()
        if not reg or reg in seen:
            continue
        seen.add(reg)
        out.append(reg)
    return out


def _resolve_connect_instance(
    instance_id: str | None,
    region: str | None,
) -> tuple[str, str, str | None]:
    """
    Return (instance_id, region, error_message).
    Auto-discovers via list_instances when env/UI id is empty and exactly one instance exists.
    """
    inst = (instance_id or os.getenv("AWS_CONNECT_INSTANCE_ID") or "").strip()
    reg = (region or os.getenv("AWS_CONNECT_REGION") or os.getenv("AWS_REGION") or "us-east-1").strip().lower()
    if inst:
        return inst, reg, None

    errors: list[str] = []
    for probe_reg in _connect_probe_regions(reg):
        try:
            client = _connect_client(probe_reg)
            resp = client.list_instances(MaxResults=10)
            summaries = resp.get("InstanceSummaryList") or []
            if len(summaries) == 1:
                found = str(summaries[0].get("Id") or "").strip()
                if found:
                    return found, probe_reg, None
            if len(summaries) > 1:
                preferred = (os.getenv("GENESIS_CONNECT_INSTANCE_ALIAS") or "genesis-connect").strip().lower()
                for s in summaries:
                    alias = str(s.get("InstanceAlias") or "").strip().lower()
                    if alias == preferred or preferred in alias:
                        found = str(s.get("Id") or "").strip()
                        if found:
                            return found, probe_reg, None
                aliases = [
                    f"{s.get('InstanceAlias') or s.get('Id')} ({probe_reg})"
                    for s in summaries[:5]
                ]
                return (
                    "",
                    reg,
                    "Multiple Connect instances found; set AWS_CONNECT_INSTANCE_ID. "
                    f"Candidates: {', '.join(aliases)}",
                )
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "ClientError")
            if code in ("ExpiredTokenException", "UnrecognizedClientException", "InvalidClientTokenId"):
                return "", reg, f"AWS credentials invalid or expired ({code}). Run: aws sso login && python3 scripts/sync_aws_creds_to_dotenv.py --profile default"
            errors.append(f"{probe_reg}: {code}")
        except Exception as e:
            err = str(e)
            if "Could not connect to the endpoint" in err:
                continue
            errors.append(f"{probe_reg}: {err[:120]}")

    hint = "; ".join(errors[:3]) if errors else "no instances in probed regions"
    return (
        "",
        reg,
        f"AWS_CONNECT_INSTANCE_ID is not set and auto-discovery failed ({hint}). "
        "Set AWS_CONNECT_INSTANCE_ID and AWS_CONNECT_REGION in .env.",
    )


def _cloudtrail_client(region: str):
    return boto3.client("cloudtrail", region_name=region)


def _cloudwatch_client(region: str):
    return boto3.client("cloudwatch", region_name=region)


def _lambda_function_name(raw: str) -> str:
    """Accept bare name or full Lambda ARN."""
    s = (raw or "").strip()
    if not s:
        return ""
    if ":function:" in s:
        tail = s.split(":function:", 1)[1]
        return tail.split(":")[0]
    return s


def _parse_event_time(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    s = str(raw).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _cloudtrail_lookup_by_source(
    region: str,
    event_source: str,
    *,
    lookback_hours: int = 24,
    max_events: int = 150,
) -> list[dict[str, Any]]:
    end = _utc_now()
    start = end - timedelta(hours=max(1, min(lookback_hours, 168)))
    client = _cloudtrail_client(region)
    rows: list[dict[str, Any]] = []
    token = None
    fetched = 0
    while fetched < max_events:
        kwargs: dict[str, Any] = {
            "LookupAttributes": [{"AttributeKey": "EventSource", "AttributeValue": event_source}],
            "StartTime": start,
            "EndTime": end,
            "MaxResults": min(50, max_events - fetched),
        }
        if token:
            kwargs["NextToken"] = token
        resp = client.lookup_events(**kwargs)
        for ev in resp.get("Events") or []:
            fetched += 1
            detail = _parse_event_detail(ev.get("CloudTrailEvent"))
            ename = str(ev.get("EventName") or detail.get("eventName") or "")
            et = ev.get("EventTime")
            ui = detail.get("userIdentity") or {}
            rows.append(
                {
                    "event_time": et.isoformat() if hasattr(et, "isoformat") else str(et or ""),
                    "event_time_dt": _parse_event_time(et),
                    "event_name": ename,
                    "username": str(
                        ev.get("Username")
                        or ui.get("userName")
                        or ui.get("arn")
                        or ui.get("principalId")
                        or "—"
                    ),
                    "error_code": str(detail.get("errorCode") or detail.get("errorMessage") or ""),
                    "detail": detail,
                    "event_id": str(ev.get("EventId") or ""),
                }
            )
            if fetched >= max_events:
                break
        token = resp.get("NextToken")
        if not token:
            break
    rows.sort(key=lambda x: x.get("event_time") or "", reverse=True)
    return rows


def _lambda_metric_sum(
    region: str,
    function_name: str,
    metric_name: str,
    *,
    hours: int = 24,
) -> float:
    if not function_name:
        return 0.0
    end = _utc_now()
    start = end - timedelta(hours=max(1, hours))
    try:
        resp = _cloudwatch_client(region).get_metric_statistics(
            Namespace="AWS/Lambda",
            MetricName=metric_name,
            Dimensions=[{"Name": "FunctionName", "Value": function_name}],
            StartTime=start,
            EndTime=end,
            Period=max(60, min(3600, hours * 3600)),
            Statistics=["Sum"],
        )
    except ClientError:
        return 0.0
    total = 0.0
    for dp in resp.get("Datapoints") or []:
        try:
            total += float(dp.get("Sum") or 0)
        except (TypeError, ValueError):
            pass
    return total


def _event_mentions_lambda(row: dict[str, Any], function_name: str) -> bool:
    if not function_name:
        return False
    fn_lower = function_name.lower()
    detail = row.get("detail") or {}
    for key in ("requestParameters", "responseElements", "additionalEventData"):
        block = detail.get(key)
        if not isinstance(block, dict):
            continue
        for val in block.values():
            if fn_lower in str(val).lower():
                return True
    hay = json.dumps(detail, default=str).lower()
    return fn_lower in hay


def _event_mentions_sso(row: dict[str, Any], idp: str, role: str) -> bool:
    detail = row.get("detail") or {}
    hay = json.dumps(detail, default=str).lower()
    if idp and idp.lower() in hay:
        return True
    if role and role.lower() in hay:
        return True
    arn = str((detail.get("userIdentity") or {}).get("arn") or "").lower()
    return bool(role and role.lower() in arn)


def _genesis_monitor_snapshot(
    region: str,
    *,
    trail_admin: list[dict[str, Any]],
    genesis_cfg: dict[str, Any],
) -> dict[str, Any]:
    fn = _lambda_function_name(str(genesis_cfg.get("azuread_sync_lambda") or "AzureADSync"))
    idp = str(genesis_cfg.get("sso_idp") or "Azure_Genesis_SSO")
    role = str(genesis_cfg.get("sso_role") or "Azure_Genesis_SSO_Role")
    corr_min = int(genesis_cfg.get("sync_delete_correlation_min") or 30)
    err_threshold = int(genesis_cfg.get("lambda_error_threshold") or 1)

    components = [
        {"name": "AzureADSync Lambda", "id": fn or "AzureADSync", "role": "Syncs Azure AD users → Connect"},
        {"name": "IAM SAML IdP", "id": idp, "role": "Azure AD SSO federation for agent login"},
        {"name": "IAM Role", "id": role, "role": "Assumed by agents after SAML auth"},
        {
            "name": "Connect instance",
            "id": str(genesis_cfg.get("instance_alias") or "genesis-connect"),
            "role": "Agent user registry (DeleteUser = mass removal risk)",
        },
    ]

    lambda_error: str | None = None
    lambda_invokes: list[dict[str, Any]] = []
    lambda_config_changes: list[dict[str, Any]] = []
    invocations_24h = 0.0
    errors_24h = 0.0
    errors_1h = 0.0

    try:
        invocations_24h = _lambda_metric_sum(region, fn, "Invocations", hours=24)
        errors_24h = _lambda_metric_sum(region, fn, "Errors", hours=24)
        errors_1h = _lambda_metric_sum(region, fn, "Errors", hours=1)
        lambda_rows = _cloudtrail_lookup_by_source(
            region, "lambda.amazonaws.com", lookback_hours=24, max_events=200
        )
        for row in lambda_rows:
            if not _event_mentions_lambda(row, fn):
                continue
            ename = row.get("event_name") or ""
            if ename == "Invoke":
                lambda_invokes.append(
                    {
                        "event_time": row.get("event_time"),
                        "username": row.get("username"),
                        "error_code": row.get("error_code"),
                    }
                )
            elif ename in _LAMBDA_CONFIG_EVENTS:
                lambda_config_changes.append(
                    {
                        "event_time": row.get("event_time"),
                        "event_name": ename,
                        "username": row.get("username"),
                    }
                )
        config_7d = _cloudtrail_lookup_by_source(
            region, "lambda.amazonaws.com", lookback_hours=168, max_events=120
        )
        for row in config_7d:
            if _event_mentions_lambda(row, fn) and (row.get("event_name") in _LAMBDA_CONFIG_EVENTS):
                if not any(c.get("event_id") == row.get("event_id") for c in lambda_config_changes):
                    lambda_config_changes.append(
                        {
                            "event_time": row.get("event_time"),
                            "event_name": row.get("event_name"),
                            "username": row.get("username"),
                        }
                    )
        lambda_config_changes.sort(key=lambda x: x.get("event_time") or "", reverse=True)
    except ClientError as e:
        lambda_error = e.response.get("Error", {}).get("Message", str(e))
    except Exception as e:
        lambda_error = str(e)

    sso_failures: list[dict[str, Any]] = []
    sso_failures_1h = 0
    sso_error: str | None = None
    try:
        sts_rows = _cloudtrail_lookup_by_source(
            region, "sts.amazonaws.com", lookback_hours=24, max_events=150
        )
        iam_rows = _cloudtrail_lookup_by_source(
            region, "iam.amazonaws.com", lookback_hours=168, max_events=80
        )
        for row in sts_rows + iam_rows:
            ename = row.get("event_name") or ""
            is_sts_login = ename in ("AssumeRoleWithSAML", "AssumeRole")
            is_iam_saml = ename in _IAM_SSO_EVENTS and "saml" in ename.lower()
            if not is_sts_login and not is_iam_saml:
                continue
            if is_sts_login:
                if not row.get("error_code"):
                    continue
                if not _event_mentions_sso(row, idp, role) and ename != "AssumeRoleWithSAML":
                    continue
            elif not _event_mentions_sso(row, idp, role):
                continue
            sso_failures.append(
                {
                    "event_time": row.get("event_time"),
                    "event_name": ename,
                    "error_code": row.get("error_code") or "config-change",
                    "username": row.get("username"),
                }
            )
            et = row.get("event_time_dt")
            if et and et >= _utc_now() - timedelta(hours=1):
                sso_failures_1h += 1
    except ClientError as e:
        sso_error = e.response.get("Error", {}).get("Message", str(e))
    except Exception as e:
        sso_error = str(e)

    delete_events = [
        e
        for e in trail_admin
        if e.get("event_name") == "DeleteUser" and e.get("event_time")
    ]
    correlations: list[dict[str, Any]] = []
    for d in delete_events:
        d_dt = _parse_event_time(d.get("event_time"))
        if not d_dt:
            continue
        for inv in lambda_invokes:
            i_dt = _parse_event_time(inv.get("event_time"))
            if not i_dt:
                continue
            delta_min = abs((d_dt - i_dt).total_seconds()) / 60.0
            if delta_min <= corr_min:
                correlations.append(
                    {
                        "delete_time": d.get("event_time"),
                        "sync_invoke_time": inv.get("event_time"),
                        "delta_minutes": round(delta_min, 1),
                        "delete_actor": d.get("username"),
                        "sync_actor": inv.get("username"),
                    }
                )

    last_invoke = lambda_invokes[0]["event_time"] if lambda_invokes else None
    investigation = [
        {
            "question": "Did AzureADSync Lambda execute around the time of user deletions?",
            "status": "critical" if correlations else ("ok" if lambda_invokes else "unknown"),
            "finding": (
                f"{len(correlations)} DeleteUser event(s) within {corr_min} min of AzureADSync Invoke."
                if correlations
                else (
                    f"Last Invoke in 24h: {last_invoke or 'none observed in CloudTrail'}."
                    if lambda_invokes
                    else "No AzureADSync Invoke events in CloudTrail (24h) — check function name/region."
                )
            ),
        },
        {
            "question": "Were there Lambda config/code changes before the incident window?",
            "status": "warning" if lambda_config_changes else "ok",
            "finding": (
                f"{len(lambda_config_changes)} config/code change(s) in 7d — review deploy timeline."
                if lambda_config_changes
                else "No UpdateFunctionCode/Configuration events for AzureADSync in 7d."
            ),
        },
        {
            "question": "Did Azure AD SSO (SAML) fail for agents trying to log in?",
            "status": "critical" if sso_failures_1h >= 5 else ("warning" if sso_failures else "ok"),
            "finding": (
                f"{len(sso_failures)} SAML/STS failure(s) in 24h ({sso_failures_1h} in last hour)."
                if sso_failures
                else "No AssumeRoleWithSAML failures tied to Genesis SSO in 24h."
            ),
        },
        {
            "question": "Is AzureADSync reporting Lambda errors?",
            "status": "critical" if errors_1h >= err_threshold else ("warning" if errors_24h >= err_threshold else "ok"),
            "finding": (
                f"CloudWatch Errors: {int(errors_1h)} (1h), {int(errors_24h)} (24h); "
                f"Invocations 24h: {int(invocations_24h)}."
            ),
        },
    ]

    return {
        "platform": "Genesis",
        "description": "Care platform — Connect + Lambda (AzureADSync) + Azure AD SSO",
        "auth_flow": [
            "Agent → Azure AD SSO (SAML)",
            f"IAM IdP ({idp})",
            f"IAM Role ({role})",
            "Amazon Connect agent access",
        ],
        "components": components,
        "azuread_sync": {
            "function_name": fn,
            "invocations_24h": int(invocations_24h),
            "errors_24h": int(errors_24h),
            "errors_1h": int(errors_1h),
            "last_invoke": last_invoke,
            "recent_invocations": lambda_invokes[:15],
            "config_changes_7d": lambda_config_changes[:10],
            "error": lambda_error,
        },
        "sso": {
            "idp_name": idp,
            "role_name": role,
            "failures_24h": len(sso_failures),
            "failures_1h": sso_failures_1h,
            "recent_failures": sso_failures[:15],
            "error": sso_error,
        },
        "correlation": {
            "window_minutes": corr_min,
            "delete_user_near_sync": correlations[:20],
            "alert": bool(correlations),
        },
        "investigation": investigation,
    }


def _genesis_alerts(genesis: dict[str, Any], genesis_cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    critical: list[dict[str, Any]] = []
    proactive: list[dict[str, Any]] = []
    sync = genesis.get("azuread_sync") or {}
    sso = genesis.get("sso") or {}
    corr = genesis.get("correlation") or {}
    fn = sync.get("function_name") or "AzureADSync"
    err_threshold = int(genesis_cfg.get("lambda_error_threshold") or 1)

    if corr.get("alert"):
        n = len(corr.get("delete_user_near_sync") or [])
        critical.append(
            _alert(
                "genesis_sync_delete_correlation",
                "critical",
                "Genesis: DeleteUser near AzureADSync",
                f"{n} Connect DeleteUser event(s) within {corr.get('window_minutes')} min of "
                f"{fn} Invoke — likely sync-related (June 3 pattern).",
                value=n,
                threshold=1,
            )
        )

    errors_1h = int(sync.get("errors_1h") or 0)
    if errors_1h >= err_threshold:
        critical.append(
            _alert(
                "genesis_lambda_errors",
                "critical",
                "Genesis: AzureADSync Lambda errors",
                f"{fn} reported {errors_1h} CloudWatch error(s) in the last hour.",
                value=errors_1h,
                threshold=err_threshold,
            )
        )
    elif sync.get("error"):
        critical.append(
            _alert(
                "genesis_lambda_errors",
                "unknown",
                "Genesis: AzureADSync metrics",
                f"Could not read Lambda metrics/events: {sync.get('error')}",
            )
        )
    else:
        critical.append(
            _alert(
                "genesis_lambda_errors",
                "ok",
                "Genesis: AzureADSync Lambda errors",
                f"No errors in last hour ({int(sync.get('errors_24h') or 0)} in 24h).",
                value=errors_1h,
                threshold=err_threshold,
            )
        )

    config_changes = sync.get("config_changes_7d") or []
    if config_changes:
        proactive.append(
            _alert(
                "genesis_lambda_deploy",
                "warning",
                "Genesis: AzureADSync deployment activity",
                f"{len(config_changes)} Lambda config/code change(s) in 7 days — verify against change calendar.",
                value=len(config_changes),
                category="proactive",
            )
        )

    sso_fails_1h = int(sso.get("failures_1h") or 0)
    if sso_fails_1h >= 5:
        critical.append(
            _alert(
                "genesis_sso_failures",
                "critical",
                "Genesis: Azure AD SSO failures",
                f"{sso_fails_1h} SAML/STS failure(s) in the last hour ({sso.get('idp_name')}).",
                value=sso_fails_1h,
                threshold=5,
            )
        )
    elif sso.get("failures_24h"):
        proactive.append(
            _alert(
                "genesis_sso_failures",
                "warning",
                "Genesis: Azure AD SSO failures",
                f"{sso.get('failures_24h')} SAML/STS failure(s) in 24h.",
                value=sso.get("failures_24h"),
                category="proactive",
            )
        )
    else:
        proactive.append(
            _alert(
                "genesis_sso_health",
                "ok",
                "Genesis: Azure AD SSO path",
                f"No SAML failures for {sso.get('idp_name')} → {sso.get('role_name')} in 24h.",
                category="proactive",
            )
        )

    return critical, proactive


def _list_queue_arns(client, instance_id: str, configured: list[str]) -> list[str]:
    if configured:
        return configured
    out: list[str] = []
    token = None
    while True:
        kwargs: dict[str, Any] = {"InstanceId": instance_id, "MaxResults": 100}
        if token:
            kwargs["NextToken"] = token
        resp = client.list_queues(**kwargs)
        for q in resp.get("QueueSummaryList") or []:
            arn = (q.get("Arn") or q.get("QueueArn") or "").strip()
            if arn:
                out.append(arn)
        token = resp.get("NextToken")
        if not token:
            break
    return out[:25]


def _list_all_users(client, instance_id: str) -> tuple[list[dict[str, str]], int]:
    users: list[dict[str, str]] = []
    token = None
    while True:
        kwargs: dict[str, Any] = {"InstanceId": instance_id, "MaxResults": 100}
        if token:
            kwargs["NextToken"] = token
        resp = client.list_users(**kwargs)
        for u in resp.get("UserSummaryList") or []:
            uid = str(u.get("Id") or "")
            uname = str(u.get("Username") or u.get("Login") or uid)
            users.append({"id": uid, "username": uname})
        token = resp.get("NextToken")
        if not token:
            break
    return users, len(users)


def _get_current_queue_metrics(
    client, instance_id: str, queue_arns: list[str]
) -> list[dict[str, Any]]:
    if not queue_arns:
        return []
    metric_specs = [
        {"Name": "AGENTS_AVAILABLE", "Unit": "COUNT"},
        {"Name": "AGENTS_ONLINE", "Unit": "COUNT"},
        {"Name": "AGENTS_AFTER_CONTACT_WORK", "Unit": "COUNT"},
        {"Name": "AGENTS_NON_PRODUCTIVE", "Unit": "COUNT"},
        {"Name": "CONTACTS_IN_QUEUE", "Unit": "COUNT"},
        {"Name": "OLDEST_CONTACT_AGE", "Unit": "SECONDS"},
    ]
    rows: list[dict[str, Any]] = []
    chunk_size = 10
    for i in range(0, len(queue_arns), chunk_size):
        chunk = queue_arns[i : i + chunk_size]
        try:
            resp = client.get_current_metric_data(
                InstanceId=instance_id,
                Filters=[{"Name": "Queue", "Values": chunk}],
                Groupings=["QUEUE"],
                CurrentMetrics=metric_specs,
            )
        except ClientError:
            continue
        by_queue: dict[str, dict[str, Any]] = {}
        for item in resp.get("MetricResults") or []:
            dims = item.get("Dimensions") or {}
            qid = str(dims.get("Queue") or dims.get("QueueId") or "unknown")
            row = by_queue.setdefault(
                qid,
                {
                    "queue_id": qid,
                    "agents_available": 0,
                    "agents_online": 0,
                    "agents_acw": 0,
                    "agents_non_productive": 0,
                    "contacts_in_queue": 0,
                    "oldest_contact_age_sec": 0,
                },
            )
            for coll in item.get("Collections") or []:
                name = str(coll.get("Metric", {}).get("Name") or "")
                try:
                    val = float((coll.get("Value") or 0))
                except (TypeError, ValueError):
                    val = 0.0
                if name == "AGENTS_AVAILABLE":
                    row["agents_available"] = int(val)
                elif name == "AGENTS_ONLINE":
                    row["agents_online"] = int(val)
                elif name == "AGENTS_AFTER_CONTACT_WORK":
                    row["agents_acw"] = int(val)
                elif name == "AGENTS_NON_PRODUCTIVE":
                    row["agents_non_productive"] = int(val)
                elif name == "CONTACTS_IN_QUEUE":
                    row["contacts_in_queue"] = int(val)
                elif name == "OLDEST_CONTACT_AGE":
                    row["oldest_contact_age_sec"] = int(val)
        rows.extend(by_queue.values())
    return rows


def _record_agent_count(count: int) -> None:
    now = time.time()
    with _history_lock:
        _agent_count_history.append((now, count))
        cutoff = now - 900
        while _agent_count_history and _agent_count_history[0][0] < cutoff:
            _agent_count_history.pop(0)


def _agent_count_drop_pct(current: int) -> tuple[float | None, int | None]:
    """Compare current count to max in last 15 minutes."""
    now = time.time()
    window = now - 900
    with _history_lock:
        prior = [c for ts, c in _agent_count_history if ts < now - 30 and ts >= window]
    if not prior:
        return None, None
    peak = max(prior)
    if peak <= 0:
        return None, peak
    drop = ((peak - current) / peak) * 100.0
    return round(drop, 2), peak


def _is_business_hours(cfg: dict[str, Any]) -> bool:
    try:
        tz = ZoneInfo(str(cfg.get("business_tz") or "America/New_York"))
    except Exception:
        tz = ZoneInfo("America/New_York")
    now_local = _utc_now().astimezone(tz)
    if now_local.weekday() >= 5:
        return False
    start_h = int(cfg.get("business_hours_start") or 8)
    end_h = int(cfg.get("business_hours_end") or 18)
    return start_h <= now_local.hour < end_h


def _cloudtrail_connect_events(
    region: str,
    *,
    lookback_hours: int = 24,
    max_events: int = 200,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """
    Returns (admin_events, login_failure_events, login_failure_count_in_window).
    """
    end = _utc_now()
    start = end - timedelta(hours=max(1, min(lookback_hours, 168)))
    login_window_min = _env_int("AWS_CONNECT_LOGIN_FAIL_WINDOW_MIN", 5, 1, 60)
    login_cutoff = end - timedelta(minutes=login_window_min)

    client = _cloudtrail_client(region)
    admin: list[dict[str, Any]] = []
    login_failures: list[dict[str, Any]] = []
    login_fail_recent = 0
    token = None
    fetched = 0

    while fetched < max_events:
        kwargs: dict[str, Any] = {
            "LookupAttributes": [
                {"AttributeKey": "EventSource", "AttributeValue": "connect.amazonaws.com"},
            ],
            "StartTime": start,
            "EndTime": end,
            "MaxResults": min(50, max_events - fetched),
        }
        if token:
            kwargs["NextToken"] = token
        try:
            resp = client.lookup_events(**kwargs)
        except ClientError as e:
            raise e
        for ev in resp.get("Events") or []:
            fetched += 1
            detail = _parse_event_detail(ev.get("CloudTrailEvent"))
            ename = str(ev.get("EventName") or detail.get("eventName") or "")
            err = detail.get("errorCode") or detail.get("errorMessage")
            et = ev.get("EventTime")
            if hasattr(et, "isoformat"):
                ets = et.isoformat()
            else:
                ets = str(et or "")
            ui = detail.get("userIdentity") or {}
            actor = str(
                ev.get("Username")
                or ui.get("userName")
                or ui.get("arn")
                or ui.get("principalId")
                or "—"
            )
            row = {
                "event_time": ets,
                "event_name": ename,
                "username": actor,
                "error_code": str(err or ""),
                "read_only": str(ev.get("ReadOnly", "")),
                "event_id": str(ev.get("EventId") or ""),
            }
            is_login_related = ename in _LOGIN_FAILURE_EVENT_HINTS or "login" in ename.lower()
            if err and (is_login_related or "connect" in str(detail.get("eventSource") or "").lower()):
                login_failures.append(row)
                if et and hasattr(et, "timestamp"):
                    evt = et if et.tzinfo else et.replace(tzinfo=timezone.utc)
                    if evt >= login_cutoff.replace(tzinfo=timezone.utc):
                        login_fail_recent += 1
            if ename in _CONNECT_ADMIN_EVENTS:
                admin.append(row)
            if fetched >= max_events:
                break
        token = resp.get("NextToken")
        if not token:
            break

    admin.sort(key=lambda x: x.get("event_time") or "", reverse=True)
    login_failures.sort(key=lambda x: x.get("event_time") or "", reverse=True)
    return admin, login_failures, login_fail_recent


def _roster_reconciliation(
    actual_users: list[dict[str, str]], expected: list[str]
) -> dict[str, Any]:
    expected_set = {u.lower() for u in expected if u.strip()}
    actual_set = {u["username"].lower() for u in actual_users if u.get("username")}
    missing = sorted(expected_set - actual_set)
    extra = sorted(actual_set - expected_set)
    return {
        "expected_count": len(expected_set),
        "actual_count": len(actual_set),
        "missing_usernames": missing[:100],
        "extra_usernames": extra[:100],
        "in_sync": not missing and not extra if expected_set else None,
    }


def _alert(
    alert_id: str,
    severity: str,
    title: str,
    message: str,
    *,
    value: Any = None,
    threshold: Any = None,
    category: str = "critical",
) -> dict[str, Any]:
    return {
        "id": alert_id,
        "severity": severity,
        "title": title,
        "message": message,
        "value": value,
        "threshold": threshold,
        "category": category,
    }


def connect_monitor_snapshot(
    *,
    instance_id: str | None = None,
    region: str | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    cfg = connect_monitor_config()
    inst, reg, resolve_err = _resolve_connect_instance(instance_id, region)
    if not inst:
        return {
            "success": False,
            "error": resolve_err or "Connect instance not configured.",
            "config": cfg,
        }
    if not re.match(r"^[a-z0-9-]+$", reg):
        return {"success": False, "error": "Invalid AWS region.", "config": cfg}

    cache_key = f"{inst}:{reg}"
    now = time.time()
    if (
        not force_refresh
        and _snapshot_cache.get("key") == cache_key
        and _snapshot_cache.get("payload")
        and now - float(_snapshot_cache.get("ts") or 0) < _CACHE_TTL_SEC
    ):
        out = dict(_snapshot_cache["payload"])
        out["cached"] = True
        return out

    try:
        cclient = _connect_client(reg)
        inst_meta = cclient.describe_instance(InstanceId=inst)
        instance_alias = str(inst_meta.get("Instance", {}).get("InstanceAlias") or inst)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "ClientError")
        msg = e.response.get("Error", {}).get("Message", str(e))
        return {"success": False, "error": f"Connect describe_instance: {code}: {msg}", "config": cfg}
    except Exception as e:
        return {"success": False, "error": str(e), "config": cfg}

    health_ok = True
    health_message = "Connect API reachable (describe_instance + list_users probe)."
    users: list[dict[str, str]] = []
    user_count = 0
    queue_metrics: list[dict[str, Any]] = []
    try:
        users, user_count = _list_all_users(cclient, inst)
        queue_arns = _list_queue_arns(cclient, inst, cfg["queue_ids"])
        queue_metrics = _get_current_queue_metrics(cclient, inst, queue_arns)
    except ClientError as e:
        health_ok = False
        health_message = f"Connect metrics/users: {e.response.get('Error', {}).get('Message', str(e))}"
    except Exception as e:
        health_ok = False
        health_message = str(e)

    _record_agent_count(user_count)
    drop_pct, peak_count = _agent_count_drop_pct(user_count)

    expected_users = [
        u.strip()
        for u in (os.getenv("AWS_CONNECT_EXPECTED_USERS") or "").split(",")
        if u.strip()
    ]
    roster = _roster_reconciliation(users, expected_users)

    trail_admin: list[dict[str, Any]] = []
    trail_login: list[dict[str, Any]] = []
    login_fail_recent = 0
    trail_error = None
    try:
        trail_admin, trail_login, login_fail_recent = _cloudtrail_connect_events(
            reg, lookback_hours=24, max_events=250
        )
    except ClientError as e:
        trail_error = e.response.get("Error", {}).get("Message", str(e))
    except Exception as e:
        trail_error = str(e)

    critical: list[dict[str, Any]] = []
    proactive: list[dict[str, Any]] = []

    baseline = int(cfg.get("agent_baseline") or 0)
    drop_threshold = float(cfg.get("agent_drop_pct") or 7.5)
    effective_peak = peak_count if peak_count is not None else baseline

    if effective_peak and effective_peak > 0:
        ref = effective_peak
        drop = drop_pct if drop_pct is not None else (
            ((ref - user_count) / ref) * 100.0 if ref > user_count else 0.0
        )
        if drop >= drop_threshold:
            critical.append(
                _alert(
                    "agent_count_drop",
                    "critical",
                    "Agent count drop",
                    f"Registered Connect users fell {drop:.1f}% ({user_count} now vs peak/baseline {ref}).",
                    value=drop,
                    threshold=drop_threshold,
                )
            )
        else:
            critical.append(
                _alert(
                    "agent_count_drop",
                    "ok",
                    "Agent count drop",
                    f"User count stable ({user_count}; reference {ref}; Δ {drop:.1f}%).",
                    value=drop,
                    threshold=drop_threshold,
                )
            )
    else:
        critical.append(
            _alert(
                "agent_count_drop",
                "unknown",
                "Agent count drop",
                f"Current users: {user_count}. Set AWS_CONNECT_AGENT_BASELINE_COUNT for baseline comparison.",
                value=user_count,
                threshold=drop_threshold,
            )
        )

    login_threshold = int(cfg.get("login_fail_threshold") or 10)
    login_window = int(cfg.get("login_fail_window_min") or 5)
    if login_fail_recent >= login_threshold:
        critical.append(
            _alert(
                "login_failure_spike",
                "critical",
                "Agent login failure spike",
                f"{login_fail_recent} failed Connect login/auth event(s) in the last {login_window} minutes (CloudTrail).",
                value=login_fail_recent,
                threshold=login_threshold,
            )
        )
    elif trail_error:
        critical.append(
            _alert(
                "login_failure_spike",
                "unknown",
                "Agent login failure spike",
                f"CloudTrail unavailable: {trail_error}",
            )
        )
    else:
        critical.append(
            _alert(
                "login_failure_spike",
                "ok",
                "Agent login failure spike",
                f"{login_fail_recent} failure(s) in last {login_window} min (threshold {login_threshold}).",
                value=login_fail_recent,
                threshold=login_threshold,
            )
        )

    destructive = [e for e in trail_admin if e.get("event_name") == "DeleteUser"]
    if destructive:
        critical.append(
            _alert(
                "connect_user_deletion",
                "critical",
                "AWS Connect user deletion (CloudTrail)",
                f"{len(destructive)} DeleteUser event(s) in the last 24h — review immediately.",
                value=len(destructive),
                threshold=1,
            )
        )
    elif any(e.get("event_name") == "UpdateUser" for e in trail_admin):
        upd = sum(1 for e in trail_admin if e.get("event_name") == "UpdateUser")
        critical.append(
            _alert(
                "connect_user_modification",
                "warning",
                "AWS Connect user modifications",
                f"{upd} UpdateUser event(s) in the last 24h.",
                value=upd,
            )
        )
    else:
        critical.append(
            _alert(
                "connect_user_deletion",
                "ok",
                "AWS Connect admin changes",
                "No DeleteUser events in the last 24h.",
                value=0,
                threshold=1,
            )
        )

    proactive.append(
        _alert(
            "instance_health",
            "ok" if health_ok else "critical",
            "Connect Center API health",
            health_message,
            category="proactive",
        )
    )

    if expected_users:
        if roster.get("in_sync") is False:
            proactive.append(
                _alert(
                    "roster_reconciliation",
                    "warning",
                    "Agent roster reconciliation",
                    f"Missing {len(roster.get('missing_usernames') or [])} expected user(s); "
                    f"{len(roster.get('extra_usernames') or [])} unexpected user(s) in Connect.",
                    value=len(roster.get("missing_usernames") or []),
                    category="proactive",
                )
            )
        else:
            proactive.append(
                _alert(
                    "roster_reconciliation",
                    "ok",
                    "Agent roster reconciliation",
                    f"Expected roster matches Connect ({roster.get('actual_count')} users).",
                    category="proactive",
                )
            )
    else:
        proactive.append(
            _alert(
                "roster_reconciliation",
                "unknown",
                "Agent roster reconciliation",
                "Set AWS_CONNECT_EXPECTED_USERS (comma-separated) to enable daily roster compare.",
                category="proactive",
            )
        )

    total_available = sum(int(q.get("agents_available") or 0) for q in queue_metrics)
    min_staff = int(cfg.get("staffing_min_available") or 0)
    if _is_business_hours(cfg) and min_staff > 0 and total_available < min_staff:
        proactive.append(
            _alert(
                "queue_staffing",
                "warning",
                "Queue staffing (business hours)",
                f"Available agents {total_available} across monitored queues (min expected {min_staff}).",
                value=total_available,
                threshold=min_staff,
                category="proactive",
            )
        )
    elif queue_metrics:
        proactive.append(
            _alert(
                "queue_staffing",
                "ok",
                "Queue staffing",
                f"Available agents: {total_available} across {len(queue_metrics)} queue(s).",
                value=total_available,
                category="proactive",
            )
        )

    genesis_cfg = cfg.get("genesis") or {}
    genesis_snapshot: dict[str, Any] | None = None
    if genesis_cfg.get("enabled"):
        genesis_snapshot = _genesis_monitor_snapshot(
            reg,
            trail_admin=trail_admin,
            genesis_cfg={**genesis_cfg, "instance_alias": instance_alias},
        )
        g_crit, g_pro = _genesis_alerts(genesis_snapshot, genesis_cfg)
        critical.extend(g_crit)
        proactive.extend(g_pro)

    dashboard = {
        "instance_alias": instance_alias,
        "total_users": user_count,
        "agents_available_total": total_available,
        "agents_online_total": sum(int(q.get("agents_online") or 0) for q in queue_metrics),
        "contacts_in_queue_total": sum(int(q.get("contacts_in_queue") or 0) for q in queue_metrics),
        "queues": queue_metrics,
        "login_failures_24h": len(trail_login),
        "admin_changes_24h": len(trail_admin),
    }

    payload = {
        "success": True,
        "cached": False,
        "config": cfg,
        "instance_id": inst,
        "region": reg,
        "generated_at": _utc_now().isoformat(),
        "critical_alerts": critical,
        "proactive_alerts": proactive,
        "dashboard": dashboard,
        "audit_trail": trail_admin[:80],
        "login_failures": trail_login[:40],
        "roster": roster,
        "trail_error": trail_error,
        "genesis": genesis_snapshot,
    }
    _snapshot_cache.update({"key": cache_key, "ts": now, "payload": payload})
    return payload
