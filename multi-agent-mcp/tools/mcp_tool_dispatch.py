"""Argument coercion and dispatch helpers for the local MCP server."""
from __future__ import annotations

import re
from typing import Any, Callable


def text_arg(arguments: dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = arguments.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def coerce_timerange_hours(timerange: Any, default: int = 4) -> int:
    if timerange is None or timerange == "":
        return default
    if isinstance(timerange, bool):
        return default
    if isinstance(timerange, (int, float)):
        return max(1, int(timerange))

    raw = str(timerange).strip().lower()
    if raw.isdigit():
        return max(1, int(raw))

    presets = {
        "1h": 1,
        "4h": 4,
        "12h": 12,
        "24h": 24,
        "1d": 24,
        "2d": 48,
        "7d": 168,
        "1w": 168,
        "1mo": 720,
        "30d": 720,
    }
    if raw in presets:
        return presets[raw]

    match = re.match(r"^(\d+)\s*(h|hour|hours|d|day|days|w|week|weeks|mo)$", raw)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        if unit in ("h", "hour", "hours"):
            return max(1, amount)
        if unit in ("d", "day", "days"):
            return max(1, amount * 24)
        if unit in ("w", "week", "weeks"):
            return max(1, amount * 168)
        return max(1, amount * 720)

    return default


def service_or_query(arguments: dict[str, Any]) -> str:
    return text_arg(arguments, "service", "query")


def invoke_tool(name: str, arguments: dict[str, Any], func: Callable[..., Any]) -> str:
    """Map MCP JSON arguments to each tool's Python signature."""
    args = arguments or {}

    if name in ("splunk_p0_streaming", "splunk_p0_cvr", "splunk_p0_adt", "splunk_p0_us_infra"):
        from tools.splunk_tool import splunk_p0_coerce_timerange_hours

        return func(
            service_or_query(args),
            splunk_p0_coerce_timerange_hours(args.get("timerange")),
        )

    datadog_timerange_tools = (
        "datadog_red_metrics",
        "datadog_red_adt",
        "datadog_red_samsung",
        "datadog_red_metrics_us",
        "datadog_errors",
        "datadog_samsung_errors",
        "datadog_failed_pods",
        "datadog_403_errors",
    )
    if name in datadog_timerange_tools:
        return func(
            service_or_query(args),
            coerce_timerange_hours(args.get("timerange"), default=4),
        )

    if name in ("datadog_search", "datadog_services"):
        return func(
            text_arg(args, "query"),
            coerce_timerange_hours(args.get("timerange"), default=4),
        )

    if name == "datadog_maintenance_windows":
        return func(
            text_arg(
                args,
                "question",
                "query",
                default="maintenance windows next 24 hours",
            )
        )

    if name == "grm_deployments":
        question = text_arg(args, "question")
        query = text_arg(args, "query")
        timerange = args.get("timerange_hours")
        if timerange is None and args.get("timerange") is not None:
            timerange = args.get("timerange")
        if question and not query and timerange is None:
            return func(question=question)
        return func(
            question=question,
            query=query,
            timerange_hours=timerange,
        )

    if name in ("grafana_dns_mapper", "grafana_savant_z2"):
        return func(
            text_arg(args, "query"),
            coerce_timerange_hours(args.get("timerange"), default=4),
        )

    if name == "grafana_dashboard_list":
        return func()

    if name == "arlo_public_status":
        return func(text_arg(args, "query"))

    if name == "noc_kt_search":
        return func(
            query=text_arg(args, "query"),
            question=text_arg(args, "question"),
        )

    if name == "pagerduty_samsung_board":
        return func(
            dashboard_id=text_arg(args, "dashboard_id"),
            query=text_arg(args, "query", "service"),
        )

    if name == "shift_report":
        return func(
            mode=text_arg(args, "mode"),
            question=text_arg(args, "question"),
        )

    if name == "status_monitor_summary":
        timerange = args.get("timerange")
        if timerange is None and args.get("timerange_hours") is not None:
            timerange = args.get("timerange_hours")
        return func(
            timerange=timerange,
            question=text_arg(args, "question"),
            force_refresh=bool(args.get("force_refresh")),
        )

    if name == "aws_cloudtrail_search":
        return func(
            resource_name=text_arg(args, "resource_name", "query"),
            resource_type=text_arg(args, "resource_type", default="OTHER"),
            region=text_arg(args, "region", default="us-east-1"),
            account_id=text_arg(args, "account_id"),
            lookback_days=int(args.get("lookback_days") or 7),
            max_events=int(args.get("max_events") or 50),
        )

    if name == "aws_connect_monitor":
        return func(
            instance_id=text_arg(args, "instance_id"),
            region=text_arg(args, "region"),
            force_refresh=bool(args.get("force_refresh")),
        )

    if name == "pagerduty_incidents":
        return func(
            text_arg(args, "query", "service", "status"),
        )

    if name == "service_owners":
        return func(text_arg(args, "service", "query"))

    if name in (
        "wiki_search",
        "arlo_versions",
        "deployed_fw_versions",
        "pagerduty_analytics",
        "pagerduty_insights",
        "oncall_schedule",
    ):
        return func(text_arg(args, "query", "service"))

    return func(service_or_query(args))
