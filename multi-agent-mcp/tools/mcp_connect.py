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
