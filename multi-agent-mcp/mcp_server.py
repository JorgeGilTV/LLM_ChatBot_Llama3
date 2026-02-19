"""
OneView GOC AI - MCP Server
Exposes all integrated tools (Datadog, PagerDuty, Jira, Splunk, Confluence) as MCP server
"""

import asyncio
import json
import logging
from typing import Any, Sequence

from mcp.server import Server
from mcp.types import Tool, TextContent

# Import all tool functions
from tools.confluence_tool import confluence_search
from tools.service_owners import service_owners_search
from tools.oncall_support import confluence_oncall_today
from tools.read_versions import read_versions
from tools.datadog_dashboards import (
    read_datadog_dashboards, 
    read_datadog_errors_only, 
    read_datadog_adt, 
    read_datadog_adt_errors_only, 
    read_datadog_all_errors, 
    read_datadog_failed_pods, 
    read_datadog_403_errors
)
from tools.splunk_tool import read_splunk_p0_dashboard, read_splunk_p0_cvr_dashboard, read_splunk_p0_adt_dashboard
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
                    "description": "Time range (1h, 4h, 1d, 7d, 1w, 1mo)",
                    "default": "4h"
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
                    "description": "Time range (1h, 4h, 1d, 7d, 1w, 1mo)",
                    "default": "4h"
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
                    "description": "Time range (1h, 4h, 1d, 7d, 1w, 1mo)",
                    "default": "4h"
                }
            }
        }
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
    }
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
        
        # Extract arguments
        query = arguments.get("query", "")
        service = arguments.get("service", "")
        timerange = arguments.get("timerange", "4h")
        status = arguments.get("status", "all")
        
        # Determine which arguments to pass based on function signature
        if name in ["datadog_red_metrics", "datadog_red_adt", "datadog_errors", "datadog_failed_pods", "datadog_403_errors",
                    "splunk_p0_streaming", "splunk_p0_cvr", "splunk_p0_adt"]:
            # These tools need timerange
            input_text = service if service else query
            result = func(input_text, timerange)
        elif name == "pagerduty_incidents":
            result = func(status)
        else:
            # Simple query-based tools
            input_text = service if service else query
            result = func(input_text)
        
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
