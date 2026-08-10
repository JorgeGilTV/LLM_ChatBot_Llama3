"""
SSL certificate monitor — data from https://sentinel.arlocloud.com/api/certificates.

Shows expired certs, those expiring soon (default ≤15 days), and valid ones with a traffic-light summary.
"""

from __future__ import annotations

import html
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests

DEFAULT_SENTINEL_BASE = "https://sentinel.arlocloud.com"
DEFAULT_EXPIRING_DAYS = 15
DEFAULT_CACHE_SECS = 300
_HTTP_TIMEOUT = (10, 45)
_ERROR_STATUSES = frozenset({"error", "timeout", "failed"})

KNOWN_BRANDS: tuple[tuple[str, str], ...] = (
    ("arlocloud", "ArloCloud"),
    ("arloxcld", "ArloXcld"),
    ("arlo", "Arlo"),
)

_cache_lock = threading.Lock()
_cache_payload: dict[str, Any] | None = None
_cache_ts: float = 0.0


def _sentinel_base() -> str:
    return (os.getenv("SENTINEL_API_BASE") or DEFAULT_SENTINEL_BASE).rstrip("/")


def _expiring_days() -> int:
    try:
        return max(1, int(os.getenv("SENTINEL_EXPIRING_DAYS") or DEFAULT_EXPIRING_DAYS))
    except (TypeError, ValueError):
        return DEFAULT_EXPIRING_DAYS


def _cache_secs() -> int:
    try:
        return max(30, int(os.getenv("SENTINEL_CACHE_SECS") or DEFAULT_CACHE_SECS))
    except (TypeError, ValueError):
        return DEFAULT_CACHE_SECS


def _sentinel_portal_url() -> str:
    return _sentinel_base()


def get_domain_type(cert: dict[str, Any]) -> str:
    if (cert or {}).get("source") == "s3_upload":
        return "mTLS"
    domain_lower = ((cert or {}).get("domain") or "").lower()
    for keyword, label in KNOWN_BRANDS:
        if keyword in domain_lower:
            return label
    return "Others"


def classify_bucket(cert: dict[str, Any], expiring_days: int | None = None) -> str:
    """Return valid | expiring | expired."""
    days_limit = expiring_days if expiring_days is not None else _expiring_days()
    status = (cert or {}).get("status") or ""
    days = (cert or {}).get("days_until_expiry")

    if status == "expired":
        return "expired"
    if days is not None and days <= 0:
        return "expired"
    if status == "valid" and days is not None and 0 < days <= days_limit:
        return "expiring"
    return "valid"


def _parse_query_filters(query: str) -> tuple[str, str | None]:
    """Extract optional status filter and remaining search text from natural language."""
    q = (query or "").strip()
    if not q:
        return "", None

    lowered = q.lower()
    status: str | None = None
    patterns = (
        (r"\b(?:expired|expirados?|vencidos?|caducados?)\b", "expired"),
        (r"\b(?:expiring|expiran|pr[oó]ximos?\s+a\s+expirar|por\s+expirar|soon)\b", "expiring"),
        (r"\b(?:valid|v[aá]lidos?|ok|healthy)\b", "valid"),
    )
    for pattern, bucket in patterns:
        if re.search(pattern, lowered, re.I):
            status = bucket
            q = re.sub(pattern, " ", q, flags=re.I).strip()
            break

    q = re.sub(r"\s+", " ", q).strip()
    return q, status


def _cert_matches_search(cert: dict[str, Any], search: str) -> bool:
    if not search:
        return True
    needle = search.lower()
    haystack = " ".join(
        str(cert.get(k) or "")
        for k in ("domain", "environment", "description", "issuer", "subject", "status")
    ).lower()
    haystack += " " + get_domain_type(cert).lower()
    return needle in haystack


def filter_certificates(
    certificates: list[dict[str, Any]],
    query: str = "",
    *,
    status_filter: str | None = None,
    domain_type: str | None = None,
) -> list[dict[str, Any]]:
    search, inferred_status = _parse_query_filters(query)
    status = status_filter or inferred_status
    domain_type_norm = (domain_type or "").strip()

    out: list[dict[str, Any]] = []
    for cert in certificates:
        if status and classify_bucket(cert) != status:
            continue
        if domain_type_norm and get_domain_type(cert) != domain_type_norm:
            continue
        if not _cert_matches_search(cert, search):
            continue
        out.append(cert)
    return out


def _connection_error_html(exc: Exception) -> str:
    return f"""
    <div style='background-color:#fff3cd;padding:16px;border-left:4px solid #f59e0b;border-radius:6px;margin:12px 0;'>
        <h3 style='margin:0 0 8px 0;color:#92400e;font-size:16px;'>⚠️ Cannot connect to Sentinel</h3>
        <p style='margin:0 0 8px 0;color:#78350f;font-size:13px;'>
            Unable to reach <code>{html.escape(_sentinel_base())}</code>.
        </p>
        <ul style='margin:0;padding-left:20px;color:#78350f;font-size:12px;'>
            <li>Ensure you are on Arlo VPN (GlobalProtect)</li>
            <li>Verify DNS resolves <code>sentinel.arlocloud.com</code></li>
        </ul>
        <p style='margin:8px 0 0;color:#991b1b;font-size:12px;'><strong>Error:</strong> {html.escape(str(exc))}</p>
    </div>
    """


def fetch_certificates(*, force_refresh: bool = False) -> dict[str, Any]:
    """Fetch and cache certificate payload from Sentinel API."""
    global _cache_payload, _cache_ts

    now = time.time()
    with _cache_lock:
        if (
            not force_refresh
            and _cache_payload is not None
            and (now - _cache_ts) < _cache_secs()
        ):
            return dict(_cache_payload)

    url = f"{_sentinel_base()}/api/certificates"
    try:
        resp = requests.get(url, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.ConnectionError as exc:
        err = str(exc)
        if "Name or service not known" in err or "Failed to resolve" in err:
            raise RuntimeError(
                f"DNS resolution failed for {_sentinel_base()} — connect to Arlo VPN"
            ) from exc
        raise RuntimeError(f"Connection error: {exc}") from exc
    except requests.exceptions.Timeout as exc:
        raise RuntimeError(f"Timeout contacting Sentinel: {exc}") from exc
    except requests.exceptions.HTTPError as exc:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.reason}") from exc
    except ValueError as exc:
        raise RuntimeError(f"Invalid JSON from Sentinel: {exc}") from exc

    if not data.get("success"):
        raise RuntimeError(data.get("error") or "Sentinel API returned success=false")

    raw = data.get("certificates") or []
    certificates = [c for c in raw if isinstance(c, dict) and c.get("status") not in _ERROR_STATUSES]

    payload = {
        "success": True,
        "certificates": certificates,
        "total_domains": data.get("total_domains") or len(certificates),
        "checked_at": data.get("checked_at") or data.get("last_updated") or "",
        "last_updated": data.get("last_updated") or data.get("checked_at") or "",
        "source_url": _sentinel_portal_url(),
        "expiring_days": _expiring_days(),
    }

    with _cache_lock:
        _cache_payload = payload
        _cache_ts = now

    return dict(payload)


def summarize_certificates(certificates: list[dict[str, Any]]) -> dict[str, Any]:
    expiring_days = _expiring_days()
    buckets = {"valid": 0, "expiring": 0, "expired": 0}
    by_env: dict[str, dict[str, int]] = {}
    by_type: dict[str, dict[str, int]] = {}

    for cert in certificates:
        bucket = classify_bucket(cert, expiring_days)
        buckets[bucket] += 1

        env = (cert.get("environment") or "Unknown").strip() or "Unknown"
        env_stats = by_env.setdefault(env, {"valid": 0, "expiring": 0, "expired": 0})
        env_stats[bucket] += 1

        dtype = get_domain_type(cert)
        type_stats = by_type.setdefault(dtype, {"valid": 0, "expiring": 0, "expired": 0})
        type_stats[bucket] += 1

    expired = buckets["expired"]
    expiring = buckets["expiring"]
    if expired > 0:
        semaphore = "red"
        semaphore_label = "Critical — expired certificates"
    elif expiring > 0:
        semaphore = "yellow"
        semaphore_label = "Warning — certificates expiring soon"
    else:
        semaphore = "green"
        semaphore_label = "Healthy — no expired or soon-to-expire certificates"

    return {
        "total": len(certificates),
        "valid": buckets["valid"],
        "expiring": expiring,
        "expired": expired,
        "expiring_days": expiring_days,
        "semaphore": semaphore,
        "semaphore_label": semaphore_label,
        "by_environment": by_env,
        "by_domain_type": by_type,
    }


def _format_dt(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "—"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return html.escape(raw)


def _semaphore_styles(semaphore: str) -> dict[str, str]:
    styles = {
        "green": {"bg": "#14532d", "dot": "#22c55e", "glow": "rgba(34,197,94,0.45)", "text": "#ecfdf5"},
        "yellow": {"bg": "#713f12", "dot": "#eab308", "glow": "rgba(234,179,8,0.5)", "text": "#fefce8"},
        "red": {"bg": "#7f1d1d", "dot": "#ef4444", "glow": "rgba(239,68,68,0.5)", "text": "#fef2f2"},
    }
    return styles.get(semaphore, styles["green"])


def _cert_row_html(cert: dict[str, Any], *, highlight: bool = False) -> str:
    bucket = classify_bucket(cert)
    days = cert.get("days_until_expiry")
    days_text = "—" if days is None else str(days)
    row_bg = ""
    if bucket == "expired":
        row_bg = "background:#fee2e2;"
    elif bucket == "expiring":
        row_bg = "background:#fef9c3;"
    elif highlight:
        row_bg = "background:#eff6ff;"

    status_badge = {
        "expired": ("Expired", "#991b1b", "#fee2e2"),
        "expiring": ("Expiring", "#854d0e", "#fef9c3"),
        "valid": ("Valid", "#166534", "#dcfce7"),
    }[bucket]
    label, color, bg = status_badge

    return (
        f"<tr style='{row_bg}'>"
        f"<td style='padding:8px;border:1px solid #e5e7eb;font-family:monospace;font-size:12px;'>"
        f"{html.escape(str(cert.get('domain') or '—'))}</td>"
        f"<td style='padding:8px;border:1px solid #e5e7eb;font-size:12px;'>"
        f"{html.escape(str(cert.get('environment') or '—'))}</td>"
        f"<td style='padding:8px;border:1px solid #e5e7eb;font-size:12px;'>"
        f"{html.escape(get_domain_type(cert))}</td>"
        f"<td style='padding:8px;border:1px solid #e5e7eb;font-size:12px;text-align:center;'>"
        f"<span style='padding:2px 8px;border-radius:999px;background:{bg};color:{color};"
        f"font-weight:600;font-size:11px;'>{label}</span></td>"
        f"<td style='padding:8px;border:1px solid #e5e7eb;font-size:12px;text-align:center;'>"
        f"{html.escape(days_text)}</td>"
        f"<td style='padding:8px;border:1px solid #e5e7eb;font-size:12px;'>"
        f"{_format_dt(cert.get('not_after'))}</td>"
        f"<td style='padding:8px;border:1px solid #e5e7eb;font-size:11px;color:#64748b;'>"
        f"{html.escape(str(cert.get('description') or '—'))}</td>"
        f"</tr>"
    )


def _table_html(title: str, certs: list[dict[str, Any]], *, empty_msg: str) -> str:
    if not certs:
        return (
            f"<div style='margin:12px 0;padding:10px;background:#f8fafc;border:1px solid #e2e8f0;"
            f"border-radius:8px;font-size:12px;color:#64748b;'>{html.escape(empty_msg)}</div>"
        )

    rows = "".join(_cert_row_html(c) for c in certs)
    return (
        f"<h3 style='margin:18px 0 8px;font-size:15px;color:#0f172a;'>{html.escape(title)} "
        f"<span style='font-size:12px;color:#64748b;'>({len(certs)})</span></h3>"
        f"<div style='overflow-x:auto;'><table style='width:100%;border-collapse:collapse;'>"
        f"<thead><tr style='background:#1e293b;color:#fff;'>"
        f"<th style='padding:8px;text-align:left;font-size:11px;'>Domain</th>"
        f"<th style='padding:8px;text-align:left;font-size:11px;'>Environment</th>"
        f"<th style='padding:8px;text-align:left;font-size:11px;'>Type</th>"
        f"<th style='padding:8px;text-align:center;font-size:11px;'>Status</th>"
        f"<th style='padding:8px;text-align:center;font-size:11px;'>Days left</th>"
        f"<th style='padding:8px;text-align:left;font-size:11px;'>Expires</th>"
        f"<th style='padding:8px;text-align:left;font-size:11px;'>Description</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div>"
    )


def render_sentinel_dashboard_html(
    query: str = "",
    *,
    compact: bool = False,
    force_refresh: bool = False,
) -> str:
    try:
        payload = fetch_certificates(force_refresh=force_refresh)
    except Exception as exc:
        return _connection_error_html(exc)

    all_certs = payload["certificates"]
    filtered = filter_certificates(all_certs, query)
    summary_all = summarize_certificates(all_certs)
    summary_view = summarize_certificates(filtered) if query.strip() else summary_all

    expiring_days = summary_all["expiring_days"]
    expired_list = sorted(
        [c for c in filtered if classify_bucket(c) == "expired"],
        key=lambda c: (c.get("days_until_expiry") if c.get("days_until_expiry") is not None else -999),
    )
    expiring_list = sorted(
        [c for c in filtered if classify_bucket(c) == "expiring"],
        key=lambda c: (c.get("days_until_expiry") if c.get("days_until_expiry") is not None else 999),
    )

    sem = _semaphore_styles(summary_all["semaphore"])
    portal = _sentinel_portal_url()
    checked = payload.get("checked_at") or payload.get("last_updated") or ""

    kpi = summary_view
    title_size = "14px" if compact else "18px"
    max_rows = 8 if compact else 50

    filter_note = ""
    if query.strip():
        filter_note = (
            f"<div style='padding:10px;background:#e0f2fe;border-left:4px solid #0284c7;"
            f"border-radius:4px;margin:10px 0;font-size:12px;color:#0c4a6e;'>"
            f"<strong>Filter:</strong> {html.escape(query)} — "
            f"{len(filtered)} of {len(all_certs)} certificates</div>"
        )

    return (
        f"<div class='sentinel-ssl-dash' style='font-family:system-ui,sans-serif;'>"
        f"<div style='display:flex;justify-content:space-between;align-items:flex-start;"
        f"gap:8px;margin-bottom:10px;flex-wrap:wrap;'>"
        f"<h2 style='margin:0;font-size:{title_size};color:#0f172a;'>🔒 SSL Certificate Monitor</h2>"
        f"<a href='{html.escape(portal)}' target='_blank' rel='noopener' "
        f"style='font-size:11px;color:#2563eb;text-decoration:none;white-space:nowrap;'>"
        f"Open Sentinel →</a></div>"
        f"<div style='display:flex;align-items:center;gap:12px;padding:12px 14px;"
        f"background:{sem['bg']};border-radius:10px;color:{sem['text']};margin-bottom:12px;'>"
        f"<span style='width:16px;height:16px;border-radius:50%;background:{sem['dot']};"
        f"box-shadow:0 0 10px {sem['glow']};flex-shrink:0;'></span>"
        f"<div style='flex:1;'><div style='font-weight:700;font-size:14px;'>"
        f"{html.escape(summary_all['semaphore_label'])}</div>"
        f"<div style='font-size:11px;opacity:0.9;margin-top:2px;'>"
        f"Checked: {_format_dt(checked)} · expiring window ≤ {expiring_days} days</div></div>"
        f"<div style='display:flex;gap:10px;text-align:center;font-size:11px;'>"
        f"<div><div style='font-size:18px;font-weight:800;'>{summary_all['valid']}</div>Valid</div>"
        f"<div><div style='font-size:18px;font-weight:800;'>{summary_all['expiring']}</div>Expiring</div>"
        f"<div><div style='font-size:18px;font-weight:800;'>{summary_all['expired']}</div>Expired</div>"
        f"</div></div>"
        f"{filter_note}"
        f"{_table_html('⛔ Expired certificates', expired_list[:max_rows], empty_msg='No expired certificates.')}"
        f"{_table_html('⚠️ Expiring soon', expiring_list[:max_rows], empty_msg=f'No certificates expiring within {expiring_days} days.')}"
        f"</div>"
    )


def sentinel_certificates_payload(
    query: str = "",
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Compact JSON for sidebar widget and /api/sentinel/certificates."""
    try:
        payload = fetch_certificates(force_refresh=force_refresh)
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "source_url": _sentinel_portal_url(),
            "summary": {},
            "expired": [],
            "expiring": [],
        }

    all_certs = payload["certificates"]
    filtered = filter_certificates(all_certs, query) if query.strip() else all_certs
    summary = summarize_certificates(all_certs)

    def _compact(cert: dict[str, Any]) -> dict[str, Any]:
        return {
            "domain": cert.get("domain"),
            "environment": cert.get("environment"),
            "domain_type": get_domain_type(cert),
            "status": classify_bucket(cert),
            "days_until_expiry": cert.get("days_until_expiry"),
            "not_after": cert.get("not_after"),
            "description": cert.get("description"),
        }

    expired = sorted(
        [_compact(c) for c in all_certs if classify_bucket(c) == "expired"],
        key=lambda c: (c.get("days_until_expiry") if c.get("days_until_expiry") is not None else -999),
    )
    expiring = sorted(
        [_compact(c) for c in all_certs if classify_bucket(c) == "expiring"],
        key=lambda c: (c.get("days_until_expiry") if c.get("days_until_expiry") is not None else 999),
    )

    return {
        "success": True,
        "source_url": payload.get("source_url") or _sentinel_portal_url(),
        "checked_at": payload.get("checked_at") or "",
        "last_updated": payload.get("last_updated") or "",
        "expiring_days": summary["expiring_days"],
        "summary": summary,
        "filtered_count": len(filtered),
        "total_count": len(all_certs),
        "query": query.strip(),
        "expired": expired[:20],
        "expiring": expiring[:20],
    }


def get_sentinel_certificates_mcp(
    question: str = "",
    query: str = "",
    *,
    force_refresh: bool = False,
) -> str:
    q = (question or query or "").strip()
    return render_sentinel_dashboard_html(q, compact=False, force_refresh=force_refresh)


def read_sentinel_certificates(query: str = "") -> str:
    """Legacy GocView tool entry point."""
    return render_sentinel_dashboard_html(query or "", compact=False)
