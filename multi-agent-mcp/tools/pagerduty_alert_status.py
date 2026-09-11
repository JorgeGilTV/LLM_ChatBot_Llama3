"""PagerDuty 'Is Alert' custom-field breakdown (true / false / empty) for the home pie chart."""

import os
import time

from tools.pagerduty_team import enrich_incidents_custom_fields, fetch_all_incidents

IS_ALERT_FIELD_NAME = "is_alert"
ALLOWED_DAYS = (7, 14)

_cache: dict[int, tuple[float, dict]] = {}
_CACHE_TTL_SECS = 300


def _classify_is_alert(incident: dict) -> str:
    for field in incident.get("custom_fields") or []:
        if (field.get("name") or "").strip().lower() != IS_ALERT_FIELD_NAME:
            continue
        raw = field.get("value")
        values = raw if isinstance(raw, list) else [raw]
        text = ", ".join(str(v).strip() for v in values if str(v or "").strip())
        if not text:
            return "empty"
        low = text.strip().lower()
        if low.startswith("true"):
            return "true"
        if low.startswith("false"):
            return "false"
        return "empty"
    return "empty"


def get_pagerduty_alert_status_counts(days: int = 7, force_refresh: bool = False) -> dict:
    """Count PagerDuty incidents in the last `days` by the 'Is Alert' custom field: true / false / empty."""
    days = 14 if int(days or 7) >= 14 else 7

    now = time.time()
    if not force_refresh:
        cached = _cache.get(days)
        if cached and (now - cached[0]) < _CACHE_TTL_SECS:
            return cached[1]

    api_token = os.getenv("PAGERDUTY_API_TOKEN")
    if not api_token:
        return {"error": "PagerDuty token not configured"}

    try:
        incidents = fetch_all_incidents(api_token, days=days)
        incidents = enrich_incidents_custom_fields(api_token, incidents)

        counts = {"true": 0, "false": 0, "empty": 0}
        for incident in incidents:
            counts[_classify_is_alert(incident)] += 1

        payload = {
            "days": days,
            "total": len(incidents),
            "true": counts["true"],
            "false": counts["false"],
            "empty": counts["empty"],
            "generated_at": now,
        }
        _cache[days] = (now, payload)
        return payload
    except Exception as e:
        return {"error": str(e)}
