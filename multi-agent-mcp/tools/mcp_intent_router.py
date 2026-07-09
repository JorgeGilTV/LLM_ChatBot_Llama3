"""Unified fast-route resolver: map questions → local MCP tool calls (no Bedrock pick)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from tools.datadog_downtimes import is_maintenance_window_question
from tools.deployments_calendar import is_grm_deployment_question
from tools.mcp_phase3_tools import (
    is_shift_report_question,
    is_status_monitor_summary_question,
    parse_shift_mode_from_question,
)
from tools.noc_kt import extract_noc_kt_query, is_noc_kt_question
from tools.pagerduty_samsung_scrape import is_pagerduty_samsung_board_question
from tools.read_arlo_status import is_arlo_public_status_question


@dataclass(frozen=True)
class McpFastRoute:
    tool_name: str
    arguments: dict[str, Any]
    title: str
    gradient: str
    log_label: str


# Order matters: more specific intents first.
_FAST_ROUTE_BUILDERS: list[Callable[[str], McpFastRoute | None]] = [
    lambda q: McpFastRoute(
        "grm_deployments",
        {"question": q},
        "🤖 GocBedrock Response (GRM Calendar)",
        "linear-gradient(135deg, #667eea 0%, #764ba2 100%)",
        "📅 Deployment query → grm_deployments (local MCP)",
    )
    if is_grm_deployment_question(q)
    else None,
    lambda q: McpFastRoute(
        "datadog_maintenance_windows",
        {"question": q},
        "🤖 GocBedrock Response (Datadog Maintenance Windows)",
        "linear-gradient(135deg, #632ca6 0%, #4f46e5 100%)",
        "🛠️ Maintenance window query → datadog_maintenance_windows (local MCP)",
    )
    if is_maintenance_window_question(q)
    else None,
    lambda q: McpFastRoute(
        "shift_report",
        {"question": q, "mode": parse_shift_mode_from_question(q)},
        "🤖 GocBedrock Response (Shift Handoff Report)",
        "linear-gradient(135deg, #003087 0%, #2563eb 100%)",
        "📋 Shift report → shift_report (local MCP)",
    )
    if is_shift_report_question(q)
    else None,
    lambda q: McpFastRoute(
        "status_monitor_summary",
        {"question": q},
        "🤖 GocBedrock Response (Status Monitor Hub)",
        "linear-gradient(135deg, #003087 0%, #08a64e 100%)",
        "📊 Status monitor hub → status_monitor_summary (local MCP)",
    )
    if is_status_monitor_summary_question(q)
    else None,
    lambda q: McpFastRoute(
        "arlo_public_status",
        {"query": q},
        "🤖 GocBedrock Response (Arlo Public Status)",
        "linear-gradient(135deg, #08a64e 0%, #003087 100%)",
        "🌐 Arlo public status → arlo_public_status (local MCP)",
    )
    if is_arlo_public_status_question(q)
    else None,
    lambda q: McpFastRoute(
        "noc_kt_search",
        {"question": q, "query": extract_noc_kt_query(q)},
        "🤖 GocBedrock Response (NOC KT)",
        "linear-gradient(135deg, #0ea5e9 0%, #2563eb 100%)",
        "📚 NOC KT query → noc_kt_search (local MCP)",
    )
    if is_noc_kt_question(q)
    else None,
    lambda q: McpFastRoute(
        "pagerduty_samsung_board",
        {"query": q},
        "🤖 GocBedrock Response (Samsung PagerDuty Board)",
        "linear-gradient(135deg, #06b6d4 0%, #3b82f6 100%)",
        "📟 Samsung PagerDuty board → pagerduty_samsung_board (local MCP)",
    )
    if is_pagerduty_samsung_board_question(q)
    else None,
]


def resolve_mcp_fast_route(question: str) -> McpFastRoute | None:
    """Return the first matching fast MCP route for a user question, or None."""
    if not (question or "").strip():
        return None
    for builder in _FAST_ROUTE_BUILDERS:
        route = builder(question)
        if route is not None:
            return route
    return None
