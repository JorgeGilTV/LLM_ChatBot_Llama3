"""CAT / Comcast partner monitoring tools — Datadog RED (same layout as ADT)."""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Callable

_PARTNER_DD_ENV: dict[str, tuple[str, ...]] = {
    "cat": ("DD_CAT_DASHBOARD_ID", "DATADOG_CAT_DASHBOARD_ID"),
    "comcast": ("DD_COMCAST_DASHBOARD_ID", "DATADOG_COMCAST_DASHBOARD_ID"),
}

_PARTNER_LABELS: dict[str, str] = {
    "cat": "CAT",
    "comcast": "Comcast",
}


def _partner_dd_dashboard_id(partner: str) -> str:
    for key in _PARTNER_DD_ENV.get(partner, ()):
        value = (os.getenv(key) or "").strip()
        if value:
            return value
    return ""


@contextmanager
def _adt_dashboard_env_override(dashboard_id: str):
    keys = ("DD_ADT_DASHBOARD_ID", "DATADOG_ADT_DASHBOARD_ID")
    backup = {key: os.environ.get(key) for key in keys}
    try:
        os.environ["DD_ADT_DASHBOARD_ID"] = dashboard_id
        os.environ.pop("DATADOG_ADT_DASHBOARD_ID", None)
        yield
    finally:
        for key, value in backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _brand_adt_html(html: str, partner: str) -> str:
    label = _PARTNER_LABELS[partner]
    replacements = (
        ("ADT Dashboard", f"{label} Dashboard"),
        ("RED - Metrics - ADT", f"RED - Metrics - {label}"),
        ("Reading Datadog ADT", f"Reading Datadog {label}"),
        ("ADT services", f"{label} services"),
        ("ADT dashboard", f"{label} dashboard"),
        ("ADT ", f"{label} "),
        ("ADT", label),
    )
    for old, new in replacements:
        html = html.replace(old, new)
    return html


def _run_partner_dd_tool(
    partner: str,
    query: str,
    timerange_hours: int,
    fn: Callable[..., str],
) -> str:
    dashboard_id = _partner_dd_dashboard_id(partner)
    label = _PARTNER_LABELS[partner]
    if not dashboard_id:
        env_hint = " / ".join(f"<code>{key}</code>" for key in _PARTNER_DD_ENV[partner])
        return (
            f"<p>❌ Configure {env_hint} for {label} RED metrics dashboard.</p>"
        )
    with _adt_dashboard_env_override(dashboard_id):
        result = fn(query, timerange_hours)
    return _brand_adt_html(result, partner)


def read_datadog_cat(query: str, timerange_hours: int = 4) -> str:
    from tools.datadog_dashboards import read_datadog_adt

    return _run_partner_dd_tool("cat", query, timerange_hours, read_datadog_adt)


def read_datadog_comcast(query: str, timerange_hours: int = 4) -> str:
    from tools.datadog_dashboards import read_datadog_adt

    return _run_partner_dd_tool("comcast", query, timerange_hours, read_datadog_adt)


def read_datadog_cat_errors_only(query: str = "", timerange_hours: int = 4) -> str:
    from tools.datadog_dashboards import read_datadog_adt_errors_only

    return _run_partner_dd_tool("cat", query, timerange_hours, read_datadog_adt_errors_only)


def read_datadog_comcast_errors_only(query: str = "", timerange_hours: int = 4) -> str:
    from tools.datadog_dashboards import read_datadog_adt_errors_only

    return _run_partner_dd_tool("comcast", query, timerange_hours, read_datadog_adt_errors_only)
