#!/usr/bin/env python3
"""
OneView GOC AI - MCP Server (stdio mode)
Run this script to expose tools via MCP stdio protocol for Claude Desktop
"""

import asyncio
import logging
from mcp.server.stdio import stdio_server
from mcp_server import get_mcp_server

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

logger = logging.getLogger(__name__)


async def main():
    """Run the MCP server in stdio mode"""
    logger.info("🚀 Starting OneView GOC AI MCP Server (stdio mode)")
    
    # Get the MCP server instance
    server = get_mcp_server()
    
    logger.info("✅ MCP Server initialized with 15 tools")
    logger.info("📡 Listening on stdin/stdout for MCP protocol messages...")
    
    # Run the server with stdio transport
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 MCP Server stopped by user")
    except Exception as e:
        logger.error(f"❌ MCP Server error: {e}")
        raise
