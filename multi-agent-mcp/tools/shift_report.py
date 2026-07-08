"""
Shift handoff reports: PagerDuty + Slack #oncall_escalation + GRM/Jira + Bedrock.
"""
from __future__ import annotations

import asyncio
import csv
import html as html_module
import io
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests

from tools.bedrock_tool import ask_bedrock
from tools.deployments_calendar import (
    _internal_deployments_api_url,
    _internal_deployments_http_timeout,
)
from tools.jira_mcp import fetch_jira_issues_by_keys
from tools.mcp_connect import get_mcp_api_key, open_mcp_session

PD_LIST_INCIDENTS = "pagerduty__list_incidents"
DD_SEARCH_MONITORS = "datadog__search_datadog_monitors"
DD_SEARCH_EVENTS = "datadog__search_datadog_events"
OUTLOOK_SEARCH_EMAIL = "outlook__search_email"
SLACK_SEARCH_CHANNELS = "slack__slack_search_channels"
SLACK_READ_CHANNEL = "slack__slack_read_channel"
SLACK_READ_THREAD = "slack__slack_read_thread"

DEFAULT_SHIFT_TZ = "America/Mexico_City"
DEFAULT_ONCALL_CHANNEL = "oncall_escalation"
DEFAULT_PROD_DEP_CHANNEL = "prod-dep-update"
DEFAULT_PROD_DEP_CHANNEL_ID = "CGNF4QCPJ"
DEFAULT_SHIFT_OUTLOOK_MAILBOX = ""

# 24h coverage: Shift 1 Mexico 11:30–20:00, Shift 2 20:00–02:30, Shift 3 02:30–11:30 (America/Mexico_City)
SHIFT_MODES = ("shift1", "shift2", "shift3")
DEFAULT_SHIFT_SCHEDULE: dict[str, dict[str, tuple[int, int]]] = {
    "shift1": {"start": (11, 30), "end": (20, 0)},
    "shift2": {"start": (20, 0), "end": (2, 30)},
    "shift3": {"start": (2, 30), "end": (11, 30)},
}
SHIFT_LABELS = {
    "shift1": "Shift 1 — Mexico",
    "shift2": "Shift 2",
    "shift3": "Shift 3",
}

# Datadog shift report: skip muted monitors and A/B canary suffixes (-a / -b)
_DD_MONITOR_SUFFIX_EXCLUDE = re.compile(r"-(?:a|b)\s*$", re.IGNORECASE)
_DD_GOLDEN_ENV_TAG = re.compile(r"(?<!\!)env:\s*(?:goldendev|goldenqa)\b", re.IGNORECASE)
_DD_GOLDEN_NAME_PREFIX = re.compile(r"^(?:\[SEV-\d+\]\s*)?G(?:DEV|QA)\b", re.IGNORECASE)
_DD_GOLDEN_NAME_LABEL = re.compile(r"\b(?:goldendev|goldenqa)\b", re.IGNORECASE)
_DD_MONITOR_JSON_RE = re.compile(r"<JSON_DATA>\s*(\[.*?\])\s*</JSON_DATA>", re.DOTALL)
_DD_EVENT_JSON_RE = re.compile(r"<JSON_DATA>\s*(\[.*?\])\s*</JSON_DATA>", re.DOTALL)
_DD_LAST_TRIGGERED_RE = re.compile(r"last triggered at (.+?)(?:\n|\.|$)", re.IGNORECASE)
_DD_EVENT_TITLE_PREFIX_RE = re.compile(
    r"^\[P[123]\]\s*(?:\[(?:Re-)?Triggered(?:\s+on\s+\{[^}]+\})?\]\s*)+",
    re.IGNORECASE,
)
_OUTLOOK_NOISE_RE = re.compile(
    r"daily digest from datadog|marketing@|newsletter|unsubscribe",
    re.IGNORECASE,
)
_GRM_ID_RE = re.compile(r"\bGRM\s*-?\s*(\d+)\b", re.IGNORECASE)
_CHECKLIST_BLOCK_RE = re.compile(
    r"=== Message from Production Release Checklist.*?Message TS:\s*([\d.]+)\n(.*?)(?=\n=== Message from |\Z)",
    re.S,
)
_PRE_CHECK_RE = re.compile(r"pre\s*check\s*list", re.IGNORECASE)
_POST_CHECK_RE = re.compile(r"post\s*check\s*list", re.IGNORECASE)
_IN_DEPLOYMENT_RE = re.compile(
    r"in\s+deployment|pipeline execution is completed|started flink|monitor for \d+ min|checkpoint stable|stable and getting",
    re.IGNORECASE,
)
_DD_PRIORITY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\[SEV-1\]|\bSEV-1\b|\[P1\]", re.IGNORECASE), "P1"),
    (re.compile(r"\[SEV-2\]|\bSEV-2\b|\[P2\]", re.IGNORECASE), "P2"),
    (re.compile(r"\[SEV-3\]|\bSEV-3\b|\[P3\]", re.IGNORECASE), "P3"),
)

SHIFT_TABLE_FIELDS = (
    "source",
    "time_local",
    "priority",
    "service_or_topic",
    "status",
    "summary",
    "action_item",
    "owner",
)

SHIFT_TABLE_HEADERS = (
    "Source",
    "Time",
    "Priority",
    "Service / Topic",
    "Status",
    "Summary",
    "Action Item",
    "Owner",
)


@dataclass(frozen=True)
class ShiftWindow:
    mode: str
    label: str
    start_utc: datetime
    end_utc: datetime
    tz_name: str


def _shift_tz() -> ZoneInfo:
    name = (os.getenv("SHIFT_TIMEZONE") or DEFAULT_SHIFT_TZ).strip()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(DEFAULT_SHIFT_TZ)


def _parse_shift_clock(prefix: str, which: str, default: tuple[int, int]) -> time:
    """Read SHIFT1_START=11:30 style env or SHIFT1_START_HOUR + SHIFT1_START_MINUTE."""
    combined = (os.getenv(f"{prefix}_{which}") or "").strip()
    if combined and ":" in combined:
        parts = combined.split(":", 1)
        try:
            return time(int(parts[0]), int(parts[1]))
        except (TypeError, ValueError):
            pass
    try:
        h = int(os.getenv(f"{prefix}_{which}_HOUR", str(default[0])))
        m = int(os.getenv(f"{prefix}_{which}_MINUTE", str(default[1])))
        return time(h, m)
    except (TypeError, ValueError):
        return time(default[0], default[1])


def _shift_schedule(mode: str) -> tuple[time, time]:
    mode = _normalize_shift_mode(mode)
    defaults = DEFAULT_SHIFT_SCHEDULE[mode]
    prefix = mode.upper()
    start = _parse_shift_clock(prefix, "START", defaults["start"])
    end = _parse_shift_clock(prefix, "END", defaults["end"])
    return start, end


def _normalize_shift_mode(mode: str) -> str:
    m = (mode or "shift1").strip().lower()
    aliases = {
        "1": "shift1",
        "2": "shift2",
        "3": "shift3",
        "mexico": "shift1",
        "start": "shift1",
        "end": "shift3",
    }
    m = aliases.get(m, m)
    if m not in SHIFT_MODES:
        raise ValueError(f"mode must be one of: {', '.join(SHIFT_MODES)}")
    return m


def _shift_bounds_for_date(mode: str, anchor: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Return local start/end for a shift instance anchored on anchor date."""
    start_t, end_t = _shift_schedule(mode)
    start = datetime.combine(anchor, start_t, tzinfo=tz)
    if end_t <= start_t:
        end = datetime.combine(anchor + timedelta(days=1), end_t, tzinfo=tz)
    else:
        end = datetime.combine(anchor, end_t, tzinfo=tz)
    return start, end


def compute_shift_window(mode: str, now_utc: datetime | None = None) -> ShiftWindow:
    """
    Fixed 8h-style rotation (Mexico anchor):
      shift1: 11:30–20:00, shift2: 20:00–02:30, shift3: 02:30–11:30 (America/Mexico_City).
    Uses the current in-progress window, or the most recent completed one for that slot.
    """
    mode = _normalize_shift_mode(mode)
    now_utc = now_utc or datetime.now(timezone.utc)
    tz = _shift_tz()
    local = now_utc.astimezone(tz)

    instances: list[tuple[datetime, datetime]] = []
    for day_offset in (-1, 0, 1):
        anchor = local.date() + timedelta(days=day_offset)
        instances.append(_shift_bounds_for_date(mode, anchor, tz))

    active: tuple[datetime, datetime] | None = None
    for start, end in sorted(instances, key=lambda x: x[0], reverse=True):
        if start <= local < end:
            active = (start, local)
            break

    if active is None:
        past = [(s, e) for s, e in instances if e <= local]
        if not past:
            start, end = instances[0]
        else:
            start, end = max(past, key=lambda x: x[1])
    else:
        start, end = active

    start_t, end_t = _shift_schedule(mode)
    label = (
        f"{SHIFT_LABELS[mode]} — {start.strftime('%Y-%m-%d')} "
        f"{start_t.strftime('%H:%M')}–{end_t.strftime('%H:%M')} {tz.key}"
    )
    if end.date() != start.date() and end_t <= start_t:
        label += f" (ends {end.strftime('%Y-%m-%d %H:%M')})"

    return ShiftWindow(
        mode,
        label,
        start.astimezone(timezone.utc),
        end.astimezone(timezone.utc),
        str(tz),
    )


def _iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _mcp_text(result: Any) -> str:
    parts: list[str] = []
    for item in getattr(result, "content", None) or []:
        if hasattr(item, "text"):
            parts.append(str(item.text))
        elif isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return "\n".join(parts).strip()


def _truncate(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[... truncated ...]"


def _parse_channel_id_from_search(text: str, configured_id: str = "") -> str | None:
    if configured_id:
        return configured_id
    m = re.search(r"/archives/(C[A-Z0-9]+)", text or "")
    if m:
        return m.group(1)
    m = re.search(r"\b(C[A-Z0-9]{8,})\b", text or "")
    return m.group(1) if m else None


async def _resolve_slack_channel_id(
    session,
    channel_query: str,
    env_id_key: str = "",
    default_id: str = "",
) -> str | None:
    configured = (os.getenv(env_id_key) or "").strip() if env_id_key else ""
    if configured:
        return configured
    if default_id:
        return default_id
    query = (channel_query or "").strip()
    if not query:
        return None
    try:
        result = await session.call_tool(
            SLACK_SEARCH_CHANNELS,
            {
                "query": query,
                "limit": 10,
                "channel_types": "public_channel,private_channel",
            },
        )
        return _parse_channel_id_from_search(_mcp_text(result))
    except Exception as exc:
        logging.warning("Slack channel search failed for %s: %s", query, exc)
        return None


async def _resolve_oncall_channel_id(session) -> str | None:
    query = (os.getenv("SLACK_ONCALL_ESCALATION_CHANNEL") or DEFAULT_ONCALL_CHANNEL).strip()
    return await _resolve_slack_channel_id(
        session,
        query,
        env_id_key="SLACK_ONCALL_ESCALATION_CHANNEL_ID",
    )


async def _resolve_prod_dep_channel_id(session) -> str | None:
    query = (os.getenv("SLACK_PROD_DEP_UPDATE_CHANNEL") or DEFAULT_PROD_DEP_CHANNEL).strip()
    return await _resolve_slack_channel_id(
        session,
        query,
        env_id_key="SLACK_PROD_DEP_UPDATE_CHANNEL_ID",
        default_id=DEFAULT_PROD_DEP_CHANNEL_ID,
    )


async def _fetch_pagerduty(session, start_utc: datetime, end_utc: datetime) -> str:
    try:
        result = await session.call_tool(
            PD_LIST_INCIDENTS,
            {
                "query_model": {
                    "since": _iso_z(start_utc),
                    "until": _iso_z(end_utc),
                    "status": ["triggered", "acknowledged", "resolved"],
                }
            },
        )
        return _prepare_pagerduty_payload(_mcp_text(result) or '{"response":[]}')
    except Exception as exc:
        logging.exception("PagerDuty MCP fetch failed")
        return json.dumps({"error": str(exc)})


def _is_pagerduty_auto_resolved(incident: dict[str, Any]) -> bool:
    """Resolved without on-call assignment (typical Datadog/integration auto-resolve)."""
    if str(incident.get("status") or "").lower() != "resolved":
        return False
    assignments = incident.get("assignments")
    return not assignments


def _slim_pagerduty_incident(incident: dict[str, Any]) -> dict[str, Any]:
    service = incident.get("service") if isinstance(incident.get("service"), dict) else {}
    assignments = incident.get("assignments") or []
    assignee = ""
    if assignments and isinstance(assignments[0], dict):
        assignee_obj = assignments[0].get("assignee")
        if isinstance(assignee_obj, dict):
            assignee = str(assignee_obj.get("summary") or "")
    return {
        "incident_number": incident.get("incident_number"),
        "status": incident.get("status"),
        "title": incident.get("title") or incident.get("summary"),
        "service": service.get("summary") if isinstance(service, dict) else "",
        "urgency": incident.get("urgency"),
        "created_at": incident.get("created_at"),
        "resolved_at": incident.get("resolved_at"),
        "assignee": assignee,
    }


def _prepare_pagerduty_payload(raw_text: str) -> str:
    """Split auto-resolved incidents (count only) from incidents needing individual rows."""
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return raw_text

    incidents = data.get("response") if isinstance(data, dict) else None
    if not isinstance(incidents, list):
        return raw_text

    auto_resolved_numbers: list[int] = []
    actionable: list[dict[str, Any]] = []
    for incident in incidents:
        if not isinstance(incident, dict):
            continue
        if _is_pagerduty_auto_resolved(incident):
            num = incident.get("incident_number")
            if num is not None:
                auto_resolved_numbers.append(int(num))
            continue
        actionable.append(_slim_pagerduty_incident(incident))

    auto_resolved_numbers.sort()
    return json.dumps(
        {
            "auto_resolved_count": len(auto_resolved_numbers),
            "auto_resolved_incident_numbers": auto_resolved_numbers,
            "auto_resolved_note": (
                "Auto-resolved PagerDuty incidents are summarized by count only — not listed individually."
                if auto_resolved_numbers
                else ""
            ),
            "incidents": actionable,
        },
        indent=2,
    )


async def _fetch_slack_channel(
    session,
    channel_id: str,
    start_utc: datetime,
    end_utc: datetime,
    label: str,
) -> str:
    try:
        result = await session.call_tool(
            SLACK_READ_CHANNEL,
            {
                "channel_id": channel_id,
                "oldest": str(int(start_utc.timestamp())),
                "latest": str(int(end_utc.timestamp())),
                "limit": 100,
                "response_format": "detailed",
            },
        )
        return _mcp_text(result) or f"(no Slack messages in window for {label})"
    except Exception as exc:
        logging.exception("Slack MCP fetch failed for %s", label)
        return f"Error reading Slack {label}: {exc}"


async def _fetch_slack_oncall(
    session, channel_id: str, start_utc: datetime, end_utc: datetime
) -> str:
    return await _fetch_slack_channel(session, channel_id, start_utc, end_utc, "#oncall_escalation")


def _slack_messages_text(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(data, dict):
        return str(data.get("messages") or "")
    return text


def _classify_prod_dep_postcheck(thread_text: str) -> str:
    """Map release thread content to postcheck status for the shift table."""
    has_pre = bool(_PRE_CHECK_RE.search(thread_text))
    has_post = bool(_POST_CHECK_RE.search(thread_text))
    in_deployment = bool(_IN_DEPLOYMENT_RE.search(thread_text))
    if has_post:
        return "Done"
    if has_pre and in_deployment:
        return "done/in deployment"
    if has_pre or in_deployment:
        return "done/pending post"
    return "pending postcheck"


def _latest_thread_activity(thread_text: str) -> str:
    replies = re.findall(
        r"--- Reply \d+ of \d+ ---\nFrom:.*?\nTime:.*?\nMessage TS:.*?\n(.*?)(?=\n--- Reply |\Z)",
        thread_text,
        re.S,
    )
    if not replies:
        return ""
    snippet = re.sub(r"\s+", " ", replies[-1]).strip()
    return snippet[:160]


def _parse_release_thread(message_ts: str, thread_text: str) -> dict[str, Any]:
    grm_ids = _extract_grm_ids(thread_text)
    release_line = ""
    match = re.search(r"\*Release Details\*\n(.+?)(?:\n\n|$)", thread_text, re.S)
    if match:
        release_line = match.group(1).strip().split("\n")[0][:200]
    status = _classify_prod_dep_postcheck(thread_text)
    return {
        "message_ts": message_ts,
        "grm_id": grm_ids[0] if grm_ids else "",
        "release_summary": release_line,
        "postcheck_status": status,
        "has_pre_check": bool(_PRE_CHECK_RE.search(thread_text)),
        "has_post_check": bool(_POST_CHECK_RE.search(thread_text)),
        "reply_count": len(re.findall(r"--- Reply \d+ of \d+ ---", thread_text)),
        "latest_activity": _latest_thread_activity(thread_text),
    }


async def _fetch_slack_prod_dep(
    session,
    channel_id: str,
    start_utc: datetime,
    end_utc: datetime,
) -> str:
    """Read #prod-dep-update and expand Production Release Checklist threads (postchecks live in replies)."""
    channel_raw = await _fetch_slack_channel(
        session, channel_id, start_utc, end_utc, "#prod-dep-update"
    )
    channel_text = _slack_messages_text(channel_raw)
    thread_limit = max(1, min(int(os.getenv("SHIFT_PROD_DEP_THREAD_LIMIT", "10")), 15))
    message_ts_list = [
        match.group(1) for match in _CHECKLIST_BLOCK_RE.finditer(channel_text)
    ][:thread_limit]

    release_threads: list[dict[str, Any]] = []
    errors: list[str] = []
    for message_ts in message_ts_list:
        try:
            result = await session.call_tool(
                SLACK_READ_THREAD,
                {
                    "channel_id": channel_id,
                    "message_ts": message_ts,
                    "limit": 50,
                    "response_format": "detailed",
                },
            )
            thread_raw = _mcp_text(result) or ""
            if thread_raw.lower().startswith("initialization_failed"):
                errors.append(thread_raw[:200])
                continue
            thread_text = _slack_messages_text(thread_raw)
            if thread_text:
                release_threads.append(_parse_release_thread(message_ts, thread_text))
        except Exception as exc:
            logging.warning("Slack thread fetch failed for %s: %s", message_ts, exc)
            errors.append(f"{message_ts}: {exc}")

    return json.dumps(
        {
            "channel_excerpt": channel_text[:10000],
            "release_threads": release_threads,
            "checklist_threads_fetched": len(release_threads),
            "errors": errors[:5],
        },
        indent=2,
    )


def _datadog_monitor_name(monitor: dict[str, Any]) -> str:
    return str(monitor.get("name") or monitor.get("message") or "").strip()


def _datadog_monitor_searchable_text(monitor: dict[str, Any]) -> str:
    parts = [monitor.get("name"), monitor.get("query"), monitor.get("message")]
    return " ".join(str(p) for p in parts if p)


def _is_golden_env_datadog_monitor(monitor: dict[str, Any]) -> bool:
    """True when the monitor targets goldendev or goldenqa (non-prod Golden envs)."""
    text = _datadog_monitor_searchable_text(monitor)
    if _DD_GOLDEN_ENV_TAG.search(text):
        return True
    name = _datadog_monitor_name(monitor)
    if not name:
        return False
    if _DD_GOLDEN_NAME_PREFIX.search(name):
        return True
    return bool(_DD_GOLDEN_NAME_LABEL.search(name))


def _datadog_monitor_priority(monitor: dict[str, Any]) -> str | None:
    """Map monitor title to P1/P2/P3 from SEV or [P#] tags; None when no priority."""
    text = f"{_datadog_monitor_name(monitor)} {monitor.get('message') or ''}"
    for pattern, priority in _DD_PRIORITY_PATTERNS:
        if pattern.search(text):
            return priority
    return None


def _is_excluded_datadog_monitor(monitor: dict[str, Any]) -> bool:
    """Exclude muted, -a/-b canaries, and goldendev/goldenqa monitors."""
    if monitor.get("muted") is True:
        return True
    status = str(monitor.get("status") or "").strip().lower()
    if status in ("muted", "silenced"):
        return True
    if _is_golden_env_datadog_monitor(monitor):
        return True
    name = _datadog_monitor_name(monitor)
    return bool(name and _DD_MONITOR_SUFFIX_EXCLUDE.search(name))


def _parse_datadog_monitors_chunk(text: str) -> list[dict[str, Any]]:
    match = _DD_MONITOR_JSON_RE.search(text or "")
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    return [m for m in data if isinstance(m, dict)]


def _normalize_dd_match_key(name: str) -> str:
    key = (name or "").lower()
    key = re.sub(r"\s+", " ", key).strip()
    return key


def _monitor_name_from_event_title(title: str) -> str:
    return _DD_EVENT_TITLE_PREFIX_RE.sub("", (title or "").strip()).strip()


def _parse_dd_event_last_triggered(message: str) -> datetime | None:
    match = _DD_LAST_TRIGGERED_RE.search(message or "")
    if not match:
        return None
    raw = match.group(1).strip()
    for fmt, tz_hours in (
        ("%a %b %d %Y %H:%M:%S PDT", -7),
        ("%a %b %d %Y %H:%M:%S PST", -8),
    ):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=timezone(timedelta(hours=tz_hours)))
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _monitor_matches_recent_trigger(monitor: dict[str, Any], recent_names: set[str]) -> bool:
    if not recent_names:
        return False
    name = _normalize_dd_match_key(_datadog_monitor_name(monitor))
    if not name:
        return False
    if name in recent_names:
        return True
    for recent in recent_names:
        if name in recent or recent in name:
            return True
        if name[:40] and name[:40] == recent[:40]:
            return True
    return False


def _parse_datadog_events_chunk(text: str) -> list[dict[str, Any]]:
    match = _DD_EVENT_JSON_RE.search(text or "")
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    return [e for e in data if isinstance(e, dict)]


async def _fetch_recent_dd_triggered_names(session, hours: int = 24) -> set[str]:
    """Monitor names whose last trigger time is within the recent window."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=max(1, hours))
    recent_names: set[str] = set()
    start_at = 0
    event_query = (
        "source:alert status:error "
        "NOT env:goldendev NOT env:goldenqa"
    )
    try:
        for _ in range(4):
            result = await session.call_tool(
                DD_SEARCH_EVENTS,
                {
                    "query": event_query,
                    "from": int(start.timestamp()),
                    "to": int(now.timestamp()),
                    "max_tokens": 10000,
                    "start_at": start_at,
                    "telemetry": {
                        "intent": "Find Datadog alerts triggered in the last 24h for shift handoff",
                    },
                },
            )
            text = _mcp_text(result) or ""
            events = _parse_datadog_events_chunk(text)
            if not events:
                break
            for event in events:
                triggered = _parse_dd_event_last_triggered(str(event.get("message") or ""))
                if triggered is None:
                    continue
                if triggered.astimezone(timezone.utc) < start:
                    continue
                name = _normalize_dd_match_key(_monitor_name_from_event_title(str(event.get("title") or "")))
                if name:
                    recent_names.add(name)
            if "<is_truncated>true</is_truncated>" not in text.lower():
                break
            start_at += 15
    except Exception:
        logging.exception("Datadog events fetch for recent alerts failed")
    return recent_names


def _filter_datadog_monitors(
    monitors: list[dict[str, Any]],
    recent_names: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    kept: list[dict[str, Any]] = []
    stats = {
        "total": len(monitors),
        "excluded_muted": 0,
        "excluded_suffix_ab": 0,
        "excluded_golden_env": 0,
        "excluded_no_priority": 0,
        "excluded_stale": 0,
        "kept": 0,
    }
    for monitor in monitors:
        if monitor.get("muted") is True or str(monitor.get("status") or "").lower() in ("muted", "silenced"):
            stats["excluded_muted"] += 1
            continue
        if _is_golden_env_datadog_monitor(monitor):
            stats["excluded_golden_env"] += 1
            continue
        name = _datadog_monitor_name(monitor)
        if name and _DD_MONITOR_SUFFIX_EXCLUDE.search(name):
            stats["excluded_suffix_ab"] += 1
            continue
        if not _datadog_monitor_priority(monitor):
            stats["excluded_no_priority"] += 1
            continue
        if recent_names is not None and not _monitor_matches_recent_trigger(monitor, recent_names):
            stats["excluded_stale"] += 1
            continue
        kept.append(monitor)
    stats["kept"] = len(kept)
    return kept, stats


def _format_datadog_alerts_payload(
    monitors: list[dict[str, Any]],
    stats: dict[str, int],
    *,
    recent_hours: int,
) -> str:
    slim: list[dict[str, Any]] = []
    for m in monitors:
        slim.append(
            {
                "id": m.get("id"),
                "name": m.get("name"),
                "priority": _datadog_monitor_priority(m),
                "status": m.get("status"),
                "type": m.get("type"),
                "created_at": m.get("created_at"),
            }
        )
    return json.dumps(
        {
            "filters_applied": [
                "status:alert",
                "muted:false",
                "exclude monitor names ending with -a or -b",
                "exclude goldendev and goldenqa monitors",
                "exclude monitors without SEV-1/2/3 or P1/P2/P3 priority in title",
                f"only monitors last triggered within {recent_hours}h",
            ],
            "recent_hours": recent_hours,
            "stats": stats,
            "monitors": slim,
        },
        indent=2,
    )


async def _fetch_datadog_active_alerts(session) -> str:
    """Datadog alerts in Alert state, prioritized, prod, and triggered within recent window."""
    recent_hours = max(1, min(int(os.getenv("SHIFT_DD_RECENT_HOURS", "24")), 72))
    all_monitors: list[dict[str, Any]] = []
    start_at = 0
    try:
        recent_names = await _fetch_recent_dd_triggered_names(session, recent_hours)
        for _ in range(3):
            result = await session.call_tool(
                DD_SEARCH_MONITORS,
                {
                    "query": "status:alert muted:false",
                    "sort": "-status",
                    "max_tokens": 10000,
                    "start_at": start_at,
                    "telemetry": {
                        "intent": "List active non-muted Datadog alerts for GOC shift handoff",
                    },
                },
            )
            text = _mcp_text(result) or ""
            if not text.strip():
                break
            all_monitors.extend(_parse_datadog_monitors_chunk(text))
            if "<is_truncated>true</is_truncated>" not in text.lower():
                break
            start_at += 35

        filtered, stats = _filter_datadog_monitors(all_monitors, recent_names)
        if not filtered and not all_monitors:
            return '{"monitors":[],"stats":{"kept":0}}'
        return _format_datadog_alerts_payload(filtered, stats, recent_hours=recent_hours)
    except Exception as exc:
        logging.exception("Datadog MCP fetch failed")
        return json.dumps({"error": str(exc)})


def _extract_grm_ids(*blobs: str) -> list[str]:
    """Collect unique GRM-#### ids from shift source payloads."""
    text = "\n".join(b for b in blobs if b)
    seen: set[str] = set()
    ids: list[str] = []

    def add(number: str) -> None:
        grm_id = f"GRM-{number}"
        if grm_id not in seen:
            seen.add(grm_id)
            ids.append(grm_id)

    for match in _GRM_ID_RE.finditer(text):
        add(match.group(1))
    for match in re.finditer(r"\bGRM\s*-?\s*(\d+)\s*,\s*(\d+)\b", text, re.I):
        add(match.group(1))
        add(match.group(2))
    return ids


def _extract_issue_search_terms(*blobs: str) -> list[str]:
    """Pull GRM/INC/service tokens from issue payloads for Outlook search."""
    text = "\n".join(b for b in blobs if b)
    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        t = (term or "").strip()
        if not t or len(t) < 3 or t.lower() in seen:
            return
        seen.add(t.lower())
        terms.append(t)

    for grm_id in _extract_grm_ids(text):
        add(grm_id)
    for match in re.finditer(r"\bINC\d+\b", text, re.I):
        add(match.group(0).upper())
    for match in re.finditer(r'"incident_number"\s*:\s*(\d+)', text):
        add(f"#{match.group(1)}")
        add(match.group(1))
    for match in re.finditer(r"\bbackend-[a-z0-9][a-z0-9-]{2,}\b", text, re.I):
        add(match.group(0).lower())
    for match in re.finditer(r"\bSEV-[123]\b", text, re.I):
        add(match.group(0).upper())

    for generic in ("pagerduty", "servicenow", "oncall", "escalation", "postcheck", "deployment", "release"):
        add(generic)

    return terms[:32]


def _outlook_related_topic(subject: str, preview: str = "") -> str:
    blob = f"{subject} {preview}"
    grms = _extract_grm_ids(blob)
    if grms:
        return grms[0]
    inc = re.search(r"\bINC\d+\b", blob, re.I)
    if inc:
        return inc.group(0).upper()
    pd = re.search(r"#?(\d{5,6})\b", blob)
    if pd and "pagerduty" in blob.lower():
        return f"#{pd.group(1)}"
    return "Email thread"


def _parse_outlook_emails(text: str) -> list[dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        return []
    if raw.lower().startswith("failed to search emails"):
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    emails = data.get("emails") if isinstance(data, dict) else None
    return [e for e in emails if isinstance(e, dict)] if isinstance(emails, list) else []


def _outlook_mcp_error(text: str) -> str:
    raw = (text or "").strip()
    if raw.lower().startswith("failed to search emails"):
        return raw
    return ""


def _slim_outlook_email(email: dict[str, Any]) -> dict[str, Any]:
    preview = str(email.get("bodyPreview") or "").replace("\r", " ").replace("\n", " ")
    preview = re.sub(r"\s+", " ", preview).strip()[:240]
    subject = str(email.get("subject") or "").strip()
    return {
        "subject": subject,
        "from": email.get("from"),
        "fromName": email.get("fromName"),
        "receivedDateTime": email.get("receivedDateTime"),
        "importance": email.get("importance"),
        "isRead": email.get("isRead"),
        "bodyPreview": preview,
        "related_topic": _outlook_related_topic(subject, preview),
    }


def _email_in_window(email: dict[str, Any], start_utc: datetime, end_utc: datetime) -> bool:
    raw = email.get("receivedDateTime")
    if not raw:
        return True
    try:
        received = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
        return start_utc <= received.astimezone(timezone.utc) <= end_utc
    except (TypeError, ValueError):
        return True


def _is_relevant_outlook_email(email: dict[str, Any]) -> bool:
    subject = str(email.get("subject") or "")
    preview = str(email.get("bodyPreview") or "")
    if _OUTLOOK_NOISE_RE.search(f"{subject} {preview}"):
        return False
    blob = f"{subject} {preview}".lower()
    if any(
        token in blob
        for token in (
            "grm-", "inc", "pagerduty", "servicenow", "deployment", "release",
            "postcheck", "escalation", "incident", "on call", "oncall",
        )
    ):
        return True
    return bool(_extract_grm_ids(subject, preview) or re.search(r"\bINC\d+\b", subject, re.I))


def _outlook_time_local(email: dict[str, Any], tz_name: str) -> str:
    raw = email.get("receivedDateTime")
    if not raw:
        return "—"
    try:
        received = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
        return received.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return "—"


def _outlook_date_range(window: ShiftWindow) -> tuple[str, str]:
    tz = ZoneInfo(window.tz_name)
    start_local = window.start_utc.astimezone(tz).date()
    end_local = window.end_utc.astimezone(tz).date()
    return start_local.isoformat(), end_local.isoformat()


def _build_outlook_queries(window: ShiftWindow, terms: list[str]) -> list[str]:
    start_d, end_d = _outlook_date_range(window)
    date_clause = f"received:{start_d}..{end_d}"
    queries = [
        f'{date_clause} AND (pagerduty OR servicenow OR incident OR escalation OR "on call")',
        f"{date_clause} AND (deployment OR release OR GRM OR postcheck)",
    ]
    issue_terms = [
        t for t in terms
        if re.match(r"^(GRM-|INC|#|\d{5,})", t, re.I) or t.lower().startswith("backend-")
    ][:12]
    if issue_terms:
        or_bits = []
        for term in issue_terms:
            if term.startswith("#"):
                or_bits.append(term)
            elif term.isdigit():
                or_bits.append(term)
            elif "-" in term:
                or_bits.append(f'"{term}"')
            else:
                or_bits.append(term)
        queries.append(f"{date_clause} AND ({' OR '.join(or_bits)})")
    grm_terms = [t for t in terms if t.upper().startswith("GRM-")][:6]
    for grm in grm_terms:
        queries.append(f'{date_clause} AND "{grm}"')
    return queries[:6]


async def _fetch_related_outlook_emails(
    session,
    window: ShiftWindow,
    issue_blobs: list[str],
) -> str:
    """Search Outlook for emails in the shift window related to collected issues."""
    terms = _extract_issue_search_terms(*issue_blobs)
    queries = _build_outlook_queries(window, terms)
    mailbox = (os.getenv("SHIFT_OUTLOOK_MAILBOX") or DEFAULT_SHIFT_OUTLOOK_MAILBOX).strip()
    limit = max(5, min(int(os.getenv("SHIFT_OUTLOOK_EMAIL_LIMIT", "15")), 50))

    collected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    errors: list[str] = []

    try:
        for query in queries:
            args: dict[str, Any] = {
                "query": query,
                "limit": limit,
            }
            if mailbox:
                args["mailbox_email"] = mailbox
            result = await session.call_tool(OUTLOOK_SEARCH_EMAIL, args)
            raw = _mcp_text(result) or ""
            err = _outlook_mcp_error(raw)
            if err:
                errors.append(err)
                continue
            for email in _parse_outlook_emails(raw):
                eid = str(email.get("id") or "")
                if eid and eid in seen_ids:
                    continue
                if not _email_in_window(email, window.start_utc, window.end_utc):
                    continue
                slim = _slim_outlook_email(email)
                if not _is_relevant_outlook_email(slim):
                    continue
                if eid:
                    seen_ids.add(eid)
                collected.append(slim)

        return json.dumps(
            {
                "mailbox": mailbox or "(authenticated user)",
                "search_terms": terms[:20],
                "queries_run": queries,
                "count": len(collected),
                "emails": collected[:40],
                "errors": errors[:3],
            },
            indent=2,
        )
    except Exception as exc:
        logging.exception("Outlook email search failed")
        return json.dumps({"error": str(exc), "search_terms": terms[:20], "errors": errors[:3]})


def _slim_jira_issue(issue: dict[str, Any]) -> dict[str, Any]:
    fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
    status_obj = fields.get("status")
    status = status_obj.get("name") if isinstance(status_obj, dict) else str(status_obj or "")
    assignee_obj = fields.get("assignee")
    assignee = (
        assignee_obj.get("displayName")
        if isinstance(assignee_obj, dict)
        else str(assignee_obj or "")
    )
    priority_obj = fields.get("priority")
    priority = (
        priority_obj.get("name")
        if isinstance(priority_obj, dict)
        else str(priority_obj or "")
    )
    return {
        "key": issue.get("key"),
        "summary": fields.get("summary"),
        "status": status,
        "priority": priority,
        "assignee": assignee,
        "updated": fields.get("updated"),
        "created": fields.get("created"),
    }


async def _fetch_jira_for_grm_ids(session, grm_ids: list[str]) -> str:
    """Look up Jira GRM tickets for ids found across shift sources."""
    limit = max(1, min(int(os.getenv("SHIFT_JIRA_GRM_LIMIT", "25")), 50))
    grm_ids = grm_ids[:limit]
    if not grm_ids:
        return json.dumps({"count": 0, "issues": [], "requested_keys": []}, indent=2)

    try:
        payload = await fetch_jira_issues_by_keys(session, grm_ids, max_results=limit)
        issues = [
            _slim_jira_issue(issue)
            for issue in (payload.get("issues") or [])
            if isinstance(issue, dict)
        ]
        requested = payload.get("requested_keys") or grm_ids
        found = {str(i.get("key") or "").upper() for i in issues}
        missing = [k for k in requested if k.upper() not in found]
        return json.dumps(
            {
                "requested_keys": requested,
                "found_keys": sorted(found - {""}),
                "missing_keys": missing,
                "jql": payload.get("jql"),
                "count": len(issues),
                "issues": issues,
                "error": payload.get("error"),
            },
            indent=2,
        )
    except Exception as exc:
        logging.exception("Jira GRM lookup failed")
        return json.dumps({"error": str(exc), "requested_keys": grm_ids})


def _fetch_grm_deployments_in_window(start_utc: datetime, end_utc: datetime) -> str:
    try:
        response = requests.get(
            _internal_deployments_api_url(),
            timeout=_internal_deployments_http_timeout(),
        )
        if response.status_code != 200:
            return json.dumps({"error": f"GRM API HTTP {response.status_code}"})
        deployments = response.json().get("deployments", []) or []
        rows: list[dict[str, Any]] = []
        for deploy in deployments:
            try:
                ts = datetime.fromisoformat(deploy.get("timestamp", ""))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if start_utc <= ts.astimezone(timezone.utc) <= end_utc:
                    rows.append(
                        {
                            "timestamp": deploy.get("timestamp"),
                            "date": deploy.get("date"),
                            "time": deploy.get("time"),
                            "service": deploy.get("service"),
                        }
                    )
            except Exception:
                continue
        rows.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return json.dumps({"count": len(rows), "deployments": rows}, indent=2)
    except requests.RequestException:
        hours = max(1, int((end_utc - start_utc).total_seconds() / 3600) + 1)
        try:
            from tools.deployments_calendar import get_grm_deployments

            html = get_grm_deployments("", timerange_hours=-hours)
            return json.dumps(
                {
                    "fallback": "get_grm_deployments",
                    "approx_hours": hours,
                    "html_excerpt": _truncate(html, 12000),
                },
                indent=2,
            )
        except Exception as inner:
            logging.exception("GRM fallback fetch failed")
            return json.dumps({"error": str(inner)})
    except Exception as exc:
        logging.exception("GRM window fetch failed")
        return json.dumps({"error": str(exc)})


def _build_bedrock_prompt(
    window: ShiftWindow,
    pagerduty_raw: str,
    slack_oncall_raw: str,
    slack_prod_dep_raw: str,
    datadog_raw: str,
    grm_raw: str,
    jira_raw: str,
    outlook_raw: str,
) -> str:
    mode_title = f"{SHIFT_LABELS.get(window.mode, window.mode)} Handoff"
    return f"""You are GocView Shift Reporter for Arlo GOC on-call engineers.

Return ONLY valid JSON (no markdown, no HTML, no commentary).

Report type: {mode_title}
Time window: {window.label}
UTC window: {_iso_z(window.start_utc)} → {_iso_z(window.end_utc)}
Display timezone: {window.tz_name}

DATA (do not invent rows; dedupe same incident across sources when obvious):

=== PagerDuty (auto-resolved = count only; others listed individually) ===
{_truncate(pagerduty_raw, 12000)}

=== Datadog active alerts (prod, P1–P3, last triggered within 24h only) ===
{_truncate(datadog_raw, 18000)}

=== Slack #oncall_escalation ===
{_truncate(slack_oncall_raw, 12000)}

=== Slack #prod-dep-update — release threads with pre/post checklists ===
{_truncate(slack_prod_dep_raw, 18000)}

Note: release_threads[] is parsed from Production Release Checklist thread replies (Post Check List / Pre Check List).

=== GRM deployments (calendar window) ===
{_truncate(grm_raw, 6000)}

=== Jira GRM tickets (status for each GRM id found in sources) ===
{_truncate(jira_raw, 12000)}

=== Outlook emails tied to shift issues (GRM, INC, PagerDuty, deployment, escalation) ===
{_truncate(outlook_raw, 12000)}

JSON schema:
{{
  "summary": "One sentence shift overview (max 200 chars)",
  "rows": [
    {{
      "source": "PagerDuty | Datadog | Slack Oncall | Slack Prod Dep | GRM | Jira | Outlook",
      "time_local": "YYYY-MM-DD HH:MM {window.tz_name} or 'current' for active DD alerts",
      "priority": "P1 | P2 | P3 | — (map SEV-1→P1, SEV-2→P2, SEV-3→P3 from monitor/incident title)",
      "service_or_topic": "service, monitor name, GRM-####, or deployment",
      "status": "triggered | acknowledged | resolved | alert | warn | Done | done/in deployment | done/pending post | pending postcheck | scheduled | info",
      "summary": "max 100 chars — what happened",
      "action_item": "max 120 chars — concrete next step for on-call",
      "owner": "team or TBD"
    }}
  ]
}}

Rules:
- One row per distinct item.
- PagerDuty: if auto_resolved_count > 0, add exactly ONE row (source = "PagerDuty", service_or_topic = "Auto-resolved", summary = "<N> PagerDuty incidents auto-resolved", status = "resolved", action_item = "—"). Do NOT list auto-resolved incidents individually. For incidents in the incidents array (triggered, acknowledged, or resolved with assignee), one row each.
- Datadog: one row per prioritized alert monitor listed (source = "Datadog", status = "alert"). Use the priority field from data (P1/P2/P3). Stale monitors (not triggered in the last 24h) are already excluded — do not add them.
- Slack Prod Dep: one row per item in release_threads[] (source = "Slack Prod Dep"). Use postcheck_status exactly:
  • done/in deployment — pre-check done and deployment in progress
  • done/pending post — pre-check or deployment done, post-check still pending
  • Done — post-check complete (use exactly "Done", not "postcheck done")
  • pending postcheck — release started, pre/post not complete
  Put grm_id in service_or_topic, release_summary in summary, latest_activity in action_item.
- GRM calendar: rows for deployments scheduled/completed in the shift window (source = "GRM", status = scheduled/completed/info). Calendar data alone does not describe ticket outcome — pair with Jira when available.
- Jira: one row per GRM ticket returned in the Jira section (source = "Jira"). Put the GRM key (e.g. GRM-3543) in service_or_topic. Use ticket status (Scheduled, In Progress, SO Sign Off, Done, Closed, etc.), summary, assignee, and updated time. action_item = next step for on-call based on current Jira status (not just calendar schedule).
- When the same GRM appears in Slack/GRM calendar and Jira, prefer one merged row with Jira status/details (source = "Jira" or "GRM" with Jira status in summary).
- Outlook: include emails from the Outlook section (source = "Outlook"). Rows are grouped server-side by sender name; related_topic in service_or_topic, subjects combined in summary.
- Jira / GRM: rows with the same GRM id or ticket name are grouped server-side into one row.
- Keep summaries and action items short and actionable.
- If no items for a source, omit rows for that source (do not fabricate).
- Merge duplicate PD+Slack references to the same incident into one row when possible.
- English only."""


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        raise ValueError("Bedrock did not return JSON for shift table")
    data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("Shift report JSON must be an object")
    return data


def _normalize_shift_status(status: str) -> str:
    """Display-friendly status labels for the shift table."""
    raw = (status or "").strip()
    if not raw:
        return raw
    lowered = raw.lower()
    if lowered in ("done", "completed"):
        return "Done"
    if (
        "postcheck done" in lowered
        or "portcheck done" in lowered
        or "in deployment/postcheck done" in lowered
        or "in deployment/portcheck done" in lowered
        or (lowered.startswith("in deployment") and lowered.endswith("done"))
    ):
        return "Done"
    if re.search(r"done\s*/\s*in\s+deployment", lowered):
        return "done/in deployment"
    if (
        "in deployment" in lowered
        and re.search(r"\bdone\b", lowered)
        and "pending" not in lowered
        and "postcheck done" not in lowered
    ):
        return "done/in deployment"
    return raw


def _normalize_rows(data: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    summary = str(data.get("summary") or "Shift handoff summary unavailable.").strip()
    rows_in = data.get("rows") or []
    rows: list[dict[str, str]] = []
    if isinstance(rows_in, list):
        for item in rows_in:
            if not isinstance(item, dict):
                continue
            row = {
                field: str(item.get(field) or "").strip()[:200]
                for field in SHIFT_TABLE_FIELDS
            }
            row["status"] = _normalize_shift_status(row.get("status", ""))
            if any(row.values()):
                rows.append(row)
    return summary, rows


def _outlook_owner_group_key(row: dict[str, str]) -> str | None:
    if (row.get("source") or "").strip().lower() != "outlook":
        return None
    owner = (row.get("owner") or "").strip()
    if not owner or owner == "—":
        return None
    return owner.lower()


def _ticket_group_key(row: dict[str, str]) -> str | None:
    source = (row.get("source") or "").strip().lower()
    if source not in ("jira", "grm", "slack prod dep"):
        return None
    topic = str(row.get("service_or_topic") or "")
    summary = str(row.get("summary") or "")
    grm_ids = _extract_grm_ids(topic, summary)
    if grm_ids:
        return grm_ids[0].upper()
    name = _normalize_dd_match_key(topic or summary)
    if len(name) < 8:
        return None
    return name[:80]


def _shift_row_group_key(row: dict[str, str]) -> tuple[str, str] | None:
    owner_key = _outlook_owner_group_key(row)
    if owner_key:
        return ("outlook", owner_key)
    ticket_key = _ticket_group_key(row)
    if ticket_key:
        return ("ticket", ticket_key)
    return None


def _priority_rank(priority: str) -> int:
    p = (priority or "").upper()
    if p == "P1":
        return 0
    if p == "P2":
        return 1
    if p == "P3":
        return 2
    return 9


def _merge_shift_row_group(rows: list[dict[str, str]]) -> dict[str, str]:
    if len(rows) == 1:
        return rows[0]
    base = dict(rows[0])
    source = (base.get("source") or "").strip()
    source_l = source.lower()
    all_sources = sorted({(r.get("source") or "").strip() for r in rows if (r.get("source") or "").strip()})
    if len(all_sources) > 1:
        base["source"] = " / ".join(all_sources)
        source_l = "ticket"

    priorities = sorted(
        {(r.get("priority") or "—") for r in rows},
        key=_priority_rank,
    )
    base["priority"] = priorities[0] if priorities else "—"

    times = [r.get("time_local") or "" for r in rows if (r.get("time_local") or "") not in ("", "—")]
    if len(times) == 1:
        base["time_local"] = times[0]
    elif times:
        base["time_local"] = f"{min(times)} – {max(times)}"[:200]

    statuses = {(r.get("status") or "").strip().lower() for r in rows if r.get("status")}
    if source_l == "outlook":
        if "unread" in statuses:
            base["status"] = "unread"
        elif statuses == {"read"}:
            base["status"] = "read"
        else:
            base["status"] = "mixed"
    else:
        base["status"] = " / ".join(sorted(statuses))[:200] if statuses else base.get("status", "")

    topics: list[str] = []
    seen_topics: set[str] = set()
    summaries: list[str] = []
    seen_summaries: set[str] = set()
    owners: list[str] = []
    seen_owners: set[str] = set()
    actions: list[str] = []
    seen_actions: set[str] = set()

    for row in rows:
        for grm in _extract_grm_ids(row.get("service_or_topic") or "", row.get("summary") or ""):
            key = grm.lower()
            if key not in seen_topics:
                seen_topics.add(key)
                topics.append(grm)
        topic = (row.get("service_or_topic") or "").strip()
        topic_key = topic.lower()
        if topic and topic_key not in seen_topics:
            seen_topics.add(topic_key)
            topics.append(topic[:120])

        summary = (row.get("summary") or "").strip()
        if summary and summary.lower() not in seen_summaries:
            seen_summaries.add(summary.lower())
            summaries.append(summary)

        owner = (row.get("owner") or "").strip()
        if owner and owner != "—" and owner.lower() not in seen_owners:
            seen_owners.add(owner.lower())
            owners.append(owner)

        action = (row.get("action_item") or "").strip()
        if action and action != "—" and action.lower() not in seen_actions:
            seen_actions.add(action.lower())
            actions.append(action)

    if source_l == "outlook":
        base["owner"] = owners[0] if owners else base.get("owner", "—")
        if topics:
            base["service_or_topic"] = " · ".join(topics[:4])[:200]
        summary_bits = summaries[:3]
        joined = " · ".join(summary_bits)
        if len(summaries) > 3:
            joined += f" (+{len(summaries) - 3} more)"
        base["summary"] = f"{len(rows)} emails — {joined}"[:200]
        base["action_item"] = actions[0] if actions else "Review grouped email threads"
    else:
        base["service_or_topic"] = topics[0] if topics else (base.get("service_or_topic") or "—")
        if len(summaries) == 1:
            base["summary"] = summaries[0][:200]
        elif summaries:
            joined = " · ".join(summaries[:2])
            if len(summaries) > 2:
                joined += f" (+{len(summaries) - 2} more)"
            base["summary"] = f"{len(rows)} updates — {joined}"[:200]
        else:
            base["summary"] = f"{len(rows)} related updates"[:200]
        base["owner"] = owners[0] if owners else base.get("owner", "—")
        base["action_item"] = actions[0] if actions else base.get("action_item", "—")

    return base


def _consolidate_shift_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Group Outlook by sender; Jira/GRM by GRM id or same ticket name."""
    group_key_for: list[tuple[str, str] | None] = [_shift_row_group_key(row) for row in rows]
    groups: dict[tuple[str, str], list[int]] = {}
    for idx, key in enumerate(group_key_for):
        if key:
            groups.setdefault(key, []).append(idx)

    skip: set[int] = set()
    consolidated: list[dict[str, str]] = []
    for idx, row in enumerate(rows):
        if idx in skip:
            continue
        key = group_key_for[idx]
        indices = groups.get(key or (), [])
        if key and len(indices) > 1:
            consolidated.append(_merge_shift_row_group([rows[i] for i in indices]))
            skip.update(indices)
        else:
            consolidated.append(row)
    return consolidated


def _ensure_pagerduty_auto_resolved_summary(
    rows: list[dict[str, str]], pagerduty_raw: str
) -> list[dict[str, str]]:
    """Guarantee a single count row when auto-resolved PagerDuty incidents exist."""
    try:
        data = json.loads(pagerduty_raw)
    except json.JSONDecodeError:
        return rows
    count = int(data.get("auto_resolved_count") or 0)
    if count <= 0:
        return rows

    for row in rows:
        blob = " ".join(
            (row.get("source") or "", row.get("service_or_topic") or "", row.get("summary") or "")
        ).lower()
        if row.get("source", "").strip().lower() == "pagerduty" and "auto-resolv" in blob:
            return rows

    rows.append(
        {
            "source": "PagerDuty",
            "time_local": "—",
            "priority": "—",
            "service_or_topic": "Auto-resolved",
            "status": "resolved",
            "summary": f"{count} PagerDuty incidents auto-resolved",
            "action_item": "—",
            "owner": "—",
        }
    )
    return rows


def _ensure_outlook_email_rows(
    rows: list[dict[str, str]], outlook_raw: str, window: ShiftWindow
) -> list[dict[str, str]]:
    """Add one table row per relevant Outlook email returned by search."""
    try:
        data = json.loads(outlook_raw)
    except json.JSONDecodeError:
        return rows
    emails = data.get("emails") if isinstance(data, dict) else None
    if not isinstance(emails, list) or not emails:
        return rows

    existing_subjects = {
        (row.get("summary") or "").strip().lower()
        for row in rows
        if (row.get("source") or "").strip().lower() == "outlook"
    }

    for email in emails:
        if not isinstance(email, dict) or not _is_relevant_outlook_email(email):
            continue
        subject = str(email.get("subject") or "(no subject)").strip()[:200]
        if subject.lower() in existing_subjects:
            continue
        owner = str(email.get("fromName") or email.get("from") or "—").strip()[:200]
        importance = str(email.get("importance") or "").lower()
        priority = "P2" if importance == "high" else "—"
        is_read = email.get("isRead")
        status = "read" if is_read is True else "unread"
        related = str(email.get("related_topic") or _outlook_related_topic(subject, email.get("bodyPreview") or ""))
        rows.append(
            {
                "source": "Outlook",
                "time_local": _outlook_time_local(email, window.tz_name),
                "priority": priority,
                "service_or_topic": related[:200],
                "status": status,
                "summary": subject,
                "action_item": "Review email thread and tie to open GRM/incident",
                "owner": owner,
            }
        )
        existing_subjects.add(subject.lower())
    return rows


def _ensure_prod_dep_postcheck_rows(
    rows: list[dict[str, str]], slack_prod_dep_raw: str
) -> list[dict[str, str]]:
    """Inject rows from parsed #prod-dep-update release checklist threads."""
    try:
        data = json.loads(slack_prod_dep_raw)
    except json.JSONDecodeError:
        return rows
    threads = data.get("release_threads") if isinstance(data, dict) else None
    if not isinstance(threads, list):
        return rows

    existing: set[str] = set()
    for row in rows:
        if (row.get("source") or "").strip().lower() != "slack prod dep":
            continue
        for grm in _extract_grm_ids(row.get("service_or_topic") or "", row.get("summary") or ""):
            existing.add(grm)
        status = (row.get("status") or "").strip().lower()
        if status in ("done/pending post", "pending postcheck") and row.get("service_or_topic"):
            existing.add((row.get("service_or_topic") or "").strip().lower())

    for thread in threads:
        if not isinstance(thread, dict):
            continue
        grm_id = str(thread.get("grm_id") or "").strip()
        if grm_id and grm_id in existing:
            continue
        status = _normalize_shift_status(str(thread.get("postcheck_status") or "pending postcheck"))
        summary = str(thread.get("release_summary") or grm_id or "Production release").strip()[:200]
        latest = str(thread.get("latest_activity") or "").strip()[:200]
        rows.append(
            {
                "source": "Slack Prod Dep",
                "time_local": "—",
                "priority": "—",
                "service_or_topic": grm_id or summary[:80],
                "status": status,
                "summary": summary,
                "action_item": latest or "Monitor release thread until post-check sign-off",
                "owner": "Release checklist",
            }
        )
        if grm_id:
            existing.add(grm_id)
    return rows


def _rows_to_csv(summary: str, window: ShiftWindow, rows: list[dict[str, str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["GocView Shift Report"])
    writer.writerow(["Window", window.label])
    writer.writerow(["Summary", summary])
    writer.writerow([])
    writer.writerow(list(SHIFT_TABLE_HEADERS))
    for row in rows:
        writer.writerow([row.get(f, "") for f in SHIFT_TABLE_FIELDS])
    if not rows:
        writer.writerow(["—", "—", "—", "No items in window", "—", "—", "—", "—"])
    return buf.getvalue()


def _rows_to_plain_text(summary: str, window: ShiftWindow, rows: list[dict[str, str]]) -> str:
    lines = [window.label, f"Summary: {summary}", ""]
    lines.append(" | ".join(SHIFT_TABLE_HEADERS))
    lines.append(" | ".join(["---"] * len(SHIFT_TABLE_HEADERS)))
    for row in rows:
        lines.append(" | ".join(row.get(f, "") for f in SHIFT_TABLE_FIELDS))
    if not rows:
        lines.append("No items in the selected time window.")
    return "\n".join(lines)


def _priority_style(priority: str) -> str:
    p = (priority or "").upper()
    if p == "P1":
        return "background:#fee2e2;color:#991b1b;font-weight:700;"
    if p == "P2":
        return "background:#ffedd5;color:#9a3412;font-weight:700;"
    if p == "P3":
        return "background:#fef9c3;color:#854d0e;"
    return ""


def _status_style(status: str) -> str:
    s = (status or "").lower()
    if s in ("triggered", "open", "alert"):
        return "color:#dc2626;font-weight:700;"
    if s in ("acknowledged", "warn"):
        return "color:#d97706;font-weight:700;"
    if s in ("resolved", "closed", "read", "done", "completed"):
        return "color:#16a34a;font-weight:700;"
    if re.search(r"done\s*/\s*in\s+deployment", s) or (
        "in deployment" in s
        and "done" in s
        and "pending" not in s
        and "postcheck done" not in s
    ):
        return "color:#16a34a;font-weight:700;"
    if s in ("unread",):
        return "color:#2563eb;font-weight:700;"
    if "done/pending post" in s:
        return "color:#d97706;font-weight:700;"
    if "postcheck" in s or "pending" in s:
        return "color:#7c3aed;font-weight:700;"
    return ""


def _rows_to_html(summary: str, window: ShiftWindow, rows: list[dict[str, str]]) -> str:
    head_cells = "".join(
        f'<th style="padding:8px 10px;border:1px solid #cbd5e1;background:#217346;color:#fff;'
        f'font-size:12px;text-align:left;white-space:nowrap;">{html_module.escape(h)}</th>'
        for h in SHIFT_TABLE_HEADERS
    )
    body_rows: list[str] = []
    for idx, row in enumerate(rows):
        bg = "#f8fafc" if idx % 2 == 0 else "#ffffff"
        cells = []
        for field in SHIFT_TABLE_FIELDS:
            val = html_module.escape(row.get(field, ""))
            style = f"padding:7px 10px;border:1px solid #e2e8f0;font-size:12px;vertical-align:top;background:{bg};"
            if field == "priority":
                style += _priority_style(row.get(field, ""))
            elif field == "status":
                style += _status_style(row.get(field, ""))
            elif field == "action_item":
                style += "font-weight:600;color:#1e40af;"
            cells.append(f'<td style="{style}">{val or "—"}</td>')
        body_rows.append(f"<tr>{''.join(cells)}</tr>")

    if not body_rows:
        body_rows.append(
            '<tr><td colspan="8" style="padding:12px;border:1px solid #e2e8f0;text-align:center;'
            'color:#64748b;font-size:13px;">No items in the selected time window.</td></tr>'
        )

    return f"""<div style="font-family:Segoe UI,Arial,sans-serif;max-width:100%;overflow-x:auto;">
  <div style="margin-bottom:10px;padding:10px 12px;background:#f1f5f9;border:1px solid #cbd5e1;border-radius:6px;">
    <div style="font-size:13px;font-weight:700;color:#0f172a;">{html_module.escape(window.label)}</div>
    <div style="font-size:12px;color:#475569;margin-top:4px;">{html_module.escape(summary)}</div>
    <div style="font-size:11px;color:#64748b;margin-top:6px;">{len(rows)} row(s) · Excel-style handoff table</div>
  </div>
  <table style="width:100%;border-collapse:collapse;min-width:960px;box-shadow:0 1px 3px rgba(0,0,0,.08);">
    <thead><tr>{head_cells}</tr></thead>
    <tbody>{''.join(body_rows)}</tbody>
  </table>
</div>"""


async def _collect_shift_data(window: ShiftWindow) -> dict[str, Any]:
    if not get_mcp_api_key():
        raise RuntimeError(
            "MINTMCP_API_KEY is not configured — required for PagerDuty, Datadog, and Slack shift reports."
        )

    grm_raw = _fetch_grm_deployments_in_window(window.start_utc, window.end_utc)

    async with open_mcp_session() as session:
        oncall_id, prod_dep_id = await asyncio.gather(
            _resolve_oncall_channel_id(session),
            _resolve_prod_dep_channel_id(session),
        )

        pagerduty_raw, datadog_raw = await asyncio.gather(
            _fetch_pagerduty(session, window.start_utc, window.end_utc),
            _fetch_datadog_active_alerts(session),
        )

        slack_oncall_raw = (
            await _fetch_slack_oncall(session, oncall_id, window.start_utc, window.end_utc)
            if oncall_id
            else "Slack channel #oncall_escalation not resolved."
        )
        slack_prod_dep_raw = (
            await _fetch_slack_prod_dep(session, prod_dep_id, window.start_utc, window.end_utc)
            if prod_dep_id
            else "Slack channel #prod-dep-update not resolved."
        )

        issue_blobs = [
            pagerduty_raw,
            datadog_raw,
            slack_oncall_raw,
            slack_prod_dep_raw,
            grm_raw,
        ]
        grm_ids = _extract_grm_ids(*issue_blobs)
        jira_raw = await _fetch_jira_for_grm_ids(session, grm_ids)
        outlook_raw = await _fetch_related_outlook_emails(
            session,
            window,
            [*issue_blobs, jira_raw],
        )

    return {
        "window": window,
        "oncall_channel_id": oncall_id,
        "prod_dep_channel_id": prod_dep_id,
        "pagerduty_raw": pagerduty_raw,
        "datadog_raw": datadog_raw,
        "slack_oncall_raw": slack_oncall_raw,
        "slack_prod_dep_raw": slack_prod_dep_raw,
        "grm_raw": grm_raw,
        "grm_ids": grm_ids,
        "jira_raw": jira_raw,
        "outlook_raw": outlook_raw,
    }


def generate_shift_report(mode: str = "shift1") -> dict[str, Any]:
    """
    Build shift handoff as a compact Excel-style table via MintMCP + Bedrock JSON rows.
    """
    window = compute_shift_window(mode)
    collected = asyncio.run(_collect_shift_data(window))

    prompt = _build_bedrock_prompt(
        window,
        collected["pagerduty_raw"],
        collected["slack_oncall_raw"],
        collected["slack_prod_dep_raw"],
        collected["datadog_raw"],
        collected["grm_raw"],
        collected["jira_raw"],
        collected["outlook_raw"],
    )
    raw = ask_bedrock(prompt, temperature=0.2, max_tokens=7000)
    if not raw or raw.startswith("Error:"):
        raise RuntimeError(raw or "Bedrock returned empty shift report")

    parsed = _extract_json_object(raw)
    summary, rows = _normalize_rows(parsed)
    rows = _ensure_pagerduty_auto_resolved_summary(rows, collected["pagerduty_raw"])
    rows = _ensure_outlook_email_rows(rows, collected["outlook_raw"], window)
    rows = _ensure_prod_dep_postcheck_rows(rows, collected["slack_prod_dep_raw"])
    rows = _consolidate_shift_rows(rows)
    for row in rows:
        row["status"] = _normalize_shift_status(row.get("status", ""))
    html = _rows_to_html(summary, window, rows)
    csv_content = _rows_to_csv(summary, window, rows)
    plain_text = _rows_to_plain_text(summary, window, rows)

    return {
        "html": html,
        "summary": summary,
        "rows": rows,
        "row_count": len(rows),
        "csv": csv_content,
        "plain_text": plain_text,
        "mode": window.mode,
        "label": window.label,
        "window_start": _iso_z(window.start_utc),
        "window_end": _iso_z(window.end_utc),
        "timezone": window.tz_name,
        "slack_oncall_channel_id": collected["oncall_channel_id"],
        "slack_prod_dep_channel_id": collected["prod_dep_channel_id"],
        "sources": {
            "pagerduty_chars": len(collected["pagerduty_raw"] or ""),
            "datadog_chars": len(collected["datadog_raw"] or ""),
            "slack_oncall_chars": len(collected["slack_oncall_raw"] or ""),
            "slack_prod_dep_chars": len(collected["slack_prod_dep_raw"] or ""),
            "grm_chars": len(collected["grm_raw"] or ""),
            "jira_chars": len(collected["jira_raw"] or ""),
            "grm_ids": collected.get("grm_ids") or [],
            "outlook_chars": len(collected["outlook_raw"] or ""),
        },
    }
