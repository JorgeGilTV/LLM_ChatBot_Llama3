"""
Per-shift NOC filter and shared PagerDuty incident helpers for query tools.
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import requests

ROOT_CAUSE_FIELD_NAME = "root_cause"
DEFAULT_INCIDENTS_LOOKBACK_DAYS = 15

PAGERDUTY_SHIFTS = ("shift1", "shift2", "shift3")

DEFAULT_SHIFT_LABELS = {
    "shift1": "Shift 1 — Mexico",
    "shift2": "Shift 2",
    "shift3": "Shift 3",
}

_PD_TEAM_TOUCH_LOG_TYPES = frozenset(
    {
        "acknowledge_log_entry",
        "assign_log_entry",
        "resolve_log_entry",
        "annotate_log_entry",
        "delegate_log_entry",
        "escalate_log_entry",
        "responder_request_log_entry",
        "custom_field_value_change_log_entry",
    }
)


def pagerduty_incidents_lookback_days() -> int:
    raw = (os.getenv("PAGERDUTY_INCIDENTS_LOOKBACK_DAYS") or str(DEFAULT_INCIDENTS_LOOKBACK_DAYS)).strip()
    try:
        return max(1, min(90, int(raw)))
    except ValueError:
        return DEFAULT_INCIDENTS_LOOKBACK_DAYS


def normalize_pagerduty_shift(shift: str | None) -> str:
    """Return shift1|shift2|shift3 or empty string."""
    s = (shift or "").strip().lower().replace(" ", "")
    aliases = {
        "1": "shift1",
        "2": "shift2",
        "3": "shift3",
        "mexico": "shift1",
        "shift1mexico": "shift1",
    }
    s = aliases.get(s, s)
    return s if s in PAGERDUTY_SHIFTS else ""


def _parse_id_list(raw: str) -> list[str]:
    ids: list[str] = []
    for part in (raw or "").split(","):
        uid = part.strip()
        if uid and uid not in ids:
            ids.append(uid)
    return ids


def pagerduty_shift_user_ids(shift: str) -> list[str]:
    """PagerDuty user IDs for one shift (PAGERDUTY_SHIFT1_USER_IDS, etc.)."""
    mode = normalize_pagerduty_shift(shift)
    if not mode:
        return []
    env_key = f"PAGERDUTY_{mode.upper()}_USER_IDS"
    raw = (os.getenv(env_key) or "").strip()
    if not raw and mode == "shift1":
        raw = (os.getenv("PAGERDUTY_TEAM_USER_IDS") or "").strip()
    return _parse_id_list(raw)


def pagerduty_shift_label(shift: str) -> str:
    mode = normalize_pagerduty_shift(shift)
    if not mode:
        return ""
    env_key = f"PAGERDUTY_{mode.upper()}_LABEL"
    custom = (os.getenv(env_key) or "").strip()
    if custom:
        return custom
    return DEFAULT_SHIFT_LABELS.get(mode, mode)


def pagerduty_team_user_ids() -> list[str]:
    """Union of all configured shift user IDs (legacy PAGERDUTY_TEAM_USER_IDS → shift1)."""
    merged: list[str] = []
    for mode in PAGERDUTY_SHIFTS:
        for uid in pagerduty_shift_user_ids(mode):
            if uid not in merged:
                merged.append(uid)
    return merged


def pagerduty_team_label() -> str:
    return (os.getenv("PAGERDUTY_TEAM_LABEL") or "NOC team").strip() or "NOC team"


def pagerduty_user_ids_for_filter(shift: str | None = None, team_only: bool = False) -> list[str]:
    """
    IDs to use when filtering incidents.
    - shift set → that shift's crew only
    - team_only (legacy) → union of all shifts
    - otherwise → no filter (empty list)
    """
    mode = normalize_pagerduty_shift(shift)
    if mode:
        return pagerduty_shift_user_ids(mode)
    if team_only:
        return pagerduty_team_user_ids()
    return []


def _agent_is_team_member(agent: dict | None, team_user_ids: set[str]) -> bool:
    if not agent or not team_user_ids:
        return False
    aid = (agent.get("id") or "").strip()
    return bool(aid and aid in team_user_ids)


def _pd_headers(api_token: str) -> dict:
    return {
        "Authorization": f"Token token={api_token}",
        "Accept": "application/vnd.pagerduty+json;version=2",
    }


def fetch_all_incidents(api_token: str, days: int | None = None) -> list[dict]:
    """All account incidents in the last `days` (triggered, acknowledged, resolved)."""
    if not api_token:
        return []
    if days is None:
        days = pagerduty_incidents_lookback_days()
    headers = _pd_headers(api_token)
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    all_incidents: list[dict] = []
    offset = 0
    limit = 100
    while offset < 10000:
        response = requests.get(
            "https://api.pagerduty.com/incidents",
            headers=headers,
            params={
                "sort_by": "created_at:desc",
                "limit": limit,
                "offset": offset,
                "statuses[]": ["triggered", "acknowledged", "resolved"],
                "since": since,
            },
            timeout=(12, 45),
        )
        if response.status_code != 200:
            break
        data = response.json()
        batch = data.get("incidents") or []
        all_incidents.extend(batch)
        if not data.get("more") or not batch:
            break
        offset += limit
    return all_incidents


def enrich_incidents_custom_fields(api_token: str, incidents: list[dict]) -> list[dict]:
    """Attach custom_fields to each incident (needed for root cause)."""
    if not api_token or not incidents:
        return incidents
    headers = _pd_headers(api_token)
    by_id = {i.get("id"): dict(i) for i in incidents if i.get("id")}

    def _fetch_one(iid: str) -> tuple[str, list]:
        r = requests.get(
            f"https://api.pagerduty.com/incidents/{iid}",
            headers=headers,
            params={"include[]": ["custom_fields"]},
            timeout=(12, 30),
        )
        if r.status_code != 200:
            return iid, []
        return iid, (r.json().get("incident") or {}).get("custom_fields") or []

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_fetch_one, iid): iid for iid in by_id}
        for fut in as_completed(futures):
            iid, fields = fut.result()
            if iid in by_id:
                by_id[iid]["custom_fields"] = fields
    return [by_id[i.get("id")] if i.get("id") in by_id else i for i in incidents]


def incident_root_cause(incident: dict) -> tuple[bool, str]:
    for field in incident.get("custom_fields") or []:
        if (field.get("name") or "").lower() != ROOT_CAUSE_FIELD_NAME:
            continue
        raw = field.get("value")
        if isinstance(raw, list):
            text = ", ".join(str(x).strip() for x in raw if str(x).strip())
        else:
            text = str(raw or "").strip()
        return bool(text), text
    return False, ""


def fetch_incidents_touched_by_team(
    api_token: str,
    days: int | None = None,
    user_ids: list[str] | None = None,
) -> list[dict]:
    """
    Incidents any configured user touched in the last `days` (PagerDuty log_entries).
    Returns raw PagerDuty incident dicts, newest touch first.
    """
    team_user_ids = user_ids if user_ids is not None else pagerduty_team_user_ids()
    if not team_user_ids or not api_token:
        return []
    if days is None:
        days = pagerduty_incidents_lookback_days()

    team_id_set = set(team_user_ids)

    headers = _pd_headers(api_token)
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    by_id: dict[str, dict] = {}
    offset = 0
    page_limit = 100
    max_rows = 2000

    while offset < max_rows:
        response = requests.get(
            "https://api.pagerduty.com/log_entries",
            headers=headers,
            params={
                "since": since,
                "limit": page_limit,
                "offset": offset,
                "user_ids[]": team_user_ids,
                "include[]": ["incidents"],
            },
            timeout=(12, 60),
        )
        if response.status_code != 200:
            break
        data = response.json()
        entries = data.get("log_entries") or []
        for entry in entries:
            if entry.get("type") not in _PD_TEAM_TOUCH_LOG_TYPES:
                continue
            agent = entry.get("agent") or {}
            if not _agent_is_team_member(agent, team_id_set):
                continue
            inc = entry.get("incident") or {}
            iid = inc.get("id")
            if not iid:
                continue
            agent_name = agent.get("summary") or agent.get("name") or ""
            created_at = entry.get("created_at") or ""
            row = by_id.get(iid)
            if row is None:
                by_id[iid] = {
                    "inc": inc,
                    "users": {agent_name} if agent_name else set(),
                    "last_at": created_at,
                }
            else:
                if agent_name:
                    row["users"].add(agent_name)
                if created_at > (row.get("last_at") or ""):
                    row["last_at"] = created_at
        if not data.get("more") or not entries:
            break
        offset += page_limit

    incidents: list[dict] = []
    for meta in sorted(by_id.values(), key=lambda r: r.get("last_at") or "", reverse=True):
        inc = dict(meta["inc"])
        inc["_team_touched_by"] = sorted(u for u in meta.get("users") or [] if u)
        inc["_team_touched_at"] = meta.get("last_at") or ""
        incidents.append(inc)
    return incidents


def team_touched_incident_ids(api_token: str, days: int, user_ids: list[str] | None = None) -> set[str]:
    return {
        i.get("id")
        for i in fetch_incidents_touched_by_team(api_token, days, user_ids=user_ids)
        if i.get("id")
    }


def filter_incidents_to_team(
    incidents: list[dict],
    api_token: str,
    days: int,
    user_ids: list[str] | None = None,
) -> list[dict]:
    allowed = team_touched_incident_ids(api_token, days, user_ids=user_ids)
    if not allowed:
        return incidents
    return [i for i in incidents if i.get("id") in allowed]
