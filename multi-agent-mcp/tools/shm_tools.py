"""
SHM (Service Health Management) — read KPIs from shmview.arlocloud.com and
daily active users (iOS/Android/Web) from shmdaily.arlocloud.com.
"""

from __future__ import annotations

import html
import json
import os
import re
import uuid
from dataclasses import dataclass
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

PILLAR_KEYS: tuple[str, ...] = tuple(PILLAR_LABELS.keys())
PILLAR_WEIGHTS: tuple[int, ...] = (20, 30, 30, 10, 10)
PILLAR_COLORS: tuple[str, ...] = ("#2563eb", "#16a34a", "#ea580c", "#9333ea", "#0d9488")
PILLAR_FILL_COLORS: tuple[str, ...] = ("#dbeafe", "#dcfce7", "#ffedd5", "#f3e8ff", "#ccfbf1")

PILLAR_DASHBOARD_DEFS: dict[str, dict[str, Any]] = {
    "customer_engagement": {
        "name": "Customer Engagement",
        "short_name": "Engagement",
        "metrics": ("livestream", "dau", "mau", "stickiness", "amplitude-avg-time"),
    },
    "protect_and_connect": {
        "name": "Protect and Connect",
        "short_name": "Protect & Connect",
        "metrics": (
            "firebase-crash-ios",
            "firebase-crash-android",
            "time-to-livestream",
            "livestream-reliability",
            "app-launch-ios",
            "app-launch-android",
        ),
    },
    "customer_satisfaction": {
        "name": "Customer Satisfaction",
        "short_name": "Customer Sat",
        "metrics": ("app-rating-ios", "app-rating-android", "care-volume", "event-csat"),
    },
    "smart_ai_adoption": {
        "name": "Smart AI Adoption",
        "short_name": "Smart AI",
        "metrics": ("ai-enablement", "ai-default-on", "ai-default-off", "ai-audio-ai"),
    },
    "onboarding": {
        "name": "Onboarding",
        "short_name": "Onboarding",
        "metrics": ("claimed-vs-located", "median-onboarding", "needed-help"),
    },
}

METRIC_SOURCES: dict[str, str] = {
    "app-rating-ios": "Tableau",
    "app-rating-android": "App30dayAveRating",
    "care-volume": "CaseData",
    "event-csat": "Harlem feedback (thumbs)",
    "livestream": "Splunk",
    "dau": "Amplitude",
    "mau": "Amplitude",
}

_MONTH_NAME_TO_NUM: dict[str, int] = {
    "january": 1,
    "jan": 1,
    "enero": 1,
    "february": 2,
    "feb": 2,
    "febrero": 2,
    "march": 3,
    "mar": 3,
    "marzo": 3,
    "april": 4,
    "apr": 4,
    "abril": 4,
    "may": 5,
    "mayo": 5,
    "june": 6,
    "jun": 6,
    "junio": 6,
    "july": 7,
    "jul": 7,
    "julio": 7,
    "august": 8,
    "aug": 8,
    "agosto": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "septiembre": 9,
    "october": 10,
    "oct": 10,
    "octubre": 10,
    "november": 11,
    "nov": 11,
    "noviembre": 11,
    "december": 12,
    "dec": 12,
    "diciembre": 12,
}

_SHM_INTENT_RE = re.compile(
    r"\b(?:shm|service\s+health\s+management|customer\s+(?:engagement|satisfaction|service)|"
    r"satisfacción|satisfaccion|nivel\s+de\s+satisfacción|nivel\s+de\s+satisfaccion|"
    r"satisfacción\s+del\s+cliente|satisfaccion\s+del\s+cliente|"
    r"pillar\s+score|shm\s+score|overall\s+score|puntaje|livestream\s+per\s+user|stickiness|"
    r"care\s+volume|app\s+(?:store|rating|ratings)|play\s+store|event\s+captions|"
    r"onboarding\s+vitals|shmview|csat|nps|gráfica|grafica|chart|graph|"
    r"últimos?|ultimos?|meses|months?)\b",
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
    if _SHM_INTENT_RE.search(q):
        return True
    # Spanish/English: satisfaction + platform (Android/iOS)
    if re.search(r"satisfac", q, re.I) and (_IOS_RE.search(q) or _ANDROID_RE.search(q)):
        return True
    if re.search(r"\b(?:rating|ratings|csat|nps)\b", q, re.I) and (
        _IOS_RE.search(q) or _ANDROID_RE.search(q)
    ):
        return True
    month, _year = _parse_month_from_question(q)
    if month and re.search(
        r"\b(?:shm|pillar|rating|satisf|score|customer|app\s+store|play\s+store)\b",
        q,
        re.I,
    ):
        return True
    return False


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


def _period_month_year(period: str) -> tuple[int, int] | None:
    m = re.match(r"(\d{2})/(\d{2})/(\d{2})", (period or "").strip())
    if not m:
        return None
    mm, yy = int(m.group(1)), int(m.group(3))
    return mm, 2000 + yy


def _period_display(period: str) -> str:
    my = _period_month_year(period)
    if not my:
        return period
    mm, yy = my
    names = (
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    )
    if 1 <= mm <= 12:
        return f"{names[mm - 1]} {yy}"
    return period


def _parse_numeric(val: str | None) -> float | None:
    if val is None:
        return None
    s = str(val).strip().replace(",", "").replace("%", "")
    if not s or s in ("—", "-", "N/A", "n/a"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_month_from_question(q: str) -> tuple[int | None, int | None]:
    """Return (month 1–12, optional year) parsed from the question."""
    ql = (q or "").lower()
    m = re.search(r"\b(\d{1,2})[/\-](\d{4})\b", ql)
    if m:
        return int(m.group(1)), int(m.group(2))
    for name, num in sorted(_MONTH_NAME_TO_NUM.items(), key=lambda x: -len(x[0])):
        if re.search(rf"\b{re.escape(name)}\b", ql):
            ym = re.search(rf"\b{re.escape(name)}\b[^\d]{{0,24}}(\d{{4}})", ql)
            year = int(ym.group(1)) if ym else None
            return num, year
    return None, None


def _parse_last_n_months(q: str) -> int | None:
    m = re.search(
        r"(?:last|past|últimos?|ultimos?)\s+(\d+)\s+(?:months?|meses)",
        q or "",
        re.I,
    )
    if m:
        return max(1, min(24, int(m.group(1))))
    return None


def _wants_chart(q: str) -> bool:
    return bool(
        re.search(
            r"\b(?:graph|chart|gráfica|grafica|plot|trend|evolución|evolucion|"
            r"linea|línea|line\s+chart|time\s+series)\b",
            q or "",
            re.I,
        )
    )


def _match_period(periods: list[str], month: int, year: int | None) -> str | None:
    matches: list[str] = []
    for p in periods:
        my = _period_month_year(p)
        if not my or my[0] != month:
            continue
        if year is None or my[1] == year:
            matches.append(p)
    return matches[-1] if matches else None


@dataclass
class ShmQueryIntent:
    target_period: str | None = None
    chart_periods: list[str] | None = None
    want_chart: bool = False
    focus_overall: bool = False
    metrics_filter: set[str] | None = None
    pillars_filter: set[str] | None = None
    focused: bool = False


def _parse_shm_query(question: str, periods: list[str]) -> ShmQueryIntent:
    q = (question or "").strip()
    metrics_filter, pillars_filter = _resolve_metric_filter(q)
    want_chart = _wants_chart(q)
    last_n = _parse_last_n_months(q)
    month, year = _parse_month_from_question(q)
    target = _match_period(periods, month, year) if month else None

    focus_overall = bool(
        re.search(
            r"\b(?:shm\s+score|overall|overview|dashboard|pantalla\s+principal|main\s+screen|"
            r"overall\s+(?:shm\s+)?score|overall\s+score|"
            r"puntaje\s+shm|score\s+general|show\s+(?:me\s+)?(?:the\s+)?shm|"
            r"home\s+screen|pantalla\s+shm)\b",
            q,
            re.I,
        )
        and not pillars_filter
    )

    chart_periods: list[str] | None = None
    focused = False

    if last_n and periods:
        chart_periods = periods[-last_n:]
        want_chart = True
        focused = True
    elif target:
        chart_periods = list(periods)
        focused = True
    elif want_chart and periods:
        chart_periods = periods[-6:]
        focused = bool(metrics_filter or pillars_filter or focus_overall)

    if not focused:
        focused = bool(
            target
            or last_n
            or want_chart
            or metrics_filter
            or pillars_filter
            or focus_overall
            or month
        )

    return ShmQueryIntent(
        target_period=target,
        chart_periods=chart_periods,
        want_chart=want_chart,
        focus_overall=focus_overall,
        metrics_filter=metrics_filter,
        pillars_filter=pillars_filter,
        focused=focused,
    )


def _overall_shm_value(pillars: dict[str, Any], period: str) -> float | None:
    num = den = 0.0
    for i, key in enumerate(PILLAR_KEYS):
        raw = (pillars.get(key) or {}).get(period)
        val = _parse_numeric(str(raw) if raw is not None else None)
        if val is None:
            continue
        w = PILLAR_WEIGHTS[i]
        num += val * w
        den += w
    return round(num / den, 2) if den else None


def _overall_shm_series(pillars: dict[str, Any], periods: list[str]) -> list[float | None]:
    return [_overall_shm_value(pillars, p) for p in periods]


def _pillar_series(pillars: dict[str, Any], key: str, periods: list[str]) -> list[float | None]:
    pdata = pillars.get(key) or {}
    return [_parse_numeric(str(pdata.get(p)) if pdata.get(p) is not None else None) for p in periods]


def _metric_hist_series(metrics: dict[str, Any], key: str, periods: list[str]) -> list[float | None]:
    mdata = metrics.get(key) or {}
    out: list[float | None] = []
    for p in periods:
        cell = mdata.get(p) or {}
        if isinstance(cell, dict):
            out.append(_parse_numeric(cell.get("hist")))
        else:
            out.append(_parse_numeric(str(cell)))
    return out


def _period_short_label(period: str) -> str:
    my = _period_month_year(period)
    if not my:
        return period[:3]
    names = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    mm, _yy = my
    return names[mm - 1] if 1 <= mm <= 12 else period[:3]


def _metric_at_period(
    metrics: dict[str, Any], key: str, period: str
) -> tuple[str | None, float | None]:
    cell = (metrics.get(key) or {}).get(period) or {}
    if isinstance(cell, dict):
        hist = cell.get("hist")
        return (
            str(hist) if hist is not None else None,
            _parse_numeric(cell.get("score")),
        )
    return (str(cell) if cell else None), None


def _metric_score_series(metrics: dict[str, Any], key: str, periods: list[str]) -> list[float | None]:
    mdata = metrics.get(key) or {}
    out: list[float | None] = []
    for p in periods:
        cell = mdata.get(p) or {}
        if isinstance(cell, dict):
            out.append(_parse_numeric(cell.get("score")))
        else:
            out.append(None)
    return out


def _score_badge_html(score: float | None) -> str:
    if score is None:
        return "<span style='color:#71717a;font-size:11px;'>—</span>"
    if score >= 90:
        fg, bg = "#86efac", "#166534"
    elif score >= 75:
        fg, bg = "#fcd34d", "#854d0e"
    else:
        fg, bg = "#fca5a5", "#991b1b"
    txt = f"{score:.2f}".rstrip("0").rstrip(".")
    return (
        f"<span style='background:{bg};color:{fg};padding:2px 8px;border-radius:999px;"
        f"font-size:11px;font-weight:600;'>{html.escape(txt)}</span>"
    )


def _wants_table(question: str) -> bool:
    return bool(
        re.search(
            r"\b(?:table|tabla|kpi\s+sheet|detailed?\s+table|spreadsheet|raw\s+data)\b",
            question or "",
            re.I,
        )
    )


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = (hex_color or "#71717a").lstrip("#")
    if len(h) != 6:
        return f"rgba(113,113,122,{alpha})"
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _shm_ee_css_block() -> str:
    return """<style>
.shm-ee-dash{--font-sans:"IBM Plex Sans",system-ui,sans-serif;--color-text-primary:#f4f4f5;--color-text-secondary:#a1a1aa;--color-text-tertiary:#71717a;--color-background-primary:#26262f;--color-background-secondary:#1f2023;--color-border-tertiary:rgba(255,255,255,0.08);--color-border-primary:rgba(255,255,255,0.14);--border-radius-md:8px;--border-radius-lg:12px;font-family:var(--font-sans);color:var(--color-text-primary);background:#1a1b1e;border:1px solid rgba(63,63,70,0.9);border-radius:16px;padding:20px 24px;margin:12px 0;box-shadow:0 4px 24px rgba(0,0,0,0.25)}
.shm-ee-dash *{box-sizing:border-box}
.shm-ee-dash .ee-top-bar{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;flex-wrap:wrap;gap:8px}
.shm-ee-dash .ee-top-bar h2{font-size:20px;font-weight:500;color:var(--color-text-primary);margin:0}
.shm-ee-dash .ee-top-bar p{font-size:13px;color:var(--color-text-secondary);margin:4px 0 0}
.shm-ee-dash .ee-badge{font-size:11px;padding:3px 10px;border-radius:20px;font-weight:500}
.shm-ee-dash .ee-badge-info{background:rgba(59,130,246,0.22);color:#93c5fd}
.shm-ee-dash .ee-section-title{font-size:12px;font-weight:500;color:var(--color-text-primary);margin-bottom:8px;padding-bottom:6px;border-bottom:0.5px solid var(--color-border-tertiary)}
.shm-ee-dash .ee-score-single{display:inline-flex;flex-direction:column;background:var(--color-background-secondary);border-radius:var(--border-radius-md);padding:8px 12px;margin-bottom:16px;min-width:112px;max-width:200px}
.shm-ee-dash .ee-score-single .ee-label{font-size:10px;color:var(--color-text-secondary);margin-bottom:2px}
.shm-ee-dash .ee-score-single .ee-val{font-size:22px;font-weight:500;line-height:1;color:var(--color-text-primary)}
.shm-ee-dash .ee-score-single .ee-sub{font-size:9px;color:var(--color-text-secondary);margin-top:2px;line-height:1.25}
.shm-ee-dash .ee-score-spark{height:26px;position:relative;margin-top:6px;width:96px}
.shm-ee-dash .ee-overall-shm-row{display:flex;flex-wrap:wrap;align-items:stretch;gap:10px;margin-bottom:14px}
.shm-ee-dash .ee-overall-shm-row .ee-score-single{margin-bottom:0}
.shm-ee-dash .ee-hero-overall-chart,.shm-ee-dash .ee-all-pillars-chart{flex:1 1 520px;max-width:720px;min-width:0;min-height:240px;height:240px;position:relative;border-radius:var(--border-radius-md);border:0.5px solid var(--color-border-tertiary);background:var(--color-background-secondary);padding:6px 8px}
.shm-ee-dash .ee-summary-box{border:1px solid var(--color-border-tertiary);border-radius:var(--border-radius-md);background:var(--color-background-secondary);padding:12px 14px 14px;margin-bottom:18px;box-shadow:0 1px 2px rgba(0,0,0,0.18)}
.shm-ee-dash .ee-summary-box--cols{display:flex;flex-wrap:wrap;align-items:stretch;gap:14px}
.shm-ee-dash .ee-summary-col{flex:1 1 420px;min-width:0;display:flex;flex-direction:column}
.shm-ee-dash .ee-summary-vdivider{flex:0 0 1px;background:var(--color-border-tertiary);align-self:stretch}
.shm-ee-dash .ee-pillars-row{display:flex;flex-wrap:wrap;align-items:stretch;gap:10px;margin-bottom:14px}
.shm-ee-dash .ee-pillars-row .ee-all-pillars-chart{margin-bottom:0;flex:1 1 640px;max-width:820px}
.shm-ee-dash .ee-pillar-overview{display:grid;grid-template-columns:repeat(auto-fit,minmax(108px,1fr));gap:8px;margin-bottom:14px;flex:0 1 auto;min-width:0}
.shm-ee-dash .ee-pillar-block{background:var(--color-background-primary);border:0.5px solid var(--color-border-tertiary);border-radius:var(--border-radius-md);padding:8px 10px}
.shm-ee-dash .ee-pillar-block .ee-pname{font-size:11px;font-weight:500;color:var(--color-text-primary);margin-bottom:1px}
.shm-ee-dash .ee-pillar-block .ee-pwt{font-size:9px;color:var(--color-text-secondary);margin-bottom:4px}
.shm-ee-dash .ee-pillar-block .ee-pscore{font-size:20px;font-weight:500}
.shm-ee-dash .ee-pillar-spark{height:24px;position:relative;margin-top:4px}
.shm-ee-dash .ee-pillar-block .ee-pcount{font-size:9px;color:var(--color-text-secondary);margin-top:3px}
.shm-ee-dash .ee-metrics-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:12px;margin-bottom:20px}
.shm-ee-dash .ee-metric-card{background:var(--color-background-primary);border:0.5px solid var(--color-border-tertiary);border-radius:var(--border-radius-lg);padding:14px 16px}
.shm-ee-dash .ee-metric-name{font-size:13px;font-weight:500;color:var(--color-text-primary);margin-bottom:2px}
.shm-ee-dash .ee-metric-row{display:flex;align-items:flex-end;justify-content:space-between;gap:8px}
.shm-ee-dash .ee-metric-current{font-size:20px;font-weight:500;color:var(--color-text-primary)}
.shm-ee-dash .ee-metric-spark{height:40px;position:relative;flex:1;min-width:80px}
.shm-ee-dash .ee-metric-footer{display:flex;align-items:center;justify-content:space-between;margin-top:8px}
.shm-ee-dash .ee-metric-source{font-size:11px;color:var(--color-text-tertiary)}
.shm-ee-dash .ee-nav-row{display:flex;align-items:center;gap:8px;margin-bottom:16px}
.shm-ee-dash .ee-nav-crumb{font-size:13px;color:var(--color-text-primary);font-weight:500}
.shm-ee-dash .ee-detail-chart-wrap{position:relative;width:100%;height:200px;margin-bottom:20px}
#vitals-compact-grid{display:grid;grid-template-columns:repeat(1,minmax(0,1fr));gap:0.35rem}
@media(min-width:900px){#vitals-compact-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
.vitals-pillar-section{display:flex;flex-direction:column;gap:0.2rem;min-width:0;border:1px solid rgba(255,255,255,0.06);border-radius:0.375rem;background:rgba(0,0,0,0.12);padding:0.2rem}
.vitals-pillar-title{display:flex;align-items:center;justify-content:space-between;gap:0.35rem;margin:0;padding:0.15rem 0.35rem 0.15rem 0.4rem;border-left:3px solid var(--pillar-accent,#71717a);border-radius:0.2rem;background:var(--pillar-accent-bg,rgba(255,255,255,0.04))}
.vitals-pillar-name{font-size:11px;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;color:#e4e4e7;line-height:1.15;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.vitals-pillar-meta{font-size:9px;font-weight:600;color:#a1a1aa;white-space:nowrap;flex-shrink:0}
.vitals-pillar-grid{display:grid;gap:0.2rem;grid-template-columns:repeat(2,minmax(0,1fr))}
@media(min-width:520px){.vitals-pillar-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
.vitals-widget-card{display:flex;flex-direction:column;min-width:0;padding:0.35rem 0.4rem;border-radius:0.3rem;border:1px solid rgba(255,255,255,0.06);background:#26262f}
.vitals-widget-head{display:flex;align-items:flex-start;justify-content:space-between;gap:0.25rem;min-height:1.5rem}
.vitals-widget-title{font-size:10px;font-weight:600;line-height:1.2;letter-spacing:0.02em;color:#e4e4e7;text-transform:uppercase;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.vitals-widget-badge{font-size:7px;line-height:1.1;padding:1px 3px;max-width:36%;border-radius:0.15rem;background:rgba(63,63,70,0.95);font-weight:600;text-transform:uppercase;color:#a1a1aa;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex-shrink:0}
.vitals-widget-body{display:flex;align-items:baseline;justify-content:space-between;gap:0.25rem;margin-top:0.15rem}
.vitals-widget-hero{min-width:0;font-size:14px;line-height:1.1;font-weight:500;color:#fff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-variant-numeric:tabular-nums;margin:0}
.vitals-widget-score{font-size:11px;line-height:1.1;font-weight:600;color:#6ee7b7;white-space:nowrap;font-variant-numeric:tabular-nums;margin:0}
.vitals-widget-spark{height:1.5rem;margin-top:0.15rem;width:100%;position:relative}
@media(max-width:900px){.shm-ee-dash .ee-summary-vdivider{display:none}}
</style>"""


def _resolve_dashboard_pillar(intent: ShmQueryIntent) -> str | None:
    if intent.focus_overall:
        return None
    if intent.pillars_filter:
        if len(intent.pillars_filter) == 1:
            return next(iter(intent.pillars_filter))
        if "customer_satisfaction" in intent.pillars_filter:
            return "customer_satisfaction"
        return next(iter(intent.pillars_filter))
    if intent.metrics_filter:
        for pk, pdef in PILLAR_DASHBOARD_DEFS.items():
            pmetrics = set(pdef.get("metrics") or ())
            if intent.metrics_filter <= pmetrics:
                return pk
        for pk, pdef in PILLAR_DASHBOARD_DEFS.items():
            pmetrics = set(pdef.get("metrics") or ())
            if intent.metrics_filter & pmetrics:
                return pk
    return None


def _chartjs_bundle_script(charts: dict[str, Any]) -> str:
    data_json = json.dumps(charts)
    return f"""<script>
(function() {{
  const specs = {data_json};
  function yRange(arrays, fallback) {{
    const flat = arrays.flat().filter(v => v != null && Number.isFinite(Number(v))).map(Number);
    if (!flat.length) return fallback || {{min: 0, max: 100}};
    let mn = Math.min(...flat), mx = Math.max(...flat);
    if (mn === mx) {{ mn -= 1; mx += 1; }}
    const pad = (mx - mn) * 0.08 || 1;
    return {{min: mn - pad, max: mx + pad}};
  }}
  function render() {{
    if (typeof Chart === 'undefined') return false;
    Object.entries(specs).forEach(([id, spec]) => {{
      const canvas = document.getElementById(id);
      if (!canvas) return;
      const existing = Chart.getChart(canvas);
      if (existing) existing.destroy();
      const ctype = spec.chartType || 'line';
      const mini = !!spec.mini;
      const ds = (spec.datasets || []).map(d => {{
        const color = d.color || '#0891b2';
        const fill = d.fillColor || color + '55';
        if (ctype === 'bar') {{
          return {{
            label: d.label,
            data: d.data,
            backgroundColor: fill,
            borderColor: color,
            borderWidth: 1.5,
            borderRadius: 3,
          }};
        }}
        return {{
          label: d.label,
          data: d.data,
          borderColor: color,
          backgroundColor: fill,
          tension: 0.35,
          fill: true,
          pointRadius: mini ? 0 : 3,
          borderWidth: mini ? 1.5 : 2,
          spanGaps: false,
        }};
      }});
      const y = spec.yAxis || yRange(ds.map(d => d.data), spec.yFallback);
      const suffix = spec.ySuffix || '';
      new Chart(canvas, {{
        type: ctype,
        data: {{ labels: spec.labels || [], datasets: ds }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          plugins: {{
            legend: {{ display: !mini && (spec.datasets || []).length > 1, position: 'bottom' }},
            tooltip: {{ mode: 'index', intersect: false }},
          }},
          scales: {{
            y: {{
              display: !mini,
              min: y.min,
              max: y.max,
              ticks: {{ color: '#a1a1aa', font: {{ size: mini ? 8 : 10 }}, callback: v => suffix ? v + suffix : v }},
              grid: {{ color: 'rgba(255,255,255,0.06)' }},
              border: {{ display: false }},
            }},
            x: {{
              display: !mini,
              ticks: {{ color: '#a1a1aa', font: {{ size: 9 }}, maxRotation: 45, minRotation: mini ? 0 : 45 }},
              grid: {{ display: false }},
              border: {{ display: false }},
            }},
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


def _shm_score_color(val: float | None) -> str:
    if val is None:
        return "#fafafa"
    if val >= 90:
        return "#86efac"
    if val >= 75:
        return "#fcd34d"
    return "#fca5a5"


def _build_vitals_compact_grid(
    *,
    pillars_filter: set[str] | None,
    metrics_filter: set[str] | None,
    metrics: dict[str, Any],
    periods: list[str],
    focus_period: str,
    uid: str,
    charts: dict[str, Any],
) -> str:
    """shmview vitals-compact-grid: KPI widgets grouped by pillar."""
    labels = [_period_short_label(p) for p in periods]
    sections: list[str] = []
    pillar_keys = (
        [k for k in PILLAR_KEYS if k in pillars_filter]
        if pillars_filter
        else list(PILLAR_KEYS)
    )

    for pk in pillar_keys:
        pidx = PILLAR_KEYS.index(pk)
        pdef = PILLAR_DASHBOARD_DEFS.get(pk, {})
        color = PILLAR_COLORS[pidx]
        accent_bg = _hex_to_rgba(color, 0.12)
        weight = PILLAR_WEIGHTS[pidx]
        metric_keys = list(pdef.get("metrics") or ())
        if metrics_filter:
            metric_keys = [k for k in metric_keys if k in metrics_filter]
        if not metric_keys:
            continue

        widgets: list[str] = []
        for mi, mkey in enumerate(metric_keys):
            hist, score = _metric_at_period(metrics, mkey, focus_period)
            if hist is None and score is None:
                continue
            cid = f"vitals_mini_{uid}_{pk}_{mi}"
            score_series = _metric_score_series(metrics, mkey, periods)
            charts[cid] = {
                "chartType": "line",
                "mini": True,
                "labels": labels,
                "datasets": [
                    {
                        "label": METRIC_LABELS.get(mkey, mkey),
                        "data": score_series,
                        "color": color,
                        "fillColor": _hex_to_rgba(color, 0.35),
                    }
                ],
                "yFallback": {"min": 0, "max": 100},
            }
            badge = METRIC_SOURCES.get(mkey, "KPI")
            badge_short = badge[:14] + "…" if len(badge) > 16 else badge
            score_txt = f"{score:.2f}".rstrip("0").rstrip(".") if score is not None else "—"
            widgets.append(
                f"<article class='vitals-widget-card'>"
                f"<div class='vitals-widget-head'>"
                f"<span class='vitals-widget-title'>{html.escape(METRIC_LABELS.get(mkey, mkey))}</span>"
                f"<span class='vitals-widget-badge' title='{html.escape(badge)}'>"
                f"{html.escape(badge_short)}</span></div>"
                f"<div class='vitals-widget-body'>"
                f"<p class='vitals-widget-hero'>{html.escape(hist or '—')}</p>"
                f"<p class='vitals-widget-score' title='Score'>{html.escape(score_txt)}</p>"
                f"</div>"
                f"<div class='vitals-widget-spark'><canvas id='{cid}' "
                f"aria-label='{html.escape(METRIC_LABELS.get(mkey, mkey))} trend'></canvas></div>"
                f"</article>"
            )

        if not widgets:
            continue
        sections.append(
            f"<section class='vitals-pillar-section' style='--pillar-accent:{color};"
            f"--pillar-accent-bg:{accent_bg}'>"
            f"<h3 class='vitals-pillar-title'>"
            f"<span class='vitals-pillar-name'>{html.escape(PILLAR_LABELS.get(pk, pk))}</span>"
            f"<span class='vitals-pillar-meta'>{weight}%</span></h3>"
            f"<div class='vitals-pillar-grid'>{''.join(widgets)}</div>"
            f"</section>"
        )

    return f"<div id='vitals-compact-grid'>{''.join(sections)}</div>" if sections else ""


def _shm_ee_top_bar(*, focus_period: str | None, n_metrics: int) -> str:
    period_txt = _period_display(focus_period) if focus_period else "—"
    return (
        f"<div class='ee-top-bar'>"
        f"<div>"
        f"<h2>SHM Dashboard <span style='font-size:13px;font-weight:400;color:var(--color-text-secondary)'>"
        f"— Everyday Experience v1</span></h2>"
        f"<p>Column: <strong>{html.escape(period_txt)}</strong> · "
        f"<a href='{html.escape(SHM_VIEW_BASE)}' target='_blank' rel='noopener' "
        f"style='color:#93c5fd;text-decoration:none;'>shmview.arlocloud.com</a></p>"
        f"</div>"
        f"<div style='display:flex;flex-wrap:wrap;gap:8px;'>"
        f"<span class='ee-badge ee-badge-info'>5 Pillars</span>"
        f"<span class='ee-badge ee-badge-info'>{n_metrics} Metrics</span>"
        f"</div></div>"
    )


def _build_shm_pillar_dashboard(
    pillar_key: str,
    *,
    intent: ShmQueryIntent,
    pillars: dict[str, Any],
    metrics: dict[str, Any],
    periods: list[str],
    focus_period: str | None,
) -> tuple[list[str], str]:
    """shmview pillar detail view: metric cards + trend chart."""
    if not periods or pillar_key not in PILLAR_DASHBOARD_DEFS:
        return [], ""

    pdef = PILLAR_DASHBOARD_DEFS[pillar_key]
    pidx = PILLAR_KEYS.index(pillar_key)
    color = PILLAR_COLORS[pidx]
    fill = PILLAR_FILL_COLORS[pidx]
    fp = focus_period or periods[-1]
    uid = uuid.uuid4().hex[:8]
    labels = [_period_short_label(p) for p in periods]
    charts: dict[str, Any] = {}

    pillar_val = (pillars.get(pillar_key) or {}).get(fp)
    score_color = _shm_score_color(_parse_numeric(str(pillar_val) if pillar_val is not None else None))

    metric_keys = list(pdef["metrics"])
    if intent.metrics_filter:
        metric_keys = [k for k in metric_keys if k in intent.metrics_filter]

    cards: list[str] = []
    for mi, mkey in enumerate(metric_keys):
        hist, score = _metric_at_period(metrics, mkey, fp)
        if hist is None and score is None:
            continue
        cid = f"shm_detail_spark_{uid}_{mi}"
        score_series = _metric_score_series(metrics, mkey, periods)
        charts[cid] = {
            "chartType": "line",
            "mini": True,
            "labels": labels,
            "datasets": [
                {
                    "label": METRIC_LABELS.get(mkey, mkey),
                    "data": score_series,
                    "color": color,
                    "fillColor": fill + "88",
                }
            ],
            "yFallback": {"min": 0, "max": 100},
        }
        cards.append(
            f"<article class='ee-metric-card'>"
            f"<div class='ee-metric-name'>{html.escape(METRIC_LABELS.get(mkey, mkey))}</div>"
            f"<div class='ee-metric-row'>"
            f"<div class='ee-metric-current'>{html.escape(hist or '—')}</div>"
            f"<div class='ee-metric-spark'><canvas id='{cid}'></canvas></div>"
            f"</div>"
            f"<div class='ee-metric-footer'>"
            f"{_score_badge_html(score)}"
            f"<span class='ee-metric-source'>"
            f"{html.escape(METRIC_SOURCES.get(mkey, 'shmview KPI history'))}</span>"
            f"</div></article>"
        )

    bar_id = f"shm_pillar_bar_{uid}"
    pillar_scores = _pillar_series(pillars, pillar_key, periods)
    charts[bar_id] = {
        "chartType": "line",
        "labels": labels,
        "datasets": [
            {
                "label": pdef["short_name"],
                "data": pillar_scores,
                "color": color,
                "fillColor": fill + "44",
            }
        ],
        "ySuffix": "%",
        "yFallback": {"min": 60, "max": 105},
    }

    pillar_score_txt = html.escape(str(pillar_val) if pillar_val is not None else "—")
    html_block = (
        f"{_shm_ee_css_block()}"
        f"<div class='shm-ee-dash'>"
        f"{_shm_ee_top_bar(focus_period=fp, n_metrics=len(metric_keys))}"
        f"<div class='ee-nav-row'>"
        f"<span class='ee-nav-crumb'>{html.escape(pdef['name'])} · {_period_display(fp)}</span>"
        f"<span style='margin-left:auto;font-size:20px;font-weight:500;color:{score_color};'>"
        f"{pillar_score_txt}</span></div>"
        f"<div class='ee-metrics-grid'>{''.join(cards)}</div>"
        f"<div class='ee-detail-chart-wrap'><canvas id='{bar_id}' "
        f"aria-label='{html.escape(pdef['name'])} score trend'></canvas></div>"
        f"</div>"
    )
    return [html_block], _chartjs_bundle_script(charts)


def _build_shm_overview_dashboard(
    *,
    pillars: dict[str, Any],
    metrics: dict[str, Any],
    periods: list[str],
    focus_period: str | None,
    pillars_filter: set[str] | None = None,
    metrics_filter: set[str] | None = None,
) -> tuple[list[str], str]:
    """shmview main screen: overall SHM score, 5 pillars, multi-line chart, KPI grid."""
    if not periods:
        return [], ""

    fp = focus_period or periods[-1]
    uid = uuid.uuid4().hex[:8]
    labels = [_period_short_label(p) for p in periods]
    charts: dict[str, Any] = {}

    overall_val = _overall_shm_value(pillars, fp)
    overall_series = _overall_shm_series(pillars, periods)
    overall_spark = f"shm_ee_spark_{uid}"
    charts[overall_spark] = {
        "chartType": "line",
        "mini": True,
        "labels": labels,
        "datasets": [
            {
                "label": "SHM Score",
                "data": overall_series,
                "color": "#a1a1aa",
                "fillColor": "#3f3f4644",
            }
        ],
        "yFallback": {"min": 80, "max": 95},
    }

    hero_chart = f"chartOverall_{uid}"
    charts[hero_chart] = {
        "chartType": "line",
        "labels": labels,
        "datasets": [
            {
                "label": "SHM Score",
                "data": overall_series,
                "color": "#a1a1aa",
                "fillColor": "#3f3f4644",
            }
        ],
        "ySuffix": "%",
        "yFallback": {"min": 80, "max": 95},
    }

    pillars_multi = f"shm_ee_score_{uid}"
    pillar_datasets = []
    pillar_blocks: list[str] = []
    for i, key in enumerate(PILLAR_KEYS):
        pdef = PILLAR_DASHBOARD_DEFS.get(key, {})
        score_raw = (pillars.get(key) or {}).get(fp)
        score_num = _parse_numeric(str(score_raw) if score_raw is not None else None)
        n_metrics = len(pdef.get("metrics") or ())
        spark_id = f"shm_ee_psp_{uid}_{i}"
        pillar_series = _pillar_series(pillars, key, periods)
        charts[spark_id] = {
            "chartType": "line",
            "mini": True,
            "labels": labels,
            "datasets": [
                {
                    "label": pdef.get("short_name") or PILLAR_LABELS.get(key, key),
                    "data": pillar_series,
                    "color": PILLAR_COLORS[i],
                    "fillColor": PILLAR_FILL_COLORS[i] + "88",
                }
            ],
            "yFallback": {"min": 60, "max": 105},
        }
        pillar_blocks.append(
            f"<div class='ee-pillar-block'>"
            f"<div class='ee-pname'>{html.escape(PILLAR_LABELS.get(key, key))}</div>"
            f"<div class='ee-pwt'>Weight {PILLAR_WEIGHTS[i]}% · {n_metrics} metrics "
            f"({_period_display(fp)})</div>"
            f"<div class='ee-pscore' style='color:{PILLAR_COLORS[i]}'>"
            f"{html.escape(str(score_raw) if score_raw is not None else '—')}</div>"
            f"<div class='ee-pillar-spark'><canvas id='{spark_id}' "
            f"aria-label='{html.escape(PILLAR_LABELS.get(key, key))} trend'></canvas></div>"
            f"</div>"
        )
        pillar_datasets.append(
            {
                "label": pdef.get("short_name") or PILLAR_LABELS.get(key, key),
                "data": pillar_series,
                "color": PILLAR_COLORS[i],
                "fillColor": PILLAR_FILL_COLORS[i] + "44",
            }
        )

    charts[pillars_multi] = {
        "chartType": "line",
        "labels": labels,
        "datasets": pillar_datasets,
        "ySuffix": "%",
        "yFallback": {"min": 60, "max": 105},
    }

    ov_txt = f"{overall_val:.1f}" if overall_val is not None else "—"
    ov_color = _shm_score_color(overall_val)
    n_metrics = len([k for k in METRIC_LABELS if k in metrics])

    compact_grid = _build_vitals_compact_grid(
        pillars_filter=pillars_filter,
        metrics_filter=metrics_filter,
        metrics=metrics,
        periods=periods,
        focus_period=fp,
        uid=uid,
        charts=charts,
    )

    html_block = (
        f"{_shm_ee_css_block()}"
        f"<div class='shm-ee-dash'>"
        f"{_shm_ee_top_bar(focus_period=fp, n_metrics=n_metrics)}"
        f"<div class='ee-summary-box ee-summary-box--cols'>"
        f"<div class='ee-summary-col'>"
        f"<div class='ee-section-title'>Overall SHM Score</div>"
        f"<div class='ee-overall-shm-row'>"
        f"<div class='ee-score-single'>"
        f"<div class='ee-label'>SHM Score</div>"
        f"<div class='ee-val' style='color:{ov_color}'>{html.escape(ov_txt)}</div>"
        f"<div class='ee-sub'>{html.escape(_period_display(fp))} · weighted from pillar scores "
        f"(20/30/30/10/10)</div>"
        f"<div class='ee-score-spark'><canvas id='{overall_spark}' "
        f"aria-label='SHM overall score trend'></canvas></div>"
        f"</div>"
        f"<div class='ee-hero-overall-chart'>"
        f"<canvas id='{hero_chart}' aria-label='Overall SHM score trend'></canvas>"
        f"</div></div></div>"
        f"<div class='ee-summary-vdivider' aria-hidden='true'></div>"
        f"<div class='ee-summary-col'>"
        f"<div class='ee-section-title'>Pillars</div>"
        f"<div class='ee-pillars-row'>"
        f"<div class='ee-all-pillars-chart'>"
        f"<canvas id='{pillars_multi}' aria-label='All pillar scores trend'></canvas>"
        f"</div>"
        f"<div class='ee-pillar-overview'>{''.join(pillar_blocks)}</div>"
        f"</div></div></div>"
    )
    if compact_grid:
        html_block += (
            f"<div class='ee-section-title' id='shm-ee-key-metrics-title'>"
            f"Key metrics · {_period_display(fp)}</div>"
            f"{compact_grid}"
        )
    html_block += "</div>"

    return [html_block], _chartjs_bundle_script(charts)


def _chart_panel(canvas_id: str, title: str, height: int = 260) -> str:
    return (
        f"<div style='background:#fff;padding:14px;border-radius:8px;border:1px solid #e2e8f0;"
        f"margin:14px 0;box-shadow:0 1px 3px rgba(0,0,0,0.06);'>"
        f"<div style='font-size:14px;font-weight:600;color:#0f172a;margin-bottom:8px;'>"
        f"{html.escape(title)}</div>"
        f"<div style='position:relative;height:{height}px;'>"
        f"<canvas id='{html.escape(canvas_id)}'></canvas></div></div>"
    )


def _chartjs_line_script(charts: dict[str, Any]) -> str:
    return _chartjs_bundle_script(charts)


def _build_shm_charts(
    intent: ShmQueryIntent,
    *,
    pillars: dict[str, Any],
    metrics: dict[str, Any],
    periods: list[str],
) -> tuple[list[str], str]:
    """Return HTML panels + Chart.js script for the query intent."""
    if not intent.want_chart or not periods:
        return [], ""

    labels = [_period_display(p) for p in periods]
    charts: dict[str, Any] = {}
    html_parts: list[str] = []
    uid = uuid.uuid4().hex[:8]

    if intent.focus_overall or (
        not intent.pillars_filter and not intent.metrics_filter and intent.want_chart
    ):
        cid = f"shm_overall_{uid}"
        series = _overall_shm_series(pillars, periods)
        if any(v is not None for v in series):
            html_parts.append(_chart_panel(cid, "Overall SHM Score"))
            charts[cid] = {
                "labels": labels,
                "datasets": [
                    {
                        "label": "SHM Score",
                        "data": series,
                        "color": "#f87171",
                    }
                ],
                "ySuffix": "%",
                "yFallback": {"min": 80, "max": 95},
            }

    pillar_keys = (
        [k for k in PILLAR_KEYS if k in intent.pillars_filter]
        if intent.pillars_filter
        else []
    )
    if pillar_keys and not intent.metrics_filter:
        cid = f"shm_pillars_{uid}"
        datasets = []
        for i, key in enumerate(pillar_keys):
            datasets.append(
                {
                    "label": PILLAR_LABELS.get(key, key),
                    "data": _pillar_series(pillars, key, periods),
                    "color": PILLAR_COLORS[PILLAR_KEYS.index(key)],
                }
            )
        title = PILLAR_LABELS.get(pillar_keys[0], "Pillar") if len(pillar_keys) == 1 else "Pillar scores"
        html_parts.append(_chart_panel(cid, title))
        charts[cid] = {"labels": labels, "datasets": datasets, "ySuffix": "%"}

    metric_keys: list[str] = []
    if intent.metrics_filter:
        metric_keys = sorted(intent.metrics_filter)

    rating_keys = [k for k in metric_keys if k in ("app-rating-ios", "app-rating-android")]
    other_keys = [k for k in metric_keys if k not in rating_keys]

    if rating_keys:
        cid = f"shm_ratings_{uid}"
        datasets = []
        colors = {"app-rating-ios": "#2563eb", "app-rating-android": "#16a34a"}
        for key in rating_keys:
            datasets.append(
                {
                    "label": METRIC_LABELS.get(key, key),
                    "data": _metric_hist_series(metrics, key, periods),
                    "color": colors.get(key, "#0891b2"),
                }
            )
        html_parts.append(_chart_panel(cid, "App Store Ratings"))
        charts[cid] = {
            "labels": labels,
            "datasets": datasets,
            "yFallback": {"min": 3.0, "max": 5.0},
        }

    for key in other_keys[:4]:
        cid = f"shm_metric_{key.replace('-', '_')}_{uid}"
        html_parts.append(_chart_panel(cid, METRIC_LABELS.get(key, key)))
        charts[cid] = {
            "labels": labels,
            "datasets": [
                {
                    "label": METRIC_LABELS.get(key, key),
                    "data": _metric_hist_series(metrics, key, periods),
                    "color": "#0891b2",
                }
            ],
        }

    if not charts and intent.pillars_filter:
        key = next(iter(intent.pillars_filter))
        cid = f"shm_pillar_{uid}"
        html_parts.append(_chart_panel(cid, PILLAR_LABELS.get(key, key)))
        charts[cid] = {
            "labels": labels,
            "datasets": [
                {
                    "label": PILLAR_LABELS.get(key, key),
                    "data": _pillar_series(pillars, key, periods),
                    "color": PILLAR_COLORS[PILLAR_KEYS.index(key)],
                }
            ],
            "ySuffix": "%",
        }

    script = _chartjs_bundle_script(charts) if charts else ""
    return html_parts, script


def _build_direct_answer(
    intent: ShmQueryIntent,
    *,
    pillars: dict[str, Any],
    metrics: dict[str, Any],
    periods: list[str],
    latest: str | None,
    live_ratings: dict[str, Any] | None,
) -> str:
    period = intent.target_period or latest
    if not period:
        return ""

    lines: list[str] = []

    if intent.focus_overall:
        val = _overall_shm_value(pillars, period)
        if val is not None:
            lines.append(
                f"<strong>Overall SHM Score</strong> ({_period_display(period)}): "
                f"<span style='font-size:22px;color:#0891b2;'>{val}%</span>"
            )

    if intent.pillars_filter:
        for key in intent.pillars_filter:
            raw = (pillars.get(key) or {}).get(period)
            if raw is not None:
                lines.append(
                    f"<strong>{html.escape(PILLAR_LABELS.get(key, key))}</strong> "
                    f"({_period_display(period)}): "
                    f"<span style='font-size:20px;color:#ea580c;'>{html.escape(str(raw))}%</span>"
                )

    if intent.metrics_filter:
        for key in sorted(intent.metrics_filter):
            cell = (metrics.get(key) or {}).get(period) or {}
            hist = score = None
            if isinstance(cell, dict):
                hist = cell.get("hist")
                score = cell.get("score")
            else:
                hist = str(cell)
            label = METRIC_LABELS.get(key, key)
            if hist is not None:
                detail = html.escape(str(hist))
                if score:
                    detail += f" <span style='color:#64748b;font-size:13px;'>(score {html.escape(str(score))})</span>"
                lines.append(
                    f"<strong>{html.escape(label)}</strong> ({_period_display(period)}): "
                    f"<span style='font-size:20px;color:#2563eb;'>{detail}</span>"
                )

    if (
        live_ratings
        and live_ratings.get("ok")
        and period == latest
        and (not intent.metrics_filter or intent.metrics_filter & {"app-rating-ios", "app-rating-android"})
    ):
        m = live_ratings.get("metrics") or {}
        for plat_key, plat_label, metric_key in (
            ("app_rating_ios", "iOS App Store", "app-rating-ios"),
            ("app_rating_android", "Android Play Store", "app-rating-android"),
        ):
            if intent.metrics_filter and metric_key not in intent.metrics_filter:
                continue
            block = m.get(plat_key) or {}
            val = block.get("value")
            if val is not None:
                lines.append(
                    f"<strong>{html.escape(plat_label)} rating</strong> (live Tableau, "
                    f"{html.escape(str(live_ratings.get('calendar_month', '')))}): "
                    f"<span style='font-size:20px;color:#2563eb;'>{html.escape(str(val))}</span>"
                )

    if not lines and intent.target_period:
        return (
            f"<p style='margin:0;'>Period <strong>{html.escape(_period_display(period))}</strong> "
            f"(<code>{html.escape(period)}</code>) selected from shmview KPI history.</p>"
        )
    if not lines:
        return ""

    return (
        "<div style='background:linear-gradient(135deg,#ecfeff,#f0fdfa);border:1px solid #a5f3fc;"
        "border-radius:10px;padding:16px 18px;margin:0 0 16px;'>"
        "<div style='font-size:12px;font-weight:600;color:#0e7490;margin-bottom:8px;"
        "text-transform:uppercase;letter-spacing:0.04em;'>Answer</div>"
        + "".join(f"<p style='margin:6px 0;'>{ln}</p>" for ln in lines)
        + "</div>"
    )


def _resolve_metric_filter(question: str) -> tuple[set[str] | None, set[str] | None]:
    """Return (metric_keys or None=all, pillar_keys or None=all)."""
    q = (question or "").lower()
    metrics: set[str] = set()
    pillars: set[str] = set()

    if re.search(r"\b(?:customer\s+engagement|engagement\s+pillar|compromiso)\b", q):
        pillars.add("customer_engagement")
        metrics.update(METRIC_GROUPS["engagement"])
    if re.search(r"\b(?:protect\s+and\s+connect|protect\s*&\s*connect|crash[\s-]?free)\b", q):
        pillars.add("protect_and_connect")
        metrics.update(METRIC_GROUPS["protect"])
    if re.search(
        r"\b(?:customer\s+satisfaction|customer\s+service|csat|app\s+ratings?|"
        r"care\s+volume|satisfacción|satisfaccion)\b",
        q,
    ):
        pillars.add("customer_satisfaction")
        metrics.update(METRIC_GROUPS["satisfaction"])
    if re.search(r"\b(?:smart\s+ai|ai\s+adoption|ai\s+enablement)\b", q):
        pillars.add("smart_ai_adoption")
        metrics.update(METRIC_GROUPS["ai"])
    if re.search(r"\b(?:onboarding|median\s+onboarding|needed\s+help)\b", q):
        pillars.add("onboarding")
        metrics.update(METRIC_GROUPS["onboarding"])

    if re.search(r"\bapp\s+store\b", q) and not re.search(r"\bplay\s+store\b", q):
        metrics.add("app-rating-ios")
    if re.search(r"\b(?:play\s+store|google\s+play)\b", q) and not re.search(r"\bapp\s+store\b", q):
        metrics.add("app-rating-android")

    explicit_rating = bool(
        re.search(r"\b(?:app\s+store\s+rating|app\s+rating|play\s+store\s+rating|rating)\b", q)
    )
    if explicit_rating and (_IOS_RE.search(q) or re.search(r"\bapp\s+store\b", q)):
        metrics.add("app-rating-ios")
        metrics -= _IOS_METRICS - {"app-rating-ios", "firebase-crash-ios"}
    if explicit_rating and (_ANDROID_RE.search(q) or re.search(r"\b(?:play\s+store|google\s+play)\b", q)):
        metrics.add("app-rating-android")
        metrics -= _ANDROID_METRICS - {"app-rating-android", "firebase-crash-android"}

    if not explicit_rating:
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

    all_metrics: dict[str, Any] = history.get("metrics") or {}
    all_pillars: dict[str, Any] = history.get("pillars") or {}
    periods = _sorted_periods(history.get("periods") or [])
    if not periods:
        for mdata in all_metrics.values():
            if isinstance(mdata, dict):
                periods = _sorted_periods(list(mdata.keys()))
                break
    latest = _latest_period(periods)
    intent = _parse_shm_query(question, periods)

    metrics_filter = intent.metrics_filter
    pillars_filter = intent.pillars_filter
    show_periods = intent.chart_periods or (periods[-6:] if len(periods) > 6 else periods)

    focus_period = intent.target_period or latest
    trend_periods = list(periods)
    dashboard_pillar = _resolve_dashboard_pillar(intent)
    wants_table = _wants_table(question)
    chart_script = ""
    show_dashboard = False

    parts: list[str] = ["<div class='shm-metrics-report'>"]

    if not wants_table and periods:
        if dashboard_pillar:
            dash_html, chart_script = _build_shm_pillar_dashboard(
                dashboard_pillar,
                intent=intent,
                pillars=all_pillars,
                metrics=all_metrics,
                periods=trend_periods,
                focus_period=focus_period,
            )
        else:
            dash_html, chart_script = _build_shm_overview_dashboard(
                pillars=all_pillars,
                metrics=all_metrics,
                periods=trend_periods,
                focus_period=focus_period,
                pillars_filter=pillars_filter,
                metrics_filter=metrics_filter,
            )
        if dash_html:
            parts.extend(dash_html)
            show_dashboard = True

    if not show_dashboard:
        parts.extend(
            [
                "<h2 style='color:#0891b2;margin:0 0 8px;'>SHM — Service Health Management</h2>",
                f"<p style='font-size:13px;color:#64748b;margin:0 0 14px;'>"
                f"Source: <a href='{html.escape(base_url)}' target='_blank'>{html.escape(base_url)}</a>"
                f" · KPI history ({len(periods)} periods)"
                + (
                    f" · latest <strong>{html.escape(_period_display(latest))}</strong> "
                    f"({html.escape(latest)})"
                    if latest
                    else ""
                )
                + "</p>",
            ]
        )
        answer = _build_direct_answer(
            intent,
            pillars=all_pillars,
            metrics=all_metrics,
            periods=periods,
            latest=latest,
            live_ratings=live_ratings,
        )
        if answer:
            parts.append(answer)

        chart_html, chart_script = _build_shm_charts(
            intent,
            pillars=all_pillars,
            metrics=all_metrics,
            periods=show_periods,
        )
        parts.extend(chart_html)

    compact = show_dashboard or (intent.focused and (intent.target_period or intent.want_chart))
    table_periods = show_periods if compact else (periods[-6:] if len(periods) > 6 else periods)

    if show_dashboard:
        pass
    elif all_pillars and (not compact or pillars_filter or not metrics_filter):
        parts.append("<h3 style='margin:16px 0 8px;'>Pillar scores</h3>")
        pillar_rows: list[list[str]] = []
        for key, label in PILLAR_LABELS.items():
            if pillars_filter and key not in pillars_filter:
                continue
            pdata = all_pillars.get(key) or {}
            if not isinstance(pdata, dict):
                continue
            row = [label]
            for p in table_periods:
                row.append(str(pdata.get(p, "—")))
            if latest and not compact:
                row.append(str(pdata.get(latest, "—")))
            pillar_rows.append(row)
        if pillar_rows:
            hdr = ["Pillar"] + [_period_display(p) for p in table_periods]
            if latest and not compact:
                hdr.append("Latest")
            parts.append(_render_table(hdr, pillar_rows))

    if not show_dashboard:
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
                for p in table_periods:
                    cell = mdata.get(p) or {}
                    if isinstance(cell, dict):
                        hist = cell.get("hist", "—")
                        score = cell.get("score")
                        row.append(f"{hist}" + (f" ({score})" if score else ""))
                    else:
                        row.append(str(cell))
                if latest and not compact:
                    cell = mdata.get(latest) or {}
                    if isinstance(cell, dict):
                        hist = cell.get("hist", "—")
                        score = cell.get("score")
                        row.append(f"{hist}" + (f" ({score})" if score else ""))
                    else:
                        row.append(str(cell))
                mrows.append(row)
            hdr = ["Metric"] + [_period_display(p) for p in table_periods]
            if latest and not compact:
                hdr.append("Latest")
            parts.append(_render_table(hdr, mrows))

    if live_ratings and live_ratings.get("ok") and not show_dashboard:
        m = live_ratings.get("metrics") or {}
        show_live = metrics_filter is None or bool(
            metrics_filter & set(METRIC_GROUPS["satisfaction"])
        )
        if show_live and not compact:
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
    if weights and not metrics_filter and not compact:
        top_w = sorted(weights.items(), key=lambda x: -float(x[1] or 0))[:8]
        wtxt = ", ".join(f"{METRIC_LABELS.get(k, k)}={v}%" for k, v in top_w if v)
        if wtxt:
            parts.append(f"<p style='font-size:12px;color:#64748b;margin-top:12px;'>Top weights: {html.escape(wtxt)}</p>")

    if chart_script:
        parts.append(chart_script)

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

    periods = _sorted_periods(data.get("periods") or [])
    latest = _latest_period(periods)
    intent = _parse_shm_query(q, periods)

    live_ratings = None
    want_ratings = force_live or bool(
        re.search(
            r"\b(?:app\s+ratings?|customer\s+satisfaction|customer\s+service|"
            r"play\s+store|app\s+store|satisfacción|satisfaccion)\b",
            q,
            re.I,
        )
    )
    if want_ratings and latest:
        rating_period = intent.target_period or latest
        if rating_period == latest or force_live:
            live_ratings = _fetch_live_app_ratings(rating_period)

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
