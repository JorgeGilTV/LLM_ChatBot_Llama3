"""
SRE Companion lookup — which SRE engineer is the reliability partner for a given
service/area, from the Confluence page:
https://arlo.atlassian.net/wiki/spaces/AFS/pages/242156236/SRE+Companion+with+Service+Teams
"""

from __future__ import annotations

import html
import os
import re
import threading
import time
from typing import Any

import requests

PAGE_ID = "242156236"
_PAGE_URL = f"https://arlo.atlassian.net/wiki/rest/api/content/{PAGE_ID}?expand=body.atlas_doc_format"
_SOURCE_URL = f"https://arlo.atlassian.net/wiki/spaces/AFS/pages/{PAGE_ID}"
_DEFAULT_CACHE_SECS = 600
_AREAS_HEADING_RE = re.compile(r"^(.*?)\s*[—–-]\s*\d+\s*areas?$", re.IGNORECASE)

_cache_lock = threading.Lock()
_cache_payload: dict[str, Any] | None = None
_cache_ts: float = 0.0


def _cache_secs() -> int:
    try:
        return max(60, int(os.getenv("SRE_COMPANION_CACHE_SECS") or _DEFAULT_CACHE_SECS))
    except (TypeError, ValueError):
        return _DEFAULT_CACHE_SECS


def _text_of(node: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in node.get("content", []) or []:
        t = item.get("type")
        if t == "text":
            parts.append(item.get("text", ""))
        elif t == "mention":
            parts.append((item.get("attrs", {}) or {}).get("text", "").strip())
        elif t == "hardBreak":
            parts.append(" | ")
        elif t in ("paragraph", "text"):
            parts.append(_text_of(item))
    return " ".join(p for p in parts if p).strip()


def _list_items(bullet_list: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for li in bullet_list.get("content", []) or []:
        line_parts = []
        for child in li.get("content", []) or []:
            if child.get("type") == "paragraph":
                line_parts.append(_text_of(child))
        line = " ".join(p for p in line_parts if p).strip()
        if line:
            out.append(line)
    return out


def _fetch_adf_doc() -> dict[str, Any]:
    email = os.getenv("ATLASSIAN_EMAIL")
    token = os.getenv("CONFLUENCE_TOKEN")
    if not email or not token:
        raise RuntimeError("ATLASSIAN_EMAIL and CONFLUENCE_TOKEN must be set in the environment.")

    resp = requests.get(_PAGE_URL, auth=(email, token), timeout=(10, 45))
    if resp.status_code != 200:
        raise RuntimeError(f"Confluence HTTP {resp.status_code}: {resp.reason}")

    data = resp.json()
    adf_raw = data.get("body", {}).get("atlas_doc_format", {}).get("value", "")
    if not adf_raw:
        raise RuntimeError("No ADF content found on the SRE Companion page.")

    import json

    return json.loads(adf_raw)


def _parse_companions(doc: dict[str, Any]) -> dict[str, Any]:
    """Return {'companions': [{'person': str, 'areas': [str,...]}], 'reach_us': [str,...], 'escalation': str}."""
    content = doc.get("content", []) or []
    companions: list[dict[str, Any]] = []
    reach_us: list[str] = []
    escalation = ""

    for i, node in enumerate(content):
        t = node.get("type")

        if t == "paragraph":
            match = _AREAS_HEADING_RE.match(_text_of(node))
            if match:
                person = match.group(1).strip()
                next_node = content[i + 1] if i + 1 < len(content) else None
                areas = _list_items(next_node) if next_node and next_node.get("type") == "bulletList" else []
                if person and areas:
                    companions.append({"person": person, "areas": areas})

        if t == "heading" and _text_of(node).strip().lower() == "reach us":
            next_node = content[i + 1] if i + 1 < len(content) else None
            if next_node and next_node.get("type") == "bulletList":
                reach_us = _list_items(next_node)

        if t == "heading" and _text_of(node).strip().lower() == "escalation path":
            next_node = content[i + 1] if i + 1 < len(content) else None
            if next_node and next_node.get("type") == "paragraph":
                escalation = _text_of(next_node)

    return {"companions": companions, "reach_us": reach_us, "escalation": escalation}


def fetch_sre_companions(*, force_refresh: bool = False) -> dict[str, Any]:
    global _cache_payload, _cache_ts

    now = time.time()
    with _cache_lock:
        if not force_refresh and _cache_payload is not None and (now - _cache_ts) < _cache_secs():
            return dict(_cache_payload)

    doc = _fetch_adf_doc()
    payload = _parse_companions(doc)

    with _cache_lock:
        _cache_payload = payload
        _cache_ts = now
    return dict(payload)


def _matches(area: str, person: str, query: str) -> bool:
    needle = query.lower()
    return needle in area.lower() or needle in person.lower()


def render_sre_companion_html(query: str = "", *, force_refresh: bool = False) -> str:
    try:
        payload = fetch_sre_companions(force_refresh=force_refresh)
    except RuntimeError as exc:
        return (
            f"<div style='background:#fee2e2;padding:12px;border-left:4px solid #ef4444;"
            f"border-radius:6px;margin:10px 0;color:#991b1b;font-size:13px;'>"
            f"<strong>Error:</strong> {html.escape(str(exc))}</div>"
        )

    companions = payload.get("companions") or []
    q = (query or "").strip()

    rows_html: list[str] = []
    total_areas = 0
    matched_areas = 0
    for entry in companions:
        person = entry.get("person") or ""
        for area in entry.get("areas") or []:
            total_areas += 1
            if q and not _matches(area, person, q):
                continue
            matched_areas += 1
            rows_html.append(
                "<tr>"
                f"<td style='padding:8px;border:1px solid #e5e7eb;'>{html.escape(area)}</td>"
                f"<td style='padding:8px;border:1px solid #e5e7eb;font-weight:700;color:#0f172a;'>"
                f"{html.escape(person)}</td>"
                "</tr>"
            )

    q_note = ""
    if q:
        q_note = (
            f"<div style='padding:10px;background:#e0f2fe;border-left:4px solid #0284c7;"
            f"border-radius:4px;margin:10px 0;font-size:12px;color:#0c4a6e;'>"
            f"<strong>Search:</strong> {html.escape(q)} — {matched_areas} of {total_areas} area(s)</div>"
        )

    body_html: str
    if not rows_html:
        body_html = (
            f"<p style='margin:10px 0;color:#64748b;font-size:13px;'>"
            f"No SRE companion area matched <strong>{html.escape(q)}</strong>. "
            f"Try a broader term or open the page directly.</p>"
        )
    else:
        body_html = (
            "<div style='overflow-x:auto;'><table style='width:100%;border-collapse:collapse;font-size:13px;'>"
            "<thead><tr style='background:#1e293b;color:#fff;'>"
            "<th style='padding:8px;text-align:left;'>Service / Area</th>"
            "<th style='padding:8px;text-align:left;'>SRE Companion</th>"
            f"</tr></thead><tbody>{''.join(rows_html)}</tbody></table></div>"
        )

    footer_bits: list[str] = []
    escalation = (payload.get("escalation") or "").strip()
    if escalation:
        footer_bits.append(
            f"<p style='margin:10px 0 0;font-size:12px;color:#374151;'>"
            f"<strong>Escalation path:</strong> {html.escape(escalation)}</p>"
        )
    reach_us = payload.get("reach_us") or []
    if reach_us:
        reach_html = " · ".join(html.escape(x) for x in reach_us)
        footer_bits.append(
            f"<p style='margin:4px 0 0;font-size:12px;color:#374151;'><strong>Reach SRE:</strong> {reach_html}</p>"
        )

    return (
        "<div class='sre-companion-dash' style='font-family:system-ui,sans-serif;'>"
        "<div style='display:flex;justify-content:space-between;align-items:flex-start;gap:8px;flex-wrap:wrap;'>"
        "<h2 style='margin:0;font-size:18px;color:#0f172a;'>🧑‍🔧 SRE Companion — Service Teams</h2>"
        f"<a href='{html.escape(_SOURCE_URL)}' target='_blank' rel='noopener' "
        "style='font-size:11px;color:#2563eb;text-decoration:none;'>Open in Confluence →</a></div>"
        f"{q_note}{body_html}{''.join(footer_bits)}"
        "</div>"
    )


def sre_companion_search(query: str = "") -> str:
    """Legacy GocView tool entry point."""
    return render_sre_companion_html(query or "")


def get_sre_companion_mcp(question: str = "", query: str = "", *, force_refresh: bool = False) -> str:
    q = (question or query or "").strip()
    return render_sre_companion_html(q, force_refresh=force_refresh)
