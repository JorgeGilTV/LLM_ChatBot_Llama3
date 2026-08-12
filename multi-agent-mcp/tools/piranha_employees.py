"""
Piranha (EngiHub) employee lookup — team, title, manager from https://piranha.arlo.com/#employees

Uses GET /api/2/employees (Okta SSO session via AWS ALB cookies).
"""

from __future__ import annotations

import html
import os
import re
import threading
import time
from typing import Any

import requests

from tools.piranha_session import (
    _piranha_base,
    api_requests_session_from_cookies,
    connect_instructions_html,
    validate_session,
)

DEFAULT_EMPLOYEES_API = "/api/2/employees"
_HTTP_TIMEOUT = (10, 60)
_DEFAULT_CACHE_SECS = 300

_cache_lock = threading.Lock()
_cache_employees: list[dict[str, Any]] | None = None
_cache_ts: float = 0.0


def _employees_api_path() -> str:
    path = (os.getenv("PIRANHA_EMPLOYEES_API") or DEFAULT_EMPLOYEES_API).strip()
    return path if path.startswith("/") else f"/{path}"


def _cache_secs() -> int:
    try:
        return max(30, int(os.getenv("PIRANHA_CACHE_SECS") or _DEFAULT_CACHE_SECS))
    except (TypeError, ValueError):
        return _DEFAULT_CACHE_SECS


def _portal_url() -> str:
    return f"{_piranha_base()}/#employees"


def _pick_value(record: dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        if key not in record:
            continue
        val = record.get(key)
        if val is None or val == "":
            continue
        if isinstance(val, dict):
            for nested in ("name", "display_name", "title", "label", "email"):
                nested_val = val.get(nested)
                if nested_val not in (None, ""):
                    return str(nested_val)
            return str(val)
        if isinstance(val, list):
            parts = [str(x) for x in val if x not in (None, "")]
            if parts:
                return ", ".join(parts)
        return str(val)
    return default


def employee_display_fields(emp: dict[str, Any]) -> dict[str, str]:
    return {
        "name": _pick_value(
            emp,
            "name",
            "full_name",
            "display_name",
            "employee_name",
            "preferred_name",
            default="—",
        ),
        "email": _pick_value(emp, "email", "mail", "work_email", "user_email", default="—"),
        "team": _pick_value(
            emp,
            "team",
            "team_name",
            "teamName",
            "engineering_team",
            "primary_team",
            "org_team",
            "group",
            default="—",
        ),
        "title": _pick_value(emp, "title", "job_title", "jobTitle", "role", default="—"),
        "manager": _pick_value(
            emp,
            "supervisor",
            "manager",
            "manager_name",
            "reports_to",
            "supervisor_name",
            default="—",
        ),
        "department": _pick_value(
            emp,
            "department",
            "ad_department",
            "dept_code",
            "dept",
            default="—",
        ),
        "location": _pick_value(emp, "location", "office", "site", default="—"),
        "start_date": _pick_value(emp, "start_date", "hire_date", default="—"),
    }


def _flatten_record_text(emp: dict[str, Any]) -> str:
    fields = employee_display_fields(emp)
    extra = " ".join(str(v) for v in emp.values() if isinstance(v, (str, int, float)))
    return " ".join(fields.values()) + " " + extra


def filter_employees(employees: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return employees[:50]

    tokens = [t for t in re.split(r"\s+", q.lower()) if len(t) >= 2]
    if not tokens:
        tokens = [q.lower()]

    scored: list[tuple[int, dict[str, Any]]] = []
    for emp in employees:
        hay = _flatten_record_text(emp).lower()
        score = sum(1 for tok in tokens if tok in hay)
        if score > 0:
            scored.append((score, emp))

    scored.sort(key=lambda item: (-item[0], employee_display_fields(item[1]).get("name", "")))
    return [emp for _, emp in scored[:50]]


def _normalize_employees_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("employees", "data", "results", "items", "records"):
            val = payload.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
        if any(k in payload for k in ("email", "name", "team", "team_name")):
            return [payload]
    return []


def fetch_employees(
    *,
    flask_session: dict[str, Any] | None = None,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    global _cache_employees, _cache_ts

    now = time.time()
    with _cache_lock:
        if not force_refresh and _cache_employees is not None and (now - _cache_ts) < _cache_secs():
            return list(_cache_employees)

    sess = api_requests_session_from_cookies(flask_session)
    if sess is None:
        raise RuntimeError(
            "Piranha is not connected. Sign in with Okta and save ALB session cookies "
            "(AWSELBAuthSessionCookie-0/1). See connect instructions in the tool output."
        )

    url = f"{_piranha_base()}{_employees_api_path()}"
    try:
        resp = sess.get(url, timeout=_HTTP_TIMEOUT, allow_redirects=False)
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(f"Cannot reach Piranha: {exc}") from exc
    except requests.exceptions.Timeout as exc:
        raise RuntimeError(f"Timeout contacting Piranha: {exc}") from exc

    if resp.status_code in (301, 302, 303, 307, 308):
        loc = resp.headers.get("location") or ""
        if "okta.com" in loc.lower():
            raise RuntimeError("Piranha session expired — reconnect with Okta.")
        raise RuntimeError(f"Piranha redirected (HTTP {resp.status_code}).")

    if resp.status_code != 200:
        raise RuntimeError(f"Piranha HTTP {resp.status_code}: {(resp.text or '')[:200]}")

    try:
        payload = resp.json()
    except ValueError as exc:
        raise RuntimeError("Invalid JSON from Piranha employees API.") from exc

    employees = _normalize_employees_payload(payload)
    if not employees:
        raise RuntimeError("Piranha returned no employee records.")

    with _cache_lock:
        _cache_employees = employees
        _cache_ts = now
    return list(employees)


def render_employee_lookup_html(
    query: str = "",
    *,
    flask_session: dict[str, Any] | None = None,
    force_refresh: bool = False,
) -> str:
    portal = _portal_url()
    try:
        all_employees = fetch_employees(flask_session=flask_session, force_refresh=force_refresh)
    except RuntimeError as exc:
        return connect_instructions_html() + (
            f"<div style='background:#fee2e2;padding:12px;border-left:4px solid #ef4444;"
            f"border-radius:6px;margin:10px 0;color:#991b1b;font-size:13px;'>"
            f"<strong>Error:</strong> {html.escape(str(exc))}</div>"
        )

    filtered = filter_employees(all_employees, query)
    q_note = ""
    if (query or "").strip():
        q_note = (
            f"<div style='padding:10px;background:#e0f2fe;border-left:4px solid #0284c7;"
            f"border-radius:4px;margin:10px 0;font-size:12px;color:#0c4a6e;'>"
            f"<strong>Search:</strong> {html.escape(query)} — "
            f"{len(filtered)} match(es) of {len(all_employees)} employees</div>"
        )

    if not filtered:
        return (
            f"<div class='piranha-employee-dash' style='font-family:system-ui,sans-serif;'>"
            f"<h2 style='margin:0 0 8px;font-size:18px;color:#0f172a;'>🐟 Piranha — Employee / Team Lookup</h2>"
            f"<p style='margin:0 0 10px;font-size:12px;color:#64748b;'>"
            f"Source: <a href='{html.escape(portal)}' target='_blank' rel='noopener'>Piranha EngiHub</a></p>"
            f"{q_note}"
            f"<p>No employees matched <strong>{html.escape(query)}</strong>.</p></div>"
        )

    rows = []
    for emp in filtered:
        f = employee_display_fields(emp)
        rows.append(
            "<tr>"
            f"<td style='padding:8px;border:1px solid #e5e7eb;font-weight:600;'>{html.escape(f['name'])}</td>"
            f"<td style='padding:8px;border:1px solid #e5e7eb;font-family:monospace;font-size:11px;'>{html.escape(f['email'])}</td>"
            f"<td style='padding:8px;border:1px solid #e5e7eb;background:#fef9c3;font-weight:700;color:#854d0e;'>{html.escape(f['team'])}</td>"
            f"<td style='padding:8px;border:1px solid #e5e7eb;'>{html.escape(f['title'])}</td>"
            f"<td style='padding:8px;border:1px solid #e5e7eb;'>{html.escape(f['manager'])}</td>"
            f"<td style='padding:8px;border:1px solid #e5e7eb;text-align:center;'>{html.escape(f['department'])}</td>"
            f"<td style='padding:8px;border:1px solid #e5e7eb;'>{html.escape(f['location'])}</td>"
            "</tr>"
        )

    return (
        f"<div class='piranha-employee-dash' style='font-family:system-ui,sans-serif;'>"
        f"<div style='display:flex;justify-content:space-between;align-items:flex-start;gap:8px;flex-wrap:wrap;'>"
        f"<h2 style='margin:0;font-size:18px;color:#0f172a;'>🐟 Piranha — Employee / Team Lookup</h2>"
        f"<a href='{html.escape(portal)}' target='_blank' rel='noopener' "
        f"style='font-size:11px;color:#2563eb;text-decoration:none;'>Open in Piranha →</a></div>"
        f"<p style='margin:8px 0;font-size:12px;color:#64748b;'>"
        f"EngiHub employees API · {len(all_employees)} total records</p>"
        f"{q_note}"
        f"<div style='overflow-x:auto;'><table style='width:100%;border-collapse:collapse;font-size:12px;'>"
        f"<thead><tr style='background:#1e293b;color:#fff;'>"
        f"<th style='padding:8px;text-align:left;'>Name</th>"
        f"<th style='padding:8px;text-align:left;'>Email</th>"
        f"<th style='padding:8px;text-align:left;'>Team</th>"
        f"<th style='padding:8px;text-align:left;'>Title</th>"
        f"<th style='padding:8px;text-align:left;'>Manager</th>"
        f"<th style='padding:8px;text-align:center;'>Dept</th>"
        f"<th style='padding:8px;text-align:left;'>Location</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div></div>"
    )


def piranha_employee_lookup(query: str = "") -> str:
    """Legacy GocView tool entry."""
    return render_employee_lookup_html(query or "")


def get_piranha_employee_lookup_mcp(
    question: str = "",
    query: str = "",
    *,
    flask_session: dict[str, Any] | None = None,
    force_refresh: bool = False,
) -> str:
    q = (question or query or "").strip()
    return render_employee_lookup_html(q, flask_session=flask_session, force_refresh=force_refresh)


def piranha_auth_status(flask_session: dict[str, Any] | None = None) -> dict[str, Any]:
    from tools.piranha_browser_connect import auto_connect_available
    from tools.piranha_session import cookie_names_hint, cookie_session_connected, server_env_auth_available

    connected_env = server_env_auth_available()
    ok, err = validate_session(flask_session if not connected_env else None)
    return {
        "connected": ok,
        "server_env_configured": connected_env,
        "session_present": cookie_session_connected(flask_session),
        "auto_connect_available": auto_connect_available(),
        "cookie_names": cookie_names_hint(flask_session),
        "portal_url": _portal_url(),
        "employees_api": _employees_api_path(),
        "error": err or None,
    }
