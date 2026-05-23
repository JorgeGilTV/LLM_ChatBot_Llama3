"""
Team Calendars fetch using the same query pattern as the Confluence browser.

Confluence Cloud often returns {"success": true} for bare /subcalendars.json and
/events.json?subCalendarId=<parent>. The embedded calendar widget uses:
  - subcalendars.json?calendarContext=spaceCalendars&viewingSpaceKey=RM&include=<parentId>&_=...
  - events.json per *child* sub-calendar (one per event type), with ISO start/end and _=...
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, Optional

import requests

_LOG = logging.getLogger(__name__)


def deployments_space_key() -> str:
    """Confluence space that owns the GRM calendar (wiki /spaces/RM/...)."""
    return (os.getenv("DEPLOYMENTS_CONFLUENCE_SPACE_KEY") or "RM").strip() or "RM"


def _cache_bust_nano() -> int:
    return time.time_ns()


def _calendar_bases() -> list[str]:
    override = (os.getenv("DEPLOYMENTS_TEAM_CALENDAR_BASE") or "").strip().rstrip("/")
    if override:
        return [override]
    h = (os.getenv("CONFLUENCE_ATLASSIAN_HOST") or "https://arlo.atlassian.net").strip().rstrip("/")
    if not h.startswith("http"):
        h = f"https://{h.lstrip('/')}"
    return [
        f"{h}/wiki/rest/calendar-services/1.0/calendar",
        f"{h}/rest/calendar-services/1.0/calendar",
    ]


def fetch_subcalendars_browser(
    email: str,
    token: str,
    parent_sub_calendar_id: str,
    space_key: str,
    bases: list[str] | None = None,
) -> tuple[object | None, str | None, dict]:
    """
    GET subcalendars.json like the embedded calendar (space context + include parent id).
    Returns (json_payload, base_used, partial_diag).
    """
    auth = (email, token)
    bases = bases or _calendar_bases()
    partial: dict[str, Any] = {"subcalendars_browser_log": []}
    params = {
        "calendarContext": "spaceCalendars",
        "viewingSpaceKey": space_key,
        "include": parent_sub_calendar_id,
        "_": _cache_bust_nano(),
    }
    for base in bases:
        url = f"{base.rstrip('/')}/subcalendars.json"
        try:
            r = requests.get(url, auth=auth, params=params, timeout=25)
            partial["subcalendars_browser_log"].append(
                f"{base.split('//', 1)[-1][:40]} -> {r.status_code}"
            )
            if r.status_code != 200:
                continue
            data = r.json()
            payload = data.get("payload") if isinstance(data, dict) else None
            if isinstance(payload, list) and payload:
                partial["subcalendars_browser_base"] = base
                partial["subcalendars_payload_count"] = len(payload)
                return data, base, partial
        except Exception as e:
            partial["subcalendars_browser_log"].append(f"{base[:32]} err {e!s}")
    return None, None, partial


def collect_event_subcalendar_ids(
    subcalendars_payload: object,
    parent_sub_calendar_id: str,
) -> list[str]:
    """
    Child sub-calendar UUIDs that hold events (browser fires one events.json per child).
    """
    ids: list[str] = []
    seen: set[str] = set()

    def walk(node: object) -> None:
        if node is None:
            return
        if isinstance(node, list):
            for it in node:
                walk(it)
        elif isinstance(node, dict):
            sc = node.get("subCalendar")
            if isinstance(sc, dict):
                sid = sc.get("id")
                tkey = str(sc.get("typeKey") or "")
                # Parent container has type parent; children are custom/other types with events.
                if sid and sid != parent_sub_calendar_id:
                    if sid not in seen:
                        seen.add(sid)
                        ids.append(str(sid))
            for ch in ("childSubCalendars", "childSubCals", "payload"):
                if ch in node and node[ch] is not None:
                    walk(node[ch])

    if isinstance(subcalendars_payload, dict):
        walk(subcalendars_payload.get("payload"))
    else:
        walk(subcalendars_payload)
    return ids


def fetch_events_for_subcalendar_ids(
    email: str,
    token: str,
    sub_calendar_ids: list[str],
    start_iso: str,
    end_iso: str,
    bases: list[str] | None = None,
    *,
    normalize_events: Callable[[object], list],
) -> tuple[list[dict], dict]:
    """
    One events.json GET per child subCalendarId (browser behavior). Merges and dedupes raw events.
    """
    auth = (email, token)
    bases = bases or _calendar_bases()
    partial: dict[str, Any] = {
        "browser_child_subcalendar_count": len(sub_calendar_ids),
        "browser_events_fetch_log": [],
        "browser_raw_events": 0,
        "browser_child_hits": 0,
    }
    merged: list[dict] = []
    seen_ids: set[str] = set()
    base_used: str | None = None

    for sid in sub_calendar_ids:
        params = {
            "subCalendarId": sid,
            "start": start_iso,
            "end": end_iso,
            "userTimeZoneId": "America/Chicago",
            "_": _cache_bust_nano(),
        }
        for base in bases:
            url = f"{base.rstrip('/')}/events.json"
            try:
                r = requests.get(url, auth=auth, params=params, timeout=25)
            except Exception as e:
                partial["browser_events_fetch_log"].append(f"{sid[:8]} err {e!s}")
                continue
            partial["browser_events_fetch_log"].append(
                f"{sid[:8]} {base.split('//', 1)[-1][:28]} -> {r.status_code}"
            )
            if r.status_code != 200:
                continue
            raw = r.json()
            evs = normalize_events(raw)
            if evs:
                base_used = base
                partial["browser_child_hits"] = int(partial.get("browser_child_hits", 0)) + 1
                for ev in evs:
                    if not isinstance(ev, dict):
                        continue
                    eid = str(ev.get("id") or "")
                    if eid and eid in seen_ids:
                        continue
                    if eid:
                        seen_ids.add(eid)
                    merged.append(ev)
                break

    partial["browser_raw_events"] = len(merged)
    if base_used:
        partial["calendar_base_used"] = base_used
    partial["browser_events_fetch_log"] = partial["browser_events_fetch_log"][:40]
    return merged, partial


def load_calendar_events_browser_style(
    email: str,
    token: str,
    parent_sub_calendar_id: str,
    start_iso: str,
    end_iso: str,
    *,
    space_key: str | None = None,
    normalize_events: Callable[[object], list],
    bases: list[str] | None = None,
) -> tuple[list[dict], dict]:
    """
    Full browser-parity load: subcalendars (space+include) then events per child sub-calendar.
    Returns merged raw Team Calendar event dicts.
    """
    sk = space_key or deployments_space_key()
    partial: dict[str, Any] = {"grm_fetch_mode": "browser", "deployments_space_key": sk}

    data, base, sub_partial = fetch_subcalendars_browser(
        email, token, parent_sub_calendar_id, sk, bases=bases
    )
    partial.update(sub_partial)
    if data is None:
        partial["browser_skip_reason"] = "subcalendars_browser_empty"
        return [], partial

    child_ids = collect_event_subcalendar_ids(data, parent_sub_calendar_id)
    partial["browser_child_subcalendar_ids"] = child_ids[:30]
    if not child_ids:
        partial["browser_skip_reason"] = "no_child_subcalendars"
        return [], partial

    raw_events, ev_partial = fetch_events_for_subcalendar_ids(
        email,
        token,
        child_ids,
        start_iso,
        end_iso,
        bases=bases,
        normalize_events=normalize_events,
    )
    partial.update(ev_partial)
    if raw_events and isinstance(raw_events[0], dict):
        partial["sample_event_keys"] = list(raw_events[0].keys())[:25]
    return raw_events, partial


def list_space_subcalendars_for_name_match(
    email: str,
    token: str,
    space_key: str,
    bases: list[str] | None = None,
) -> tuple[object | None, str | None]:
    """subcalendars.json?calendarContext=spaceCalendars&viewingSpaceKey=... (no include)."""
    auth = (email, token)
    bases = bases or _calendar_bases()
    params = {
        "calendarContext": "spaceCalendars",
        "viewingSpaceKey": space_key,
        "_": _cache_bust_nano(),
    }
    for base in bases:
        url = f"{base.rstrip('/')}/subcalendars.json"
        try:
            r = requests.get(url, auth=auth, params=params, timeout=22)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and data.get("payload"):
                    return data, base
        except Exception as e:
            _LOG.debug("space subcalendars %s: %s", base, e)
    return None, None
