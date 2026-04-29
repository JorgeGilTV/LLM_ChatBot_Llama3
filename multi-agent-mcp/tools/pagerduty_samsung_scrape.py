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
