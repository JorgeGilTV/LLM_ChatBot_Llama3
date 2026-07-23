"""
OneView GOC AI - MCP Server
Exposes all integrated tools (Datadog, PagerDuty, Jira, Splunk, Confluence) as MCP server
"""

import asyncio
import json
import logging
import os
from typing import Any, Sequence

try:
    from dotenv import load_dotenv

    _MCP_ROOT = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(_MCP_ROOT, ".env"))
except ImportError:
    pass
try:
    from tools.aws_secrets_env import load_aws_secrets_manager_into_environ

    load_aws_secrets_manager_into_environ()
except ImportError:
    pass
except Exception as e:
    if (os.getenv("AWS_SECRETS_MANAGER_REQUIRED") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        raise
    logging.getLogger(__name__).warning("AWS Secrets Manager (optional): %s", e)

from mcp.server import Server
from mcp.types import Tool, TextContent

# Import all tool functions
from tools.confluence_tool import confluence_search
from tools.service_owners import service_owners_search
from tools.oncall_support import confluence_oncall_today
from tools.read_versions import read_versions
from tools.deployed_fw_versions import read_deployed_fw_versions
from tools.datadog_dashboards import (
    read_datadog_dashboards, 
    read_datadog_errors_only, 
    read_datadog_adt, 
    read_datadog_adt_errors_only, 
    read_datadog_all_errors, 
    read_datadog_failed_pods, 
    read_datadog_403_errors,
    read_datadog_samsung,
    read_datadog_samsung_errors_only,
    read_datadog_redmetrics_us,
    search_datadog_dashboards,
    search_datadog_services
)
from tools.datadog_downtimes import get_datadog_maintenance_windows
from tools.deployments_calendar import get_grm_deployments_mcp
from tools.mcp_tool_dispatch import invoke_tool
from tools.noc_kt import noc_kt_search_mcp
from tools.read_arlo_status import read_arlo_status
from tools.pagerduty_samsung_scrape import get_pagerduty_samsung_board_html
from tools.mcp_phase3_tools import (
    aws_cloudtrail_search_mcp,
    aws_connect_monitor_mcp,
    get_shift_report_mcp,
    get_status_monitor_summary_mcp,
)
from tools.shm_tools import get_shm_daily_mcp, get_shm_metrics_mcp
from tools.grafana_dashboards import get_grafana_dns_mapper, get_grafana_savant_z2, get_grafana_dashboard_list
from tools.splunk_tool import (
    read_splunk_p0_dashboard,
    read_splunk_p0_cvr_dashboard,
    read_splunk_p0_adt_dashboard,
    read_splunk_p0_us_infra_dashboard,
)
from tools.pagerduty_tool import get_pagerduty_incidents
from tools.pagerduty_analytics import get_pagerduty_analytics
from tools.pagerduty_insights import get_pagerduty_insights
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create MCP server instance
mcp_server = Server("oneview-goc-ai")

# Tool definitions with their functions
TOOL_REGISTRY = {
    "wiki_search": {
        "description": "Search Arlo Confluence documentation for workarounds, guides, and technical information",
        "function": confluence_search,
        "schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query for Confluence documents"
                }
            },
            "required": ["query"]
        }
    },
    "service_owners": {
        "description": "Find the owner/team responsible for specific Arlo services",
        "function": service_owners_search,
        "schema": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Service name to look up owner"
                }
            },
            "required": ["service"]
        }
    },
    "arlo_versions": {
        "description": "Get version information from versions.arlocloud.com for Arlo services",
        "function": read_versions,
        "schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Service or version query"
                }
            },
            "required": ["query"]
        }
    },
    "deployed_fw_versions": {
        "description": "Get deployed firmware / version matrix from deployed-fw-versions.arlocloud.com (internal)",
        "function": read_deployed_fw_versions,
        "schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional filter substring to match table rows (e.g. product or FW version)"
                }
            }
        }
    },
    "datadog_search": {
        "description": "Search and list Datadog dashboards by name or query. Returns dashboard titles, IDs, and links.",
        "function": search_datadog_dashboards,
        "schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term to filter dashboards (e.g., 'streaming', 'redis', 'api')"
                },
                "timerange": {
                    "type": "integer",
                    "description": "Time range in hours for dashboard links (default: 4)",
                    "default": 4
                }
            }
        }
    },
    "datadog_services": {
        "description": "Search and list Datadog APM services by name (e.g., 'backend-hmsmatter', 'api-payment'). Shows service performance metrics.",
        "function": search_datadog_services,
        "schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Service name to search (e.g., 'hmsmatter', 'payment', 'streaming')"
                },
                "timerange": {
                    "type": "integer",
                    "description": "Time range in hours for service links (default: 4)",
                    "default": 4
                }
            }
        }
    },
    "datadog_maintenance_windows": {
        "description": (
            "List Datadog monitor downtimes (maintenance windows) from "
            "https://arlo.datadoghq.com/monitors/downtimes — creator, schedule, active status. "
            "Filters NOC team creators and tags (team:noc, partner hosts, env:production/prod/prd/adt_prod). "
            "Default window: active now + next 24 hours."
        ),
        "function": get_datadog_maintenance_windows,
        "schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "Natural-language time/filter hint, e.g. 'maintenance windows next 24 hours', "
                        "'downtimes last 48 hours', 'all creators next 12 hours'"
                    ),
                }
            },
            "required": ["question"],
        },
    },
    "datadog_red_metrics": {
        "description": "Get Datadog RED metrics (Rate, Errors, Duration) for Arlo services",
        "function": read_datadog_dashboards,
        "schema": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Service name to query"
                },
                "timerange": {
                    "type": "string",
                    "description": "Time range (1h, 4h, 1d, 7d, 1w, 1mo)",
                    "default": "4h"
                }
            },
            "required": ["service"]
        }
    },
    "datadog_red_adt": {
        "description": "Get Datadog RED metrics specifically for ADT dashboard",
        "function": read_datadog_adt,
        "schema": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Service name to query"
                },
                "timerange": {
                    "type": "string",
                    "description": "Time range (1h, 4h, 1d, 7d, 1w, 1mo)",
                    "default": "4h"
                }
            },
            "required": ["service"]
        }
    },
    "datadog_red_samsung": {
        "description": "Get Datadog RED metrics for Samsung / partner network dashboard",
        "function": read_datadog_samsung,
        "schema": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Service name to query"
                },
                "timerange": {
                    "type": "string",
                    "description": "Time range (1h, 4h, 1d, 7d, 1w, 1mo)",
                    "default": "4h"
                }
            },
            "required": ["service"]
        }
    },
    "datadog_red_metrics_us": {
        "description": "Get Datadog RED metrics for US region dashboard",
        "function": read_datadog_redmetrics_us,
        "schema": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Service name to query"
                },
                "timerange": {
                    "type": "string",
                    "description": "Time range (1h, 4h, 1d, 7d, 1w, 1mo)",
                    "default": "4h"
                }
            },
            "required": ["service"]
        }
    },
    "datadog_errors": {
        "description": "Show services with errors > 0 from RED Metrics and ADT dashboards",
        "function": read_datadog_all_errors,
        "schema": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Optional: filter by service name"
                },
                "timerange": {
                    "type": "string",
                    "description": "Time range (1h, 4h, 1d, 7d, 1w, 1mo)",
                    "default": "4h"
                }
            }
        }
    },
    "datadog_samsung_errors": {
        "description": "Show Samsung network services with errors > 0 from Datadog",
        "function": read_datadog_samsung_errors_only,
        "schema": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Optional: filter by service name"
                },
                "timerange": {
                    "type": "string",
                    "description": "Time range (1h, 4h, 1d, 7d, 1w, 1mo)",
                    "default": "4h"
                }
            }
        }
    },
    "datadog_failed_pods": {
        "description": "Monitor Kubernetes pods with failures (ImagePullBackOff, CrashLoop) causing errors",
        "function": read_datadog_failed_pods,
        "schema": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Optional: filter by service name"
                },
                "timerange": {
                    "type": "string",
                    "description": "Time range (1h, 4h, 1d, 7d, 1w, 1mo)",
                    "default": "4h"
                }
            }
        }
    },
    "datadog_403_errors": {
        "description": "Monitor 403 Forbidden errors from APM traces (Artifactory, authentication issues)",
        "function": read_datadog_403_errors,
        "schema": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Optional: filter by service name"
                },
                "timerange": {
                    "type": "string",
                    "description": "Time range (1h, 4h, 1d, 7d, 1w, 1mo)",
                    "default": "4h"
                }
            }
        }
    },
    "splunk_p0_streaming": {
        "description": "Get P0 Streaming dashboard data from Splunk",
        "function": read_splunk_p0_dashboard,
        "schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional: search query"
                },
                "timerange": {
                    "type": "string",
                    "description": "Time range (e.g. 24h, 2d, 4h); default 24h",
                    "default": "24h"
                }
            }
        }
    },
    "splunk_p0_cvr": {
        "description": "Get P0 CVR Streaming dashboard data from Splunk",
        "function": read_splunk_p0_cvr_dashboard,
        "schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional: search query"
                },
                "timerange": {
                    "type": "string",
                    "description": "Time range (e.g. 24h, 2d, 4h); default 24h",
                    "default": "24h"
                }
            }
        }
    },
    "splunk_p0_adt": {
        "description": "Get P0 ADT Streaming dashboard data from Splunk",
        "function": read_splunk_p0_adt_dashboard,
        "schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional: search query"
                },
                "timerange": {
                    "type": "string",
                    "description": "Time range (e.g. 24h, 2d, 4h); default 24h",
                    "default": "24h"
                }
            }
        }
    },
    "splunk_p0_us_infra": {
        "description": "Get P0 Streaming US infra dashboard data from Splunk (zones z1–z4)",
        "function": read_splunk_p0_us_infra_dashboard,
        "schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional: host filter / search query"
                },
                "timerange": {
                    "type": "string",
                    "description": "Time range (e.g. 24h, 2d, 4h); default 24h",
                    "default": "24h"
                }
            }
        }
    },
    "grafana_dns_mapper": {
        "description": "Monitor DNS Mapper IP usage for HMS/CVR streaming services in Grafana (Zone 4)",
        "function": get_grafana_dns_mapper,
        "schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional: search query"
                },
                "timerange": {
                    "type": "integer",
                    "description": "Time range in hours (default: 4)",
                    "default": 4
                }
            }
        }
    },
    "grafana_savant_z2": {
        "description": "Monitor Savant infrastructure in Harlem datacenter - Zone 2 (z2)",
        "function": get_grafana_savant_z2,
        "schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional: search query"
                },
                "timerange": {
                    "type": "integer",
                    "description": "Time range in hours (default: 4)",
                    "default": 4
                }
            }
        }
    },
    "grafana_dashboard_list": {
        "description": "List available Grafana dashboards (DNS Mapper, Savant z2, etc.)",
        "function": get_grafana_dashboard_list,
        "schema": {
            "type": "object",
            "properties": {},
        },
    },
    "grm_deployments": {
        "description": (
            "GRM Calendar deployments from Confluence — upcoming or past releases. "
            "Natural language supported (e.g. 'next deployments 48 hours', 'past 3 deployments')."
        ),
        "function": get_grm_deployments_mcp,
        "schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "Natural-language query, e.g. 'upcoming deployments next 24 hours', "
                        "'past 3 deployments', 'GRM calendar last 48 hours'"
                    ),
                },
                "query": {
                    "type": "string",
                    "description": "Optional service filter or limit:N (structured override)",
                },
                "timerange_hours": {
                    "type": "integer",
                    "description": "Hours ahead (positive) or behind (negative). Default 24.",
                },
            },
        },
    },
    "pagerduty_incidents": {
        "description": "Get active incidents from PagerDuty for Arlo services",
        "function": get_pagerduty_incidents,
        "schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by status: triggered, acknowledged, resolved",
                    "enum": ["triggered", "acknowledged", "resolved", "all"]
                }
            }
        }
    },
    "pagerduty_analytics": {
        "description": "Get PagerDuty analytics with charts and metrics",
        "function": get_pagerduty_analytics,
        "schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional: filter query"
                }
            }
        }
    },
    "pagerduty_insights": {
        "description": "Get incident activity insights and trends from PagerDuty",
        "function": get_pagerduty_insights,
        "schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional: filter query"
                }
            }
        }
    },
    "oncall_schedule": {
        "description": "Get current on-call schedule from Confluence",
        "function": confluence_oncall_today,
        "schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional: date or team query"
                }
            }
        }
    },
    "arlo_public_status": {
        "description": (
            "Scrape https://status.arlo.com — overall health, core services, and past incidents."
        ),
        "function": read_arlo_status,
        "schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional; reserved for future filters",
                }
            },
        },
    },
    "noc_kt_search": {
        "description": (
            "Search the NOC Knowledge Transfer table in Confluence (escalations, runbooks, contacts)."
        ),
        "function": noc_kt_search_mcp,
        "schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term to match rows (service, team, contact, etc.)",
                },
                "question": {
                    "type": "string",
                    "description": "Alias for query when using natural language",
                },
            },
        },
    },
    "pagerduty_samsung_board": {
        "description": (
            "Scrape Samsung PagerDuty external status dashboard (default board PRBJIO4) — "
            "active and recently resolved incidents without REST API."
        ),
        "function": get_pagerduty_samsung_board_html,
        "schema": {
            "type": "object",
            "properties": {
                "dashboard_id": {
                    "type": "string",
                    "description": "External status dashboard ID (default SAMSUNG_STATUS_DASHBOARD_ID or PRBJIO4)",
                },
                "query": {
                    "type": "string",
                    "description": "Optional filter on title/service/status",
                },
            },
        },
    },
    "shift_report": {
        "description": (
            "Shift handoff report (PagerDuty, Datadog, Slack, GRM, Jira, Outlook) for shift1/2/3 "
            "Mexico time. Heavy orchestration via MintMCP + Bedrock — may take several minutes."
        ),
        "function": get_shift_report_mcp,
        "schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "description": "shift1 | shift2 | shift3 (default shift1)",
                },
                "question": {
                    "type": "string",
                    "description": "Natural language, e.g. 'shift 2 handoff report'",
                },
            },
        },
    },
    "status_monitor_summary": {
        "description": (
            "Compact Status Monitor hub summary — per-environment healthy/warning/critical counts "
            "and Datadog monitor alerts rollup."
        ),
        "function": get_status_monitor_summary_mcp,
        "schema": {
            "type": "object",
            "properties": {
                "timerange": {
                    "type": "integer",
                    "description": "Hours to look back (1–24, default 1)",
                },
                "question": {
                    "type": "string",
                    "description": "Optional NL hint for timerange, e.g. 'status monitor last 4 hours'",
                },
                "force_refresh": {
                    "type": "boolean",
                    "description": "Bypass cache",
                    "default": False,
                },
            },
        },
    },
    "aws_cloudtrail_search": {
        "description": (
            "Search AWS CloudTrail events by resource name (admin/niche). "
            "Requires resource_name, account_id (12 digits), region."
        ),
        "function": aws_cloudtrail_search_mcp,
        "schema": {
            "type": "object",
            "properties": {
                "resource_name": {"type": "string"},
                "resource_type": {
                    "type": "string",
                    "description": "EC2, Lambda, IAM, S3, OTHER, etc.",
                    "default": "OTHER",
                },
                "region": {"type": "string", "default": "us-east-1"},
                "account_id": {"type": "string", "description": "12-digit AWS account ID"},
                "lookback_days": {"type": "integer", "default": 7},
                "max_events": {"type": "integer", "default": 50},
            },
            "required": ["resource_name", "account_id"],
        },
    },
    "aws_connect_monitor": {
        "description": (
            "AWS Connect contact-center health snapshot (queues, agents, alerts). "
            "Uses CONNECT_INSTANCE_ID / CONNECT_REGION from env when omitted."
        ),
        "function": aws_connect_monitor_mcp,
        "schema": {
            "type": "object",
            "properties": {
                "instance_id": {"type": "string"},
                "region": {"type": "string"},
                "force_refresh": {"type": "boolean", "default": False},
            },
        },
    },
    "shm_metrics": {
        "description": (
            "SHM pillar scores and KPI metrics from shmview.arlocloud.com — Customer Engagement, "
            "Protect & Connect, Customer Satisfaction, Smart AI, Onboarding. Parses month names "
            "(e.g. July/julio), last N months, and renders Chart.js trend graphs like shmview. "
            "Includes iOS/Android app store ratings, CSAT, crash-free sessions, DAU/MAU."
        ),
        "function": get_shm_metrics_mcp,
        "schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Natural language, e.g. 'SHM customer satisfaction iOS ratings'",
                },
                "query": {"type": "string", "description": "Alias for question"},
                "force_live": {
                    "type": "boolean",
                    "description": "Refresh live Tableau app ratings (slower)",
                    "default": False,
                },
            },
        },
    },
    "shm_daily": {
        "description": (
            "SHM daily active users by OS (iOS, Android, Web) from shmdaily.arlocloud.com — "
            "Splunk active_user_count_v2 averages and platform split."
        ),
        "function": get_shm_daily_mcp,
        "schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Natural language, e.g. 'DAU by OS last 30 days'",
                },
                "query": {"type": "string", "description": "Alias for question"},
                "timerange": {
                    "type": "integer",
                    "description": "Lookback in hours (default 720 = 30d)",
                    "default": 720,
                },
                "earliest": {
                    "type": "string",
                    "description": "Splunk earliest override, e.g. -30d@d",
                },
                "latest": {
                    "type": "string",
                    "description": "Splunk latest override, e.g. now",
                },
            },
        },
    },
}


@mcp_server.list_tools()
async def list_tools() -> list[Tool]:
    """List all available tools"""
    tools = []
    
    for tool_name, tool_info in TOOL_REGISTRY.items():
        tools.append(Tool(
            name=tool_name,
            description=tool_info["description"],
            inputSchema=tool_info["schema"]
        ))
    
    logger.info(f"📋 MCP Server: Listed {len(tools)} tools")
    return tools


@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict) -> Sequence[TextContent]:
    """Execute a tool with given arguments"""
    
    logger.info(f"🔧 MCP Server: Calling tool '{name}' with args: {arguments}")
    
    if name not in TOOL_REGISTRY:
        error_msg = f"Tool '{name}' not found. Available tools: {', '.join(TOOL_REGISTRY.keys())}"
        logger.error(f"❌ {error_msg}")
        return [TextContent(type="text", text=error_msg)]
    
    try:
        tool_info = TOOL_REGISTRY[name]
        func = tool_info["function"]
        result = invoke_tool(name, arguments, func)
        
        logger.info(f"✅ MCP Server: Tool '{name}' executed successfully")
        
        # Return result as text content
        return [TextContent(
            type="text",
            text=str(result)
        )]
        
    except Exception as e:
        error_msg = f"Error executing tool '{name}': {str(e)}"
        logger.error(f"❌ {error_msg}")
        return [TextContent(
            type="text",
            text=error_msg
        )]


def get_mcp_server():
    """Get the MCP server instance"""
    return mcp_server
