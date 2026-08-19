"""
Status Monitor Dashboard Tool
Real-time service health monitoring across all environments using Datadog APM
"""

import os
import re
import time
import json
import html
import threading
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import metrics persistence
from urllib.parse import quote, urlparse

from tools.metrics_persistence import (
    save_service_metrics,
    save_dashboard_snapshot,
    get_dashboard_history,
    get_service_eks_clusters,
    set_service_eks_clusters,
    sm_api_cache_get,
    sm_api_cache_set,
    clear_status_monitor_api_cache,
)

# Import Datadog dashboard utilities
from tools.datadog_dashboards import datadog_rest_api_base, datadog_ui_origin, get_dashboard_details
from tools.status_monitor_service_lists import (
    ADT_MONITOR_SERVICES,
    GENERAL_MONITOR_SERVICES,
    SAMSUNG_MONITOR_SERVICES,
    SOFTWARE_CATALOG_TREEMAP_EXTRAS,
)


def _sm_cluster_tokens_z1_z2(cluster_blob: str) -> tuple[bool, bool]:
    """
    Arlo-style cluster naming: Z1 → Ireland, Z2 → Oregon.

    - Standalone token: z1 / z2 (not glued to other alphanumeric ids).
    - Prefix: z1* / z2* (e.g. z1w, z2w, z1-prod) — cluster name encodes region in the suffix.
    """
    if not cluster_blob or not cluster_blob.strip():
        return False, False

    def _zone(zone: str) -> bool:
        # Standalone z1 / z2 (word-ish token)
        if re.search(rf"(?<![a-z0-9]){zone}(?![a-z0-9])", cluster_blob, re.I):
            return True
        # Prefix z1* / z2* (e.g. z2w, z1a, z1-eks)
        if re.search(rf"(?<![a-z0-9]){zone}(?=[a-z0-9\-_./])", cluster_blob, re.I):
            return True
        return False

    return _zone("z1"), _zone("z2")


def _sm_infer_service_region(svc: dict, *, page_environment: str | None = None) -> str:
    """
    Infer service region from EKS cluster names and known tokens.
    Returns: Oregon, Ireland, or Multi-region.

    Cluster names: Z1 / z1* → Ireland, Z2 / z2* → Oregon (case-insensitive). If multiple clusters
    span both, Multi-region. If clusters exist but neither z1 nor z2 appears → default region.
    Falls back to AWS region hints (us-west-2, eu-west-1, …), then default.

    For ADT and Samsung pages/wall sections, every service is US-West (Oregon); unknown signal
    (e.g. HMSWEB with no cluster hint) defaults to Oregon instead of Ireland.
    """
    oregon_default = page_environment in (
        "adt",
        "adt_prod",
        "samsung",
        "samsung_prod",
        "cat",
        "cat_prod",
        "comcast",
        "comcast_prod",
    )

    def _default_unknown() -> str:
        return "Oregon" if oregon_default else "Ireland"

    clusters = svc.get("eks_clusters") or []
    if not isinstance(clusters, list):
        clusters = []
    cluster_blob = " ".join(str(x) for x in clusters if x)
    if cluster_blob.strip():
        oz1, oz2 = _sm_cluster_tokens_z1_z2(cluster_blob)
        if oz1 and oz2:
            return "Multi-region"
        if oz1:
            return "Ireland"
        if oz2:
            return "Oregon"
        return _default_unknown()

    haystacks = [str(x).lower() for x in clusters if x]
    for key in ("service", "environment"):
        v = svc.get(key)
        if isinstance(v, str) and v.strip():
            haystacks.append(v.lower())
    blob = " ".join(haystacks)
    if not blob:
        return _default_unknown()

    has_oregon = any(tok in blob for tok in ("us-west-2", "usw2", "oregon"))
    has_ireland = any(tok in blob for tok in ("eu-west-1", "euw1", "ireland"))
    if has_oregon and has_ireland:
        return "Multi-region"
    if has_oregon:
        return "Oregon"
    if has_ireland:
        return "Ireland"
    return _default_unknown()


def _sm_hover_json_attr(payload: dict) -> str:
    """HTML attribute value for data-sm-hover (JSON)."""
    return html.escape(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), quote=True)


def _status_monitor_int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(os.getenv(name, str(default)))
        return max(lo, min(hi, v))
    except (TypeError, ValueError):
        return default


def _sm_status_shows_issue_links(svc: dict) -> bool:
    """True when we show Splunk / PD issue links (problem signal)."""
    st = svc.get("status")
    if st in ("critical", "warning"):
        return True
    if int(svc.get("dd_monitor_alert_count") or 0) > 0:
        return True
    if int(svc.get("dd_monitor_alert_suffix_count") or 0) > 0:
        return True
    if svc.get("pd_incident"):
        return True
    if svc.get("traffic_drop"):
        return True
    if svc.get("high_latency"):
        return True
    return False


def _sm_sanitize_href_for_wall(value: str | None) -> str | None:
    """
    PagerDuty / API sometimes returns newlines or junk in hrefs; WebKit can throw
    (\"The string did not match the expected pattern\") on invalid href. Keep only
    well-formed http(s) URLs.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s or s == "#":
        return None
    s = "".join(ch for ch in s if ch not in "\r\n\0")
    s = s.strip()
    if not s:
        return None
    u = urlparse(s)
    if u.scheme in ("http", "https") and u.netloc:
        return s
    return None


def _sm_pagerduty_external_incidents_url() -> str:
    """Public external-status-dashboard incidents URL (same default board as home PagerDuty widget)."""
    dash_id = (os.getenv("PAGERDUTY_EXTERNAL_STATUS_DASHBOARD_ID") or "PRBJIO4").strip()
    raw = (os.getenv("PAGERDUTY_SUBDOMAIN") or "arlo").strip()
    raw = raw.replace("https://", "").replace("http://", "").split("/")[0]
    sub = (raw.split(".")[0] if raw else "arlo") or "arlo"
    return f"https://{sub}.pagerduty.com/external-status-dashboard/{dash_id}/incidents"


def _sm_dd_monitor_name_suffix_ab(name: str) -> bool:
    """
    True if monitor name ends with -a or -b (case-insensitive).

    Ops treat those suffixes as non-critical/noise; aligns with Datadog Manage
    filters such as NOT ("-A" OR "-B").
    """
    n = (name or "").strip()
    if len(n) < 2:
        return False
    return n[-2:].lower() in ("-a", "-b")


def _dd_monitors_manage_url(service_name: str, environment: str, dd_site: str) -> str:
    """
    Monitors / Manage page: service + env, Alert state, excluding -a/-b suffix monitors
    (same idea as org filters using NOT ("-A" OR "-B")).
    """
    sn = (service_name or "").strip()
    if not sn:
        return ""
    env = (environment or "").strip() or "production"
    q = f'service:"{sn}" env:{env} status:alert NOT ("-A" OR "-B")'
    return f"{datadog_ui_origin(dd_site)}/monitors/manage?q={quote(q, safe='')}"


def _dd_monitors_manage_url_all_alerts(service_name: str, environment: str, dd_site: str) -> str:
    """Manage UI: all Alert monitors for service+env (includes -a/-b tier)."""
    sn = (service_name or "").strip()
    if not sn:
        return ""
    env = (environment or "").strip() or "production"
    q = f'service:"{sn}" env:{env} status:alert'
    return f"{datadog_ui_origin(dd_site)}/monitors/manage?q={quote(q, safe='')}"


def _sm_rank_for_status(st: str | None) -> int:
    s = (st or "").strip().lower()
    if s in ("unknown", "inactive"):
        return 0
    if s == "healthy":
        return 1
    if s == "warning":
        return 2
    if s == "critical":
        return 3
    return 0


def _sm_status_from_rank(r: int) -> str:
    if r >= 3:
        return "critical"
    if r >= 2:
        return "warning"
    if r >= 1:
        return "healthy"
    return "unknown"


def _sm_merge_status_with_dd_alerts(current: str, alert_name_count: int) -> str:
    """1 firing monitor → at least warning; 2+ → at least critical. Takes max with APM-derived status."""
    n = int(alert_name_count or 0)
    if n <= 0:
        return current
    r_dd = 2 if n == 1 else 3
    return _sm_status_from_rank(max(_sm_rank_for_status(current), r_dd))


def _sm_dd_open_alert_counts(svc: dict) -> tuple[int, int, int]:
    """(non -a/-b alerts, -a/-b alerts, total open) for pills and tooltips."""
    n_c = int(svc.get("dd_monitor_alert_count") or 0) or len(svc.get("dd_monitor_alerts") or [])
    n_s = int(svc.get("dd_monitor_alert_suffix_count") or 0) or len(
        svc.get("dd_monitor_alerts_suffix_ab") or []
    )
    return n_c, n_s, n_c + n_s


def _sm_bump_min_warning_for_dd_suffix_alerts(current: str, suffix_alert_count: int) -> str:
    """
    -a / -b tier monitors do not change tile color (stay green when APM is healthy).
    Open counts are still shown on the DD pill.
    """
    return current


def _sm_dd_monitor_alerts_enabled() -> bool:
    v = (os.getenv("STATUS_MONITOR_DD_MONITOR_ALERTS") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _sm_attention_dd_alerts_cell_html(s: dict, dd_site: str) -> str:
    """Attention queue: boxed list of open Datadog monitors (same data as hover DD pill)."""
    if not _sm_dd_monitor_alerts_enabled():
        return "<span style='color:#94a3b8;font-size:10px;font-weight:600;'>—</span>"
    alerts = [str(x).strip() for x in (s.get("dd_monitor_alerts") or []) if str(x).strip()]
    suffix = [str(x).strip() for x in (s.get("dd_monitor_alerts_suffix_ab") or []) if str(x).strip()]
    n_c = len(alerts)
    n_s = len(suffix)
    total = int(s.get("dd_monitor_open_count") or 0) or (n_c + n_s)
    svc = str(s.get("service") or "")
    env = str(s.get("environment") or "")
    if total <= 0:
        return (
            "<div class='cc-dd-alerts cc-dd-alerts--none' title='No Datadog monitors in Alert'>"
            "<span class='cc-dd-alerts__count'>0</span>"
            "<span class='cc-dd-alerts__lbl'>alerts</span>"
            "</div>"
        )
    only_ab = n_c == 0 and n_s > 0
    dd_url = (s.get("dd_monitors_url_all_alerts") or s.get("dd_monitors_url") or "").strip()
    if not dd_url and svc and env:
        dd_url = (
            _dd_monitors_manage_url_all_alerts(svc, env, dd_site)
            if n_s
            else _dd_monitors_manage_url(svc, env, dd_site)
        )
    dd_url = _sm_sanitize_href_for_wall(dd_url) or ""
    box_cls = "cc-dd-alerts cc-dd-alerts--suffix" if only_ab else "cc-dd-alerts cc-dd-alerts--open"
    title = (
        f"{total} Datadog alert(s) (-a/-b tier; tile may stay green)"
        if only_ab
        else f"{total} Datadog monitor(s) in Alert"
    )
    lines: list[str] = []
    shown = 0
    max_show = 5
    for name in alerts:
        if shown >= max_show:
            break
        lines.append(f"<li>{html.escape(name)}</li>")
        shown += 1
    for name in suffix:
        if shown >= max_show:
            break
        lines.append(f"<li class='cc-dd-alerts__suffix'>{html.escape(name)}</li>")
        shown += 1
    rest = total - shown
    if rest > 0:
        lines.append(f"<li class='cc-dd-alerts__more'>+{rest} more</li>")
    inner = (
        f"<div class='cc-dd-alerts__head'>"
        f"<span class='cc-dd-alerts__count'>{total}</span>"
        f"<span class='cc-dd-alerts__lbl'>DD alert{'s' if total != 1 else ''}</span>"
        f"</div>"
        f"<ul class='cc-dd-alerts__list'>{''.join(lines)}</ul>"
    )
    if dd_url:
        url_e = html.escape(dd_url, quote=True)
        return (
            f"<a class='{box_cls}' href='{url_e}' target='_blank' rel='noopener noreferrer' "
            f"title='{html.escape(title, quote=True)}'>{inner}</a>"
        )
    return f"<div class='{box_cls}' title='{html.escape(title, quote=True)}'>{inner}</div>"


def _sm_splunk_service_search_url(service_name: str, timerange_hours: int = 24) -> str:
    """Splunk Cloud search URL for logs mentioning this APM service name."""
    sn = (service_name or "").strip()
    if not sn:
        return ""
    host = (os.getenv("SPLUNK_HOST") or "arlo.splunkcloud.com").strip()
    host = host.replace("https://", "").split("/")[0]
    locale = (os.getenv("SPLUNK_UI_LOCALE") or "en-US").strip()
    h = max(1, min(int(timerange_hours), 168))
    q = f"search index=* service={sn} earliest=-{h}h"
    return f"https://{host}/{locale}/app/search/search?q={quote(q, safe='')}"


def _sm_hover_service_payload(svc: dict, env: str, page_environment: str | None = None) -> dict:
    ek = svc.get("eks_clusters") or []
    if not isinstance(ek, list):
        ek = []
    spl_h = _status_monitor_int_env("STATUS_MONITOR_SPLUNK_SEARCH_HOURS", 24, 1, 168)
    spl_url = None
    if _sm_status_shows_issue_links(svc):
        su = _sm_splunk_service_search_url(str(svc.get("service") or ""), spl_h)
        spl_url = su if su else None
    pd_u = (svc.get("pd_incident_url") or "").strip()
    dda = list(svc.get("dd_monitor_alerts") or [])
    if not isinstance(dda, list):
        dda = []
    dda = dda[:32]
    dda_suf = list(svc.get("dd_monitor_alerts_suffix_ab") or [])
    if not isinstance(dda_suf, list):
        dda_suf = []
    dda_suf = dda_suf[:32]
    dd_n, dd_n_suf, dd_n_open = _sm_dd_open_alert_counts(svc)
    ddm = (svc.get("dd_monitors_url") or "").strip()
    if not ddm and svc.get("service") and env:
        ddm = _dd_monitors_manage_url(
            str(svc.get("service") or ""), str(env or ""), os.getenv("DD_SITE", "datadoghq.com")
        )
    ddm = ddm if ddm else None
    ddm_all = (svc.get("dd_monitors_url_all_alerts") or "").strip()
    if not ddm_all and svc.get("service") and env and dd_n_suf > 0:
        ddm_all = _dd_monitors_manage_url_all_alerts(
            str(svc.get("service") or ""), str(env or ""), os.getenv("DD_SITE", "datadoghq.com")
        )
    ddm_all = ddm_all if ddm_all else None
    return {
        "type": "service",
        "service": svc.get("service"),
        "environment": env,
        "region": _sm_infer_service_region(svc, page_environment=page_environment),
        "status": svc.get("status"),
        "error_rate": float(svc.get("error_rate") or 0),
        "requests": svc.get("requests"),
        "errors": svc.get("errors"),
        "p95_latency": svc.get("p95_latency"),
        "p99_latency": svc.get("p99_latency"),
        "eks_clusters": ek[:12],
        "pd_incident": bool(svc.get("pd_incident")),
        "pd_incident_url": pd_u if pd_u else None,
        "splunk_url": spl_url,
        "traffic_drop": bool(svc.get("traffic_drop")),
        "traffic_variance": svc.get("traffic_variance"),
        "high_latency": bool(svc.get("high_latency")),
        "dd_monitor_alerts": dda,
        "dd_monitor_alert_count": dd_n,
        "dd_monitor_alerts_suffix_ab": dda_suf,
        "dd_monitor_alert_suffix_count": dd_n_suf,
        "dd_monitor_open_count": dd_n_open,
        "dd_monitors_url": ddm,
        "dd_monitors_url_all_alerts": ddm_all,
    }


# Simple in-memory cache for status monitor data
_status_cache = {}
_hub_summary_cache = {}
_wall_data_cache = {}
_software_catalog_wall_cache = {}
# When each in-memory cache entry was stored (for force_refresh grace window)
_mem_cache_saved_at = {}
# Longer default TTL + env override reduces repeated full DD fan-out (CPU + rate limits)
_cache_ttl = _status_monitor_int_env("STATUS_MONITOR_CACHE_SECS", 180, 60, 900)
# SQLite-backed API cache (per-service DD health, PagerDuty, Arlo) — shared across hub/wall/dashboard
# Default 300s: reuse rows younger than 5 minutes instead of calling Datadog again
_db_api_cache_ttl = _status_monitor_int_env("STATUS_MONITOR_DB_CACHE_SECS", 300, 30, 900)
# User clicked Refresh: still reuse full response + DB rows if younger than this (seconds)
_FORCE_REFRESH_GRACE_SECS = _status_monitor_int_env("STATUS_MONITOR_FORCE_REFRESH_GRACE_SECS", 30, 5, 300)
# Extra live Datadog attempts when status is unknown (transient errors)
_UNKNOWN_RETRY_COUNT = _status_monitor_int_env("STATUS_MONITOR_UNKNOWN_RETRIES", 2, 0, 5)

# Parallel Datadog health checks per dashboard/hub mode (each task ~2 HTTP calls)
STATUS_MONITOR_DD_MAX_WORKERS = _status_monitor_int_env("STATUS_MONITOR_DD_MAX_WORKERS", 16, 2, 32)
STATUS_MONITOR_DD_MIN_WORKERS = _status_monitor_int_env("STATUS_MONITOR_DD_MIN_WORKERS", 4, 1, 16)
# Hub: parallel env batches (main + samsung + adt + red-us…). Higher = faster if DD rate limits allow.
STATUS_MONITOR_HUB_PARALLEL_ENVS = _status_monitor_int_env("STATUS_MONITOR_HUB_PARALLEL_ENVS", 6, 1, 6)
STATUS_MONITOR_EKS_MAX_WORKERS = _status_monitor_int_env("STATUS_MONITOR_EKS_MAX_WORKERS", 8, 1, 24)
# APM Status Wall: longer in-memory bucket + optional skip EKS (EKS = many extra Datadog calls per tile)
def _apm_status_wall_cache_bucket_secs() -> int:
    return _status_monitor_int_env("APM_STATUS_WALL_CACHE_SECS", 300, 60, 1200)


def _apm_status_wall_attach_eks(dd_env: str = "") -> bool:
    """Default on: EKS names feed region split (Oregon / Ireland). Set APM_STATUS_WALL_ATTACH_EKS=0 to skip (faster)."""
    v = (os.getenv("APM_STATUS_WALL_ATTACH_EKS") or "1").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    # Partner prod walls are single-region (Oregon); per-tile EKS fan-out adds minutes and causes 504/timeouts.
    if (dd_env or "").strip() in ("adt_prod", "cat_prod", "comcast_prod"):
        return False
    return v in ("1", "true", "yes", "on", "")


def _classic_status_wall_attach_eks() -> bool:
    """
    Classic /statuswall: per-tile EKS cluster lookups add many Datadog calls (common 504 behind 60s ALB).
    STATUS_MONITOR_WALL_ATTACH_EKS=0 skips (tooltips omit cluster names; wall loads faster).
    Unset: follows APM_STATUS_WALL_ATTACH_EKS so one knob can disable EKS on both walls.
    """
    v = (os.getenv("STATUS_MONITOR_WALL_ATTACH_EKS") or "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    return _apm_status_wall_attach_eks()


def _status_monitor_dashboard_attach_eks() -> bool:
    """
    /statusmonitor/<env>: per-service EKS cluster lookups add many aws/datadog calls (504 behind short ALB idle).
    Off by default; set STATUS_MONITOR_DASHBOARD_ATTACH_EKS=1 to re-enable cluster rows.
    """
    v = (os.getenv("STATUS_MONITOR_DASHBOARD_ATTACH_EKS") or "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    return False


def _apm_status_wall_header_light() -> bool:
    """If true (default), APM /apm-services uses one PD call + omits full Splunk badge fetch (PagerDuty + stub Splunk)."""
    v = (os.getenv("APM_STATUS_WALL_HEADER_LIGHT") or "1").strip().lower()
    return v in ("1", "true", "yes", "on", "")


def _wall_apm_header_badges_reuse_pd(pd_counts: dict, pd_api_key: str | None) -> dict:
    """Pills for APM page with light load: reuses org-wide PagerDuty counts; Splunk stub when header_light."""
    if pd_api_key:
        pd_badge = _wall_pd_badge(pd_counts)
    else:
        pd_badge = {
            "label": "PD",
            "status": "unknown",
            "short": "—",
            "detail": "PAGERDUTY_API_TOKEN not set",
        }
    omitted = {
        "label": "—",
        "status": "ok",
        "short": "—",
        "detail": "Omitted for fast APM load (set APM_STATUS_WALL_HEADER_LIGHT=0 for Splunk + board pills).",
    }
    return {
        "pagerduty": pd_badge,
        "splunk": omitted,
    }


_ADT_SPLUNK_DASHBOARD_URL = (
    "https://arlo.splunkcloud.com/en-US/app/search/p0_streaming_dashboard_pp"
)


def _wall_adt_splunk_badge_light() -> dict:
    """ADT Splunk pill without live P0 outlier queries (fast wall load; embed link preserved)."""
    return {
        "label": "SPL",
        "href": _ADT_SPLUNK_DASHBOARD_URL,
        "embed_url": "/embed/splunk-p0-adt",
        "panel_toggle": True,
        "p0_id": "p0_adt",
        "status": "ok",
        "short": "—",
        "detail": (
            "P0 ADT Splunk summary omitted for fast load. Click to open the dashboard, or set "
            "APM_STATUS_WALL_HEADER_LIGHT=0 to fetch live outlier counts (slower)."
        ),
    }


def _wall_adt_splunk_badge(timerange: int, force_refresh: bool) -> dict:
    """
    Splunk pill for ADT Status Wall: P0 ADT outlier summary + in-page embed panel.
    """
    base = {
        "label": "SPL",
        "href": _ADT_SPLUNK_DASHBOARD_URL,
        "embed_url": "/embed/splunk-p0-adt",
        "panel_toggle": True,
        "p0_id": "p0_adt",
    }
    if not (os.getenv("SPLUNK_TOKEN") or "").strip():
        return {
            **base,
            "status": "unknown",
            "short": "—",
            "detail": "SPLUNK_TOKEN not set",
        }
    try:
        from tools.splunk_tool import splunk_outliers_monitor_payload

        spl = splunk_outliers_monitor_payload(timerange)
        if not spl.get("success"):
            err = spl.get("error") or "unavailable"
            return {**base, "status": "unknown", "short": "—", "detail": err}
        adt_tool = None
        for t in spl.get("tools") or []:
            if (t or {}).get("id") == "p0_adt":
                adt_tool = t
                break
        tot = int((adt_tool or {}).get("total_outliers") or 0)
        th = int(spl.get("timerange_hours") or timerange or 0)
        if tot > 0:
            return {
                **base,
                "status": "warning",
                "short": f"{tot} out",
                "detail": (
                    f"P0 ADT global (zones z1–z4, not the selected tile): {tot} Splunk predict "
                    f"outlier(s) in the last {th}h. Yellow = outliers detected; not the same as a "
                    f"red/yellow Datadog tile. Click to open the full P0 ADT dashboard."
                ),
            }
        return {
            **base,
            "status": "ok",
            "short": "OK",
            "detail": (
                f"P0 ADT global (zones z1–z4): no Splunk predict outliers in the last {th}h. "
                f"Click to open the full P0 ADT dashboard (not filtered to one service unless you "
                f"filter inside Splunk)."
            ),
        }
    except Exception as e:
        return {
            **base,
            "status": "unknown",
            "short": "—",
            "detail": str(e)[:200],
        }


def _wall_apm_monitors_for_dd_env(
    dd_env: str,
    pd_counts: dict | None,
    pd_api_key: str | None,
    timerange: int,
    force_refresh: bool,
) -> dict:
    """PagerDuty + Splunk pills for APM wall; ADT env always gets Splunk embed badge."""
    if pd_api_key and pd_counts is not None:
        pd_badge = _wall_pd_badge(pd_counts)
    elif pd_api_key:
        try:
            counts, _ = get_pagerduty_status_counts(pd_api_key, force_refresh)
            pd_badge = _wall_pd_badge(counts)
        except Exception as e:
            pd_badge = {
                "label": "PD",
                "status": "unknown",
                "short": "—",
                "detail": str(e)[:200],
            }
    else:
        pd_badge = {
            "label": "PD",
            "status": "unknown",
            "short": "—",
            "detail": "PAGERDUTY_API_TOKEN not set",
        }
    if dd_env == "adt_prod":
        if _apm_status_wall_header_light():
            spl_badge = _wall_adt_splunk_badge_light()
        else:
            spl_badge = _wall_adt_splunk_badge(timerange, force_refresh)
    elif _apm_status_wall_header_light():
        spl_badge = _wall_apm_header_badges_reuse_pd(pd_counts or {}, pd_api_key)["splunk"]
    else:
        spl_badge = _wall_fetch_monitor_badges(timerange, force_refresh)["splunk"]
    return {"pagerduty": pd_badge, "splunk": spl_badge}


def _dd_health_worker_count(num_tasks: int) -> int:
    cap = min(STATUS_MONITOR_DD_MAX_WORKERS, max(STATUS_MONITOR_DD_MIN_WORKERS, num_tasks))
    return cap


# Datadog HTTP timeouts — too low causes false "no data" / 504 under parallel wall load
def _dd_query_timeout_secs() -> int:
    return _status_monitor_int_env("STATUS_MONITOR_DD_QUERY_TIMEOUT", 30, 5, 120)


def _dd_http_connect_timeout() -> int:
    return _status_monitor_int_env("STATUS_MONITOR_DD_HTTP_CONNECT_TIMEOUT", 15, 5, 60)


def _dd_http_read_timeout() -> int:
    return _status_monitor_int_env("STATUS_MONITOR_DD_HTTP_READ_TIMEOUT", 90, 15, 300)


def _dd_requests_timeout() -> tuple[int, int]:
    return (_dd_http_connect_timeout(), _dd_http_read_timeout())

# Datadog monitor search (error-rate override): short TTL to limit API load
_DD_MONITOR_SEARCH_CACHE = {}
_DD_MONITOR_SEARCH_LOCK = threading.Lock()
_DD_MONITOR_SEARCH_TTL = _status_monitor_int_env("STATUS_MONITOR_DD_MONITOR_CACHE_SECS", 120, 30, 600)

# Cache for dashboard services (so we don't fetch dashboard details every time)
_dashboard_services_cache = {}
_dashboard_services_cache_ttl = 3600  # 1 hour


def _https_get_with_retries(
    url,
    *,
    headers=None,
    params=None,
    timeout=None,
    label="HTTPS",
    max_attempts=5,
):
    if timeout is None:
        timeout = _dd_requests_timeout()
    """
    GET with backoff for flaky TLS (VPN/proxy) — SSLEOF, reset by peer, etc.
    Same symptom often hits Datadog, PagerDuty, and status.arlo.com together.
    """
    import requests
    from requests.exceptions import ChunkedEncodingError, ConnectionError, SSLError, Timeout

    headers = headers or {}
    last_exc = None
    for attempt in range(max_attempts):
        try:
            return requests.get(url, headers=headers, params=params, timeout=timeout)
        except (SSLError, ConnectionError, Timeout, ChunkedEncodingError) as e:
            last_exc = e
            if attempt < max_attempts - 1:
                delay = 0.6 * (2**attempt)
                print(
                    f"⚠️ {label}: transient error, retry {attempt + 1}/{max_attempts} "
                    f"in {delay:.1f}s: {e!s}"
                )
                time.sleep(delay)
            else:
                raise
    raise last_exc


def clear_status_cache():
    """Clear the status monitor cache - useful after config changes"""
    global _status_cache, _hub_summary_cache, _wall_data_cache, _software_catalog_wall_cache, _mem_cache_saved_at, _DD_MONITOR_SEARCH_CACHE
    _status_cache.clear()
    _hub_summary_cache.clear()
    _wall_data_cache.clear()
    _software_catalog_wall_cache.clear()
    _mem_cache_saved_at.clear()
    with _DD_MONITOR_SEARCH_LOCK:
        _DD_MONITOR_SEARCH_CACHE.clear()
    clear_status_monitor_api_cache()
    print("🧹 Status monitor cache cleared (memory + DB API cache)")


def _effective_db_cache_ttl_secs(force_refresh: bool) -> float:
    """SQLite API cache: on user Refresh use short TTL so stale rows refetch; normal uses full TTL."""
    if force_refresh:
        return float(_FORCE_REFRESH_GRACE_SECS)
    return float(_db_api_cache_ttl)


def _read_sm_mem_cache(cache_dict: dict, cache_key: str, force_refresh: bool):
    """
    In-memory HTML/JSON cache. If force_refresh, only return hit if saved within grace window.
    """
    if cache_key not in cache_dict:
        return None
    if not force_refresh:
        return cache_dict[cache_key]
    ts = _mem_cache_saved_at.get(cache_key)
    if ts is not None and time.time() - ts < _FORCE_REFRESH_GRACE_SECS:
        return cache_dict[cache_key]
    return None


def _write_sm_mem_cache(cache_dict: dict, cache_key: str, value) -> None:
    cache_dict[cache_key] = value
    _mem_cache_saved_at[cache_key] = time.time()


def get_services_from_dashboard(dashboard_id: str, cache_key: str = None) -> list:
    """
    Extract all service names from a Datadog dashboard dynamically
    
    Args:
        dashboard_id: Datadog dashboard ID (e.g., 'cum-ivw-92c' for ADT)
        cache_key: Optional cache key (default: dashboard_id)
    
    Returns:
        List of service names found in the dashboard
    """
    if cache_key is None:
        cache_key = dashboard_id
    
    # Check cache first
    if cache_key in _dashboard_services_cache:
        cached_data = _dashboard_services_cache[cache_key]
        if time.time() - cached_data['timestamp'] < _dashboard_services_cache_ttl:
            print(f"📦 Using cached services for dashboard {dashboard_id}: {len(cached_data['services'])} services")
            return cached_data['services']
    
    # Fetch dashboard details
    dd_api_key = os.getenv("DATADOG_API_KEY")
    dd_app_key = os.getenv("DATADOG_APP_KEY")
    dd_site = os.getenv("DATADOG_SITE", "datadoghq.com")
    
    if not dd_api_key or not dd_app_key:
        print("⚠️ Datadog credentials not available, using fallback service list")
        return []
    
    try:
        print(f"🔍 Fetching services from Datadog dashboard {dashboard_id}...")
        details = get_dashboard_details(dd_api_key, dd_app_key, dd_site, dashboard_id)
        
        if not details or 'widgets' not in details:
            print(f"⚠️ Could not fetch dashboard {dashboard_id}")
            return []
        
        services = set()
        
        # Helper function to extract service from queries
        def extract_services_from_queries(queries):
            if not queries:
                return []
            found = []
            for query in queries:
                if isinstance(query, dict):
                    query_str = query.get('query', '')
                elif isinstance(query, str):
                    query_str = query
                else:
                    continue
                
                # Extract service from query string like "service:backend-pp" or "service:backend-pp-samsung-prod"
                import re
                matches = re.findall(r'service:([a-zA-Z0-9\-_]+)', query_str)
                found.extend(matches)
            return found
        
        # Extract services from all widgets (recursive for nested widgets)
        def process_widget(widget_def, depth=0):
            """Process a single widget and extract services (handles nested widgets)"""
            widget_type = widget_def.get('type', '')
            found = []
            
            # Check trace_service widgets
            if widget_type == 'trace_service':
                service = widget_def.get('service', '')
                if service:
                    found.append(service)
            
            # Check all query-based widgets
            if widget_type in ['timeseries', 'query_value', 'query_table', 'toplist', 'heatmap', 'distribution', 'change']:
                # Extract from queries
                requests = widget_def.get('requests', [])
                found.extend(extract_services_from_queries(requests))
                
                # Also check formulas/queries array
                if 'queries' in widget_def:
                    found.extend(extract_services_from_queries(widget_def['queries']))
                
                # Extract service name from widget title (e.g., "backend-hmsguard -> Requests")
                title = widget_def.get('title', '')
                if title:
                    import re
                    title_match = re.match(r'^([a-zA-Z0-9\-_]+)\s*->', title)
                    if title_match:
                        service_name = title_match.group(1)
                        found.append(service_name)
            
            # Check group widgets (contain nested widgets)
            if widget_type == 'group':
                nested_widgets = widget_def.get('widgets', [])
                for nested_widget in nested_widgets:
                    nested_def = nested_widget.get('definition', {})
                    found.extend(process_widget(nested_def, depth + 1))
            
            # Check powerpack widgets (may contain nested widgets)
            if widget_type == 'powerpack':
                template_variables = widget_def.get('template_variables', [])
                for var in template_variables:
                    if isinstance(var, dict) and 'defaults' in var:
                        defaults = var['defaults']
                        if isinstance(defaults, list):
                            for default in defaults:
                                if isinstance(default, str) and not default.startswith('$'):
                                    found.append(default)
            
            return found
        
        # Process all top-level widgets
        for widget in details.get('widgets', []):
            widget_def = widget.get('definition', {})
            widget_type = widget_def.get('type', '')
            
            # Process this widget
            found = process_widget(widget_def)
            services.update(found)
            
            # Check group widgets (nested widgets)
            if widget_type == 'group':
                for group_widget in widget_def.get('widgets', []):
                    group_def = group_widget.get('definition', {})
                    found = process_widget(group_def)
                    services.update(found)
            
            # Check split_graph widgets (also can have nested widgets)
            if widget_type == 'split_graph':
                for split_widget in widget_def.get('source_widget_definition', {}).get('widgets', []):
                    split_def = split_widget.get('definition', {})
                    found = process_widget(split_def)
                    services.update(found)
        
        services_list = sorted(list(services))
        print(f"✅ Found {len(services_list)} services in dashboard {dashboard_id}")
        
        # Cache the results
        _dashboard_services_cache[cache_key] = {
            'services': services_list,
            'timestamp': time.time()
        }
        
        return services_list
        
    except Exception as e:
        print(f"⚠️ Error fetching services from dashboard {dashboard_id}: {e}")
        return []


def get_aws_costs_and_changes(days=1):
    """Yesterday AWS cost via Cost Explorer (IAM needs ce:GetCostAndUsage)."""
    del days  # unused; kept for call-site compatibility
    try:
        import boto3
        from datetime import datetime, timedelta

        aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
        aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        aws_region = os.getenv("AWS_REGION", "us-east-1")

        if not aws_access_key or not aws_secret_key:
            print("⚠️ AWS credentials not configured")
            return {
                "cost_today": 0,
                "cost_yesterday": 0,
                "error": "No credentials",
            }

        session = boto3.Session(
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=aws_region,
        )

        ce_client = session.client("ce", region_name="us-east-1")

        today = datetime.now().date()
        yesterday = today - timedelta(days=1)

        cost_response = ce_client.get_cost_and_usage(
            TimePeriod={"Start": str(yesterday), "End": str(today)},
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
        )

        cost_yesterday = 0.0
        if cost_response.get("ResultsByTime"):
            cost_yesterday = float(
                cost_response["ResultsByTime"][0]["Total"]["UnblendedCost"]["Amount"]
            )

        print(f"✅ AWS Cost Explorer: ${cost_yesterday:.2f} yesterday")

        return {
            "cost_yesterday": cost_yesterday,
            "error": None,
        }

    except Exception as e:
        print(f"❌ Error fetching AWS cost data: {e}")
        return {
            "cost_today": 0,
            "cost_yesterday": 0,
            "error": str(e),
        }


def get_splunk_infra_exceptions(timerange_hours=4):
    """Get US Infrastructure Exceptions count from Splunk"""
    try:
        import requests
        
        splunk_host = os.getenv("SPLUNK_HOST", "arlo.splunkcloud.com")
        splunk_token = os.getenv("SPLUNK_TOKEN")
        
        if not splunk_token:
            print("⚠️ Splunk token not configured")
            return 0, []
        
        # Query for US infra exceptions
        search_query = f'''search index=* (exception OR Exception OR ERROR OR error)
earliest=-{timerange_hours}h latest=now
| stats count by service, error_message
| sort -count
| head 10'''
        
        headers = {
            "Authorization": f"Bearer {splunk_token}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        search_url = f"https://{splunk_host}:8089/services/search/jobs/export"
        data = {
            "search": search_query,
            "earliest_time": f"-{timerange_hours}h",
            "latest_time": "now",
            "output_mode": "json"
        }
        
        print(f"🔍 Querying Splunk for US Infra Exceptions (last {timerange_hours}h)...")
        from tools.splunk_tool import splunk_ipv4_rest_scope, splunk_rest_dispatch_form_fields

        data.update(splunk_rest_dispatch_form_fields())
        with splunk_ipv4_rest_scope():
            response = requests.post(search_url, headers=headers, data=data, verify=True, timeout=(15, 60))
        
        if response.status_code == 200:
            results = []
            total_count = 0
            for line in response.text.strip().split('\n'):
                if line:
                    try:
                        result = json.loads(line)
                        row = result.get("result")
                        if not row:
                            continue
                        if result.get("preview") is True:
                            continue
                        results.append(row)
                        total_count += int(row.get("count", 0))
                    except json.JSONDecodeError:
                        continue
            print(f"✅ Found {total_count} US Infra Exceptions")
            return total_count, results[:10]
        else:
            print(f"❌ Splunk API returned status {response.status_code}")
            return 0, []
    except Exception as e:
        print(f"❌ Error fetching Splunk Infra Exceptions: {e}")
        return 0, []


def get_splunk_outliers(timerange_hours=4):
    """Get outliers/anomalies from Splunk for key services"""
    try:
        import requests
        
        splunk_host = os.getenv("SPLUNK_HOST", "arlo.splunkcloud.com")
        splunk_token = os.getenv("SPLUNK_TOKEN")
        
        if not splunk_token:
            print("⚠️ Splunk token not configured")
            return []
        
        # Query for top errors/exceptions across streaming, advisor, oauth services
        # More specific: only ERROR/FATAL/CRITICAL log levels and exceptions, with minimum threshold
        search_query = f'''search index=streaming_prod OR index=advisor_prod OR index=oauth_prod OR index=aria_prod 
(log_level=ERROR OR log_level=FATAL OR log_level=CRITICAL)
earliest=-{timerange_hours}h latest=now
| rex field=_raw "(?<error_type>Exception|Error|Failed|Timeout|Unavailable)"
| eval service=coalesce(service, sourcetype, "Unknown")
| stats count by service, error_type
| where count > 5
| sort -count
| head 8'''
        
        headers = {
            "Authorization": f"Bearer {splunk_token}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        search_url = f"https://{splunk_host}:8089/services/search/jobs/export"
        data = {
            "search": search_query,
            "earliest_time": f"-{timerange_hours}h",
            "latest_time": "now",
            "output_mode": "json"
        }
        
        print(f"🔍 Querying Splunk for outliers (last {timerange_hours}h)...")
        from tools.splunk_tool import splunk_ipv4_rest_scope, splunk_rest_dispatch_form_fields

        data.update(splunk_rest_dispatch_form_fields())
        with splunk_ipv4_rest_scope():
            response = requests.post(search_url, headers=headers, data=data, verify=True, timeout=(15, 90))
        
        if response.status_code == 200:
            results = []
            for line in response.text.strip().split('\n'):
                if line:
                    try:
                        result = json.loads(line)
                        row = result.get("result")
                        if not row:
                            continue
                        if result.get("preview") is True:
                            continue
                        results.append(row)
                    except json.JSONDecodeError:
                        continue
            print(f"✅ Found {len(results)} Splunk outliers")
            return results[:8]  # Return top 8 outliers
        else:
            print(f"❌ Splunk API returned status {response.status_code}")
            return []
    except Exception as e:
        print(f"❌ Error fetching Splunk outliers: {e}")
        return []


def _sm_dd_monitor_error_override_enabled() -> bool:
    v = (os.getenv("STATUS_MONITOR_DD_MONITOR_ERROR_OVERRIDE") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _sm_expected_err_rate_ok_services() -> frozenset[str]:
    """Services whose high APM error rate is expected (Cicd delegates, etc.)."""
    names = {"harness-delegate-svn-ireland"}
    extra = (os.getenv("STATUS_MONITOR_EXPECTED_ERR_RATE_OK") or "").strip()
    if extra:
        for part in extra.split(","):
            s = re.sub(r"\s+", "", (part or "").strip().lower())
            if s:
                names.add(s)
    return frozenset(names)


def _sm_is_expected_err_rate_ok(service_name: str) -> bool:
    k = re.sub(r"\s+", "", (service_name or "").strip().lower())
    return k in _sm_expected_err_rate_ok_services()


def _dd_monitor_search_info(service_name, environment, dd_api_key, dd_app_key, dd_site):
    """
    Query Datadog monitor search (service + env tags, same as UI facets).

    Returns a dict, or None on total API failure (same as legacy “uncached failed”):
      allow_error_override: bool | None — None = no matches / no usable states (keep APM);
        True = all non-problem; False = any Alert/Warn
      alert_names: list[str] — Alert monitors excluding -a/-b suffix (drive red/critical merge).
      alert_names_suffix_ab: list[str] — Alert monitors with -a/-b suffix (do not change tile color; counted on DD pill).
    """
    import requests

    cache_key = (service_name, environment, dd_site, "msearch_v4_ab_suffix_warn")
    now = time.time()
    with _DD_MONITOR_SEARCH_LOCK:
        hit = _DD_MONITOR_SEARCH_CACHE.get(cache_key)
        if hit and now - hit[0] < _DD_MONITOR_SEARCH_TTL:
            return hit[1]

    url = f"{datadog_rest_api_base(dd_site)}/api/v1/monitor/search"
    headers = {"DD-API-KEY": dd_api_key, "DD-APPLICATION-KEY": dd_app_key}
    bad_states = frozenset({"Alert", "Warn"})
    ok_states = frozenset({"OK", "No Data", "Skipped", "Ignored", "Unknown"})
    # Hyphenated service names: quoted per Datadog search reserved characters
    query_str = f'service:"{service_name}" env:{environment}'
    collected = []
    alert_keyed: dict[str, str] = {}
    alert_keyed_suffix: dict[str, str] = {}
    page = 0
    per_page = 100

    def _store(result):
        with _DD_MONITOR_SEARCH_LOCK:
            _DD_MONITOR_SEARCH_CACHE[cache_key] = (now, result)

    try:
        while page < 20:
            r = requests.get(
                url,
                headers=headers,
                params={"query": query_str, "page": page, "per_page": per_page},
                timeout=_dd_query_timeout_secs(),
            )
            if r.status_code == 429:
                time.sleep(0.75)
                r = requests.get(
                    url,
                    headers=headers,
                    params={"query": query_str, "page": page, "per_page": per_page},
                    timeout=_dd_query_timeout_secs(),
                )
            if r.status_code != 200:
                _store(None)
                return None
            data = r.json() if r.content else {}
            monitors = data.get("monitors") or []
            for m in monitors:
                st = m.get("overall_state")
                if st is None and isinstance(m.get("status"), str):
                    st = m["status"]
                st = (st or "").strip() if isinstance(st, str) else ""
                raw_name = (m.get("name") or "").strip()
                mid = m.get("id")
                skip_ab = _sm_dd_monitor_name_suffix_ab(raw_name)
                if st:
                    eff = st
                    if st == "Alert" and skip_ab:
                        eff = "OK"
                    collected.append(eff)
                if st == "Alert" and not skip_ab:
                    key = f"id:{mid}" if mid is not None else f"n:{raw_name}"
                    disp = raw_name or (f"monitor {mid}" if mid is not None else "monitor")
                    if key not in alert_keyed:
                        alert_keyed[key] = disp
                elif st == "Alert" and skip_ab:
                    key = f"id:{mid}" if mid is not None else f"n:{raw_name}"
                    disp = raw_name or (f"monitor {mid}" if mid is not None else "monitor")
                    if key not in alert_keyed_suffix:
                        alert_keyed_suffix[key] = disp
            if not monitors or len(monitors) < per_page:
                break
            page += 1
    except Exception as e:
        print(f"⚠️ Datadog monitor search ({service_name}, {environment}): {e}")
        _store(None)
        return None

    if not collected:
        out = {"allow_error_override": None, "alert_names": [], "alert_names_suffix_ab": []}
        _store(out)
        return out
    if any(s in bad_states for s in collected):
        allow = False
    elif all(s in ok_states for s in collected):
        allow = True
    else:
        allow = False
    alert_names = sorted(alert_keyed.values(), key=str.lower)
    alert_names_suffix_ab = sorted(alert_keyed_suffix.values(), key=str.lower)
    out = {
        "allow_error_override": allow,
        "alert_names": alert_names,
        "alert_names_suffix_ab": alert_names_suffix_ab,
    }
    _store(out)
    return out


def get_service_health_status(service_name, environment, dd_api_key, dd_app_key, dd_site, from_time, to_time, enable_extended_metrics=False):
    """Get comprehensive health status for a single service using multiple Datadog APM metrics
    
    Args:
        enable_extended_metrics: If True, fetch latency and baseline (slower but more comprehensive)
    """
    try:
        import requests
        
        headers = {
            "DD-API-KEY": dd_api_key,
            "DD-APPLICATION-KEY": dd_app_key
        }
        
        # Initialize metrics
        requests_count = 0
        errors_count = 0
        p95_latency = None
        p99_latency = None
        baseline_requests = 0
        baseline_from = 0
        baseline_to = 0
        
        # Use primary metric pattern (servlet) - optimized for speed
        # Only try alternative patterns if extended metrics are enabled
        if enable_extended_metrics:
            metric_patterns = [
                ('trace.servlet.request.hits', 'trace.servlet.request.errors', 'trace.servlet.request.duration.by.service.95p'),
                ('trace.http.request.hits', 'trace.http.request.errors', 'trace.http.request.duration.by.service.95p'),
                ('trace.web.request.hits', 'trace.web.request.errors', 'trace.web.request.duration.by.service.95p'),
            ]
        else:
            # Fast mode: try common APM hit patterns (no latency/baseline — still fast)
            metric_patterns = [
                ('trace.servlet.request.hits', 'trace.servlet.request.errors', 'trace.servlet.request.duration.by.service.95p'),
                ('trace.http.request.hits', 'trace.http.request.errors', 'trace.http.request.duration.by.service.95p'),
                ('trace.web.request.hits', 'trace.web.request.errors', 'trace.web.request.duration.by.service.95p'),
            ]
        
        # True if Datadog returned HTTP 200 at least once (empty series = no traffic, not timeout)
        dd_query_reachable = False
        
        # Try each pattern until we get data
        dd_query_url = f"{datadog_rest_api_base(dd_site)}/api/v1/query"
        for hits_metric, errors_metric, latency_metric in metric_patterns:
            hits_query = f"sum:{hits_metric}{{service:{service_name},env:{environment}}}.as_count()"
            err_query = f"sum:{errors_metric}{{service:{service_name},env:{environment}}}.as_count()"
            query = hits_query
            params = {
                "from": from_time,
                "to": to_time,
                "query": query
            }
            
            # Parallel hits + errors; one quick retry on transport errors (busy DD / parallel load)
            response = None
            err_response = None
            for attempt in (0, 1):
                fetch_out = {}

                def _dd_fetch(key, q):
                    try:
                        fetch_out[key] = requests.get(
                            dd_query_url,
                            headers=headers,
                            params={"from": from_time, "to": to_time, "query": q},
                            timeout=_dd_query_timeout_secs(),
                        )
                    except Exception as ex:
                        fetch_out[key] = ex

                t_hits = threading.Thread(target=_dd_fetch, args=("hits", hits_query))
                t_err = threading.Thread(target=_dd_fetch, args=("err", err_query))
                t_hits.start()
                t_err.start()
                t_hits.join()
                t_err.join()
                response = fetch_out.get("hits")
                err_response = fetch_out.get("err")
                if not isinstance(response, Exception):
                    break
                if attempt == 0:
                    time.sleep(0.25)
            if isinstance(response, Exception):
                continue
            if isinstance(err_response, Exception):
                err_response = None
            
            if response.status_code == 200:
                dd_query_reachable = True
                data = response.json()
                if 'series' in data and len(data['series']) > 0:
                    points = data['series'][0].get('pointlist', [])
                    if points:
                        requests_count = sum(p[1] for p in points if p[1] is not None)
                        
                        if requests_count > 0 or enable_extended_metrics:
                            if err_response is not None and err_response.status_code == 200:
                                err_data = err_response.json()
                                if 'series' in err_data and len(err_data['series']) > 0:
                                    err_points = err_data['series'][0].get('pointlist', [])
                                    if err_points:
                                        errors_count = sum(p[1] for p in err_points if p[1] is not None)
                            
                            # Only fetch extended metrics if enabled (to improve performance)
                            if enable_extended_metrics:
                                # Try multiple latency metric patterns
                                latency_patterns_to_try = [
                                    (latency_metric, 'primary'),
                                    (latency_metric.replace('.by.service.', '.by.resource_service.'), 'resource_service'),
                                ]
                                
                                for lat_metric_pattern, pattern_name in latency_patterns_to_try:
                                    # Get p95 latency
                                    params['query'] = f"avg:{lat_metric_pattern}{{service:{service_name},env:{environment}}}"
                                    
                                    print(f"   🔍 Trying latency metric ({pattern_name}): {lat_metric_pattern}")
                                    
                                    lat_response = requests.get(
                                        dd_query_url,
                                        headers=headers,
                                        params=params,
                                        timeout=5
                                    )
                                    
                                    if lat_response.status_code == 200:
                                        lat_data = lat_response.json()
                                        print(f"   📡 Response status: 200, series count: {len(lat_data.get('series', []))}")
                                        
                                        if 'series' in lat_data and len(lat_data['series']) > 0:
                                            lat_points = lat_data['series'][0].get('pointlist', [])
                                            print(f"   📊 Points received: {len(lat_points)}")
                                            
                                            if lat_points:
                                                valid_latencies = [p[1] for p in lat_points if p[1] is not None and p[1] > 0]
                                                print(f"   📊 Valid latency points: {len(valid_latencies)}, values: {valid_latencies[:5] if len(valid_latencies) > 5 else valid_latencies}")
                                                
                                                if valid_latencies:
                                                    avg_latency = sum(valid_latencies) / len(valid_latencies)
                                                    
                                                    # Datadog APM duration.by.service metrics ALWAYS return in SECONDS
                                                    # Convert to milliseconds (check if already seems to be in ms to avoid double conversion)
                                                    if avg_latency > 100:
                                                        # Values > 100 are likely already in milliseconds (100s = 100000ms is unrealistic)
                                                        p95_latency = avg_latency
                                                        print(f"   ✅ {service_name} ({environment}): P95 = {p95_latency:.2f}ms (detected as already in ms)")
                                                    else:
                                                        # Standard case: convert seconds to milliseconds
                                                        p95_latency = avg_latency * 1000
                                                        print(f"   ✅ {service_name} ({environment}): P95 = {p95_latency:.2f}ms (converted from {avg_latency:.3f}s)")
                                                    
                                                    print(f"      Pattern: {pattern_name}, Data points: {len(valid_latencies)}")
                                                    break  # Found data, stop trying patterns
                                    else:
                                        print(f"   ⚠️  API returned status: {lat_response.status_code}")
                                
                                # Get baseline (7 days) for traffic comparison - weekly average is more stable
                                baseline_from = from_time - (7 * 86400)  # 7 days before current period
                                baseline_to = from_time  # Up to the start of current period
                                params['query'] = query
                                params['from'] = baseline_from
                                params['to'] = baseline_to
                                
                                print(f"   📊 Fetching 7-day baseline for traffic comparison...")
                                
                                baseline_response = requests.get(
                                    dd_query_url,
                                    headers=headers,
                                    params=params,
                                    timeout=8
                                )
                                
                                if baseline_response.status_code == 200:
                                    baseline_data = baseline_response.json()
                                    if 'series' in baseline_data and len(baseline_data['series']) > 0:
                                        baseline_points = baseline_data['series'][0].get('pointlist', [])
                                        if baseline_points:
                                            baseline_requests = sum(p[1] for p in baseline_points if p[1] is not None)
                                            print(f"   📈 Baseline (7 days): {baseline_requests:,} requests")
                            
                            break  # Found working metric pattern
        
        # Calculate error rate
        error_rate = (errors_count / requests_count * 100) if requests_count > 0 else 0
        
        # Detect traffic drop (> 85% drop from weekly average) - only if extended metrics enabled
        traffic_drop = False
        if enable_extended_metrics and baseline_requests > 5000:  # Only check if baseline had significant traffic over the week
            # Calculate rates per hour for comparison
            current_time_window_hours = (to_time - from_time) / 3600
            baseline_time_window_hours = (baseline_to - baseline_from) / 3600  # 7 days = 168 hours
            
            current_rate = requests_count / current_time_window_hours  # requests per hour (current)
            baseline_avg_rate = baseline_requests / baseline_time_window_hours  # requests per hour (7-day avg)
            
            # Compare current rate against weekly average
            # Only flag if MAJOR drop (>85%) compared to weekly pattern
            if baseline_avg_rate > 0 and current_rate < (baseline_avg_rate * 0.15):  # 85% drop
                drop_percentage = ((baseline_avg_rate - current_rate) / baseline_avg_rate) * 100
                traffic_drop = True
                print(f"   🚨 TRAFFIC DROP: {service_name} current={current_rate:.0f}/h vs 7-day avg={baseline_avg_rate:.0f}/h (drop: {drop_percentage:.0f}%)")
            else:
                # Log comparison for monitoring (even if not critical)
                if baseline_avg_rate > 0:
                    variance = ((current_rate - baseline_avg_rate) / baseline_avg_rate) * 100
                    print(f"   📊 {service_name} traffic: current={current_rate:.0f}/h vs 7-day avg={baseline_avg_rate:.0f}/h (variance: {variance:+.0f}%)")
        
        # Detect high latency - only if extended metrics enabled
        high_latency = False
        if enable_extended_metrics:
            if p95_latency and p95_latency > 2000:  # 2 seconds in ms
                high_latency = True
            if p99_latency and p99_latency > 5000:  # 5 seconds in ms
                high_latency = True
        
        # Determine status based on multiple factors
        # Be more conservative to avoid false positives
        if requests_count == 0:
            # unreachable API / timeouts vs confirmed empty APM series
            status = 'inactive' if dd_query_reachable else 'unknown'
        elif traffic_drop:
            status = 'critical'  # Sudden traffic drop is critical
        elif error_rate > 5:  # Increased from 3% to 5% to be less aggressive
            status = 'critical'
            print(f"   🚨 {service_name} ({environment}): CRITICAL - Error rate {error_rate:.2f}% (>{requests_count:,} requests, {errors_count:,} errors)")
        elif error_rate > 1:  # Increased from 0.5% to 1%
            status = 'warning'
            print(f"   ⚠️  {service_name} ({environment}): WARNING - Error rate {error_rate:.2f}%")
        elif high_latency:
            status = 'warning'
            print(f"   ⚠️  {service_name} ({environment}): WARNING - High latency {p95_latency:.0f}ms")
        else:
            status = 'healthy'

        er_critical = status == "critical" and error_rate > 5 and not traffic_drop
        er_warning = status == "warning" and error_rate > 1 and not high_latency
        need_dd_for_override = (
            _sm_dd_monitor_error_override_enabled()
            and status in ("critical", "warning")
            and (er_critical or er_warning)
        )
        need_dd = need_dd_for_override or _sm_dd_monitor_alerts_enabled()
        dd_info = None
        if need_dd:
            dd_info = _dd_monitor_search_info(
                service_name, environment, dd_api_key, dd_app_key, dd_site
            )
        alert_names: list = []
        suffix_alert_names: list = []
        if _sm_dd_monitor_alerts_enabled() and isinstance(dd_info, dict):
            alert_names = list(dd_info.get("alert_names") or [])
            suffix_alert_names = list(dd_info.get("alert_names_suffix_ab") or [])

        dd_monitor_override = False
        if _sm_dd_monitor_error_override_enabled() and status in ("critical", "warning") and (er_critical or er_warning):
            m_all_ok = (dd_info or {}).get("allow_error_override")
            if m_all_ok is True:
                prev = status
                dd_monitor_override = True
                status = "healthy"
                print(
                    f"   ✅ {service_name} ({environment}): Datadog monitors all OK — "
                    f"overriding error-rate {prev} → healthy (ERR {error_rate:.2f}%)"
                )

        if _sm_dd_monitor_alerts_enabled() and alert_names:
            status = _sm_merge_status_with_dd_alerts(status, len(alert_names))
            if status != "healthy":
                dd_monitor_override = False

        if _sm_dd_monitor_alerts_enabled() and suffix_alert_names:
            status = _sm_bump_min_warning_for_dd_suffix_alerts(status, len(suffix_alert_names))
            if status != "healthy":
                dd_monitor_override = False

        if (
            _sm_is_expected_err_rate_ok(service_name)
            and status in ("critical", "warning")
            and not traffic_drop
            and not high_latency
            and error_rate > 1
        ):
            prev = status
            status = "healthy"
            dd_monitor_override = True
            print(
                f"   ✅ {service_name} ({environment}): expected error-rate — "
                f"{prev} → healthy (ERR {error_rate:.2f}%)"
            )

        dd_m_url = _dd_monitors_manage_url(service_name, environment, dd_site)
        dd_m_url_all = (
            _dd_monitors_manage_url_all_alerts(service_name, environment, dd_site)
            if suffix_alert_names
            else None
        )
        
        # Calculate traffic variance for context
        traffic_variance = None
        if enable_extended_metrics and baseline_requests > 0:
            current_time_window_hours = (to_time - from_time) / 3600
            baseline_time_window_hours = (baseline_to - baseline_from) / 3600 if baseline_from > 0 else 168
            current_rate = requests_count / current_time_window_hours
            baseline_avg_rate = baseline_requests / baseline_time_window_hours
            if baseline_avg_rate > 0:
                traffic_variance = ((current_rate - baseline_avg_rate) / baseline_avg_rate) * 100
        
        return {
            'service': service_name,
            'environment': environment,
            'status': status,
            'requests': int(requests_count),
            'errors': int(errors_count),
            'error_rate': round(error_rate, 2),
            'p95_latency': round(p95_latency, 2) if p95_latency else None,
            'p99_latency': round(p99_latency, 2) if p99_latency else None,
            'traffic_drop': traffic_drop,
            'high_latency': high_latency,
            'baseline_requests': int(baseline_requests),
            'traffic_variance': round(traffic_variance, 1) if traffic_variance is not None else None,
            'dd_monitor_override': dd_monitor_override,
            'dd_monitor_alerts': alert_names,
            'dd_monitor_alert_count': len(alert_names),
            'dd_monitor_alerts_suffix_ab': suffix_alert_names,
            'dd_monitor_alert_suffix_count': len(suffix_alert_names),
            'dd_monitor_open_count': len(alert_names) + len(suffix_alert_names),
            'dd_monitors_url': dd_m_url or None,
            'dd_monitors_url_all_alerts': dd_m_url_all or None,
        }
        
    except Exception as e:
        print(f"Error fetching status for {service_name} in {environment}: {e}")
        return {
            'service': service_name,
            'environment': environment,
            'status': 'unknown',
            'requests': 0,
            'errors': 0,
            'error_rate': 0,
            'p95_latency': None,
            'p99_latency': None,
            'traffic_drop': False,
            'high_latency': False,
            'baseline_requests': 0,
            'dd_monitor_override': False,
            'dd_monitor_alerts': [],
            'dd_monitor_alert_count': 0,
            'dd_monitor_alerts_suffix_ab': [],
            'dd_monitor_alert_suffix_count': 0,
            'dd_monitors_url': _dd_monitors_manage_url(service_name, environment, dd_site) or None,
            'dd_monitors_url_all_alerts': None,
        }


def get_pagerduty_incidents_count(pd_api_key: str):
    """Get count of active PagerDuty incidents"""
    try:
        import requests
        headers = {
            "Authorization": f"Token token={pd_api_key}",
            "Accept": "application/vnd.pagerduty+json;version=2"
        }
        
        # Get triggered and acknowledged incidents (active)
        params = {
            "statuses[]": ["triggered", "acknowledged"],
            "limit": 100
        }
        
        response = _https_get_with_retries(
            "https://api.pagerduty.com/incidents",
            headers=headers,
            params=params,
            timeout=(12, 45),
            label="PagerDuty",
            max_attempts=4,
        )
        
        if response.status_code == 200:
            data = response.json()
            incidents = data.get("incidents", [])
            return len(incidents)
        return 0
    except:
        return 0


def _pagerduty_fetch_slices(
    pd_api_key: str,
    force_refresh: bool,
    status_dashboard_id: str | None,
):
    """
    Single source for PagerDuty semaphore + /api/pagerduty/*-monitor widgets.

    - Account-wide (no board): last 24h — matches ops / correlation (active noise).
    - External status board (Samsung/ADT): very wide `since` (10y) — the public Resolved tab
      lists all-time resolved for the board; a 180d window omitted older demo/history rows and
      the API often omits `total` when `status_dashboard_ids[]` is set (use list length).

    total=true, same retries, SQLite cache.
    Returns (counts dict, incidents_by_status dict, active_incidents list).
    """
    ttl = _effective_db_cache_ttl_secs(force_refresh)
    # v3 account / v4 board: bump when PD list mapping changes (invalidate stale empty-row cache).
    cache_key = "v3" if not status_dashboard_id else f"board_{status_dashboard_id}_v6"
    cached = sm_api_cache_get("pagerduty_status", cache_key, ttl)
    if cached is not None and cached.get("incidents_by_status") is not None:
        scope = "board " + status_dashboard_id if status_dashboard_id else "account"
        print(
            f"🗄️ PagerDuty ({scope}): DB cache (≤{_db_api_cache_ttl}s) — "
            f"{cached['counts'].get('triggered', 0)} trg / "
            f"{cached['counts'].get('acknowledged', 0)} ack / "
            f"{cached['counts'].get('resolved', 0)} res"
        )
        return cached["counts"], cached["incidents_by_status"], cached["active_incidents"]

    counts = {}
    incidents_by_status = {"triggered": [], "acknowledged": [], "resolved": []}

    headers = {
        "Authorization": f"Token token={pd_api_key}",
        "Accept": "application/vnd.pagerduty+json;version=2",
    }

    if status_dashboard_id:
        # Match external status UI (e.g. Resolved tab): include old demos, not only last ~6 months.
        since_hours = 24 * 365 * 10
        window_lbl = "10y (board)"
    else:
        since_hours = 24
        window_lbl = "24h"
    since = (datetime.utcnow() - timedelta(hours=since_hours)).isoformat()
    statuses = ["triggered", "acknowledged", "resolved"]
    # External status boards (Samsung/ADT): paginate so lists include full history (typically small).
    _BOARD_PAGE = 100
    _BOARD_MAX_ROWS = 2500

    def _fetch_pd_incidents_for_status(status: str):
        params = {
            "statuses[]": [status],
            "since": since,
            "sort_by": "created_at:desc",
            "total": "true",
            "include[]": ["escalation_policies"],
        }
        if status_dashboard_id:
            params["status_dashboard_ids[]"] = status_dashboard_id
            params["limit"] = _BOARD_PAGE
        else:
            params["limit"] = 10

        if not status_dashboard_id:
            response = _https_get_with_retries(
                "https://api.pagerduty.com/incidents",
                headers=headers,
                params=params,
                timeout=(12, 45),
                label="PagerDuty",
                max_attempts=4,
            )
            if response.status_code != 200:
                return status, 0, []
            data = response.json()
            n = data.get("total", 0) if data.get("total") else len(data.get("incidents", []))
            inc_list = data.get("incidents", [])
            _sm_pd_attach_escalation_summaries(inc_list, data)
            return status, n, inc_list

        # Board: fetch all pages up to _BOARD_MAX_ROWS
        all_incidents: list = []
        offset = 0
        total_reported: int | None = None
        while len(all_incidents) < _BOARD_MAX_ROWS:
            p = dict(params)
            p["offset"] = offset
            response = _https_get_with_retries(
                "https://api.pagerduty.com/incidents",
                headers=headers,
                params=p,
                timeout=(12, 60),
                label="PagerDuty",
                max_attempts=4,
            )
            if response.status_code != 200:
                return status, 0, all_incidents
            data = response.json()
            if total_reported is None:
                raw_t = data.get("total")
                if raw_t is not None:
                    try:
                        total_reported = int(raw_t)
                    except (TypeError, ValueError):
                        total_reported = None
            page = data.get("incidents") or []
            _sm_pd_attach_escalation_summaries(page, data)
            all_incidents.extend(page)
            if not page or len(page) < _BOARD_PAGE:
                break
            if total_reported is not None and len(all_incidents) >= total_reported:
                break
            offset += _BOARD_PAGE
        # PD often omits or misreports `total` when filtering by status_dashboard_ids; paginated length is truth.
        n = len(all_incidents)
        return status, n, all_incidents

    # Parallel: external boards need 3 slices; sequential was ~3× latency for Samsung/ADT widgets.
    with ThreadPoolExecutor(max_workers=3) as pool:
        for status, n, inc_list in pool.map(_fetch_pd_incidents_for_status, statuses):
            counts[status] = n
            incidents_by_status[status] = inc_list

    active_incidents = incidents_by_status["triggered"] + incidents_by_status["acknowledged"]

    scope = "board " + status_dashboard_id if status_dashboard_id else "account"
    print(
        f"✅ PagerDuty ({scope}): {counts.get('triggered', 0)} trg, "
        f"{counts.get('acknowledged', 0)} ack, {counts.get('resolved', 0)} res ({window_lbl})"
    )
    print(f"🔗 Active incidents for correlation: {len(active_incidents)}")
    sm_api_cache_set(
        "pagerduty_status",
        cache_key,
        {
            "counts": counts,
            "active_incidents": active_incidents,
            "incidents_by_status": incidents_by_status,
        },
    )
    return counts, incidents_by_status, active_incidents


def get_pagerduty_status_counts(
    pd_api_key: str, force_refresh: bool = False, status_dashboard_id: str | None = None
):
    """Same PD query path as the traffic-light semaphore; optional external status board scope."""
    try:
        counts, _ibs, active_incidents = _pagerduty_fetch_slices(
            pd_api_key, force_refresh, status_dashboard_id
        )
        return counts, active_incidents
    except Exception as e:
        print(f"❌ Error fetching PagerDuty status counts: {e}")
        return {"triggered": 0, "acknowledged": 0, "resolved": 0}, []


def _pd_incident_to_monitor_dict(inc: dict) -> dict:
    """Shape expected by static/js PagerDuty monitor widgets."""
    num = inc.get("incident_number")
    if num is None:
        num = inc.get("number")
    svc = inc.get("service")
    if isinstance(svc, dict):
        svc_label = svc.get("summary") or "Unknown"
    elif svc is not None:
        svc_label = str(svc)
    else:
        svc_label = "Unknown"
    st = inc.get("status")
    if isinstance(st, str):
        st = st.lower()
    else:
        st = "unknown"
    return {
        "number": num if num is not None else "N/A",
        "title": inc.get("title") or "No title",
        "service": svc_label,
        "status": st,
        "url": inc.get("html_url") or "#",
    }


def build_pagerduty_monitor_api_payload(
    pd_api_key: str,
    status_dashboard_id: str | None,
    force_refresh: bool = False,
) -> dict:
    """
    JSON for Flask /api/pagerduty/monitor and samsung-/adt-monitor.
    Uses _pagerduty_fetch_slices: account-wide 24h; external boards use a wide since window
    and paginate the Incidents API so active + resolved lists include full history (capped in fetch).
    """
    try:
        counts, ibs, _active = _pagerduty_fetch_slices(
            pd_api_key, force_refresh, status_dashboard_id
        )
        # Do not filter by incident.status: PD responses are already bucketed by query; some payloads omit/mismatch status.
        tr = [_pd_incident_to_monitor_dict(i) for i in ibs["triggered"]]
        ack = [_pd_incident_to_monitor_dict(i) for i in ibs["acknowledged"]]
        res = [_pd_incident_to_monitor_dict(i) for i in ibs["resolved"]]
        if status_dashboard_id:
            active = tr + ack
            recently_resolved = res
        else:
            active = (tr + ack)[:10]
            _home_res_n = 3
            try:
                _home_res_n = max(
                    1,
                    min(
                        10,
                        int(
                            (os.getenv("PAGERDUTY_HOME_RESOLVED_LIST_LIMIT") or "3").strip()
                            or "3"
                        ),
                    ),
                )
            except ValueError:
                _home_res_n = 3
            recently_resolved = res[:_home_res_n]
        payload = {
            "triggered": int(counts.get("triggered") or 0),
            "acknowledged": int(counts.get("acknowledged") or 0),
            "resolved": int(counts.get("resolved") or 0),
            "active": active,
            "recently_resolved": recently_resolved,
            "timestamp": time.strftime("%H:%M:%S"),
        }
        return payload
    except Exception as e:
        print(f"❌ build_pagerduty_monitor_api_payload: {e}")
        return {"error": str(e)}


def _is_meaningful_kube_cluster_name(name) -> bool:
    """Datadog may return kube_cluster_name:N/A when the tag is missing; hide those in the UI."""
    if name is None:
        return False
    s = str(name).strip()
    return bool(s) and s.upper() != "N/A"


def get_service_clusters_from_metrics(service_name: str, env: str, timerange_hours: int = 1):
    """
    Get ALL EKS cluster names where a service is running
    
    Args:
        service_name: Service name
        env: Environment tag
        timerange_hours: Hours to look back
    
    Returns:
        List of cluster names or empty list
    """
    try:
        import requests
        
        dd_api_key = os.getenv("DATADOG_API_KEY")
        dd_app_key = os.getenv("DATADOG_APP_KEY")
        dd_site = os.getenv("DATADOG_SITE", "datadoghq.com")
        
        if not dd_api_key or not dd_app_key:
            return []
        
        current_time = int(time.time())
        from_time = current_time - (timerange_hours * 3600)
        
        # Try multiple metrics to find cluster info
        # Order: APM traces first (most accurate), then Kubernetes metrics, then system metrics
        metrics_to_try = [
            # APM Trace metrics (best for application services)
            f"avg:trace.servlet.request.hits{{service:{service_name},env:{env}}} by {{kube_cluster_name}}",
            f"avg:trace.flask.request.hits{{service:{service_name},env:{env}}} by {{kube_cluster_name}}",
            f"avg:trace.http.request.hits{{service:{service_name},env:{env}}} by {{kube_cluster_name}}",
            f"avg:trace.web.request{{service:{service_name},env:{env}}} by {{kube_cluster_name}}",
            f"avg:trace.express.request{{service:{service_name},env:{env}}} by {{kube_cluster_name}}",
            f"avg:trace.django.request{{service:{service_name},env:{env}}} by {{kube_cluster_name}}",
            # Kubernetes pod metrics (good for all k8s services)
            f"avg:kubernetes.cpu.usage.total{{kube_service:{service_name},env:{env}}} by {{kube_cluster_name}}",
            f"avg:kubernetes.memory.usage{{kube_service:{service_name},env:{env}}} by {{kube_cluster_name}}",
            f"avg:kubernetes_state.pod.ready{{kube_service:{service_name},env:{env}}} by {{kube_cluster_name}}",
            # Container metrics (works for containerized services)
            f"avg:container.cpu.usage{{container_name:{service_name},env:{env}}} by {{kube_cluster_name}}",
            f"avg:docker.cpu.usage{{container_name:{service_name},env:{env}}} by {{kube_cluster_name}}",
            # System metrics with service tag (fallback)
            f"avg:system.cpu.user{{service:{service_name},env:{env}}} by {{kube_cluster_name}}",
        ]
        
        clusters = []
        
        for query in metrics_to_try:
            params = {
                "from": from_time,
                "to": current_time,
                "query": query
            }
            
            try:
                response = requests.get(
                    f"{datadog_rest_api_base(dd_site)}/api/v1/query",
                    headers={
                        "DD-API-KEY": dd_api_key,
                        "DD-APPLICATION-KEY": dd_app_key
                    },
                    params=params,
                    timeout=10  # Increased timeout for more reliable results
                )
                
                if response.status_code != 200:
                    continue
            except Exception:
                # Network or timeout error, try next metric
                continue
            
            data = response.json()
            
            if not data.get('series') or len(data['series']) == 0:
                continue
            
            # Extract ALL cluster names from all series
            for series in data['series']:
                scope = series.get('scope', '')
                if 'kube_cluster_name:' in scope:
                    cluster_name = scope.split('kube_cluster_name:')[1].split(',')[0].strip()
                    if _is_meaningful_kube_cluster_name(cluster_name) and cluster_name not in clusters:
                        clusters.append(cluster_name)
            
            # If we found clusters, log which metric worked and stop trying
            if clusters:
                # Extract metric name for logging
                metric_name = query.split(':')[1].split('{')[0] if ':' in query else 'unknown'
                print(f"      ✓ Found clusters via {metric_name}")
                break
        
        return clusters
        
    except Exception as e:
        print(f"      ✗ Error in get_service_clusters_from_metrics: {e}")
        return []


_EKS_ENV_TAG_VARIANTS = {
    "production": ["prod", "production", "samsung_prod"],
    "goldendev": ["goldendev", "dev"],
    "goldenqa": ["goldenqa", "qa"],
    "qa": ["qa"],
    "samsung_prod": ["samsung_prod", "production", "prod"],
    "adt_prod": ["adt_prod"],
    "cat_prod": ["cat", "cat_prod"],
    "comcast_prod": ["comcast", "comcast_prod"],
}


def _eks_cluster_cache_max_age_secs() -> float:
    """Non-empty cluster lists: reuse DB without calling Datadog until this age (default 30 days)."""
    try:
        return max(60.0, float((os.getenv("STATUS_MONITOR_EKS_CLUSTER_CACHE_SECS") or "2592000").strip()))
    except ValueError:
        return 2592000.0


def _eks_cluster_empty_retry_secs() -> float:
    """Empty cluster result: retry Datadog after this age (default 1 hour)."""
    try:
        return max(60.0, float((os.getenv("STATUS_MONITOR_EKS_CLUSTER_EMPTY_RETRY_SECS") or "3600").strip()))
    except ValueError:
        return 3600.0


def _resolve_eks_cluster_names(
    service_name: str,
    service_env: str,
    timerange_hours: int,
    force_refresh: bool = False,
) -> tuple[list, bool]:
    """
    Resolve kube_cluster_name via Datadog metrics. Persists results in SQLite (service_eks_clusters)
    so we do not query DD on every load. Returns (cluster_names, used_database_only).
    """
    now = time.time()
    if not force_refresh:
        row = get_service_eks_clusters(service_name, service_env)
        if row is not None:
            clusters, updated_at = row
            age = now - updated_at
            max_age = _eks_cluster_cache_max_age_secs()
            empty_retry = _eks_cluster_empty_retry_secs()
            if clusters:
                if age < max_age:
                    return clusters, True
            elif age < empty_retry:
                return [], True

    for env_tag in _EKS_ENV_TAG_VARIANTS.get(service_env, [service_env]):
        found = get_service_clusters_from_metrics(service_name, env_tag, timerange_hours=timerange_hours)
        if found:
            set_service_eks_clusters(service_name, service_env, found)
            return found, False
    set_service_eks_clusters(service_name, service_env, [])
    return [], False


def _attach_eks_clusters_wall(
    statuses: list,
    timerange: int,
    eks_cache: dict | None = None,
    force_refresh: bool = False,
) -> None:
    """Populate eks_clusters on wall rows (healthy / warning / critical) for tooltips."""
    if not statuses:
        return
    thr = max(1, int(timerange))
    cache = eks_cache if eks_cache is not None else {}
    lock = threading.Lock()

    def work(row: dict) -> None:
        if row.get("status") not in ("healthy", "warning", "critical"):
            return
        if row.get("wall_idle"):
            return
        if row.get("eks_clusters"):
            return
        svc = row.get("service") or ""
        env = row.get("environment") or ""
        if not svc:
            return
        key = (svc, env)
        with lock:
            cached = key in cache
            if cached:
                names = cache[key]
        if not cached:
            resolved, _db = _resolve_eks_cluster_names(svc, env, thr, force_refresh)
            with lock:
                if key not in cache:
                    cache[key] = resolved
                names = cache[key]
        if names:
            row["eks_clusters"] = names
            row["eks_cluster_count"] = len(names)

    with ThreadPoolExecutor(max_workers=STATUS_MONITOR_EKS_MAX_WORKERS) as ex:
        futs = [ex.submit(work, r) for r in statuses]
        for f in as_completed(futs):
            try:
                f.result()
            except Exception as e:
                print(f"⚠️ Status wall EKS lookup error: {e}")


def get_arlo_services_status(force_refresh: bool = False):
    """Scrape status.arlo.com to get platform service status"""
    try:
        cached = sm_api_cache_get("arlo_platform_status", "v1", _effective_db_cache_ttl_secs(force_refresh))
        if cached is not None:
            svcs = cached.get("services") or []
            print(f"🗄️ Arlo status: using DB cache (≤{_db_api_cache_ttl}s) — {len(svcs)} core services")
            return svcs

        import requests
        from bs4 import BeautifulSoup
        import logging
        
        url = "https://status.arlo.com"
        response = _https_get_with_retries(
            url,
            timeout=(10, 35),
            label="Arlo status",
            max_attempts=5,
        )
        
        if response.status_code != 200:
            return []
        
        soup = BeautifulSoup(response.text, "html.parser")
        text = soup.get_text("\n", strip=True)
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        
        # Core services to monitor (same as in app.py)
        core_services_names = ["Log In", "Notifications", "Library", "Live Streaming", 
                               "Video Recording", "Arlo Store", "Community"]
        
        # Extract services with deduplication logic (same as app.py)
        services = []
        seen_services = set()
        
        for i, line in enumerate(lines):
            if line in core_services_names:
                if i + 1 < len(lines) and line not in seen_services:
                    status_text = lines[i + 1]
                    
                    # Skip if next line is also a service name (means status wasn't captured)
                    if status_text in core_services_names:
                        logging.warning(f"⚠️ Arlo Platform Status: {line} → status not found (next line is another service: {status_text})")
                        continue
                    
                    status_lower = status_text.lower()
                    
                    # Determine status (be conservative - default to healthy)
                    if "outage" in status_lower or "down" in status_lower or "major" in status_lower:
                        status = "critical"
                    elif "degraded" in status_lower or "partial" in status_lower or "disruption" in status_lower:
                        status = "warning"
                    else:
                        # Default to healthy (includes "operational", "all good" and any unknown states)
                        status = "healthy"
                    
                    logging.info(f"✅ Arlo Platform Status: {line} → {status_text}")
                    services.append({
                        "name": line,
                        "status": status,
                        "status_text": status_text
                    })
                    seen_services.add(line)
        
        sm_api_cache_set("arlo_platform_status", "v1", {"services": services})
        return services
    except Exception as e:
        print(f"❌ Error fetching Arlo platform status: {e}")
        return []


HUB_ENV_ROWS = [
    {"slug": "production", "label": "Production", "href": "/statusmonitor/production", "mode": "production"},
    {"slug": "goldendev", "label": "GoldenDev", "href": "/statusmonitor/goldendev", "mode": "goldendev"},
    {"slug": "goldenqa", "label": "GoldenQA", "href": "/statusmonitor/goldenqa", "mode": "goldenqa"},
    {"slug": "qa", "label": "QA", "href": "/statusmonitor/qa", "mode": "qa"},
    {"slug": "samsung", "label": "Samsung", "href": "/statusmonitor/samsung", "mode": "samsung"},
    {"slug": "adt", "label": "ADT", "href": "/statusmonitor/adt", "mode": "adt"},
    {"slug": "cat", "label": "CAT", "href": "/statusmonitor/cat", "mode": "cat"},
    {"slug": "comcast", "label": "Comcast", "href": "/statusmonitor/comcast", "mode": "comcast"},
    {"slug": "redmetrics-us", "label": "RED Metrics US", "href": "/statusmonitor/redmetrics-us", "mode": "redmetrics-us"},
]

# Home + hub cards for Production/Samsung/ADT/CAT/Comcast use the same APM Status Wall pipeline
# (service scope, idle→green normalization, overall rollup) so colors match /apm-services.
HUB_WALL_ALIGNED_SLUGS = frozenset({"production", "samsung", "adt", "cat", "comcast"})
HUB_SLUG_TO_WALL_DD_ENV = {
    "production": "production",
    "samsung": "samsung_prod",
    "adt": "adt_prod",
    "cat": "cat_prod",
    "comcast": "comcast_prod",
}

# Full-screen wall: fixed section order (not the same as hub card order).
WALL_DISPLAY_GROUPS = [
    {"mode": "production", "slug": "production", "label": "Production"},
    {"mode": "adt", "slug": "adt", "label": "ADT"},
    {"mode": "samsung", "slug": "samsung", "label": "Samsung specific services"},
    {"mode": "cat", "slug": "cat", "label": "CAT"},
    {"mode": "comcast", "slug": "comcast", "label": "Comcast"},
    {"mode": "goldenqa", "slug": "goldenqa", "label": "GoldenQA"},
    {"mode": "goldendev", "slug": "goldendev", "label": "GoldenDev"},
    {"mode": "qa", "slug": "qa", "label": "QA"},
]


def _sm_status_monitor_bundled_lists_enabled() -> bool:
    """If true (default), /statusmonitor uses the same committed lists/ files as the APM Status Wall."""
    return os.getenv("STATUS_MONITOR_USE_BUNDLED_LISTS", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _sm_read_service_names_from_bundled_file(path: str) -> list:
    """Non-comment, non-blank lines from a lists/*.txt file."""
    out: list = []
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                s = ln.strip()
                if s and not s.lstrip().startswith("#"):
                    out.append(s)
    except OSError as e:
        print(f"⚠️ status monitor: could not read bundled list {path!r}: {e}")
    return out


def _sm_bundled_status_monitor_service_list(environment: str) -> list | None:
    """
    Same per-environment service names as the APM wall (lists/*). None = use legacy
    resolution (GENERAL, dashboard, ADT_MONITOR, etc.).
    """
    if not _sm_status_monitor_bundled_lists_enabled():
        return None
    e = (environment or "").strip().lower()
    path = None
    if e == "production":
        path = _bundled_production_apm_127_path()
    elif e == "goldendev":
        path = _bundled_goldendev_apm_path()
    elif e == "goldenqa":
        path = _bundled_goldenqa_apm_path()
    elif e == "adt":
        path = _bundled_adt_apm_path()
    elif e == "cat":
        path = _bundled_cat_apm_path()
    elif e == "comcast":
        path = _bundled_comcast_apm_path()
    elif e == "samsung":
        path = _bundled_samsung_apm_path()
    elif e == "qa":
        path = _bundled_qa_apm_path()
    else:
        return None
    if not path or not os.path.isfile(path):
        return None
    names = _sm_read_service_names_from_bundled_file(path)
    if not names:
        return None
    return sorted(set(names), key=str.lower)


def _merge_samsung_dashboard_services(dynamic_services: list) -> list:
    """
    Always keep the 6 canonical Samsung APM service names, then add any extra
    names found on dashboard widgets that look Samsung-specific.

    Previously we replaced the canonical list entirely when the dashboard parse
    returned any services — that often yielded a partial list (or abbreviated
    names), so the monitor never queried all 6 active services.
    """
    base = list(SAMSUNG_MONITOR_SERVICES)
    seen = dict.fromkeys(base)
    out = list(base)
    for s in dynamic_services or []:
        if not s or not isinstance(s, str):
            continue
        if s in seen:
            continue
        if "samsung" not in s.lower():
            continue
        seen[s] = None
        out.append(s)
    return out


def _sm_page_environment_to_wall_dd_env(environment: str | None) -> str | None:
    """Map /statusmonitor/<slug> to APM wall `dd_env` (same as /apm-services)."""
    e = (environment or "").strip().lower()
    return {
        "production": "production",
        "goldendev": "goldendev",
        "goldenqa": "goldenqa",
        "adt": "adt_prod",
        "cat": "cat_prod",
        "comcast": "comcast_prod",
        "samsung": "samsung_prod",
        "qa": "qa",
    }.get(e)


def _sm_wall_dd_env_to_dd_tag(wall_dd_env: str) -> str:
    """Map Status Wall slug (e.g. cat_prod) to Datadog APM `env` tag (e.g. cat)."""
    env = (wall_dd_env or "").strip()
    if env == "samsung_prod":
        tag = (os.getenv("SAMSUNG_DD_ENV") or "samsung_prod").strip()
        return tag or "samsung_prod"
    if env == "cat_prod":
        tag = (os.getenv("CAT_DD_ENV") or "cat").strip()
        return tag or "cat"
    if env == "comcast_prod":
        tag = (os.getenv("COMCAST_DD_ENV") or "comcast").strip()
        return tag or "comcast"
    return env


def _sm_wall_dde_to_page_slug(wall_dde: str) -> str:
    """Map wall dd_env to /statusmonitor/<slug> and PagerDuty correlation slug."""
    return {
        "samsung_prod": "samsung",
        "adt_prod": "adt",
        "cat_prod": "cat",
        "comcast_prod": "comcast",
    }.get((wall_dde or "").strip(), (wall_dde or "").strip())


def _apm_wall_finalize_statuses(
    all_statuses: list,
    scope_services: list,
    wall_dde: str,
    dd_tag: str,
    *,
    dd_api_key: str | None = None,
    dd_app_key: str | None = None,
    dd_site: str | None = None,
) -> tuple[list, dict]:
    """
    Same post-fetch tile set as APM /apm-services: engineering merge → drop Other →
    org legacy idle shown as healthy (green).

    Returns (statuses, meta) where meta may include dropped_other, owner_by_service.
    """
    meta: dict = {"dropped_other": 0, "owner_by_service": None}
    try:
        from tools.apm_engineering_groups import (
            GOLDEN_WALL_DD_ENVS,
            apm_engineering_groups_enabled,
            apm_status_wall_use_dd_team,
            drop_other_unlisted_org_wall_tiles,
            engineering_wall_uses_org_catalog,
            fetch_datadog_catalog_service_owners,
            merge_engineering_wall_statuses,
            normalize_org_wall_legacy_tile_statuses,
        )
    except Exception as e:
        print(f"⚠️ APM wall finalize: import failed: {e}")
        return all_statuses, meta

    if wall_dde == "samsung_prod":
        by_svc = {
            (s.get("service") or "").strip(): s
            for s in all_statuses
            if s.get("service")
        }
        statuses: list = []
        for name in scope_services:
            row = by_svc.get(name)
            if row is not None:
                statuses.append(row)
            else:
                statuses.append(
                    {
                        "service": name,
                        "status": "inactive",
                        "environment": dd_tag,
                    }
                )
        statuses.sort(key=_wall_service_sort_key)
        return statuses, meta

    try:
        if apm_engineering_groups_enabled() and engineering_wall_uses_org_catalog(wall_dde):
            statuses = merge_engineering_wall_statuses(
                all_statuses,
                wall_dde,
                dd_tag,
                scope_service_names=scope_services,
            )
        else:
            statuses = [
                s
                for s in all_statuses
                if s.get("status") in ("healthy", "warning", "critical")
            ]
            statuses.sort(key=_wall_service_sort_key)
    except Exception:
        statuses = [
            s
            for s in all_statuses
            if s.get("status") in ("healthy", "warning", "critical")
        ]
        statuses.sort(key=_wall_service_sort_key)

    n_dropped_other = 0
    owner_by_service: dict | None = None
    try:
        if (
            apm_engineering_groups_enabled()
            and engineering_wall_uses_org_catalog(wall_dde)
            and apm_status_wall_use_dd_team(wall_dde)
            and dd_api_key
            and dd_app_key
        ):
            owner_by_service = fetch_datadog_catalog_service_owners(
                dd_api_key, dd_app_key, dd_site or os.getenv("DATADOG_SITE", "arlo.datadoghq.com")
            )
            meta["owner_by_service"] = owner_by_service
            statuses, n_dropped_other = drop_other_unlisted_org_wall_tiles(
                statuses, wall_dde, owner_by_service
            )
            meta["dropped_other"] = n_dropped_other
            if n_dropped_other:
                print(
                    f"🧭 APM wall scope ({wall_dde}): dropped {n_dropped_other} "
                    f"unlisted Other-bucket tile(s)"
                )
    except Exception as e:
        print(f"⚠️ Other-bucket filter skipped: {e}")

    try:
        if engineering_wall_uses_org_catalog(wall_dde) and wall_dde not in GOLDEN_WALL_DD_ENVS:
            statuses = normalize_org_wall_legacy_tile_statuses(statuses, wall_dde)
    except Exception as e:
        print(f"⚠️ Org legacy idle→OK normalization skipped: {e}")

    return statuses, meta


def _sm_apply_wall_display_statuses(
    all_statuses: list,
    scope_services: list,
    page_environment: str | None,
    dd_tag: str,
    *,
    dd_api_key: str | None = None,
    dd_app_key: str | None = None,
    dd_site: str | None = None,
) -> list:
    """Status monitor: same tile count/order/colors as APM status wall."""
    wall_dde = _sm_page_environment_to_wall_dd_env(page_environment)
    if not wall_dde or not scope_services:
        return all_statuses
    out, _meta = _apm_wall_finalize_statuses(
        all_statuses,
        scope_services,
        wall_dde,
        dd_tag,
        dd_api_key=dd_api_key,
        dd_app_key=dd_app_key,
        dd_site=dd_site,
    )
    if page_environment:
        print(
            f"📋 status monitor ({page_environment}): {len(out)} tile(s) after wall scope "
            f"(was {len(all_statuses)} raw DD rows)"
        )
    return out


def _sm_op_tile_metric_html(svc: dict) -> tuple[str, str]:
    """Metric cell for operational tile; org-wall idle → green OK (same as status wall)."""
    if svc.get("wall_idle"):
        return "—", "OK"
    er = svc.get("error_rate")
    if er is None:
        er = 0
    return html.escape(f"{er}"), "ERR"


def _sm_resolve_services_and_environments(environment):
    """
    Service list + Datadog env tag(s) for status monitor pages.
    Uses resolve_software_catalog_wall_service_names (same as APM /apm-services).
    environment None => legacy hub triple (production, goldendev, goldenqa).
    """
    if environment is None:
        return list(GENERAL_MONITOR_SERVICES), ["production", "goldendev", "goldenqa"]

    wall_dde = _sm_page_environment_to_wall_dd_env(environment)
    if wall_dde:
        services, source = resolve_software_catalog_wall_service_names(wall_dde)
        if services:
            dd_tag = _sm_wall_dd_env_to_dd_tag(wall_dde)
            print(
                f"📋 status monitor ({environment}): {len(services)} service(s) "
                f"via APM wall resolver ({source}, env tag={dd_tag})"
            )
            return services, [dd_tag]

    b = _sm_bundled_status_monitor_service_list(environment)
    if b is not None and environment in (
        "production",
        "goldendev",
        "goldenqa",
        "qa",
    ):
        return b, [environment]
    if b is not None and environment == "adt":
        return b, ["adt_prod"]
    if b is not None and environment == "cat":
        return b, [_sm_wall_dd_env_to_dd_tag("cat_prod")]
    if b is not None and environment == "comcast":
        return b, [_sm_wall_dd_env_to_dd_tag("comcast_prod")]
    if b is not None and environment == "samsung":
        return b, [_sm_wall_dd_env_to_dd_tag("samsung_prod")]
    if environment == "samsung":
        # README/APM: Samsung RED services use env tag samsung_prod (not "production").
        samsung_dd_env = (os.getenv("SAMSUNG_DD_ENV") or "samsung_prod").strip()
        if not samsung_dd_env:
            samsung_dd_env = "samsung_prod"
        dynamic_services = get_services_from_dashboard("wnz-fqh-z4f", cache_key="samsung_dashboard")
        services = _merge_samsung_dashboard_services(dynamic_services)
        return services, [samsung_dd_env]
    if environment == "adt":
        dynamic_services = get_services_from_dashboard("cum-ivw-92c", cache_key="adt_dashboard_v2")
        services = dynamic_services if dynamic_services else list(ADT_MONITOR_SERVICES)
        return services, ["adt_prod"]
    if environment == "cat":
        cat_tag = _sm_wall_dd_env_to_dd_tag("cat_prod")
        services, _src = resolve_software_catalog_wall_service_names("cat_prod")
        if services:
            return services, [cat_tag]
        return list(ADT_MONITOR_SERVICES), [cat_tag]
    if environment == "comcast":
        comcast_tag = _sm_wall_dd_env_to_dd_tag("comcast_prod")
        services, _src = resolve_software_catalog_wall_service_names("comcast_prod")
        if services:
            return services, [comcast_tag]
        return list(ADT_MONITOR_SERVICES), [comcast_tag]
    if environment == "redmetrics-us":
        dynamic_services = get_services_from_dashboard("qiz-7xc-fqr", cache_key="redmetrics_us_dashboard")
        services = dynamic_services if dynamic_services else list(GENERAL_MONITOR_SERVICES)
        return services, ["production"]
    if environment in ("production", "goldendev", "goldenqa"):
        return list(GENERAL_MONITOR_SERVICES), [environment]
    if environment == "qa":
        return list(GENERAL_MONITOR_SERVICES), ["qa"]
    raise ValueError(f"Invalid environment '{environment}'")


def _dd_health_cache_key(service: str, env: str, timerange_hours: int, dd_site: str) -> str:
    return f"{service}\x1f{env}\x1f{int(timerange_hours)}\x1f{dd_site}"


def _apm_wall_inactive_lookback_hours() -> int:
    """
    If the selected wall window has 0 APM hits but a longer window does (e.g. collector),
    re-query with this lookback so tiles are not falsely gray. Default 24h.
    Set APM_STATUS_WALL_INACTIVE_LOOKBACK_HOURS=0 to disable.
    """
    raw = (os.getenv("APM_STATUS_WALL_INACTIVE_LOOKBACK_HOURS", "24") or "24").strip()
    if raw.lower() in ("0", "false", "no", "off", "none"):
        return 0
    try:
        return max(1, min(168, int(raw)))
    except (TypeError, ValueError):
        return 24


def _sm_fetch_one_service_health_cached(
    service,
    env,
    dd_api_key,
    dd_app_key,
    dd_site,
    from_time,
    current_time,
    timerange_hours: int,
    force_refresh: bool = False,
):
    key = _dd_health_cache_key(service, env, timerange_hours, dd_site)
    ttl = _effective_db_cache_ttl_secs(force_refresh)
    cached = sm_api_cache_get("dd_service_health", key, ttl)
    if cached is not None:
        return cached, True
    out = get_service_health_status(
        service, env, dd_api_key, dd_app_key, dd_site, from_time, current_time, False
    )
    lookback_h = _apm_wall_inactive_lookback_hours()
    if (
        lookback_h > 0
        and int(timerange_hours) < lookback_h
        and out.get("status") == "inactive"
        and int(out.get("requests") or 0) == 0
    ):
        fb_from = current_time - (lookback_h * 3600)
        fb_out = get_service_health_status(
            service, env, dd_api_key, dd_app_key, dd_site, fb_from, current_time, False
        )
        if int(fb_out.get("requests") or 0) > 0 or fb_out.get("status") in (
            "healthy",
            "warning",
            "critical",
        ):
            fb_out = dict(fb_out)
            fb_out["wall_timerange_hours"] = int(timerange_hours)
            fb_out["wall_effective_lookback_hours"] = lookback_h
            out = fb_out
    if out.get("status") == "unknown" and _UNKNOWN_RETRY_COUNT > 0:
        for attempt in range(_UNKNOWN_RETRY_COUNT):
            delay = 0.22 * (attempt + 1)
            time.sleep(delay)
            retry_out = get_service_health_status(
                service, env, dd_api_key, dd_app_key, dd_site, from_time, current_time, False
            )
            if retry_out.get("status") != "unknown":
                out = retry_out
                print(
                    f"   🔁 {service} ({env}): unknown → {retry_out.get('status')} after retry {attempt + 1}"
                )
                break
    sm_api_cache_set("dd_service_health", key, out)
    return out, False


def _sm_fetch_parallel_service_health(
    services,
    environments,
    dd_api_key,
    dd_app_key,
    dd_site,
    from_time,
    current_time,
    timerange_hours: int,
    force_refresh: bool = False,
):
    all_statuses = []
    n_health_tasks = len(services) * len(environments)
    max_workers = _dd_health_worker_count(n_health_tasks)
    eff_ttl = _effective_db_cache_ttl_secs(force_refresh)
    print(
        f"📡 DD worker pool size: {max_workers} (tasks={n_health_tasks}), "
        f"DB cache TTL={eff_ttl}s (force_refresh={force_refresh})"
    )
    cache_hits = 0
    cache_miss = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for service in services:
            for env in environments:
                futures.append(
                    executor.submit(
                        _sm_fetch_one_service_health_cached,
                        service,
                        env,
                        dd_api_key,
                        dd_app_key,
                        dd_site,
                        from_time,
                        current_time,
                        timerange_hours,
                        force_refresh,
                    )
                )
        for future in as_completed(futures):
            try:
                row, hit = future.result()
                all_statuses.append(row)
                if hit:
                    cache_hits += 1
                else:
                    cache_miss += 1
            except Exception as e:
                print(f"Error in parallel execution: {e}")
    if cache_hits:
        print(
            f"🗄️ Datadog service health: {cache_hits} from DB cache, {cache_miss} live API "
            f"(same timerange≤{eff_ttl}s)"
        )
    return all_statuses


def _sm_pd_attach_escalation_summaries(incidents: list, data: dict) -> None:
    """Rellena incident.escalation_policy.summary desde la lista hermana de la API (include=escalation_policies)."""
    if not incidents or not isinstance(data, dict):
        return
    raw_eps = list(data.get("escalation_policies") or [])
    if not raw_eps:
        for item in data.get("included") or []:
            if isinstance(item, dict) and item.get("type") == "escalation_policy":
                raw_eps.append(item)
    id_to_summary: dict = {}
    for ep in raw_eps:
        if not isinstance(ep, dict) or not ep.get("id"):
            continue
        label = (ep.get("summary") or ep.get("name") or "").strip()
        if label:
            id_to_summary[ep["id"]] = label
    for inc in incidents:
        if not isinstance(inc, dict):
            continue
        ep = inc.get("escalation_policy")
        if not isinstance(ep, dict) or not ep.get("id"):
            continue
        if (ep.get("summary") or "").strip():
            continue
        sid = ep["id"]
        if sid in id_to_summary:
            ep["summary"] = id_to_summary[sid]


def _sm_pd_escalation_policy_summary(incident: dict) -> str:
    ep = incident.get("escalation_policy")
    if isinstance(ep, dict):
        return (ep.get("summary") or ep.get("name") or "").strip()
    return ""


def _sm_pd_incident_looks_streaming(title_lower: str, service_summary_lower: str, blob: str) -> bool:
    """Heuristic: PagerDuty incident looks related to streaming / live video."""
    combined = f"{title_lower} {service_summary_lower} {blob.lower()}"
    if "streaming" in combined or "live stream" in combined:
        return True
    if re.search(r"\bstream\b", combined):
        return True
    if "p0_streaming" in combined or "p0 streaming" in combined:
        return True
    if any(x in combined for x in ("hmsmatter", "hmsrecording", "webrtc", "transcod", "kurento", "mediamtx")):
        return True
    if "cvr" in combined and ("stream" in combined or "recording" in combined or "video" in combined):
        return True
    return False


# Escalation policy name (e.g. savant-ep) → substring that should appear in the APM service name
_PD_STREAMING_EP_SVC_RULES = (
    ("savant", "savant"),
    ("cvr", "cvr"),
    ("adt", "adt"),
    ("samsung", "samsung"),
    ("cat", "cat"),
    ("comcast", "comcast"),
)


def _sm_pd_streaming_ep_team_services(ep_summary: str, services: list) -> list:
    """
    If the incident is streaming-related and the escalation policy names the team (e.g. savant-ep),
    return monitored services that belong to that stack.
    """
    if not ep_summary or not services:
        return []
    esl = ep_summary.lower().strip()
    found: list = []
    seen = set()
    for ep_sub, svc_sub in _PD_STREAMING_EP_SVC_RULES:
        if ep_sub not in esl:
            continue
        for svc in services:
            if svc_sub in svc.lower() and svc not in seen:
                seen.add(svc)
                found.append(svc)
        if found:
            return found
    return []


def _sm_pd_incident_search_blob(incident: dict) -> str:
    """Combined PagerDuty incident text for correlation (title, description, service)."""
    chunks = []
    for key in ("title", "description", "summary"):
        v = incident.get(key)
        if isinstance(v, str) and v.strip():
            chunks.append(v)
    body = incident.get("body")
    if isinstance(body, dict):
        det = body.get("details")
        if isinstance(det, str) and det.strip():
            chunks.append(det)
    so = incident.get("service")
    if isinstance(so, dict):
        for k in ("summary", "name", "description"):
            v = so.get(k)
            if isinstance(v, str) and v.strip():
                chunks.append(v)
    ep_sum = _sm_pd_escalation_policy_summary(incident)
    if ep_sum:
        chunks.append(ep_sum)
    return " ".join(chunks)


def _sm_pd_resolve_fuzzy_pd_services(search_blob: str, services: list) -> list:
    """
    When the PD title mentions Savant / smart notifications but not the exact DD name,
    infer monitored services and return names that exist in `services`.
    """
    if not search_blob or not services:
        return []
    combined_lower = search_blob.lower()
    triggers = (
        "smart notification",
        "smart notifications",
        "smart-notification",
        "savant",
    )
    if not any(t in combined_lower for t in triggers):
        return []
    found: list = []
    seen = set()

    def _add(svc: str) -> None:
        if svc in seen:
            return
        seen.add(svc)
        found.append(svc)

    # 1) Tokens estilo backend-* o servicio con guiones en el texto
    for m in re.finditer(
        r"\b(backend-[a-z0-9-]+|[a-z][a-z0-9]*-[a-z0-9-]{2,}[a-z0-9]*)\b",
        search_blob,
        re.I,
    ):
        tok = m.group(1).lower()
        for svc in services:
            sl = svc.lower()
            if sl == tok or sl.startswith(tok + "-") or tok == sl:
                _add(svc)

    # 2) Savant → cualquier servicio monitorizado con "savant" en el nombre
    if "savant" in combined_lower:
        for svc in services:
            if "savant" in svc.lower():
                _add(svc)

    # 3) Smart notification(s) → stack de notificaciones (nombres típicos en APM)
    if any(
        x in combined_lower
        for x in ("smart notification", "smart notifications", "smart-notification")
    ):
        subs = ("hmsnotification", "partner-notifications", "notificationservice")
        for svc in services:
            sl = svc.lower()
            if any(sub in sl for sub in subs):
                _add(svc)

    return found


# Hyphen segments shared by many monitored service names. If we match `part in title` for these,
# a title like "backend-hmsgoogleapi ..." flags every `backend-*` service (false positives).
_PD_FUZZY_PART_BLOCKLIST = frozenset(
    {
        "backend",
        "nginx",
        "device",
        "oauth",
        "partner",
        "proxy",
        "logger",
        "hmsweb",
        "secret",
        "mqtt",
        "broker",
        "privacy",
        "registration",
        "support",
        "discovery",
        "directory",
        "presence",
        "messaging",
        "history",
        "advisor",
        "geolocation",
        "mediamigrationscheduler",
        "automation",  # backend-hmsautomation, scheduler, arloautomation-leader, etc.
        "arlo",
    }
)


def _sm_apply_pagerduty_correlation(
    all_statuses, services, environments, environment_slug, pd_incidents, silent: bool = False
):
    """Mutates all_statuses in place (same rules as drill-down). pd_incidents: list or None."""
    pd_affected_services = set()
    pd_incident_urls: dict[tuple[str, str], str] = {}
    if pd_incidents:
        if not silent:
            print(f"🔍 Filtering {len(pd_incidents)} PagerDuty incidents (triggered + acknowledged only, last 24h)...")
        recent_active_incidents = []
        cutoff_time = datetime.utcnow() - timedelta(hours=24)
        for incident in pd_incidents:
            incident_status = incident.get("status", "").lower()
            if incident_status not in ["triggered", "acknowledged"]:
                continue
            created_at_str = incident.get("created_at", "")
            try:
                if created_at_str:
                    created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                    if created_at < cutoff_time:
                        continue
            except Exception as e:
                if not silent:
                    print(f"  ⚠️  Could not parse incident date: {e}")
            recent_active_incidents.append(incident)
            pd_url = (incident.get("html_url") or "").strip()

            def _note_pd_url(svc_name: str, env_name: str) -> None:
                if pd_url and (svc_name, env_name) not in pd_incident_urls:
                    pd_incident_urls[(svc_name, env_name)] = pd_url

            title_raw = incident.get("title", "")
            title = title_raw.lower() if isinstance(title_raw, str) else ""
            service_obj = incident.get("service", {})
            if isinstance(service_obj, dict):
                service_summary = service_obj.get("summary", "").lower()
            else:
                service_summary = ""
            detected_environments = []
            for env in environments:
                env_lower = env.lower()
                if env_lower in title or env_lower in service_summary:
                    detected_environments.append(env)
            if not detected_environments:
                if any(keyword in title or keyword in service_summary for keyword in ["prod", "production"]):
                    detected_environments.append("production")
                elif any(keyword in title or keyword in service_summary for keyword in ["dev", "development"]):
                    detected_environments.append("goldendev")
                elif any(keyword in title or keyword in service_summary for keyword in ["qa", "quality"]):
                    detected_environments.append("goldenqa")
                elif "samsung" in title or "samsung" in service_summary:
                    detected_environments.append(
                        environments[0] if environment_slug == "samsung" and environments else "samsung_prod"
                    )
                elif "adt" in title or "adt" in service_summary or "partnerprod" in title or "partnerprod" in service_summary:
                    detected_environments.append("adt_prod")
                elif "comcast" in title or "comcast" in service_summary:
                    detected_environments.append("comcast_prod")
                elif re.search(r"\bcat\b", title) or re.search(r"\bcat\b", service_summary):
                    detected_environments.append("cat_prod")
            if not detected_environments:
                detected_environments = environments.copy()
            matched_services = []
            for service in services:
                service_lower = service.lower()
                if service_lower in title or service_lower in service_summary:
                    for env in detected_environments:
                        pd_affected_services.add((service, env))
                        _note_pd_url(service, env)
                    matched_services.append(service)
            for service in services:
                if service not in matched_services:
                    service_parts = service.split("-")
                    for part in service_parts:
                        pl = part.lower()
                        if (
                            len(pl) > 4
                            and pl not in _PD_FUZZY_PART_BLOCKLIST
                            and pl in title
                        ):
                            for env in detected_environments:
                                pd_affected_services.add((service, env))
                                _note_pd_url(service, env)
                            matched_services.append(service)
                            break
            blob = _sm_pd_incident_search_blob(incident)
            if not matched_services:
                for svc in _sm_pd_resolve_fuzzy_pd_services(blob, services):
                    for env in detected_environments:
                        pd_affected_services.add((svc, env))
                        _note_pd_url(svc, env)
                    matched_services.append(svc)
            ep_summary = _sm_pd_escalation_policy_summary(incident)
            if (
                ep_summary
                and _sm_pd_incident_looks_streaming(title, service_summary, blob)
            ):
                ep_svcs = _sm_pd_streaming_ep_team_services(ep_summary, services)
                if ep_svcs:
                    for svc in ep_svcs:
                        for env in detected_environments:
                            pd_affected_services.add((svc, env))
                            _note_pd_url(svc, env)
                        if svc not in matched_services:
                            matched_services.append(svc)
                    if not silent:
                        print(
                            f"  📺 Streaming + escalation policy '{ep_summary}' → "
                            f"{', '.join(ep_svcs)}"
                        )
            if matched_services:
                env_str = ", ".join(detected_environments) if len(detected_environments) < len(environments) else "all envs"
                if not silent:
                    print(
                        f"  🚨 Incident [{incident_status.upper()}]: '{title[:60]}...' → "
                        f"{', '.join(matched_services)} in [{env_str}]"
                    )
        if not silent:
            print(
                f"🔗 PagerDuty correlation: {len(recent_active_incidents)} recent active incidents → "
                f"{len(pd_affected_services)} service-environment pairs affected"
            )
    else:
        if not silent:
            print("✅ No active PagerDuty incidents")
    if pd_affected_services and not silent:
        affected_display = [f"{svc} ({env})" for svc, env in list(pd_affected_services)[:10]]
        print(f"   Affected: {', '.join(affected_display)}")
    for status_obj in all_statuses:
        service_env_tuple = (status_obj["service"], status_obj["environment"])
        if service_env_tuple in pd_affected_services:
            status_obj["pd_incident_url"] = pd_incident_urls.get(service_env_tuple)
            current_status = status_obj["status"]
            if current_status == "critical":
                status_obj["pd_incident"] = True
                if not silent:
                    print(
                        f"🚨 {status_obj['service']} ({status_obj['environment']}): CRITICAL "
                        f"(metrics + PagerDuty alert)"
                    )
            elif current_status == "warning":
                status_obj["status"] = "critical"
                status_obj["pd_incident"] = True
                if not silent:
                    print(
                        f"⚠️→🚨 Escalating {status_obj['service']} ({status_obj['environment']}) to CRITICAL "
                        f"(warning metrics + PagerDuty)"
                    )
            else:
                status_obj["status"] = "warning"
                status_obj["pd_incident"] = True
                if not silent:
                    print(
                        f"✅→⚠️ {status_obj['service']} ({status_obj['environment']}): WARNING "
                        f"(healthy metrics but PagerDuty alert active)"
                    )
        else:
            status_obj["pd_incident"] = False
            status_obj["pd_incident_url"] = None


def collect_hub_statuses_aligned_with_dashboard(
    timerange: int, environment_slug: str, pd_incidents_preloaded=None, force_refresh: bool = False
) -> list:
    """
    Same per-service Datadog APM checks + PagerDuty overrides as /statusmonitor/<slug> Summary
    (no HTML / EKS / Splunk). Hub cards match drill-down counts for the same timerange.

    If pd_incidents_preloaded is not None, PagerDuty is not fetched again (hub summary passes one shared list).
    """
    dd_api_key = os.getenv("DATADOG_API_KEY")
    dd_app_key = os.getenv("DATADOG_APP_KEY")
    dd_site = os.getenv("DATADOG_SITE", "arlo.datadoghq.com")
    pd_api_key = os.getenv("PAGERDUTY_API_TOKEN")
    if not dd_api_key or not dd_app_key:
        return []
    try:
        services, environments = _sm_resolve_services_and_environments(environment_slug)
    except ValueError as e:
        print(f"⚠️ Hub aligned: {e}")
        return []
    current_time = int(time.time())
    from_time = current_time - (timerange * 3600)
    if pd_incidents_preloaded is None:
        print(
            f"🧭 Hub (aligned with dashboard): {environment_slug} — {len(services)} services × "
            f"{len(environments)} env(s), {timerange}h"
        )
    all_statuses = _sm_fetch_parallel_service_health(
        services,
        environments,
        dd_api_key,
        dd_app_key,
        dd_site,
        from_time,
        current_time,
        int(timerange),
        force_refresh,
    )
    if pd_incidents_preloaded is not None:
        pd_incidents = list(pd_incidents_preloaded)
    else:
        pd_incidents = []
        if pd_api_key:
            try:
                _pd_counts, pd_incidents = get_pagerduty_status_counts(pd_api_key, force_refresh)
            except Exception as e:
                print(f"⚠️ Hub: PagerDuty fetch failed: {e}")
    _sm_apply_pagerduty_correlation(
        all_statuses,
        services,
        environments,
        environment_slug,
        pd_incidents,
        silent=pd_incidents_preloaded is not None,
    )
    return all_statuses


def _hub_collect_main_three_envs_batched(timerange: int, pd_incidents_preloaded, force_refresh: bool = False) -> dict:
    """
    Production + goldendev + goldenqa: one parallel wave each (per-env service list; same
    as drill-down / bundled lists/ when STATUS_MONITOR_USE_BUNDLED_LISTS=1).
    """
    dd_api_key = os.getenv("DATADOG_API_KEY")
    dd_app_key = os.getenv("DATADOG_APP_KEY")
    dd_site = os.getenv("DATADOG_SITE", "arlo.datadoghq.com")
    if not dd_api_key or not dd_app_key:
        return {"production": [], "goldendev": [], "goldenqa": []}
    sp, ep = _sm_resolve_services_and_environments("production")
    sg, eg = _sm_resolve_services_and_environments("goldendev")
    sq, eq = _sm_resolve_services_and_environments("goldenqa")
    if len(ep) != 1 or len(eg) != 1 or len(eq) != 1:
        return {"production": [], "goldendev": [], "goldenqa": []}
    eprod, egdev, egqa = ep[0], eg[0], eq[0]
    current_time = int(time.time())
    from_time = current_time - (timerange * 3600)
    n_tasks = len(sp) + len(sg) + len(sq)
    if pd_incidents_preloaded is None:
        print(
            f"🧭 Hub batch: main 3 envs — {len(sp)}+{len(sg)}+{len(sq)} = {n_tasks} "
            f"DD tasks, {timerange}h (bundled per env when enabled)"
        )
    all_statuses: list = []

    def _one_fetch(svcs, envs):
        return _sm_fetch_parallel_service_health(
            svcs,
            envs,
            dd_api_key,
            dd_app_key,
            dd_site,
            from_time,
            current_time,
            int(timerange),
            force_refresh,
        )

    with ThreadPoolExecutor(max_workers=3) as ex:
        f1 = ex.submit(_one_fetch, sp, ep)
        f2 = ex.submit(_one_fetch, sg, eg)
        f3 = ex.submit(_one_fetch, sq, eq)
        for fut in as_completed((f1, f2, f3)):
            try:
                all_statuses.extend(fut.result())
            except Exception as e:
                print(f"❌ Hub batch partial fetch error: {e}")
    services_union = list(dict.fromkeys([*sp, *sg, *sq]))
    envs_for_pd = list(dict.fromkeys([*ep, *eg, *eq]))
    pd_incidents = list(pd_incidents_preloaded) if pd_incidents_preloaded is not None else []
    if pd_incidents_preloaded is None:
        pd_api_key = os.getenv("PAGERDUTY_API_TOKEN")
        if pd_api_key:
            try:
                _pd_counts, pd_incidents = get_pagerduty_status_counts(pd_api_key, force_refresh)
            except Exception as e:
                print(f"⚠️ Hub batch: PagerDuty fetch failed: {e}")
    _sm_apply_pagerduty_correlation(
        all_statuses, services_union, envs_for_pd, None, pd_incidents, silent=pd_incidents_preloaded is not None
    )
    out = {"production": [], "goldendev": [], "goldenqa": []}

    def _hub_main_three_bucket_key(env) -> str | None:
        """Map DD env tag onto hub keys so Status wall / hub batch never drops rows on alias mismatch."""
        if env is None:
            return None
        s = str(env).strip().lower()
        if s in ("production", "prod"):
            return "production"
        if s == "goldendev":
            return "goldendev"
        if s == "goldenqa":
            return "goldenqa"
        return None

    for s in all_statuses:
        key = _hub_main_three_bucket_key(s.get("environment"))
        if key in out:
            out[key].append(s)
    return out


def _fetch_datadog_statuses_for_mode(
    timerange: int, mode: str, pd_incidents_preloaded=None, force_refresh: bool = False
) -> list:
    """Hub summary: same logic as drill-down for this environment slug."""
    return collect_hub_statuses_aligned_with_dashboard(
        timerange, mode, pd_incidents_preloaded, force_refresh
    )


def _hub_service_alert_count(s: dict) -> int:
    """DD monitors in alert (+ PagerDuty incident as +1)."""
    n_dd = int(s.get("dd_monitor_open_count") or 0)
    if not n_dd:
        n_dd = int(s.get("dd_monitor_alert_count") or 0) + int(
            s.get("dd_monitor_alert_suffix_count") or 0
        )
    if not n_dd:
        n_dd = len(s.get("dd_monitor_alerts") or []) + len(
            s.get("dd_monitor_alerts_suffix_ab") or []
        )
    pd = 1 if s.get("pd_incident") else 0
    return int(n_dd) + int(pd)


def _hub_service_issue_href(s: dict, env_href: str = "") -> str:
    for key in ("dd_monitors_url_all_alerts", "dd_monitors_url", "apm_url", "pd_incident_url"):
        u = (s.get(key) or "").strip()
        if u:
            return u
    svc = (s.get("service") or "").strip()
    env = (s.get("environment") or "").strip()
    if svc and env:
        dd_site = os.getenv("DD_SITE", "arlo.datadoghq.com")
        alerts = _hub_service_alert_count(s)
        if alerts > 0:
            u = _dd_monitors_manage_url_all_alerts(svc, env, dd_site)
            if u:
                return u
        return (
            f"{datadog_ui_origin(dd_site)}/apm/service/"
            f"{quote(svc, safe='')}/overview?env={quote(env, safe='')}"
        )
    return (env_href or "").strip()


def _hub_display_service_name(service: str) -> str:
    svc = (service or "").strip()
    if svc.startswith("backend-"):
        return "bknd-" + svc[len("backend-") :]
    return svc


def _hub_build_issue_services(
    statuses: list,
    env_href: str = "",
    max_items: int = 12,
) -> tuple[list[dict], int]:
    """
    Services degraded (warning/critical) or with DD/PD alerts — for hub card links.
    Returns (items, truncated_count).
    """
    issues: list[dict] = []
    for s in statuses or []:
        st = (s.get("status") or "unknown").strip().lower()
        alerts = _hub_service_alert_count(s)
        degraded = st in ("warning", "critical")
        if not degraded and alerts <= 0:
            continue
        svc = (s.get("service") or "").strip()
        if not svc:
            continue
        href = _hub_service_issue_href(s, env_href)
        if not href:
            href = env_href
        issues.append(
            {
                "service": svc,
                "display": _hub_display_service_name(svc),
                "status": st if degraded else "alert",
                "alert_count": alerts,
                "href": href,
            }
        )

    def _sort_key(x: dict) -> tuple:
        st = x.get("status") or ""
        rank = 0 if st == "critical" else 1 if st == "warning" else 2
        return (rank, -int(x.get("alert_count") or 0), x.get("display") or "")

    issues.sort(key=_sort_key)
    if len(issues) <= max_items:
        return issues, 0
    return issues[:max_items], len(issues) - max_items


def _hub_entry_from_wall_payload(row: dict, wall_payload: dict) -> dict:
    """Build hub card fields from an APM Status Wall single-env payload."""
    group = (wall_payload.get("groups") or [{}])[0]
    counts = group.get("counts") or {}
    overall = group.get("overall") or "healthy"
    ser = list(group.get("services") or [])
    h = int(counts.get("healthy") or 0)
    w = int(counts.get("warning") or 0)
    c = int(counts.get("critical") or 0)
    dd_atot, dd_asvcs = _hub_dd_alerts_rollup(ser)
    bad = [s for s in ser if s.get("status") in ("warning", "critical")]
    issue_services, issue_truncated = _hub_build_issue_services(ser, row.get("href") or "")
    return {
        "slug": row["slug"],
        "label": row["label"],
        "href": row["href"],
        "healthy": h,
        "warning": w,
        "critical": c,
        "unknown": 0,
        "inactive": 0,
        "operational": h + w + c,
        "configured": int(counts.get("total") or len(ser)),
        "monitored": int(counts.get("total") or len(ser)),
        "overall": overall,
        "dd_monitor_alerts_total": dd_atot,
        "dd_monitor_alerts_services": dd_asvcs,
        "status_reason_lines": _hub_build_status_reason_lines(bad, overall),
        "issue_services": issue_services,
        "issue_services_truncated": issue_truncated,
        "aligned_with_status_wall": True,
    }


def _hub_build_entry_from_legacy_statuses(row: dict, statuses_for_card: list) -> dict:
    """Hub card for environments not aligned with the APM Status Wall."""
    h = sum(1 for s in statuses_for_card if s.get("status") == "healthy")
    w = sum(1 for s in statuses_for_card if s.get("status") == "warning")
    c = sum(1 for s in statuses_for_card if s.get("status") == "critical")
    unk = sum(1 for s in statuses_for_card if s.get("status") == "unknown")
    inn = sum(1 for s in statuses_for_card if s.get("status") == "inactive")
    if c > 0:
        overall = "critical"
    elif w > 0:
        overall = "warning"
    else:
        overall = "healthy"
    dd_atot, dd_asvcs = _hub_dd_alerts_rollup(statuses_for_card)
    issue_services, issue_truncated = _hub_build_issue_services(
        statuses_for_card, row.get("href") or ""
    )
    return {
        "slug": row["slug"],
        "label": row["label"],
        "href": row["href"],
        "healthy": h,
        "warning": w,
        "critical": c,
        "unknown": unk,
        "inactive": inn,
        "operational": h + w + c,
        "configured": len(statuses_for_card),
        "monitored": len(statuses_for_card),
        "overall": overall,
        "dd_monitor_alerts_total": dd_atot,
        "dd_monitor_alerts_services": dd_asvcs,
        "status_reason_lines": _hub_build_status_reason_lines(statuses_for_card, overall),
        "issue_services": issue_services,
        "issue_services_truncated": issue_truncated,
        "aligned_with_status_wall": False,
    }


def _hub_build_status_reason_lines(statuses_for_card: list, overall: str, max_lines: int = 2) -> list:
    """
    Compact English reason lines for Environment status cards when overall is warning/critical.
    """
    if overall == "healthy":
        return []
    if not statuses_for_card:
        return ["No service data available for this environment."]

    bad = [s for s in statuses_for_card if s.get("status") in ("warning", "critical")]
    h = sum(1 for s in statuses_for_card if s.get("status") == "healthy")
    w = sum(1 for s in bad if s.get("status") == "warning")
    c = sum(1 for s in bad if s.get("status") == "critical")
    if c == 0 and w == 0:
        return []
    lines = []

    pd_count = sum(1 for s in bad if s.get("pd_incident"))
    if pd_count:
        lines.append(f"PagerDuty incidents impacting {pd_count} service(s).")

    er_crit = sum(1 for s in bad if float(s.get("error_rate") or 0) > 5 and not s.get("traffic_drop"))
    if er_crit:
        lines.append(f"Error rate above 5% on {er_crit} service(s).")

    er_warn = sum(1 for s in bad if 1 < float(s.get("error_rate") or 0) <= 5)
    if er_warn:
        lines.append(f"Error rate between 1% and 5% on {er_warn} service(s).")

    lat_count = sum(1 for s in bad if s.get("high_latency"))
    if lat_count:
        lines.append(f"High latency detected on {lat_count} service(s).")

    td_count = sum(1 for s in bad if s.get("traffic_drop"))
    if td_count:
        lines.append(f"Traffic drop vs baseline on {td_count} service(s).")

    def _dd_n(x):
        n_c = int(x.get("dd_monitor_alert_count") or 0) or len(x.get("dd_monitor_alerts") or [])
        n_s = int(x.get("dd_monitor_alert_suffix_count") or 0) or len(
            x.get("dd_monitor_alerts_suffix_ab") or []
        )
        return n_c + n_s

    dd_one = [s for s in statuses_for_card if _dd_n(s) == 1]
    dd_mul = [s for s in statuses_for_card if _dd_n(s) > 1]
    if dd_mul:
        lines.append(
            f"Datadog: ≥2 monitors in Alert on {len(dd_mul)} service(s) (see hover for names)."
        )
    if dd_one:
        lines.append(
            f"Datadog: 1 monitor in Alert on {len(dd_one)} service(s) (see hover for names)."
        )

    if not lines and bad:
        lines.append("Open the environment page for per-service details.")

    return lines[:max_lines]


def _hub_dd_alerts_rollup(statuses: list) -> tuple[int, int]:
    """
    Total Datadog monitor Alert count across services (critical-tier + -a/-b tier),
    and how many services have ≥1 firing monitor.
    """
    tot = 0
    n_svcs = 0
    for s in statuses or []:
        n_c = int(s.get("dd_monitor_alert_count") or 0) or len(s.get("dd_monitor_alerts") or [])
        n_s = int(s.get("dd_monitor_alert_suffix_count") or 0) or len(
            s.get("dd_monitor_alerts_suffix_ab") or []
        )
        n = n_c + n_s
        if n > 0:
            n_svcs += 1
        tot += n
    return int(tot), int(n_svcs)


def _hub_collect_statuses_by_mode(
    timerange: int, log_label: str = "Hub summary", force_refresh: bool = False
) -> dict:
    """
    One shared PagerDuty pull + parallel Datadog per hub mode (same layout as hub drill-down).
    Returns dict mode -> list of per-service status dicts.
    """
    pd_api_key = os.getenv("PAGERDUTY_API_TOKEN")
    pd_incidents_shared = []
    if pd_api_key:
        try:
            _pd_counts_hub, pd_incidents_shared = get_pagerduty_status_counts(
                pd_api_key, force_refresh
            )
            print(
                f"🧭 {log_label}: shared PagerDuty pull — {len(pd_incidents_shared)} incident(s) "
                f"(triggered/ack for correlation)"
            )
        except Exception as e:
            print(f"⚠️ {log_label}: PagerDuty fetch failed: {e}")

    statuses_by_mode = {}
    with ThreadPoolExecutor(max_workers=STATUS_MONITOR_HUB_PARALLEL_ENVS) as ex:
        f_batch = ex.submit(
            _hub_collect_main_three_envs_batched, timerange, pd_incidents_shared, force_refresh
        )
        f_extra = {}
        for row in HUB_ENV_ROWS:
            if row["mode"] in ("production", "goldendev", "goldenqa"):
                continue
            f_extra[row["mode"]] = ex.submit(
                _fetch_datadog_statuses_for_mode,
                timerange,
                row["mode"],
                pd_incidents_shared,
                force_refresh,
            )
        try:
            main_three = f_batch.result()
            statuses_by_mode["production"] = main_three.get("production", [])
            statuses_by_mode["goldendev"] = main_three.get("goldendev", [])
            statuses_by_mode["goldenqa"] = main_three.get("goldenqa", [])
        except Exception as e:
            print(f"❌ Hub batch (main 3 envs) error: {e}")
            statuses_by_mode["production"] = []
            statuses_by_mode["goldendev"] = []
            statuses_by_mode["goldenqa"] = []
        for mode, fut in f_extra.items():
            try:
                statuses_by_mode[mode] = fut.result()
            except Exception as e:
                print(f"❌ {log_label} error for {mode}: {e}")
                statuses_by_mode[mode] = []
    return statuses_by_mode


def _wall_status_reason_plain(s: dict) -> str:
    """Plain-text alert context for status wall tooltip (same signals as command center reasons)."""
    parts = []
    st = s.get("status")
    n_dd = int(s.get("dd_monitor_alert_count") or 0) or len(s.get("dd_monitor_alerts") or [])
    n_suf = int(s.get("dd_monitor_alert_suffix_count") or 0) or len(
        s.get("dd_monitor_alerts_suffix_ab") or []
    )
    if n_dd >= 1:
        parts.append(f"{n_dd} DD")
    if n_suf >= 1 and n_dd == 0:
        if st == "healthy":
            parts.append(f"{n_suf} DD open (-a/-b, tile stays green)")
        else:
            parts.append(f"{n_suf} DD")
    elif n_suf >= 1 and n_dd >= 1:
        parts.append(f"+{n_suf} DD")
    if s.get("pd_incident"):
        parts.append("PD incident")
    if s.get("traffic_drop"):
        parts.append("Traffic drop vs 7d")
    if s.get("high_latency"):
        parts.append("High latency (APM)")
    er = float(s.get("error_rate") or 0)
    if st == "critical" and er > 5:
        parts.append("Error rate >5%")
    elif st == "warning" and er > 1:
        parts.append("Error rate >1%")
    tv = s.get("traffic_variance")
    if not s.get("traffic_drop") and tv is not None and abs(float(tv)) >= 12:
        parts.append(f"Traffic {float(tv):+.0f}% vs 7d")
    if not parts:
        if st == "healthy":
            parts.append("Healthy — within APM thresholds")
        elif st == "inactive":
            parts.append("No APM hits in selected time window")
        elif st == "unknown":
            parts.append("No data or APM query issue")
        elif st == "critical":
            parts.append("Critical (APM)")
        elif st == "warning":
            parts.append("Warning (APM)")
        else:
            parts.append("—")
    text = " · ".join(parts)
    if s.get("dd_monitor_override") and n_dd == 0 and n_suf == 0 and "override" not in text.lower():
        text = f"{text} · DD monitors OK (override)"
    return text


def _wall_service_sort_key(s: dict):
    """Critical/warning first (by error rate), then healthy alpha, then other."""
    st = s.get("status")
    er = float(s.get("error_rate") or 0)
    err_cnt = int(s.get("errors") or 0)
    name = (s.get("service") or "").lower()
    if st == "critical":
        return (0, -er, -err_cnt, name)
    if st == "warning":
        return (1, -er, -err_cnt, name)
    if st == "healthy":
        return (2, name, 0, "")
    return (3, name, 0, "")


def _wall_pd_semaphore_badge(counts: dict, label: str, scope_note: str) -> dict:
    """
    Same traffic-light rules as the main PagerDuty card near Arlo status:
    critical if triggered, warning if ack only, ok if clear.
    scope_note is shown in parentheses in detail text, e.g. '24h' or 'Samsung board PRBJIO4'.
    """
    tr = int(counts.get("triggered") or 0)
    ack = int(counts.get("acknowledged") or 0)
    href = _sm_pagerduty_external_incidents_url()
    if tr > 0:
        return {
            "label": label,
            "status": "critical",
            "short": f"{tr} trg",
            "detail": f"{tr} triggered, {ack} ack ({scope_note})",
            "href": href,
        }
    if ack > 0:
        return {
            "label": label,
            "status": "warning",
            "short": f"{ack} ack",
            "detail": f"{ack} acknowledged ({scope_note})",
            "href": href,
        }
    return {
        "label": label,
        "status": "ok",
        "short": "OK",
        "detail": f"No active incidents ({scope_note})",
        "href": href,
    }


def _wall_pd_badge(counts: dict) -> dict:
    """Compact status for Status wall header (PagerDuty incidents, last 24h API window)."""
    return _wall_pd_semaphore_badge(counts, "PD", "24h")


def _wall_splunk_badge(payload: dict) -> dict:
    """P0 predict / outliers summary from splunk_outliers_monitor_payload."""
    if not payload.get("success"):
        err = payload.get("error") or "unavailable"
        return {"label": "SPL", "status": "unknown", "short": "—", "detail": err}
    tools = payload.get("tools") or []
    tot = sum(int(t.get("total_outliers") or 0) for t in tools)
    th = int(payload.get("timerange_hours") or 0)
    if tot > 0:
        return {
            "label": "SPL",
            "status": "warning",
            "short": f"{tot} out",
            "detail": f"P0 predict: {tot} outliers ({th}h)",
        }
    return {
        "label": "SPL",
        "status": "ok",
        "short": "OK",
        "detail": f"P0 predict: no outliers ({th}h)",
    }


def _wall_split_services_by_region(services: list) -> tuple[list, list]:
    """Arlo Global (Oregon) vs Arlo EU (Ireland); Multi-region appears in both."""
    arlo_global = []
    arlo_eu = []
    for s in services:
        r = (s.get("region") or "Ireland").strip()
        if r == "Multi-region":
            arlo_global.append(s)
            arlo_eu.append(s)
        elif r == "Oregon":
            arlo_global.append(s)
        elif r == "Ireland":
            arlo_eu.append(s)
        else:
            arlo_eu.append(s)
    arlo_global.sort(key=_wall_service_sort_key)
    arlo_eu.sort(key=_wall_service_sort_key)
    return arlo_global, arlo_eu


def _wall_filter_nonempty_region_columns(region_columns: list | None) -> list:
    """Omit region buckets with no services (e.g. hide Arlo EU when all tiles are Oregon)."""
    if not region_columns:
        return []
    return [c for c in region_columns if c.get("services")]


def _wall_fetch_monitor_badges(timerange: int, force_refresh: bool) -> dict:
    """PagerDuty + Splunk P0 badges for Status wall / APM wall section headers."""
    pd_badge = {
        "label": "PD",
        "status": "unknown",
        "short": "—",
        "detail": "PAGERDUTY_API_TOKEN not set",
    }
    pd_api_key = os.getenv("PAGERDUTY_API_TOKEN")
    if pd_api_key:
        try:
            counts, _ = get_pagerduty_status_counts(pd_api_key, force_refresh)
            pd_badge = _wall_pd_badge(counts)
        except Exception as e:
            pd_badge = {
                "label": "PD",
                "status": "unknown",
                "short": "—",
                "detail": str(e)[:200],
            }

    spl_badge = {
        "label": "SPL",
        "status": "unknown",
        "short": "—",
        "detail": "SPLUNK_TOKEN not set",
    }
    try:
        from tools.splunk_tool import splunk_outliers_monitor_payload

        # P0 semaphore: default P0 lookback (splunk_p0_default_timerange_hours), not the wall’s DD timerange
        spl = splunk_outliers_monitor_payload()
        spl_badge = _wall_splunk_badge(spl)
    except Exception as e:
        spl_badge = {
            "label": "SPL",
            "status": "unknown",
            "short": "—",
            "detail": str(e)[:200],
        }

    return {
        "pagerduty": pd_badge,
        "splunk": spl_badge,
    }


def _wall_serialize_status(
    s: dict, dd_site: str, wall_mode: str | None = None, timerange_hours: int = 24
) -> dict:
    svc = s.get("service") or ""
    env = s.get("environment") or ""
    spl_url = None
    pd_u = (s.get("pd_incident_url") or "").strip() or None
    if _sm_status_shows_issue_links(s):
        su = _sm_splunk_service_search_url(str(svc), max(1, min(int(timerange_hours), 168)))
        spl_url = su if su else None
    if pd_u:
        pd_u = _sm_sanitize_href_for_wall(pd_u) or None
    if spl_url:
        spl_url = _sm_sanitize_href_for_wall(spl_url) or None
    ddm = (s.get("dd_monitors_url") or "").strip() or None
    if not ddm:
        b = _dd_monitors_manage_url(str(svc), str(env), dd_site)
        ddm = b if b else None
    if ddm:
        ddm = _sm_sanitize_href_for_wall(ddm) or None
    n_suf_w = int(s.get("dd_monitor_alert_suffix_count") or 0) or len(
        s.get("dd_monitor_alerts_suffix_ab") or []
    )
    ddm_all = (s.get("dd_monitors_url_all_alerts") or "").strip() or None
    if not ddm_all and n_suf_w > 0 and svc and env:
        b_all = _dd_monitors_manage_url_all_alerts(str(svc), str(env), dd_site)
        ddm_all = b_all if b_all else None
    if ddm_all:
        ddm_all = _sm_sanitize_href_for_wall(ddm_all) or None
    return {
        "service": svc,
        "environment": env,
        "region": _sm_infer_service_region(s, page_environment=wall_mode),
        "status": s.get("status"),
        "error_rate": s.get("error_rate"),
        "requests": s.get("requests"),
        "errors": s.get("errors"),
        "pd_incident": bool(s.get("pd_incident")),
        "pd_incident_url": pd_u,
        "splunk_url": spl_url,
        "high_latency": bool(s.get("high_latency")),
        "traffic_drop": bool(s.get("traffic_drop")),
        "p95_latency": s.get("p95_latency"),
        "traffic_variance": s.get("traffic_variance"),
        "dd_monitor_override": bool(s.get("dd_monitor_override")),
        "dd_monitor_alerts": list(s.get("dd_monitor_alerts") or [])[:32],
        "dd_monitor_alert_count": int(s.get("dd_monitor_alert_count") or 0)
        or len(s.get("dd_monitor_alerts") or []),
        "dd_monitor_alerts_suffix_ab": list(s.get("dd_monitor_alerts_suffix_ab") or [])[:32],
        "dd_monitor_alert_suffix_count": int(s.get("dd_monitor_alert_suffix_count") or 0)
        or len(s.get("dd_monitor_alerts_suffix_ab") or []),
        "dd_monitor_open_count": int(s.get("dd_monitor_open_count") or 0)
        or (
            int(s.get("dd_monitor_alert_count") or 0)
            + int(s.get("dd_monitor_alert_suffix_count") or 0)
        ),
        "dd_monitors_url": ddm,
        "dd_monitors_url_all_alerts": ddm_all,
        "status_reason": _wall_status_reason_plain(s),
        "eks_clusters": list(s.get("eks_clusters") or []),
        "p99_latency": s.get("p99_latency"),
        "apm_url": (
            f"{datadog_ui_origin(dd_site)}/apm/service/"
            f"{quote(str(svc), safe='')}/overview?env={quote(str(env), safe='')}"
        ),
    }


def status_monitor_wall_data(timerange: int = 1, force_refresh: bool = False) -> dict:
    """
    JSON for /statuswall: all hub environments in a fixed order, per-service tiles
    (same Datadog APM + PagerDuty semantics as the hub).

    Wall shows only operational tiles (healthy / warning / critical). inactive and
    unknown are omitted so the screen stays focused on live APM signal + issues.
    """
    global _wall_data_cache
    cache_version = "wall_v22_dd_ab_green_pill"
    cache_key = f"{cache_version}_{timerange}_{int(time.time() // _cache_ttl)}"
    hit = _read_sm_mem_cache(_wall_data_cache, cache_key, force_refresh)
    if hit is not None:
        return dict(hit)

    # Overlap hub Datadog fan-out with PD+Splunk badge fetch (saves wall-clock vs sequential).
    with ThreadPoolExecutor(max_workers=2) as _wall_pool:
        f_hub = _wall_pool.submit(_hub_collect_statuses_by_mode, timerange, "Status wall", force_refresh)
        f_badges = _wall_pool.submit(_wall_fetch_monitor_badges, timerange, force_refresh)
        statuses_by_mode = f_hub.result()
        monitors = f_badges.result()
    dd_site = os.getenv("DD_SITE", "datadoghq.com")
    groups = []
    eks_wall_cache = {}
    for g in WALL_DISPLAY_GROUPS:
        mode = g["mode"]
        statuses = list(statuses_by_mode.get(mode) or [])
        if mode == "samsung":
            _blw = _sm_bundled_status_monitor_service_list("samsung")
            canon = set(_blw) if _blw is not None else set(SAMSUNG_MONITOR_SERVICES)
            statuses = [s for s in statuses if s.get("service") in canon]
        statuses = [
            s
            for s in statuses
            if s.get("status") in ("healthy", "warning", "critical")
        ]
        statuses.sort(key=_wall_service_sort_key)
        if _classic_status_wall_attach_eks():
            _attach_eks_clusters_wall(statuses, timerange, eks_wall_cache, force_refresh)

        h = sum(1 for s in statuses if s.get("status") == "healthy")
        w = sum(1 for s in statuses if s.get("status") == "warning")
        c = sum(1 for s in statuses if s.get("status") == "critical")
        unk = 0
        inn = 0
        if c > 0:
            overall = "critical"
        elif w > 0:
            overall = "warning"
        else:
            overall = "healthy"

        ser = [_wall_serialize_status(s, dd_site, mode, timerange) for s in statuses]
        if mode in ("adt", "samsung", "cat", "comcast"):
            region_columns = [
                {
                    "key": "oregon_all",
                    "label": "Services",
                    "subtitle": "Oregon",
                    "services": list(ser),
                },
            ]
        else:
            ag, ar_eu = _wall_split_services_by_region(ser)
            region_columns = [
                {
                    "key": "arlo_global",
                    "label": "Arlo Global",
                    "subtitle": "Oregon",
                    "services": ag,
                },
                {
                    "key": "arlo_eu",
                    "label": "Arlo EU",
                    "subtitle": "Ireland",
                    "services": ar_eu,
                },
            ]
        region_columns = _wall_filter_nonempty_region_columns(region_columns)
        groups.append(
            {
                "slug": g["slug"],
                "label": g["label"],
                "mode": mode,
                "overall": overall,
                "counts": {
                    "healthy": h,
                    "warning": w,
                    "critical": c,
                    "unknown": unk,
                    "inactive": inn,
                    "total": len(statuses),
                },
                "services": ser,
                "region_columns": region_columns,
            }
        )

    out = {"success": True, "timerange": timerange, "monitors": monitors, "groups": groups}
    _write_sm_mem_cache(_wall_data_cache, cache_key, out)
    return dict(out)


def _software_catalog_fallback_service_names() -> list:
    """
    When the Software Catalog API is unavailable (403 / keys), use the same service names
    the monitor already tracks (ADT + general + treemap extras) — parity with ~90 Software Catalog.
    """
    merged = set(ADT_MONITOR_SERVICES)
    merged.update(GENERAL_MONITOR_SERVICES)
    merged.update(SOFTWARE_CATALOG_TREEMAP_EXTRAS)
    return sorted(merged, key=str.lower)


def _software_catalog_wall_use_dd_apm_list(dd_env: str) -> bool:
    """Use GET /api/v2/apm/services?filter[env]=… (same source as Datadog Software UI)."""
    env = (dd_env or "").strip()
    if env not in SOFTWARE_CATALOG_WALL_APM_ENVS:
        return False
    raw = (os.getenv("SOFTWARE_CATALOG_WALL_DD_APM_LIST") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    try:
        from tools.apm_engineering_groups import apm_engineering_groups_enabled

        return apm_engineering_groups_enabled()
    except Exception:
        return True


def _fetch_datadog_apm_service_names_for_env(
    dd_api_key: str,
    dd_app_key: str,
    dd_site: str,
    dd_env: str,
) -> list | None:
    """
    Services registered in Datadog APM for this env (Software Catalog /software?env=…).
    GET /api/v2/apm/services — matches the production list (~129–133), not a static file.
    """
    import requests

    env = (dd_env or "production").strip() or "production"
    base = f"{datadog_rest_api_base(dd_site)}/api/v2/apm/services"
    headers = {
        "DD-API-KEY": dd_api_key,
        "DD-APPLICATION-KEY": dd_app_key,
        "Accept": "application/json",
    }
    try:
        r = requests.get(
            base,
            headers=headers,
            params={"filter[env]": env},
            timeout=(15, 90),
        )
    except Exception as e:
        print(f"⚠️ Datadog APM services list failed: {e}")
        return None
    if r.status_code != 200:
        print(
            f"⚠️ Datadog APM services list {r.status_code} (env={env}): {(r.text or '')[:300]}"
        )
        return None
    try:
        payload = r.json()
    except Exception:
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    attr = data.get("attributes") or {}
    services = attr.get("services") if isinstance(attr, dict) else None
    if not isinstance(services, list):
        return None
    names = sorted(
        {(str(s) or "").strip() for s in services if (s or "").strip()},
        key=str.lower,
    )
    return names if names else None


def _resolve_software_catalog_wall_from_dd_apm(
    dd_env: str,
    dd_api: str,
    dd_app: str,
    dd_site: str,
) -> tuple[list, str] | None:
    """Live APM service list for org-wall envs (production, adt_prod)."""
    if not _software_catalog_wall_use_dd_apm_list(dd_env):
        return None
    dd_tag = _sm_wall_dd_env_to_dd_tag(dd_env)
    apm_names = _fetch_datadog_apm_service_names_for_env(
        dd_api, dd_app, dd_site, dd_tag
    )
    if not apm_names:
        return None
    try:
        from tools.apm_engineering_groups import (
            apm_engineering_groups_enabled,
            apm_status_wall_use_dd_team,
            fetch_datadog_catalog_service_owners,
            merge_apm_names_with_org_wall_legacy,
            order_services_for_engineering_wall,
        )

        apm_names, n_org_legacy = merge_apm_names_with_org_wall_legacy(
            apm_names, dd_env
        )
        if n_org_legacy:
            from tools.apm_engineering_groups import org_wall_legacy_list_path

            leg_path = org_wall_legacy_list_path(dd_env)
            print(
                f"🧭 Software catalog wall: merged {n_org_legacy} org-wall name(s) "
                f"from {leg_path} into APM scope"
            )

        if apm_engineering_groups_enabled():
            owners = None
            if apm_status_wall_use_dd_team(dd_env):
                owners = fetch_datadog_catalog_service_owners(
                    dd_api, dd_app, dd_site
                )
            ordered = order_services_for_engineering_wall(
                apm_names,
                dd_env=dd_env,
                owner_by_service=owners,
            )
            team_note = (
                ", Datadog groupBy=Team"
                if owners
                else ", org tile order"
            )
            print(
                f"🧭 Software catalog wall: {len(ordered)} service(s) from "
                f"Datadog APM services API (env={dd_env}{team_note})"
            )
            return (
                ordered,
                "dd_apm_services_dd_team" if owners else "dd_apm_services_org_order",
            )
    except Exception:
        pass
    print(
        f"🧭 Software catalog wall: {len(apm_names)} service(s) from "
        f"Datadog APM services API (env={dd_env})"
    )
    return apm_names, "dd_apm_services"


def _fetch_software_catalog_service_names_from_api(
    dd_api_key: str,
    dd_app_key: str,
    dd_site: str,
    *,
    max_entities: int | None = None,
) -> list | None:
    """
    GET /api/v2/catalog/entity (filter[kind]=service, includeDiscovered=true), paginate.
    Requires Software Catalog read permission on the app key, or set SOFTWARE_CATALOG_USE_API=0.
    """
    # Opt-in: catalog API returns many entities; the default wall uses the ADT+GENERAL union (~90).
    if (os.getenv("SOFTWARE_CATALOG_USE_API") or "0").strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return None
    if max_entities is None:
        try:
            max_entities = int(os.getenv("SOFTWARE_CATALOG_MAX_ENTITIES", "150"))
        except (TypeError, ValueError):
            max_entities = 150
    max_entities = max(10, min(int(max_entities), 500))
    import requests

    base = f"{datadog_rest_api_base(dd_site)}/api/v2/catalog/entity"
    headers = {
        "DD-API-KEY": dd_api_key,
        "DD-APPLICATION-KEY": dd_app_key,
        "Accept": "application/json",
    }
    all_names: list = []
    offset = 0
    limit = 100
    while len(all_names) < max_entities and offset < max_entities * 2:
        params = {
            "page[offset]": offset,
            "page[limit]": limit,
            "filter[kind]": "service",
            "includeDiscovered": "true",
        }
        try:
            r = requests.get(base, headers=headers, params=params, timeout=(15, 60))
        except Exception as e:
            print(f"⚠️ Software catalog API request failed: {e}")
            return None
        if r.status_code == 403:
            print(
                "⚠️ Software catalog API 403 (needs catalog read) — use SOFTWARE_CATALOG_USE_API=0 or "
                "set SOFTWARE_CATALOG_SERVICE_LIST_FILE / SOFTWARE_CATALOG_SERVICE_NAMES"
            )
            return None
        if r.status_code != 200:
            print(f"⚠️ Software catalog API {r.status_code}: {(r.text or '')[:300]}")
            return None
        try:
            payload = r.json()
        except Exception:
            return None
        rows = payload.get("data") or []
        if not rows:
            break
        for item in rows:
            if not isinstance(item, dict):
                continue
            attr = item.get("attributes")
            if not isinstance(attr, dict):
                continue
            name = (attr.get("name") or "").strip()
            if not name:
                iid = str(item.get("id") or "")
                if iid and len(iid) < 512:
                    for token in re.findall(r"(?:[a-z0-9][a-z0-9._-]+)", iid, re.I):
                        token = re.sub(
                            r"^service[._]?",
                            "",
                            token,
                            flags=re.I,
                        )
                        if 2 < len(token) < 200 and re.match(
                            r"^[a-z0-9][a-z0-9._-]*$", token, re.I
                        ):
                            name = token
                            break
            if not name:
                continue
            all_names.append(name)
        if len(rows) < limit:
            break
        offset += limit
    if not all_names:
        return None
    return sorted(set(all_names), key=str.lower)


def _bundled_production_apm_127_path() -> str:
    """Path to committed list of 90 APM `service` names for env:production."""
    return os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "lists", "production_apm_127.txt")
    )


def _bundled_goldendev_apm_path() -> str:
    """Path to committed list of APM `service` names for env:goldendev (GoldenDev)."""
    return os.path.normpath(
        os.path.join(
            os.path.dirname(__file__), "..", "lists", "goldendev_apm_services.txt"
        )
    )


def _bundled_goldenqa_apm_path() -> str:
    """Path to committed list of APM `service` names for env:goldenqa (GoldenQA)."""
    return os.path.normpath(
        os.path.join(
            os.path.dirname(__file__), "..", "lists", "goldenqa_apm_services.txt"
        )
    )


def _bundled_adt_apm_path() -> str:
    """Path to committed list of APM `service` names for env:adt_prod (ADT)."""
    return os.path.normpath(
        os.path.join(
            os.path.dirname(__file__), "..", "lists", "adt_apm_services.txt"
        )
    )


def _bundled_cat_apm_path() -> str:
    """Path to committed list of APM `service` names for env:cat_prod (CAT)."""
    return os.path.normpath(
        os.path.join(
            os.path.dirname(__file__), "..", "lists", "cat_apm_services.txt"
        )
    )


def _bundled_comcast_apm_path() -> str:
    """Path to committed list of APM `service` names for env:comcast_prod (Comcast)."""
    return os.path.normpath(
        os.path.join(
            os.path.dirname(__file__), "..", "lists", "comcast_apm_services.txt"
        )
    )


def _bundled_qa_apm_path() -> str:
    """Path to committed list of APM `service` names for env:qa."""
    return os.path.normpath(
        os.path.join(
            os.path.dirname(__file__), "..", "lists", "qa_apm_services.txt"
        )
    )


def _bundled_samsung_apm_path() -> str:
    """Path to committed Samsung RED service names (APM env: SAMSUNG_DD_ENV or samsung_prod)."""
    return os.path.normpath(
        os.path.join(
            os.path.dirname(__file__), "..", "lists", "samsung_apm_services.txt"
        )
    )


def _filter_samsung_apm_wall_services(names: list) -> list:
    """
    Samsung Status Wall: only `*samsung*` service names (no production/ADT extras).
    Default SAMSUNG_WALL_PROD_ONLY=0 shows all six canonical tiers (prod, qa, dev).
    Set SAMSUNG_WALL_PROD_ONLY=1 to drop *-dev / *-qa on the samsung_prod wall.
    """
    raw_only = (os.getenv("SAMSUNG_WALL_PROD_ONLY") or "0").strip().lower()
    prod_only = raw_only in ("1", "true", "yes", "on")
    out: list = []
    for n in names or []:
        k = (n or "").strip()
        if not k:
            continue
        lk = k.lower()
        if "samsung" not in lk:
            continue
        if prod_only and ("-dev" in lk or "-qa" in lk):
            continue
        out.append(k)
    return sorted(set(out), key=str.lower)


# APM Software Catalog /apm-services: "all" = one block per environment below (order preserved).
# Golden (goldendev, goldenqa) is not included here — use dd_env=golden or ?tab=golden on /apm-services.
SOFTWARE_CATALOG_WALL_APM_ENVS: tuple[str, ...] = (
    "production",
    "samsung_prod",
    "adt_prod",
    "cat_prod",
    "comcast_prod",
)

# APM Golden tab only: goldendev then goldenqa.
SOFTWARE_CATALOG_WALL_GOLDEN_ENVS: tuple[str, ...] = (
    "goldendev",
    "goldenqa",
)


def _apm_wall_group_label(dd_env: str) -> str:
    """Short section title for each env block (no repeated 'APM Status Wall' prefix)."""
    return {
        "production": "Production",
        "samsung_prod": "Samsung",
        "adt_prod": "ADT",
        "cat_prod": "CAT",
        "comcast_prod": "Comcast",
        "qa": "QA",
        "goldendev": "Golden dev",
        "goldenqa": "Golden QA",
    }.get(dd_env, dd_env.replace("_", " ").title())


def normalize_software_catalog_wall_dd_env(raw: str | None) -> str:
    """
    APM / Software UI env: "all" (main envs only), "golden" (goldendev + goldenqa), or one concrete env.
    Aliases: gqa, adt, env-qa, samsung. Other values fall back to production.
    """
    s = (raw or "").strip().lower()
    if s in (
        "all",
        "*",
        "todos",
        "todos_los",
        "todos_los_env",
        "todos_los_envs",
        "all_envs",
        "all_env",
        "every",
        "todo",
    ):
        return "all"
    if not s:
        return "all"
    if s in ("prod", "production"):
        return "production"
    if s in ("gdev", "goldendev", "golden-dev", "golden_dev"):
        return "goldendev"
    if s in ("goldenqa", "gqa", "golden-qa", "golden_qa"):
        return "goldenqa"
    if s in ("adt", "adt_prod", "adt-prod", "adtprod", "partner-prod", "adt_partner"):
        return "adt_prod"
    if s in ("qa", "env-qa", "env_qa"):
        return "qa"
    if s in ("samsung", "samsung_prod", "samsung-prod", "samsungprod"):
        return "samsung_prod"
    if s in ("cat", "cat_prod", "cat-prod", "catprod"):
        return "cat_prod"
    if s in ("comcast", "comcast_prod", "comcast-prod", "comcastprod"):
        return "comcast_prod"
    if s in ("golden", "golden_tab", "golden-envs", "golden_envs"):
        return "golden"
    return "production"


def resolve_software_catalog_wall_service_names(
    dd_env: str = "production",
) -> tuple[list, str]:
    """
    Service list for the APM Status Wall, evaluated against APM with the given
    `env` tag (production, goldendev, goldenqa, adt_prod, qa, or samsung_prod in the UI/API).
    Precedence: SOFTWARE_CATALOG_SERVICE_NAMES → SOFTWARE_CATALOG_SERVICE_LIST_FILE
    → bundled lists (…production_apm_127…, goldendev_*, goldenqa_*, adt_*, qa_*, samsung_*);
    disable with USE_BUNDLED_127, USE_BUNDLED_GOLDENDEV, USE_BUNDLED_GOLDENQA, USE_BUNDLED_ADT,
    USE_BUNDLED_QA, USE_BUNDLED_SAMSUNG=0
    → optional catalog API → ADT+GENERAL union.
    """
    _dd_env = normalize_software_catalog_wall_dd_env(dd_env)
    if _dd_env == "all":
        return (
            [],
            "invalid: use a concrete env, not 'all' (all is handled by the APM wall aggregator)",
        )
    if _dd_env == "golden":
        return (
            [],
            "invalid: use goldendev/goldenqa or the golden tab aggregator (dd_env=golden)",
        )

    raw = (os.getenv("SOFTWARE_CATALOG_SERVICE_NAMES") or "").strip()
    if raw:
        names = sorted(
            {x.strip() for x in raw.split(",") if x.strip()}, key=str.lower
        )
        if names:
            print(
                f"🧭 Software catalog wall (env={_dd_env}): {len(names)} service(s) from "
                f"SOFTWARE_CATALOG_SERVICE_NAMES"
            )
            return names, "env_csv"

    path = (os.getenv("SOFTWARE_CATALOG_SERVICE_LIST_FILE") or "").strip()
    if path and os.path.isfile(path):
        file_names: list = []
        try:
            with open(path, encoding="utf-8") as f:
                file_names = [
                    ln.strip()
                    for ln in f
                    if ln.strip() and not ln.lstrip().startswith("#")
                ]
        except OSError as e:
            print(f"⚠️ SOFTWARE_CATALOG_SERVICE_LIST_FILE: {e}")
        else:
            if file_names:
                print(
                    f"🧭 Software catalog wall (env={_dd_env}): {len(file_names)} service(s) from file {path!r}"
                )
                return sorted(set(file_names), key=str.lower), "file"

    dd_api = os.getenv("DATADOG_API_KEY")
    dd_app = os.getenv("DATADOG_APP_KEY")
    dd_site = os.getenv("DATADOG_SITE", "arlo.datadoghq.com")
    if dd_api and dd_app and _software_catalog_wall_use_dd_apm_list(_dd_env):
        dd_apm = _resolve_software_catalog_wall_from_dd_apm(
            _dd_env, dd_api, dd_app, dd_site
        )
        if dd_apm:
            return dd_apm

    if _dd_env == "production" and (os.getenv("SOFTWARE_CATALOG_USE_BUNDLED_127", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )):
        bundled = _bundled_production_apm_127_path()
        if os.path.isfile(bundled):
            bnames: list = []
            try:
                with open(bundled, encoding="utf-8") as f:
                    bnames = [
                        ln.strip()
                        for ln in f
                        if ln.strip() and not ln.lstrip().startswith("#")
                    ]
            except OSError as e:
                print(f"⚠️ APM wall bundled list {bundled!r}: {e}")
            else:
                if bnames:
                    try:
                        from tools.apm_engineering_groups import (
                            apm_engineering_groups_enabled,
                            merge_bundled_names_with_org_catalog,
                        )

                        if apm_engineering_groups_enabled():
                            merged = merge_bundled_names_with_org_catalog(bnames)
                            print(
                                f"🧭 Software catalog wall: {len(merged)} service(s) "
                                f"(org wall catalog + production extras, env=production)"
                            )
                            return merged, "bundled_127_org_wall"
                    except Exception:
                        pass
                    deduped_p: list = []
                    seen_p: set = set()
                    for n in bnames:
                        k = (n or "").strip().lower()
                        if k and k not in seen_p:
                            seen_p.add(k)
                            deduped_p.append(n.strip())
                    print(
                        f"🧭 Software catalog wall: {len(deduped_p)} service(s) from "
                        f"bundled production_apm_127.txt (env=production)"
                    )
                    return deduped_p, "bundled_127"

    if _dd_env == "goldendev" and (os.getenv("SOFTWARE_CATALOG_USE_BUNDLED_GOLDENDEV", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )):
        gb = _bundled_goldendev_apm_path()
        if os.path.isfile(gb):
            gnames: list = []
            try:
                with open(gb, encoding="utf-8") as f:
                    gnames = [
                        ln.strip()
                        for ln in f
                        if ln.strip() and not ln.lstrip().startswith("#")
                    ]
            except OSError as e:
                print(f"⚠️ APM wall bundled list {gb!r}: {e}")
            else:
                if gnames:
                    print(
                        f"🧭 Software catalog wall: {len(gnames)} service(s) from "
                        f"bundled goldendev_apm_services.txt (env=goldendev)"
                    )
                    return sorted(set(gnames), key=str.lower), "bundled_goldendev"

    if _dd_env == "goldenqa" and (os.getenv("SOFTWARE_CATALOG_USE_BUNDLED_GOLDENQA", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )):
        gq = _bundled_goldenqa_apm_path()
        if os.path.isfile(gq):
            gqnames: list = []
            try:
                with open(gq, encoding="utf-8") as f:
                    gqnames = [
                        ln.strip()
                        for ln in f
                        if ln.strip() and not ln.lstrip().startswith("#")
                    ]
            except OSError as e:
                print(f"⚠️ APM wall bundled list {gq!r}: {e}")
            else:
                if gqnames:
                    print(
                        f"🧭 Software catalog wall: {len(gqnames)} service(s) from "
                        f"bundled goldenqa_apm_services.txt (env=goldenqa)"
                    )
                    return sorted(set(gqnames), key=str.lower), "bundled_goldenqa"

    if _dd_env == "adt_prod" and (os.getenv("SOFTWARE_CATALOG_USE_BUNDLED_ADT", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )):
        adt_p = _bundled_adt_apm_path()
        if os.path.isfile(adt_p):
            anames: list = []
            try:
                with open(adt_p, encoding="utf-8") as f:
                    anames = [
                        ln.strip()
                        for ln in f
                        if ln.strip() and not ln.lstrip().startswith("#")
                    ]
            except OSError as e:
                print(f"⚠️ APM wall bundled list {adt_p!r}: {e}")
            else:
                if anames:
                    print(
                        f"🧭 Software catalog wall: {len(anames)} service(s) from "
                        f"bundled adt_apm_services.txt (env=adt_prod)"
                    )
                    try:
                        from tools.apm_engineering_groups import (
                            apm_engineering_groups_enabled,
                            merge_bundled_names_with_org_catalog,
                        )

                        if apm_engineering_groups_enabled():
                            merged = merge_bundled_names_with_org_catalog(anames)
                            print(
                                f"🧭 Software catalog wall: {len(merged)} service(s) "
                                f"(org wall catalog + adt extras, env=adt_prod)"
                            )
                            return merged, "bundled_adt_org_wall"
                    except Exception:
                        pass
                    deduped: list = []
                    seen_adt: set = set()
                    for n in anames:
                        k = (n or "").strip().lower()
                        if k and k not in seen_adt:
                            seen_adt.add(k)
                            deduped.append(n.strip())
                    return deduped, "bundled_adt"

    if _dd_env == "cat_prod" and (os.getenv("SOFTWARE_CATALOG_USE_BUNDLED_CAT", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )):
        cat_p = _bundled_cat_apm_path()
        if os.path.isfile(cat_p):
            cnames: list = []
            try:
                with open(cat_p, encoding="utf-8") as f:
                    cnames = [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
            except OSError as e:
                print(f"⚠️ APM wall bundled list {cat_p!r}: {e}")
            else:
                if cnames:
                    print(f"🧭 Software catalog wall: {len(cnames)} service(s) from bundled cat_apm_services.txt")
                    return sorted(set(cnames), key=str.lower), "bundled_cat"

    if _dd_env == "comcast_prod" and (os.getenv("SOFTWARE_CATALOG_USE_BUNDLED_COMCAST", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )):
        comcast_p = _bundled_comcast_apm_path()
        if os.path.isfile(comcast_p):
            xnames: list = []
            try:
                with open(comcast_p, encoding="utf-8") as f:
                    xnames = [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
            except OSError as e:
                print(f"⚠️ APM wall bundled list {comcast_p!r}: {e}")
            else:
                if xnames:
                    print(f"🧭 Software catalog wall: {len(xnames)} service(s) from bundled comcast_apm_services.txt")
                    return sorted(set(xnames), key=str.lower), "bundled_comcast"

    if _dd_env == "qa" and (os.getenv("SOFTWARE_CATALOG_USE_BUNDLED_QA", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )):
        qpath = _bundled_qa_apm_path()
        if os.path.isfile(qpath):
            qnames: list = []
            try:
                with open(qpath, encoding="utf-8") as f:
                    qnames = [
                        ln.strip()
                        for ln in f
                        if ln.strip() and not ln.lstrip().startswith("#")
                    ]
            except OSError as e:
                print(f"⚠️ APM wall bundled list {qpath!r}: {e}")
            else:
                if qnames:
                    print(
                        f"🧭 Software catalog wall: {len(qnames)} service(s) from "
                        f"bundled qa_apm_services.txt (env=qa)"
                    )
                    return sorted(set(qnames), key=str.lower), "bundled_qa"

    if _dd_env == "samsung_prod" and (os.getenv("SOFTWARE_CATALOG_USE_BUNDLED_SAMSUNG", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )):
        s_path = _bundled_samsung_apm_path()
        if os.path.isfile(s_path):
            snames: list = []
            try:
                with open(s_path, encoding="utf-8") as f:
                    snames = [
                        ln.strip()
                        for ln in f
                        if ln.strip() and not ln.lstrip().startswith("#")
                    ]
            except OSError as e:
                print(f"⚠️ APM wall bundled list {s_path!r}: {e}")
            else:
                if snames:
                    filtered = _filter_samsung_apm_wall_services(snames)
                    if filtered:
                        print(
                            f"🧭 Software catalog wall: {len(filtered)} Samsung service(s) "
                            f"(from samsung_apm_services.txt, env=samsung_prod)"
                        )
                        return filtered, "bundled_samsung"

    if _dd_env == "samsung_prod":
        samsung_only = _filter_samsung_apm_wall_services(list(SAMSUNG_MONITOR_SERVICES))
        if samsung_only:
            print(
                f"🧭 Software catalog wall: {len(samsung_only)} Samsung service(s) "
                f"(built-in list, env=samsung_prod)"
            )
            return samsung_only, "samsung_builtin"
        print("🧭 Software catalog wall: no Samsung services resolved")
        return [], "samsung_empty"

    if dd_api and dd_app:
        api_list = _fetch_software_catalog_service_names_from_api(
            dd_api, dd_app, dd_site
        )
        if api_list:
            if (os.getenv("SOFTWARE_CATALOG_API_INTERSECT_FALLBACK") or "").strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            ):
                inter = sorted(
                    set(api_list) & set(_software_catalog_fallback_service_names()),
                    key=str.lower,
                )
                if inter:
                    print(
                        f"🧭 Software catalog wall (env={_dd_env}): {len(inter)} service(s) (API ∩ "
                        f"ADT+GENERAL fallback) — {len(api_list)} from API before intersect"
                    )
                    return inter, "catalog_api_intersect_fallback"
            print(
                f"🧭 Software catalog wall (env={_dd_env}): {len(api_list)} service(s) from "
                f"Datadog /api/v2/catalog/entity"
            )
            return api_list, "catalog_api"

    fallback = _software_catalog_fallback_service_names()
    if _dd_env == "goldendev":
        print(
            f"🧭 Software catalog wall: {len(fallback)} service(s) from built-in "
            f"ADT+GENERAL union (fallback) — rellena o habilita lists/goldendev_apm_services.txt"
        )
    elif _dd_env == "goldenqa":
        print(
            f"🧭 Software catalog wall: {len(fallback)} service(s) from built-in "
            f"ADT+GENERAL union (fallback) — rellena o habilita lists/goldenqa_apm_services.txt"
        )
    elif _dd_env == "adt_prod":
        print(
            f"🧭 Software catalog wall: {len(fallback)} service(s) from built-in "
            f"ADT+GENERAL union (fallback) — rellena o habilita lists/adt_apm_services.txt"
        )
    elif _dd_env == "cat_prod":
        print(
            f"🧭 Software catalog wall: {len(fallback)} service(s) from built-in "
            f"ADT+GENERAL union (fallback) — rellena o habilita lists/cat_apm_services.txt"
        )
    elif _dd_env == "comcast_prod":
        print(
            f"🧭 Software catalog wall: {len(fallback)} service(s) from built-in "
            f"ADT+GENERAL union (fallback) — rellena o habilita lists/comcast_apm_services.txt"
        )
    elif _dd_env == "qa":
        print(
            f"🧭 Software catalog wall: {len(fallback)} service(s) from built-in "
            f"ADT+GENERAL union (fallback) — rellena o habilita lists/qa_apm_services.txt"
        )
    elif _dd_env == "samsung_prod":
        samsung_fb = _filter_samsung_apm_wall_services(list(SAMSUNG_MONITOR_SERVICES))
        if samsung_fb:
            print(
                f"🧭 Software catalog wall: {len(samsung_fb)} Samsung service(s) "
                f"(built-in SAMSUNG_MONITOR_SERVICES fallback)"
            )
            return samsung_fb, "samsung_builtin"
        print("🧭 Software catalog wall: no Samsung services resolved")
        return [], "samsung_empty"
    else:
        print(
            f"🧭 Software catalog wall: {len(fallback)} service(s) from built-in "
            f"ADT+GENERAL union (fallback)"
        )
    return fallback, "fallback_union"


def _software_catalog_wall_payload_for_single_env(
    dde: str,
    timerange: int,
    force_refresh: bool,
    pre_pd: tuple[dict, list] | None = None,
) -> dict:
    """
    One APM software-catalog wall response (one `groups` item). `dde` is never "all".
    If `pre_pd` is (PagerDuty counts dict, incidents list), reuses that fetch (for all-env build).
    """
    dd_api_key = os.getenv("DATADOG_API_KEY")
    dd_app_key = os.getenv("DATADOG_APP_KEY")
    dd_site = os.getenv("DATADOG_SITE", "arlo.datadoghq.com")
    if not dd_api_key or not dd_app_key:
        return {
            "success": False,
            "error": "Datadog API keys not configured",
            "timerange": timerange,
            "dd_env": dde,
            "groups": [],
        }

    services, _source = resolve_software_catalog_wall_service_names(dde)
    if not services:
        return {
            "success": False,
            "error": f"No services resolved for APM software catalog wall (env={dde})",
            "timerange": timerange,
            "dd_env": dde,
            "groups": [],
        }

    current_time = int(time.time())
    from_time = current_time - (timerange * 3600)
    environments = [_sm_wall_dd_env_to_dd_tag(dde)]
    pd_slug = _sm_wall_dde_to_page_slug(dde)
    wall_mode = pd_slug

    pd_api_key = os.getenv("PAGERDUTY_API_TOKEN")
    if pre_pd is not None:
        _pd_c, pd_incidents = pre_pd
    else:
        _pd_c = {"triggered": 0, "acknowledged": 0, "resolved": 0}
        pd_incidents: list = []
        if pd_api_key:
            try:
                _pd_c, pd_incidents = get_pagerduty_status_counts(
                    pd_api_key, force_refresh
                )
            except Exception as e:
                print(f"⚠️ Software catalog wall: PagerDuty fetch failed: {e}")

    all_statuses = _sm_fetch_parallel_service_health(
        services,
        environments,
        dd_api_key,
        dd_app_key,
        dd_site,
        from_time,
        current_time,
        int(timerange),
        force_refresh,
    )
    _sm_apply_pagerduty_correlation(
        all_statuses,
        services,
        environments,
        pd_slug,
        pd_incidents,
        silent=False,
    )
    n_inactive = sum(1 for s in all_statuses if s.get("status") == "inactive")
    n_unknown = sum(1 for s in all_statuses if s.get("status") == "unknown")
    statuses, wall_meta = _apm_wall_finalize_statuses(
        all_statuses,
        services,
        dde,
        environments[0] if environments else dde,
        dd_api_key=dd_api_key,
        dd_app_key=dd_app_key,
        dd_site=dd_site,
    )
    n_dropped_other = int(wall_meta.get("dropped_other") or 0)
    owner_by_service = wall_meta.get("owner_by_service")
    if _apm_status_wall_attach_eks(dde):
        eks_wall_cache: dict = {}
        _attach_eks_clusters_wall(statuses, timerange, eks_wall_cache, force_refresh)

    h = sum(1 for s in statuses if s.get("status") == "healthy")
    w = sum(1 for s in statuses if s.get("status") == "warning")
    c = sum(1 for s in statuses if s.get("status") == "critical")
    unk = 0
    inn = 0
    if c > 0:
        overall = "critical"
    elif w > 0:
        overall = "warning"
    else:
        overall = "healthy"

    dd_site_ser = os.getenv("DD_SITE", "datadoghq.com")
    ser = [_wall_serialize_status(s, dd_site_ser, wall_mode, timerange) for s in statuses]
    if dde == "samsung_prod":
        region_columns = [
            {
                "key": "samsung",
                "label": "Samsung",
                "subtitle": "",
                "services": list(ser),
            },
        ]
    else:
        ag, ar_eu = _wall_split_services_by_region(ser)
        region_columns = [
            {
                "key": "arlo_global",
                "label": "Arlo Global",
                "subtitle": "Oregon",
                "services": ag,
            },
            {
                "key": "arlo_eu",
                "label": "Arlo EU",
                "subtitle": "Ireland",
                "services": ar_eu,
            },
        ]
        region_columns = _wall_filter_nonempty_region_columns(region_columns)
    monitors = _wall_apm_monitors_for_dd_env(dde, _pd_c, pd_api_key, timerange, force_refresh)
    _wall_label = _apm_wall_group_label(dde)
    _wall_slug = (
        f"software-catalog-{dde}" if dde != "production" else "software-catalog-production"
    )
    eng_sections: list = []
    eng_column_layout: list = []
    wall_group_by_team = False
    try:
        from tools.apm_engineering_groups import (
            apm_engineering_groups_enabled,
            apm_status_wall_use_dd_team,
            build_engineering_sections,
            engineering_column_layout,
            engineering_wall_uses_org_catalog,
            fetch_datadog_catalog_service_owners,
        )

        if apm_engineering_groups_enabled() and engineering_wall_uses_org_catalog(dde):
            if owner_by_service is None and apm_status_wall_use_dd_team(dde) and dd_api_key and dd_app_key:
                owner_by_service = fetch_datadog_catalog_service_owners(
                    dd_api_key, dd_app_key, dd_site
                )
            if apm_status_wall_use_dd_team(dde) and owner_by_service:
                wall_group_by_team = True
            eng_sections = build_engineering_sections(
                ser,
                dd_env=dde,
                owner_by_service=owner_by_service,
            )
            eng_column_layout = engineering_column_layout(dde)
    except Exception as e:
        print(f"⚠️ Engineering group layout skipped: {e}")
    return {
        "success": True,
        "timerange": timerange,
        "dd_env": dde,
        "monitors": monitors,
        "source": {
            "kind": "apm_status_wall",
            "service_name_source": _source,
            "services_in_scope": len(services),
            "tiles_shown": len(statuses),
            "dropped_inactive": n_inactive,
            "dropped_unknown": n_unknown,
            "dropped_other": n_dropped_other,
            "apm_environment": dde,
            "header_light": _apm_status_wall_header_light(),
            "eks_hints": _apm_status_wall_attach_eks(dde),
            "group_by": "team" if wall_group_by_team else None,
        },
        "groups": [
            {
                "slug": _wall_slug,
                "label": _wall_label,
                "mode": dde,
                "overall": overall,
                "counts": {
                    "healthy": h,
                    "warning": w,
                    "critical": c,
                    "unknown": unk,
                    "inactive": inn,
                    "total": len(statuses),
                },
                "services": ser,
                "region_columns": region_columns,
                "engineering_sections": eng_sections,
                "engineering_column_layout": eng_column_layout,
            }
        ],
    }


def _status_monitor_software_catalog_wall_data_all_envs(
    timerange: int, force_refresh: bool
) -> dict:
    """
    APM /apm-services: one `groups` section per env in SOFTWARE_CATALOG_WALL_APM_ENVS.
    PagerDuty is fetched once and reused.
    """
    pre_pd: tuple[dict, list] | None = None
    pd_api_key = os.getenv("PAGERDUTY_API_TOKEN")
    if pd_api_key:
        try:
            pre_pd = get_pagerduty_status_counts(pd_api_key, force_refresh)
        except Exception as e:
            print(f"⚠️ Software catalog wall (all): PagerDuty fetch failed: {e}")

    groups: list = []
    per_env_sources: list = []
    tot_in = 0
    tot_ti = 0
    tot_di = 0
    tot_du = 0
    monitors: dict = {}

    with ThreadPoolExecutor(
        max_workers=max(1, min(6, len(SOFTWARE_CATALOG_WALL_APM_ENVS)))
    ) as ex:
        results: list[dict] = list(
            ex.map(
                lambda d: _software_catalog_wall_payload_for_single_env(
                    d, timerange, force_refresh, pre_pd=pre_pd
                ),
                list(SOFTWARE_CATALOG_WALL_APM_ENVS),
            )
        )
    for i, dde in enumerate(SOFTWARE_CATALOG_WALL_APM_ENVS):
        part = results[i]
        if not part.get("success"):
            print(
                f"⚠️ APM wall (all envs): {dde} — {part.get('error', 'no group')}, skipping"
            )
            continue
        if not monitors and part.get("monitors"):
            monitors = part["monitors"]
        g = part.get("groups") or []
        if g:
            groups.append(g[0])
        s = part.get("source") or {}
        per_env_sources.append(
            {
                "apm_environment": dde,
                "service_name_source": s.get("service_name_source"),
                "services_in_scope": s.get("services_in_scope", 0),
                "tiles_shown": s.get("tiles_shown", 0),
                "dropped_inactive": s.get("dropped_inactive", 0),
                "dropped_unknown": s.get("dropped_unknown", 0),
            }
        )
        tot_in += int(s.get("services_in_scope") or 0)
        tot_ti += int(s.get("tiles_shown") or 0)
        tot_di += int(s.get("dropped_inactive") or 0)
        tot_du += int(s.get("dropped_unknown") or 0)

    if not groups:
        return {
            "success": False,
            "error": "No APM data for any environment (check bundled lists, Datadog keys, or pick one env in the menu).",
            "timerange": timerange,
            "dd_env": "all",
            "groups": [],
        }

    return {
        "success": True,
        "timerange": timerange,
        "dd_env": "all",
        "monitors": monitors,
        "source": {
            "kind": "apm_status_wall_all",
            "aggregated": True,
            "environments": [g.get("mode") for g in groups if isinstance(g, dict)],
            "services_in_scope": tot_in,
            "tiles_shown": tot_ti,
            "dropped_inactive": tot_di,
            "dropped_unknown": tot_du,
            "per_environment": per_env_sources,
            "header_light": _apm_status_wall_header_light(),
            "eks_hints": _apm_status_wall_attach_eks(),
        },
        "groups": groups,
    }


def _status_monitor_software_catalog_wall_data_golden_envs(
    timerange: int, force_refresh: bool
) -> dict:
    """
    APM /apm-services Golden tab: goldendev then goldenqa only.
    """
    pre_pd: tuple[dict, list] | None = None
    pd_api_key = os.getenv("PAGERDUTY_API_TOKEN")
    if pd_api_key:
        try:
            pre_pd = get_pagerduty_status_counts(pd_api_key, force_refresh)
        except Exception as e:
            print(f"⚠️ Software catalog wall (golden): PagerDuty fetch failed: {e}")

    groups: list = []
    per_env_sources: list = []
    tot_in = 0
    tot_ti = 0
    tot_di = 0
    tot_du = 0
    monitors: dict = {}

    golden_envs = SOFTWARE_CATALOG_WALL_GOLDEN_ENVS
    with ThreadPoolExecutor(
        max_workers=max(1, min(4, len(golden_envs)))
    ) as ex:
        results: list[dict] = list(
            ex.map(
                lambda d: _software_catalog_wall_payload_for_single_env(
                    d, timerange, force_refresh, pre_pd=pre_pd
                ),
                list(golden_envs),
            )
        )
    for i, dde in enumerate(golden_envs):
        part = results[i]
        if not part.get("success"):
            print(
                f"⚠️ APM wall (golden): {dde} — {part.get('error', 'no group')}, skipping"
            )
            continue
        if not monitors and part.get("monitors"):
            monitors = part["monitors"]
        g = part.get("groups") or []
        if g:
            groups.append(g[0])
        s = part.get("source") or {}
        per_env_sources.append(
            {
                "apm_environment": dde,
                "service_name_source": s.get("service_name_source"),
                "services_in_scope": s.get("services_in_scope", 0),
                "tiles_shown": s.get("tiles_shown", 0),
                "dropped_inactive": s.get("dropped_inactive", 0),
                "dropped_unknown": s.get("dropped_unknown", 0),
            }
        )
        tot_in += int(s.get("services_in_scope") or 0)
        tot_ti += int(s.get("tiles_shown") or 0)
        tot_di += int(s.get("dropped_inactive") or 0)
        tot_du += int(s.get("dropped_unknown") or 0)

    if not groups:
        return {
            "success": False,
            "error": "No APM data for Golden environments (check bundled lists and Datadog keys).",
            "timerange": timerange,
            "dd_env": "golden",
            "groups": [],
        }

    return {
        "success": True,
        "timerange": timerange,
        "dd_env": "golden",
        "monitors": monitors,
        "source": {
            "kind": "apm_status_wall_golden",
            "aggregated": True,
            "environments": [g.get("mode") for g in groups if isinstance(g, dict)],
            "services_in_scope": tot_in,
            "tiles_shown": tot_ti,
            "dropped_inactive": tot_di,
            "dropped_unknown": tot_du,
            "per_environment": per_env_sources,
            "header_light": _apm_status_wall_header_light(),
            "eks_hints": _apm_status_wall_attach_eks(),
        },
        "groups": groups,
    }


def status_monitor_software_catalog_wall_data(
    timerange: int = 1, force_refresh: bool = False, dd_env: str = "all"
) -> dict:
    """
    APM /apm-services: default `all` = one `groups` section per env; or a single `dd_env`
    (production, goldendev, …) for a focused list only.
    Same APM+PD+health rules. Inactive/unknown omitted.
    """
    global _software_catalog_wall_cache
    dde = normalize_software_catalog_wall_dd_env(dd_env)
    cache_version = "sc_wall_v50_adt_splunk_light"
    apm_bucket = _apm_status_wall_cache_bucket_secs()
    cache_key = f"{cache_version}_{dde}_{timerange}_{int(time.time() // apm_bucket)}"
    hit = _read_sm_mem_cache(_software_catalog_wall_cache, cache_key, force_refresh)
    if hit is not None:
        return dict(hit)

    if dde == "all":
        out = _status_monitor_software_catalog_wall_data_all_envs(
            timerange, force_refresh
        )
    elif dde == "golden":
        out = _status_monitor_software_catalog_wall_data_golden_envs(
            timerange, force_refresh
        )
    else:
        out = _software_catalog_wall_payload_for_single_env(
            dde, timerange, force_refresh, pre_pd=None
        )
    if out.get("success") is not False and isinstance(out, dict) and "groups" in out:
        _write_sm_mem_cache(_software_catalog_wall_cache, cache_key, out)
    return dict(out)


def status_monitor_hub_summary(timerange: int = 1, force_refresh: bool = False) -> dict:
    """
    JSON summary for the /statusmonitor hub: one card per environment.

    Production, Samsung, and ADT cards reuse the APM Status Wall pipeline so overall
    colors match /apm-services. Other environments use the legacy hub resolver.
    """
    global _hub_summary_cache
    cache_version = "hub_v22_issue_services"
    cache_key = f"{cache_version}_{timerange}_{int(time.time() // _cache_ttl)}"
    hit = _read_sm_mem_cache(_hub_summary_cache, cache_key, force_refresh)
    if hit is not None:
        return dict(hit)

    pre_pd: tuple[dict, list] | None = None
    pd_api_key = os.getenv("PAGERDUTY_API_TOKEN")
    if pd_api_key:
        try:
            pre_pd = get_pagerduty_status_counts(pd_api_key, force_refresh)
        except Exception as e:
            print(f"⚠️ Hub summary: PagerDuty fetch failed: {e}")

    wall_by_slug: dict[str, dict] = {}
    wall_rows = [r for r in HUB_ENV_ROWS if r["slug"] in HUB_WALL_ALIGNED_SLUGS]
    if wall_rows:
        with ThreadPoolExecutor(max_workers=max(1, len(wall_rows))) as ex:
            futs = {
                row["slug"]: ex.submit(
                    _software_catalog_wall_payload_for_single_env,
                    HUB_SLUG_TO_WALL_DD_ENV[row["slug"]],
                    timerange,
                    force_refresh,
                    pre_pd,
                )
                for row in wall_rows
            }
            for slug, fut in futs.items():
                try:
                    wall_by_slug[slug] = fut.result()
                except Exception as e:
                    print(f"❌ Hub wall-aligned fetch error for {slug}: {e}")
                    wall_by_slug[slug] = {"success": False, "error": str(e)}

    statuses_by_mode = _hub_collect_statuses_by_mode(timerange, "Hub summary", force_refresh)

    env_payload = []
    for row in HUB_ENV_ROWS:
        if row["slug"] in HUB_WALL_ALIGNED_SLUGS:
            wall_payload = wall_by_slug.get(row["slug"]) or {}
            if wall_payload.get("success") is not False and wall_payload.get("groups"):
                env_payload.append(_hub_entry_from_wall_payload(row, wall_payload))
                continue
        statuses = statuses_by_mode.get(row["mode"], [])
        if row["slug"] == "samsung":
            _bl = _sm_bundled_status_monitor_service_list("samsung")
            canon = set(_bl) if _bl is not None else set(SAMSUNG_MONITOR_SERVICES)
            statuses_for_card = [s for s in statuses if s.get("service") in canon]
        else:
            statuses_for_card = statuses
        env_payload.append(_hub_build_entry_from_legacy_statuses(row, statuses_for_card))

    order = {row["slug"]: i for i, row in enumerate(HUB_ENV_ROWS)}
    env_payload.sort(key=lambda r: order.get(r["slug"], 99))

    out = {"success": True, "timerange": timerange, "environments": env_payload}
    _write_sm_mem_cache(_hub_summary_cache, cache_key, out)
    return dict(out)


def _splunk_p0_zone_hover_title(label: str, zones_list) -> str:
    """Multiline tooltip: label + z1–z4 outlier counts for one P0 tool."""
    zmap = {z.get("zone"): z for z in (zones_list or []) if z and z.get("zone")}
    lines = [f"{label} — outliers by zone (LLP predict):"]
    for zn in ("z1", "z2", "z3", "z4"):
        z = zmap.get(zn) or {}
        o = int(z.get("outliers") or 0)
        err = z.get("error")
        if err:
            lines.append(f"  {zn.upper()}: data unavailable")
        else:
            lines.append(f"  {zn.upper()}: {o} outlier(s)")
    return "\n".join(lines)


def _splunk_p0_semaphore_light_html(
    spl_by_id: dict,
    tid: str,
    short_label: str,
    tooltip_label: str,
    default_url: str,
    *,
    link_color: str = "#fff",
    home_style: bool = False,
) -> str:
    """Una luz compacta + etiqueta corta + Σ; hover = zonas z1–z4."""
    row = spl_by_id.get(tid) or {}
    url = (row.get("dashboard_url") or "").strip() or default_url
    url_e = html.escape(url, quote=True)
    tot = int(row.get("total_outliers") or 0)
    zones = row.get("zones") or []
    title = _splunk_p0_zone_hover_title(tooltip_label, zones)
    title_e = html.escape(title, quote=True)
    dot_bg = "#22c55e" if tot == 0 else "#ef4444"
    if home_style:
        dot_sh = "rgba(34,197,94,0.45)" if tot == 0 else "rgba(239,68,68,0.45)"
        link_color_e = "inherit"
        dot_px = "11px"
        label_fs = "8px"
        num_fs = "10px"
        min_w = "38px"
        max_w = ""
    else:
        dot_sh = "rgba(34,197,94,0.55)" if tot == 0 else "rgba(239,68,68,0.55)"
        link_color_e = html.escape(link_color, quote=True)
        dot_px = "12px"
        label_fs = "9px"
        num_fs = "11px"
        min_w = "40px"
        max_w = "max-width:72px;"
    return (
        f'<a href="{url_e}" target="_blank" rel="noopener" title="{title_e}" '
        f'style="display:inline-flex;flex-direction:column;align-items:center;gap:2px;'
        f'text-decoration:none;color:{link_color_e};min-width:{min_w};{max_w}">'
        f'<span style="width:{dot_px};height:{dot_px};border-radius:50%;background:{dot_bg};'
        f"box-shadow:0 0 6px {dot_sh};flex-shrink:0;\"></span>"
        f'<span style="font-size:{label_fs};font-weight:800;opacity:0.9;line-height:1;">'
        f"{html.escape(short_label)}</span>"
        f'<span style="font-size:{num_fs};font-weight:900;">{tot}</span>'
        f"</a>"
    )


def _splunk_p0_semaphore_bar_html(
    spl_by_id: dict,
    *,
    link_color: str = "#fff",
    home_style: bool = False,
) -> str:
    """Horizontal traffic-light row (Str · CVR · ADT · US); compact height."""
    items = [
        (
            "p0_streaming",
            "Str",
            "Streaming",
            "https://arlo.splunkcloud.com/en-US/app/arlo_sre/p0_streaming_dashboard",
        ),
        ("p0_cvr", "CVR", "CVR", "https://arlo.splunkcloud.com/en-US/app/arlo_sre/p0_cvr_dashboard"),
        (
            "p0_adt",
            "ADT",
            "ADT",
            "https://arlo.splunkcloud.com/en-US/app/search/p0_streaming_dashboard_pp",
        ),
        (
            "p0_streaming_us_infra",
            "US",
            "US infra",
            "https://arlo.splunkcloud.com/en-US/app/arlo_sre/p0_streaming_dashboard__us_infra",
        ),
    ]
    parts = [
        _splunk_p0_semaphore_light_html(
            spl_by_id, tid, short, tip, u, link_color=link_color, home_style=home_style
        )
        for tid, short, tip, u in items
    ]
    if home_style:
        sep = '<span style="opacity:0.35;font-size:10px;padding:0 2px;">|</span>'
        gap = "4px 8px"
        pad = "4px 2px"
    else:
        sep = '<span style="opacity:0.45;font-size:11px;font-weight:700;padding:0 1px;">|</span>'
        gap = "6px 10px"
        pad = "2px 0"
    inner = sep.join(parts)
    return (
        f'<div class="spl-p0-sem" style="display:flex;flex-wrap:wrap;align-items:flex-end;'
        f'justify-content:center;gap:{gap};padding:{pad};">{inner}</div>'
    )


_SPLUNK_P0_TOOL_IDS = ("p0_streaming", "p0_cvr", "p0_adt", "p0_streaming_us_infra")


def _splunk_p0_grand_total(spl_by_id: dict) -> int:
    return sum(int((spl_by_id.get(tid) or {}).get("total_outliers") or 0) for tid in _SPLUNK_P0_TOOL_IDS)


def _splunk_p0_last_updated_label(tz_name: str | None = None) -> str:
    """Same footer as home Splunk P0: Last updated: 11:38:14 AM."""
    tz_raw = (tz_name or "America/Los_Angeles").strip() or "America/Los_Angeles"
    try:
        tz_obj = ZoneInfo(tz_raw)
    except Exception:
        tz_obj = ZoneInfo("America/Los_Angeles")
    return datetime.now(tz_obj).strftime("%I:%M:%S %p")


def _splunk_p0_sidebar_widget_html(
    spl_data: dict,
    *,
    title: str = "📊 Splunk P0",
) -> str:
    """
    Home Splunk P0 card (index.html): Σ total bar, Str·CVR·ADT·US row, Last updated.
    Uses splunk_outliers_monitor_payload() — same logic as /api/splunk/monitor.
    """
    title_e = html.escape(title)
    spl_ok = bool(spl_data.get("success"))
    spl_tools_list = spl_data.get("tools") if spl_ok else []
    spl_by_id = {t.get("id"): t for t in (spl_tools_list or [])}
    tr = int(spl_data.get("timerange_hours") or 72)
    tz_name = str(spl_data.get("timezone") or "America/Los_Angeles")
    last_updated = html.escape(_splunk_p0_last_updated_label(tz_name))

    if not spl_ok:
        err = html.escape(str(spl_data.get("error") or "Unavailable")[:160])
        return f"""
            <div class="splunk-outliers-card" style='background:#ffffff;padding:16px;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,0.06);border:1px solid #e5e7eb;'>
                <h3 style='margin:0 0 6px;font-size:13px;color:#111827;'>{title_e}</h3>
                <div style='padding:6px 10px;border-radius:8px;background:#7f1d1d;color:#fca5a5;font-size:10px;'>{err}</div>
                <div style='margin-top:10px;font-size:10px;color:#6b7280;text-align:center;'>Last updated: {last_updated}</div>
            </div>"""

    grand = _splunk_p0_grand_total(spl_by_id)
    summary_bg = "#7f1d1d" if grand > 0 else "#14532d"
    sem_bar = _splunk_p0_semaphore_bar_html(spl_by_id, home_style=True)

    return f"""
            <div class="splunk-outliers-card" style='background:#ffffff;padding:16px;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,0.06),0 1px 2px rgba(0,0,0,0.04);border:1px solid #e5e7eb;'>
                <h3 style='margin:0 0 6px;font-size:13px;color:#111827;'>{title_e}</h3>
                <div style='padding:6px 10px;border-radius:8px;background:{summary_bg};color:#e2e8f0;font-size:10px;margin-bottom:6px;'>
                    <div style='display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;'>
                        <span>Σ total: <strong style='color:#fff;font-size:1.2em;'>{grand}</strong></span>
                        <span style='font-size:9px;opacity:0.9;'>{tr}h LLP</span>
                    </div>
                </div>
                <div style='font-size:11px;margin:0;padding:0;color:#111827;'>
                    {sem_bar}
                </div>
                <div style='margin-top:10px;font-size:10px;color:#6b7280;text-align:center;'>
                    Last updated: {last_updated}
                </div>
            </div>"""


def _samsung_splunk_embed_aside_html() -> str:
    """
    Right column for Samsung status monitor: Splunk latency charts (REST + token) via
    /embed/splunk-samsung-latencies (same as Samsung Dashboard). Falls back to Splunk web link.
    """
    tok = (os.environ.get("SPLUNK_TOKEN") or "").strip()
    splunk_web = (os.environ.get("SPLUNK_WEB_BASE") or "https://arlo.splunkcloud.com").rstrip("/")
    dash_path = (
        os.environ.get("SPLUNK_DASHBOARD_PATH") or "/en-US/app/search/samsung_alarm_latencies?tab=layout_1"
    ).strip()
    splunk_ui = splunk_web + (dash_path if dash_path.startswith("/") else "/" + dash_path)
    try:
        h = int((os.environ.get("SAMSUNG_SPLUNK_IFRAME_HEIGHT") or os.environ.get("DASHBOARD_IFRAME_HEIGHT") or "2800").strip() or "2800")
    except ValueError:
        h = 2800
    h = max(400, min(h, 20000))
    try:
        from samsung_splunk_api_latencies import any_panel_configured

        api_ready = bool(tok) and bool(any_panel_configured())
    except Exception:
        api_ready = bool(tok)
    if api_ready:
        u_iframe = "/embed/splunk-samsung-latencies"
        u_esc = html.escape(splunk_ui, quote=True)
        return f"""
        <aside class="sm-splunk-embed-side" aria-label="Splunk Samsung latencies" style="position:sticky;top:8px;align-self:start;min-width:0;border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;background:#0b0c12;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
            <div style="padding:8px 10px;border-bottom:1px solid #e2e8f0;background:linear-gradient(180deg,#f0f9ff 0%,#e0f2fe 100%);">
                <div style="font-size:11px;font-weight:800;color:#0c4a6e;">Splunk — Samsung latencies</div>
                <div style="font-size:9px;color:#64748b;margin-top:2px;">API (REST) · same charts as the Samsung dashboard</div>
            </div>
            <div style="position:relative;line-height:0;background:#0b0c12;">
                <div id="samsung-splunk-embed-load" class="samsung-splunk-embed-load" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;z-index:2;background:linear-gradient(180deg,#e0f2fe 0%,#bae6fd 100%);color:#0369a1;font-size:12px;font-weight:600;padding:16px;text-align:center;line-height:1.45;">Loading Splunk charts (REST)…<br><span style="font-size:10px;font-weight:500;opacity:0.9">May take 20s–2m with several panels.</span></div>
                <iframe class="samsung-spl-iframe" title="Splunk Samsung latencies" src="{html.escape(u_iframe, quote=True)}" style="width:100%;height:{h}px;border:0;display:block;background:#0b0c12;vertical-align:top;" loading="eager" onload="var e=document.getElementById('samsung-splunk-embed-load');if(e) e.style.display='none';"></iframe>
            </div>
            <p style="margin:0;padding:6px 10px;font-size:9px;color:#64748b;background:#f8fafc;border-top:1px solid #e2e8f0;">
                <a href="{u_esc}" target="_blank" rel="noopener" style="color:#0284c7;font-weight:600;">Open dashboard in Splunk</a>
            </p>
        </aside>"""
    hint = "Add <code>SPLUNK_TOKEN</code> and (optional) <code>spl/samsung_studio_dashboard.json</code> or Studio/SPL env vars (see <code>DOCKER_DEPLOYMENT.md</code> / your <code>.env</code>)."
    if tok and not api_ready:
        hint = (
            "Token set — configure a panel source: <code>SPLUNK_SAMSUNG_STUDIO_JSON</code>, "
            "or <code>SPLUNK_SAMSUNG_SPL_PROD</code> / <code>SPLUNK_FETCH_STUDIO_FROM_REST</code> (see <code>DOCKER_DEPLOYMENT.md</code>)."
        )
    u_esc2 = html.escape(splunk_ui, quote=True)
    return f"""
        <aside class="sm-splunk-embed-side" aria-label="Splunk" style="position:sticky;top:8px;min-width:0;padding:12px 14px;border:1px solid #e2e8f0;border-radius:10px;background:#f8fafc;box-shadow:0 1px 3px rgba(0,0,0,0.06);max-height:min(80vh,520px);overflow:auto;">
            <div style="font-size:12px;font-weight:800;color:#0f172a;margin-bottom:8px;">Splunk — Samsung latencies</div>
            <p style="margin:0 0 10px 0;font-size:10px;color:#64748b;line-height:1.5;">{hint}</p>
            <a href="{u_esc2}" target="_blank" rel="noopener" style="display:inline-block;padding:8px 12px;background:#0ea5e9;color:#fff;border-radius:8px;font-size:10px;font-weight:700;text-decoration:none;">Open in Splunk (browser)</a>
        </aside>"""


# Incremental /statusmonitor loads: merge per-env APM before command center + summary.
_sm_incr_sessions: dict = {}
_sm_incr_lock = threading.Lock()
_SM_INCR_TTL_SECS = 900


def _sm_incr_purge_sessions() -> None:
    now = time.time()
    with _sm_incr_lock:
        dead = [k for k, v in _sm_incr_sessions.items() if now - float(v.get("created") or 0) > _SM_INCR_TTL_SECS]
        for k in dead:
            _sm_incr_sessions.pop(k, None)


def _sm_incr_session_ensure(
    session_id: str,
    *,
    environment: str | None,
    timerange: int,
    force_refresh: bool,
    services: list,
    dd_environments: list,
) -> dict:
    _sm_incr_purge_sessions()
    with _sm_incr_lock:
        sess = _sm_incr_sessions.get(session_id)
        if sess is None:
            sess = {
                "created": time.time(),
                "environment": environment,
                "timerange": int(timerange),
                "force_refresh": bool(force_refresh),
                "services": list(services),
                "dd_environments": list(dd_environments),
                "statuses_by_env": {},
                "pd_counts": {"triggered": 0, "acknowledged": 0, "resolved": 0},
                "pd_incidents": [],
                "arlo_services_status": [],
            }
            _sm_incr_sessions[session_id] = sess
        return sess


def _sm_incr_merged_statuses(session_id: str) -> list:
    sess = _sm_incr_sessions.get(session_id) or {}
    out: list = []
    for env in sess.get("dd_environments") or []:
        out.extend(list((sess.get("statuses_by_env") or {}).get(env) or []))
    return out


_SM_ENG_HALF_WIDTH_SLUGS = frozenset(
    {
        "client-engineering",
        "firmware",
        "windows",
        "onecloud-engineering",
        "ecommerce",
        "infrared-services",
        "samsung-partner",
        "smart-vision",
        "sre",
        "oci",
        "noc",
        "npnoc",
    }
)
_SM_ENG_HALF_WIDTH_PAIR_LEAD: dict[str, tuple[str, ...]] = {
    "samsung-partner": ("samsung-partner", "sre"),
    "smart-vision": ("smart-vision", "oci"),
    "noc": ("noc", "npnoc"),
    "windows": ("windows", "onecloud-engineering"),
    "ecommerce": ("ecommerce", "infrared-services"),
    "client-engineering": ("client-engineering", "firmware"),
}
_SM_ENG_HALF_WIDTH_PAIR_SKIP = frozenset(
    {"sre", "oci", "npnoc", "onecloud-engineering", "infrared-services", "firmware"}
)


def _sm_engineering_mosaic_enabled(page_environment: str | None, env: str) -> bool:
    wall_dde = _sm_page_environment_to_wall_dd_env(page_environment)
    if not wall_dde:
        return False
    expected_tag = _sm_wall_dd_env_to_dd_tag(wall_dde)
    if (env or "").strip() != expected_tag:
        return False
    try:
        from tools.apm_engineering_groups import (
            apm_engineering_groups_enabled,
            engineering_wall_uses_org_catalog,
        )

        return apm_engineering_groups_enabled() and engineering_wall_uses_org_catalog(wall_dde)
    except Exception:
        return False


def _sm_safe_http_href(url: str | None) -> str | None:
    u = (url or "").strip()
    if u.startswith("http://") or u.startswith("https://"):
        return u
    return None


def _sm_org_wall_incident_count(s: dict) -> int:
    n = 0
    if s.get("pd_incident"):
        n += 1
    dd = int(s.get("dd_monitor_alert_count") or 0)
    if dd > 0:
        n += dd
    suff = int(s.get("dd_monitor_alert_suffix_count") or 0)
    if suff > 0:
        n += suff
    return n


def _sm_hover_chip_html(text: str, chip_cls: str, href: str | None = None) -> str:
    if href:
        return (
            f'<a class="{chip_cls}" href="{html.escape(href, quote=True)}" '
            f'target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">'
            f"{html.escape(text)}</a>"
        )
    return f'<span class="{chip_cls}">{html.escape(text)}</span>'


def _sm_org_wall_dd_pill_html(s: dict) -> str:
    wddc = int(s.get("dd_monitor_alert_count") or 0)
    wdd_suf = int(s.get("dd_monitor_alert_suffix_count") or 0)
    total = int(s.get("dd_monitor_open_count") or 0) or (wddc + wdd_suf)
    if total <= 0:
        return ""
    only_ab = wddc == 0 and wdd_suf > 0
    href = _sm_safe_http_href(
        s.get("dd_monitors_url_all_alerts") if wdd_suf > 0 else s.get("dd_monitors_url")
    ) or _sm_safe_http_href(s.get("dd_monitors_url"))
    chip_cls = (
        "sm-hover-tip-chip sm-hover-tip-chip--dd-open"
        if only_ab
        else "sm-hover-tip-chip sm-hover-tip-chip--alert"
    )
    label = f"{total} DD"
    title = (
        f"{total} Datadog alert(s) (-a/-b tier); tile stays green"
        if only_ab
        else f"{total} Datadog monitor(s) in Alert"
    )
    title_attr = html.escape(title, quote=True)
    if href:
        return (
            f'<a class="{chip_cls}" href="{html.escape(href, quote=True)}" '
            f'target="_blank" rel="noopener noreferrer" title="{title_attr}" '
            f'onclick="event.stopPropagation()">{html.escape(label)}</a>'
        )
    return f'<span class="{chip_cls}" title="{title_attr}">{html.escape(label)}</span>'


def _sm_org_wall_service_monitor_chips_html(s: dict) -> str:
    parts: list[str] = []
    if s.get("pd_incident"):
        pdu = _sm_safe_http_href(s.get("pd_incident_url"))
        parts.append(_sm_hover_chip_html("PD", "sm-hover-tip-chip sm-hover-tip-chip--alert", pdu))
    spl = _sm_safe_http_href(s.get("splunk_url"))
    if spl:
        parts.append(_sm_hover_chip_html("SPL", "sm-hover-tip-chip sm-hover-tip-chip--splunk", spl))
    if s.get("traffic_drop"):
        parts.append(_sm_hover_chip_html("Traffic drop", "sm-hover-tip-chip sm-hover-tip-chip--alert"))
    elif s.get("traffic_variance") is not None:
        tv = int(round(float(s.get("traffic_variance"))))
        parts.append(_sm_hover_chip_html(f"Traffic {tv}% vs 7d", "sm-hover-tip-chip"))
    dd = _sm_org_wall_dd_pill_html(s)
    if dd:
        parts.append(dd)
    return "".join(parts)


def _sm_org_wall_monitor_chips_html(ser: dict) -> str:
    st = ser.get("status") or ""
    if st in ("critical", "warning"):
        inner = _sm_org_wall_service_monitor_chips_html(ser)
    else:
        inner = ""
        spl = _sm_safe_http_href(ser.get("splunk_url"))
        if spl:
            inner += _sm_hover_chip_html("SPL", "sm-hover-tip-chip sm-hover-tip-chip--splunk", spl)
        inner += _sm_org_wall_dd_pill_html(ser)
    if not inner:
        return ""
    return (
        f'<div class="sw-tile-org-chips sw-tile-inline-chips sm-hover-tip-chips">{inner}</div>'
    )


def _sm_org_wall_status_foot_html(ser: dict) -> str:
    st = ser.get("status") or "unknown"
    badge_cls = "sw-tile-org-badge"
    if st == "warning":
        badge_text, badge_cls = "WARN", badge_cls + " sw-tile-org-badge--warn"
    elif st == "critical":
        badge_text, badge_cls = "CRIT", badge_cls + " sw-tile-org-badge--crit"
    else:
        badge_text = "OK"
    inc_html = ""
    inc = _sm_org_wall_incident_count(ser)
    if st in ("warning", "critical") and inc > 0:
        inc_html = f'<span class="sw-tile-org-inc">{inc} inc</span>'
    chips = _sm_org_wall_monitor_chips_html(ser)
    return (
        f'<div class="sw-tile-org-foot"><span class="{badge_cls}">{badge_text}</span>'
        f"{inc_html}{chips}</div>"
    )


def _sm_org_wall_tile_wrap_class(ser: dict) -> str:
    st = ser.get("status") or "unknown"
    label = str(ser.get("service") or "")
    lab_len = len(label)
    if st == "critical":
        return "sw-tile-wrap--size-crit-wide" if lab_len > 17 else "sw-tile-wrap--size-crit"
    if st == "warning":
        return "sw-tile-wrap--size-warn-wide" if lab_len > 17 else "sw-tile-wrap--size-warn"
    return "sw-tile-wrap--size-ok"


def _sm_org_wall_tile_class(ser: dict) -> str:
    st = ser.get("status") or "unknown"
    cls = "sw-tile"
    if st == "critical":
        cls += " sw-tile--crit sw-tile--alert"
    elif st == "warning":
        cls += " sw-tile--warn sw-tile--alert"
    elif st == "healthy":
        cls += " sw-tile--healthy"
    else:
        cls += " sw-tile--neutral"
    return cls


def _sm_org_wall_tile_html(
    ser: dict,
    raw_svc: dict,
    env: str,
    page_environment: str | None,
) -> str:
    label = html.escape(str(ser.get("service") or "—"))
    wrap_cls = _sm_org_wall_tile_wrap_class(ser)
    tile_cls = _sm_org_wall_tile_class(ser)
    apm_url = html.escape(str(ser.get("apm_url") or "#"), quote=True)
    st = ser.get("status") or ""
    hyphen_split = ""
    if st in ("warning", "critical") and "-" in str(ser.get("service") or ""):
        segs = [p.strip() for p in re.split(r"-+", str(ser.get("service") or "")) if p.strip()]
        if len(segs) >= 2:
            wrap_cls += " sw-tile-wrap--hyphen-split"
            hyphen_split = " sw-tile-name-hyphen-split"
    hover_j = _sm_hover_json_attr(
        _sm_hover_service_payload(raw_svc, env, page_environment=page_environment)
    )
    foot = _sm_org_wall_status_foot_html(ser)
    return f"""
    <div class="sw-tile-wrap {wrap_cls} sm-tip-wrap" data-sm-hover="{hover_j}">
        <div class="{tile_cls}" role="link" tabindex="0"
             onclick="window.open('{apm_url}', '_blank')" style="cursor:pointer">
            <div class="sw-tile-name-compact">
                <div class="sw-tile-name-scroll{hyphen_split}">{label}</div>
            </div>
            {foot}
        </div>
    </div>
    """


def _sm_build_engineering_mosaic_data(
    env_services: list,
    page_environment: str | None,
    *,
    timerange: int = 1,
) -> tuple[list, list] | None:
    wall_dde = _sm_page_environment_to_wall_dd_env(page_environment)
    if not wall_dde:
        return None
    dd_site = os.getenv("DD_SITE", "datadoghq.com")
    dd_api_key = os.getenv("DATADOG_API_KEY") or os.getenv("DD_API_KEY")
    dd_app_key = os.getenv("DATADOG_APP_KEY") or os.getenv("DD_APP_KEY")
    ser = [
        _wall_serialize_status(s, dd_site, wall_mode=wall_dde, timerange_hours=timerange)
        for s in env_services
        if s.get("status") in ("healthy", "warning", "critical")
    ]
    try:
        from tools.apm_engineering_groups import (
            apm_status_wall_use_dd_team,
            build_engineering_sections,
            engineering_column_layout,
            fetch_datadog_catalog_service_owners,
        )

        owner_by_service = None
        if apm_status_wall_use_dd_team(wall_dde) and dd_api_key and dd_app_key:
            owner_by_service = fetch_datadog_catalog_service_owners(
                dd_api_key, dd_app_key, dd_site
            )
        eng_sections = build_engineering_sections(
            ser, dd_env=wall_dde, owner_by_service=owner_by_service
        )
        return eng_sections, engineering_column_layout(wall_dde)
    except Exception as e:
        print(f"⚠️ SM engineering mosaic skipped: {e}")
        return None


def _sm_render_engineering_block_html(
    eng: dict,
    raw_by_service: dict[str, dict],
    env: str,
    page_environment: str | None,
    section_prefix: str,
    width_mode: str | None = None,
) -> str:
    esvcs = eng.get("services") or []
    if not esvcs:
        return ""
    slug = str(eng.get("slug") or "")
    block_id = html.escape(f"{section_prefix}-{slug}" if slug else section_prefix)
    block_cls = "sw-eng-block"
    if width_mode == "quarter":
        block_cls += " sw-eng-block--quarter"
    elif width_mode == "half" or slug in _SM_ENG_HALF_WIDTH_SLUGS:
        block_cls += " sw-eng-block--half"
    ec = eng.get("counts") or {}
    cols = max(1, int(eng.get("tile_columns") or 2))
    tiles = "".join(
        _sm_org_wall_tile_html(
            ser,
            raw_by_service.get(str(ser.get("service") or ""), ser),
            env,
            page_environment,
        )
        for ser in esvcs
    )
    label = html.escape(str(eng.get("label") or eng.get("key") or "Engineering"))
    return f"""
    <div class="{block_cls}" id="{block_id}">
        <div class="sw-eng-head">
            <div class="sw-eng-head-title">{label}</div>
            <div class="sw-eng-head-badges">
                <span class="sw-eng-head-badge--ok" title="Healthy">{int(ec.get('healthy') or 0)}</span>
                <span class="sw-eng-head-badge--warn" title="Warning">{int(ec.get('warning') or 0)}</span>
                <span class="sw-eng-head-badge--crit" title="Critical">{int(ec.get('critical') or 0)}</span>
            </div>
        </div>
        <div class="sw-eng-tiles-wrap">
            <div class="sw-tiles sw-tiles--org-wall" data-tile-cols="{cols}">
                {tiles}
            </div>
        </div>
    </div>
    """


def _sm_render_engineering_mosaic_html(
    env_services: list,
    env: str,
    page_environment: str | None,
    *,
    timerange: int = 1,
) -> str:
    data = _sm_build_engineering_mosaic_data(
        env_services, page_environment, timerange=timerange
    )
    if not data:
        return ""
    eng_sections, column_layout = data
    raw_by_service = {str(s.get("service") or ""): s for s in env_services}
    by_slug = {
        str(e.get("slug") or ""): e
        for e in eng_sections
        if e.get("slug") and (e.get("services") or [])
    }
    section_prefix = "sm-eng"
    placed: set[str] = set()
    layout_parts: list[str] = ['<div class="sm-eng-mosaic-wrap sw-compact-org-wrap"><div class="sw-eng-layout">']

    for col_slugs in column_layout or []:
        col_parts: list[str] = ['<div class="sw-eng-col">']
        col_has = False
        for slug in col_slugs or []:
            slug = str(slug)
            if slug in _SM_ENG_HALF_WIDTH_PAIR_SKIP:
                continue
            if slug in _SM_ENG_HALF_WIDTH_PAIR_LEAD:
                pair = _SM_ENG_HALF_WIDTH_PAIR_LEAD[slug]
                row_parts = ['<div class="sw-eng-block-row">']
                row_any = False
                for ps in pair:
                    eng = by_slug.get(ps)
                    if not eng:
                        continue
                    blk = _sm_render_engineering_block_html(
                        eng, raw_by_service, env, page_environment, section_prefix, "half"
                    )
                    if blk:
                        row_parts.append(blk)
                        placed.add(ps)
                        row_any = True
                row_parts.append("</div>")
                if row_any:
                    col_parts.append("".join(row_parts))
                    col_has = True
                continue
            eng = by_slug.get(slug)
            if not eng:
                continue
            blk = _sm_render_engineering_block_html(
                eng, raw_by_service, env, page_environment, section_prefix
            )
            if blk:
                col_parts.append(blk)
                placed.add(slug)
                col_has = True
        col_parts.append("</div>")
        if col_has:
            layout_parts.append("".join(col_parts))

    trailing: list[str] = []
    for eng in eng_sections:
        slug = str(eng.get("slug") or "")
        if slug and slug not in placed and (eng.get("services") or []):
            blk = _sm_render_engineering_block_html(
                eng, raw_by_service, env, page_environment, section_prefix
            )
            if blk:
                trailing.append(blk)
                placed.add(slug)
    if trailing:
        layout_parts.append(f'<div class="sw-eng-col">{"".join(trailing)}</div>')

    layout_parts.append("</div></div>")
    return "".join(layout_parts)


def _sm_render_dd_env_column_html(
    env: str,
    all_statuses: list,
    page_environment: str | None,
    environments_list: list,
    *,
    timerange: int = 1,
) -> str:
    """HTML for one Datadog env column (service groups + tiles)."""
    env_config = {
        "production": {"icon": "🔵", "color": "#3b82f6"},
        "goldendev": {"icon": "🔵", "color": "#3b82f6"},
        "goldenqa": {"icon": "🔵", "color": "#3b82f6"},
        "qa": {"icon": "🔵", "color": "#3b82f6"},
        "samsung_prod": {"icon": "📱", "color": "#0ea5e9"},
        "adt_prod": {"icon": "🏠", "color": "#8b5cf6"},
    }
    if page_environment == "samsung":
        config = {"icon": "📱", "color": "#0ea5e9"}
    else:
        config = env_config.get(env, {"icon": "🔵", "color": "#3b82f6"})
    env_services = [s for s in all_statuses if s.get("environment") == env]
    env_services.sort(key=lambda x: (x.get("service") or "").lower())

    if _sm_engineering_mosaic_enabled(page_environment, env):
        mosaic = _sm_render_engineering_mosaic_html(
            env_services, env, page_environment, timerange=timerange
        )
        if mosaic:
            return mosaic

    if page_environment == "samsung":

        def _sk(s):
            return (s.get("service") or "").lower()

        def _is_samsung_partner(sk):
            return sk.startswith("backend-pp") or "pp-samsung" in sk or "pp_samsung" in sk

        def _is_samsung_hmsguard(sk):
            return "hmsguard" in sk

        partner_svcs = [s for s in env_services if _is_samsung_partner(_sk(s))]
        _pkeys = {(s["service"], s["environment"]) for s in partner_svcs}
        hmg_svcs = [
            s
            for s in env_services
            if _is_samsung_hmsguard(_sk(s)) and (s["service"], s["environment"]) not in _pkeys
        ]
        _hkeys = {(s["service"], s["environment"]) for s in hmg_svcs}
        other_svcs = [
            s
            for s in env_services
            if (s["service"], s["environment"]) not in _pkeys and (s["service"], s["environment"]) not in _hkeys
        ]
        for _grp in (partner_svcs, hmg_svcs, other_svcs):
            _grp.sort(key=lambda x: (x.get("service") or "").lower())
        service_groups = []
        if hmg_svcs:
            service_groups.append({"name": "HMSGUARD", "icon": "🛡️", "color": "#0ea5e9", "services": hmg_svcs})
        if partner_svcs:
            service_groups.append({"name": "Partner Platform", "icon": "🤝", "color": "#0284c7", "services": partner_svcs})
        if other_svcs:
            service_groups.append({"name": "Samsung services", "icon": "📱", "color": "#06b6d4", "services": other_svcs})
        if not service_groups and env_services:
            service_groups.append({"name": "Samsung", "icon": "📱", "color": "#0ea5e9", "services": list(env_services)})
    else:
        service_groups = [{"name": env.upper(), "icon": config["icon"], "color": config["color"], "services": env_services}]

    out = ""
    pad = "12px" if len(environments_list) == 1 else "10px"
    for group in service_groups:
        group_services = group["services"]
        if not group_services:
            continue
        group_healthy = sum(1 for s in group_services if s["status"] == "healthy")
        group_warning = sum(1 for s in group_services if s["status"] == "warning")
        group_critical = sum(1 for s in group_services if s["status"] == "critical")
        group_nosig = sum(1 for s in group_services if s["status"] in ("inactive", "unknown"))
        out += f"""
        <div>
            <div style='background: {group['color']}; color: white; padding: 6px 8px; border-radius: 5px 5px 0 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
                <div style='display: flex; justify-content: space-between; align-items: center;'>
                    <div style='font-size: 11px; font-weight: bold;'>{group['icon']} {group['name']}</div>
                    <div style='font-size: 8px; opacity: 0.9;'>✓ {group_healthy} | ⚠ {group_warning} | ✗ {group_critical} | ○ {group_nosig}</div>
                </div>
            </div>
            <div style='padding: {pad}; background: #f8fafc; border-radius: 0 0 8px 8px; min-height: 80px;'>
        """
        issue_svcs = sorted(
            [s for s in group_services if s["status"] in ("critical", "warning")],
            key=lambda s: (0 if s["status"] == "critical" else 1, -s["error_rate"], -s["errors"]),
        )
        healthy_svcs = sorted([s for s in group_services if s["status"] == "healthy"], key=lambda x: x["service"].lower())
        dd_site_chips = os.getenv("DD_SITE", "datadoghq.com")
        op_tile_count = len(issue_svcs) + len(healthy_svcs)
        if op_tile_count:
            out += """
            <div class='sm-band-healthy'>
            <div class='sm-section-label' style='margin-top:0;'>Operational — """ + str(op_tile_count) + """</div>
            <div class='sm-op-tiles'>
        """
        for svc in issue_svcs:
            dd_site_tile = os.getenv("DD_SITE", "datadoghq.com")
            service_name = svc["service"]
            dd_url = (
                f"{datadog_ui_origin(dd_site_tile)}/apm/service/"
                f"{quote(service_name, safe='')}/overview?env={quote(env, safe='')}"
            )
            is_crit = svc["status"] == "critical"
            tile_mod = "sm-op-tile--crit" if is_crit else "sm-op-tile--warn"
            alert_tile = " service-box-alert" if svc["status"] in ("warning", "critical") else ""
            icon = "✕" if is_crit else "⚠"
            t_name = html.escape(svc["service"])
            t_err = html.escape(f"{svc['error_rate']}")
            url_attr = html.escape(dd_url, quote=True)
            hover_j = _sm_hover_json_attr(_sm_hover_service_payload(svc, env, page_environment=page_environment))
            out += f"""
                <div class='sm-tip-wrap' data-sm-hover="{hover_j}">
                <a class='sm-op-tile {tile_mod}{alert_tile}' href="{url_attr}" target="_blank" rel="noopener">
                    <div class='sm-op-tile-icon'>{icon}</div>
                    <div class='sm-op-tile-name'>{t_name}</div>
                    <div class='sm-op-tile-metric'>{t_err}%</div>
                    <div class='sm-op-tile-metric-lbl'>ERR</div>
                </a>
                </div>
                """
        for hsvc in healthy_svcs:
            t_url = f"{datadog_ui_origin(dd_site_chips)}/apm/service/{hsvc['service']}/overview?env={env}"
            t_name = html.escape(hsvc["service"])
            t_met, t_lbl = _sm_op_tile_metric_html(hsvc)
            met_suffix = "" if t_lbl == "OK" else "%"
            hover_j = _sm_hover_json_attr(_sm_hover_service_payload(hsvc, env, page_environment=page_environment))
            out += f"""
                <div class='sm-tip-wrap' data-sm-hover="{hover_j}">
                <div class='sm-op-tile' onclick="window.open('{t_url}', '_blank')">
                    <div class='sm-op-tile-icon'>✓</div>
                    <div class='sm-op-tile-name'>{t_name}</div>
                    <div class='sm-op-tile-metric'>{t_met}{met_suffix}</div>
                    <div class='sm-op-tile-metric-lbl'>{html.escape(t_lbl)}</div>
                </div>
                </div>
                """
        if op_tile_count:
            out += """
            </div>
            </div>
        """
        nosig_svcs = sorted(
            [s for s in group_services if s.get("status") in ("inactive", "unknown")],
            key=lambda x: (x.get("service") or "").lower(),
        )
        if page_environment == "samsung" and nosig_svcs:
            dd_site_nosig = os.getenv("DD_SITE", "datadoghq.com")
            out += f"""
            <div class="sm-band-nosig">
            <div class="sm-section-label" style="margin-top:0;">No APM signal — {len(nosig_svcs)}</div>
            <div class="sm-op-tiles">
        """
            for svc in nosig_svcs:
                service_name = svc["service"]
                dd_url = (
                    f"{datadog_ui_origin(dd_site_nosig)}/apm/service/"
                    f"{quote(service_name, safe='')}/overview?env={quote(env, safe='')}"
                )
                st = svc.get("status") or "unknown"
                icon = "○" if st == "inactive" else "?"
                t_name = html.escape(service_name)
                url_attr = html.escape(dd_url, quote=True)
                lbl = "idle" if st == "inactive" else "unknown"
                hover_j = _sm_hover_json_attr(_sm_hover_service_payload(svc, env, page_environment=page_environment))
                out += f"""
                <div class="sm-tip-wrap" data-sm-hover="{hover_j}">
                <a class="sm-op-tile sm-op-tile--nosig" href="{url_attr}" target="_blank" rel="noopener">
                    <div class="sm-op-tile-icon">{icon}</div>
                    <div class="sm-op-tile-name">{t_name}</div>
                    <div class="sm-op-tile-metric">—</div>
                    <div class="sm-op-tile-metric-lbl">{html.escape(lbl)}</div>
                </a>
                </div>
                """
            out += """
            </div>
            </div>
        """
        out += """
            </div>
        </div>
        """
    return out


def status_monitor_dashboard(
    timerange: int = 1,
    environment: str = None,
    force_refresh: bool = False,
    *,
    fragment: str | None = None,
    only_dd_env: str | None = None,
    all_statuses_override: list | None = None,
    incr_session_id: str | None = None,
    skip_splunk: bool = False,
) -> str:
    """
    Generate Status Monitor Dashboard HTML
    
    Args:
        timerange: Time range in hours (default 1)
        environment: Specific environment (production, goldendev, goldenqa, qa, samsung, adt, redmetrics-us) or None for hub
    
    Returns:
        HTML string for the dashboard
    """
    global _status_cache
    
    frag = (fragment or "").strip().lower() or None
    is_full = frag is None

    # Check cache first - include version to invalidate cache when logic changes
    cache_version = "v3.4.27_eng_mosaic_green"  # Change this when logic changes
    cache_key = f"{cache_version}_{timerange}_{environment}_{int(time.time() // _cache_ttl)}"
    if is_full:
        hit = _read_sm_mem_cache(_status_cache, cache_key, force_refresh)
        if hit is not None:
            print(f"✅ Using cached dashboard data (cache key: {cache_key}, force_refresh={force_refresh})")
            return hit
    
    if is_full:
        print(f"🔄 Cache miss - fetching fresh data (key: {cache_key}, force_refresh={force_refresh})")

    current_time = int(time.time())
    from_time = current_time - (timerange * 3600)
    skip_chrome = frag in ("sidebar_fast", "sidebar_splunk", "env_column", "finalize")

    if skip_chrome:
        output = ""
    else:
        # Header HTML + styles
        output = """
    <style>
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.7; }
        }
        @keyframes blink-alert {
            0%, 100% { opacity: 1; box-shadow: 0 1px 2px rgba(0,0,0,0.12); }
            50% { opacity: 0.85; box-shadow: 0 0 8px rgba(255,255,255,0.5), 0 0 12px rgba(255,255,255,0.3); }
        }
        .service-box-clickable {
            cursor: pointer;
            transition: all 0.2s;
        }
        .service-box-clickable:hover {
            transform: translateY(-2px);
            box-shadow: 0 3px 8px rgba(0,0,0,0.25) !important;
        }
        .service-box-alert {
            animation: blink-alert 2s ease-in-out infinite;
        }
        /* Custom scrollbar for cluster lists */
        div[style*="overflow-y: auto"]::-webkit-scrollbar {
            width: 4px;
        }
        div[style*="overflow-y: auto"]::-webkit-scrollbar-track {
            background: rgba(255, 255, 255, 0.1);
            border-radius: 2px;
        }
        div[style*="overflow-y: auto"]::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.4);
            border-radius: 2px;
        }
        div[style*="overflow-y: auto"]::-webkit-scrollbar-thumb:hover {
            background: rgba(255, 255, 255, 0.6);
        }
        /* Firefox scrollbar */
        div[style*="overflow-y: auto"] {
            scrollbar-width: thin;
            scrollbar-color: rgba(255, 255, 255, 0.4) rgba(255, 255, 255, 0.1);
        }
        .cc-strip {
            background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
            border-radius: 10px;
            padding: 14px 16px;
            margin-bottom: 16px;
            border: 1px solid #e2e8f0;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
        }
        .cc-kpi-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 8px;
            margin-bottom: 14px;
        }
        .cc-kpi {
            background: #f1f5f9;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 10px 12px;
            text-align: center;
        }
        .cc-kpi-val {
            font-size: 26px;
            font-weight: 800;
            color: #0f172a;
            letter-spacing: -0.03em;
            line-height: 1.1;
        }
        .cc-kpi-lbl {
            font-size: 10px;
            font-weight: 600;
            color: #64748b;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            margin-top: 4px;
        }
        .cc-delta-up { color: #dc2626; font-size: 12px; font-weight: 700; }
        .cc-delta-down { color: #15803d; font-size: 12px; font-weight: 700; }
        .cc-delta-flat { color: #64748b; font-size: 12px; font-weight: 600; }
        .cc-attention-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 11px;
            color: #334155;
        }
        .cc-attention-table th {
            text-align: left;
            padding: 6px 8px;
            color: #64748b;
            font-weight: 700;
            text-transform: uppercase;
            font-size: 9px;
            letter-spacing: 0.05em;
            border-bottom: 1px solid #e2e8f0;
        }
        .cc-attention-table td {
            padding: 7px 8px;
            border-bottom: 1px solid #f1f5f9;
            vertical-align: middle;
        }
        .cc-attention-table tr:hover td {
            background: #f8fafc;
        }
        .cc-dd-alerts {
            display: block;
            min-width: 120px;
            max-width: 220px;
            padding: 6px 8px;
            border-radius: 8px;
            border: 1px solid #fecaca;
            background: #fef2f2;
            text-decoration: none;
            color: inherit;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
        }
        a.cc-dd-alerts:hover {
            border-color: #f87171;
            box-shadow: 0 2px 6px rgba(220, 38, 38, 0.15);
        }
        .cc-dd-alerts--suffix {
            border-color: #bbf7d0;
            background: #f0fdf4;
        }
        a.cc-dd-alerts--suffix:hover {
            border-color: #4ade80;
            box-shadow: 0 2px 6px rgba(34, 197, 94, 0.12);
        }
        .cc-dd-alerts--none {
            border-color: #e2e8f0;
            background: #f8fafc;
            text-align: center;
            padding: 8px;
        }
        .cc-dd-alerts__head {
            display: flex;
            align-items: baseline;
            gap: 6px;
            margin-bottom: 4px;
        }
        .cc-dd-alerts__count {
            font-size: 14px;
            font-weight: 900;
            color: #b91c1c;
            line-height: 1;
        }
        .cc-dd-alerts--suffix .cc-dd-alerts__count {
            color: #15803d;
        }
        .cc-dd-alerts--none .cc-dd-alerts__count {
            color: #64748b;
        }
        .cc-dd-alerts__lbl {
            font-size: 8px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: #64748b;
        }
        .cc-dd-alerts__list {
            margin: 0;
            padding: 0 0 0 14px;
            font-size: 9px;
            font-weight: 600;
            color: #334155;
            line-height: 1.35;
            max-height: 72px;
            overflow-y: auto;
        }
        .cc-dd-alerts__list li {
            margin: 0 0 2px 0;
        }
        .cc-dd-alerts__suffix {
            color: #166534;
        }
        .cc-dd-alerts__more {
            list-style: none;
            margin-left: -14px;
            color: #64748b;
            font-weight: 700;
        }
        .cc-cluster-select {
            max-width: 168px;
            width: 100%;
            font-size: 10px;
            font-weight: 600;
            padding: 4px 8px;
            border-radius: 6px;
            border: 1px solid #cbd5e1;
            background: #ffffff;
            color: #0f172a;
            cursor: pointer;
        }
        .cc-cluster-select:focus {
            outline: 2px solid #38bdf8;
            outline-offset: 1px;
        }
        .cc-err-bar-wrap {
            width: 38px;
            max-width: 38px;
            flex-shrink: 0;
        }
        .cc-err-bar-track {
            width: 100%;
            min-width: 0;
            height: 10px;
            background: #e2e8f0;
            border-radius: 5px;
            overflow: hidden;
            box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.06);
        }
        .cc-err-bar-fill {
            height: 100%;
            border-radius: 5px;
            min-width: 0;
            transition: width 0.35s ease;
        }
        .cc-pill {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 999px;
            font-size: 9px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }
        .cc-pill-watch {
            background: #16a34a;
            color: #f0fdf4;
        }
        .cc-link-btn {
            display: inline-block;
            margin-top: 8px;
            padding: 6px 12px;
            background: #e0f2fe;
            border: 1px solid #7dd3fc;
            color: #0369a1;
            border-radius: 6px;
            font-size: 10px;
            font-weight: 700;
            text-decoration: none;
            cursor: pointer;
        }
        .cc-link-btn:hover {
            background: #bae6fd;
            color: #0c4a6e;
        }
        .sm-healthy-chips {
            display: flex;
            flex-wrap: wrap;
            gap: 5px;
            margin-top: 6px;
            align-items: center;
        }
        /* Operational: green squares (~10% smaller vs v3.2.1, tighter grid) */
        .sm-hub-cell.sm-tip-wrap { position: relative; overflow: visible; }
        .sm-tip-wrap {
            position: relative;
            min-width: 0;
            overflow: visible;
            display: flex;
            align-items: stretch;
        }
        .sm-tip-wrap > .sm-op-tile {
            flex: 1;
            width: 100%;
            min-width: 0;
        }
        .sm-op-tiles {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(96px, 1fr));
            gap: 5px;
            margin-top: 5px;
            justify-items: stretch;
        }
        .sm-op-tile {
            aspect-ratio: 1;
            min-height: 96px;
            max-height: 120px;
            border-radius: 12px;
            background: linear-gradient(165deg, #86efac 0%, #4ade80 32%, #22c55e 68%, #15803d 100%);
            border: 1px solid #166534;
            box-shadow: 0 3px 10px rgba(34, 197, 94, 0.5);
            color: #052e16;
            cursor: pointer;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            padding: 7px 6px;
            text-align: center;
            transition: transform 0.12s ease, box-shadow 0.12s ease;
            text-decoration: none;
            box-sizing: border-box;
        }
        a.sm-op-tile {
            text-decoration: none;
            color: inherit;
        }
        .sm-op-tile:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 22px rgba(34, 197, 94, 0.58);
            border-color: #14532d;
        }
        .sm-op-tile-icon {
            font-size: 15px;
            line-height: 1;
            margin-bottom: 5px;
            color: #f0fdf4;
            text-shadow: 0 1px 2px rgba(0,0,0,0.2);
        }
        .sm-op-tile-name {
            font-size: 10px;
            font-weight: 800;
            line-height: 1.15;
            color: #f0fdf4;
            text-shadow: 0 1px 2px rgba(0,0,0,0.25);
            word-break: break-word;
            overflow: hidden;
            display: -webkit-box;
            -webkit-line-clamp: 3;
            -webkit-box-orient: vertical;
            max-height: 3.45em;
            letter-spacing: -0.02em;
        }
        .sm-op-tile-metric {
            font-size: 14px;
            font-weight: 800;
            margin-top: 5px;
            color: #ecfdf5;
            letter-spacing: -0.03em;
            text-shadow: 0 1px 2px rgba(0,0,0,0.2);
        }
        .sm-op-tile-metric-lbl {
            font-size: 7.5px;
            font-weight: 700;
            color: #dcfce7;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-top: 1px;
        }
        /* Live issues: same footprint as operational tiles */
        .sm-op-tile--warn {
            background: linear-gradient(165deg, #fef08a 0%, #facc15 35%, #f59e0b 72%, #d97706 100%);
            border: 1px solid #b45309;
            box-shadow: 0 3px 10px rgba(245, 158, 11, 0.55);
        }
        .sm-op-tile--warn:hover {
            box-shadow: 0 8px 22px rgba(245, 158, 11, 0.55);
            border-color: #92400e;
        }
        .sm-op-tile--warn .sm-op-tile-icon,
        .sm-op-tile--warn .sm-op-tile-name,
        .sm-op-tile--warn .sm-op-tile-metric,
        .sm-op-tile--warn .sm-op-tile-metric-lbl {
            color: #422006;
            text-shadow: 0 1px 0 rgba(255,255,255,0.35);
        }
        .sm-op-tile--crit {
            background: linear-gradient(165deg, #fca5a5 0%, #f87171 32%, #ef4444 65%, #b91c1c 100%);
            border: 1px solid #7f1d1d;
            box-shadow: 0 3px 12px rgba(239, 68, 68, 0.55);
        }
        .sm-op-tile--crit:hover {
            box-shadow: 0 8px 24px rgba(220, 38, 38, 0.6);
            border-color: #450a0a;
        }
        .sm-op-tile--crit .sm-op-tile-icon,
        .sm-op-tile--crit .sm-op-tile-name,
        .sm-op-tile--crit .sm-op-tile-metric,
        .sm-op-tile--crit .sm-op-tile-metric-lbl {
            color: #fff7ed;
            text-shadow: 0 1px 2px rgba(0,0,0,0.25);
        }
        .sm-op-tile--crit.service-box-alert {
            animation: blink-alert 2s ease-in-out infinite;
        }
        .sm-op-tile--warn.service-box-alert {
            animation: blink-alert 2s ease-in-out infinite;
        }
        .sm-band-nosig {
            background: linear-gradient(180deg, #f8fafc 0%, #f1f5f9 100%);
            border: 1px solid #cbd5e1;
            border-radius: 10px;
            padding: 10px 10px 12px;
            margin-top: 8px;
        }
        .sm-op-tile--nosig {
            background: linear-gradient(165deg, #94a3b8 0%, #64748b 45%, #475569 100%);
            border: 1px solid #334155;
            box-shadow: 0 2px 8px rgba(51, 65, 85, 0.35);
        }
        .sm-op-tile--nosig:hover {
            box-shadow: 0 6px 18px rgba(51, 65, 85, 0.45);
            border-color: #1e293b;
        }
        .sm-op-tile--nosig .sm-op-tile-icon,
        .sm-op-tile--nosig .sm-op-tile-name,
        .sm-op-tile--nosig .sm-op-tile-metric,
        .sm-op-tile--nosig .sm-op-tile-metric-lbl {
            color: #f8fafc;
            text-shadow: 0 1px 2px rgba(0,0,0,0.25);
        }
        .sm-section-label {
            font-size: 10px;
            font-weight: 800;
            color: #475569;
            text-transform: uppercase;
            letter-spacing: 0.07em;
            margin-top: 12px;
            margin-bottom: 4px;
        }
        .sm-band-issues {
            background: linear-gradient(180deg, #fff1f2 0%, #ffe4e6 100%);
            border: 1px solid #fecdd3;
            border-radius: 8px;
            padding: 8px 8px 10px;
            margin-bottom: 8px;
        }
        .sm-band-healthy {
            background: #f7fef9;
            border: 1px solid #bbf7d0;
            border-radius: 10px;
            padding: 10px 10px 12px;
        }
        /* Samsung page: less wide sidebar, main column gets more width for Splunk */
        .sm-outer-grid {
            display: grid;
            grid-template-columns: minmax(188px, 21%) minmax(0, 1fr);
            gap: 14px;
            margin-bottom: 16px;
            align-items: start;
        }
        .sm-page-samsung .sm-sidebar-col {
            display: flex;
            flex-direction: column;
            gap: 10px;
            min-width: 0;
        }
        /* Samsung tab: service tiles (left), Splunk embed (right) — bias toward charts */
        .sm-samsung-splunk-layout {
            display: grid;
            grid-template-columns: minmax(200px, 27%) minmax(340px, 1fr);
            gap: 12px;
            align-items: start;
            width: 100%;
        }
        @media (max-width: 1200px) {
            .sm-samsung-splunk-layout {
                grid-template-columns: 1fr;
            }
            .sm-splunk-embed-side {
                position: static !important;
            }
        }
        .sm-splunk-embed-side { min-width: 0; }
        .sm-samsung-splunk-layout .samsung-spl-iframe { vertical-align: top; }
        /* Command center: empty attention queue = one compact row (no full table chrome) */
        .cc-strip--samsung-attn-empty .cc-attention-table thead {
            display: none;
        }
        .cc-strip--samsung-attn-empty .cc-attention-table {
            margin-top: 4px;
        }
        .cc-strip--samsung-attn-empty .cc-attention-table tbody td {
            padding: 6px 10px;
            border-bottom: none;
            font-size: 10px;
        }
        .cc-strip.cc-strip--compact-samsung {
            padding: 10px 12px;
            margin-bottom: 12px;
        }
        .cc-strip--compact-samsung .cc-kpi-grid {
            margin-bottom: 8px;
            gap: 6px;
        }
        .cc-strip--compact-samsung .cc-kpi {
            padding: 8px 10px;
        }
        .cc-strip--compact-samsung .cc-kpi-val {
            font-size: 22px;
        }
        </style>
        <div style='max-width: 100%; margin: 0; padding: 0;'>
        """
    
    try:
        services, environments = _sm_resolve_services_and_environments(environment)
    except ValueError:
        return f"<p style='color: #dc2626;'>⚠️ Error: Invalid environment '{html.escape(str(environment))}'</p>"

    if only_dd_env:
        environments = [only_dd_env]

    dd_api_key = os.getenv("DATADOG_API_KEY")
    dd_app_key = os.getenv("DATADOG_APP_KEY")
    dd_site = os.getenv("DATADOG_SITE", "arlo.datadoghq.com")
    pd_api_key = os.getenv("PAGERDUTY_API_TOKEN")

    if not dd_api_key or not dd_app_key:
        return "<p style='color: #dc2626;'>⚠️ Error: Datadog credentials not configured</p>"

    pd_counts = {"triggered": 0, "acknowledged": 0, "resolved": 0}
    pd_incidents = []
    arlo_services_status = []

    need_apm = frag in (None, "env_column", "finalize") or all_statuses_override is not None
    need_pd_arlo = frag in (None, "sidebar_fast", "finalize")
    if frag == "bootstrap":
        need_apm = False
        need_pd_arlo = False

    if need_apm and all_statuses_override is not None:
        all_statuses = list(all_statuses_override)
    elif need_apm and frag != "finalize":
        print(f"📡 Fetching health for {len(services)} services across {len(environments)} environment(s): {environments}...")
        all_statuses = _sm_fetch_parallel_service_health(
            services,
            environments,
            dd_api_key,
            dd_app_key,
            dd_site,
            from_time,
            current_time,
            int(timerange),
            force_refresh,
        )
        if incr_session_id and only_dd_env:
            sess = _sm_incr_sessions.get(incr_session_id)
            if sess is not None:
                with _sm_incr_lock:
                    sess.setdefault("statuses_by_env", {})[only_dd_env] = list(all_statuses)
    elif frag == "finalize" and incr_session_id:
        all_statuses = _sm_incr_merged_statuses(incr_session_id)
    else:
        all_statuses = []

    if frag == "env_column" and only_dd_env and incr_session_id:
        sess = _sm_incr_sessions.get(incr_session_id) or {}
        pd_incidents = list(sess.get("pd_incidents") or [])
        _sm_apply_pagerduty_correlation(all_statuses, services, [only_dd_env], environment, pd_incidents)
        all_statuses = _sm_apply_wall_display_statuses(
            all_statuses,
            services,
            environment,
            only_dd_env,
            dd_api_key=dd_api_key,
            dd_app_key=dd_app_key,
            dd_site=dd_site,
        )
        col = _sm_render_dd_env_column_html(
            only_dd_env, all_statuses, environment, environments, timerange=timerange
        )
        return (
            f'<div class="sm-inc-env-slot" data-sm-dd-env="{html.escape(only_dd_env, quote=True)}">{col}</div>'
        )

    if need_pd_arlo and frag in ("sidebar_fast", None):
        print(f"🔄 Fetching PagerDuty and Arlo status (sequential, resilient)...")
        if pd_api_key:
            try:
                pd_counts, pd_incidents = get_pagerduty_status_counts(pd_api_key, force_refresh)
            except Exception as e:
                print(f"⚠️ Error fetching PagerDuty status: {e}")
        else:
            print(f"⚠️ PagerDuty API key not available")
        time.sleep(0.25)
        try:
            arlo_services_status = get_arlo_services_status(force_refresh)
            print(f"🎯 Arlo: {len(arlo_services_status)} core services")
        except Exception as e:
            print(f"⚠️ Error fetching Arlo status: {e}")
        if incr_session_id:
            sess = _sm_incr_sessions.get(incr_session_id)
            if sess is not None:
                with _sm_incr_lock:
                    sess["pd_counts"] = dict(pd_counts)
                    sess["pd_incidents"] = list(pd_incidents)
                    sess["arlo_services_status"] = list(arlo_services_status)
        if frag == "sidebar_fast":
            return _sm_incr_sidebar_fast_html(pd_counts, arlo_services_status, int(timerange))
    elif frag == "env_column" and incr_session_id:
        sess = _sm_incr_sessions.get(incr_session_id) or {}
        pd_counts = dict(sess.get("pd_counts") or pd_counts)
        pd_incidents = list(sess.get("pd_incidents") or [])
        arlo_services_status = list(sess.get("arlo_services_status") or [])
    elif frag == "finalize" and incr_session_id:
        sess = _sm_incr_sessions.get(incr_session_id) or {}
        pd_counts = dict(sess.get("pd_counts") or pd_counts)
        pd_incidents = list(sess.get("pd_incidents") or [])
        arlo_services_status = list(sess.get("arlo_services_status") or [])

    if need_apm and frag != "env_column":
        _sm_apply_pagerduty_correlation(all_statuses, services, environments, environment, pd_incidents)
        if environment and all_statuses and environments:
            all_statuses = _sm_apply_wall_display_statuses(
                all_statuses,
                services,
                environment,
                environments[0],
                dd_api_key=dd_api_key,
                dd_app_key=dd_app_key,
                dd_site=dd_site,
            )

    total_no_dd = sum(1 for s in all_statuses if s.get('status') in ('inactive', 'unknown'))
    if total_no_dd:
        print(
            f"📋 {total_no_dd} service(s) with no APM signal (inactive=no hits; unknown=query errors/timeouts) "
            f"— not shown in service bands"
        )
    
    # EKS cluster lookup: off by default (heavy); STATUS_MONITOR_DASHBOARD_ATTACH_EKS=1 to re-enable.
    cluster_service_map = {}
    if is_full and _status_monitor_dashboard_attach_eks():
        eks_tr_h = max(1, int(timerange))
        operational_ct = sum(
            1 for s in all_statuses if s.get("status") in ("healthy", "warning", "critical")
        )
        skip_eks_ct = sum(1 for s in all_statuses if s.get("status") in ("inactive", "unknown"))
        print(
            f"☸️  EKS cluster lookup: {operational_ct} operational service(s), "
            f"{skip_eks_ct} skipped (inactive/unknown), timerange={eks_tr_h}h (parallel)..."
        )

        def fetch_clusters_for_service(status_obj):
            """Fetch EKS clusters for a single service"""
            service_name = status_obj["service"]
            service_env = status_obj["environment"]
            if status_obj.get("status") in ("inactive", "unknown"):
                return (status_obj, [], service_name, service_env, None)
            cluster_names, from_db = _resolve_eks_cluster_names(
                service_name, service_env, eks_tr_h, force_refresh
            )
            return (status_obj, cluster_names, service_name, service_env, from_db)

        eks_attached = 0
        eks_cluster_rows_db = 0
        eks_cluster_rows_live = 0
        with ThreadPoolExecutor(max_workers=STATUS_MONITOR_EKS_MAX_WORKERS) as executor:
            futures = [executor.submit(fetch_clusters_for_service, status_obj) for status_obj in all_statuses]

            for future in as_completed(futures):
                try:
                    status_obj, cluster_names, service_name, service_env, from_db = future.result()
                    if from_db is True:
                        eks_cluster_rows_db += 1
                    elif from_db is False:
                        eks_cluster_rows_live += 1

                    if cluster_names:
                        status_obj["eks_clusters"] = cluster_names
                        status_obj["eks_cluster_count"] = len(cluster_names)
                        eks_attached += 1

                        for cluster_name in cluster_names:
                            if cluster_name not in cluster_service_map:
                                cluster_service_map[cluster_name] = []
                            cluster_service_map[cluster_name].append(f"{service_name} ({service_env})")

                except Exception as e:
                    print(f"   ❌ Error fetching clusters: {e}")
        print(
            f"☸️  EKS: kube_cluster_name resolved for {eks_attached} / {operational_ct} operational service(s) "
            f"(rows from SQLite={eks_cluster_rows_db}, live Datadog={eks_cluster_rows_live})."
        )

        if cluster_service_map:
            print(f"☸️  EKS Summary (All Environments):")
            for cluster_name, services in sorted(cluster_service_map.items()):
                print(f"   • {cluster_name}: {len(services)} services")
    elif is_full:
        print(
            "☸️  EKS cluster lookup skipped (status monitor dashboards) — "
            "set STATUS_MONITOR_DASHBOARD_ATTACH_EKS=1 to re-enable."
        )
    
    # Get US Infra Exceptions count (DISABLED)
    # print(f"🏗️ Fetching US Infra Exceptions...")
    # infra_exceptions_count, infra_exceptions_details = get_splunk_infra_exceptions(timerange)
    # print(f"🚨 US Infra Exceptions: {infra_exceptions_count} found")
    infra_exceptions_count = 0  # Disabled temporarily
    infra_exceptions_details = []

    from tools.splunk_tool import splunk_outliers_monitor_payload, splunk_p0_default_timerange_hours

    spl_data: dict = {
        "success": False,
        "tools": [],
        "error": None,
        "timerange_hours": splunk_p0_default_timerange_hours(),
    }
    if frag in ("sidebar_splunk",) or (is_full and not skip_splunk):
        try:
            spl_data = splunk_outliers_monitor_payload()
        except Exception as e:
            print(f"⚠️ Splunk outliers (status monitor sidebar): {e}")
            spl_data = {
                "success": False,
                "tools": [],
                "error": str(e),
                "timerange_hours": splunk_p0_default_timerange_hours(),
            }

    if frag == "sidebar_splunk":
        return _splunk_p0_sidebar_widget_html(spl_data)

    # Build dashboard
    # Get current time (will be replaced by client-side timezone)
    current_dt = datetime.utcnow()
    
    # Dashboard title based on environment mode
    if environment == 'samsung':
        dashboard_title = "Samsung"
        dashboard_subtitle = "Real-time health status for Samsung partner network services"
    elif environment == 'adt':
        dashboard_title = "ADT"
        dashboard_subtitle = "Real-time health status for ADT partner network services"
    elif environment == 'redmetrics-us':
        dashboard_title = "🇺🇸 RED Metrics US"
        dashboard_subtitle = "Real-time health status for US region services"
    elif environment == 'qa':
        dashboard_title = "QA"
        dashboard_subtitle = "Real-time health for env:qa (platform / cluster services)"
    elif environment:
        dashboard_title = f"📊 {environment.upper()} Status Monitor"
        dashboard_subtitle = f"Real-time health status for {environment}"
    else:
        dashboard_title = "📊 Service Status Monitor"
        dashboard_subtitle = "Real-time health status across all environments"
    
    if not skip_chrome:
        output += f"""
    <div style='background: #ffffff; padding: 8px 20px; border-bottom: 1px solid #e5e7eb; margin: -24px -24px 12px -24px;'>
        <div style='display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px;'>
            <div>
                <h1 style='margin: 0; color: #111827; font-size: 18px; font-weight: 700; margin-bottom: 2px; letter-spacing: -0.02em;'>
                    {dashboard_title.replace('📊 ', '')}
                </h1>
                <p style='margin: 0; font-size: 12px; color: #6b7280; font-weight: 500; letter-spacing: -0.01em;'>
                    {dashboard_subtitle} <span style='margin-left: 12px; font-size: 11px; color: #374151; font-weight: 600;'>📊 Baseline: 7-day avg | Threshold: &gt;5%↑1%</span>
                </p>
            </div>
            
            <div style='display: flex; align-items: center; gap: 8px;'>
                <div style='display: flex; align-items: center; gap: 6px;'>
                    <label for='timerange' style='font-size: 12px; font-weight: 600; color: #374151;'>Time Range:</label>
                    <select id='timerange' style='padding: 5px 10px; border: 1px solid #d1d5db; border-radius: 5px; font-size: 12px; cursor: pointer; background: #ffffff; color: #111827; outline: none; font-weight: 500;'>
                        <option value='1' selected>Last 1 hour</option>
                        <option value='2'>Last 2 hours</option>
                        <option value='4'>Last 4 hours</option>
                        <option value='8'>Last 8 hours</option>
                        <option value='12'>Last 12 hours</option>
                        <option value='24'>Last 24 hours</option>
                    </select>
                </div>
                
                <button onclick='loadDashboardIncremental()' style='padding: 5px 12px; background: #0095da; color: #ffffff; border: none; border-radius: 5px; font-size: 12px; font-weight: 700; cursor: pointer; transition: all 0.15s ease-in-out;' onmouseover="this.style.background='#0088c7'" onmouseout="this.style.background='#0095da'">
                    🔄 Refresh
                </button>
                
                <button onclick='clearCacheAndReload()' style='padding: 5px 12px; background: #dc2626; color: #ffffff; border: none; border-radius: 5px; font-size: 12px; font-weight: 700; cursor: pointer; transition: all 0.15s ease-in-out;' onmouseover="this.style.background='#b91c1c'" onmouseout="this.style.background='#dc2626'" title='Clear cache and force fresh data from Datadog'>
                    🧹 Force Refresh
                </button>
                
                <div style='text-align: right; padding-left: 8px; border-left: 1px solid #e5e7eb;'>
                    <div style='font-size: 9px; color: #9ca3af; font-weight: 500;'>Last updated</div>
                    <div style='font-size: 12px; font-weight: 700; color: #111827;'>--:--:--</div>
                    <div style='font-size: 9px; color: #9ca3af; font-weight: 500;'>-----xx-xx</div>
                </div>
            </div>
        </div>
    </div>
    """
    
    # Main layout container (Samsung: narrower sidebar + class hooks for compact CSS)
    if environment == "samsung":
        output += """
    <!-- Main Container: Sidebar + Content -->
    <div class="sm-page-samsung sm-outer-grid">
        <div class="sm-sidebar-col">
    """
    else:
        output += """
    <div style='display: grid; grid-template-columns: 260px 1fr; gap: 24px; margin-bottom: 20px;'>
        <div style='display: flex; flex-direction: column; gap: 16px;'>
    """

    if frag == "bootstrap":
        ncols = len(environments)
        grid_tpl = "1fr" if ncols <= 1 else f"repeat({ncols}, 1fr)"
        env_slots = "".join(
            f'<div class="sm-inc-env-slot sm-inc-pulse" data-sm-dd-env="{html.escape(e, quote=True)}" '
            f'id="sm-inc-env-{html.escape(e, quote=True)}">'
            f'<div class="sm-inc-loading" style="padding:16px;color:#64748b;font-size:11px;font-weight:600;">'
            f"Loading {html.escape(e)} services…</div></div>"
            for e in environments
        )
        samsung_aside = ""
        if environment == "samsung":
            samsung_aside = (
                f'<div id="sm-inc-samsung-aside" class="sm-inc-pulse">'
                f"{_samsung_splunk_embed_aside_html()}</div>"
            )
            main_inner = f"""
        <div class="sm-samsung-splunk-layout"><div class="sm-samsung-groups" style="min-width:0;">
        <div id="sm-inc-env-grid">{env_slots}</div></div>{samsung_aside}</div>"""
        else:
            main_inner = f'<div id="sm-inc-env-grid" style="display:grid;grid-template-columns:{grid_tpl};gap:3px;">{env_slots}</div>'
        return (
            output
            + """
        <div id="sm-inc-status">Loading…</div>
        <div id="sm-inc-summary" class="sm-inc-pulse" style="background:#fff;padding:12px;border-radius:10px;border:1px solid #e5e7eb;color:#64748b;font-size:11px;font-weight:600;">Loading summary…</div>
        <div id="sm-inc-sidebar-fast" class="sm-inc-pulse" style="background:#fff;padding:12px;border-radius:10px;border:1px solid #e5e7eb;color:#64748b;font-size:11px;font-weight:600;margin-top:12px;">Loading PagerDuty…</div>
        <div id="sm-inc-sidebar-splunk" class="sm-inc-pulse" style="background:#fff;padding:12px;border-radius:10px;border:1px solid #e5e7eb;color:#64748b;font-size:11px;font-weight:600;margin-top:12px;">Loading Splunk P0…</div>
        </div>
        <div id="sm-inc-main">
        <div id="sm-inc-command-center" class="sm-inc-pulse" style="background:#f8fafc;padding:16px;border-radius:10px;border:1px solid #e2e8f0;color:#64748b;font-size:11px;font-weight:600;margin-bottom:12px;">Loading command center…</div>
        """
            + main_inner
            + """
        </div>
    </div>
    </div>
        """
        )
    
    # PagerDuty Status Widget - use counts from API
    pd_triggered = pd_counts["triggered"]
    pd_acknowledged = pd_counts["acknowledged"]
    pd_resolved = pd_counts["resolved"]
    
    # Determine background color and blink behavior based on status
    if pd_triggered > 0:
        pd_bg_color = '#dc2626'  # Red
        pd_status_icon = '🔴'
        pd_status_text = 'CRITICAL'
        pd_blink_class = 'pd-status-blink'
    elif pd_acknowledged > 0:
        pd_bg_color = '#f59e0b'  # Yellow/Orange
        pd_status_icon = '🟡'
        pd_status_text = 'WARNING'
        pd_blink_class = 'pd-status-blink'
    else:
        pd_bg_color = '#10b981'  # Green
        pd_status_icon = '🟢'
        pd_status_text = 'HEALTHY'
        pd_blink_class = ''  # No blink when healthy

    pd_sem_link_esc = html.escape(_sm_pagerduty_external_incidents_url(), quote=True)

    spl_p0_widget_html = _splunk_p0_sidebar_widget_html(spl_data)

    # Calculate summary statistics (all configured services, including no-telemetry)
    total_services = len(all_statuses)
    total_healthy = sum(1 for s in all_statuses if s['status'] == 'healthy')
    total_warning = sum(1 for s in all_statuses if s['status'] == 'warning')
    total_critical = sum(1 for s in all_statuses if s['status'] == 'critical')
    total_inactive = sum(1 for s in all_statuses if s['status'] == 'inactive')
    total_unknown = sum(1 for s in all_statuses if s['status'] == 'unknown')
    total_no_telemetry = total_inactive + total_unknown
    total_listed_ui = total_healthy + total_warning + total_critical
    total_requests = sum(s['requests'] for s in all_statuses)
    total_errors = sum(s['errors'] for s in all_statuses)
    overall_error_rate = (total_errors / total_requests * 100) if total_requests > 0 else 0
    
    # Detailed logging of status distribution
    print(f"\n{'='*80}")
    print(f"📊 STATUS SUMMARY for {environment}")
    print(f"{'='*80}")
    print(f"Total Active Services: {total_services}")
    
    if total_services > 0:
        print(f"  ✅ Healthy: {total_healthy} ({total_healthy/total_services*100:.1f}%)")
        print(f"  ⚠️  Warning: {total_warning} ({total_warning/total_services*100:.1f}%)")
        print(f"  🚨 Critical: {total_critical} ({total_critical/total_services*100:.1f}%)")
        print(f"Overall: {total_requests:,} requests, {total_errors:,} errors, {overall_error_rate:.2f}% error rate")
    else:
        print(f"  ⚠️  No services found for this environment")
        print(f"  💡 Check service names and environment tags in Datadog")
    
    # List all critical services with details
    if total_critical > 0:
        print(f"\n🚨 CRITICAL SERVICES ({total_critical}):")
        critical_services = [s for s in all_statuses if s['status'] == 'critical']
        for svc in critical_services:
            error_rate = (svc['errors'] / svc['requests'] * 100) if svc['requests'] > 0 else 0
            reasons = []
            if error_rate > 5:
                reasons.append(f"Error: {error_rate:.2f}%")
            if svc.get('traffic_drop'):
                reasons.append(f"Traffic Drop: {svc.get('traffic_variance', 'N/A')}")
            if svc.get('pd_incident'):
                reasons.append("PagerDuty Alert")
            reason_str = " | ".join(reasons) if reasons else "Unknown"
            print(f"  • {svc['service']} ({svc['environment']}): {reason_str}")
            print(f"    Requests: {svc['requests']:,}, Errors: {svc['errors']:,}")
    
    # List all warning services with details
    if total_warning > 0:
        print(f"\n⚠️  WARNING SERVICES ({total_warning}):")
        warning_services = [s for s in all_statuses if s['status'] == 'warning']
        for svc in warning_services:
            error_rate = (svc['errors'] / svc['requests'] * 100) if svc['requests'] > 0 else 0
            reasons = []
            if error_rate > 1:
                reasons.append(f"Error: {error_rate:.2f}%")
            if svc.get('high_latency') and not svc.get('traffic_drop'):
                reasons.append("High Latency")
            reason_str = " | ".join(reasons) if reasons else "Unknown"
            print(f"  • {svc['service']} ({svc['environment']}): {reason_str}")
            print(f"    Requests: {svc['requests']:,}, Errors: {svc['errors']:,}")
    
    # List healthy services (names only)
    if total_healthy > 0:
        print(f"\n✅ HEALTHY SERVICES ({total_healthy}):")
        healthy_services = [s['service'] for s in all_statuses if s['status'] == 'healthy']
        print(f"  {', '.join(healthy_services)}")
    
    print(f"{'='*80}\n")
    
    # Command center strip: KPIs, ERR% delta vs last snapshot, attention queue
    snap_env = environment if environment else 'all'
    prev_err_delta_html = "<span class='cc-delta-flat'>vs last: n/a</span>"
    try:
        hist = get_dashboard_history(environment=snap_env, hours=72)
        if hist:
            prev = hist[-1]
            prev_rate = float(prev.get('overall_error_rate') or 0)
            delta = overall_error_rate - prev_rate
            if abs(delta) < 0.001:
                prev_err_delta_html = "<span class='cc-delta-flat'>vs last refresh: flat</span>"
            elif delta > 0:
                prev_err_delta_html = f"<span class='cc-delta-up'>▲ {delta:+.2f} pp vs last</span>"
            else:
                prev_err_delta_html = f"<span class='cc-delta-down'>▼ {abs(delta):.2f} pp vs last</span>"
    except Exception:
        pass
    
    def _attention_sort_key(s):
        tier = 0 if s['status'] == 'critical' else 1 if s['status'] == 'warning' else 2
        return (tier, -s['error_rate'], -s['errors'])
    
    bad_svcs = [s for s in all_statuses if s['status'] in ('critical', 'warning')]
    bad_svcs.sort(key=_attention_sort_key)
    seen_keys = set()
    attention_merged = []
    for s in bad_svcs:
        key = (s['service'], s['environment'])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        attention_merged.append(s)
        if len(attention_merged) >= 14:
            break
    
    # Attention queue: warning + critical only (no healthy "watch" rows); no EKS cluster column.
    show_clusters_col = _status_monitor_dashboard_attach_eks()
    attn_missing_clusters = (
        [s for s in attention_merged if not s.get('eks_clusters')] if show_clusters_col else []
    )
    if attn_missing_clusters:
        def _clusters_for_attention_row(status_obj):
            service_name = status_obj['service']
            service_env = status_obj['environment']
            names, _db = _resolve_eks_cluster_names(service_name, service_env, 1, force_refresh)
            return names

        with ThreadPoolExecutor(max_workers=min(12, len(attn_missing_clusters))) as attn_cl_ex:
            fut_map = {
                attn_cl_ex.submit(_clusters_for_attention_row, row): row
                for row in attn_missing_clusters
            }
            for fut in as_completed(fut_map):
                row = fut_map[fut]
                try:
                    names = fut.result()
                    if names:
                        row['eks_clusters'] = names
                        row['eks_cluster_count'] = len(names)
                except Exception:
                    pass
    
    needs_attention_n = total_warning + total_critical
    pd_open = pd_triggered + pd_acknowledged
    dd_site_cc = os.getenv('DD_SITE', 'datadoghq.com')
    splunk_mqtt_q = (
        'index="*prod" sourcetype="kube:container:backend-log-server" LL=ERROR '
        '"DevicesSubscriptionsManagerMQTT" "Sequence timeout" earliest=-24h'
    )
    splunk_mqtt_url = f"https://arlo.splunkcloud.com/en-US/app/search/search?q={quote(splunk_mqtt_q)}"
    
    if total_requests >= 1_000_000:
        req_kpi_disp = f"{total_requests / 1e6:.2f}M"
    elif total_requests >= 1000:
        req_kpi_disp = f"{total_requests / 1e3:.1f}K"
    else:
        req_kpi_disp = f"{total_requests:,}"
    
    def _attention_reason_text(s):
        parts = []
        if s.get('pd_incident'):
            parts.append('PD incident')
        if s.get('traffic_drop'):
            parts.append('Traffic drop vs 7d')
        if s.get('high_latency'):
            parts.append('High latency (APM)')
        er = float(s.get('error_rate') or 0)
        st = s.get('status')
        if st == 'critical' and er > 5:
            parts.append('Error rate >5%')
        elif st == 'warning' and er > 1:
            parts.append('Error rate >1%')
        tv = s.get('traffic_variance')
        if not s.get('traffic_drop') and tv is not None and abs(tv) >= 12:
            parts.append(f'Traffic {tv:+.0f}% vs 7d')
        if not parts:
            if st == 'critical':
                parts.append('Critical (APM)')
            elif st == 'warning':
                parts.append('Warning (APM)')
            else:
                parts.append('Needs review')
        return html.escape(' · '.join(parts))

    attention_colspan = "8" if show_clusters_col else "7"
    cc_clusters_th = "<th>Clusters</th>" if show_clusters_col else ""
    attention_rows_html = ""
    if not attention_merged:
        attention_rows_html = (
            f"<tr><td colspan='{attention_colspan}' style='color:#64748b;padding:12px;'>"
            "No warning or critical services in this view.</td></tr>"
        )
    else:
        for s in attention_merged:
            st = s['status']
            if st == 'critical':
                pill = "<span class='cc-pill' style='background:#7f1d1d;color:#fecaca;'>Critical</span>"
            elif st == 'warning':
                pill = "<span class='cc-pill' style='background:#78350f;color:#fcd34d;'>Warning</span>"
            else:
                pill = f"<span class='cc-pill' style='background:#475569;color:#e2e8f0;'>{html.escape(st)}</span>"
            tv = s.get('traffic_variance')
            tv_txt = f"{tv:+.0f}% vs 7d" if tv is not None else '—'
            svc_url = (
                f"{datadog_ui_origin(dd_site_cc)}/apm/service/{s['service']}/overview?env={s['environment']}"
            )
            err_val = float(s.get('error_rate') or 0)
            bar_w = min(100.0, max(0.0, err_val))
            if err_val > 0 and bar_w < 2.0:
                bar_w = 2.0
            if err_val <= 0.3:
                bar_grad = 'linear-gradient(90deg,#4ade80,#22c55e)'
            elif err_val <= 1.0:
                bar_grad = 'linear-gradient(90deg,#fde047,#eab308)'
            else:
                bar_grad = 'linear-gradient(90deg,#f87171,#dc2626)'
            cluster_td = ""
            if show_clusters_col:
                clusters = [c for c in (s.get('eks_clusters') or []) if _is_meaningful_kube_cluster_name(c)]
                if clusters:
                    opt_lines = []
                    for c in clusters:
                        opt_lines.append(
                            f'<option value="{html.escape(c, quote=True)}">{html.escape(c)}</option>'
                        )
                    cluster_cell = (
                        f"<select class='cc-cluster-select' "
                        f"title='EKS clusters (kube_cluster_name) with traffic for this service' "
                        f"aria-label='Cluster names for {html.escape(s['service'], quote=True)}'>"
                        f"{''.join(opt_lines)}</select>"
                    )
                else:
                    cluster_cell = (
                        "<span style='color:#94a3b8;font-size:10px;font-weight:600;'>—</span>"
                    )
                cluster_td = f'<td style="vertical-align:middle;">{cluster_cell}</td>'
            err_cell = (
                f"<div style='display:flex;align-items:center;gap:8px;max-width:96px;'>"
                f"<span style='font-weight:800;color:#0f172a;min-width:42px;flex-shrink:0;font-size:11px;'>"
                f"{err_val:.2f}%</span>"
                f"<div class='cc-err-bar-wrap'><div class='cc-err-bar-track' title='Error %'>"
                f"<div class='cc-err-bar-fill' style='width:{bar_w:.1f}%;background:{bar_grad};'></div>"
                f"</div></div></div>"
            )
            reason_cell = _attention_reason_text(s)
            dd_alerts_cell = _sm_attention_dd_alerts_cell_html(s, dd_site_cc)
            attention_rows_html += f"""<tr>
                <td>{pill}</td>
                <td style='font-weight:700;color:#0f172a;'><a href="{svc_url}" target="_blank" rel="noopener" style="color:#0284c7;text-decoration:none;">{html.escape(s['service'])}</a></td>
                <td style="color:#475569;">{html.escape(s['environment'])}</td>
                {cluster_td}
                <td style="vertical-align:middle;">{err_cell}</td>
                <td style="font-size:10px;color:#0f172a;font-weight:600;line-height:1.35;max-width:200px;">{reason_cell}</td>
                <td style="vertical-align:top;max-width:220px;">{dd_alerts_cell}</td>
                <td style="color:#64748b;font-size:10px;">{s['requests']:,} req · {tv_txt}</td>
            </tr>"""
    
    err_kpi_color = '#f87171' if overall_error_rate > 1 else '#fbbf24' if overall_error_rate > 0.3 else '#4ade80'
    cc_strip_classes = "cc-strip"
    if environment == "samsung":
        cc_strip_classes += " cc-strip--compact-samsung"
        if not attention_merged:
            cc_strip_classes += " cc-strip--samsung-attn-empty"
    command_center_html = f"""
            <div class="{cc_strip_classes}">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
                    <div>
                        <div style="font-size:11px;font-weight:800;color:#64748b;text-transform:uppercase;letter-spacing:0.12em;">Command center</div>
                        <div style="font-size:15px;font-weight:700;color:#0f172a;margin-top:2px;">At-a-glance</div>
                    </div>
                    <a class="cc-link-btn" href="{splunk_mqtt_url}" target="_blank" rel="noopener" title="iOS MQTT subscribe sequence timeouts (client signal, may not move API ERR%)">Splunk: iOS MQTT timeouts</a>
                </div>
                <div class="cc-kpi-grid">
                    <div class="cc-kpi">
                        <div class="cc-kpi-val" style="color:{'#f87171' if needs_attention_n else '#4ade80'};">{needs_attention_n}</div>
                        <div class="cc-kpi-lbl">Needs attention</div>
                        <div style="font-size:9px;color:#64748b;margin-top:4px;">warning + critical</div>
                    </div>
                    <div class="cc-kpi">
                        <div class="cc-kpi-val" style="color:{'#fbbf24' if pd_open else '#4ade80'};">{pd_open}</div>
                        <div class="cc-kpi-lbl">PagerDuty open</div>
                        <div style="font-size:9px;color:#64748b;margin-top:4px;">triggered + ack</div>
                    </div>
                    <div class="cc-kpi">
                        <div class="cc-kpi-val" style="color:{err_kpi_color};">{overall_error_rate:.2f}%</div>
                        <div class="cc-kpi-lbl">Global ERR rate</div>
                        <div style="margin-top:4px;">{prev_err_delta_html}</div>
                    </div>
                    <div class="cc-kpi">
                        <div class="cc-kpi-val" style="font-size:22px;">{req_kpi_disp}</div>
                        <div class="cc-kpi-lbl">Total requests</div>
                        <div style="font-size:9px;color:#64748b;margin-top:4px;">{timerange}h window</div>
                    </div>
                </div>
                <div style="font-size:10px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:6px;">Attention queue (warning &amp; critical)</div>
                <table class="cc-attention-table">
                    <thead><tr><th>Status</th><th>Service</th><th>Env</th>{cc_clusters_th}<th>ERR%</th><th>Reason</th><th>DD alerts</th><th>Context</th></tr></thead>
                    <tbody>{attention_rows_html}</tbody>
                </table>
            </div>
    """
    if frag == "finalize":
        summary_html = f"""
            <div style='background: #ffffff; padding: 12px; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); border: 1px solid #e5e7eb;'>
                <div style='margin-bottom: 10px;'><h3 style='font-size: 14px; font-weight: 700; color: #111827; margin: 0;'>📈 Summary</h3></div>
                <div style='display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px;'>
                    <div style='display: flex; flex-direction: column; gap: 8px;'>
                        <div style='display: flex; align-items: center; gap: 6px;'>
                            <div style='width: 10px; height: 10px; background: #10b981; border-radius: 2px;'></div>
                            <div><div style='color: #111827; font-weight: 600; font-size: 11px;'>Healthy</div>
                            <div style='color: #10b981; font-weight: 700; font-size: 16px;'>{total_healthy}</div></div>
                        </div>
                        <div style='display: flex; align-items: center; gap: 6px;'>
                            <div style='width: 10px; height: 10px; background: #f59e0b; border-radius: 2px;'></div>
                            <div><div style='color: #111827; font-weight: 600; font-size: 11px;'>Warning</div>
                            <div style='color: #f59e0b; font-weight: 700; font-size: 16px;'>{total_warning}</div></div>
                        </div>
                    </div>
                    <div style='display: flex; flex-direction: column; gap: 8px;'>
                        <div style='display: flex; align-items: center; gap: 6px;'>
                            <div style='width: 10px; height: 10px; background: #dc2626; border-radius: 2px;'></div>
                            <div><div style='color: #111827; font-weight: 600; font-size: 11px;'>Critical</div>
                            <div style='color: #dc2626; font-weight: 700; font-size: 16px;'>{total_critical}</div></div>
                        </div>
                        <div style='display: flex; align-items: center; gap: 6px;'>
                            <div style='width: 10px; height: 10px; background: #e5e7eb; border-radius: 2px;'></div>
                            <div><div style='color: #111827; font-weight: 600; font-size: 11px;'>Total listed</div>
                            <div style='color: #6b7280; font-weight: 700; font-size: 16px;'>{total_listed_ui}</div></div>
                        </div>
                    </div>
                    <div style='background: #f9fafb; padding: 8px; border-radius: 6px; display: flex; flex-direction: column; gap: 6px;'>
                        <div style='display: flex; justify-content: space-between;'><span style='font-size:10px;color:#6b7280;'>REQ:</span><span style='font-weight:700;font-size:11px;'>{total_requests:,}</span></div>
                        <div style='display: flex; justify-content: space-between;'><span style='font-size:10px;color:#6b7280;'>ERR:</span><span style='font-weight:700;font-size:11px;color:#dc2626;'>{total_errors:,}</span></div>
                        <div style='display: flex; justify-content: space-between;'><span style='font-size:10px;color:#6b7280;'>ERR%:</span><span style='font-weight:700;font-size:11px;'>{overall_error_rate:.2f}%</span></div>
                    </div>
                </div>
            </div>
        """
        return (
            summary_html
            + f"""
    <script>
        window.chartData = {{
            healthy: {total_healthy},
            warning: {total_warning},
            critical: {total_critical},
            total: {total_listed_ui}
        }};
        if (window.initializePieChart) {{
            window.initializePieChart(window.chartData);
        }}
    </script>
    """
            + command_center_html
        )
    # First: Overall Summary
    if is_full:
        output += f"""
            <!-- Overall Summary -->
            <div style='background: #ffffff; padding: 12px; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04); border: 1px solid #e5e7eb;'>
                <div style='margin-bottom: 10px;'>
                    <h3 style='font-size: 14px; font-weight: 700; color: #111827; margin: 0; letter-spacing: -0.02em;'>📈 Summary</h3>
                </div>
                
                <!-- 3 Column Layout: Status Labels + Metrics -->
                <div style='display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px;'>
                    <!-- Column 1: Healthy & Warning -->
                    <div style='display: flex; flex-direction: column; gap: 8px;'>
                        <div style='display: flex; align-items: center; gap: 6px;'>
                            <div style='width: 10px; height: 10px; background: #10b981; border-radius: 2px; flex-shrink: 0;'></div>
                            <div style='flex: 1; min-width: 0;'>
                                <div style='color: #111827; font-weight: 600; font-size: 11px; letter-spacing: -0.01em;'>Healthy</div>
                                <div style='color: #10b981; font-weight: 700; font-size: 16px;'>{total_healthy}</div>
                            </div>
                        </div>
                        <div style='display: flex; align-items: center; gap: 6px;'>
                            <div style='width: 10px; height: 10px; background: #f59e0b; border-radius: 2px; flex-shrink: 0;'></div>
                            <div style='flex: 1; min-width: 0;'>
                                <div style='color: #111827; font-weight: 600; font-size: 11px; letter-spacing: -0.01em;'>Warning</div>
                                <div style='color: #f59e0b; font-weight: 700; font-size: 16px;'>{total_warning}</div>
                            </div>
                        </div>
                    </div>
                    
                    <!-- Column 2: Critical & Total -->
                    <div style='display: flex; flex-direction: column; gap: 8px;'>
                        <div style='display: flex; align-items: center; gap: 6px;'>
                            <div style='width: 10px; height: 10px; background: #dc2626; border-radius: 2px; flex-shrink: 0;'></div>
                            <div style='flex: 1; min-width: 0;'>
                                <div style='color: #111827; font-weight: 600; font-size: 11px; letter-spacing: -0.01em;'>Critical</div>
                                <div style='color: #dc2626; font-weight: 700; font-size: 16px;'>{total_critical}</div>
                            </div>
                        </div>
                        <div style='display: flex; align-items: center; gap: 6px;'>
                            <div style='width: 10px; height: 10px; background: #e5e7eb; border-radius: 2px; flex-shrink: 0;'></div>
                            <div style='flex: 1; min-width: 0;'>
                                <div style='color: #111827; font-weight: 600; font-size: 11px; letter-spacing: -0.01em;'>Total listed</div>
                                <div style='color: #6b7280; font-weight: 700; font-size: 16px;'>{total_listed_ui}</div>
                            </div>
                        </div>
                    </div>
                    
                    <!-- Column 3: Metrics -->
                    <div style='background: #f9fafb; padding: 8px; border-radius: 6px; display: flex; flex-direction: column; justify-content: center; gap: 6px;'>
                        <div style='display: flex; justify-content: space-between;'>
                            <span style='color: #6b7280; font-weight: 500; font-size: 10px;'>REQ:</span>
                            <span style='color: #111827; font-weight: 700; font-size: 11px; letter-spacing: -0.01em;'>{total_requests:,}</span>
                        </div>
                        <div style='display: flex; justify-content: space-between;'>
                            <span style='color: #6b7280; font-weight: 500; font-size: 10px;'>ERR:</span>
                            <span style='color: #dc2626; font-weight: 700; font-size: 11px; letter-spacing: -0.01em;'>{total_errors:,}</span>
                        </div>
                        <div style='display: flex; justify-content: space-between;'>
                            <span style='color: #6b7280; font-weight: 500; font-size: 10px;'>ERR%:</span>
                            <span style='color: {"#dc2626" if overall_error_rate > 1 else "#10b981"}; font-weight: 700; font-size: 11px; letter-spacing: -0.01em;'>{overall_error_rate:.2f}%</span>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- PagerDuty Status -->
            <div style='background: #ffffff; padding: 16px; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04); border: 1px solid #e5e7eb;'>
                <div style='display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px;flex-wrap:wrap;'>
                    <h3 style='font-size: 15px; font-weight: 700; color: #111827; margin: 0; letter-spacing: -0.02em;'>🚨 PagerDuty</h3>
                    <a href="{pd_sem_link_esc}" target="_blank" rel="noopener noreferrer" title="Open PagerDuty external status (incidents)" style="font-size:10px;font-weight:800;color:#2563eb;text-decoration:none;white-space:nowrap;">Incidents ↗</a>
                </div>
                <a href="{pd_sem_link_esc}" target="_blank" rel="noopener noreferrer" title="Open PagerDuty incidents" style="text-decoration:none;display:block;border-radius:8px;color:inherit;">
                <div class='{pd_blink_class}' style='display: flex; justify-content: space-between; gap: 12px; padding: 12px; background: {pd_bg_color}; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.1); cursor:pointer;'>
                    <div style='text-align: center; flex: 1;'>
                        <div style='font-size: 24px; font-weight: 700; color: white; letter-spacing: -0.01em;'>{pd_triggered}</div>
                        <div style='font-size: 10px; color: rgba(255,255,255,0.9); font-weight: 600;'>Triggered</div>
                    </div>
                    <div style='text-align: center; flex: 1;'>
                        <div style='font-size: 24px; font-weight: 700; color: white; letter-spacing: -0.01em;'>{pd_acknowledged}</div>
                        <div style='font-size: 10px; color: rgba(255,255,255,0.9); font-weight: 600;'>Ack</div>
                    </div>
                    <div style='text-align: center; flex: 1;'>
                        <div style='font-size: 24px; font-weight: 700; color: white; letter-spacing: -0.01em;'>{pd_resolved}</div>
                        <div style='font-size: 10px; color: rgba(255,255,255,0.9); font-weight: 600;'>Resolved</div>
                    </div>
                </div>
                </a>
            </div>

            {spl_p0_widget_html}
            
            <!-- Arlo Platform Status -->
            <div style='background: #ffffff; padding: 16px; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04); border: 1px solid #e5e7eb;'>
                <div style='margin-bottom: 12px;'>
                    <h3 style='font-size: 15px; font-weight: 700; color: #111827; margin: 0; letter-spacing: -0.02em;'>🎯 Arlo Platform</h3>
                </div>
                <div style='display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px;'>
    """
    
    if arlo_services_status:
        for service in arlo_services_status:
            service_name = service['name']
            status = service['status']
            status_text = service.get('status_text', 'Unknown')
            
            # Color based on status
            if status == 'critical':
                bg_color = '#dc2626'  # Red
            elif status == 'warning':
                bg_color = '#f59e0b'  # Orange
            else:
                bg_color = '#10b981'  # Green (default)
            
            # Shorter service names
            short_name = service_name.replace('Live ', '').replace('Video ', '')
            
            output += f"""
                    <div style='background: {bg_color}; padding: 7px 8px; border-radius: 5px; text-align: center;'>
                        <div style='font-size: 10px; color: white; font-weight: 700; line-height: 1.2; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; letter-spacing: -0.01em;'>{short_name}</div>
                    </div>
            """
    else:
        output += """
                    <div style='grid-column: 1 / -1; text-align: center; padding: 12px; color: #6b7280; font-size: 11px; font-weight: 500;'>
                        No data available
                    </div>
        """
    
    output += f"""
                </div>
            </div>
    """
    
    output += f"""
            
            <!-- US Infra Exceptions -->
            <div style='background: white; padding: 6px; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
                <div style='background: #00c853; color: white; padding: 3px 4px; border-radius: 3px; margin-bottom: 4px; text-align: center;'>
                    <span style='font-size: 9px; font-weight: bold;'>🏗️ US Infra Exceptions</span>
                </div>
    """
    
    # Determine color based on exception count
    if infra_exceptions_count > 100:
        infra_bg_color = '#dc2626'  # Red
        infra_icon = '🔴'
        infra_status = 'CRITICAL'
    elif infra_exceptions_count > 50:
        infra_bg_color = '#f59e0b'  # Orange
        infra_icon = '🟡'
        infra_status = 'WARNING'
    elif infra_exceptions_count > 0:
        infra_bg_color = '#fb923c'  # Light orange
        infra_icon = '🟠'
        infra_status = 'ATTENTION'
    else:
        infra_bg_color = '#10b981'  # Green
        infra_icon = '🟢'
        infra_status = 'HEALTHY'
    
    # US Infra Exceptions dashboard URL
    infra_dashboard_url = 'https://arlo.splunkcloud.com/en-GB/app/search/us_infra_exceptions'
    
    output += f"""
                <div style='background: {infra_bg_color}; padding: 5px; border-radius: 3px; color: white; cursor: pointer; transition: all 0.2s;' 
                     onclick="window.open('{infra_dashboard_url}', '_blank')" 
                     title='Click to view US Infra Exceptions dashboard in Splunk'
                     onmouseover="this.style.opacity='0.9'; this.style.transform='scale(1.02)'" 
                     onmouseout="this.style.opacity='1'; this.style.transform='scale(1)'">
                    <div style='text-align: center; margin-bottom: 3px;'>
                        <div style='font-size: 16px;'>{infra_icon}</div>
                        <div style='font-size: 7px; font-weight: bold; opacity: 0.95;'>{infra_status}</div>
                    </div>
                    <div style='text-align: center; border-top: 1px solid rgba(255,255,255,0.3); padding-top: 3px;'>
                        <div style='font-size: 14px; font-weight: bold;'>{infra_exceptions_count:,}</div>
                        <div style='font-size: 6px; opacity: 0.9;'>Exceptions (last {timerange}h)</div>
                    </div>
                </div>
            </div>
    """

    if is_full:
        output += f"""
        </div>
        
        <!-- Main Content Area -->
        <div>
    
    <script>
        // Pie chart: services with APM signal (inactive/unknown omitted from tiles)
        window.chartData = {{
            healthy: {total_healthy},
            warning: {total_warning},
            critical: {total_critical},
            total: {total_listed_ui}
        }};
        
        // Signal that data is ready
        if (window.initializePieChart) {{
            window.initializePieChart(window.chartData);
        }}
    </script>
    {command_center_html}
    """
    
    # Build environment layout (1 or 3 columns depending on mode)
    # Samsung: two columns — service mosaics (left) + Splunk REST viewer (right)
    if environment == 'samsung':
        output += """
    <div class="sm-samsung-splunk-layout">
    <div class="sm-samsung-groups" style="display: flex; flex-direction: column; gap: 12px; min-width:0;">
    """
    else:
        num_cols = len(environments)
        grid_template = f"repeat({num_cols}, 1fr)" if num_cols > 1 else "1fr"
        output += f"""
    <div style='display: grid; grid-template-columns: {grid_template}; gap: 3px;'>
    """

    for env in environments:
        output += _sm_render_dd_env_column_html(
            env, all_statuses, environment, environments, timerange=timerange
        )

    if environment == "samsung":
        output += """
    </div>
    """
        output += _samsung_splunk_embed_aside_html()
        output += """
    </div>
    </div>
    </div>
    </div>
    """
    else:
        output += """
    </div>
    </div>
    </div>
    </div>
    """
    
    # Generate detailed alert summary for logging
    critical_services = [s for s in all_statuses if s['status'] == 'critical']
    warning_services = [s for s in all_statuses if s['status'] == 'warning']
    
    print(
        f"✅ Dashboard generated: {total_services} services "
        f"({total_healthy} healthy, {total_warning} warn, {total_critical} critical, {total_no_telemetry} no telemetry)"
    )
    
    if critical_services:
        print(f"\n🚨 CRITICAL SERVICES ({len(critical_services)}):")
        for svc in critical_services:
            reasons = []
            if svc.get('pd_incident'):
                reasons.append("PagerDuty Alert")
            if svc.get('traffic_drop'):
                reasons.append("Traffic Drop")
            if svc['error_rate'] > 3:
                reasons.append(f"High Error Rate: {svc['error_rate']}%")
            if svc.get('high_latency'):
                reasons.append(f"High Latency: P95={svc.get('p95_latency')}ms")
            print(f"   • {svc['service']} ({svc['environment']}): {', '.join(reasons)}")
    
    if warning_services:
        print(f"\n⚠️ WARNING SERVICES ({len(warning_services)}):")
        for svc in warning_services[:10]:  # Show first 10
            reasons = []
            if svc['error_rate'] > 0.5:
                reasons.append(f"Error Rate: {svc['error_rate']}%")
            if svc.get('high_latency'):
                reasons.append(f"High Latency: P95={svc.get('p95_latency')}ms")
            print(f"   • {svc['service']} ({svc['environment']}): {', '.join(reasons)}")
    
    # Save metrics to database for historical analysis
    try:
        current_timestamp = datetime.utcnow().isoformat()
        
        # Save individual service metrics
        save_service_metrics(all_statuses, current_timestamp)
        
        # Save dashboard snapshot
        dashboard_snapshot = {
            'environment': environment if environment else 'all',
            'total_services': total_services,
            'healthy_count': total_healthy,
            'warning_count': total_warning,
            'critical_count': total_critical,
            'total_requests': total_requests,
            'total_errors': total_errors,
            'overall_error_rate': overall_error_rate,
            'pd_triggered': pd_triggered,
            'pd_acknowledged': pd_acknowledged,
            'pd_resolved': pd_resolved
        }
        save_dashboard_snapshot(dashboard_snapshot, current_timestamp)
    except Exception as e:
        print(f"⚠️ Error saving metrics to database: {e}")
    
    # Cache the result
    if is_full:
        _write_sm_mem_cache(_status_cache, cache_key, output)
    
    # Clean old cache entries (keep only last 5)
    if len(_status_cache) > 5:
        oldest_key = min(_status_cache.keys())
        del _status_cache[oldest_key]
        _mem_cache_saved_at.pop(oldest_key, None)
    
    return output


def _sm_incr_sidebar_fast_html(pd_counts: dict, arlo_services_status: list, timerange: int) -> str:
    """PagerDuty + Arlo + US Infra (Splunk P0 loads in #sm-inc-sidebar-splunk)."""
    pd_triggered = int(pd_counts.get("triggered") or 0)
    pd_acknowledged = int(pd_counts.get("acknowledged") or 0)
    pd_resolved = int(pd_counts.get("resolved") or 0)
    if pd_triggered > 0:
        pd_bg_color, pd_blink_class = "#dc2626", "pd-status-blink"
    elif pd_acknowledged > 0:
        pd_bg_color, pd_blink_class = "#f59e0b", "pd-status-blink"
    else:
        pd_bg_color, pd_blink_class = "#10b981", ""
    pd_sem_link_esc = html.escape(_sm_pagerduty_external_incidents_url(), quote=True)
    infra_exceptions_count = 0
    infra_bg_color, infra_icon, infra_status = "#10b981", "🟢", "HEALTHY"
    infra_dashboard_url = "https://arlo.splunkcloud.com/en-GB/app/search/us_infra_exceptions"
    arlo_cells = ""
    if arlo_services_status:
        for service in arlo_services_status:
            service_name = service["name"]
            status = service["status"]
            bg_color = "#dc2626" if status == "critical" else "#f59e0b" if status == "warning" else "#10b981"
            short_name = service_name.replace("Live ", "").replace("Video ", "")
            arlo_cells += f"""
                    <div style='background: {bg_color}; padding: 7px 8px; border-radius: 5px; text-align: center;'>
                        <div style='font-size: 10px; color: white; font-weight: 700; line-height: 1.2; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;'>{html.escape(short_name)}</div>
                    </div>"""
    else:
        arlo_cells = """<div style='grid-column:1/-1;text-align:center;padding:12px;color:#6b7280;font-size:11px;'>No data available</div>"""
    return f"""
            <div style='background: #ffffff; padding: 16px; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); border: 1px solid #e5e7eb;'>
                <div style='display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px;'>
                    <h3 style='font-size: 15px; font-weight: 700; color: #111827; margin: 0;'>🚨 PagerDuty</h3>
                    <a href="{pd_sem_link_esc}" target="_blank" rel="noopener noreferrer" style="font-size:10px;font-weight:800;color:#2563eb;text-decoration:none;">Incidents ↗</a>
                </div>
                <a href="{pd_sem_link_esc}" target="_blank" rel="noopener noreferrer" style="text-decoration:none;display:block;color:inherit;">
                <div class='{pd_blink_class}' style='display:flex;justify-content:space-between;gap:12px;padding:12px;background:{pd_bg_color};border-radius:8px;color:white;'>
                    <div style='text-align:center;flex:1;'><div style='font-size:24px;font-weight:700;'>{pd_triggered}</div><div style='font-size:10px;opacity:0.9;'>Triggered</div></div>
                    <div style='text-align:center;flex:1;'><div style='font-size:24px;font-weight:700;'>{pd_acknowledged}</div><div style='font-size:10px;opacity:0.9;'>Ack</div></div>
                    <div style='text-align:center;flex:1;'><div style='font-size:24px;font-weight:700;'>{pd_resolved}</div><div style='font-size:10px;opacity:0.9;'>Resolved</div></div>
                </div></a>
            </div>
            <div style='background: #ffffff; padding: 16px; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); border: 1px solid #e5e7eb; margin-top:12px;'>
                <div style='margin-bottom: 12px;'><h3 style='font-size: 15px; font-weight: 700; color: #111827; margin: 0;'>🎯 Arlo Platform</h3></div>
                <div style='display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px;'>{arlo_cells}</div>
            </div>
            <div style='background: white; padding: 6px; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-top:12px;'>
                <div style='background: #00c853; color: white; padding: 3px 4px; border-radius: 3px; margin-bottom: 4px; text-align: center;'>
                    <span style='font-size: 9px; font-weight: bold;'>🏗️ US Infra Exceptions</span>
                </div>
                <div style='background: {infra_bg_color}; padding: 5px; border-radius: 3px; color: white; cursor: pointer;' onclick="window.open('{infra_dashboard_url}', '_blank')">
                    <div style='text-align:center;'><div style='font-size:16px;'>{infra_icon}</div><div style='font-size:7px;font-weight:bold;'>{infra_status}</div></div>
                    <div style='text-align:center;border-top:1px solid rgba(255,255,255,0.3);padding-top:3px;'>
                        <div style='font-size:14px;font-weight:bold;'>{infra_exceptions_count:,}</div>
                        <div style='font-size:6px;opacity:0.9;'>Exceptions (last {timerange}h)</div>
                    </div>
                </div>
            </div>
    """


def status_monitor_partial(
    part: str,
    *,
    timerange: int = 1,
    environment: str | None = None,
    force_refresh: bool = False,
    session_id: str | None = None,
    dd_env: str | None = None,
) -> dict:
    """
    Incremental /statusmonitor/<env> fragments. Client loads bootstrap first, then parallel parts.
    """
    part = (part or "").strip().lower()
    try:
        services, dd_environments = _sm_resolve_services_and_environments(environment)
    except ValueError as e:
        return {"success": False, "error": str(e), "part": part}

    sid = (session_id or "").strip() or uuid.uuid4().hex
    _sm_incr_session_ensure(
        sid,
        environment=environment,
        timerange=timerange,
        force_refresh=force_refresh,
        services=services,
        dd_environments=dd_environments,
    )

    if part == "meta":
        return {
            "success": True,
            "part": "meta",
            "session_id": sid,
            "environment": environment,
            "dd_environments": dd_environments,
            "layout": "samsung" if environment == "samsung" else "default",
        }

    if part == "bootstrap":
        html = status_monitor_dashboard(
            timerange=timerange,
            environment=environment,
            force_refresh=force_refresh,
            fragment="bootstrap",
            incr_session_id=sid,
        )
        return {"success": True, "part": "bootstrap", "session_id": sid, "html": html}

    if part == "sidebar_fast":
        html = status_monitor_dashboard(
            timerange=timerange,
            environment=environment,
            force_refresh=force_refresh,
            fragment="sidebar_fast",
            incr_session_id=sid,
            skip_splunk=True,
        )
        return {"success": True, "part": "sidebar_fast", "session_id": sid, "html": html}

    if part == "sidebar_splunk":
        html = status_monitor_dashboard(
            timerange=timerange,
            environment=environment,
            force_refresh=force_refresh,
            fragment="sidebar_splunk",
            incr_session_id=sid,
        )
        return {"success": True, "part": "sidebar_splunk", "session_id": sid, "html": html}

    if part == "apm":
        if not dd_env:
            return {"success": False, "error": "dd_env required for part=apm", "part": part}
        html = status_monitor_dashboard(
            timerange=timerange,
            environment=environment,
            force_refresh=force_refresh,
            fragment="env_column",
            only_dd_env=dd_env,
            incr_session_id=sid,
            skip_splunk=True,
        )
        sess = _sm_incr_sessions.get(sid) or {}
        ready = len(sess.get("statuses_by_env") or {}) >= len(dd_environments)
        return {
            "success": True,
            "part": "apm",
            "session_id": sid,
            "dd_env": dd_env,
            "html": html,
            "ready_for_finalize": ready,
        }

    if part == "finalize":
        html = status_monitor_dashboard(
            timerange=timerange,
            environment=environment,
            force_refresh=force_refresh,
            fragment="finalize",
            incr_session_id=sid,
            skip_splunk=True,
        )
        return {"success": True, "part": "finalize", "session_id": sid, "html": html}

    return {"success": False, "error": f"Unknown part: {part}", "part": part}
