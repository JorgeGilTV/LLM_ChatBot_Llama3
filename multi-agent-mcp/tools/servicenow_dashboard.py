"""
ServiceNow ServiceDesk dashboard — KPIs and charts mirroring the PA dashboard
(https://arlo.service-now.com/.../pa_dashboard.do) via Table/Stats REST API.
"""

from __future__ import annotations

import html
import json
import os
import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

DEFAULT_SNOW_INSTANCE = "https://arlo.service-now.com"
DEFAULT_PA_DASHBOARD_URL = (
    "https://arlo.service-now.com/now/nav/ui/classic/params/target/%24pa_dashboard.do"
)
_HTTP_TIMEOUT = (10, 45)

# ServiceNow priority: 1=Critical (P1), 2=High (P2), 3=Moderate (P3)
_CLOSED_STATES = "6,7"  # Resolved, Closed

# PA dashboard assignment group names (override via .env if needed)
_GROUP_GLOBAL = os.getenv("SNOW_GROUP_GLOBAL", "ServiceDesk - Global")
_GROUP_APAC = os.getenv("SNOW_GROUP_APAC", "ServiceDesk - APAC")
_GROUP_EMEA = os.getenv("SNOW_GROUP_EMEA", "ServiceDesk - EMEA")
_GROUP_ITII = os.getenv("SNOW_GROUP_ITII", "ITII")
_GROUP_AWS = os.getenv("SNOW_GROUP_AWS", "ServiceDesk - AWS Windows SQL")


def _snow_instance() -> str:
    return (os.getenv("SNOW_INSTANCE") or DEFAULT_SNOW_INSTANCE).rstrip("/")


def _snow_dashboard_url() -> str:
    return (os.getenv("SNOW_PA_DASHBOARD_URL") or DEFAULT_PA_DASHBOARD_URL).strip()


def _snow_session(flask_session: dict[str, Any] | None = None) -> requests.Session:
    if flask_session is not None:
        from tools.servicenow_oauth import api_requests_session
        from tools.servicenow_session import api_requests_session_from_cookies

        oauth_sess = api_requests_session(flask_session)
        if oauth_sess is not None:
            return oauth_sess
        cookie_sess = api_requests_session_from_cookies(flask_session)
        if cookie_sess is not None:
            return cookie_sess
    else:
        from tools.servicenow_session import api_requests_session_from_cookies

        cookie_sess = api_requests_session_from_cookies(None)
        if cookie_sess is not None:
            return cookie_sess

    user = (os.getenv("SNOW_USER") or "").strip()
    password = (os.getenv("SNOW_PASSWORD") or "").strip()
    if user and password:
        session = requests.Session()
        session.auth = (user, password)
        session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
        return session

    raise RuntimeError(
        "ServiceNow is not connected. Use «Connect ServiceNow» in the ServiceDesk panel "
        "(session cookie after Okta login) or configure OAuth."
    )


def _snow_auth_hint(status_code: int, body: str) -> str:
    if status_code != 401:
        return ""
    if "not authenticated" in body.lower() or "auth information" in body.lower():
        return (
            " ServiceNow rejected user/password (HTTP 401). With Okta login, "
            "your corporate password does not work for REST API — request OAuth or a technical account from admin."
        )
    return ""


def _snow_api_result(response: requests.Response) -> Any:
    try:
        payload = response.json()
    except Exception:
        return None
    if isinstance(payload, dict):
        return payload.get("result")
    return None


def _snow_stats_bundle(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    if isinstance(result, list):
        for item in result:
            if isinstance(item, dict) and ("stats" in item or "rows" in item):
                return item
        if result and isinstance(result[0], dict):
            return result[0]
    return {}


def _snow_stats_rows(result: Any) -> list[dict[str, Any]]:
    bundle = _snow_stats_bundle(result)
    rows = bundle.get("rows") if isinstance(bundle, dict) else None
    if isinstance(rows, list):
        return [r for r in rows if isinstance(r, dict)]
    if isinstance(result, list):
        return [r for r in result if isinstance(r, dict)]
    return []


def _snow_group_field(item: dict[str, Any]) -> dict[str, Any]:
    raw = item.get("groupby_fields")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        return raw[0]
    return {}


def _snow_table_rows(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, list):
        return [r for r in result if isinstance(r, dict)]
    if isinstance(result, dict):
        return [result]
    return []


def _snow_count(session: requests.Session, table: str, query: str) -> int:
    """Count rows via Table API X-Total-Count (stats API ignores some filters)."""
    url = f"{_snow_instance()}/api/now/table/{table}"
    r = session.get(
        url,
        params={
            "sysparm_query": query,
            "sysparm_limit": "1",
            "sysparm_fields": "sys_id",
        },
        timeout=_HTTP_TIMEOUT,
    )
    if r.status_code != 200:
        hint = _snow_auth_hint(r.status_code, r.text or "")
        raise RuntimeError(
            f"ServiceNow table HTTP {r.status_code}: {(r.text or '')[:200]}.{hint}"
        )
    total_header = r.headers.get("X-Total-Count") or r.headers.get("x-total-count")
    if total_header is not None and str(total_header).isdigit():
        return int(total_header)
    # Some instances omit the header — ask explicitly for count
    cr = session.get(
        url,
        params={
            "sysparm_query": query,
            "sysparm_limit": "1",
            "sysparm_fields": "sys_id",
            "sysparm_count": "true",
        },
        timeout=_HTTP_TIMEOUT,
    )
    if cr.status_code == 200:
        total_header = cr.headers.get("X-Total-Count") or cr.headers.get("x-total-count")
        if total_header is not None and str(total_header).isdigit():
            return int(total_header)
    # Fallback: stats endpoint
    stats_url = f"{_snow_instance()}/api/now/stats/{table}"
    sr = session.get(
        stats_url,
        params={"sysparm_count": "true", "sysparm_query": query},
        timeout=_HTTP_TIMEOUT,
    )
    if sr.status_code != 200:
        return 0
    bundle = _snow_stats_bundle(_snow_api_result(sr))
    stats = bundle.get("stats") if isinstance(bundle, dict) else {}
    if not isinstance(stats, dict):
        stats = {}
    return int(stats.get("count") or 0)


def _snow_group_counts_from_table(
    session: requests.Session,
    table: str,
    query: str,
    group_field: str,
    *,
    limit: int = 12,
    fetch_limit: int = 500,
) -> list[dict[str, Any]]:
    rows = _snow_fetch_table(session, table, query, group_field, limit=fetch_limit)
    tally: dict[str, int] = defaultdict(int)
    for row in rows:
        label = _snow_field_value(row.get(group_field)) or "(empty)"
        tally[str(label)] += 1
    out = [{"label": k, "count": v} for k, v in tally.items() if v]
    out.sort(key=lambda x: -x["count"])
    return out[:limit]


def _snow_group_counts(
    session: requests.Session,
    table: str,
    query: str,
    group_field: str,
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    total = _snow_count(session, table, query)
    if total <= 500:
        return _snow_group_counts_from_table(session, table, query, group_field, limit=limit)

    url = f"{_snow_instance()}/api/now/stats/{table}"
    r = session.get(
        url,
        params={
            "sysparm_count": "true",
            "sysparm_group_by": group_field,
            "sysparm_query": query,
            "sysparm_display_value": "true",
        },
        timeout=_HTTP_TIMEOUT,
    )
    if r.status_code != 200:
        return _snow_group_counts_from_table(session, table, query, group_field, limit=limit)
    rows: list[dict[str, Any]] = []
    for item in _snow_stats_rows(_snow_api_result(r)):
        gf = _snow_group_field(item)
        label = gf.get("display_value") or gf.get("value") or "(empty)"
        if not str(label).strip():
            label = "(empty)"
        stats = item.get("stats") if isinstance(item.get("stats"), dict) else {}
        count = int(stats.get("count") or 0)
        if count:
            rows.append({"label": str(label), "count": count})
    rows.sort(key=lambda x: -x["count"])
    if rows and sum(x["count"] for x in rows) > max(total * 2, total + 100):
        return _snow_group_counts_from_table(session, table, query, group_field, limit=limit)
    return rows[:limit]


def _snow_fetch_table(
    session: requests.Session,
    table: str,
    query: str,
    fields: str,
    *,
    limit: int = 500,
) -> list[dict[str, Any]]:
    url = f"{_snow_instance()}/api/now/table/{table}"
    r = session.get(
        url,
        params={
            "sysparm_query": query,
            "sysparm_fields": fields,
            "sysparm_limit": str(limit),
            "sysparm_display_value": "true",
        },
        timeout=_HTTP_TIMEOUT,
    )
    if r.status_code != 200:
        return []
    return _snow_table_rows(_snow_api_result(r))


def _parse_snow_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    s = str(raw).strip()[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[: len(fmt)], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _snow_field_value(raw: Any) -> str:
    if isinstance(raw, dict):
        return str(raw.get("display_value") or raw.get("value") or "").strip()
    if isinstance(raw, list) and raw:
        return _snow_field_value(raw[0])
    return str(raw or "").strip()


def _group_search_queries(clean: str) -> list[str]:
    """Build sys_user_group lookup queries (exact → specific → fuzzy)."""
    queries = [f"name={clean}"]
    if " - " in clean:
        # Avoid matching "ServiceDesk - Global" when looking for AWS group
        queries.append(f"nameSTARTSWITH{clean}")
    if clean.upper() == "ITII":
        queries.extend(["nameLIKEITII", "name=IT II", "nameSTARTSWITHIT II"])
    queries.append(f"nameLIKE{clean}")
    queries.append(f"nameSTARTSWITH{clean.split()[0]}")
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out


def _lookup_assignment_group_ids(session: requests.Session, names: list[str]) -> dict[str, str]:
    """Resolve sys_user_group.name → sys_id (exact match, then fuzzy fallback)."""
    found: dict[str, str] = {}
    inst = _snow_instance()

    def _scan_rows(rows: list[dict[str, Any]], clean: str) -> str | None:
        clean_l = clean.lower()
        for row in rows:
            row_name = _snow_field_value(row.get("name"))
            sys_id = _snow_field_value(row.get("sys_id"))
            if sys_id and row_name.lower() == clean_l:
                return sys_id
        for row in rows:
            row_name = _snow_field_value(row.get("name"))
            sys_id = _snow_field_value(row.get("sys_id"))
            if sys_id and clean_l in row_name.lower():
                return sys_id
        return None

    for name in names:
        clean = (name or "").strip()
        if not clean or clean in found:
            continue
        for query in _group_search_queries(clean):
            r = session.get(
                f"{inst}/api/now/table/sys_user_group",
                params={
                    "sysparm_query": query,
                    "sysparm_fields": "sys_id,name",
                    "sysparm_limit": "20",
                    "sysparm_display_value": "false",
                },
                timeout=_HTTP_TIMEOUT,
            )
            if r.status_code != 200:
                continue
            sys_id = _scan_rows(_snow_table_rows(_snow_api_result(r)), clean)
            if sys_id:
                found[clean] = sys_id
                break
    return found


def _q_assignment_group(sys_id: str) -> str:
    return f"assignment_group={sys_id}"


def _q_assignment_group_in(sys_ids: list[str]) -> str:
    ids = [i for i in sys_ids if i]
    if not ids:
        return ""
    if len(ids) == 1:
        return _q_assignment_group(ids[0])
    return f"assignment_groupIN{','.join(ids)}"


def _snow_count_try(
    session: requests.Session,
    table: str,
    queries: list[str],
    *,
    prefer_max: bool = False,
    prefer_first_nonzero: bool = False,
) -> tuple[int, str]:
    """Run count queries; return (count, query_used)."""
    results: list[tuple[int, str]] = []
    last_err: Exception | None = None
    for q in queries:
        if not q:
            continue
        try:
            results.append((_snow_count(session, table, q), q))
        except Exception as e:
            last_err = e
    if not results:
        if last_err:
            raise last_err
        return 0, ""
    if prefer_first_nonzero:
        for count, q in results:
            if count > 0:
                return count, q
        return results[0]
    if prefer_max:
        return max(results, key=lambda x: x[0])
    return results[0]


def _open_request_group_names() -> str:
    return ",".join([_GROUP_ITII, _GROUP_AWS, _GROUP_GLOBAL])


def _group_name_or_clause(field: str, names: list[str]) -> str:
    clean = [n.strip() for n in names if (n or "").strip()]
    if not clean:
        return ""
    if len(clean) == 1:
        return f"{field}={clean[0]}"
    return f"({'^OR'.join(f'{field}={n}' for n in clean)})"


def _get_current_user_sys_id(session: requests.Session) -> str:
    """Resolve logged-in user sys_id from session cookie (for 'One of My Groups')."""
    inst = _snow_instance()
    for url in (
        f"{inst}/api/now/ui/user_profile/current_user",
        f"{inst}/api/now/session/whoami",
    ):
        try:
            r = session.get(url, timeout=_HTTP_TIMEOUT)
            if r.status_code != 200:
                continue
            payload = r.json()
            result = payload.get("result") if isinstance(payload, dict) else {}
            if isinstance(result, dict):
                for key in ("user_sys_id", "sys_id", "userId"):
                    val = result.get(key)
                    if val:
                        return str(val).strip()
        except Exception:
            continue

    snow_user = (os.getenv("SNOW_USER") or "").strip()
    if snow_user:
        for field in ("email", "user_name"):
            r = session.get(
                f"{inst}/api/now/table/sys_user",
                params={
                    "sysparm_query": f"{field}={snow_user}",
                    "sysparm_fields": "sys_id",
                    "sysparm_limit": "1",
                },
                timeout=_HTTP_TIMEOUT,
            )
            if r.status_code == 200:
                rows = _snow_table_rows(_snow_api_result(r))
                if rows:
                    sid = _snow_field_value(rows[0].get("sys_id"))
                    if sid:
                        return sid
    return ""


def _get_my_assignment_group_ids(session: requests.Session, user_sys_id: str) -> list[str]:
    """Groups the current user belongs to — ServiceNow 'One of My Groups' filter."""
    if not user_sys_id:
        return []
    inst = _snow_instance()
    r = session.get(
        f"{inst}/api/now/table/sys_user_grmember",
        params={
            "sysparm_query": f"user={user_sys_id}",
            "sysparm_fields": "group",
            "sysparm_limit": "200",
            "sysparm_display_value": "false",
        },
        timeout=_HTTP_TIMEOUT,
    )
    if r.status_code != 200:
        return []
    ids: list[str] = []
    seen: set[str] = set()
    for row in _snow_table_rows(_snow_api_result(r)):
        gid = _snow_field_value(row.get("group"))
        if gid and gid not in seen:
            seen.add(gid)
            ids.append(gid)
    return ids


def _probe_request_counts(
    session: requests.Session,
    probes: list[tuple[str, str]],
) -> dict[str, int | str]:
    out: dict[str, int | str] = {}
    for table, query in probes:
        label = f"{table}|{query[:90]}"
        try:
            out[label] = _snow_count(session, table, query)
        except Exception as exc:
            out[label] = str(exc)[:120]
    return out


def _count_pa_open_requests(
    session: requests.Session,
    *,
    q_by_name: str,
    open_req_ids: list[str],
    req_group_names: list[str],
    my_group_ids: list[str],
) -> tuple[int, str, str, dict[str, int | str]]:
    """PA: Active=true + assignment group IN (ITII, AWS, Global)."""
    static_ids = ",".join(open_req_ids)
    name_or = _group_name_or_clause("assignment_group.name", req_group_names)
    _MAX = 300
    probes: list[tuple[str, str]] = []
    if static_ids:
        grp = f"assignment_groupIN{static_ids}"
        probes.extend([
            ("sc_request", f"active=true^{grp}"),
            ("sc_request", grp),
            ("task", f"active=true^sys_class_name=sc_request^{grp}"),
            ("sc_req_item", f"active=true^request.active=true^{grp}"),
            ("sc_req_item", f"request.active=true^{grp}"),
        ])
    probes.extend([
        ("sc_request", q_by_name),
        ("sc_request", f"active=true^{name_or}" if name_or else q_by_name),
    ])
    debug = _probe_request_counts(session, probes)

    # Prefer sc_request → task → sc_req_item; accept only sane counts (108-ish, not 2592)
    table_priority = {"sc_request": 0, "task": 1, "sc_req_item": 2}
    best: tuple[int, str, str] | None = None
    for table, query in probes:
        key = f"{table}|{query[:90]}"
        val = debug.get(key)
        if not isinstance(val, int) or val <= 0 or val > _MAX:
            continue
        if best is None or table_priority.get(table, 9) < table_priority.get(best[2], 9):
            best = (val, query, table)
    if best:
        return best[0], best[1], best[2], debug

    # Fallback: distinct parent requests from line items (assignment group on item)
    if static_ids:
        q_items = f"active=true^request.active=true^assignment_groupIN{static_ids}"
        try:
            item_total = _snow_count(session, "sc_req_item", q_items)
            if 0 < item_total <= _MAX:
                rows = _snow_fetch_table(
                    session, "sc_req_item", q_items, "request", limit=min(item_total, 800)
                )
                distinct = {
                    _snow_field_value(r.get("request"))
                    for r in rows
                    if _snow_field_value(r.get("request"))
                }
                if distinct:
                    debug[f"distinct_requests|{q_items[:60]}"] = len(distinct)
                    return len(distinct), q_items, "sc_req_item", debug
        except RuntimeError:
            pass

    return 0, probes[0][1] if probes else q_by_name, "sc_request", debug


def _count_pa_unassigned_requests(
    session: requests.Session,
    *,
    global_id: str,
    q_by_name: str,
) -> tuple[int, str, str, dict[str, int | str]]:
    """PA unassigned: Assigned To empty + Active + State != Closed Complete + Global."""
    gname = _GROUP_GLOBAL
    _MAX = 50
    sc_base = f"active=true^assigned_toISEMPTY^assignment_group={global_id}"
    item_base = f"active=true^request.active=true^assigned_toISEMPTY^assignment_group={global_id}"
    candidates: list[tuple[str, str]] = [
        ("sc_request", f"{sc_base}^state!=3"),
        ("sc_request", f"{sc_base}^state!=closed_complete"),
        ("sc_request", f"{sc_base}^request_state!=3"),
        ("sc_request", f"{sc_base}^request_state!=closed_complete"),
        ("sc_request", q_by_name),
        ("sc_request", f"active=true^assigned_toISEMPTY^state!=closed_complete^assignment_group.name={gname}"),
        ("sc_request", sc_base),
        ("sc_req_item", f"{item_base}^state!=3"),
        ("sc_req_item", f"{item_base}^state!=closed_complete"),
        ("sc_req_item", f"{item_base}^request_state!=3"),
        ("sc_req_item", item_base),
        ("task", f"active=true^sys_class_name=sc_request^assigned_toISEMPTY^assignment_group={global_id}^state!=3"),
    ]
    debug = _probe_request_counts(session, candidates)
    table_priority = {"sc_request": 0, "task": 1, "sc_req_item": 2}
    best: tuple[int, str, str] | None = None
    for table, query in candidates:
        key = f"{table}|{query[:90]}"
        val = debug.get(key)
        if not isinstance(val, int) or val <= 0 or val > _MAX:
            continue
        if best is None or table_priority.get(table, 9) < table_priority.get(best[2], 9):
            best = (val, query, table)
    if best:
        return best[0], best[1], best[2], debug

    # Fallback: distinct parent requests from unassigned line items (same pattern as open requests)
    for q_items in (
        f"{item_base}^state!=3",
        f"{item_base}^state!=closed_complete",
        item_base,
    ):
        try:
            item_total = _snow_count(session, "sc_req_item", q_items)
            if item_total <= 0 or item_total > _MAX * 3:
                continue
            rows = _snow_fetch_table(
                session, "sc_req_item", q_items, "request", limit=min(item_total, 200)
            )
            distinct = {
                _snow_field_value(r.get("request"))
                for r in rows
                if _snow_field_value(r.get("request"))
            }
            if distinct:
                debug[f"distinct_unassigned|{q_items[:60]}"] = len(distinct)
                return len(distinct), q_items, "sc_req_item", debug
        except RuntimeError:
            continue

    first_table, first_q = candidates[0]
    first_key = f"{first_table}|{first_q[:90]}"
    first_val = debug.get(first_key)
    return (int(first_val) if isinstance(first_val, int) else 0), first_q, first_table, debug


def _discover_assignment_groups(session: requests.Session, keyword: str) -> dict[str, str]:
    """Find sys_user_group rows whose name contains keyword."""
    inst = _snow_instance()
    r = session.get(
        f"{inst}/api/now/table/sys_user_group",
        params={
            "sysparm_query": f"nameLIKE{keyword}",
            "sysparm_fields": "sys_id,name",
            "sysparm_limit": "30",
            "sysparm_display_value": "false",
        },
        timeout=_HTTP_TIMEOUT,
    )
    if r.status_code != 200:
        return {}
    out: dict[str, str] = {}
    for row in _snow_table_rows(_snow_api_result(r)):
        name = _snow_field_value(row.get("name"))
        sys_id = _snow_field_value(row.get("sys_id"))
        if name and sys_id:
            out[name] = sys_id
    return out


def _ensure_open_request_groups(session: requests.Session, groups: dict[str, str]) -> dict[str, str]:
    """Fill missing ITII / AWS group sys_ids via keyword discovery."""
    out = dict(groups)
    missing = [n for n in (_GROUP_ITII, _GROUP_AWS) if not out.get(n)]
    if not missing:
        return out
    for keyword in ("ITII", "AWS Windows SQL", "ServiceDesk - AWS"):
        for name, sys_id in _discover_assignment_groups(session, keyword).items():
            if _GROUP_ITII in missing and "ITII" in name.upper() and not out.get(_GROUP_ITII):
                out[_GROUP_ITII] = sys_id
            if _GROUP_AWS in missing and "AWS" in name and "Windows" in name and not out.get(_GROUP_AWS):
                out[_GROUP_AWS] = sys_id
    return out


def _snow_normalize_assignee_breakdown(
    rows: list[dict[str, Any]], total: int
) -> list[dict[str, Any]]:
    """Ensure donut slices sum to total (add Other / empty bucket if needed)."""
    if total <= 0:
        return rows
    shown = sum(int(x.get("count") or 0) for x in rows)
    if shown >= total:
        return rows
    gap = total - shown
    for row in rows:
        if row.get("label") in ("(empty)", "(other)"):
            row["count"] = int(row.get("count") or 0) + gap
            return rows
    rows.append({"label": "(other)", "count": gap})
    return rows


def _priority_incident_groups(groups: dict[str, str]) -> list[tuple[str, str]]:
    """PA Open Priority Incidents: APAC, EMEA, Global assignment groups."""
    out: list[tuple[str, str]] = []
    for name in (_GROUP_APAC, _GROUP_EMEA, _GROUP_GLOBAL):
        sys_id = groups.get(name)
        if sys_id:
            out.append((name, sys_id))
    return out


def _count_incident_level_for_groups(
    session: requests.Session,
    group_entries: list[tuple[str, str]],
    level: int,
    field: str,
) -> tuple[int, dict[str, int]]:
    """Sum incident counts per assignment group (matches PA OR filter without IN quirks)."""
    per_group: dict[str, int] = {}
    total = 0
    for name, sys_id in group_entries:
        query = f"active=true^assignment_group={sys_id}^{field}={level}"
        try:
            count = _snow_count(session, "incident", query)
        except RuntimeError:
            count = 0
        per_group[name] = count
        total += count
    return total, per_group


def _count_incident_level_by_group_names(
    session: requests.Session,
    names: list[str],
    level: int,
    field: str,
) -> tuple[int, dict[str, int]]:
    per_group: dict[str, int] = {}
    total = 0
    for name in names:
        query = f"active=true^assignment_group.name={name}^{field}={level}"
        try:
            count = _snow_count(session, "incident", query)
        except RuntimeError:
            count = 0
        per_group[name] = count
        total += count
    return total, per_group


def _pick_pa_priority_field(
    session: requests.Session,
    group_entries: list[tuple[str, str]],
    *,
    probe_level: int = 2,
) -> str:
    """PA P2 widget filters on Urgency; use urgency when it returns data."""
    urg, _ = _count_incident_level_for_groups(session, group_entries, probe_level, "urgency")
    if urg > 0:
        return "urgency"
    pri, _ = _count_incident_level_for_groups(session, group_entries, probe_level, "priority")
    if pri > 0:
        return "priority"
    return "urgency"


def _count_pa_p_levels(
    session: requests.Session, groups: dict[str, str]
) -> tuple[dict[str, int], dict[str, str], dict[str, Any]]:
    """
    P1/P2/P3 — Active + (APAC | EMEA | Global) + Urgency 1/2/3 (PA filter builder).
    Counts are summed per group to mirror PA OR logic reliably.
    """
    group_entries = _priority_incident_groups(groups)
    group_names = [_GROUP_APAC, _GROUP_EMEA, _GROUP_GLOBAL]
    debug: dict[str, Any] = {"groups_resolved": [n for n, _ in group_entries]}

    if not group_entries:
        global_id = groups.get(_GROUP_GLOBAL)
        if global_id:
            group_entries = [(_GROUP_GLOBAL, global_id)]
            debug["groups_resolved"] = [_GROUP_GLOBAL]

    field = _pick_pa_priority_field(session, group_entries)
    debug["field"] = field

    levels: dict[str, int] = {}
    fields_used: dict[str, str] = {}
    per_level: dict[str, dict[str, int]] = {}

    for key, level in (("open_p1", 1), ("open_p2", 2), ("open_p3", 3)):
        total, per_group = _count_incident_level_for_groups(
            session, group_entries, level, field
        )
        if total == 0 and group_entries:
            name_total, name_per = _count_incident_level_by_group_names(
                session, group_names, level, field
            )
            if name_total > total:
                total, per_group = name_total, name_per
                debug[f"{key}_via"] = "assignment_group.name"
        levels[key] = total
        fields_used[key] = field
        per_level[key] = per_group

    debug["per_level"] = per_level
    return levels, fields_used, debug


def _build_pa_queries(session: requests.Session) -> dict[str, str]:
    """Build encoded queries using assignment group sys_ids (matches PA dashboard lists)."""
    group_names = [_GROUP_GLOBAL, _GROUP_APAC, _GROUP_EMEA, _GROUP_ITII, _GROUP_AWS]
    groups = _ensure_open_request_groups(session, _lookup_assignment_group_ids(session, group_names))

    global_id = groups.get(_GROUP_GLOBAL)
    apac_id = groups.get(_GROUP_APAC)
    emea_id = groups.get(_GROUP_EMEA)
    itii_id = groups.get(_GROUP_ITII)
    aws_id = groups.get(_GROUP_AWS)

    if not global_id:
        raise RuntimeError(
            f"Assignment group «{_GROUP_GLOBAL}» was not found in ServiceNow. "
            "Check SNOW_GROUP_GLOBAL in .env."
        )

    incidents_total = f"active=true^assignment_group={global_id}"

    inc_priority_ids = [x for x in (apac_id, emea_id, global_id) if x]
    if inc_priority_ids:
        # PA P1/P2/P3: Active + Assignment group (APAC | EMEA | Global) + Urgency
        incidents_priority_base = f"active=true^assignment_groupIN{','.join(inc_priority_ids)}"
    else:
        incidents_priority_base = incidents_total

    inc_unassigned_ids = [x for x in (apac_id, emea_id, global_id) if x]
    if inc_unassigned_ids:
        # NQ = OR branch in ServiceNow encoded query
        unassigned_incidents = (
            f"active=true^assigned_toISEMPTY^assignment_groupIN{','.join(inc_unassigned_ids)}"
            f"^NQactive=true^assigned_toISEMPTY^assignment_groupISEMPTY"
        )
    else:
        unassigned_incidents = f"active=true^assigned_toISEMPTY^assignment_group={global_id}"

    req_group_names = _open_request_group_names()
    user_sys_id = _get_current_user_sys_id(session)
    my_group_ids = _get_my_assignment_group_ids(session, user_sys_id)

    # Open requests: PA groups ITII + AWS + Global (matches ~108 in dashboard)
    open_req_static_ids = [x for x in (itii_id, aws_id, global_id) if x]
    if open_req_static_ids:
        open_requests = f"active=true^assignment_groupIN{','.join(open_req_static_ids)}"
    else:
        open_requests = f"active=true^assignment_group.nameIN{req_group_names}"

    # Unassigned requests: 4 conditions from PA filter builder
    unassigned_requests = (
        f"active=true^assigned_toISEMPTY^assignment_group={global_id}^state!=3"
    )
    unassigned_requests_by_name = unassigned_requests

    return {
        "incidents_total": incidents_total,
        "incidents_priority_base": incidents_priority_base,
        "unassigned_incidents": unassigned_incidents,
        "open_requests": open_requests,
        "open_requests_by_name": f"active=true^assignment_group.nameIN{req_group_names}",
        "open_requests_mode": "pa_static_groups",
        "my_group_ids": my_group_ids,
        "current_user_sys_id": user_sys_id,
        "unassigned_requests": unassigned_requests,
        "unassigned_requests_by_name": unassigned_requests_by_name,
        "closed_incidents": f"stateIN{_CLOSED_STATES}^assignment_group={global_id}",
        "group_ids": groups,
    }


def fetch_servicedesk_dashboard_data(flask_session: dict[str, Any] | None = None) -> dict[str, Any]:
    """Aggregate ServiceDesk KPIs for home widget and MCP tool."""
    session = _snow_session(flask_session)
    inst = _snow_instance()
    queries = _build_pa_queries(session)
    q_inc = queries["incidents_total"]
    q_inc_p = queries.get("incidents_priority_base") or q_inc
    q_unasg_inc = queries["unassigned_incidents"]
    q_open_req = queries["open_requests"]
    q_open_req_names = queries.get("open_requests_by_name", "")
    q_unasg_req = queries["unassigned_requests"]
    q_unasg_req_names = queries.get("unassigned_requests_by_name", "")

    unassigned_inc = _snow_count(session, "incident", q_unasg_inc)
    group_ids = queries["group_ids"]
    p_levels, p_fields, p_debug = _count_pa_p_levels(session, group_ids)
    open_p1 = p_levels["open_p1"]
    open_p2 = p_levels["open_p2"]
    open_p3 = p_levels["open_p3"]
    open_inc_total = _snow_count(session, "incident", q_inc)

    open_req_ids = [
        group_ids.get(n, "") for n in (_GROUP_ITII, _GROUP_AWS, _GROUP_GLOBAL)
    ]
    open_req_ids = [x for x in open_req_ids if x]

    my_group_ids = queries.get("my_group_ids") or []

    open_req_total, req_query_for_groups, req_table, open_debug = _count_pa_open_requests(
        session,
        q_by_name=q_open_req,
        open_req_ids=open_req_ids,
        req_group_names=[_GROUP_ITII, _GROUP_AWS, _GROUP_GLOBAL],
        my_group_ids=my_group_ids,
    )

    global_id = queries["group_ids"].get(_GROUP_GLOBAL, "")
    unassigned_req, unasg_query_used, unasg_table, unasg_debug = _count_pa_unassigned_requests(
        session,
        global_id=global_id,
        q_by_name=q_unasg_req,
    )

    inc_by_assignee = _snow_group_counts(session, "incident", q_inc, "assigned_to", limit=8)
    req_by_assignee = _snow_group_counts(
        session,
        req_table,
        req_query_for_groups or q_open_req,
        "assigned_to",
        limit=8,
    )
    inc_by_assignee = _snow_normalize_assignee_breakdown(inc_by_assignee, open_inc_total)
    req_by_assignee = _snow_normalize_assignee_breakdown(req_by_assignee, open_req_total)

    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    closed_rows = _snow_fetch_table(
        session,
        "incident",
        f"{queries['closed_incidents']}^closed_at>={since}",
        "closed_at,assigned_to,subcategory",
        limit=800,
    )

    closed_by_day: dict[str, int] = defaultdict(int)
    closed_by_day_assignee: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in closed_rows:
        closed_raw = _snow_field_value(row.get("closed_at"))
        dt = _parse_snow_datetime(closed_raw or None)
        if not dt:
            continue
        day_key = dt.strftime("%Y-%m-%d")
        assignee = _snow_field_value(row.get("assigned_to")) or "(empty)"
        closed_by_day[day_key] += 1
        closed_by_day_assignee[day_key][assignee] += 1

    day_labels = sorted(closed_by_day.keys())[-7:]
    closed_series = [closed_by_day.get(d, 0) for d in day_labels]

    top_assignees = sorted(
        {a for day in closed_by_day_assignee.values() for a in day},
        key=lambda a: -sum(closed_by_day_assignee[d].get(a, 0) for d in day_labels),
    )[:5]
    stacked_closed: list[dict[str, Any]] = []
    palette = ["#3b82f6", "#f97316", "#a855f7", "#22c55e", "#eab308", "#64748b"]
    for i, name in enumerate(top_assignees):
        stacked_closed.append(
            {
                "label": name,
                "data": [closed_by_day_assignee[d].get(name, 0) for d in day_labels],
                "color": palette[i % len(palette)],
            }
        )

    filters: dict[str, Any] = {
        **queries,
        "open_requests_table": req_table,
        "open_requests_query_used": req_query_for_groups,
        "open_requests_probe": open_debug,
        "unassigned_requests_table": unasg_table,
        "unassigned_requests_query_used": unasg_query_used,
        "unassigned_requests_probe": unasg_debug,
        "p_levels_query_base": q_inc_p,
        "p_levels_fields": p_fields,
        "p_levels_debug": p_debug,
    }

    return {
        "success": True,
        "instance": inst,
        "dashboard_url": _snow_dashboard_url(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "kpis": {
            "unassigned_incidents": unassigned_inc,
            "open_p1": open_p1,
            "open_p2": open_p2,
            "open_p3": open_p3,
            "open_incidents_total": open_inc_total,
            "open_requests_total": open_req_total,
            "unassigned_requests": unassigned_req,
        },
        "open_incidents_by_assignee": inc_by_assignee,
        "open_requests_by_assignee": req_by_assignee,
        "closed_incidents_7d": {
            "labels": day_labels,
            "totals": closed_series,
            "by_assignee": stacked_closed,
        },
        "filters": filters,
    }


def _snow_reconnect_payload(auth: dict[str, Any], error: str) -> dict[str, Any]:
    from tools.servicenow_oauth import oauth_configured

    stale_auth = dict(auth)
    stale_auth["connected"] = False
    stale_auth["message"] = error
    if auth.get("method") in ("cookie", "env", None):
        stale_auth["manual_login"] = True
    else:
        stale_auth["manual_login"] = not oauth_configured()
    return {
        "success": False,
        "authenticated": False,
        "auth": stale_auth,
        "login_url": auth.get("login_path") or "/oauth/snow/login",
        "error": error,
        "dashboard_url": _snow_dashboard_url(),
        "kpis": {},
    }


def _snow_session_probe_target(flask_session: dict[str, Any] | None) -> dict[str, Any] | None:
    from tools.servicenow_oauth import get_token_bundle
    from tools.servicenow_session import cookie_session_connected, server_env_auth_available

    if get_token_bundle(flask_session or {}):
        return None
    if server_env_auth_available():
        return None
    if cookie_session_connected(flask_session):
        return flask_session
    return flask_session


def servicedesk_dashboard_payload(flask_session: dict[str, Any] | None = None) -> dict[str, Any]:
    from tools.servicenow_oauth import auth_status, get_token_bundle
    from tools.servicenow_session import server_env_auth_available, validate_session

    session_dict = flask_session if flask_session is not None else {}
    auth = auth_status(session_dict)
    if not auth.get("connected") and not server_env_auth_available():
        return {
            "success": False,
            "authenticated": False,
            "auth": auth,
            "login_url": auth.get("login_path") or "/oauth/snow/login",
            "error": auth.get("message")
            or "Connect ServiceNow with Okta to view the dashboard.",
            "dashboard_url": _snow_dashboard_url(),
            "kpis": {},
        }

    if auth.get("connected") and not get_token_bundle(session_dict):
        ok, err = validate_session(_snow_session_probe_target(flask_session))
        if not ok:
            return _snow_reconnect_payload(
                auth,
                err or "ServiceNow session expired. Reconnect to view KPIs.",
            )

    try:
        data = fetch_servicedesk_dashboard_data(flask_session)
        data["authenticated"] = True
        data["auth"] = auth
        return data
    except Exception as e:
        msg = str(e)
        if any(
            token in msg.lower()
            for token in ("401", "session expired", "not connected", "not authenticated")
        ):
            return _snow_reconnect_payload(auth, msg)
        return {
            "success": False,
            "authenticated": True,
            "auth": auth,
            "error": msg,
            "dashboard_url": _snow_dashboard_url(),
            "kpis": {},
        }


def _chartjs_script(charts: dict[str, Any]) -> str:
    uid = uuid.uuid4().hex[:8]
    data_json = json.dumps(charts)
    return f"""<script>
(function() {{
  const specs = {data_json};
  function render() {{
    if (typeof Chart === 'undefined') return false;
    Object.entries(specs).forEach(([id, spec]) => {{
      const canvas = document.getElementById(id);
      if (!canvas) return;
      const existing = Chart.getChart(canvas);
      if (existing) existing.destroy();
      const ctype = spec.type || 'bar';
      const ds = (spec.datasets || []).map(d => ({{
        label: d.label,
        data: d.data,
        backgroundColor: d.color + (ctype === 'line' ? '44' : 'cc'),
        borderColor: d.color,
        borderWidth: ctype === 'doughnut' ? 1 : 2,
        tension: 0.3,
        fill: ctype === 'line',
      }}));
      new Chart(canvas, {{
        type: ctype,
        data: {{ labels: spec.labels || [], datasets: ds }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          plugins: {{
            legend: {{ display: !!spec.legend, position: 'bottom', labels: {{ boxWidth: 10, font: {{ size: 9 }} }} }},
          }},
          scales: ctype === 'doughnut' ? {{}} : {{
            x: {{ ticks: {{ font: {{ size: 9 }}, maxRotation: 45 }}, grid: {{ display: false }} }},
            y: {{ beginAtZero: true, ticks: {{ font: {{ size: 9 }} }}, grid: {{ color: '#f1f5f9' }} }},
          }},
        }},
      }});
    }});
    return true;
  }}
  if (!render()) {{
    const iv = setInterval(() => {{ if (render()) clearInterval(iv); }}, 250);
    setTimeout(() => clearInterval(iv), 12000);
  }}
}})();
</script>"""


def render_servicedesk_dashboard_html(
    *, compact: bool = False, flask_session: dict[str, Any] | None = None
) -> str:
    payload = servicedesk_dashboard_payload(flask_session)
    if not payload.get("authenticated"):
        auth = payload.get("auth") or {}
        inst = html.escape(str(auth.get("instance") or _snow_instance()))
        msg = html.escape(str(payload.get("error") or "Connect ServiceNow."))
        if auth.get("manual_login"):
            return (
                f"<div style='font-family:system-ui,sans-serif;padding:12px;border:1px solid #e2e8f0;"
                f"border-radius:8px;background:#f8fafc;'>"
                f"<p style='margin:0 0 8px;color:#334155;'>{msg}</p>"
                f"<ol style='margin:0 0 10px;padding-left:18px;color:#475569;font-size:13px;line-height:1.5;'>"
                f"<li><a href='{inst}' target='_blank' rel='noopener'>Open ServiceNow</a> and sign in with Okta</li>"
                f"<li>Develop → Web Inspector → Storage → Cookies → copy <code>glide_session_store</code></li>"
                f"<li>Paste in the GocView ServiceDesk widget</li></ol>"
                f"<p style='margin:0;color:#64748b;font-size:12px;'>Does not require Application Registry or admin OAuth.</p>"
                f"</div>"
            )
        login = html.escape(payload.get("login_url") or "/oauth/snow/login")
        return (
            f"<div style='font-family:system-ui,sans-serif;padding:12px;border:1px solid #e2e8f0;"
            f"border-radius:8px;background:#f8fafc;'>"
            f"<p style='margin:0 0 10px;color:#334155;'>{msg}</p>"
            f"<a href='{login}' style='display:inline-block;padding:8px 14px;background:#2563eb;"
            f"color:#fff;border-radius:6px;text-decoration:none;font-weight:600;'>"
            f"Connect ServiceNow (Okta)</a></div>"
        )
    if not payload.get("success"):
        return _render_dashboard_html(payload, compact=compact)
    return _render_dashboard_html(payload, compact=compact)


def _render_dashboard_html(data: dict[str, Any], *, compact: bool = False) -> str:
    if not data.get("success"):
        err = html.escape(str(data.get("error") or "Unknown error"))
        url = html.escape(data.get("dashboard_url") or _snow_dashboard_url())
        return (
            f"<p style='color:#dc2626;'><strong>ServiceNow dashboard error:</strong> {err}</p>"
            f"<p><a href='{url}' target='_blank' rel='noopener'>Open ServiceDesk dashboard in ServiceNow →</a></p>"
        )

    kpis = data.get("kpis") or {}
    uid = uuid.uuid4().hex[:8]
    dash_url = html.escape(data.get("dashboard_url") or _snow_dashboard_url())
    charts: dict[str, Any] = {}

    kpi_cards = [
        ("Unassigned Incidents", kpis.get("unassigned_incidents", 0), "#f97316"),
        ("Open P1", kpis.get("open_p1", 0), "#dc2626"),
        ("Open P2", kpis.get("open_p2", 0), "#ea580c"),
        ("Open P3", kpis.get("open_p3", 0), "#ca8a04"),
        ("Open Incidents", kpis.get("open_incidents_total", 0), "#2563eb"),
        ("Open Requests", kpis.get("open_requests_total", 0), "#0891b2"),
        ("Unassigned Requests", kpis.get("unassigned_requests", 0), "#16a34a"),
    ]

    kpi_html = "".join(
        f"<div style='background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:"
        f"{'8px 10px' if compact else '12px 14px'};text-align:center;min-width:0;'>"
        f"<div style='font-size:{'18px' if compact else '24px'};font-weight:700;color:{c};'>"
        f"{html.escape(str(v))}</div>"
        f"<div style='font-size:{'9px' if compact else '11px'};color:#64748b;margin-top:2px;line-height:1.2;'>"
        f"{html.escape(label)}</div></div>"
        for label, v, c in kpi_cards
    )

    inc_assignee = data.get("open_incidents_by_assignee") or []
    req_assignee = data.get("open_requests_by_assignee") or []
    closed = data.get("closed_incidents_7d") or {}

    inc_donut_id = f"snow_inc_donut_{uid}"
    req_donut_id = f"snow_req_donut_{uid}"
    closed_bar_id = f"snow_closed_bar_{uid}"

    if inc_assignee:
        charts[inc_donut_id] = {
            "type": "doughnut",
            "legend": True,
            "labels": [x["label"] for x in inc_assignee],
            "datasets": [
                {
                    "label": "Open Incidents",
                    "data": [x["count"] for x in inc_assignee],
                    "color": "#3b82f6",
                }
            ],
        }
    if req_assignee:
        charts[req_donut_id] = {
            "type": "doughnut",
            "legend": True,
            "labels": [x["label"] for x in req_assignee],
            "datasets": [
                {
                    "label": "Open Requests",
                    "data": [x["count"] for x in req_assignee],
                    "color": "#0891b2",
                }
            ],
        }
    if closed.get("labels"):
        charts[closed_bar_id] = {
            "type": "bar",
            "legend": len(closed.get("by_assignee") or []) > 1,
            "labels": closed.get("labels") or [],
            "datasets": closed.get("by_assignee")
            or [
                {
                    "label": "Closed",
                    "data": closed.get("totals") or [],
                    "color": "#6366f1",
                }
            ],
        }

    chart_h = "140px" if compact else "220px"
    charts_row = ""
    if inc_assignee or req_assignee or closed.get("labels"):
        panels: list[str] = []
        if inc_assignee:
            panels.append(
                f"<div style='flex:1;min-width:160px;background:#fff;border:1px solid #e2e8f0;"
                f"border-radius:8px;padding:8px;'><div style='font-size:11px;font-weight:600;"
                f"color:#334155;margin-bottom:4px;'>Open Incidents — Total</div>"
                f"<div style='position:relative;height:{chart_h};'>"
                f"<canvas id='{inc_donut_id}'></canvas></div></div>"
            )
        if req_assignee:
            panels.append(
                f"<div style='flex:1;min-width:160px;background:#fff;border:1px solid #e2e8f0;"
                f"border-radius:8px;padding:8px;'><div style='font-size:11px;font-weight:600;"
                f"color:#334155;margin-bottom:4px;'>Open Requests</div>"
                f"<div style='position:relative;height:{chart_h};'>"
                f"<canvas id='{req_donut_id}'></canvas></div></div>"
            )
        if closed.get("labels"):
            panels.append(
                f"<div style='flex:2;min-width:200px;background:#fff;border:1px solid #e2e8f0;"
                f"border-radius:8px;padding:8px;'><div style='font-size:11px;font-weight:600;"
                f"color:#334155;margin-bottom:4px;'>Closed Incidents (Last 7 days)</div>"
                f"<div style='position:relative;height:{chart_h};'>"
                f"<canvas id='{closed_bar_id}'></canvas></div></div>"
            )
        charts_row = (
            f"<div style='display:flex;flex-wrap:wrap;gap:8px;margin-top:10px;'>{''.join(panels)}</div>"
        )

    title_size = "14px" if compact else "18px"
    grid_cols = "repeat(auto-fit,minmax(72px,1fr))" if compact else "repeat(auto-fit,minmax(100px,1fr))"

    return (
        f"<div class='snow-servicedesk-dash' style='font-family:system-ui,sans-serif;'>"
        f"<div style='display:flex;justify-content:space-between;align-items:flex-start;"
        f"gap:8px;margin-bottom:8px;flex-wrap:wrap;'>"
        f"<h2 style='margin:0;font-size:{title_size};color:#0f172a;'>🎫 ServiceDesk Dashboard</h2>"
        f"<a href='{dash_url}' target='_blank' rel='noopener' "
        f"style='font-size:11px;color:#2563eb;text-decoration:none;white-space:nowrap;'>"
        f"Open in ServiceNow →</a></div>"
        f"<div style='display:grid;grid-template-columns:{grid_cols};gap:6px;'>{kpi_html}</div>"
        f"{charts_row}"
        f"{_chartjs_script(charts) if charts else ''}"
        f"</div>"
    )


def get_servicedesk_dashboard_mcp(
    question: str = "",
    query: str = "",
    flask_session: dict[str, Any] | None = None,
) -> str:
    """MCP entry: ServiceDesk KPIs + Chart.js graphs from ServiceNow REST API."""
    _ = (question or query or "").strip()
    return render_servicedesk_dashboard_html(compact=False, flask_session=flask_session)
