"""
Datadog maintenance windows (monitor downtimes).

Source UI: https://arlo.datadoghq.com/monitors/downtimes?sort=-start_dt
API: GET /api/v1/downtime/search?sort=-start_dt (matches UI list)
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

# Handles visible in Datadog downtimes UI (NOC / GOC sidebar filter).
DEFAULT_NOC_CREATOR_HANDLES = (
    "fvaghasiya.c@arlo.com",
    "dhshah@arlo.com",
    "sbarochiya.c@arlo.com",
    "kparate.c@arlo.com",
    "akabra.c@arlo.com",
    "vmishra@arlo.com",
    "dsharma.c@arlo.com",
    "ndammalapati.c@arlo.com",
)

DEFAULT_NOC_CREATOR_PATTERNS = (
    "noc@",
    "@noc.",
    "noc-team",
    "noc_team",
    "/noc",
    "goc@",
    "goc-",
)

# Scope / monitor tag hints for NOC deployment maintenance (see Datadog downtimes UI).
DEFAULT_NOC_TAG_PATTERNS = (
    "team:noc",
    "env:adt_prod",
    "env:production",
    "env:prod",
    "env:prd",
    "host:partner",
    "adt_prod",
)


@dataclass
class MaintenanceWindowQuery:
    window_start_utc: datetime
    window_end_utc: datetime
    label: str
    noc_only: bool
    include_all_creators: bool
    include_active_now: bool


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


def _noc_creator_handles() -> set[str]:
    raw = (os.getenv("DATADOG_DOWNTIME_NOC_CREATORS") or "").strip()
    if raw.lower() in ("0", "false", "off", "none"):
        return set()
    if raw:
        return {p.strip().lower() for p in raw.split(",") if p.strip()}
    return {h.lower() for h in DEFAULT_NOC_CREATOR_HANDLES}


def _noc_creator_patterns() -> tuple[str, ...]:
    raw = (os.getenv("DATADOG_DOWNTIME_NOC_CREATOR_PATTERNS") or "").strip()
    if raw.lower() in ("0", "false", "off", "none", "*"):
        return tuple()
    if raw:
        return tuple(p.strip().lower() for p in raw.split(",") if p.strip())
    return DEFAULT_NOC_CREATOR_PATTERNS


def _noc_tag_patterns() -> tuple[str, ...]:
    raw = (os.getenv("DATADOG_DOWNTIME_NOC_TAG_PATTERNS") or "").strip()
    if raw.lower() in ("0", "false", "off", "none"):
        return tuple()
    if raw:
        return tuple(p.strip().lower() for p in raw.split(",") if p.strip())
    return DEFAULT_NOC_TAG_PATTERNS


def _creator_blob(creator: dict[str, Any] | None) -> str:
    if not isinstance(creator, dict):
        return ""
    parts = [creator.get("name"), creator.get("email"), creator.get("handle")]
    return " ".join(str(p) for p in parts if p).lower()


def _downtime_tag_blob(downtime: dict[str, Any], monitor_tags: list[str] | None = None) -> str:
    parts: list[str] = []
    scope = downtime.get("scope") or []
    if isinstance(scope, list):
        parts.extend(str(s) for s in scope)
    elif scope:
        parts.append(str(scope))
    for field in ("monitor_name", "message"):
        val = downtime.get(field)
        if val:
            parts.append(str(val))
    tags = monitor_tags if monitor_tags is not None else downtime.get("monitor_tags")
    if isinstance(tags, list):
        parts.extend(str(t) for t in tags)
    elif tags:
        parts.append(str(tags))
    return " ".join(parts).lower()


def _creator_matches_noc(creator: dict[str, Any] | None) -> bool:
    handles = _noc_creator_handles()
    if handles:
        blob = _creator_blob(creator)
        email = str((creator or {}).get("email") or "").strip().lower()
        handle = str((creator or {}).get("handle") or "").strip().lower()
        if email in handles or handle in handles:
            return True
        if blob and any(h in blob for h in handles):
            return True
    patterns = _noc_creator_patterns()
    if not patterns:
        return not handles
    blob = _creator_blob(creator)
    return bool(blob) and any(p in blob for p in patterns)


def _tags_match_noc(tag_blob: str) -> bool:
    patterns = _noc_tag_patterns()
    if not patterns:
        return False
    return any(p in tag_blob for p in patterns)


def is_noc_downtime(
    downtime: dict[str, Any],
    *,
    monitor_tags: list[str] | None = None,
) -> bool:
    """NOC maintenance = known NOC creator OR team:noc / partner / adt_prod tags."""
    if _creator_matches_noc(downtime.get("creator") if isinstance(downtime.get("creator"), dict) else None):
        return True
    blob = _downtime_tag_blob(downtime, monitor_tags)
    return _tags_match_noc(blob)


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
    """Default: active now + scheduled in the next 24 hours."""
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
        include_active = False
    else:
        start = now
        end = now + timedelta(hours=hours)
        label = f"Next {hours} hours (incl. active now)"
        include_active = True

    return MaintenanceWindowQuery(
        window_start_utc=start,
        window_end_utc=end,
        label=label,
        noc_only=noc_only,
        include_all_creators=include_all,
        include_active_now=include_active,
    )


def _downtime_is_active_now(downtime: dict[str, Any], now_utc: datetime) -> bool:
    if downtime.get("canceled") or downtime.get("canceled_dt"):
        return False
    if not downtime.get("active"):
        return False
    end = _parse_epoch_ts(downtime.get("end") or downtime.get("end_dt"))
    return end is None or end > now_utc


def _downtime_overlaps_window(
    downtime: dict[str, Any],
    window_start: datetime,
    window_end: datetime,
) -> bool:
    start = _parse_epoch_ts(downtime.get("start") or downtime.get("start_dt"))
    if start is None:
        return False
    end = _parse_epoch_ts(downtime.get("end") or downtime.get("end_dt"))
    if end is None:
        return start <= window_end
    return start <= window_end and end >= window_start


def _downtime_matches_query_window(
    downtime: dict[str, Any],
    query: MaintenanceWindowQuery,
    now_utc: datetime,
) -> bool:
    if query.include_active_now and _downtime_is_active_now(downtime, now_utc):
        return True
    return _downtime_overlaps_window(downtime, query.window_start_utc, query.window_end_utc)


def _downtime_status(downtime: dict[str, Any], now_utc: datetime) -> str:
    if downtime.get("canceled") or downtime.get("canceled_dt"):
        return "Canceled"
    start = _parse_epoch_ts(downtime.get("start") or downtime.get("start_dt"))
    end = _parse_epoch_ts(downtime.get("end") or downtime.get("end_dt"))
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


def _format_local_dt(dt: datetime | None, tz: ZoneInfo, *, indefinite: bool = False) -> str:
    if indefinite and dt is None:
        return "Indefinitely"
    if dt is None:
        return "—"
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")


def _scope_label(downtime: dict[str, Any], monitor_tags: list[str] | None = None) -> str:
    message = (downtime.get("message") or "").strip()
    scope = downtime.get("scope") or []
    if isinstance(scope, list):
        scope_text = ", ".join(str(s) for s in scope if s)
    else:
        scope_text = str(scope or "")
    monitor_name = (downtime.get("monitor_name") or "").strip()
    tags = monitor_tags if monitor_tags is not None else downtime.get("monitor_tags")
    tag_text = ""
    if isinstance(tags, list) and tags:
        tag_text = ", ".join(str(t) for t in tags if t and t != "*")

    bits = [b for b in (monitor_name, scope_text, tag_text, message) if b]
    return " · ".join(bits) if bits else "—"


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
        "Accept": "application/json",
    }


def _normalize_search_downtime(row: dict[str, Any]) -> dict[str, Any]:
    data = row.get("data") if isinstance(row.get("data"), dict) else row
    attrs = data.get("attributes") if isinstance(data.get("attributes"), dict) else data
    out = dict(attrs)
    out["id"] = data.get("id") or attrs.get("id")
    if attrs.get("start_dt") and not out.get("start"):
        out["start"] = attrs.get("start_dt")
    if attrs.get("end_dt") and not out.get("end"):
        out["end"] = attrs.get("end_dt")
    return out


def _fetch_downtime_search_rows(headers: dict[str, str], dd_site: str) -> list[dict[str, Any]]:
    """Paginate /api/v1/downtime/search — same ordering as the Datadog UI."""
    url = f"{datadog_rest_api_base(dd_site)}/api/v1/downtime/search"
    max_rows = max(30, min(int(os.getenv("DATADOG_DOWNTIME_SEARCH_MAX", "500")), 2000))
    page_size = 100
    rows: list[dict[str, Any]] = []
    offset = 0

    while len(rows) < max_rows:
        response = requests.get(
            url,
            headers=headers,
            params={"limit": page_size, "offset": offset, "sort": "-start_dt"},
            timeout=60,
        )
        if response.status_code != 200:
            raise requests.HTTPError(
                f"Datadog downtime search HTTP {response.status_code}: {response.text[:300]}",
                response=response,
            )
        downtimes = (
            (response.json().get("data") or {}).get("attributes", {}).get("downtimes") or []
        )
        if not downtimes:
            break
        for item in downtimes:
            if isinstance(item, dict):
                rows.append(_normalize_search_downtime(item))
        if len(downtimes) < page_size:
            break
        offset += page_size
    return rows[:max_rows]


def _fetch_monitor_tags(
    headers: dict[str, str],
    dd_site: str,
    monitor_id: Any,
    cache: dict[int, list[str]],
) -> list[str]:
    try:
        mid = int(monitor_id)
    except (TypeError, ValueError):
        return []
    if mid in cache:
        return cache[mid]
    try:
        response = requests.get(
            f"{datadog_rest_api_base(dd_site)}/api/v1/monitor/{mid}",
            headers=headers,
            timeout=25,
        )
        if response.status_code != 200:
            cache[mid] = []
            return []
        tags = response.json().get("tags") or []
        cache[mid] = [str(t) for t in tags if t]
        return cache[mid]
    except requests.RequestException:
        cache[mid] = []
        return []


def fetch_datadog_downtimes(
    *,
    window_start_utc: datetime,
    window_end_utc: datetime,
    noc_only: bool = True,
    include_active_now: bool = True,
) -> dict[str, Any]:
    headers = _datadog_headers()
    dd_site = os.getenv("DATADOG_SITE", "datadoghq.com")
    if not headers:
        return {
            "error": "DATADOG_API_KEY and DATADOG_APP_KEY must be set.",
            "downtimes": [],
        }

    query = MaintenanceWindowQuery(
        window_start_utc=window_start_utc,
        window_end_utc=window_end_utc,
        label="",
        noc_only=noc_only,
        include_all_creators=not noc_only,
        include_active_now=include_active_now,
    )

    try:
        items = _fetch_downtime_search_rows(headers, dd_site)
    except requests.RequestException as exc:
        _LOG.exception("Datadog downtime search failed")
        return {"error": str(exc), "downtimes": []}

    now_utc = datetime.now(timezone.utc)
    tz = _display_tz()
    rows: list[dict[str, Any]] = []
    skipped_non_noc = 0
    monitor_tag_cache: dict[int, list[str]] = {}

    for item in items:
        if not isinstance(item, dict):
            continue
        if not _downtime_matches_query_window(item, query, now_utc):
            continue

        monitor_id = item.get("monitor_id")
        monitor_tags = list(item.get("monitor_tags") or [])
        if monitor_id:
            fetched_tags = _fetch_monitor_tags(headers, dd_site, monitor_id, monitor_tag_cache)
            if fetched_tags:
                monitor_tags = fetched_tags

        if noc_only and not is_noc_downtime(item, monitor_tags=monitor_tags):
            skipped_non_noc += 1
            continue

        creator = item.get("creator") if isinstance(item.get("creator"), dict) else {}
        start = _parse_epoch_ts(item.get("start") or item.get("start_dt"))
        end = _parse_epoch_ts(item.get("end") or item.get("end_dt"))
        status = _downtime_status(item, now_utc)
        creator_name = (creator or {}).get("name") or (
            "Datadog (auto)" if item.get("automuted") else "—"
        )
        creator_email = (creator or {}).get("handle") or (creator or {}).get("email") or "—"

        rows.append(
            {
                "id": item.get("id"),
                "creator_name": creator_name,
                "creator_email": creator_email,
                "status": status,
                "active": bool(item.get("active")),
                "start_utc": start.isoformat() if start else None,
                "end_utc": end.isoformat() if end else None,
                "start_local": _format_local_dt(start, tz),
                "end_local": _format_local_dt(end, tz, indefinite=end is None),
                "scope": _scope_label(item, monitor_tags),
                "monitor_tags": monitor_tags,
                "message": (item.get("message") or "").strip(),
                "automuted": bool(item.get("automuted")),
                "url": _downtime_ui_url(dd_site, item.get("id")),
            }
        )

    rows.sort(
        key=lambda r: (
            0 if r.get("status") == "Active" else 1,
            r.get("start_utc") or "",
        ),
        reverse=False,
    )
    rows.sort(key=lambda r: r.get("start_utc") or "", reverse=True)

    return {
        "downtimes": rows,
        "total_fetched": len(items),
        "total_in_window": len(rows) + (skipped_non_noc if noc_only else 0),
        "skipped_non_noc": skipped_non_noc,
        "noc_only": noc_only,
        "noc_handles": sorted(_noc_creator_handles()),
        "noc_tag_patterns": list(_noc_tag_patterns()),
        "ui_url": _downtimes_ui_url(dd_site),
        "window_utc": {
            "start": window_start_utc.isoformat(),
            "end": window_end_utc.isoformat(),
        },
        "includes_active_now": include_active_now,
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
    filter_note = "NOC team (creators + team:noc / partner tags)" if query.noc_only else "All creators"
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
          <strong>{len(rows)}</strong> window(s) matched
        </div>
        <div style="background:#f8fafc;border:1px solid #cbd5e1;border-radius:8px;padding:10px 14px;font-size:12px;color:#334155;">
          Scanned <strong>{int(data.get('total_fetched') or 0)}</strong> downtimes (search API)
        </div>
    """
    if query.noc_only and skipped:
        meta += f"""
        <div style="background:#fff7ed;border:1px solid #fdba74;border-radius:8px;padding:10px 14px;font-size:12px;color:#9a3412;">
          Hidden <strong>{skipped}</strong> non-NOC window(s) in range
        </div>
        """
    meta += "</div>"

    if not rows:
        handles = ", ".join(data.get("noc_handles") or [])
        tags = ", ".join(data.get("noc_tag_patterns") or [])
        return (
            header
            + meta
            + f"""
      <div style="background:#ecfdf5;border:1px solid #6ee7b7;border-radius:8px;padding:16px;color:#065f46;">
        ✅ No NOC maintenance windows found for {html.escape(query.label.lower())}.
        <p style='margin:8px 0 0;font-size:11px;color:#64748b;'>
          NOC creators: <code>{html.escape(handles)}</code><br>
          Tag patterns: <code>{html.escape(tags)}</code>
        </p>
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
            <th style="padding:10px 12px;text-align:left;border-bottom:1px solid #e2e8f0;">Scope / tags</th>
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
        tags = row.get("monitor_tags") or []
        tag_line = ""
        if tags:
            tag_line = (
                "<div style='font-size:11px;color:#6366f1;margin-top:4px;'>"
                + html.escape(", ".join(str(t) for t in tags if t))
                + "</div>"
            )
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
              {tag_line}
              <div style="margin-top:6px;"><a href="{link}" target="_blank" rel="noopener noreferrer" style="font-size:11px;color:#4f46e5;font-weight:700;">Open ↗</a></div>
            </td>
          </tr>
        """

    body += "</tbody></table></div>"
    return header + meta + body


def get_datadog_maintenance_windows(question: str = "") -> str:
    """MCP tool entry point: parse question, fetch downtimes, return HTML table."""
    query = parse_maintenance_window_query(question)
    data = fetch_datadog_downtimes(
        window_start_utc=query.window_start_utc,
        window_end_utc=query.window_end_utc,
        noc_only=query.noc_only,
        include_active_now=query.include_active_now,
    )
    return format_maintenance_windows_html(data, query)
