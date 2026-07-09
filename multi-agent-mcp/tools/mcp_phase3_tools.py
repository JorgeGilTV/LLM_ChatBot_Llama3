"""MCP wrappers for Phase 3 tools (shift report, status hub, AWS admin)."""
from __future__ import annotations

import html
import json
import re
from typing import Any

SHIFT_REPORT_KEYWORDS = (
    "shift report",
    "shift handoff",
    "handoff report",
    "shift turnover",
    "reporte de turno",
    "entrega de turno",
    "turnover report",
)

STATUS_MONITOR_KEYWORDS = (
    "status monitor",
    "status wall",
    "environment health",
    "hub summary",
    "all environments",
    "environments status",
    "statusmonitor",
)


def is_shift_report_question(question: str) -> bool:
    if not (question or "").strip():
        return False
    ql = question.lower()
    if any(kw in ql for kw in SHIFT_REPORT_KEYWORDS):
        return True
    return bool(re.search(r"\bshift\s*[123]\b", ql))


def parse_shift_mode_from_question(question: str) -> str:
    ql = (question or "").lower()
    for token in ("shift3", "shift 3", "shift2", "shift 2", "shift1", "shift 1"):
        if token in ql:
            return token.replace(" ", "")
    if any(x in ql for x in ("end shift", "graveyard", "night shift")):
        return "shift3"
    if "evening" in ql or "tarde" in ql:
        return "shift2"
    return "shift1"


def is_status_monitor_summary_question(question: str) -> bool:
    if not (question or "").strip():
        return False
    ql = question.lower()
    return any(kw in ql for kw in STATUS_MONITOR_KEYWORDS)


def _parse_status_monitor_timerange(question: str, default: int = 1) -> int:
    ql = (question or "").lower()
    match = re.search(r"(\d+)\s*(h|hour|hours|hr)", ql)
    if match:
        return max(1, min(int(match.group(1)), 24))
    if "day" in ql or "24h" in ql:
        return 24
    return default


def _format_hub_summary_html(data: dict[str, Any]) -> str:
    if not data.get("success"):
        err = html.escape(str(data.get("error") or "Unknown error"))
        return f"<p style='color:#dc2626;'>Status monitor error: {err}</p>"

    timerange = int(data.get("timerange") or 1)
    rows = data.get("environments") or []
    overall_colors = {
        "healthy": "#16a34a",
        "warning": "#d97706",
        "critical": "#dc2626",
    }

    header = f"""
    <div style='background:linear-gradient(135deg,#003087 0%,#08a64e 100%);padding:14px 16px;border-radius:8px;color:white;margin-bottom:12px;'>
      <h2 style='margin:0;font-size:16px;'>Status Monitor Hub</h2>
      <p style='margin:6px 0 0;font-size:12px;opacity:0.95;'>Timerange: last {timerange}h · {len(rows)} environments</p>
    </div>
    """
    if not rows:
        return header + "<p style='color:#64748b;'>No environment data.</p>"

    body = (
        "<table style='width:100%;border-collapse:collapse;font-size:13px;'>"
        "<thead><tr style='background:#f8fafc;'>"
        "<th style='padding:8px;text-align:left;'>Environment</th>"
        "<th style='padding:8px;text-align:left;'>Overall</th>"
        "<th style='padding:8px;text-align:right;'>Healthy</th>"
        "<th style='padding:8px;text-align:right;'>Warning</th>"
        "<th style='padding:8px;text-align:right;'>Critical</th>"
        "<th style='padding:8px;text-align:right;'>DD Alerts</th>"
        "</tr></thead><tbody>"
    )
    for env in rows:
        overall = str(env.get("overall") or "unknown")
        color = overall_colors.get(overall, "#64748b")
        label = html.escape(str(env.get("label") or env.get("slug") or "—"))
        href = html.escape(str(env.get("href") or "#"))
        body += (
            f"<tr style='border-bottom:1px solid #e2e8f0;'>"
            f"<td style='padding:8px;'><a href='{href}' target='_blank' rel='noopener'>{label}</a></td>"
            f"<td style='padding:8px;color:{color};font-weight:700;'>{html.escape(overall)}</td>"
            f"<td style='padding:8px;text-align:right;'>{int(env.get('healthy') or 0)}</td>"
            f"<td style='padding:8px;text-align:right;'>{int(env.get('warning') or 0)}</td>"
            f"<td style='padding:8px;text-align:right;'>{int(env.get('critical') or 0)}</td>"
            f"<td style='padding:8px;text-align:right;'>{int(env.get('dd_monitor_alerts_total') or 0)}</td>"
            f"</tr>"
        )
    body += "</tbody></table>"
    return header + body


def get_shift_report_mcp(mode: str = "", question: str = "") -> str:
    """MCP entry: shift handoff table (MintMCP + Bedrock; may take several minutes)."""
    from tools.shift_report import _normalize_shift_mode, generate_shift_report

    raw_mode = (mode or parse_shift_mode_from_question(question) or "shift1").strip()
    try:
        normalized = _normalize_shift_mode(raw_mode)
    except ValueError as exc:
        return f"<p style='color:#dc2626;'>{html.escape(str(exc))}</p>"

    try:
        report = generate_shift_report(normalized)
    except Exception as exc:
        return (
            f"<p style='color:#dc2626;'><strong>Shift report failed:</strong> "
            f"{html.escape(str(exc))}</p>"
        )

    meta = (
        f"<p style='font-size:12px;color:#64748b;margin-top:12px;'>"
        f"{int(report.get('row_count') or 0)} rows · "
        f"{html.escape(str(report.get('label') or normalized))} · "
        f"{html.escape(str(report.get('window_start') or ''))} → "
        f"{html.escape(str(report.get('window_end') or ''))}"
        f"</p>"
    )
    return str(report.get("html") or "") + meta


def get_status_monitor_summary_mcp(
    timerange: int | None = None,
    question: str = "",
    force_refresh: bool = False,
) -> str:
    """MCP entry: compact status monitor hub card summary."""
    from tools.status_monitor import status_monitor_hub_summary

    hours = timerange if timerange is not None else _parse_status_monitor_timerange(question)
    hours = max(1, min(int(hours), 24))
    data = status_monitor_hub_summary(timerange=hours, force_refresh=bool(force_refresh))
    return _format_hub_summary_html(data)


def aws_cloudtrail_search_mcp(
    resource_name: str = "",
    resource_type: str = "OTHER",
    region: str = "us-east-1",
    account_id: str = "",
    lookback_days: int = 7,
    max_events: int = 50,
) -> str:
    """MCP entry: CloudTrail lookup by resource name."""
    from tools.aws_cloudtrail_tracker import cloudtrail_search

    result = cloudtrail_search(
        resource_name=resource_name,
        resource_type=resource_type or "OTHER",
        region=region or "us-east-1",
        account_id=account_id,
        lookback_days=lookback_days,
        max_events=max_events,
    )
    return json.dumps(result, indent=2, default=str)


def aws_connect_monitor_mcp(
    instance_id: str = "",
    region: str = "",
    force_refresh: bool = False,
) -> str:
    """MCP entry: AWS Connect contact-center health snapshot."""
    from tools.aws_connect_monitor import connect_monitor_snapshot

    kwargs: dict[str, Any] = {"force_refresh": bool(force_refresh)}
    if instance_id:
        kwargs["instance_id"] = instance_id
    if region:
        kwargs["region"] = region
    result = connect_monitor_snapshot(**kwargs)
    return json.dumps(result, indent=2, default=str)
