"""
SHM (Service Health Management) — read KPIs from shmview.arlocloud.com and
daily active users (iOS/Android/Web) from shmdaily.arlocloud.com.
"""

from __future__ import annotations

import html
import os
import re
from typing import Any

import requests

SHM_VIEW_BASE = (os.getenv("SHM_VIEW_API_BASE") or "https://shmview.arlocloud.com").rstrip("/")
SHM_DAILY_BASE = (os.getenv("SHM_DAILY_API_BASE") or "https://shmdaily.arlocloud.com").rstrip("/")
_HTTP_TIMEOUT = (12, 90)

PILLAR_LABELS: dict[str, str] = {
    "customer_engagement": "Customer Engagement",
    "protect_and_connect": "Protect and Connect",
    "customer_satisfaction": "Customer Satisfaction",
    "smart_ai_adoption": "Smart AI Adoption",
    "onboarding": "Onboarding",
}

METRIC_LABELS: dict[str, str] = {
    "livestream": "Livestream Per User",
    "dau": "Daily Active Users (DAU)",
    "mau": "Monthly Active Users (MAU)",
    "stickiness": "Stickiness (DAU/MAU)",
    "amplitude-avg-time": "Avg Time Spent on App (Amplitude)",
    "firebase-crash-ios": "Crash-free sessions (iOS)",
    "firebase-crash-android": "Crash-free sessions (Android)",
    "time-to-livestream": "Time to Livestream",
    "livestream-reliability": "Livestream Reliability",
    "app-launch-ios": "App Launch Time (iOS)",
    "app-launch-android": "App Launch Time (Android)",
    "app-rating-ios": "App Store Rating (iOS)",
    "app-rating-android": "Play Store Rating (Android)",
    "care-volume": "Care Volume",
    "event-csat": "Event Captions CSAT",
    "ai-enablement": "AI Feature Enablement",
    "ai-default-on": "AI Default On",
    "ai-default-off": "AI Default Off",
    "ai-audio-ai": "Audio AI Adoption",
    "emergency-response": "Emergency Response",
    "claimed-vs-located": "Claimed vs Located",
    "median-onboarding": "Median Onboarding Time",
    "needed-help": "Needed Help (Onboarding)",
}

METRIC_GROUPS: dict[str, tuple[str, ...]] = {
    "engagement": (
        "livestream",
        "dau",
        "mau",
        "stickiness",
        "amplitude-avg-time",
    ),
    "protect": (
        "firebase-crash-ios",
        "firebase-crash-android",
        "time-to-livestream",
        "livestream-reliability",
        "app-launch-ios",
        "app-launch-android",
    ),
    "satisfaction": (
        "app-rating-ios",
        "app-rating-android",
        "care-volume",
        "event-csat",
    ),
    "ai": ("ai-enablement", "ai-default-on", "ai-default-off", "ai-audio-ai"),
    "onboarding": (
        "emergency-response",
        "claimed-vs-located",
        "median-onboarding",
        "needed-help",
    ),
}

_IOS_METRICS = frozenset(
    k
    for k in METRIC_LABELS
    if "ios" in k or k in ("app-launch-ios", "app-rating-ios", "firebase-crash-ios")
)
_ANDROID_METRICS = frozenset(
    k
    for k in METRIC_LABELS
    if "android" in k or k in ("app-launch-android", "app-rating-android", "firebase-crash-android")
)

_SHM_INTENT_RE = re.compile(
    r"\b(?:shm|service\s+health\s+management|customer\s+(?:engagement|satisfaction)|"
    r"pillar\s+score|livestream\s+per\s+user|stickiness|care\s+volume|"
    r"app\s+(?:store|rating)|event\s+captions|onboarding\s+vitals|shmview)\b",
    re.I,
)

_SHM_DAILY_INTENT_RE = re.compile(
    r"\b(?:shmdaily|active\s+users?\s+(?:daily|by\s+os)|dau\s+(?:trend|daily|by\s+os)|"
    r"daily\s+active\s+users?|ios\s+(?:vs|and)\s+android\s+users?|"
    r"android\s+(?:vs|and)\s+ios\s+users?|users?\s+by\s+os|platform\s+split)\b",
    re.I,
)

_IOS_RE = re.compile(r"\b(?:ios|iphone|app\s+store|apple)\b", re.I)
_ANDROID_RE = re.compile(r"\b(?:android|play\s+store|google\s+play)\b", re.I)


def is_shm_metrics_question(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    if _SHM_DAILY_INTENT_RE.search(q) and not _SHM_INTENT_RE.search(q):
        return False
    return bool(_SHM_INTENT_RE.search(q))


def is_shm_daily_question(question: str) -> bool:
    return bool(_SHM_DAILY_INTENT_RE.search(question or ""))


def _http_get_json(base: str, path: str) -> tuple[dict[str, Any] | None, str | None]:
    url = f"{base}{path}"
    try:
        r = requests.get(url, timeout=_HTTP_TIMEOUT)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}: {(r.text or '')[:300]}"
        return r.json(), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _http_post_json(
    base: str, path: str, payload: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    url = f"{base}{path}"
    try:
        r = requests.post(url, json=payload, timeout=_HTTP_TIMEOUT)
        body: dict[str, Any] = {}
        try:
            body = r.json()
        except Exception:
            body = {}
        if r.status_code != 200:
            detail = body.get("detail") or body.get("message") or body.get("error") or r.text
            if isinstance(detail, list):
                detail = str(detail)[:400]
            return None, f"HTTP {r.status_code}: {str(detail)[:400]}"
        return body, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _sorted_periods(periods: list[str] | None) -> list[str]:
    if not periods:
        return []

    def _key(p: str) -> tuple[int, int, int]:
        m = re.match(r"(\d{2})/(\d{2})/(\d{2})", (p or "").strip())
        if not m:
            return (99, 99, 99)
        mm, dd, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return (2000 + yy, mm, dd)

    return sorted([p for p in periods if p], key=_key)


def _latest_period(periods: list[str]) -> str | None:
    s = _sorted_periods(periods)
    return s[-1] if s else None


def _resolve_metric_filter(question: str) -> tuple[set[str] | None, set[str] | None]:
    """Return (metric_keys or None=all, pillar_keys or None=all)."""
    q = (question or "").lower()
    metrics: set[str] = set()
    pillars: set[str] = set()

    if re.search(r"\b(?:customer\s+engagement|engagement\s+pillar)\b", q):
        pillars.add("customer_engagement")
        metrics.update(METRIC_GROUPS["engagement"])
    if re.search(r"\b(?:protect\s+and\s+connect|protect\s*&\s*connect|crash[\s-]?free)\b", q):
        pillars.add("protect_and_connect")
        metrics.update(METRIC_GROUPS["protect"])
    if re.search(r"\b(?:customer\s+satisfaction|csat|app\s+ratings?|care\s+volume)\b", q):
        pillars.add("customer_satisfaction")
        metrics.update(METRIC_GROUPS["satisfaction"])
    if re.search(r"\b(?:smart\s+ai|ai\s+adoption|ai\s+enablement)\b", q):
        pillars.add("smart_ai_adoption")
        metrics.update(METRIC_GROUPS["ai"])
    if re.search(r"\b(?:onboarding|median\s+onboarding|needed\s+help)\b", q):
        pillars.add("onboarding")
        metrics.update(METRIC_GROUPS["onboarding"])

    if _IOS_RE.search(q) and not _ANDROID_RE.search(q):
        metrics.update(_IOS_METRICS)
    elif _ANDROID_RE.search(q) and not _IOS_RE.search(q):
        metrics.update(_ANDROID_METRICS)
    elif _IOS_RE.search(q) and _ANDROID_RE.search(q):
        metrics.update(_IOS_METRICS | _ANDROID_METRICS)

    if not metrics and not pillars:
        return None, None
    return (metrics or None), (pillars or None)


def _timerange_to_splunk_window(hours: int) -> tuple[str, str]:
    h = max(1, int(hours or 720))
    if h <= 24:
        return "-1d@d", "now"
    if h <= 48:
        return "-2d@d", "now"
    if h <= 168:
        return "-7d@d", "now"
    if h <= 336:
        return "-14d@d", "now"
    return "-30d@d", "now"


def _fetch_live_app_ratings(period: str) -> dict[str, Any] | None:
    data, err = _http_post_json(
        SHM_VIEW_BASE,
        "/api/tableau/app-ratings",
        {"period": period, "platform": None},
    )
    if err or not data or not data.get("ok"):
        return {"error": err or data.get("message") or "Tableau app-ratings failed"}
    return data


def _render_table(headers: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = []
    for row in rows:
        tds = "".join(f"<td>{html.escape(str(c))}</td>" for c in row)
        body.append(f"<tr>{tds}</tr>")
    return (
        "<table class='ticket-table' style='border-collapse:collapse;width:100%;font-size:13px;'>"
        f"<thead><tr>{th}</tr></thead><tbody>{''.join(body)}</tbody></table>"
    )


def _format_shm_metrics_html(
    *,
    history: dict[str, Any],
    question: str,
    base_url: str,
    live_ratings: dict[str, Any] | None = None,
    error: str | None = None,
) -> str:
    if error:
        return (
            f"<p style='color:#dc2626;'><strong>SHM metrics error:</strong> "
            f"{html.escape(error)}</p>"
            f"<p>Source: <a href='{html.escape(base_url)}' target='_blank'>{html.escape(base_url)}</a></p>"
        )

    metrics_filter, pillars_filter = _resolve_metric_filter(question)
    all_metrics: dict[str, Any] = history.get("metrics") or {}
    all_pillars: dict[str, Any] = history.get("pillars") or {}
    periods = _sorted_periods(history.get("periods") or [])
    if not periods:
        for mdata in all_metrics.values():
            if isinstance(mdata, dict):
                periods = _sorted_periods(list(mdata.keys()))
                break
    latest = _latest_period(periods)
    show_periods = periods[-6:] if len(periods) > 6 else periods

    parts: list[str] = [
        "<div class='shm-metrics-report'>",
        "<h2 style='color:#0891b2;margin:0 0 8px;'>SHM — Service Health Management</h2>",
        f"<p style='font-size:13px;color:#64748b;margin:0 0 14px;'>"
        f"Source: <a href='{html.escape(base_url)}' target='_blank'>{html.escape(base_url)}</a>"
        f" · KPI history ({len(periods)} periods)"
        + (f" · latest <strong>{html.escape(latest)}</strong>" if latest else "")
        + "</p>",
    ]

    if all_pillars:
        parts.append("<h3 style='margin:16px 0 8px;'>Pillar scores</h3>")
        pillar_rows: list[list[str]] = []
        for key, label in PILLAR_LABELS.items():
            if pillars_filter and key not in pillars_filter:
                continue
            pdata = all_pillars.get(key) or {}
            if not isinstance(pdata, dict):
                continue
            row = [label]
            for p in show_periods:
                row.append(str(pdata.get(p, "—")))
            if latest:
                row.append(str(pdata.get(latest, "—")))
            pillar_rows.append(row)
        if pillar_rows:
            hdr = ["Pillar"] + show_periods + (["Latest"] if latest else [])
            parts.append(_render_table(hdr, pillar_rows))

    metric_keys = sorted(all_metrics.keys())
    if metrics_filter:
        metric_keys = [k for k in metric_keys if k in metrics_filter]

    if metric_keys:
        parts.append("<h3 style='margin:16px 0 8px;'>KPI metrics</h3>")
        mrows: list[list[str]] = []
        for key in metric_keys:
            mdata = all_metrics.get(key) or {}
            if not isinstance(mdata, dict):
                continue
            label = METRIC_LABELS.get(key, key)
            row = [label]
            for p in show_periods:
                cell = mdata.get(p) or {}
                if isinstance(cell, dict):
                    hist = cell.get("hist", "—")
                    score = cell.get("score")
                    row.append(f"{hist}" + (f" ({score})" if score else ""))
                else:
                    row.append(str(cell))
            if latest:
                cell = mdata.get(latest) or {}
                if isinstance(cell, dict):
                    hist = cell.get("hist", "—")
                    score = cell.get("score")
                    row.append(f"{hist}" + (f" ({score})" if score else ""))
                else:
                    row.append(str(cell))
            mrows.append(row)
        hdr = ["Metric"] + show_periods + (["Latest"] if latest else [])
        parts.append(_render_table(hdr, mrows))

    if live_ratings and live_ratings.get("ok"):
        m = live_ratings.get("metrics") or {}
        show_live = metrics_filter is None or bool(
            metrics_filter & METRIC_GROUPS["satisfaction"]
        )
        if show_live:
            parts.append("<h3 style='margin:16px 0 8px;'>Live App Ratings (Tableau)</h3>")
            lr_rows: list[list[str]] = []
            for plat_key, plat_label in (
                ("app_rating_ios", "iOS"),
                ("app_rating_android", "Android"),
            ):
                block = m.get(plat_key) or {}
                lr_rows.append(
                    [
                        plat_label,
                        str(block.get("value", "—")),
                        str(block.get("app_group", "")),
                        str(live_ratings.get("calendar_month", "")),
                    ]
                )
            if lr_rows:
                parts.append(
                    _render_table(
                        ["Platform", "Rating", "App group", "Month"],
                        lr_rows,
                    )
                )
    elif live_ratings and live_ratings.get("error"):
        parts.append(
            f"<p style='font-size:12px;color:#b45309;'>Live Tableau ratings: "
            f"{html.escape(str(live_ratings['error'])[:200])}</p>"
        )

    weights = history.get("weights") or {}
    if weights and not metrics_filter:
        top_w = sorted(weights.items(), key=lambda x: -float(x[1] or 0))[:8]
        wtxt = ", ".join(f"{METRIC_LABELS.get(k, k)}={v}%" for k, v in top_w if v)
        if wtxt:
            parts.append(f"<p style='font-size:12px;color:#64748b;margin-top:12px;'>Top weights: {html.escape(wtxt)}</p>")

    parts.append("</div>")
    return "\n".join(parts)


def get_shm_metrics_mcp(
    question: str = "",
    query: str = "",
    force_live: bool = False,
) -> str:
    """
    MCP entry: SHM pillar scores and KPI metrics from shmview.arlocloud.com.
    Uses /api/kpi/history (SQLite cache). Optionally refreshes App Ratings live.
    """
    q = (question or query or "").strip()
    data, err = _http_get_json(SHM_VIEW_BASE, "/api/kpi/history")
    if err or not data:
        return _format_shm_metrics_html(
            history={},
            question=q,
            base_url=SHM_VIEW_BASE,
            error=err or "Empty KPI history response",
        )

    live_ratings = None
    want_ratings = force_live or bool(
        re.search(r"\b(?:app\s+ratings?|customer\s+satisfaction|play\s+store|app\s+store)\b", q, re.I)
    )
    if want_ratings:
        periods = _sorted_periods(data.get("periods") or [])
        period = _latest_period(periods) or "03/31/26"
        live_ratings = _fetch_live_app_ratings(period)

    return _format_shm_metrics_html(
        history=data,
        question=q,
        base_url=SHM_VIEW_BASE,
        live_ratings=live_ratings,
    )


def get_shm_daily_mcp(
    question: str = "",
    query: str = "",
    timerange: int | None = None,
    earliest: str = "",
    latest: str = "",
) -> str:
    """
    MCP entry: daily / average active users by OS (iOS, Android, Web) from shmdaily.arlocloud.com.
    """
    q = (question or query or "").strip()
    if earliest and latest:
        ew, lw = earliest, latest
    else:
        ew, lw = _timerange_to_splunk_window(timerange or 720)

    data, err = _http_post_json(
        SHM_DAILY_BASE,
        "/api/splunk/active-user-by-os",
        {"earliest": ew, "latest": lw},
    )
    if err or not data:
        return (
            f"<p style='color:#dc2626;'><strong>SHM daily users error:</strong> "
            f"{html.escape(err or 'Empty response')}</p>"
            f"<p>Source: <a href='{html.escape(SHM_DAILY_BASE)}' target='_blank'>"
            f"{html.escape(SHM_DAILY_BASE)}</a></p>"
        )

    av = data.get("averages_by_os") or {}
    rows = data.get("rows") or []
    parts = [
        "<div class='shm-daily-report'>",
        "<h2 style='color:#0891b2;margin:0 0 8px;'>SHM Daily Active Users (by OS)</h2>",
        f"<p style='font-size:13px;color:#64748b;'>"
        f"Source: <a href='{html.escape(SHM_DAILY_BASE)}' target='_blank'>{html.escape(SHM_DAILY_BASE)}</a>"
        f" · window <code>{html.escape(ew)}</code> → <code>{html.escape(lw)}</code>"
        f"</p>",
    ]

    if av:
        chips = []
        for os_name in ("iOS", "Android", "Web", "Other"):
            val = av.get(os_name)
            if val is None:
                continue
            chips.append(
                f"<span style='display:inline-block;margin:4px 8px 4px 0;padding:6px 12px;"
                f"background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;font-size:13px;'>"
                f"<strong>{html.escape(os_name)}</strong>: {html.escape(f'{val:,.0f}' if isinstance(val, (int, float)) else str(val))}"
                f"</span>"
            )
        parts.append("<div style='margin:12px 0;'>" + "".join(chips) + "</div>")

    osk = data.get("inferred_os_field") or "os"
    vk = data.get("inferred_value_field") or "average"
    table_rows: list[list[str]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        table_rows.append([str(r.get(osk, "")), str(r.get(vk, ""))])
    if table_rows:
        parts.append(_render_table(["OS", "Average users"], table_rows))

    sid = data.get("sid")
    if sid:
        parts.append(f"<p style='font-size:12px;color:#64748b;'>Splunk SID: {html.escape(str(sid))}</p>")
    parts.append("</div>")
    return "\n".join(parts)
