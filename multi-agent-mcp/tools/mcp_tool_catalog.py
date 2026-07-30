"""MCP local tool catalog — categories for UI checkboxes and API."""
from __future__ import annotations

import re
from typing import Any

from tools.mcp_phase3_tools import parse_shift_mode_from_question
from tools.noc_kt import extract_noc_kt_query

# Legacy GocView tools shown in the UI (no MCP equivalent).
UI_SYNTHESIS_TOOL_NAMES: tuple[str, ...] = ("Bedrock_Report", "Ask_Bedrock")

# (category_key, title, color, tool_names)
MCP_TOOL_CATEGORIES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "confluence",
        "Confluence / Docs",
        "#0052CC",
        (
            "wiki_search",
            "oncall_schedule",
            "noc_kt_search",
        ),
    ),
    (
        "services",
        "Services & Versions",
        "#0d9488",
        (
            "service_owners",
            "arlo_versions",
            "deployed_fw_versions",
        ),
    ),
    (
        "datadog",
        "Datadog",
        "#632CA6",
        (
            "datadog_search",
            "datadog_services",
            "datadog_maintenance_windows",
            "datadog_red_metrics",
            "datadog_red_adt",
            "datadog_red_samsung",
            "datadog_red_metrics_us",
            "datadog_errors",
            "datadog_samsung_errors",
            "datadog_failed_pods",
            "datadog_403_errors",
        ),
    ),
    (
        "splunk",
        "Splunk",
        "#000000",
        (
            "splunk_p0_streaming",
            "splunk_p0_cvr",
            "splunk_p0_adt",
            "splunk_p0_us_infra",
        ),
    ),
    (
        "grafana",
        "Grafana",
        "#f46800",
        (
            "grafana_dns_mapper",
            "grafana_savant_z2",
            "grafana_dashboard_list",
        ),
    ),
    (
        "pagerduty",
        "PagerDuty",
        "#06AC38",
        (
            "pagerduty_incidents",
            "pagerduty_analytics",
            "pagerduty_insights",
            "pagerduty_samsung_board",
        ),
    ),
    (
        "noc_ops",
        "NOC / Calendar / Status",
        "#2563eb",
        (
            "grm_deployments",
            "arlo_public_status",
            "shift_report",
            "status_monitor_summary",
        ),
    ),
    (
        "servicenow",
        "ServiceNow",
        "#81B5A1",
        (
            "servicenow_servicedesk",
        ),
    ),
    (
        "aws",
        "AWS Admin",
        "#ff9900",
        (
            "aws_cloudtrail_search",
            "aws_connect_monitor",
        ),
    ),
    (
        "shm",
        "SHM / Service Health",
        "#0891b2",
        (
            "shm_metrics",
            "shm_daily",
        ),
    ),
)

MCP_TOOL_NAMES: frozenset[str] = frozenset(
    name for _k, _t, _c, names in MCP_TOOL_CATEGORIES for name in names
)

MCP_CHECKBOX_PREFIX = "MCP:"

# Tools that use the shared timerange selector (hours).
MCP_TIMERANGE_TOOLS: frozenset[str] = frozenset(
    {
        "datadog_search",
        "datadog_services",
        "datadog_red_metrics",
        "datadog_red_adt",
        "datadog_red_samsung",
        "datadog_red_metrics_us",
        "datadog_errors",
        "datadog_samsung_errors",
        "datadog_failed_pods",
        "datadog_403_errors",
        "splunk_p0_streaming",
        "splunk_p0_cvr",
        "splunk_p0_adt",
        "splunk_p0_us_infra",
        "grafana_dns_mapper",
        "grafana_savant_z2",
        "status_monitor_summary",
        "shm_daily",
    }
)

MCP_SPLUNK_P0_TOOLS: frozenset[str] = frozenset(
    {
        "splunk_p0_streaming",
        "splunk_p0_cvr",
        "splunk_p0_adt",
        "splunk_p0_us_infra",
    }
)


def build_ui_tool_catalog(
    tool_registry: dict[str, dict[str, Any]],
    *,
    synthesis_tools: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Single categorized tool list for checkbox UI (MCP + synthesis-only legacy)."""
    categories: list[dict[str, Any]] = []
    synthesis = synthesis_tools or {}

    ai_tools = [
        {"name": name, "desc": synthesis.get(name) or name, "value": name}
        for name in UI_SYNTHESIS_TOOL_NAMES
        if name in synthesis
    ]
    if ai_tools:
        categories.append(
            {
                "key": "ai",
                "title": "AI / Synthesis",
                "color": "#8b5cf6",
                "tools": ai_tools,
            }
        )

    for key, title, color, tool_names in MCP_TOOL_CATEGORIES:
        tools = []
        for name in tool_names:
            info = tool_registry.get(name)
            if not info:
                continue
            tools.append(
                {
                    "name": name,
                    "desc": info.get("description") or name,
                    "value": mcp_checkbox_value(name),
                }
            )
        if tools:
            categories.append({"key": key, "title": title, "color": color, "tools": tools})

    return {
        "categories": categories,
        "total_tools": sum(len(c["tools"]) for c in categories),
    }


def mcp_checkbox_value(tool_name: str) -> str:
    return f"{MCP_CHECKBOX_PREFIX}{tool_name}"


def parse_mcp_checkbox_value(value: str) -> str | None:
    if (value or "").startswith(MCP_CHECKBOX_PREFIX):
        name = value[len(MCP_CHECKBOX_PREFIX) :]
        return name if name in MCP_TOOL_NAMES else None
    return None


def build_mcp_tool_arguments(
    tool_name: str,
    user_query: str = "",
    timerange_hours: int = 4,
    service_filter: str = "",
) -> dict:
    """Build MCP JSON args from GocView query + timerange selector."""
    q = (user_query or "").strip()
    svc = (service_filter or "").strip()
    tr = max(1, int(timerange_hours or 4))

    if tool_name == "datadog_maintenance_windows":
        return {"question": q or "maintenance windows next 24 hours"}
    if tool_name == "grm_deployments":
        return {"question": q or "upcoming deployments next 24 hours"}
    if tool_name == "shift_report":
        return {"question": q, "mode": parse_shift_mode_from_question(q)}
    if tool_name == "status_monitor_summary":
        return {"question": q, "timerange": tr}
    if tool_name == "shm_metrics":
        return {
            "question": q,
            "force_live": bool(re.search(r"\b(?:live|refresh|latest|tableau)\b", q, re.I)),
        }
    if tool_name == "shm_daily":
        daily_tr = 720 if tr <= 24 else max(tr, 168)
        return {"question": q, "timerange": daily_tr}
    if tool_name == "noc_kt_search":
        return {"question": q, "query": extract_noc_kt_query(q) or svc or q}
    if tool_name == "servicenow_servicedesk":
        return {"question": q or "ServiceDesk dashboard", "query": q}
    if tool_name in MCP_SPLUNK_P0_TOOLS:
        return {"query": svc or q, "timerange": f"{tr}h"}
    if tool_name in MCP_TIMERANGE_TOOLS:
        if tool_name in (
            "datadog_red_metrics",
            "datadog_red_adt",
            "datadog_red_samsung",
            "datadog_red_metrics_us",
        ):
            return {"service": svc or q, "timerange": f"{tr}h"}
        if tool_name in (
            "datadog_errors",
            "datadog_samsung_errors",
            "datadog_failed_pods",
            "datadog_403_errors",
        ):
            return {"service": svc or q, "timerange": f"{tr}h"}
        if tool_name in ("datadog_search", "datadog_services"):
            return {"query": svc or q, "timerange": tr}
        if tool_name in ("grafana_dns_mapper", "grafana_savant_z2"):
            return {"query": q, "timerange": tr}
    if tool_name == "service_owners":
        return {"service": svc or q}
    if tool_name == "pagerduty_incidents":
        return {"query": svc or q}
    if tool_name == "pagerduty_samsung_board":
        return {"query": q}
    if tool_name == "arlo_public_status":
        return {"query": q}
    if tool_name == "grafana_dashboard_list":
        return {}
    return {"query": svc or q}
