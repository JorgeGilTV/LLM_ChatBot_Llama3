"""
MCP client transport selection: MintMCP (streamable HTTP + Bearer) vs legacy SSE ALB.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

MINTMCP_DEFAULT_URL = "https://app.mintmcp.com/o/arlo/s/arlo/mcp"
_LEGACY_INTERNAL_MCP_URL = (
    "http://internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com:8080"
)


def get_mintmcp_url() -> str:
    return (os.getenv("MINTMCP_URL") or MINTMCP_DEFAULT_URL).strip().rstrip("/")


def get_mcp_api_key() -> str:
    return (os.getenv("MINTMCP_API_KEY") or os.getenv("MINTMCP_BEARER_TOKEN") or "").strip()


def is_mintmcp_url(url: str) -> bool:
    return "mintmcp.com" in (url or "").lower()


def get_mcp_server_url() -> str:
    """Active MCP base URL (MintMCP full /mcp URL, or legacy host without /sse)."""
    explicit = (os.getenv("MCP_SERVER_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    if get_mcp_api_key():
        return get_mintmcp_url()
    if (os.getenv("ECS_CONTAINER_METADATA_URI_V4") or "").strip() or (
        (os.getenv("ECS_SYNC_SECRETS_ON_SAVE") or "").strip().lower() in ("1", "true", "yes", "on")
    ):
        port = (os.getenv("PORT") or "8080").strip()
        return f"http://127.0.0.1:{port}"
    return _LEGACY_INTERNAL_MCP_URL


def get_mcp_sse_endpoint() -> str:
    url = get_mcp_server_url()
    if is_mintmcp_url(url):
        return url
    return f"{url}/sse"


def get_mcp_auth_headers() -> dict[str, str]:
    if is_mintmcp_url(get_mcp_server_url()) and get_mcp_api_key():
        return {"Authorization": f"Bearer {get_mcp_api_key()}"}
    return {}


def mcp_transport_label() -> str:
    url = get_mcp_server_url()
    if is_mintmcp_url(url):
        return f"MintMCP streamable ({url})"
    if "127.0.0.1" in url or "localhost" in url:
        return f"embedded SSE ({url})"
    return f"SSE ({get_mcp_sse_endpoint()})"


@asynccontextmanager
async def open_mcp_session() -> AsyncIterator[Any]:
    """Open initialized MCP ClientSession (MintMCP or legacy SSE)."""
    from mcp import ClientSession

    url = get_mcp_server_url()
    headers = get_mcp_auth_headers() or None

    if is_mintmcp_url(url):
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(url, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    else:
        from mcp.client.sse import sse_client

        sse_url = url if url.endswith("/sse") else f"{url}/sse"
        async with sse_client(sse_url, headers=headers) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


class _LocalMcpTool:
    """Mimics the mcp.types.Tool shape (name + description) for the local fallback."""

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description


class _LocalMcpListToolsResult:
    def __init__(self, tools: list[_LocalMcpTool]):
        self.tools = tools


class _LocalMcpContentItem:
    def __init__(self, text: str):
        self.text = text


class _LocalMcpCallToolResult:
    def __init__(self, text: str):
        self.content = [_LocalMcpContentItem(text)]


class LocalMcpSession:
    """
    In-process fallback with the same list_tools()/call_tool() shape as mcp.ClientSession,
    backed directly by this app's own TOOL_REGISTRY (mcp_server.py) instead of an MCP transport.
    Used when the primary MCP server (MintMCP / legacy SSE) can't be reached.
    """

    async def list_tools(self) -> _LocalMcpListToolsResult:
        from mcp_server import TOOL_REGISTRY

        tools = [
            _LocalMcpTool(name, info.get("description", ""))
            for name, info in TOOL_REGISTRY.items()
        ]
        return _LocalMcpListToolsResult(tools)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> _LocalMcpCallToolResult:
        from mcp_server import TOOL_REGISTRY
        from tools.mcp_tool_dispatch import invoke_tool

        info = TOOL_REGISTRY.get(name)
        if not info:
            return _LocalMcpCallToolResult(f"Tool '{name}' not found in local registry")
        try:
            result = invoke_tool(name, arguments or {}, info["function"])
        except Exception as e:
            return _LocalMcpCallToolResult(f"Error executing tool '{name}': {e}")
        return _LocalMcpCallToolResult(str(result))


@asynccontextmanager
async def open_local_mcp_session() -> AsyncIterator[Any]:
    """Fallback session backed by this app's own local tool registry (no network transport)."""
    yield LocalMcpSession()


@asynccontextmanager
async def open_mcp_session_with_fallback() -> AsyncIterator[Any]:
    """
    Try the primary MCP transport (MintMCP or legacy SSE) first. If connecting/initializing it
    fails (e.g. MintMCP gateway misconfigured — "Server not found"), fall back to this app's own
    local tool registry so Bedrock_Report can still run, just with fewer tools.
    """
    try:
        async with open_mcp_session() as session:
            yield session
            return
    except Exception as e:
        print(f"⚠️  Primary MCP session failed ({e}); falling back to local MCP tools...")

    async with open_local_mcp_session() as session:
        yield session
