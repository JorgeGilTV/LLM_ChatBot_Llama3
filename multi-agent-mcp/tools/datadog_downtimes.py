"""
Datadog maintenance windows (monitor downtimes).

Source UI: https://arlo.datadoghq.com/monitors/downtimes?sort=-start_dt
API: GET /api/v1/downtime?with_creator=true
"""
from __future__ import annotations

import html
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests

from tools.datadog_dashboards import datadog_rest_api_base, datadog_ui_origin

_LOG = logging.getLogger(__name__)

DEFAULT_DISPLAY_TZ = "America/Mexico_City"

MAINTENANCE_KEYWORDS = (
    "maintenance window",
    "maintenance windows",
    "maint window",
    "maint windows",
    "ventana de mantenimiento",
    "ventanas de mantenimiento",
    "downtime",
    "downtimes",
    "monitor downtime",
    "scheduled downtime",
    "datadog downtime",
    "datadog downtimes",
    "monitors/downtimes",
)

DEFAULT_NOC_CREATOR_PATTERNS = (
    "noc@",
    "@noc.",
    "noc-team",
    "noc_team",
    "/noc",
    "goc@",
    "goc-",
    "noc ",
    " noc",
)


@dataclass
class MaintenanceWindowQuery:
    window_start_utc: datetime
    window_end_utc: datetime
    label: str
    noc_only: bool
    include_all_creators: bool


def is_maintenance_window_question(question: str) -> bool:
    if not (question or "").strip():
        return False
    ql = question.lower()
    return any(kw in ql for kw in MAINTENANCE_KEYWORDS)


def _display_tz() -> ZoneInfo:
    name = (os.getenv("DATADOG_DOWNTIME_DISPLAY_TZ") or DEFAULT_DISPLAY_TZ).strip()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(DEFAULT_DISPLAY_TZ)


def _noc_creator_patterns() -> tuple[str, ...]:
    raw = (os.getenv("DATADOG_DOWNTIME_NOC_CREATOR_PATTERNS") or "").strip()
    if raw.lower() in ("0", "false", "off", "none", "*"):
        return tuple()
    if raw:
        return tuple(p.strip().lower() for p in raw.split(",") if p.strip())
    return DEFAULT_NOC_CREATOR_PATTERNS


def _creator_blob(creator: dict[str, Any] | None) -> str:
    if not isinstance(creator, dict):
        return ""
    parts = [creator.get("name"), creator.get("email"), creator.get("handle")]
    return " ".join(str(p) for p in parts if p).lower()


def _noc_creator_allowlist() -> set[str]:
    raw = (os.getenv("DATADOG_DOWNTIME_NOC_CREATORS") or "").strip()
    if not raw:
        return set()
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


def is_noc_creator(creator: dict[str, Any] | None) -> bool:
    allowlist = _noc_creator_allowlist()
    if allowlist:
        blob = _creator_blob(creator)
        if any(email in blob for email in allowlist):
            return True
        email = str((creator or {}).get("email") or "").strip().lower()
        handle = str((creator or {}).get("handle") or "").strip().lower()
        return email in allowlist or handle in allowlist

    patterns = _noc_creator_patterns()
    if not patterns:
        return True
    blob = _creator_blob(creator)
    if not blob:
        return False
    return any(p in blob for p in patterns)


def _parse_epoch_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            sec = float(value)
            if sec > 1e12:
                sec /= 1000.0
            return datetime.fromtimestamp(sec, tz=timezone.utc)
        text = str(value).strip().replace("Z", "+00:00")
        if not text:
            return None
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def parse_maintenance_window_query(question: str, now_utc: datetime | None = None) -> MaintenanceWindowQuery:
    """Default: overlapping the next 24 hours. Honors explicit ranges in the question."""
    now = now_utc or datetime.now(timezone.utc)
    ql = (question or "").lower()

    include_all = any(
        phrase in ql
        for phrase in (
            "all creators",
            "all teams",
            "any creator",
            "todos los",
            "cualquier equipo",
            "not only noc",
            "sin filtro noc",
        )
    )
    noc_only = not include_all

    past = any(
        kw in ql
        for kw in ("past", "pasado", "pasados", "last", "último", "ultimo", "últimas", "ultimas", "previous")
    )

    hours = 24
    match = re.search(r"(\d+)\s*(hora|hour|horas|hours|day|days|dia|dias|día|días)", ql)
    if match:
        hours = int(match.group(1))
        unit = match.group(2)
        if any(x in unit for x in ("day", "dia", "día")):
            hours *= 24
    else:
        num = re.search(r"(?:next|próxim|proxim|last|últim|ultim)\w*\s+(\d+)", ql)
        if num:
            hours = int(num.group(1)) * 24

    if past:
        start = now - timedelta(hours=hours)
        end = now
        label = f"Last {hours} hours"
    else:
        start = now
        end = now + timedelta(hours=hours)
        label = f"Next {hours} hours"

    return MaintenanceWindowQuery(
        window_start_utc=start,
        window_end_utc=end,
        label=label,
        noc_only=noc_only,
        include_all_creators=include_all,
    )


def _downtime_overlaps_window(
    downtime: dict[str, Any],
    window_start: datetime,
    window_end: datetime,
) -> bool:
    start = _parse_epoch_ts(downtime.get("start"))
    if start is None:
        return False
    end = _parse_epoch_ts(downtime.get("end"))
    if end is None:
        # Open-ended downtime: relevant if it started before window end.
        return start <= window_end
    return start <= window_end and end >= window_start


def _downtime_status(downtime: dict[str, Any], now_utc: datetime) -> str:
    if downtime.get("canceled"):
        return "Canceled"
    start = _parse_epoch_ts(downtime.get("start"))
    end = _parse_epoch_ts(downtime.get("end"))
    active = bool(downtime.get("active"))

    if start and start > now_utc:
        return "Scheduled"
    if active and (end is None or end > now_utc):
        return "Active"
    if end and end <= now_utc:
        return "Ended"
    api_status = str(downtime.get("status") or "").strip()
    if api_status:
        return api_status.capitalize()
    return "Unknown"


def _format_local_dt(dt: datetime | None, tz: ZoneInfo) -> str:
    if dt is None:
        return "—"
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")


def _scope_label(downtime: dict[str, Any]) -> str:
    message = (downtime.get("message") or "").strip()
    scope = downtime.get("scope") or downtime.get("monitor_tags") or []
    if isinstance(scope, list):
        scope_text = ", ".join(str(s) for s in scope if s)
    else:
        scope_text = str(scope or "")
    if message and scope_text:
        return f"{message} · {scope_text}"
    return message or scope_text or "—"


def _downtimes_ui_url(dd_site: str | None) -> str:
    return f"{datadog_ui_origin(dd_site)}/monitors/downtimes?sort=-start_dt"


def _downtime_ui_url(dd_site: str | None, downtime_id: Any) -> str:
    base = _downtimes_ui_url(dd_site)
    if downtime_id is None:
        return base
    return f"{base}&query=id%3A{downtime_id}"


def _datadog_headers() -> dict[str, str] | None:
    api_key = (os.getenv("DATADOG_API_KEY") or "").strip()
    app_key = (os.getenv("DATADOG_APP_KEY") or "").strip()
    if not api_key or not app_key:
        return None
    return {
        "DD-API-KEY": api_key,
        "DD-APPLICATION-KEY": app_key,
        "Content-Type": "application/json",
    }


def fetch_datadog_downtimes(
    *,
    window_start_utc: datetime,
    window_end_utc: datetime,
    noc_only: bool = True,
) -> dict[str, Any]:
    headers = _datadog_headers()
    dd_site = os.getenv("DATADOG_SITE", "datadoghq.com")
    if not headers:
        return {
            "error": "DATADOG_API_KEY and DATADOG_APP_KEY must be set.",
            "downtimes": [],
        }

    url = f"{datadog_rest_api_base(dd_site)}/api/v1/downtime"
    try:
        response = requests.get(
            url,
            headers=headers,
            params={"with_creator": "true"},
            timeout=45,
        )
        if response.status_code != 200:
            return {
                "error": f"Datadog downtime API HTTP {response.status_code}: {response.text[:300]}",
                "downtimes": [],
            }
        payload = response.json()
        items = payload if isinstance(payload, list) else payload.get("downtimes") or []
        if not isinstance(items, list):
            items = []
    except requests.RequestException as exc:
        _LOG.exception("Datadog downtime fetch failed")
        return {"error": str(exc), "downtimes": []}

    now_utc = datetime.now(timezone.utc)
    tz = _display_tz()
    rows: list[dict[str, Any]] = []
    skipped_non_noc = 0

    for item in items:
        if not isinstance(item, dict):
            continue
        if not _downtime_overlaps_window(item, window_start_utc, window_end_utc):
            continue
        creator = item.get("creator") if isinstance(item.get("creator"), dict) else {}
        if noc_only and not is_noc_creator(creator):
            skipped_non_noc += 1
            continue

        start = _parse_epoch_ts(item.get("start"))
        end = _parse_epoch_ts(item.get("end"))
        status = _downtime_status(item, now_utc)
        rows.append(
            {
                "id": item.get("id"),
                "creator_name": (creator or {}).get("name") or "—",
                "creator_email": (creator or {}).get("email") or (creator or {}).get("handle") or "—",
                "status": status,
                "active": bool(item.get("active")),
                "start_utc": start.isoformat() if start else None,
                "end_utc": end.isoformat() if end else None,
                "start_local": _format_local_dt(start, tz),
                "end_local": _format_local_dt(end, tz),
                "scope": _scope_label(item),
                "message": (item.get("message") or "").strip(),
                "url": _downtime_ui_url(dd_site, item.get("id")),
            }
        )

    rows.sort(key=lambda r: r.get("start_utc") or "", reverse=True)
    return {
        "downtimes": rows,
        "total_fetched": len(items),
        "total_in_window": len(rows) + (skipped_non_noc if noc_only else 0),
        "skipped_non_noc": skipped_non_noc,
        "noc_only": noc_only,
        "noc_patterns": list(_noc_creator_patterns()),
        "ui_url": _downtimes_ui_url(dd_site),
        "window_utc": {
            "start": window_start_utc.isoformat(),
            "end": window_end_utc.isoformat(),
        },
    }


def format_maintenance_windows_html(
    data: dict[str, Any],
    query: MaintenanceWindowQuery,
) -> str:
    if data.get("error"):
        return f"""
        <div style='background:#fee;padding:12px;border-left:4px solid #dc2626;border-radius:4px;'>
            <p style='margin:0;color:#991b1b;'>
                ❌ <strong>Datadog maintenance windows:</strong> {html.escape(str(data["error"]))}
            </p>
        </div>
        """

    rows = data.get("downtimes") or []
    ui_url = html.escape(str(data.get("ui_url") or _downtimes_ui_url(None)))
    filter_note = (
        "NOC team creators only"
        if query.noc_only
        else "All creators"
    )
    skipped = int(data.get("skipped_non_noc") or 0)

    header = f"""
    <div style="font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;max-width:100%;">
      <div style="background:linear-gradient(135deg,#632ca6 0%,#4f46e5 100%);padding:20px 18px;border-radius:10px;margin-bottom:16px;color:#fff;">
        <div style="font-size:24px;font-weight:800;margin-bottom:6px;">🛠️ Datadog Maintenance Windows</div>
        <div style="font-size:13px;opacity:.95;">{html.escape(query.label)} · {html.escape(filter_note)}</div>
        <div style="margin-top:8px;font-size:12px;opacity:.9;">
          Same data as <a href="{ui_url}" target="_blank" rel="noopener noreferrer" style="color:#e9d5ff;font-weight:700;">Monitors → Downtimes</a>
        </div>
      </div>
    """

    meta = f"""
      <div style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:14px;">
        <div style="background:#f5f3ff;border:1px solid #c4b5fd;border-radius:8px;padding:10px 14px;font-size:12px;color:#4c1d95;">
          <strong>{len(rows)}</strong> window(s) in range
        </div>
        <div style="background:#f8fafc;border:1px solid #cbd5e1;border-radius:8px;padding:10px 14px;font-size:12px;color:#334155;">
          Scanned <strong>{int(data.get('total_fetched') or 0)}</strong> downtimes from Datadog
        </div>
    """
    if query.noc_only and skipped:
        meta += f"""
        <div style="background:#fff7ed;border:1px solid #fdba74;border-radius:8px;padding:10px 14px;font-size:12px;color:#9a3412;">
          Hidden <strong>{skipped}</strong> non-NOC window(s) in this range
        </div>
        """
    meta += "</div>"

    if not rows:
        patterns = ", ".join(data.get("noc_patterns") or [])
        hint = ""
        if query.noc_only and patterns:
            hint = (
                f"<p style='margin:8px 0 0;font-size:11px;color:#64748b;'>"
                f"NOC filter patterns: <code>{html.escape(patterns)}</code>. "
                f"Adjust <code>DATADOG_DOWNTIME_NOC_CREATOR_PATTERNS</code> or ask for all creators."
                f"</p>"
            )
        return (
            header
            + meta
            + f"""
      <div style="background:#ecfdf5;border:1px solid #6ee7b7;border-radius:8px;padding:16px;color:#065f46;">
        ✅ No NOC maintenance windows found for {html.escape(query.label.lower())}.
        {hint}
      </div>
    </div>
            """
        )

    status_colors = {
        "Active": ("#dcfce7", "#166534", "#86efac"),
        "Scheduled": ("#dbeafe", "#1e40af", "#93c5fd"),
        "Ended": ("#f3f4f6", "#374151", "#d1d5db"),
        "Canceled": ("#fee2e2", "#991b1b", "#fca5a5"),
    }

    body = """
      <table style="width:100%;border-collapse:separate;border-spacing:0;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;font-size:13px;">
        <thead>
          <tr style="background:#f8fafc;">
            <th style="padding:10px 12px;text-align:left;border-bottom:1px solid #e2e8f0;">Creator</th>
            <th style="padding:10px 12px;text-align:left;border-bottom:1px solid #e2e8f0;">Start</th>
            <th style="padding:10px 12px;text-align:left;border-bottom:1px solid #e2e8f0;">End</th>
            <th style="padding:10px 12px;text-align:left;border-bottom:1px solid #e2e8f0;">Status</th>
            <th style="padding:10px 12px;text-align:left;border-bottom:1px solid #e2e8f0;">Scope / message</th>
          </tr>
        </thead>
        <tbody>
    """

    for row in rows:
        status = str(row.get("status") or "Unknown")
        bg, fg, border = status_colors.get(status, ("#f8fafc", "#334155", "#e2e8f0"))
        creator = html.escape(str(row.get("creator_name") or "—"))
        email = html.escape(str(row.get("creator_email") or ""))
        scope = html.escape(str(row.get("scope") or "—"))
        link = html.escape(str(row.get("url") or ui_url))
        body += f"""
          <tr style="border-bottom:1px solid #f1f5f9;">
            <td style="padding:10px 12px;vertical-align:top;">
              <div style="font-weight:700;color:#0f172a;">{creator}</div>
              <div style="font-size:11px;color:#64748b;">{email}</div>
            </td>
            <td style="padding:10px 12px;vertical-align:top;color:#334155;">{html.escape(str(row.get('start_local') or '—'))}</td>
            <td style="padding:10px 12px;vertical-align:top;color:#334155;">{html.escape(str(row.get('end_local') or '—'))}</td>
            <td style="padding:10px 12px;vertical-align:top;">
              <span style="display:inline-block;padding:4px 10px;border-radius:999px;background:{bg};color:{fg};border:1px solid {border};font-weight:700;font-size:11px;">{html.escape(status)}</span>
            </td>
            <td style="padding:10px 12px;vertical-align:top;color:#475569;">
              {scope}
              <div style="margin-top:6px;"><a href="{link}" target="_blank" rel="noopener noreferrer" style="font-size:11px;color:#4f46e5;font-weight:700;">Open ↗</a></div>
            </td>
          </tr>
        """

    body += "</tbody></table></div>"
    return header + meta + body


def get_datadog_maintenance_windows(question: str = "") -> str:
    """Entry point for chat/MCP: parse question, fetch downtimes, return HTML table."""
    query = parse_maintenance_window_query(question)
    data = fetch_datadog_downtimes(
        window_start_utc=query.window_start_utc,
        window_end_utc=query.window_end_utc,
        noc_only=query.noc_only,
    )
    return format_maintenance_windows_html(data, query)
