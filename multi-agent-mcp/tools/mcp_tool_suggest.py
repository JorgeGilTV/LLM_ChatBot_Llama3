"""Map legacy tool names → MCP checkboxes and heuristics for auto tool selection."""
from __future__ import annotations

import re

from tools.mcp_tool_catalog import MCP_TOOL_CATEGORIES, MCP_CHECKBOX_PREFIX, MCP_TOOL_NAMES, mcp_checkbox_value
from tools.service_query import extract_service_name_from_query
from tools.shm_tools import is_shm_daily_question, is_shm_metrics_question

# Legacy GocView checkbox / Bedrock names → MCP UI values.
LEGACY_TO_MCP_CHECKBOX: dict[str, str] = {
    "Wiki": mcp_checkbox_value("wiki_search"),
    "Owners": mcp_checkbox_value("service_owners"),
    "Arlo_Versions": mcp_checkbox_value("arlo_versions"),
    "Deployed_FW_Versions": mcp_checkbox_value("deployed_fw_versions"),
    "Holiday_Oncall": mcp_checkbox_value("oncall_schedule"),
    "DD_Search": mcp_checkbox_value("datadog_search"),
    "DD_Services": mcp_checkbox_value("datadog_services"),
    "DD_Red_Metrics": mcp_checkbox_value("datadog_red_metrics"),
    "DD_Red_ADT": mcp_checkbox_value("datadog_red_adt"),
    "DD_Red_Samsung": mcp_checkbox_value("datadog_red_samsung"),
    "DD_Red_Metrics_US": mcp_checkbox_value("datadog_red_metrics_us"),
    "DD_Errors": mcp_checkbox_value("datadog_errors"),
    "DD_Samsung_Errors": mcp_checkbox_value("datadog_samsung_errors"),
    "DD_Failed_Pods": mcp_checkbox_value("datadog_failed_pods"),
    "DD_403_Errors": mcp_checkbox_value("datadog_403_errors"),
    "P0_Streaming": mcp_checkbox_value("splunk_p0_streaming"),
    "P0_CVR_Streaming": mcp_checkbox_value("splunk_p0_cvr"),
    "P0_ADT_Streaming": mcp_checkbox_value("splunk_p0_adt"),
    "P0_Streaming_US": mcp_checkbox_value("splunk_p0_us_infra"),
    "Grafana_DNS_Mapper": mcp_checkbox_value("grafana_dns_mapper"),
    "Grafana_Savant_z2": mcp_checkbox_value("grafana_savant_z2"),
    "PagerDuty": mcp_checkbox_value("pagerduty_incidents"),
    "PagerDuty_Dashboards": mcp_checkbox_value("pagerduty_analytics"),
    "PagerDuty_Insights": mcp_checkbox_value("pagerduty_insights"),
}

SYNTHESIS_TOOL_NAMES = frozenset({"Bedrock_Report", "Ask_Bedrock"})

# MCP tools to run for a specific-service health / incident question.
SERVICE_HEALTH_MCP_TOOLS: tuple[str, ...] = (
    "datadog_services",
    "datadog_search",
    "datadog_errors",
    "datadog_red_metrics",
    "service_owners",
    "pagerduty_incidents",
)

_HEALTH_INTENT_RE = re.compile(
    r"\b(?:going\s+on|happening|pasando|pasa|wrong|issue|issues|problema|falla|fallando|"
    r"error|errors|errores|status|estado|degraded|down|alert|alerta|metric|metrics|"
    r"health|healthy|critical|warning|monitor|dashboard|apm|latency|latencia)\b",
    re.I,
)

_PAGERDUTY_INTENT_RE = re.compile(
    r"\b(?:pagerduty|incident|incidents|incidencia|on[- ]?call|oncall|alert|alerts)\b",
    re.I,
)

_SPLUNK_INTENT_RE = re.compile(
    r"\b(?:splunk|log|logs|streaming|p0)\b",
    re.I,
)

_SHM_INTENT_RE = re.compile(
    r"\b(?:shm|service\s+health\s+management|customer\s+(?:engagement|satisfaction)|"
    r"satisfacción|satisfaccion|nivel\s+de\s+satisfacción|nivel\s+de\s+satisfaccion|"
    r"satisfacción\s+del\s+cliente|satisfaccion\s+del\s+cliente|"
    r"pillar\s+score|livestream\s+per\s+user|stickiness|care\s+volume|"
    r"app\s+(?:store|rating|ratings)|event\s+captions|onboarding\s+vitals|shmview|csat|nps)\b",
    re.I,
)

_SHM_DAILY_INTENT_RE = re.compile(
    r"\b(?:shmdaily|active\s+users?\s+(?:daily|by\s+os)|dau\s+(?:trend|daily|by\s+os)|"
    r"daily\s+active\s+users?|users?\s+by\s+os|platform\s+split|ios\s+(?:vs|and|e)\s+android)\b",
    re.I,
)

_IOS_ANDROID_RE = re.compile(r"\b(?:ios|android|iphone|play\s+store|app\s+store)\b", re.I)


def mcp_checkbox_values_for(*tool_names: str) -> list[str]:
    return [mcp_checkbox_value(n) for n in tool_names if n in MCP_TOOL_NAMES]


def service_health_mcp_checkboxes() -> list[str]:
    return mcp_checkbox_values_for(*SERVICE_HEALTH_MCP_TOOLS)


def is_service_health_question(text: str) -> bool:
    """True when the query targets a specific service's operational health."""
    raw = (text or "").strip()
    if not raw:
        return False
    svc = extract_service_name_from_query(raw)
    if not svc:
        return False
    # Bare service name only → assume health check.
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", raw, re.I) and len(raw) >= 3:
        return True
    return bool(_HEALTH_INTENT_RE.search(raw))


def normalize_suggested_tool_name(name: str) -> str | None:
    """Map legacy / MCP names to a valid UI checkbox value."""
    n = (name or "").strip()
    if not n:
        return None
    if n in SYNTHESIS_TOOL_NAMES:
        return n
    if n.startswith(MCP_CHECKBOX_PREFIX):
        mcp = n[len(MCP_CHECKBOX_PREFIX) :]
        return mcp_checkbox_value(mcp) if mcp in MCP_TOOL_NAMES else None
    if n in LEGACY_TO_MCP_CHECKBOX:
        return LEGACY_TO_MCP_CHECKBOX[n]
    if n in MCP_TOOL_NAMES:
        return mcp_checkbox_value(n)
    return None


def normalize_and_validate_suggested_tools(names: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for name in names or []:
        norm = normalize_suggested_tool_name(name)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


def augment_suggested_tools_for_query(query: str, tools: list[str]) -> list[str]:
    """Add MCP Datadog (and related) tools when a service-specific health query is detected."""
    out = list(tools or [])
    seen = set(out)
    q = (query or "").strip()

    if is_service_health_question(q):
        for cb in service_health_mcp_checkboxes():
            if cb not in seen:
                out.append(cb)
                seen.add(cb)

    if _PAGERDUTY_INTENT_RE.search(q):
        for cb in mcp_checkbox_values_for("pagerduty_incidents", "pagerduty_insights"):
            if cb not in seen:
                out.append(cb)
                seen.add(cb)

    if _SPLUNK_INTENT_RE.search(q):
        for cb in mcp_checkbox_values_for("splunk_p0_streaming"):
            if cb not in seen:
                out.append(cb)
                seen.add(cb)

    if _SHM_DAILY_INTENT_RE.search(q) or is_shm_daily_question(q):
        for cb in mcp_checkbox_values_for("shm_daily"):
            if cb not in seen:
                out.append(cb)
                seen.add(cb)

    if is_shm_metrics_question(q) or (
        _IOS_ANDROID_RE.search(q)
        and re.search(r"\b(?:metric|metrics|rating|ratings|crash|dau|mau|users?|satisfac|csat|nps)\b", q, re.I)
    ):
        for cb in mcp_checkbox_values_for("shm_metrics"):
            if cb not in seen:
                out.append(cb)
                seen.add(cb)
        if not is_shm_daily_question(q) and re.search(
            r"\b(?:dau|daily\s+active|active\s+users?|by\s+os)\b", q, re.I
        ):
            for cb in mcp_checkbox_values_for("shm_daily"):
                if cb not in seen:
                    out.append(cb)
                    seen.add(cb)

    return out


def build_suggest_tools_catalog_text(tool_registry: dict) -> str:
    """Prompt text listing every selectable UI tool (MCP + synthesis)."""
    lines = ["**AI / Synthesis**", "- Bedrock_Report: AI synthesis (runs last; include for data queries)", "- Ask_Bedrock: general explanation only"]
    for _key, title, _color, tool_names in MCP_TOOL_CATEGORIES:
        lines.append(f"\n**{title}**")
        for name in tool_names:
            info = tool_registry.get(name) or {}
            desc = info.get("description") or name
            lines.append(f"- {mcp_checkbox_value(name)}: {desc}")
    return "\n".join(lines)


def bedrock_service_health_tool_calls(service_name: str, timerange_hours: int = 4) -> list[dict]:
    """Default local MCP tool calls for a specific service investigation."""
    tr = max(1, int(timerange_hours or 4))
    tr_s = f"{tr}h"
    svc = (service_name or "").strip()
    return [
        {
            "tool_name": "datadog_services",
            "reason": f"Find APM service match for {svc}",
            "params": {"query": svc, "timerange": tr},
        },
        {
            "tool_name": "datadog_search",
            "reason": f"Search Datadog dashboards for {svc}",
            "params": {"query": svc, "timerange": tr},
        },
        {
            "tool_name": "datadog_errors",
            "reason": f"Check error rate for {svc}",
            "params": {"service": svc, "timerange": tr_s},
        },
        {
            "tool_name": "datadog_red_metrics",
            "reason": f"RED metrics for {svc}",
            "params": {"service": svc, "timerange": tr_s},
        },
        {
            "tool_name": "service_owners",
            "reason": f"Service ownership for {svc}",
            "params": {"service": svc},
        },
    ]
