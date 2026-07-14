"""
GocBedrock MCP Integration - Direct Tool Execution

This module provides direct access to MCP tools without AI reasoning:

1️⃣ Receive question → Connect to MCP server
2️⃣ List available tools → Get all ~70 tools from MCP
3️⃣ Execute tools → MCP Server connects to real APIs (Jira, Datadog, Splunk, etc.)
4️⃣ Return raw results → Display tool results directly to user

This approach provides unfiltered access to all MCP tool results without
intermediate AI processing or filtering.

Supports two modes:
- SDK Async Mode (Python 3.10+): Uses official MCP SDK with async/await
- HTTP Fallback Mode (Python 3.9+): Uses direct HTTP calls to MCP server
"""

import asyncio
import ast
import html
import os
import re
import json
import requests
import time
import logging
from typing import Dict, List, Any, Optional

# Try to import google.generativeai
try:
    import google.generativeai as genai
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    print("⚠️  WARNING: google-generativeai not installed. Install with: pip3 install google-generativeai")

# Try to import MCP SDK (requires Python 3.10+)
try:
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    MCP_SDK_AVAILABLE = True
    print("✅ MCP SDK available - using async mode")
except ImportError:
    MCP_SDK_AVAILABLE = False
    print("⚠️  MCP SDK not available - using HTTP fallback mode")

# MCP Server Configuration (see tools/mcp_connect.py)
from tools.mcp_connect import (
    get_mcp_auth_headers,
    get_mcp_server_url,
    get_mcp_sse_endpoint,
    is_mintmcp_url,
    mcp_transport_label,
    open_mcp_session,
)


def _mcp_call_result_text(result) -> str:
    parts: list[str] = []
    if hasattr(result, "content"):
        for content in result.content:
            if hasattr(content, "text"):
                parts.append(content.text)
    return "".join(parts)


def _mcp_direct_response_html(title: str, body_html: str, gradient: str) -> str:
    return f"""
        <div style='background-color: white; padding: 16px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
            <div style='background: {gradient}; padding: 12px; border-radius: 6px; margin-bottom: 16px;'>
                <h2 style='margin: 0; color: white; font-size: 16px;'>{title}</h2>
            </div>
            <div style='background-color: #f7fafc; padding: 16px; border-radius: 4px;'>
                {body_html}
            </div>
        </div>
        """


def _mcp_uses_local_server() -> bool:
    u = get_mcp_server_url().lower()
    return "127.0.0.1" in u or "localhost" in u


def _mcp_connect_hint_html() -> str:
    if _mcp_uses_local_server():
        return (
            "• El MCP corre dentro de este mismo servicio (no requiere VPN)<br>"
            "• Revisa logs ECS o reinicia el task si persiste<br>"
        )
    if is_mintmcp_url(get_mcp_server_url()):
        return (
            "• MintMCP requiere MINTMCP_API_KEY válido<br>"
            "• Revisa permisos del gateway arlo en app.mintmcp.com<br>"
        )
    return (
        "• Conéctate a Arlo VPN (GlobalProtect) para alcanzar el MCP interno<br>"
        "• Comprueba DNS y red corporativa<br>"
    )


# Backward-compatible module constants
MCP_SERVER_URL = get_mcp_server_url()
MCP_SSE_ENDPOINT = get_mcp_sse_endpoint()


class SimpleMCPClient:
    """MCP client: legacy SSE (ALB) or MintMCP streamable HTTP."""
    
    def __init__(self, server_url: str):
        self.server_url = server_url.rstrip("/")
        self._mint = is_mintmcp_url(self.server_url)
        self.session = requests.Session()
        for k, v in get_mcp_auth_headers().items():
            self.session.headers[k] = v
        self.session_id = None
        self.message_endpoint = None
        self.sse_connection = None
        self.sse_responses = {}
        self.sse_thread = None
        self.sse_running = False

    def _mint_list_tools(self) -> List[Dict[str, Any]]:
        async def _run():
            async with open_mcp_session() as session:
                r = await session.list_tools()
                return [{"name": t.name, "description": t.description or ""} for t in r.tools]

        return asyncio.run(_run())

    def _mint_call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Optional[str]:
        async def _run():
            async with open_mcp_session() as session:
                r = await session.call_tool(tool_name, arguments)
                parts = []
                for item in r.content or []:
                    if hasattr(item, "text"):
                        parts.append(str(item.text))
                    elif isinstance(item, dict) and item.get("type") == "text":
                        parts.append(str(item.get("text", "")))
                return "\n".join(parts) if parts else None

        return asyncio.run(_run())
        
    def _sse_reader_thread(self):
        """Background thread to read SSE events continuously."""
        try:
            print(f"🔗 SSE reader thread starting...")
            
            for line in self.sse_connection.iter_lines(decode_unicode=True):
                if not self.sse_running:
                    break
                
                if line.startswith('data: '):
                    data = line[6:].strip()
                    
                    # Skip endpoint announcements
                    if data.startswith('/messages/'):
                        continue
                    
                    try:
                        # Try to parse as JSON
                        event_data = json.loads(data)
                        request_id = event_data.get('id')
                        
                        if request_id:
                            print(f"📨 Got SSE response for request {request_id}")
                            self.sse_responses[request_id] = event_data
                    except json.JSONDecodeError:
                        continue
            
            print(f"🔌 SSE reader thread stopped")
        except Exception as e:
            print(f"❌ SSE reader thread error: {e}")
            self.sse_running = False
    
    def initialize(self) -> bool:
        """Initialize MCP session via SSE or MintMCP."""
        if self._mint:
            try:
                tools = self._mint_list_tools()
                print(f"✅ MintMCP connected — {len(tools)} tools via {self.server_url}")
                return True
            except Exception as e:
                print(f"❌ MintMCP initialization error: {e}")
                return False
        try:
            import threading
            
            print(f"🔗 Connecting to SSE endpoint: {self.server_url}/sse")
            
            # Connect to SSE endpoint and keep connection open
            self.sse_connection = self.session.get(
                f"{self.server_url}/sse",
                stream=True,
                timeout=None  # No timeout for persistent connection
            )
            
            if self.sse_connection.status_code != 200:
                print(f"⚠️  SSE connection failed: {self.sse_connection.status_code}")
                return False
            
            # Read the first SSE event to get session_id
            for line in self.sse_connection.iter_lines(decode_unicode=True):
                if line.startswith('data: '):
                    data = line[6:].strip()
                    # Parse the endpoint URL
                    if data.startswith('/messages/'):
                        self.message_endpoint = f"{self.server_url}{data}"
                        # Extract session_id from URL
                        import urllib.parse
                        parsed = urllib.parse.urlparse(data)
                        params = urllib.parse.parse_qs(parsed.query)
                        self.session_id = params.get('session_id', [None])[0]
                        print(f"✅ Got session_id: {self.session_id}")
                        break
            
            if not self.session_id or not self.message_endpoint:
                print("⚠️  Failed to get session_id from SSE")
                return False
            
            # Start background thread to read SSE events
            self.sse_running = True
            self.sse_thread = threading.Thread(target=self._sse_reader_thread, daemon=True)
            self.sse_thread.start()
            print(f"✅ SSE reader thread started")
            
            # Send initialization request
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "gocbedrock-client",
                        "version": "1.0.0"
                    }
                }
            }
            
            init_response = self.session.post(
                self.message_endpoint,
                json=payload,
                timeout=10
            )
            
            # Accept both 200 (OK) and 202 (Accepted) for async servers
            if init_response.status_code in [200, 202]:
                # For 202, wait for response from SSE
                if init_response.status_code == 202:
                    print(f"✅ MCP Session accepted (status 202) - waiting for SSE confirmation...")
                    # Wait up to 5 seconds for init response
                    for _ in range(50):
                        if 1 in self.sse_responses:
                            result = self.sse_responses[1]
                            print(f"✅ Got init confirmation via SSE")
                            if result.get('result', {}).get('serverInfo'):
                                print(f"   Server: {result['result']['serverInfo']}")
                            return True
                        time.sleep(0.1)
                    # Even if we don't get confirmation, proceed if we have session_id
                    print(f"⚠️  No init confirmation from SSE, but proceeding with session_id")
                    return True
                else:
                    try:
                        result = init_response.json() if init_response.text else {}
                        print(f"✅ MCP Session initialized (status {init_response.status_code})")
                        if result.get('result', {}).get('serverInfo'):
                            print(f"   Server: {result['result']['serverInfo']}")
                        return True
                    except:
                        print(f"✅ MCP Session accepted (status {init_response.status_code})")
                        return True
            else:
                print(f"⚠️  MCP initialization failed: {init_response.status_code}")
                print(f"   Response: {init_response.text[:200]}")
                return False
                
        except Exception as e:
            print(f"❌ MCP initialization error: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def list_tools(self) -> List[Dict[str, Any]]:
        """List available tools from MCP server."""
        if self._mint:
            try:
                return self._mint_list_tools()
            except Exception as e:
                print(f"❌ Error listing MintMCP tools: {e}")
                return []
        try:
            if not self.message_endpoint:
                print("⚠️  No message endpoint - not initialized")
                return []
            
            payload = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {}
            }
            
            response = self.session.post(
                self.message_endpoint,
                json=payload,
                timeout=10
            )
            
            print(f"📊 list_tools response: status={response.status_code}, body_length={len(response.text)}, body={response.text[:100]}")
            
            if response.status_code in [200, 202]:
                # Check if response has content
                if not response.text or response.text.strip() == "" or response.text.strip().lower() == "accepted":
                    print(f"⚠️  Empty/minimal response body (status {response.status_code}) - reading from SSE stream")
                    # For async servers, response comes via SSE - need to reconnect and read
                    return self._read_sse_response(request_id=2)
                
                try:
                    result = response.json()
                    tools = result.get('result', {}).get('tools', [])
                    print(f"✅ Found {len(tools)} MCP tools")
                    return tools
                except json.JSONDecodeError as e:
                    print(f"⚠️  Failed to parse JSON: {e} - trying SSE stream instead")
                    return self._read_sse_response(request_id=2)
            else:
                print(f"⚠️  Failed to list tools: {response.status_code}")
                print(f"   Response: {response.text[:200]}")
                return []
                
        except Exception as e:
            print(f"❌ Error listing tools: {e}")
            return []
    
    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Optional[str]:
        """Call a specific MCP tool."""
        if self._mint:
            try:
                return self._mint_call_tool(tool_name, arguments)
            except Exception as e:
                print(f"❌ Error calling MintMCP tool {tool_name}: {e}")
                return None
        try:
            if not self.message_endpoint:
                print("⚠️  No message endpoint - not initialized")
                return None
            
            request_id = int(time.time() * 1000)
            payload = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments
                }
            }
            
            response = self.session.post(
                self.message_endpoint,
                json=payload,
                timeout=30
            )
            
            if response.status_code in [200, 202]:
                # Check if response has content
                if not response.text or response.text.strip() == "":
                    print(f"⚠️  Empty response body (status {response.status_code}) - reading from SSE")
                    return self._read_sse_response(request_id=request_id, timeout=30)
                
                result = response.json()
                
                # Extract content from response
                content_items = result.get('result', {}).get('content', [])
                text_parts = []
                
                for item in content_items:
                    if isinstance(item, dict) and item.get('type') == 'text':
                        text_parts.append(item.get('text', ''))
                    elif isinstance(item, str):
                        text_parts.append(item)
                
                return '\n'.join(text_parts) if text_parts else None
            else:
                print(f"⚠️  Tool call failed: {response.status_code} - {response.text[:200]}")
                return None
                
        except Exception as e:
            print(f"❌ Error calling tool {tool_name}: {e}")
            return None
    
    def _read_sse_response(self, request_id: int, timeout: int = 30) -> Any:
        """Wait for response from SSE background thread."""
        try:
            print(f"📡 Waiting for SSE response for request {request_id}...")
            
            # Poll for response from background thread
            import time
            start_time = time.time()
            
            while time.time() - start_time < timeout:
                if request_id in self.sse_responses:
                    event_data = self.sse_responses[request_id]
                    print(f"✅ Got SSE response for request {request_id}")
                    
                    if request_id == 2:  # tools/list
                        tools = event_data.get('result', {}).get('tools', [])
                        print(f"✅ Found {len(tools)} MCP tools via SSE")
                        return tools
                    else:  # tools/call
                        content_items = event_data.get('result', {}).get('content', [])
                        text_parts = []
                        for item in content_items:
                            if isinstance(item, dict) and item.get('type') == 'text':
                                text_parts.append(item.get('text', ''))
                            elif isinstance(item, str):
                                text_parts.append(item)
                        return '\n'.join(text_parts) if text_parts else None
                
                time.sleep(0.1)  # Poll every 100ms
            
            print(f"⏱️  Timeout waiting for SSE response (waited {timeout}s)")
            return [] if request_id == 2 else None
            
        except Exception as e:
            print(f"❌ Error reading SSE response: {e}")
            return [] if request_id == 2 else None
    
    def close(self):
        """Close the session and stop SSE reader thread."""
        print(f"🔌 Closing MCP client...")
        self.sse_running = False
        
        if self.sse_connection:
            try:
                self.sse_connection.close()
            except:
                pass
        
        if self.sse_thread and self.sse_thread.is_alive():
            self.sse_thread.join(timeout=1)
        
        self.session.close()
        print(f"✅ MCP client closed")


def extract_keywords(question: str) -> str:
    """Extract meaningful keywords from the question."""
    # Remove common question words
    stop_words = ['what', 'is', 'are', 'how', 'does', 'do', 'can', 'que', 'es', 'como', 'funciona', 'the', 'a', 'an']
    words = re.findall(r'\w+', question.lower())
    keywords = [w for w in words if w not in stop_words and len(w) > 2]
    return ' '.join(keywords)

def format_jira_as_table(result_text: str) -> str:
    """
    Format Jira results as an HTML table.
    Parses pipe-separated format (|) and converts to HTML table.
    """
    lines = result_text.strip().split('\n')
    
    # Check if it has pipe-separated format
    if any('|' in line for line in lines[:5]):
        # Parse pipe-separated table
        table_lines = [line for line in lines if '|' in line and line.strip()]
        
        if len(table_lines) < 2:
            # Not enough data for a table
            return f"<div style='white-space: pre-wrap; color: #2d3748; font-size: 13px; line-height: 1.6; font-family: monospace;'>{html.escape(result_text)}</div>"
        
        # Extract header and data rows
        header_line = table_lines[0]
        headers = [h.strip() for h in header_line.split('|') if h.strip()]
        
        # Skip separator lines (those with only dashes and pipes)
        data_lines = [line for line in table_lines[1:] if not re.match(r'^[\s|:\-]+$', line)]
        
        # Build HTML table
        table_html = """
        <div style='overflow-x: auto;'>
            <table style='width: 100%; border-collapse: collapse; font-size: 12px;'>
                <thead>
                    <tr style='background-color: #667eea; color: white;'>
        """
        
        # Add headers
        for header in headers:
            table_html += f"<th style='padding: 8px; text-align: left; border: 1px solid #ddd;'>{html.escape(header)}</th>"
        
        table_html += """
                    </tr>
                </thead>
                <tbody>
        """
        
        # Add data rows
        for line in data_lines[:50]:  # Limit to 50 rows
            cells = [c.strip() for c in line.split('|') if c.strip()]
            
            if len(cells) >= len(headers):
                table_html += "<tr style='border-bottom: 1px solid #ddd;'>"
                for i, cell in enumerate(cells[:len(headers)]):
                    # Make first column bold (usually the key) and clickeable if it's a Jira ticket
                    if i == 0:
                        # Check if it looks like a Jira key (e.g., SRE-1272, GOC-123)
                        jira_key_pattern = r'^([A-Z][A-Z0-9]+-\d+)$'
                        if re.match(jira_key_pattern, cell.strip()):
                            jira_url = f"https://arlo.atlassian.net/browse/{html.escape(cell.strip())}"
                            table_html += f"<td style='padding: 8px; border: 1px solid #ddd;'><strong><a href='{jira_url}' target='_blank' style='color: #667eea; text-decoration: none;'>{html.escape(cell)}</a></strong></td>"
                        else:
                            table_html += f"<td style='padding: 8px; border: 1px solid #ddd;'><strong>{html.escape(cell)}</strong></td>"
                    else:
                        # Truncate long cells
                        if len(cell) > 100:
                            cell = cell[:97] + "..."
                        table_html += f"<td style='padding: 8px; border: 1px solid #ddd;'>{html.escape(cell)}</td>"
                table_html += "</tr>"
        
        table_html += """
                </tbody>
            </table>
        </div>
        """
        
        if len(data_lines) > 50:
            table_html += f"""
            <p style='margin-top: 8px; color: #666; font-size: 11px;'>
                Showing 50 of {len(data_lines)} results
            </p>
            """
        
        return table_html
    
    # Try JSON parsing as fallback
    try:
        data = json.loads(result_text)
        
        if isinstance(data, list) and len(data) > 0:
            # Build HTML table from JSON
            table_html = """
            <div style='overflow-x: auto;'>
                <table style='width: 100%; border-collapse: collapse; font-size: 12px;'>
                    <thead>
                        <tr style='background-color: #667eea; color: white;'>
                            <th style='padding: 8px; text-align: left; border: 1px solid #ddd;'>Key</th>
                            <th style='padding: 8px; text-align: left; border: 1px solid #ddd;'>Summary</th>
                            <th style='padding: 8px; text-align: left; border: 1px solid #ddd;'>Status</th>
                            <th style='padding: 8px; text-align: left; border: 1px solid #ddd;'>Type</th>
                            <th style='padding: 8px; text-align: left; border: 1px solid #ddd;'>Priority</th>
                        </tr>
                    </thead>
                    <tbody>
            """
            
            for issue in data[:20]:
                key_raw = str(issue.get('key', 'N/A'))
                key = html.escape(key_raw)
                summary = html.escape(str(issue.get('summary', issue.get('fields', {}).get('summary', 'N/A'))))
                status = html.escape(str(issue.get('status', issue.get('fields', {}).get('status', {}).get('name', 'N/A'))))
                issue_type = html.escape(str(issue.get('type', issue.get('fields', {}).get('issuetype', {}).get('name', 'N/A'))))
                priority = html.escape(str(issue.get('priority', issue.get('fields', {}).get('priority', {}).get('name', 'N/A'))))
                
                if len(summary) > 80:
                    summary = summary[:77] + "..."
                
                # Create clickeable link for the key
                jira_key_pattern = r'^([A-Z][A-Z0-9]+-\d+)$'
                if re.match(jira_key_pattern, key_raw.strip()) and key_raw != 'N/A':
                    jira_url = f"https://arlo.atlassian.net/browse/{key}"
                    key_cell = f"<strong><a href='{jira_url}' target='_blank' style='color: #667eea; text-decoration: none;'>{key}</a></strong>"
                else:
                    key_cell = f"<strong>{key}</strong>"
                
                table_html += f"""
                <tr style='border-bottom: 1px solid #ddd;'>
                    <td style='padding: 8px; border: 1px solid #ddd;'>{key_cell}</td>
                    <td style='padding: 8px; border: 1px solid #ddd;'>{summary}</td>
                    <td style='padding: 8px; border: 1px solid #ddd;'>{status}</td>
                    <td style='padding: 8px; border: 1px solid #ddd;'>{issue_type}</td>
                    <td style='padding: 8px; border: 1px solid #ddd;'>{priority}</td>
                </tr>
                """
            
            table_html += """
                    </tbody>
                </table>
            </div>
            """
            return table_html
    except:
        pass
    
    # Final fallback: return as preformatted text
    return f"<div style='white-space: pre-wrap; color: #2d3748; font-size: 13px; line-height: 1.6; font-family: monospace;'>{html.escape(result_text)}</div>"

def format_datadog_metrics_as_table(result_text: str) -> str:
    """
    Format Datadog metrics results as a simple table showing only metric name and URL.
    Expected format: {'search_query': '...', 'total_found': N, 'metrics': [{...}]}
    """
    # Try JSON parsing first (this is the format from datadog_search_metrics)
    try:
        data = json.loads(result_text)
        
        # Check if it has the expected Datadog format with 'metrics' array
        if isinstance(data, dict) and 'metrics' in data:
            metrics_list = data['metrics']
            total_found = data.get('total_found', len(metrics_list))
            
            if not metrics_list or len(metrics_list) == 0:
                return """
                <div style='background-color: #fff3cd; padding: 12px; border-radius: 4px;'>
                    <p style='margin: 0; color: #856404;'>No metrics found</p>
                </div>
                """
            
            table_html = f"""
            <div style='overflow-x: auto;'>
                <p style='margin-bottom: 8px; color: #666; font-size: 12px;'>Found {total_found} metric(s)</p>
                <table style='width: 100%; border-collapse: collapse; font-size: 12px;'>
                    <thead>
                        <tr style='background-color: #667eea; color: white;'>
                            <th style='padding: 8px; text-align: left; border: 1px solid #ddd;'>Metric Name</th>
                            <th style='padding: 8px; text-align: left; border: 1px solid #ddd;'>URL</th>
                        </tr>
                    </thead>
                    <tbody>
            """
            
            for item in metrics_list[:50]:  # Limit to 50 metrics
                metric_name = item.get('metric_name', 'N/A')
                metric_url = item.get('metric_url', item.get('url', 'N/A'))
                
                # Make URL clickable if valid
                if metric_url and metric_url.startswith('http'):
                    url_cell = f"<a href='{html.escape(metric_url)}' target='_blank' style='color: #667eea; text-decoration: none;'>🔗 View in Datadog</a>"
                else:
                    url_cell = html.escape(str(metric_url))
                
                table_html += f"""
                <tr style='border-bottom: 1px solid #ddd;'>
                    <td style='padding: 8px; border: 1px solid #ddd; font-family: monospace; font-size: 11px;'><strong>{html.escape(str(metric_name))}</strong></td>
                    <td style='padding: 8px; border: 1px solid #ddd;'>{url_cell}</td>
                </tr>
                """
            
            table_html += """
                    </tbody>
                </table>
            </div>
            """
            
            if total_found > 50:
                table_html += f"""
                <p style='margin-top: 8px; color: #666; font-size: 11px;'>
                    Showing 50 of {total_found} metrics
                </p>
                """
            
            return table_html
        
        # Try generic list format
        elif isinstance(data, list) and len(data) > 0:
            table_html = """
            <div style='overflow-x: auto;'>
                <table style='width: 100%; border-collapse: collapse; font-size: 12px;'>
                    <thead>
                        <tr style='background-color: #667eea; color: white;'>
                            <th style='padding: 8px; text-align: left; border: 1px solid #ddd;'>Metric Name</th>
                            <th style='padding: 8px; text-align: left; border: 1px solid #ddd;'>URL</th>
                        </tr>
                    </thead>
                    <tbody>
            """
            
            for item in data[:50]:
                metric_name = item.get('metric_name', item.get('name', item.get('metric', 'N/A')))
                metric_url = item.get('metric_url', item.get('url', item.get('link', 'N/A')))
                
                if metric_url and metric_url.startswith('http'):
                    url_cell = f"<a href='{html.escape(metric_url)}' target='_blank' style='color: #667eea; text-decoration: none;'>🔗 View</a>"
                else:
                    url_cell = html.escape(str(metric_url))
                
                table_html += f"""
                <tr style='border-bottom: 1px solid #ddd;'>
                    <td style='padding: 8px; border: 1px solid #ddd;'><strong>{html.escape(str(metric_name))}</strong></td>
                    <td style='padding: 8px; border: 1px solid #ddd;'>{url_cell}</td>
                </tr>
                """
            
            table_html += """
                    </tbody>
                </table>
            </div>
            """
            return table_html
    except json.JSONDecodeError:
        pass
    
    # Try pipe-separated format
    lines = result_text.strip().split('\n')
    if any('|' in line for line in lines[:5]):
        table_lines = [line for line in lines if '|' in line and line.strip()]
        
        if len(table_lines) >= 2:
            header_line = table_lines[0]
            headers = [h.strip().lower() for h in header_line.split('|') if h.strip()]
            
            name_idx = -1
            url_idx = -1
            for i, header in enumerate(headers):
                if 'metric' in header or 'name' in header:
                    name_idx = i
                if 'url' in header or 'link' in header:
                    url_idx = i
            
            data_lines = [line for line in table_lines[1:] if not re.match(r'^[\s|:\-]+$', line)]
            
            table_html = """
            <div style='overflow-x: auto;'>
                <table style='width: 100%; border-collapse: collapse; font-size: 12px;'>
                    <thead>
                        <tr style='background-color: #667eea; color: white;'>
                            <th style='padding: 8px; text-align: left; border: 1px solid #ddd;'>Metric Name</th>
                            <th style='padding: 8px; text-align: left; border: 1px solid #ddd;'>URL</th>
                        </tr>
                    </thead>
                    <tbody>
            """
            
            for line in data_lines[:50]:
                cells = [c.strip() for c in line.split('|') if c.strip()]
                
                if len(cells) >= 2:
                    metric_name = cells[name_idx] if name_idx >= 0 and name_idx < len(cells) else cells[0]
                    metric_url = cells[url_idx] if url_idx >= 0 and url_idx < len(cells) else (cells[1] if len(cells) > 1 else 'N/A')
                    
                    if metric_url.startswith('http'):
                        url_cell = f"<a href='{html.escape(metric_url)}' target='_blank' style='color: #667eea; text-decoration: none;'>🔗 View</a>"
                    else:
                        url_cell = html.escape(metric_url)
                    
                    table_html += f"""
                    <tr style='border-bottom: 1px solid #ddd;'>
                        <td style='padding: 8px; border: 1px solid #ddd;'><strong>{html.escape(metric_name)}</strong></td>
                        <td style='padding: 8px; border: 1px solid #ddd;'>{url_cell}</td>
                    </tr>
                    """
            
            table_html += """
                    </tbody>
                </table>
            </div>
            """
            return table_html
    
    # Fallback: return as text
    return f"<div style='white-space: pre-wrap; color: #2d3748; font-size: 13px; line-height: 1.6; font-family: monospace;'>{html.escape(result_text)}</div>"


def format_value_smart(value: any) -> str:
    """Format a single value with smart styling."""
    if value is None or value == 'N/A':
        return "<span style='color: #999; font-style: italic;'>N/A</span>"
    
    value_str = str(value)
    
    # URLs
    if isinstance(value, str) and value.startswith('http'):
        return f"<a href='{html.escape(value)}' target='_blank' style='color: #667eea; text-decoration: none;'>🔗 Link</a>"
    
    # Booleans
    if isinstance(value, bool) or value_str.lower() in ['true', 'false']:
        color = '#10b981' if str(value).lower() == 'true' else '#f59e0b'
        emoji = '✓' if str(value).lower() == 'true' else '✗'
        return f"<span style='color: {color}; font-weight: bold;'>{emoji} {value_str}</span>"
    
    # Status badges
    if isinstance(value, str):
        value_lower = value.lower()
        if value_lower in ['active', 'done', 'completed', 'success', 'resolved']:
            return f"<span style='background: #10b981; color: white; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: bold;'>{html.escape(value)}</span>"
        elif value_lower in ['pending', 'in progress', 'in_progress', 'open']:
            return f"<span style='background: #3b82f6; color: white; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: bold;'>{html.escape(value)}</span>"
        elif value_lower in ['failed', 'error', 'closed', 'rejected']:
            return f"<span style='background: #ef4444; color: white; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: bold;'>{html.escape(value)}</span>"
        elif value_lower in ['warning', 'blocked']:
            return f"<span style='background: #f59e0b; color: white; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: bold;'>{html.escape(value)}</span>"
    
    # Truncate long values
    if len(value_str) > 100:
        value_str = value_str[:97] + "..."
    
    return html.escape(value_str)


def format_mcp_result(tool_name: str, result_text: str) -> str:
    """
    Smart formatter that detects the type of MCP result and applies appropriate formatting.
    
    Args:
        tool_name: Name of the MCP tool
        result_text: Raw result text from the tool
    
    Returns:
        Formatted HTML string
    """
    # Handle empty or error results
    if not result_text or not result_text.strip():
        return "<p style='color: #999; font-style: italic;'>No data returned</p>"
    
    if "error" in result_text.lower()[:100] or "failed" in result_text.lower()[:100]:
        return f"<div style='background-color: #fee; padding: 8px; border-radius: 4px; color: #c53030;'>{html.escape(result_text[:500])}</div>"
    
    # Jira tools - use Jira table formatter
    if 'jira' in tool_name.lower() or 'zephyr' in tool_name.lower():
        return format_jira_as_table(result_text)
    
    # Datadog tools - use Datadog formatter
    if 'datadog' in tool_name.lower() or 'dd_' in tool_name.lower():
        return format_datadog_metrics_as_table(result_text)
    
    # Try to detect JSON and format it nicely
    try:
        data = json.loads(result_text)
        
        # If it's a list of strings or simple values
        if isinstance(data, list) and len(data) > 0 and not isinstance(data[0], (dict, list)):
            html_output = "<ul style='margin: 0; padding-left: 20px;'>"
            for item in data[:50]:
                html_output += f"<li style='margin: 4px 0;'>{format_value_smart(item)}</li>"
            html_output += "</ul>"
            if len(data) > 50:
                html_output += f"<p style='margin-top: 8px; color: #666; font-size: 11px;'>Showing 50 of {len(data)} items</p>"
            return html_output
        
        # If it's a list of objects, try to create a table
        elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
            # Get all unique keys from first few items
            keys = set()
            for item in data[:5]:
                if isinstance(item, dict):
                    keys.update(item.keys())
            
            # Prioritize common important keys
            priority_keys = ['name', 'key', 'id', 'title', 'summary', 'status', 'type', 'priority', 'url', 'link']
            keys_list = []
            for pk in priority_keys:
                if pk in keys:
                    keys_list.append(pk)
                    keys.discard(pk)
            keys_list.extend(sorted(list(keys)))
            keys_list = keys_list[:6]  # Limit to 6 columns for readability
            
            if len(keys_list) > 0:
                table_html = """
                <div style='overflow-x: auto;'>
                    <table style='width: 100%; border-collapse: collapse; font-size: 12px;'>
                        <thead>
                            <tr style='background-color: #667eea; color: white;'>
                """
                
                for key in keys_list:
                    table_html += f"<th style='padding: 8px; text-align: left; border: 1px solid #ddd;'>{html.escape(key.replace('_', ' ').title())}</th>"
                
                table_html += """
                            </tr>
                        </thead>
                        <tbody>
                """
                
                for item in data[:30]:  # Limit to 30 rows
                    if isinstance(item, dict):
                        table_html += "<tr style='border-bottom: 1px solid #ddd;'>"
                        for key in keys_list:
                            value = item.get(key, 'N/A')
                            table_html += f"<td style='padding: 8px; border: 1px solid #ddd;'>{format_value_smart(value)}</td>"
                        table_html += "</tr>"
                
                table_html += """
                        </tbody>
                    </table>
                </div>
                """
                
                if len(data) > 30:
                    table_html += f"<p style='margin-top: 8px; color: #666; font-size: 11px;'>Showing 30 of {len(data)} results</p>"
                
                return table_html
        
        # If it's a single object, format as styled card
        elif isinstance(data, dict):
            html_output = "<div style='display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 12px;'>"
            
            items = list(data.items())[:20]  # Limit to 20 fields
            for key, value in items:
                key_formatted = html.escape(key.replace('_', ' ').title())
                
                # Handle nested objects/arrays
                if isinstance(value, (dict, list)):
                    value_formatted = f"<pre style='margin: 4px 0 0 0; font-size: 11px; background: #f7fafc; padding: 6px; border-radius: 3px; max-height: 100px; overflow-y: auto;'>{html.escape(json.dumps(value, indent=2)[:300])}</pre>"
                else:
                    value_formatted = format_value_smart(value)
                
                html_output += f"""
                <div style='background: #f7fafc; padding: 10px; border-radius: 6px; border-left: 3px solid #667eea;'>
                    <div style='font-size: 11px; color: #718096; margin-bottom: 4px; font-weight: 600;'>{key_formatted}</div>
                    <div style='font-size: 13px;'>{value_formatted}</div>
                </div>
                """
            
            html_output += "</div>"
            
            if len(data) > 20:
                html_output += f"<p style='margin-top: 8px; color: #666; font-size: 11px;'>Showing 20 of {len(data)} fields</p>"
            
            return html_output
        
        # For simple JSON values or arrays, pretty print in a compact way
        else:
            if isinstance(data, list) and len(data) > 100:
                data = data[:100]  # Limit array size
            
            formatted_json = json.dumps(data, indent=2, ensure_ascii=False)
            if len(formatted_json) > 3000:
                formatted_json = formatted_json[:3000] + "\n... (truncated)"
            return f"<pre style='white-space: pre-wrap; font-family: monospace; font-size: 11px; background: #f7fafc; padding: 10px; border-radius: 4px; overflow-x: auto; max-height: 400px; overflow-y: auto;'>{html.escape(formatted_json)}</pre>"
    
    except (json.JSONDecodeError, ValueError):
        pass
    
    # Check for pipe-separated table format
    if '|' in result_text and '\n' in result_text:
        lines = result_text.strip().split('\n')
        table_lines = [line for line in lines if '|' in line and line.strip()]
        
        if len(table_lines) >= 2:
            # Has potential table format
            header_line = table_lines[0]
            headers = [h.strip() for h in header_line.split('|') if h.strip()]
            
            if len(headers) >= 2:
                data_lines = [line for line in table_lines[1:] if not re.match(r'^[\s|:\-]+$', line)]
                
                table_html = """
                <div style='overflow-x: auto;'>
                    <table style='width: 100%; border-collapse: collapse; font-size: 12px;'>
                        <thead>
                            <tr style='background-color: #667eea; color: white;'>
                """
                
                for header in headers:
                    table_html += f"<th style='padding: 8px; text-align: left; border: 1px solid #ddd;'>{html.escape(header)}</th>"
                
                table_html += """
                            </tr>
                        </thead>
                        <tbody>
                """
                
                for line in data_lines[:50]:
                    cells = [c.strip() for c in line.split('|') if c.strip()]
                    if len(cells) >= len(headers):
                        table_html += "<tr style='border-bottom: 1px solid #ddd;'>"
                        for i, cell in enumerate(cells[:len(headers)]):
                            style = 'font-weight: bold;' if i == 0 else ''
                            table_html += f"<td style='padding: 8px; border: 1px solid #ddd; {style}'>{format_value_smart(cell)}</td>"
                        table_html += "</tr>"
                
                table_html += """
                        </tbody>
                    </table>
                </div>
                """
                
                if len(data_lines) > 50:
                    table_html += f"<p style='margin-top: 8px; color: #666; font-size: 11px;'>Showing 50 of {len(data_lines)} results</p>"
                
                return table_html
    
    # Check for bullet points or numbered lists
    lines = result_text.split('\n')
    if len(lines) > 2:
        list_lines = [l for l in lines if l.strip().startswith(('- ', '* ', '• ')) or (len(l) > 2 and l.strip()[0].isdigit() and l.strip()[1] in ('.', ')'))]
        if len(list_lines) > len(lines) * 0.5:  # More than 50% are list items
            html_output = "<ul style='margin: 0; padding-left: 20px; line-height: 1.8;'>"
            for line in lines[:100]:
                stripped = line.strip()
                if stripped:
                    # Remove bullet markers
                    if stripped.startswith(('- ', '* ', '• ')):
                        stripped = stripped[2:]
                    elif len(stripped) > 2 and stripped[0].isdigit() and stripped[1] in ('.', ')'):
                        stripped = stripped[stripped.find(' ')+1:] if ' ' in stripped else stripped
                    
                    # Convert URLs in text
                    if 'http' in stripped:
                        import re as regex
                        stripped = regex.sub(r'(https?://[^\s<>"{}|\\^`\[\]]+)', r'<a href="\1" target="_blank" style="color: #667eea;">🔗 Link</a>', stripped)
                    
                    html_output += f"<li style='margin: 4px 0;'>{html.escape(stripped) if 'http' not in stripped else stripped}</li>"
            html_output += "</ul>"
            if len(lines) > 100:
                html_output += f"<p style='margin-top: 8px; color: #666; font-size: 11px;'>Showing 100 of {len(lines)} lines</p>"
            return html_output
    
    # Check for markdown-style content
    if any(marker in result_text for marker in ['##', '**', '- ', '* ', '`', '[', '](']):
        try:
            return markdown_to_html(result_text)
        except:
            pass
    
    # Final fallback: format as readable text with line breaks
    if len(result_text) > 3000:
        result_text = result_text[:3000] + "\n... (truncated)"
    
    # Convert URLs in plain text
    if 'http' in result_text:
        import re as regex
        result_text = regex.sub(r'(https?://[^\s<>"{}|\\^`\[\]]+)', r'<a href="\1" target="_blank" style="color: #667eea; text-decoration: underline;">🔗 \1</a>', html.escape(result_text))
        result_html = result_text.replace('\n', '<br>')
        return f"<div style='font-size: 12px; line-height: 1.6; background: #f7fafc; padding: 12px; border-radius: 4px;'>{result_html}</div>"
    
    return f"<pre style='white-space: pre-wrap; font-family: monospace; font-size: 12px; background: #f7fafc; padding: 12px; border-radius: 4px; overflow-x: auto; line-height: 1.6;'>{html.escape(result_text)}</pre>"


async def ask_arlo_async(question: str = "") -> str:
    """
    Ask GocBedrock via MCP SDK (async version) - executes MCP tools and returns raw results.
    
    Requires MCP SDK (Python 3.10+).
    
    Args:
        question: The user's question/prompt (full text)
    Returns:
        HTML formatted tool results
    """
    print("=" * 80)
    print("🤖 GocBedrock MCP - Direct Mode (Async/SDK)")
    print(f"📝 Question: '{question}'")
    print(f"🌐 MCP Server: {mcp_transport_label()}")
    
    if not question or not question.strip():
        return """
        <div style='background-color: #fff3cd; padding: 12px; border-left: 4px solid #ffc107; border-radius: 4px; margin: 8px 0;'>
            <p style='margin: 0; color: #856404;'>
                ⚠️ <strong>No question provided.</strong><br>
                Please enter a question to ask GocBedrock.
            </p>
        </div>
        """
    
    try:
        print("🔗 Connecting to MCP server...")
        async with open_mcp_session() as session:
                print("📋 Fetching available tools from MCP...")
                mcp_tools_response = await session.list_tools()
                mcp_tools = mcp_tools_response.tools
                
                print(f"✅ Got {len(mcp_tools)} tools from MCP")
                
                # Build tools map and extract keywords from question
                tools_map = {}
                for tool in mcp_tools:
                    tools_map[tool.name] = tool
                
                # Extract keywords from question for intelligent filtering
                question_lower = question.lower()
                keywords = question_lower.split()
                print(f"🔍 Keywords from question: {keywords}")
                
                # Smart filtering: detect if user mentions specific tool categories
                filter_keywords = {
                    'jira': ['jira', 'ticket', 'tickets', 'issue', 'issues', 'epic', 'story', 'bug', 'incidencia'],
                    'confluence': ['confluence', 'wiki', 'document', 'page'],
                    'datadog': ['datadog', 'metric', 'monitor', 'dashboard', 'apm', 'downtime', 'maintenance window', 'maintenance windows'],
                    'deployment': ['deployment', 'deploy', 'despliegue', 'release', 'grm', 'calendario', 'calendar', 'scheduled'],
                    'pagerduty': ['pagerduty', 'incident', 'alert', 'oncall'],
                    'arlo_status': ['status.arlo', 'arlo status', 'status page', 'public status'],
                    'noc_kt': ['noc kt', 'knowledge transfer', 'kt table', 'noc knowledge'],
                    'status_monitor': ['status monitor', 'status wall', 'hub summary', 'environment health'],
                    'shift_report': ['shift report', 'shift handoff', 'handoff report', 'turnover'],
                    'splunk': ['splunk', 'log', 'search'],
                    'aws': ['aws', 'cost', 'billing', 'account'],
                    'appbot': ['appbot', 'review', 'feedback', 'rating'],
                    'zephyr': ['zephyr', 'test', 'execution']
                }
                
                # Determine which category to filter by
                detected_categories = set()
                for category, category_keywords in filter_keywords.items():
                    if any(kw in question_lower for kw in category_keywords):
                        detected_categories.add(category)
                
                # Auto-detect service health queries → Datadog + ownership tools
                from tools.mcp_tool_suggest import is_service_health_question
                from tools.service_query import extract_service_name_from_query

                if is_service_health_question(question):
                    detected_categories.add('datadog')
                    detected_categories.add('services')
                    svc = extract_service_name_from_query(question)
                    print(f"📊 Auto-detected service health query for '{svc}' -> Datadog MCP tools")

                # Auto-detect informational questions -> use Confluence (documentation/wiki)
                informational_keywords = ['what', 'que', 'qué', 'how', 'como', 'cómo', 'where', 
                                         'donde', 'dónde', 'why', 'porque', 'por qué', 'when', 
                                         'cuando', 'cuándo', 'explain', 'explica', 'define', 
                                         'define', 'tell', 'dime', 'information', 'información',
                                         'about', 'acerca', 'is', 'es', 'are', 'son']
                
                # Check if question starts with or contains informational keywords
                question_words = question_lower.split()
                if question_words and any(question_words[0] == kw for kw in informational_keywords):
                    # Question starts with informational keyword
                    detected_categories.add('confluence')
                    print(f"📚 Auto-detected informational question -> adding Confluence (wiki/docs)")
                    logging.info(f"📚 Auto-detected informational question -> adding Confluence (wiki/docs)")
                elif any(kw in question_lower for kw in ['what is', 'qué es', 'que es', 'how to', 
                                                          'como hacer', 'cómo hacer', 'tell me about',
                                                          'dime acerca', 'explain', 'explica']):
                    # Question contains informational phrase
                    detected_categories.add('confluence')
                    print(f"📚 Auto-detected informational phrase -> adding Confluence (wiki/docs)")
                    logging.info(f"📚 Auto-detected informational phrase -> adding Confluence (wiki/docs)")
                
                # Auto-detect Jira tickets by pattern (SRE-, SV-, GOC-, etc.)
                jira_ticket_pattern = r'\b([A-Z][A-Z0-9]+-\d+)\b'
                if re.search(jira_ticket_pattern, question.upper()):
                    detected_categories.add('jira')
                    print(f"🎫 Auto-detected Jira ticket pattern in query")
                
                # Detect status queries (jira open, jira closed, tickets open, etc.)
                jira_search_status = None
                jira_project_filter = None
                
                # Status map
                status_map = {
                    'open': ['open', 'abierto', 'abiertos', 'new', 'nuevo'],
                    'in progress': ['in progress', 'en progreso', 'progress', 'progreso', 'working'],
                    'closed': ['closed', 'cerrado', 'cerrados', 'done', 'completed', 'terminado'],
                    'resolved': ['resolved', 'resuelto', 'resueltos']
                }
                
                # Check if question contains jira/ticket keywords with status
                has_jira_keyword = any(kw in question_lower for kw in ['jira', 'ticket', 'tickets', 'issue', 'issues'])
                
                # Detect status if jira keyword present
                if has_jira_keyword:
                    for status, keywords in status_map.items():
                        if any(kw in question_lower for kw in keywords):
                            jira_search_status = status
                            detected_categories.add('jira')
                            print(f"🔍 Detected Jira status query - filtering to Jira tools")
                            print(f"📋 Detected status filter: {status}")
                            break
                
                # If we detected a jira status search, look for project filter
                if jira_search_status:
                    # Detect project filter (for SRE, de GOC, in SV, etc.)
                    project_pattern = r'\b(?:FOR|DE|IN|PROJECT|DEL)\s+([A-Z][A-Z0-9]{0,10})\b'
                    project_match = re.search(project_pattern, question.upper())
                    if project_match:
                        jira_project_filter = project_match.group(1)
                        print(f"🎯 Detected project filter: {jira_project_filter}")
                    else:
                        # Try to detect project without preposition (e.g., "jira open sre")
                        # Look for standalone project codes (SRE, GOC, SV, etc.)
                        standalone_project_pattern = r'\b([A-Z]{2,10})\b'
                        for match in re.finditer(standalone_project_pattern, question.upper()):
                            potential_project = match.group(1)
                            # Skip common words that aren't projects
                            if potential_project not in ['JIRA', 'TICKET', 'TICKETS', 'OPEN', 'CLOSED', 'NEW', 'ALL', 'FOR', 'THE', 'AND', 'OR']:
                                jira_project_filter = potential_project
                                print(f"🎯 Detected project filter (standalone): {jira_project_filter}")
                                break
                
                # Filter tools based on detected categories
                if detected_categories:
                    print(f"🎯 Detected categories: {detected_categories}")
                    filtered_tools = []
                    for tool in mcp_tools:
                        tool_name_lower = tool.name.lower()
                        tool_desc_lower = (tool.description if hasattr(tool, 'description') else '').lower()
                        
                        # Check if tool matches any detected category
                        for category in detected_categories:
                            name_match = category in tool_name_lower or category in tool_desc_lower
                            if category == 'jira' and not name_match:
                                name_match = any(
                                    x in tool_name_lower
                                    for x in ('atlassian', 'jql', 'jiraissue', 'jira')
                                )
                            if category == 'confluence' and not name_match:
                                name_match = 'confluence' in tool_name_lower
                            if category == 'arlo_status' and not name_match:
                                name_match = 'arlo_public_status' in tool_name_lower
                            if category == 'noc_kt' and not name_match:
                                name_match = 'noc_kt' in tool_name_lower
                            if category == 'status_monitor' and not name_match:
                                name_match = 'status_monitor' in tool_name_lower
                            if category == 'shift_report' and not name_match:
                                name_match = 'shift_report' in tool_name_lower
                            if category == 'deployment' and not name_match:
                                name_match = 'grm_deployments' in tool_name_lower
                            if category == 'services' and not name_match:
                                name_match = any(
                                    x in tool_name_lower
                                    for x in ('service_owners', 'arlo_versions', 'deployed_fw')
                                )
                            if name_match:
                                filtered_tools.append(tool)
                                break
                    
                    tools_to_execute = filtered_tools
                    print(f"🔧 Filtered to {len(tools_to_execute)} relevant tools (from {len(mcp_tools)} total)")
                else:
                    # No specific category detected - use smart keyword matching
                    print(f"⚠️  No specific category detected, using smart keyword matching...")
                    filtered_tools = []
                    
                    # Extract important keywords from question (ignore common words)
                    stop_words = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 
                                  'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'should',
                                  'can', 'could', 'may', 'might', 'must', 'shall', 'me', 'my', 'show',
                                  'tell', 'get', 'find', 'what', 'when', 'where', 'why', 'how', 'please',
                                  'thanks', 'thank', 'you', 'i', 'we', 'all', 'some', 'any'}
                    
                    important_keywords = [word for word in keywords if word not in stop_words and len(word) > 2]
                    print(f"🔍 Important keywords for matching: {important_keywords}")
                    
                    # Match tools by keywords in tool name or description
                    for tool in mcp_tools:
                        tool_name_lower = tool.name.lower()
                        tool_desc_lower = (tool.description if hasattr(tool, 'description') else '').lower()
                        
                        # Check if any important keyword matches tool name or description
                        for keyword in important_keywords:
                            if keyword in tool_name_lower or keyword in tool_desc_lower:
                                filtered_tools.append(tool)
                                print(f"   ✓ Matched tool '{tool.name}' with keyword '{keyword}'")
                                break
                    
                    if filtered_tools:
                        tools_to_execute = filtered_tools
                        print(f"🔧 Smart match: filtered to {len(tools_to_execute)} relevant tools (from {len(mcp_tools)} total)")
                    else:
                        # If no matches, return a helpful message instead of executing everything
                        print(f"⚠️  No relevant tools found for query")
                        return f"""
                        <div style='background-color: #fff3cd; padding: 12px; border-left: 4px solid #ffc107; border-radius: 4px; margin: 8px 0;'>
                            <p style='margin: 0; color: #856404;'>
                                ⚠️ <strong>No relevant tools found for your query.</strong><br><br>
                                <strong>Your question:</strong> {html.escape(question)}<br><br>
                                <strong>Suggestion:</strong> Try to be more specific. Mention one of these topics:<br>
                                • <strong>Jira</strong> - for tickets, issues, epics<br>
                                • <strong>Confluence</strong> - for wiki pages, documents<br>
                                • <strong>Datadog</strong> - for metrics, monitors, dashboards<br>
                                • <strong>PagerDuty</strong> - for incidents, alerts<br>
                                • <strong>Splunk</strong> - for logs, searches<br>
                                • <strong>AWS</strong> - for costs, billing<br>
                                • <strong>AppBot</strong> - for app reviews<br>
                            </p>
                        </div>
                        """
                
                # Detect specific Jira ticket IDs (e.g., SRE-1272, PROJ-123)
                jira_ticket_pattern = r'\b([A-Z][A-Z0-9]+-\d+)\b'
                jira_tickets = re.findall(jira_ticket_pattern, question.upper())
                if jira_tickets:
                    print(f"🎫 Detected Jira ticket IDs: {jira_tickets}")
                
                # Execute filtered tools and collect results
                tool_results = []
                
                for tool in tools_to_execute:
                    tool_name = tool.name
                    print(f"\n🎯 Calling: {tool_name}")
                    
                    try:
                        # Call tool with question as parameter if it accepts it
                        tool_params = {}
                        if hasattr(tool, 'inputSchema') and tool.inputSchema:
                            schema = tool.inputSchema
                            # Try to pass question/query/jql/cql based on schema
                            if isinstance(schema, dict) and 'properties' in schema:
                                props = schema['properties']
                                
                                # Special handling for "show me all" with status filter
                                if jira_search_status and (
                                    'jira' in tool_name.lower()
                                    or 'jql' in tool_name.lower()
                                    or 'atlassian' in tool_name.lower()
                                ):
                                    if 'jql' in props:
                                        # Build JQL for status search
                                        status_jql_map = {
                                            'open': 'status in ("Open", "New", "To Do")',
                                            'in progress': 'status in ("In Progress", "In Development")',
                                            'closed': 'status in ("Closed", "Done", "Resolved")',
                                            'resolved': 'status in ("Resolved", "Done")'
                                        }
                                        jql_query = status_jql_map.get(jira_search_status, 'status = "Open"')
                                        
                                        # Add project filter if detected
                                        if jira_project_filter:
                                            jql_query = f'project = "{jira_project_filter}" AND {jql_query}'
                                            print(f"   🎯 Adding project filter: {jira_project_filter}")
                                        
                                        tool_params['jql'] = f'{jql_query} ORDER BY updated DESC'
                                        print(f"   📋 Using JQL: {tool_params['jql']}")
                                    elif 'query' in props:
                                        query_parts = [f'status:{jira_search_status}']
                                        if jira_project_filter:
                                            query_parts.append(f'project:{jira_project_filter}')
                                        tool_params['query'] = ' '.join(query_parts)
                                        print(f"   📋 Using query: {tool_params['query']}")
                                # Special handling for Jira tools with specific ticket IDs
                                elif jira_tickets and (
                                    'jira' in tool_name.lower() or 'atlassian' in tool_name.lower()
                                ):
                                    if 'getjiraissue' in tool_name.lower().replace('_', '').replace('-', ''):
                                        tool_params['issueIdOrKey'] = jira_tickets[0]
                                        cloud_id = getattr(session, '_arlo_atlassian_cloud_id', None)
                                        if cloud_id:
                                            tool_params['cloudId'] = cloud_id
                                        print(f"   📋 Using issue key: {jira_tickets[0]}")
                                    elif 'issue_key' in props or 'key' in props:
                                        tool_params['issue_key' if 'issue_key' in props else 'key'] = jira_tickets[0]
                                        print(f"   📋 Using ticket ID: {jira_tickets[0]}")
                                    elif 'jql' in props:
                                        # Build JQL for specific tickets
                                        jql_keys = ' OR '.join([f'key = {ticket}' for ticket in jira_tickets])
                                        tool_params['jql'] = jql_keys
                                        print(f"   📋 Using JQL: {jql_keys}")
                                    elif 'query' in props:
                                        tool_params['query'] = jira_tickets[0]
                                        print(f"   📋 Using query: {jira_tickets[0]}")
                                    elif 'question' in props:
                                        tool_params['question'] = question
                                # Standard parameter handling
                                elif 'question' in props:
                                    tool_params['question'] = question
                                elif 'query' in props:
                                    # For Confluence searches with informational questions, extract the search term
                                    if 'confluence' in tool_name.lower() and any(question_lower.startswith(kw) for kw in ['what', 'que', 'qué', 'how', 'como', 'cómo', 'where', 'donde', 'why', 'cuando']):
                                        # Extract search term after informational keywords
                                        # "what is hmspayment" -> "hmspayment"
                                        # "how to deploy" -> "deploy"
                                        search_patterns = [
                                            r'^what\s+is\s+(.+)', r'^que\s+es\s+(.+)', r'^qué\s+es\s+(.+)',
                                            r'^how\s+to\s+(.+)', r'^como\s+hacer\s+(.+)', r'^cómo\s+hacer\s+(.+)',
                                            r'^where\s+is\s+(.+)', r'^donde\s+esta\s+(.+)', r'^dónde\s+está\s+(.+)',
                                            r'^why\s+(.+)', r'^porque\s+(.+)', r'^por\s+qué\s+(.+)',
                                            r'^when\s+(.+)', r'^cuando\s+(.+)', r'^cuándo\s+(.+)',
                                            r'^explain\s+(.+)', r'^explica\s+(.+)',
                                            r'^tell\s+me\s+about\s+(.+)', r'^dime\s+acerca\s+de\s+(.+)'
                                        ]
                                        extracted_term = None
                                        for pattern in search_patterns:
                                            match = re.search(pattern, question_lower)
                                            if match:
                                                extracted_term = match.group(1).strip()
                                                break
                                        
                                        if extracted_term:
                                            tool_params['query'] = extracted_term
                                            print(f"   📝 Extracted search term: '{extracted_term}' from '{question}'")
                                            logging.info(f"   📝 Extracted search term: '{extracted_term}' from '{question}'")
                                        else:
                                            tool_params['query'] = question
                                    else:
                                        tool_params['query'] = question
                                elif 'jql' in props:
                                    tool_params['jql'] = f'text ~ "{question}"'
                                elif 'cql' in props:
                                    # For Confluence CQL with informational questions, extract the search term
                                    if any(question_lower.startswith(kw) for kw in ['what', 'que', 'qué', 'how', 'como', 'cómo', 'where', 'donde', 'why', 'cuando']):
                                        search_patterns = [
                                            r'^what\s+is\s+(.+)', r'^que\s+es\s+(.+)', r'^qué\s+es\s+(.+)',
                                            r'^how\s+to\s+(.+)', r'^como\s+hacer\s+(.+)', r'^cómo\s+hacer\s+(.+)',
                                            r'^where\s+is\s+(.+)', r'^donde\s+esta\s+(.+)', r'^dónde\s+está\s+(.+)',
                                            r'^why\s+(.+)', r'^porque\s+(.+)', r'^por\s+qué\s+(.+)',
                                            r'^when\s+(.+)', r'^cuando\s+(.+)', r'^cuándo\s+(.+)',
                                            r'^explain\s+(.+)', r'^explica\s+(.+)',
                                            r'^tell\s+me\s+about\s+(.+)', r'^dime\s+acerca\s+de\s+(.+)'
                                        ]
                                        extracted_term = None
                                        for pattern in search_patterns:
                                            match = re.search(pattern, question_lower)
                                            if match:
                                                extracted_term = match.group(1).strip()
                                                break
                                        
                                        if extracted_term:
                                            tool_params['cql'] = f'text ~ "{extracted_term}"'
                                            print(f"   📝 Extracted search term for CQL: '{extracted_term}' from '{question}'")
                                        else:
                                            tool_params['cql'] = f'text ~ "{question}"'
                                    else:
                                        tool_params['cql'] = f'text ~ "{question}"'
                        
                        result = await session.call_tool(tool_name, tool_params)
                        
                        # Extract result text
                        result_text = ""
                        if hasattr(result, 'content') and result.content:
                            for item in result.content:
                                if hasattr(item, 'text'):
                                    result_text += item.text + "\n"
                                else:
                                    result_text += str(item) + "\n"
                        else:
                            result_text = str(result)
                        
                        # Check if result is valid and doesn't contain error messages
                        if result_text.strip():
                            # For Confluence tools, be less strict with error filtering
                            # (documentation often contains phrases like "not found" in normal content)
                            if 'confluence' in tool_name.lower():
                                # Only filter if it's clearly an error message at the start
                                result_start = result_text.lower()[:100].strip()
                                is_error = any(result_start.startswith(err) for err in [
                                    'error executing tool',
                                    'error:',
                                    'exception:',
                                    'failed to connect',
                                    'connection refused',
                                    'permission denied'
                                ])
                                
                                if is_error:
                                    print(f"   ⚠️  Skipping - starts with error message")
                                    logging.warning(f"   ⚠️  Skipping {tool_name} - starts with error message: {result_start}")
                                else:
                                    print(f"   ✅ Success! Got {len(result_text)} characters (Confluence)")
                                    logging.info(f"   ✅ Success! Got {len(result_text)} characters (Confluence) - ADDING TO RESULTS")
                                    tool_results.append({
                                        "tool": tool_name,
                                        "result": result_text,
                                        "description": tool.description if hasattr(tool, 'description') else ""
                                    })
                            else:
                                # For other tools, use normal error filtering
                                result_lower = result_text.lower()[:200]  # Check first 200 chars
                                if any(error_keyword in result_lower for error_keyword in [
                                    'error executing tool',
                                    'error:',
                                    'exception:',
                                    'failed to',
                                    'could not',
                                    'unable to',
                                    'permission denied',
                                    'not found',
                                    'connection refused',
                                    'timeout'
                                ]):
                                    print(f"   ⚠️  Skipping - contains error message")
                                else:
                                    print(f"   ✅ Success! Got {len(result_text)} characters")
                                    tool_results.append({
                                        "tool": tool_name,
                                        "result": result_text,
                                        "description": tool.description if hasattr(tool, 'description') else ""
                                    })
                        else:
                            print(f"   ⚠️  Empty result")
                        
                    except Exception as e:
                        error_msg = str(e)
                        print(f"   ❌ Error: {error_msg[:100]}")
                        # Don't add errors to results, just skip them
                
                print(f"\n✅ Completed! Got results from {len(tool_results)} tool(s)")
                logging.info(f"✅ Completed! Got results from {len(tool_results)} tool(s) - tool_results array length: {len(tool_results)}")
                
                # Extract main ticket info and linked work items from jira_read_issue results
                main_ticket_info = None
                linked_items = []
                if jira_tickets:
                    print(f"\n🔍 Extracting ticket info for: {jira_tickets[0]}")
                    for tr in tool_results:
                        if tr['tool'] == 'jira_read_issue':
                            print(f"✅ Found jira_read_issue result")
                            try:
                                result_text = tr['result']
                                print(f"📊 Result length: {len(result_text)} chars")
                                print(f"📊 First 200 chars: {result_text[:200]}")
                                
                                # Try to parse as JSON first
                                try:
                                    issue_data = json.loads(result_text)
                                    print(f"✅ Successfully parsed as JSON")
                                except json.JSONDecodeError as je:
                                    print(f"⚠️  JSON decode error: {str(je)[:100]}")
                                    # Try using ast.literal_eval for Python dict strings (with single quotes)
                                    try:
                                        issue_data = ast.literal_eval(result_text)
                                        print(f"✅ Successfully parsed as Python dict using ast.literal_eval")
                                    except (ValueError, SyntaxError) as ae:
                                        print(f"⚠️  ast.literal_eval error: {str(ae)[:100]}")
                                        # If not valid Python dict, try to extract dict from text
                                        # Look for dict-like content
                                        if '{' in result_text and '}' in result_text:
                                            start = result_text.find('{')
                                            end = result_text.rfind('}') + 1
                                            dict_str = result_text[start:end]
                                            try:
                                                issue_data = ast.literal_eval(dict_str)
                                                print(f"✅ Successfully extracted and parsed dict from text")
                                            except:
                                                issue_data = None
                                                print(f"❌ Could not parse extracted dict")
                                        else:
                                            issue_data = None
                                            print(f"❌ Could not find dict in text")
                                
                                if issue_data and isinstance(issue_data, dict):
                                    print(f"📋 Issue data keys: {list(issue_data.keys())}")
                                    
                                    # Extract main ticket information
                                    fields = issue_data.get('fields', {})
                                    print(f"📋 Fields keys: {list(fields.keys())[:20]}")  # Show first 20 keys
                                    
                                    # Get main ticket details
                                    main_ticket_info = {
                                        'key': issue_data.get('key', jira_tickets[0]),
                                        'summary': fields.get('summary', 'No summary'),
                                        'status': fields.get('status', {}).get('name', 'Unknown'),
                                        'priority': fields.get('priority', {}).get('name', 'Unknown'),
                                        'description': fields.get('description', 'No description'),
                                        'created': fields.get('created', 'Unknown'),
                                        'updated': fields.get('updated', 'Unknown'),
                                    }
                                    
                                    # Get assignee
                                    main_assignee = fields.get('assignee', {})
                                    if main_assignee and isinstance(main_assignee, dict):
                                        main_ticket_info['assignee'] = main_assignee.get('displayName', main_assignee.get('name', 'Unassigned'))
                                    else:
                                        main_ticket_info['assignee'] = 'Unassigned'
                                    
                                    # Get reporter
                                    main_reporter = fields.get('reporter', {})
                                    if main_reporter and isinstance(main_reporter, dict):
                                        main_ticket_info['reporter'] = main_reporter.get('displayName', main_reporter.get('name', 'Unknown'))
                                    else:
                                        main_ticket_info['reporter'] = 'Unknown'
                                    
                                    print(f"📋 Main ticket: {main_ticket_info['key']} - {main_ticket_info['summary'][:50]}...")
                                    
                                    # Look for issuelinks in fields
                                    issue_links = fields.get('issuelinks', [])
                                    print(f"🔗 Found {len(issue_links)} issue links")
                                    
                                    for idx, link in enumerate(issue_links):
                                        print(f"  Processing link {idx + 1}/{len(issue_links)}")
                                        # Jira links can be inward or outward
                                        linked_issue = link.get('outwardIssue') or link.get('inwardIssue')
                                        if linked_issue:
                                            link_type = link.get('type', {}).get('name', 'Related')
                                            linked_key = linked_issue.get('key', '')
                                            print(f"    Found linked key: {linked_key}, type: {link_type}")
                                            linked_fields = linked_issue.get('fields', {})
                                            linked_summary = linked_fields.get('summary', 'No summary')
                                            linked_status = linked_fields.get('status', {}).get('name', 'Unknown')
                                            
                                            # Extract assignee
                                            assignee = linked_fields.get('assignee', {})
                                            if assignee and isinstance(assignee, dict):
                                                assignee_name = assignee.get('displayName', assignee.get('name', 'Unassigned'))
                                            else:
                                                assignee_name = 'Unassigned'
                                            
                                            print(f"    Summary: {linked_summary[:50]}..., Status: {linked_status}, Assignee: {assignee_name}")
                                            
                                            if linked_key:
                                                linked_items.append({
                                                    'key': linked_key,
                                                    'summary': linked_summary,
                                                    'status': linked_status,
                                                    'assignee': assignee_name,
                                                    'link_type': link_type,
                                                    'url': f"https://arlo.atlassian.net/browse/{linked_key}"
                                                })
                                        else:
                                            print(f"    ⚠️  Link {idx + 1} has no outwardIssue or inwardIssue")
                                    
                                    print(f"🔗 Total linked work items collected: {len(linked_items)}")
                                
                                # Also look for subtasks
                                if issue_data and isinstance(issue_data, dict):
                                    fields = issue_data.get('fields', {})
                                    subtasks = fields.get('subtasks', [])
                                    print(f"📋 Found {len(subtasks)} subtask(s)")
                                    
                                    for idx, subtask in enumerate(subtasks):
                                        print(f"  Processing subtask {idx + 1}/{len(subtasks)}")
                                        subtask_key = subtask.get('key', '')
                                        print(f"    Subtask key: {subtask_key}")
                                        subtask_fields = subtask.get('fields', {})
                                        subtask_summary = subtask_fields.get('summary', 'No summary')
                                        subtask_status = subtask_fields.get('status', {}).get('name', 'Unknown')
                                        
                                        # Extract assignee for subtask
                                        subtask_assignee = subtask_fields.get('assignee', {})
                                        if subtask_assignee and isinstance(subtask_assignee, dict):
                                            subtask_assignee_name = subtask_assignee.get('displayName', subtask_assignee.get('name', 'Unassigned'))
                                        else:
                                            subtask_assignee_name = 'Unassigned'
                                        
                                        print(f"    Summary: {subtask_summary[:50]}..., Status: {subtask_status}, Assignee: {subtask_assignee_name}")
                                        
                                        if subtask_key:
                                            linked_items.append({
                                                'key': subtask_key,
                                                'summary': subtask_summary,
                                                'status': subtask_status,
                                                'assignee': subtask_assignee_name,
                                                'link_type': 'Subtask',
                                                'url': f"https://arlo.atlassian.net/browse/{subtask_key}"
                                            })
                                        
                            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
                                print(f"❌ Error extracting linked items: {str(e)[:100]}")
                                
                                # Fallback: Use regex to find ticket IDs in the text
                                result_text = tr['result']
                                # Look for patterns like "SRE-123" in the text
                                linked_ticket_pattern = r'\b([A-Z][A-Z0-9]+-\d+)\b'
                                found_tickets = set(re.findall(linked_ticket_pattern, result_text))
                                # Remove the original ticket
                                found_tickets.discard(jira_tickets[0])
                                
                                for ticket_id in list(found_tickets)[:10]:  # Limit to 10
                                    linked_items.append({
                                        'key': ticket_id,
                                        'summary': 'Linked issue (details not available)',
                                        'status': 'Unknown',
                                        'assignee': 'Unknown',
                                        'link_type': 'Related',
                                        'url': f"https://arlo.atlassian.net/browse/{ticket_id}"
                                    })
                                
                                if found_tickets:
                                    print(f"🔗 Found {len(found_tickets)} linked ticket(s) via regex")
                    
                    print(f"\n📊 SUMMARY: Total linked items collected: {len(linked_items)}")
                    if linked_items:
                        for item in linked_items:
                            print(f"  - {item['key']}: {item['summary'][:40]}... ({item['link_type']})")
                
                # Build HTML response with all tool results
                logging.info(f"🎨 Building HTML response - tool_results has {len(tool_results)} items")
                if tool_results:
                    # Tools to hide by default (already shown in formatted cards)
                    # Don't hide jira_search if we're showing all tickets by status
                    if jira_search_status:
                        hidden_tools = ['jira_find_user', 'jira_list_projects', 'jira_read_issue']
                    else:
                        hidden_tools = ['jira_find_user', 'jira_list_projects', 'jira_read_issue', 'jira_search']
                    
                    # Check if user explicitly wants to see hidden tools
                    show_all = any(phrase in question.lower() for phrase in [
                        'show all', 'show hidden', 'display all', 'display hidden'
                    ])
                    
                    results_html = ""
                    visible_results_count = 0
                    for idx, tr in enumerate(tool_results):
                        # Skip hidden tools unless user explicitly requests them
                        if tr['tool'] in hidden_tools and not show_all:
                            print(f"   🙈 Hiding {tr['tool']} (already shown in formatted view)")
                            continue
                        
                        visible_results_count += 1
                        
                        # Use smart formatting based on tool type and content
                        result_html = format_mcp_result(tr['tool'], tr['result'])
                        
                        # Create unique IDs for each collapsible section
                        tool_id = f"tool-result-{idx}"
                        
                        results_html += f"""
                        <div style='margin-bottom: 20px; padding: 12px; background-color: white; border-radius: 6px; border-left: 4px solid #667eea; box-shadow: 0 1px 3px rgba(0,0,0,0.1);'>
                            <div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;'>
                                <h3 style='margin: 0; color: #667eea; font-size: 14px; font-weight: bold;'>
                                    🔧 {html.escape(tr['tool'])}
                                </h3>
                                <button 
                                    id="btn-{tool_id}" 
                                    onclick="toggleResult('{tool_id}')"
                                    style='background: #667eea; color: white; border: none; padding: 4px 12px; border-radius: 4px; cursor: pointer; font-size: 11px; font-weight: bold; transition: background 0.2s;'
                                    onmouseover="this.style.background='#5568d3'"
                                    onmouseout="this.style.background='#667eea'">
                                    ▼ Expand
                                </button>
                            </div>
                            <p style='margin: 0 0 10px 0; font-size: 12px; color: #718096;'>
                                {html.escape(tr['description']) if tr['description'] else 'No description'}
                            </p>
                            <div id="{tool_id}" style='font-size: 13px; max-height: 120px; overflow: hidden; position: relative; transition: max-height 0.3s ease-out;'>
                                {result_html}
                                <div style='position: absolute; bottom: 0; left: 0; right: 0; height: 40px; background: linear-gradient(to bottom, transparent, white); pointer-events: none;'></div>
                            </div>
                        </div>
                        """
                    
                    # Build Jira ticket links if detected
                    jira_links_html = ""
                    if jira_tickets:
                        jira_links = []
                        for ticket in jira_tickets:
                            jira_url = f"https://arlo.atlassian.net/browse/{ticket}"
                            jira_links.append(f'<a href="{jira_url}" target="_blank" style="color: white; text-decoration: underline; margin-right: 12px;">🎫 {ticket}</a>')
                        jira_links_html = f"""
                        <div style='margin-top: 8px; padding-top: 8px; border-top: 1px solid rgba(255,255,255,0.3);'>
                            <p style='margin: 0; font-size: 11px; opacity: 0.9;'>Jira Tickets:</p>
                            <div style='margin-top: 4px;'>
                                {''.join(jira_links)}
                            </div>
                        </div>
                        """
                    
                    # Build main ticket info section
                    main_ticket_html = ""
                    if main_ticket_info:
                        # Determine status color for main ticket
                        main_status_lower = main_ticket_info['status'].lower()
                        if any(word in main_status_lower for word in ['done', 'resolved', 'closed', 'completed', 'finished']):
                            main_status_color = '#10b981'  # Green
                        elif 'new' in main_status_lower:
                            main_status_color = '#6b7280'  # Gray
                        elif 'progress' in main_status_lower:
                            main_status_color = '#3b82f6'  # Blue
                        else:
                            main_status_color = '#6b7280'  # Gray by default
                        
                        # Format dates if available
                        created_date = main_ticket_info['created']
                        updated_date = main_ticket_info['updated']
                        if created_date != 'Unknown':
                            try:
                                created_date = created_date.split('T')[0]  # Extract just the date
                            except:
                                pass
                        if updated_date != 'Unknown':
                            try:
                                updated_date = updated_date.split('T')[0]  # Extract just the date
                            except:
                                pass
                        
                        # Truncate description
                        description = main_ticket_info['description']
                        if len(description) > 300:
                            description = description[:300] + '...'
                        
                        main_ticket_html = f"""
                        <div style='background: white; padding: 20px; margin: 12px 0; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); border: 2px solid #e5e7eb;'>
                            <div style='display: flex; justify-content: space-between; align-items: start; margin-bottom: 12px;'>
                                <div style='flex: 1;'>
                                    <div style='font-size: 24px; font-weight: bold; color: #667eea; margin-bottom: 8px;'>
                                        <a href="https://arlo.atlassian.net/browse/{html.escape(main_ticket_info['key'])}" target="_blank" style="color: #667eea; text-decoration: none;">
                                            🎫 {html.escape(main_ticket_info['key'])}
                                        </a>
                                    </div>
                                    <div style='font-size: 16px; color: #374151; margin-bottom: 12px; line-height: 1.4;'>
                                        {html.escape(main_ticket_info['summary'])}
                                    </div>
                                </div>
                                <div style='margin-left: 16px;'>
                                    <span style='background: {main_status_color}; color: white; padding: 6px 12px; border-radius: 12px; font-weight: bold; font-size: 13px; white-space: nowrap;'>
                                        {html.escape(main_ticket_info['status'])}
                                    </span>
                                </div>
                            </div>
                            
                            <div style='display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px;'>
                                <div style='background: #f9fafb; padding: 10px; border-radius: 6px; border: 1px solid #e5e7eb;'>
                                    <div style='font-size: 11px; color: #6b7280; margin-bottom: 4px;'>👤 Assignee</div>
                                    <div style='font-size: 13px; color: #374151; font-weight: 600;'>{html.escape(main_ticket_info['assignee'])}</div>
                                </div>
                                <div style='background: #f9fafb; padding: 10px; border-radius: 6px; border: 1px solid #e5e7eb;'>
                                    <div style='font-size: 11px; color: #6b7280; margin-bottom: 4px;'>📝 Reporter</div>
                                    <div style='font-size: 13px; color: #374151; font-weight: 600;'>{html.escape(main_ticket_info['reporter'])}</div>
                                </div>
                                <div style='background: #f9fafb; padding: 10px; border-radius: 6px; border: 1px solid #e5e7eb;'>
                                    <div style='font-size: 11px; color: #6b7280; margin-bottom: 4px;'>⚠️ Priority</div>
                                    <div style='font-size: 13px; color: #374151; font-weight: 600;'>{html.escape(main_ticket_info['priority'])}</div>
                                </div>
                                <div style='background: #f9fafb; padding: 10px; border-radius: 6px; border: 1px solid #e5e7eb;'>
                                    <div style='font-size: 11px; color: #6b7280; margin-bottom: 4px;'>📅 Created</div>
                                    <div style='font-size: 13px; color: #374151; font-weight: 600;'>{html.escape(created_date)}</div>
                                </div>
                            </div>
                            
                            <div style='background: #f9fafb; padding: 12px; border-radius: 6px; border: 1px solid #e5e7eb;'>
                                <div style='font-size: 11px; color: #6b7280; margin-bottom: 6px;'>📄 Description</div>
                                <div style='font-size: 12px; color: #374151; line-height: 1.5;'>{html.escape(description) if description != 'No description' else '<em>No description available</em>'}</div>
                            </div>
                        </div>
                        """
                    
                    # Build linked work items section
                    linked_items_html = ""
                    if linked_items:
                        linked_html_items = []
                        for item in linked_items:
                            # Determine status color based on status text
                            status_lower = item['status'].lower()
                            is_closed = any(word in status_lower for word in ['done', 'resolved', 'closed', 'completed', 'finished'])
                            
                            if is_closed:
                                status_color = '#10b981'  # Green for closed
                                text_decoration = 'line-through'  # Strike through if closed
                            elif 'new' in status_lower:
                                status_color = '#6b7280'  # Gray for new
                                text_decoration = 'none'
                            elif 'progress' in status_lower:
                                status_color = '#3b82f6'  # Blue for in progress
                                text_decoration = 'none'
                            else:
                                status_color = '#6b7280'  # Gray by default
                                text_decoration = 'none'
                            
                            linked_html_items.append(f"""
                            <div style='background: white; padding: 10px; border-radius: 6px; border-left: 3px solid {status_color}; margin-bottom: 8px;'>
                                <div style='display: flex; justify-content: space-between; align-items: start;'>
                                    <div style='flex: 1;'>
                                        <div style='font-weight: bold; color: #667eea; margin-bottom: 4px;'>
                                            <a href="{item['url']}" target="_blank" style="color: #667eea; text-decoration: {text_decoration};">
                                                🎫 {html.escape(item['key'])}
                                            </a>
                                        </div>
                                        <div style='font-size: 12px; color: #374151; margin-bottom: 6px;'>
                                            {html.escape(item['summary'][:100] + ('...' if len(item['summary']) > 100 else ''))}
                                        </div>
                                        <div style='font-size: 11px; color: #6b7280; margin-bottom: 4px;'>
                                            <span style='background: {status_color}; color: white; padding: 2px 6px; border-radius: 10px; font-weight: bold;'>
                                                {html.escape(item['status'])}
                                            </span>
                                            <span style='margin-left: 8px; color: #9ca3af;'>
                                                {html.escape(item['link_type'])}
                                            </span>
                                        </div>
                                        <div style='font-size: 11px; color: #6b7280;'>
                                            <span style='font-weight: 600; color: #374151;'>👤 Assignee:</span>
                                            <span style='margin-left: 4px; color: #4b5563;'>
                                                {html.escape(item.get('assignee', 'Unassigned'))}
                                            </span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            """)
                        
                        linked_items_html = f"""
                        <div style='background-color: #f0f4ff; padding: 16px; margin: 12px 0; border-radius: 6px; border: 2px solid #667eea;'>
                            <h3 style='margin: 0 0 12px 0; color: #667eea; font-size: 14px; font-weight: bold;'>
                                🔗 Linked Work Items ({len(linked_items)})
                            </h3>
                            {''.join(linked_html_items)}
                        </div>
                        """
                    
                    final_html = f"""
                    <script>
                    function toggleResult(id) {{
                        const content = document.getElementById(id);
                        const btn = document.getElementById('btn-' + id);
                        const gradient = content.querySelector('div[style*="linear-gradient"]');
                        
                        if (content.style.maxHeight === 'none' || content.style.maxHeight === '') {{
                            content.style.maxHeight = '120px';
                            content.style.overflow = 'hidden';
                            btn.innerHTML = '▼ Expand';
                            if (gradient) gradient.style.display = 'block';
                        }} else {{
                            content.style.maxHeight = 'none';
                            content.style.overflow = 'visible';
                            btn.innerHTML = '▲ Collapse';
                            if (gradient) gradient.style.display = 'none';
                        }}
                    }}
                    </script>
                    <div style='background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                                padding: 12px; border-radius: 6px; margin: 8px 0; color: white; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
                        <h2 style='margin: 0 0 6px 0; color: white; font-size: 16px; font-weight: bold;'>
                            🤖 GocBedrock MCP Results
                        </h2>
                        <p style='margin: 0; font-size: 12px; opacity: 0.95;'>
                            Direct Mode • {visible_results_count} detailed tool result(s) shown
                        </p>
                        {jira_links_html}
                    </div>
                    {main_ticket_html}
                    {linked_items_html}
                    <div style='background-color: #f7fafc; padding: 16px; margin: 8px 0; border-radius: 4px;'>
                        {results_html}
                        {f'''
                        <div style='background-color: #e0e7ff; padding: 10px; border-radius: 4px; margin-top: 12px; border-left: 3px solid #667eea;'>
                            <p style='margin: 0; font-size: 11px; color: #4338ca;'>
                                💡 <strong>Tip:</strong> Some technical results are hidden (already shown above). 
                                To see them, use: "show all" or "display all"
                            </p>
                        </div>
                        ''' if (len(tool_results) - visible_results_count) > 0 and not show_all else ''}
                    </div>
                    """
                else:
                    # ========== EMERGENCY FALLBACK ==========
                    # If MCP tools returned nothing, try local Wiki tool as backup
                    logging.warning(f"⚠️  No results from MCP - activating emergency fallback to local Wiki tool")
                    
                    try:
                        from tools.confluence_tool import confluence_search
                        
                        # Extract search term if it's an informational question
                        search_term = question
                        search_patterns = [
                            r'^what\s+is\s+(.+)', r'^que\s+es\s+(.+)', r'^qué\s+es\s+(.+)',
                            r'^how\s+to\s+(.+)', r'^como\s+hacer\s+(.+)', r'^cómo\s+hacer\s+(.+)',
                            r'^where\s+is\s+(.+)', r'^donde\s+esta\s+(.+)', r'^dónde\s+está\s+(.+)',
                            r'^explain\s+(.+)', r'^explica\s+(.+)',
                            r'^tell\s+me\s+about\s+(.+)', r'^dime\s+acerca\s+de\s+(.+)'
                        ]
                        for pattern in search_patterns:
                            match = re.search(pattern, question.lower())
                            if match:
                                search_term = match.group(1).strip()
                                logging.info(f"🔍 Extracted search term for Wiki fallback: '{search_term}'")
                                break
                        
                        logging.info(f"🆘 Executing local Wiki tool as emergency fallback with query: '{search_term}'")
                        wiki_result = confluence_search(search_term)
                        
                        if wiki_result and len(wiki_result) > 50:
                            logging.info(f"✅ Wiki fallback successful! Got {len(wiki_result)} characters")
                            final_html = f"""
                            <div style='background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%); 
                                        padding: 12px; border-radius: 6px; margin: 8px 0; color: white; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
                                <h2 style='margin: 0 0 6px 0; color: white; font-size: 16px; font-weight: bold;'>
                                    🆘 Emergency Fallback - Local Wiki
                                </h2>
                                <p style='margin: 0; font-size: 12px; opacity: 0.95;'>
                                    MCP tools didn't return data, using local Confluence search instead
                                </p>
                            </div>
                            <div style='background-color: #f7fafc; padding: 16px; margin: 8px 0; border-radius: 4px; border-left: 4px solid #f59e0b;'>
                                {wiki_result}
                            </div>
                            """
                        else:
                            logging.warning(f"⚠️  Wiki fallback also returned no results")
                            final_html = """
                            <div style='background-color: #fff3cd; padding: 12px; border-left: 4px solid #ffc107; border-radius: 4px; margin: 8px 0;'>
                                <p style='margin: 0; color: #856404;'>
                                    ⚠️ <strong>No results found</strong><br>
                                    Neither MCP tools nor local Wiki returned data for your query.
                                </p>
                            </div>
                            """
                    except Exception as fallback_error:
                        logging.error(f"❌ Wiki fallback failed: {fallback_error}")
                        final_html = f"""
                        <div style='background-color: #fff3cd; padding: 12px; border-left: 4px solid #ffc107; border-radius: 4px; margin: 8px 0;'>
                            <p style='margin: 0; color: #856404;'>
                                ⚠️ <strong>No results found</strong><br>
                                None of the MCP tools returned data for your query.<br>
                                <small>Emergency Wiki fallback also failed: {html.escape(str(fallback_error)[:100])}</small>
                            </p>
                        </div>
                        """
                
                return final_html
                
    except Exception as e:
        error_msg = str(e)
        error_type = type(e).__name__
        
        print(f"❌ Error ({error_type}): {error_msg[:200]}")
        import traceback
        traceback.print_exc()
        
        # Check for specific error types
        if "DNS resolution failed" in error_msg or "Could not contact DNS servers" in error_msg:
            hint = _mcp_connect_hint_html()
            return f"""
            <div style='background-color: #fee; padding: 12px; border-left: 4px solid #f56565; border-radius: 4px; margin: 8px 0;'>
                <p style='margin: 0; color: #c53030;'>
                    ❌ <strong>MCP Server Connection Error</strong><br><br>
                    <strong>Problem:</strong> Cannot resolve DNS for MCP server ({html.escape(get_mcp_server_url())})<br><br>
                    <strong>Recommendations:</strong><br>
                    {hint}
                </p>
            </div>
            """
        elif "ServiceUnavailable" in error_type or "503" in error_msg:
            return """
            <div style='background-color: #fee; padding: 12px; border-left: 4px solid #f56565; border-radius: 4px; margin: 8px 0;'>
                <p style='margin: 0; color: #c53030;'>
                    ❌ <strong>MCP Server Unavailable</strong><br><br>
                    The MCP server is temporarily unavailable (503).<br><br>
                    Please try again in a few moments.
                </p>
            </div>
            """
        elif "ExceptionGroup" in error_type or "TaskGroup" in error_msg:
            return f"""
            <div style='background-color: #fee; padding: 12px; border-left: 4px solid #f56565; border-radius: 4px; margin: 8px 0;'>
                <p style='margin: 0; color: #c53030;'>
                    ❌ <strong>MCP Session Error</strong><br><br>
                    An error occurred during communication with the MCP server.<br><br>
                    <strong>Details:</strong> {html.escape(error_msg[:300])}<br><br>
                    <strong>Recommendations:</strong><br>
                    • Check your internet connection<br>
                    {_mcp_connect_hint_html()}
                    • Try again<br>
                    • If the problem persists, check server logs
                </p>
            </div>
            """
        else:
            return f"""
            <div style='background-color: #fee; padding: 12px; border-left: 4px solid #f56565; border-radius: 4px; margin: 8px 0;'>
                <p style='margin: 0; color: #c53030;'>
                    ❌ <strong>Error: {html.escape(error_type)}</strong><br><br>
                    {html.escape(error_msg[:500])}<br><br>
                    <strong>Recommendations:</strong><br>
                    • Check your internet connection<br>
                    {_mcp_connect_hint_html()}
                    • Review logs for more details
                </p>
            </div>
            """


async def ask_arlo_with_bedrock_intelligence_async(question: str = "", context_from_other_tools: Optional[Dict[str, str]] = None) -> str:
    """
    Ask GocBedrock via MCP using Bedrock intelligence (async version with official SDK).
    Uses AWS Bedrock to analyze the question and intelligently select and execute MCP tools.
    
    Args:
        question: The user's question/prompt (full text)
        context_from_other_tools: Optional dict with results from other tools (e.g., DD_Red_Metrics, DD_Search)
                                   Format: {"tool_name": "html_result", ...}
    Returns:
        HTML formatted tool results
    """
    print("=" * 80)
    print("🤖 GocBedrock MCP - Bedrock Intelligence Mode (SDK)")
    print(f"📝 Question: '{question}'")
    print(f"🌐 MCP Server: {mcp_transport_label()}")
    
    if not question or not question.strip():
        return """
        <div style='background-color: #fff3cd; padding: 12px; border-left: 4px solid #ffc107; border-radius: 4px; margin: 8px 0;'>
            <p style='margin: 0; color: #856404;'>
                ⚠️ <strong>No question provided.</strong><br>
                Please enter a question to ask GocBedrock.
            </p>
        </div>
        """
    
    try:
        from tools.bedrock_tool import ask_bedrock
        
        print("🔗 Connecting to MCP server...")
        async with open_mcp_session() as session:
                
                print("📋 Fetching available tools from MCP...")
                mcp_tools_response = await session.list_tools()
                mcp_tools_list = mcp_tools_response.tools
                
                print(f"✅ Got {len(mcp_tools_list)} tools from MCP")
                
                if not mcp_tools_list:
                    raise Exception("No tools available from MCP server")

                from tools.jira_mcp import is_jira_question, run_jira_mcp_search
                from tools.mcp_intent_router import resolve_mcp_fast_route
                from tools.mcp_tool_suggest import (
                    bedrock_service_health_tool_calls,
                    is_service_health_question,
                )
                from tools.service_query import extract_service_name_from_query
                from tools.shm_tools import (
                    get_shm_metrics_mcp,
                    is_shm_daily_question,
                    is_shm_metrics_question,
                )

                fast_route = resolve_mcp_fast_route(question)
                if fast_route:
                    print(fast_route.log_label)
                    fast_result = await session.call_tool(
                        fast_route.tool_name,
                        fast_route.arguments,
                    )
                    fast_html = _mcp_call_result_text(fast_result)
                    if not fast_html.strip():
                        fast_html = (
                            f"<p style='color:#b45309;'>No response from {fast_route.tool_name}.</p>"
                        )
                    return _mcp_direct_response_html(
                        fast_route.title,
                        fast_html,
                        fast_route.gradient,
                    )

                tool_results = []
                if is_shm_metrics_question(question):
                    print("📊 SHM customer satisfaction query — prefetching shm_metrics (shmview API)...")
                    try:
                        shm_html = get_shm_metrics_mcp(
                            question=question,
                            force_live=bool(
                                re.search(
                                    r"\b(?:rating|ratings|satisfac|csat|android|ios)\b",
                                    question,
                                    re.I,
                                )
                            ),
                        )
                        if (shm_html or "").strip():
                            tool_results.append({
                                "tool": "shm_metrics",
                                "result": shm_html,
                                "description": "SHM KPIs from shmview.arlocloud.com (Tableau app ratings, CSAT pillar)",
                                "reason": "Customer satisfaction / iOS Android ratings — local SHM API (not MintMCP Amplitude)",
                            })
                            print("   ✅ Prefetch shm_metrics")
                    except Exception as shm_err:
                        print(f"   ⚠️ Prefetch shm_metrics failed: {shm_err}")

                if is_jira_question(question):
                    print("🎫 Jira query detected — running MintMCP Jira search...")
                    jira_hit = await run_jira_mcp_search(session, question)
                    if jira_hit:
                        print(f"   ✅ Jira MCP ({jira_hit['tool']}) JQL: {jira_hit.get('jql')}")
                        tool_results.append({
                            "tool": jira_hit["tool"],
                            "result": jira_hit["result"],
                            "description": f"Jira search: {jira_hit.get('jql', '')}",
                            "reason": f"Auto Jira search — {jira_hit.get('jql', '')}",
                        })
                    else:
                        print("   ⚠️ Jira MCP search returned no results")
                
                # Convert to dict format for Bedrock
                mcp_tools = []
                for tool in mcp_tools_list:
                    mcp_tools.append({
                        'name': tool.name,
                        'description': tool.description if hasattr(tool, 'description') else 'No description'
                    })
                
                # Build tools list for Bedrock
                tools_description = "Available MCP tools:\n\n"
                tools_map_mcp = {}  # Map name to MCP tool object
                for i, tool in enumerate(mcp_tools_list):
                    tool_name = tool.name
                    tool_desc = tool.description if hasattr(tool, 'description') else 'No description'
                    tools_description += f"- **{tool_name}**: {tool_desc}\n"
                    tools_map_mcp[tool_name] = tool

                # Service-specific health: prefetch Datadog before Bedrock tool-pick
                service_name = extract_service_name_from_query(question)
                if is_service_health_question(question) and service_name:
                    print(
                        f"📊 Service health query for '{service_name}' — "
                        "prefetching Datadog MCP tools..."
                    )
                    for tool_call in bedrock_service_health_tool_calls(service_name):
                        tname = tool_call.get("tool_name")
                        tparams = tool_call.get("params") or {}
                        treason = tool_call.get("reason") or ""
                        if not tname or tname not in tools_map_mcp:
                            continue
                        try:
                            result = await session.call_tool(tname, tparams)
                            result_text = _mcp_call_result_text(result)
                            if result_text.strip():
                                tool_results.append({
                                    "tool": tname,
                                    "result": result_text,
                                    "description": treason,
                                    "reason": treason,
                                })
                                print(f"   ✅ Prefetch {tname}")
                        except Exception as prefetch_err:
                            print(f"   ⚠️ Prefetch {tname} failed: {prefetch_err}")

                # Step 1: Ask Bedrock to analyze and select tools
                print("\n🧠 Step 1: Asking Bedrock to analyze question and select MCP tools...")
                service_dd_rule = ""
                if service_name:
                    service_dd_rule = (
                        f'- SERVICE QUERY detected ("{service_name}"): MUST call datadog_services, '
                        f"datadog_search, datadog_errors, datadog_red_metrics with service/query="
                        f'"{service_name}" (in addition to any prefetched data).\n'
                    )
                analysis_prompt = f"""You are Bedrock Report, an AI assistant that helps with Arlo infrastructure questions.

{tools_description}

User question: "{question}"

Analyze the user's question and decide which MCP tools (if any) you need to call to answer it.
Respond with ONLY a JSON object (no markdown, no explanation):
{{
    "needs_tools": true/false,
    "tools_to_call": [
        {{"tool_name": "tool1", "reason": "why", "params": {{"param": "value"}}}},
        ...
    ],
    "direct_answer": "If no tools needed, provide answer here"
}}

Guidelines:
{service_dd_rule}- Jira (MintMCP): use atlassian-rovo__searchJiraIssuesUsingJql with cloudId + jql (e.g. project = "GOC" AND text ~ "shm")
- Single Jira issue: atlassian-rovo__getJiraIssue with cloudId + issueIdOrKey
- cloudId for arlo.atlassian.net: call atlassian-rovo__getAccessibleAtlassianResources if needed
- For Confluence searches: use cql parameter on atlassian-rovo__searchConfluenceUsingCql
- For Datadog service lookup: datadog_services + datadog_search with query=service name
- For Datadog metrics/errors for one service: datadog_errors + datadog_red_metrics with service= name
- For org-wide Datadog: datadog_red_metrics, datadog_red_adt, datadog_red_samsung, datadog_red_metrics_us, datadog_errors
- For Datadog maintenance windows / downtimes: use datadog_maintenance_windows with question
- For GRM deployments / release calendar: use grm_deployments with question
- For Arlo public status page (status.arlo.com): use arlo_public_status
- For NOC KT / knowledge transfer table: use noc_kt_search with query
- For Samsung PagerDuty external status board: use pagerduty_samsung_board
- For shift handoff report: use shift_report with mode shift1|shift2|shift3 (slow, several minutes)
- For status monitor hub / all environments health: use status_monitor_summary
- For SHM / customer satisfaction / iOS Android app ratings / CSAT / NPS: use shm_metrics (NOT MintMCP amplitude__* tools). Data comes from shmview.arlocloud.com KPI history + Tableau.
- For daily active users by OS (iOS/Android/Web): use shm_daily (shmdaily.arlocloud.com)
- For AWS CloudTrail lookup (admin): use aws_cloudtrail_search with resource_name + account_id
- For AWS Connect health (admin): use aws_connect_monitor
- If question asks what is wrong / status / errors for a named service, ALWAYS use Datadog tools (not only Confluence)
- If question is conversational with no data lookup, set needs_tools=false
- Extract specific search terms from the question

Return ONLY the JSON object."""

                # Skip redundant Bedrock tool-pick if Jira or SHM prefetch already succeeded
                if tool_results and (
                    is_jira_question(question) or is_shm_metrics_question(question)
                ):
                    analysis = {"needs_tools": False, "direct_answer": ""}
                    print("📊 Skipping Bedrock tool selection — prefetched results already available")
                else:
                    # Call Bedrock
                    analysis_response = ask_bedrock(analysis_prompt, selected_tools=None)
                    
                    # Extract JSON from response
                    json_match = re.search(r'\{.*\}', analysis_response, re.DOTALL)
                    if json_match:
                        analysis = json.loads(json_match.group(0))
                    else:
                        print(f"⚠️  Failed to parse Bedrock response: {analysis_response[:200]}")
                        analysis = {"needs_tools": False, "direct_answer": analysis_response}
                
                print(f"📊 Analysis: {json.dumps(analysis, indent=2)}")
                
                # Step 2: Execute selected tools using SDK
                if analysis.get("needs_tools", False):
                    tools_to_call = analysis.get("tools_to_call", [])
                    print(f"\n🔧 Step 2: Executing {len(tools_to_call)} selected MCP tool(s)...")
                    
                    for tool_call in tools_to_call:
                        tool_name = tool_call.get("tool_name")
                        tool_params = tool_call.get("params", {})
                        reason = tool_call.get("reason", "")
                        
                        if tool_name not in tools_map_mcp:
                            print(f"⚠️  Tool '{tool_name}' not found")
                            continue
                        
                        print(f"\n🎯 Calling: {tool_name}")
                        print(f"   Reason: {reason}")
                        print(f"   Params: {tool_params}")
                        
                        # Call MCP tool using SDK
                        try:
                            result = await session.call_tool(tool_name, tool_params)
                            
                            # Extract text from result
                            result_text = ""
                            if hasattr(result, 'content'):
                                for content in result.content:
                                    if hasattr(content, 'text'):
                                        result_text += content.text
                            
                            if result_text:
                                # Check for error messages
                                result_lower = result_text.lower()[:200]
                                if any(error_keyword in result_lower for error_keyword in [
                                    'error executing tool', 'error:', 'exception:', 'failed to',
                                    'could not', 'unable to', 'permission denied', 'not found',
                                    'connection refused', 'timeout'
                                ]):
                                    print(f"   ⚠️  Skipping - contains error message")
                                else:
                                    # Truncate long results
                                    if len(result_text) > 5000:
                                        result_text = result_text[:5000] + "\n... (truncated)"
                                    
                                    print(f"   ✅ Success! Got {len(result_text)} characters")
                                    tool_results.append({
                                        "tool": tool_name,
                                        "result": result_text,
                                        "reason": reason
                                    })
                        except Exception as e:
                            print(f"   ❌ Error calling tool: {e}")
                
                # Step 3: Issues-only context → compact Bedrock synthesis
                print("\n💬 Step 3: Generating triage report with Bedrock (issues-only context)...")

                from tools.issues_context import build_bedrock_report_prompt, build_issues_context

                issues_block, summary_line, recurrence_block = build_issues_context(
                    context_from_other_tools,
                    tool_results,
                )
                print(
                    f"   Issues context: {len(issues_block):,} chars | {summary_line} | "
                    f"recurrence: {len(recurrence_block):,} chars"
                )

                if issues_block.strip() or context_from_other_tools or tool_results:
                    response_prompt = build_bedrock_report_prompt(
                        question, issues_block, summary_line, recurrence_block
                    )
                else:
                    response_prompt = f"""You are GocBedrock, an SRE assistant for Arlo infrastructure.

User question: "{question}"

No monitoring tool data was provided. Answer briefly in HTML (<div>...</div>).
Focus on what the user should check; do not invent live metrics.
Return ONLY HTML, no markdown fences."""

                response_html = ask_bedrock(response_prompt, selected_tools=None)
                
                # Clean up any remaining markdown code blocks
                if '```' in response_html:
                    json_match = re.search(r'```(?:html)?\s*\n(.*?)\n```', response_html, re.DOTALL)
                    if json_match:
                        response_html = json_match.group(1)
                
                # SPECIFIC cleanup - ONLY remove "Tools Executed" sections, NOT Jira or Recommendations
                # 1. Remove ONLY divs with cyan/teal background that contain "Tools Executed"
                response_html = re.sub(r'(?i)<div[^>]*background[^>]*(?:#e0f2fe|#cfe9f8|#bae6fd)[^>]*>[\s\S]*?(?:🔧\s*)?tools?\s+executed[\s\S]*?</div>', '', response_html, flags=re.DOTALL)
                # 2. Remove h2/h3 with EXACT text "Tools Executed" (don't touch other headings)
                response_html = re.sub(r'(?i)<h[23][^>]*>\s*(?:🔧|📊)?\s*tools?\s+executed:?\s*</h[23]>', '', response_html)
                # 3. Remove bullet lists ONLY if they say "Tools Executed:" at start
                response_html = re.sub(r'(?i)<ul[^>]*>\s*<li[^>]*>(?:🔧|📊)?\s*tools?\s+executed:?[\s\S]*?</ul>', '', response_html, flags=re.DOTALL)
                # 4. Remove standalone line that starts with "Tools Executed:"
                response_html = re.sub(r'(?i)^(?:🔧|📊)?\s*tools?\s+executed:.*?$', '', response_html, flags=re.MULTILINE)
                
                print(f"✅ Generated response: {len(response_html)} characters (cleaned)")
                
                # PYTHON-SIDE: Extract ALL Jira tickets from MCP tool results
                jira_tickets = []
                seen_jira_ids: set[str] = set()

                def _append_jira_ticket(ticket_id: str, summary: str, status: str, assignee: str) -> None:
                    tid = (ticket_id or "").strip()
                    if not tid or tid in seen_jira_ids:
                        return
                    seen_jira_ids.add(tid)
                    jira_tickets.append({
                        'id': tid,
                        'summary': (summary or 'No summary').strip(),
                        'status': (status or 'Unknown').strip(),
                        'assignee': (assignee or 'Unassigned').strip(),
                    })

                for tr in tool_results:
                    tool_name = str(tr.get('tool') or '')
                    tool_l = tool_name.lower()
                    if tool_l != 'jira_search' and not (
                        'atlassian' in tool_l and 'jira' in tool_l
                    ):
                        continue

                    result_text = str(tr.get('result') or '')
                    print(f"🎫 Extracting Jira tickets from {tool_name} ({len(result_text)} chars)")

                    if result_text.strip().startswith('{'):
                        try:
                            data = json.loads(result_text)
                            for row in (data.get('issues') or [])[:50]:
                                if not isinstance(row, dict):
                                    continue
                                key = str(row.get('key') or '')
                                fields = row.get('fields') or {}
                                summary = str(fields.get('summary') or '')
                                status_obj = fields.get('status')
                                status = (
                                    status_obj.get('name')
                                    if isinstance(status_obj, dict)
                                    else str(status_obj or '')
                                )
                                assignee_obj = fields.get('assignee')
                                assignee = 'Unassigned'
                                if isinstance(assignee_obj, dict):
                                    assignee = (
                                        assignee_obj.get('displayName')
                                        or assignee_obj.get('name')
                                        or 'Unassigned'
                                    )
                                _append_jira_ticket(key, summary, status, assignee)
                                print(f"  ✓ {key}: {summary[:60]}... [{status}]")
                            continue
                        except (json.JSONDecodeError, TypeError, AttributeError):
                            pass

                    print(f"📄 First 500 chars: {result_text[:500]}")

                    # MARKDOWN TABLE FORMAT (most common from MCP)
                    markdown_pattern = r'\|\s*([A-Z]+-\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|'
                    markdown_matches = list(re.finditer(markdown_pattern, result_text, re.MULTILINE))
                    print(f"🔍 Found {len(markdown_matches)} markdown table rows")

                    if markdown_matches:
                        for match in markdown_matches[1:]:
                            ticket_id = match.group(1).strip()
                            summary = match.group(2).strip()
                            status = match.group(3).strip()
                            assignee = match.group(4).strip()
                            if '---' in ticket_id or 'Key' in ticket_id:
                                continue
                            _append_jira_ticket(ticket_id, summary, status, assignee)
                            print(f"  ✓ {ticket_id}: {summary[:60]}... [{status}]")
                    else:
                        pattern1 = r'(?:Key|ID|Ticket):\s*([A-Z]+-\d+)[\s\S]{0,500}?Summary:\s*([^\n]+)'
                        pattern2 = r'^([A-Z]+-\d+)\s*[-:]\s*([^\n]+)'
                        all_matches = []
                        all_matches.extend(list(re.finditer(pattern1, result_text, re.IGNORECASE | re.MULTILINE)))
                        all_matches.extend(list(re.finditer(pattern2, result_text, re.MULTILINE)))
                        print(f"🔍 Fallback: Found {len(all_matches)} non-markdown matches")
                        for match in all_matches:
                            ticket_id = match.group(1).strip()
                            summary = match.group(2).strip() if len(match.groups()) >= 2 else 'No summary'
                            ticket_context = result_text[match.start():match.start()+400]
                            status_match = re.search(r'Status:\s*([^\n]+)', ticket_context, re.IGNORECASE)
                            assignee_match = re.search(r'Assignee:\s*([^\n]+)', ticket_context, re.IGNORECASE)
                            _append_jira_ticket(
                                ticket_id,
                                summary,
                                status_match.group(1).strip() if status_match else 'Unknown',
                                assignee_match.group(1).strip() if assignee_match else 'Unassigned',
                            )
                            print(f"  ✓ {ticket_id}: {summary[:60]}...")
                
                print(f"✅ Extracted {len(jira_tickets)} unique Jira tickets from MCP results")
                
                # Build Jira table HTML (Python-generated, guaranteed complete)
                jira_table_html = ""
                if jira_tickets:
                    jira_table_html = f"""
<div style='background: white; padding: 28px; border-radius: 16px; margin-top: 28px; border: 1px solid #e5e7eb;'>
    <h2 style='font-size: 22px; margin: 0 0 20px 0; color: #0f172a;'>🎫 Jira Tickets — GOC Project ({len(jira_tickets)} tickets)</h2>
    <table style='width: 100%; border-collapse: collapse; border: 2px solid #f1f5f9;'>
        <thead>
            <tr style='background: #1e293b;'>
                <th style='padding: 14px; text-align: center; font-size: 12px; color: white; font-weight: 700; width: 60px;'>#</th>
                <th style='padding: 14px; text-align: left; font-size: 12px; color: white; font-weight: 700; width: 140px;'>TICKET</th>
                <th style='padding: 14px; text-align: left; font-size: 12px; color: white; font-weight: 700;'>SUMMARY</th>
                <th style='padding: 14px; text-align: center; font-size: 12px; color: white; font-weight: 700; width: 140px;'>STATUS</th>
                <th style='padding: 14px; text-align: left; font-size: 12px; color: white; font-weight: 700; width: 180px;'>ASSIGNEE</th>
            </tr>
        </thead>
        <tbody>
"""
                    
                    for idx, ticket in enumerate(jira_tickets, 1):
                        # Determine status badge color
                        status_upper = ticket['status'].upper()
                        if any(kw in status_upper for kw in ['DONE', 'CLOSED', 'RESOLVED', 'COMPLETE']):
                            badge_bg = '#dcfce7'
                            badge_color = '#166534'
                        elif any(kw in status_upper for kw in ['PROGRESS', 'REVIEW', 'PENDING']):
                            badge_bg = '#dbeafe'
                            badge_color = '#1e40af'
                        else:  # NEW/OPEN
                            badge_bg = '#fef3c7'
                            badge_color = '#92400e'
                        
                        jira_table_html += f"""
            <tr style='border-bottom: 1px solid #f1f5f9;'>
                <td style='padding: 14px; text-align: center; color: #94a3b8; font-weight: 600;'>{idx}</td>
                <td style='padding: 14px;'><a href='https://arlo.atlassian.net/browse/{ticket['id']}' target='_blank' style='color: #6366f1; font-weight: 700; text-decoration: none;'>{ticket['id']}</a></td>
                <td style='padding: 14px; color: #334155; font-size: 14px;'>{html.escape(ticket['summary'])}</td>
                <td style='padding: 14px; text-align: center;'><span style='padding: 5px 12px; background: {badge_bg}; color: {badge_color}; border-radius: 12px; font-size: 11px; font-weight: 700;'>{html.escape(ticket['status'].upper())}</span></td>
                <td style='padding: 14px; color: #64748b;'>{html.escape(ticket['assignee'])}</td>
            </tr>
"""
                    
                    jira_table_html += """
        </tbody>
    </table>
</div>
"""
                
                # Build final HTML: Bedrock analysis + Python-generated Jira table (no suggestions)
                final_html = f"""
        <div style='background-color: white; padding: 16px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
            <div style='background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 12px; border-radius: 6px; margin-bottom: 16px;'>
                <h2 style='margin: 0; color: white; font-size: 16px;'>
                    🤖 GocBedrock Response (via MCP SDK + Bedrock)
                </h2>
            </div>
            <div style='background-color: #f7fafc; padding: 16px; border-radius: 4px;'>
                {response_html}
            </div>
            {jira_table_html}
        </div>
"""
                
                return final_html
        
    except Exception as e:
        error_msg = str(e)
        error_type = type(e).__name__
        
        print(f"❌ Error ({error_type}): {error_msg[:200]}")
        import traceback
        traceback.print_exc()
        
        return f"""
        <div style='background-color: #fee; padding: 12px; border-left: 4px solid #f56565; border-radius: 4px; margin: 8px 0;'>
            <p style='margin: 0; color: #c53030;'>
                ❌ <strong>Error:</strong> {html.escape(str(e))}<br><br>
                Make sure AWS Bedrock is configured and you are connected to Arlo VPN for MCP access.
            </p>
        </div>
        """


def ask_arlo_with_bedrock_intelligence(question: str = "", context_from_other_tools: Optional[Dict[str, str]] = None) -> str:
    """
    Sync wrapper for ask_arlo_with_bedrock_intelligence_async.
    
    Args:
        question: The user's question/prompt (full text)
        context_from_other_tools: Optional dict with results from other tools
    """
    return asyncio.run(ask_arlo_with_bedrock_intelligence_async(question, context_from_other_tools))


def ask_arlo_sync_legacy(question: str = "") -> str:
    """
    LEGACY: Ask GocBedrock via MCP using HTTP with Gemini (fallback when SDK not available).
    Uses Gemini for analysis. This is kept for backwards compatibility.
    
    Args:
        question: The user's question/prompt (full text)
    Returns:
        HTML formatted tool results
    """
    print("=" * 80)
    print("🤖 GocBedrock MCP - Direct Mode (HTTP Fallback - Gemini)")
    print(f"📝 Question: '{question}'")
    print(f"🌐 MCP Server: {get_mcp_server_url()}")
    
    if not question or not question.strip():
        return """
        <div style='background-color: #fff3cd; padding: 12px; border-left: 4px solid #ffc107; border-radius: 4px; margin: 8px 0;'>
            <p style='margin: 0; color: #856404;'>
                ⚠️ <strong>No question provided.</strong><br>
                Please enter a question to ask GocBedrock.
            </p>
        </div>
        """
    
    mcp_client = None
    try:
        # Configure Gemini
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise Exception("GEMINI_API_KEY not found in environment variables")
        
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.0-flash-exp")
        
        print("🔗 Connecting to MCP server via HTTP...")
        mcp_client = SimpleMCPClient(get_mcp_server_url())
        
        # Initialize MCP session
        if not mcp_client.initialize():
            raise Exception("Failed to initialize MCP session")
        
        # Get available tools
        mcp_tools = mcp_client.list_tools()
        if not mcp_tools:
            raise Exception("No tools available from MCP server")
        
        # Build tools list for Gemini
        tools_description = "Available tools:\n\n"
        tools_map = {}
        for tool in mcp_tools:
            tool_name = tool.get('name', 'unknown')
            tool_desc = tool.get('description', 'No description')
            tools_description += f"- **{tool_name}**: {tool_desc}\n"
            tools_map[tool_name] = tool
        
        # Step 1: Ask Gemini to select relevant tools
        print("\n🧠 Step 1: Asking Gemini to analyze question and select tools...")
        analysis_prompt = f"""You are GocBedrock, an AI assistant that helps with Arlo infrastructure questions.

{tools_description}

User question: "{question}"

Analyze the user's question and decide which tools (if any) you need to call to answer it.
Respond in JSON format with:
{{
    "needs_tools": true/false,
    "tools_to_call": [
        {{"tool_name": "tool1", "reason": "why", "params": {{"param": "value"}}}},
        ...
    ],
    "direct_answer": "If no tools needed, provide answer here"
}}

Guidelines:
- For Jira searches: use jql parameter like 'text ~ "keywords"' or 'summary ~ "keywords"'
- For Confluence searches: use cql parameter
- For Datadog metrics/dashboards: use datadog_red_metrics, datadog_red_samsung, datadog_red_metrics_us, datadog_errors
- For Datadog maintenance windows / downtimes: use datadog_maintenance_windows with question param
- For GRM deployments / calendar: use grm_deployments with question param
- For Arlo public status: use arlo_public_status
- For NOC KT table: use noc_kt_search with query
- For Samsung PagerDuty board: use pagerduty_samsung_board
- For shift handoff: use shift_report (mode shift1|shift2|shift3)
- For status monitor hub: use status_monitor_summary
- For AWS CloudTrail: use aws_cloudtrail_search
- For AWS Connect: use aws_connect_monitor
- **IMPORTANT**: If question starts with "what", "how", "where", "why", "when", "que", "como", "donde" or asks for explanations/information, prioritize Confluence tools (wiki/documentation)
- If question is conversational or doesn't need data lookup, set needs_tools=false
- Be selective - only call tools that are truly relevant
- Extract specific search terms from the question for better results"""

        analysis_response = model.generate_content(analysis_prompt)
        analysis_text = analysis_response.text.strip()
        
        # Extract JSON from response (handle markdown code blocks)
        if "```json" in analysis_text:
            analysis_text = analysis_text.split("```json")[1].split("```")[0].strip()
        elif "```" in analysis_text:
            analysis_text = analysis_text.split("```")[1].split("```")[0].strip()
        
        try:
            analysis = json.loads(analysis_text)
        except json.JSONDecodeError:
            print(f"⚠️  Failed to parse Gemini response as JSON: {analysis_text[:200]}")
            analysis = {"needs_tools": False, "direct_answer": analysis_text}
        
        print(f"📊 Analysis: {json.dumps(analysis, indent=2)}")
        
        # Step 2: Execute selected tools
        tool_results = []
        if analysis.get("needs_tools", False):
            tools_to_call = analysis.get("tools_to_call", [])
            print(f"\n🔧 Step 2: Executing {len(tools_to_call)} selected tool(s)...")
            
            for tool_call in tools_to_call:
                tool_name = tool_call.get("tool_name")
                tool_params = tool_call.get("params", {})
                reason = tool_call.get("reason", "")
                
                if tool_name not in tools_map:
                    print(f"⚠️  Tool '{tool_name}' not found")
                    continue
                
                print(f"\n🎯 Calling: {tool_name}")
                print(f"   Reason: {reason}")
                print(f"   Params: {tool_params}")
                
                result_text = mcp_client.call_tool(tool_name, tool_params)
                
                if result_text:
                    # Check for error messages
                    result_lower = result_text.lower()[:200]
                    if any(error_keyword in result_lower for error_keyword in [
                        'error executing tool',
                        'error:',
                        'exception:',
                        'failed to',
                        'could not',
                        'unable to',
                        'permission denied',
                        'not found',
                        'connection refused',
                        'timeout'
                    ]):
                        print(f"   ⚠️  Skipping - contains error message")
                    else:
                        # Truncate long results
                        if len(result_text) > 5000:
                            result_text = result_text[:5000] + "\n... (truncated)"
                        
                        print(f"   ✅ Success! Got {len(result_text)} characters")
                        tool_results.append({
                            "tool": tool_name,
                            "result": result_text,
                            "reason": reason
                        })
                else:
                    print(f"   ⚠️  No result returned")
        
        # Step 3: Generate conversational response
        print("\n💬 Step 3: Generating conversational response...")
        
        if tool_results:
            # Build context with tool results
            context = "Tool execution results:\n\n"
            for tr in tool_results:
                context += f"**{tr['tool']}** (called because: {tr.get('reason', tr.get('description', 'MCP'))}):\n{tr['result']}\n\n"
            
            response_prompt = f"""You are GocBedrock, a helpful AI assistant for Arlo infrastructure.

User question: "{question}"

{context}

Based on the tool results above, provide a natural, conversational response to the user's question.

Guidelines:
- Be friendly and conversational (like chatting in Slack)
- Format the response clearly (use markdown: headers, lists, code blocks)
- If data is tabular, present it in markdown table format
- Include relevant links if available
- If no useful results, say so politely
- Keep it concise but informative
- Use emojis sparingly for emphasis

Respond in plain text with markdown formatting (NOT HTML)."""
        else:
            # No tools needed - direct answer
            response_prompt = f"""You are GocBedrock, a helpful AI assistant for Arlo infrastructure.

User question: "{question}"

This question doesn't require looking up data. Provide a helpful, conversational response.

Guidelines:
- Be friendly and conversational (like chatting in Slack)
- If you can answer based on general knowledge, do so
- If you need more information, ask clarifying questions
- Use markdown formatting
- Keep it concise

Respond in plain text with markdown formatting (NOT HTML)."""
        
        response = model.generate_content(response_prompt)
        response_text = response.text.strip()
        
        print(f"✅ Generated response: {len(response_text)} characters")
        
        # Convert markdown to HTML for display
        response_html = markdown_to_html(response_text)
        
        # Wrap in GocBedrock styled container
        final_html = f"""
        <div style='background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                    padding: 12px; border-radius: 6px; margin: 8px 0; color: white; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
            <h2 style='margin: 0 0 6px 0; color: white; font-size: 16px; font-weight: bold;'>
                🤖 GocBedrock Response
            </h2>
            <p style='margin: 0; font-size: 12px; opacity: 0.95;'>
                Conversational Mode • {len(tool_results)} tool(s) used
            </p>
        </div>
        <div style='background-color: #f7fafc; padding: 16px; margin: 8px 0; border-radius: 4px; border-left: 4px solid #667eea;'>
            {response_html}
        </div>
        """
        
        return final_html
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return f"""
        <div style='background-color: #fee; padding: 12px; border-left: 4px solid #f56565; border-radius: 4px; margin: 8px 0;'>
            <p style='margin: 0; color: #c53030;'>
                ❌ <strong>Error:</strong> {html.escape(str(e))}<br><br>
                Make sure you have GEMINI_API_KEY configured and are connected to Arlo VPN.
            </p>
        </div>
        """
    finally:
        # Always close MCP client to cleanup SSE connection
        if mcp_client:
            try:
                mcp_client.close()
            except:
                pass


def markdown_to_html(markdown_text: str) -> str:
    """Convert markdown to HTML for display."""
    # Simple markdown conversion (headers, lists, code, bold, italic)
    lines = markdown_text.split('\n')
    html_lines = []
    in_code_block = False
    in_list = False
    code_lang = ""
    
    for line in lines:
        # Code blocks
        if line.startswith('```'):
            if in_code_block:
                html_lines.append('</code></pre>')
                in_code_block = False
            else:
                code_lang = line[3:].strip() or 'text'
                html_lines.append(f'<pre style="background-color: #2d3748; color: #e2e8f0; padding: 12px; border-radius: 4px; overflow-x: auto; font-size: 12px; font-family: monospace;"><code>')
                in_code_block = True
            continue
        
        if in_code_block:
            html_lines.append(html.escape(line))
            continue
        
        # Headers
        if line.startswith('### '):
            html_lines.append(f'<h3 style="margin: 16px 0 8px 0; color: #2d3748; font-size: 14px; font-weight: bold;">{html.escape(line[4:])}</h3>')
        elif line.startswith('## '):
            html_lines.append(f'<h2 style="margin: 16px 0 8px 0; color: #2d3748; font-size: 15px; font-weight: bold;">{html.escape(line[3:])}</h2>')
        elif line.startswith('# '):
            html_lines.append(f'<h1 style="margin: 16px 0 8px 0; color: #2d3748; font-size: 16px; font-weight: bold;">{html.escape(line[2:])}</h1>')
        # Lists
        elif line.startswith('- ') or line.startswith('* '):
            if not in_list:
                html_lines.append('<ul style="margin: 8px 0; padding-left: 24px;">')
                in_list = True
            content = line[2:].strip()
            # Handle inline markdown
            content = format_inline_markdown(content)
            html_lines.append(f'<li style="margin: 4px 0;">{content}</li>')
        elif line.startswith(('1. ', '2. ', '3. ', '4. ', '5. ')):
            if not in_list:
                html_lines.append('<ol style="margin: 8px 0; padding-left: 24px;">')
                in_list = True
            content = line[line.index('.')+1:].strip()
            content = format_inline_markdown(content)
            html_lines.append(f'<li style="margin: 4px 0;">{content}</li>')
        else:
            if in_list:
                html_lines.append('</ul>')
                in_list = False
            
            if line.strip():
                # Regular paragraph with inline markdown
                formatted_line = format_inline_markdown(line)
                html_lines.append(f'<p style="margin: 8px 0; line-height: 1.6; color: #2d3748;">{formatted_line}</p>')
            else:
                html_lines.append('<br>')
    
    if in_list:
        html_lines.append('</ul>')
    if in_code_block:
        html_lines.append('</code></pre>')
    
    return '\n'.join(html_lines)


def format_inline_markdown(text: str) -> str:
    """Format inline markdown (bold, italic, code, links)."""
    import re
    
    # Links [text](url)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2" target="_blank" style="color: #667eea; text-decoration: none;">\1</a>', text)
    
    # Bold **text**
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    
    # Italic *text*
    text = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', text)
    
    # Inline code `text`
    text = re.sub(r'`([^`]+)`', r'<code style="background-color: #e2e8f0; padding: 2px 4px; border-radius: 3px; font-size: 11px; font-family: monospace;">\1</code>', text)
    
    return text


def ask_arlo(question: str = "", context_from_other_tools: Optional[Dict[str, str]] = None) -> str:
    """
    Ask GocBedrock via MCP - uses Bedrock for intelligent tool selection and execution.
    
    This function:
    1. Connects to MCP server to get available tools
    2. Uses Bedrock to analyze the question and select appropriate MCP tools
    3. Executes the selected MCP tools
    4. Uses Bedrock to generate a conversational response with the results
    
    Args:
        question: The user's question/prompt (full text)
        context_from_other_tools: Optional dict with results from other tools (e.g., DD_Red_Metrics, DD_Search)
    Returns:
        HTML formatted conversational response
    """
    # Use Bedrock-powered intelligent MCP interaction
    return ask_arlo_with_bedrock_intelligence(question, context_from_other_tools)
