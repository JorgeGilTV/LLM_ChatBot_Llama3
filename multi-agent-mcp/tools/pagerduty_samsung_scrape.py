"""
Scrape the public PagerDuty External Status Dashboard (per-tab HTML) for Samsung board data
instead of the Incidents REST API.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import html as html_module
from collections.abc import Mapping
from typing import Any

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _subdomain() -> str:
    raw = (os.getenv("PAGERDUTY_SUBDOMAIN") or "arlo").strip()
    raw = raw.replace("https://", "").replace("http://", "").split("/")[0]
    return (raw.split(".")[0] if raw else "arlo") or "arlo"


def _tab_urls(dashboard_id: str) -> dict[str, str]:
    s = _subdomain()
    base = f"https://{s}.pagerduty.com/external-status-dashboard/{dashboard_id}/incidents"
    return {
        "active": f"{base}?tab=active",
        "pending": f"{base}?tab=pending",
        "resolved": f"{base}?tab=resolved",
    }


def _is_incident_obj(obj: Any) -> bool:
    if not isinstance(obj, Mapping):
        return False
    h = str(obj.get("html_url") or obj.get("self") or obj.get("url") or "")
    if "/incidents/" in h and "pagerduty" in h.lower():
        return True
    if obj.get("incident_number") is not None and (obj.get("title") or obj.get("summary")):
        return True
    return False


def _walk_incidents(node: Any, out: list, depth: int = 0) -> None:
    if depth > 24 or len(out) > 150:
        return
    if isinstance(node, list):
        if node and isinstance(node[0], Mapping) and _is_incident_obj(node[0]):
            for m in node:
                if isinstance(m, Mapping) and _is_incident_obj(m):
                    out.append(m)
        else:
            for it in node:
                _walk_incidents(it, out, depth + 1)
    elif isinstance(node, Mapping):
        for v in node.values():
            _walk_incidents(v, out, depth + 1)


def _from_next_data(html: str) -> list[Mapping[str, Any]]:
    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except (json.JSONDecodeError, TypeError) as e:
        logging.debug("samsung_scrape __NEXT_DATA__: %s", e)
        return []
    out: list = []
    _walk_incidents(data, out)
    return out[:80]


def _from_links(html: str) -> list[dict[str, Any]]:
    sub = _subdomain()
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    rows: list[dict] = []
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "")
        if "/incidents/" not in href:
            continue
        m = re.search(r"/incidents/([\w-]+)(?:$|[?#])", href, re.I)
        if not m:
            continue
        key = m.group(1)
        if key in seen or key.lower() in ("view", "new", "incidents"):
            continue
        seen.add(key)
        label = a.get_text(strip=True) or f"Incident {key}"
        if not href.startswith("http"):
            href = f"https://{sub}.pagerduty.com{href}"
        rows.append(
            {
                "incident_number": key,
                "title": label[:300],
                "service": {"summary": "External status"},
                "status": "unknown",
                "html_url": href,
            }
        )
        if len(rows) >= 24:
            break
    return rows


def _fetch_tab(url: str) -> list[Mapping[str, Any]]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=40)
    except OSError as e:
        logging.warning("samsung_scrape GET: %s", e)
        return []
    if r.status_code != 200:
        logging.warning("samsung_scrape %s -> HTTP %s", url[:80], r.status_code)
        return []
    nxt = _from_next_data(r.text)
    if nxt:
        return nxt
    return _from_links(r.text)


def _to_row(inc: Mapping[str, Any]) -> dict:
    num = inc.get("incident_number")
    if num is None:
        m = re.search(r"/incidents/([\w-]+)", str(inc.get("html_url") or inc.get("self") or ""))
        num = m.group(1) if m else "N/A"
    svc = inc.get("service")
    if isinstance(svc, dict):
        sl = str(svc.get("summary") or "Unknown")
    else:
        sl = str(svc or "Unknown")
    st = str(inc.get("status") or "unknown").lower()
    u = str(inc.get("html_url") or inc.get("self") or "#")
    if u == "#":
        m = re.search(r"/incidents/([\w-]+)", str(inc.get("id") or ""))
        if m:
            u = f"https://{_subdomain()}.pagerduty.com/incidents/{m.group(1)}"
    return {
        "number": num,
        "title": str(inc.get("title") or inc.get("summary") or "Incident")[:500],
        "service": sl,
        "status": st,
        "url": u,
    }


def build_samsung_pagerduty_scrape_payload(dashboard_id: str) -> dict[str, Any]:
    """
    Same top-level shape as build_pagerduty_monitor_api_payload (widget-compatible).
    ~4 records: up to 4 in each of active + recently_resolved (from Ongoing+Pending vs Resolved tabs).
    """
    tabs = _tab_urls(dashboard_id)
    t0 = time.time()
    active_raw = _fetch_tab(tabs["active"])
    pending_raw = _fetch_tab(tabs["pending"])
    resolved_raw = _fetch_tab(tabs["resolved"])

    combined_ongoing: list[Mapping] = []
    for src in (active_raw, pending_raw):
        for inc in src or []:
            if isinstance(inc, Mapping):
                combined_ongoing.append(inc)
    seen_n: set = set()
    combined_dedup: list[Mapping] = []
    for inc in combined_ongoing:
        r = _to_row(inc)
        k = (str(r.get("number")), str(r.get("url")))
        if k in seen_n:
            continue
        seen_n.add(k)
        combined_dedup.append(inc)

    active_list = [_to_row(inc) for inc in combined_dedup[:4]]
    recent = [_to_row(inc) for inc in (resolved_raw or [])[:4]]

    tr = 0
    ack = 0
    for r in active_list:
        s = (r.get("status") or "").lower()
        if s == "triggered":
            tr += 1
        elif s in ("acknowledged", "ack"):
            ack += 1
        else:
            tr += 1  # treat unknown as open/noisy

    if tr == 0 and active_list:
        tr = min(len(active_list), 4)
    if ack == 0 and active_list and any(
        (x.get("status") or "").lower() in ("acknowledged", "ack") for x in active_list
    ):
        ack = 1

    return {
        "triggered": tr,
        "acknowledged": ack,
        "resolved": min(len(resolved_raw or []), 99),
        "active": active_list,
        "recently_resolved": recent,
        "timestamp": time.strftime("%H:%M:%S"),
        "source": "scrape",
        "scrape_ms": int((time.time() - t0) * 1000),
    }


SAMSUNG_BOARD_KEYWORDS = (
    "samsung pagerduty",
    "pagerduty samsung",
    "samsung board",
    "samsung incidents",
    "samsung status dashboard",
    "external status samsung",
)

CAT_BOARD_KEYWORDS = (
    "cat pagerduty",
    "pagerduty cat",
    "cat board",
    "cat incidents",
    "cat status dashboard",
    "external status cat",
)

COMCAST_BOARD_KEYWORDS = (
    "comcast pagerduty",
    "pagerduty comcast",
    "comcast board",
    "comcast incidents",
    "comcast status dashboard",
    "external status comcast",
)

_PARTNER_BOARD_META: dict[str, dict[str, Any]] = {
    "samsung": {
        "label": "Samsung",
        "env": "SAMSUNG_STATUS_DASHBOARD_ID",
        "default_id": "PRBJIO4",
        "gradient": "linear-gradient(135deg,#06b6d4 0%,#3b82f6 100%)",
        "keywords": SAMSUNG_BOARD_KEYWORDS,
        "token": "samsung",
    },
    "cat": {
        "label": "CAT",
        "env": "CAT_STATUS_DASHBOARD_ID",
        "default_id": "",
        "gradient": "linear-gradient(135deg,#ea580c 0%,#c2410c 100%)",
        "keywords": CAT_BOARD_KEYWORDS,
        "token": "cat",
    },
    "comcast": {
        "label": "Comcast",
        "env": "COMCAST_STATUS_DASHBOARD_ID",
        "default_id": "",
        "gradient": "linear-gradient(135deg,#7c3aed 0%,#5b21b6 100%)",
        "keywords": COMCAST_BOARD_KEYWORDS,
        "token": "comcast",
    },
}


def _default_partner_dashboard_id(partner: str) -> str:
    meta = _PARTNER_BOARD_META[partner]
    value = os.getenv(meta["env"])
    if value is None:
        return str(meta["default_id"] or "")
    text = str(value).strip()
    if not text or text.lower() in ("off", "false", "no", "0", "none", "*"):
        return str(meta["default_id"] or "")
    return text


def _default_samsung_dashboard_id() -> str:
    return _default_partner_dashboard_id("samsung")


def _external_status_url(dashboard_id: str, tab: str = "active") -> str:
    sub = _subdomain()
    base = f"https://{sub}.pagerduty.com/external-status-dashboard/{dashboard_id}/incidents"
    if tab in ("active", "resolved", "pending"):
        return f"{base}?tab={tab}"
    return base


def is_pagerduty_partner_board_question(partner: str, question: str) -> bool:
    if not (question or "").strip():
        return False
    meta = _PARTNER_BOARD_META[partner]
    ql = question.lower()
    token = str(meta["token"])
    if token not in ql:
        return False
    return any(kw in ql for kw in meta["keywords"]) or (
        "pagerduty" in ql and token in ql
    )


def is_pagerduty_samsung_board_question(question: str) -> bool:
    return is_pagerduty_partner_board_question("samsung", question)


def is_pagerduty_cat_board_question(question: str) -> bool:
    return is_pagerduty_partner_board_question("cat", question)


def is_pagerduty_comcast_board_question(question: str) -> bool:
    return is_pagerduty_partner_board_question("comcast", question)


def _filter_incident_rows(rows: list[dict], query: str) -> list[dict]:
    q = (query or "").strip().lower()
    if not q:
        return rows
    out = []
    for row in rows:
        blob = " ".join(
            str(row.get(k) or "") for k in ("title", "service", "status", "number")
        ).lower()
        if q in blob:
            out.append(row)
    return out


def _incidents_table_html(title: str, rows: list[dict]) -> str:
    if not rows:
        return f"<p style='color:#64748b;margin:8px 0;'>{html_module.escape(title)}: none</p>"
    body = ""
    for row in rows:
        url = html_module.escape(str(row.get("url") or "#"))
        num = html_module.escape(str(row.get("number") or "—"))
        st = html_module.escape(str(row.get("status") or "—"))
        svc = html_module.escape(str(row.get("service") or "—"))
        tit = html_module.escape(str(row.get("title") or "Incident"))
        body += (
            f"<tr>"
            f"<td style='padding:8px;border-bottom:1px solid #e2e8f0;'>#{num}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e2e8f0;'>{tit}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e2e8f0;'>{svc}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e2e8f0;'>{st}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e2e8f0;'>"
            f"<a href='{url}' target='_blank' rel='noopener'>Open ↗</a></td>"
            f"</tr>"
        )
    return f"""
    <h3 style='margin:16px 0 8px;color:#0f172a;'>{html_module.escape(title)}</h3>
    <table style='width:100%;border-collapse:collapse;font-size:13px;'>
      <thead>
        <tr style='background:#f8fafc;'>
          <th style='padding:8px;text-align:left;'>#</th>
          <th style='padding:8px;text-align:left;'>Title</th>
          <th style='padding:8px;text-align:left;'>Service</th>
          <th style='padding:8px;text-align:left;'>Status</th>
          <th style='padding:8px;text-align:left;'>Link</th>
        </tr>
      </thead>
      <tbody>{body}</tbody>
    </table>
    """


def get_pagerduty_partner_board_html(
    partner: str,
    dashboard_id: str = "",
    query: str = "",
) -> str:
    """MCP entry: scrape a partner PagerDuty external status dashboard."""
    meta = _PARTNER_BOARD_META[partner]
    label = str(meta["label"])
    bid = (dashboard_id or _default_partner_dashboard_id(partner)).strip()
    if not bid:
        return (
            f"<p style='color:#dc2626;'>Set <code>{html_module.escape(str(meta['env']))}</code> "
            f"for {html_module.escape(label)} PagerDuty external status board.</p>"
        )
    try:
        payload = build_samsung_pagerduty_scrape_payload(bid)
    except Exception as exc:
        logging.exception("%s PagerDuty scrape failed", label)
        return (
            f"<p style='color:#dc2626;'>Error scraping {html_module.escape(label)} PagerDuty board "
            f"{html_module.escape(bid)}: {html_module.escape(str(exc))}</p>"
        )

    active = _filter_incident_rows(payload.get("active") or [], query)
    resolved = _filter_incident_rows(payload.get("recently_resolved") or [], query)
    board_url = _external_status_url(bid, "active")
    gradient = str(meta["gradient"])

    header = f"""
    <div style='background:{gradient};padding:14px 16px;border-radius:8px;color:white;margin-bottom:12px;'>
      <h2 style='margin:0;font-size:16px;'>{html_module.escape(label)} PagerDuty External Status</h2>
      <p style='margin:6px 0 0;font-size:12px;opacity:0.95;'>
        Board <strong>{html_module.escape(bid)}</strong> ·
        triggered {int(payload.get('triggered') or 0)} ·
        acknowledged {int(payload.get('acknowledged') or 0)} ·
        scrape {int(payload.get('scrape_ms') or 0)}ms ·
        <a href='{html_module.escape(board_url)}' target='_blank' rel='noopener' style='color:#e0f2fe;'>Open dashboard ↗</a>
      </p>
    </div>
    """
    return (
        header
        + _incidents_table_html("Active / Ongoing", active)
        + _incidents_table_html("Recently resolved", resolved)
    )


def get_pagerduty_samsung_board_html(
    dashboard_id: str = "",
    query: str = "",
) -> str:
    return get_pagerduty_partner_board_html("samsung", dashboard_id, query)


def get_pagerduty_cat_board_html(
    dashboard_id: str = "",
    query: str = "",
) -> str:
    return get_pagerduty_partner_board_html("cat", dashboard_id, query)


def get_pagerduty_comcast_board_html(
    dashboard_id: str = "",
    query: str = "",
) -> str:
    return get_pagerduty_partner_board_html("comcast", dashboard_id, query)
