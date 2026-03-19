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

# MCP Server Configuration  
MCP_SERVER_URL = "http://internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com:8080"
MCP_SSE_ENDPOINT = f"{MCP_SERVER_URL}/sse"


class SimpleMCPClient:
    """Simple MCP client using HTTP requests for SSE-based MCP server."""
    
    def __init__(self, server_url: str):
        self.server_url = server_url
        self.session = requests.Session()
        self.session_id = None
        self.message_endpoint = None
        self.sse_connection = None
        self.sse_responses = {}  # Store responses by request_id
        self.sse_thread = None
        self.sse_running = False
        
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
        """Initialize MCP session via SSE."""
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
    print(f"🌐 MCP Server: {MCP_SSE_ENDPOINT}")
    
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
        print("🔗 Connecting to MCP server via SSE...")
        async with sse_client(MCP_SSE_ENDPOINT) as (read, write):
            async with ClientSession(read, write) as session:
                print("🔄 Initializing MCP session...")
                await session.initialize()
                
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
                    'jira': ['jira', 'ticket', 'issue', 'epic', 'story', 'bug'],
                    'confluence': ['confluence', 'wiki', 'document', 'page'],
                    'datadog': ['datadog', 'metric', 'monitor', 'dashboard', 'apm'],
                    'pagerduty': ['pagerduty', 'incident', 'alert', 'oncall'],
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
                            if category in tool_name_lower or category in tool_desc_lower:
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
                                if jira_search_status and 'jira' in tool_name.lower():
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
                                elif jira_tickets and 'jira' in tool_name.lower():
                                    # For tools that accept issue_key or key parameter
                                    if 'issue_key' in props or 'key' in props:
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
            return """
            <div style='background-color: #fee; padding: 12px; border-left: 4px solid #f56565; border-radius: 4px; margin: 8px 0;'>
                <p style='margin: 0; color: #c53030;'>
                    ❌ <strong>MCP Server Connection Error</strong><br><br>
                    <strong>Problem:</strong> Cannot resolve DNS for MCP server<br><br>
                    <strong>Possible causes:</strong><br>
                    • No internet connection<br>
                    • Not connected to Arlo VPN<br>
                    • DNS server issues<br><br>
                    <strong>Solutions:</strong><br>
                    1. Check your internet connection<br>
                    2. Connect to Arlo VPN<br>
                    3. Try again<br>
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
                    • Verify that you are connected to Arlo VPN (for MCP)<br>
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
                    • Verify that you are connected to Arlo VPN (for MCP)<br>
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
    print(f"🌐 MCP Server: {MCP_SSE_ENDPOINT}")
    
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
        # Import required modules at the start
        from tools.bedrock_tool import ask_bedrock
        from tools.deployments_calendar import get_grm_deployments
        
        # Check if question is about deployments (before MCP)
        question_lower = question.lower()
        deployment_keywords = ['deployment', 'deploy', 'despliegue', 'release', 'próximo', 
                              'proximas', 'pasados', 'pasado', 'past', 'anteriores', 'previous', 
                              'scheduled', 'programado', 'calendario', 'calendar', 'grm']
        
        is_deployment_query = any(kw in question_lower for kw in deployment_keywords)
        
        if is_deployment_query:
            print("📅 Detected deployment query - using local GRM Deployments tool")
            
            # Detect if asking for past deployments
            past_keywords = ['past', 'pasado', 'pasados', 'anteriores', 'previous', 'último', 'ultimos', 'last']
            is_past_query = any(kw in question_lower for kw in past_keywords)
            
            # Extract time range if mentioned
            limit_count = None  # Will limit results if user asked for specific number
            
            # Try to find time range with unit
            timerange_match = re.search(r'(\d+)\s*(hora|hour|horas|hours|day|days|dia|dias)', question_lower)
            if timerange_match:
                timerange_hours = int(timerange_match.group(1))
                unit = timerange_match.group(2)
                if 'day' in unit or 'dia' in unit:
                    timerange_hours *= 24  # Convert days to hours
                print(f"⏰ Extracted time range: {timerange_hours} hours")
            else:
                # Try to find number without unit (e.g., "past 3 deployments")
                number_match = re.search(r'(?:past|last|próximos?|últimos?|next)\s+(\d+)', question_lower)
                if number_match:
                    limit_count = int(number_match.group(1))
                    # Use a large window to ensure we capture enough deployments
                    timerange_hours = 72  # 3 days
                    print(f"⏰ User asked for {limit_count} deployments → using {timerange_hours} hours window, will limit to {limit_count} results")
                else:
                    # No specific number mentioned - if it's a generic "últimos/past/next" query, show at least 4
                    generic_plural_keywords = ['deployments', 'despliegues', 'releases', 'últimos', 'ultimos', 'past', 'próximos', 'proximos']
                    if any(kw in question_lower for kw in generic_plural_keywords):
                        timerange_hours = 96  # 4 days to capture enough deployments
                        limit_count = 4 if is_past_query else 5  # Show at least 4 for past, 5 for future
                        print(f"⏰ Generic plural query detected → using {timerange_hours} hours window, will show at least {limit_count} deployments")
                    else:
                        timerange_hours = 24  # Default for singular queries
            
            # If asking for past deployments, use negative timerange
            if is_past_query:
                print(f"⏮️  Query is for PAST deployments")
                timerange_hours = -timerange_hours  # Negative = past
            else:
                print(f"⏭️  Query is for FUTURE deployments")
            
            # Call GRM deployments directly (passing limit via query parameter if needed)
            query_param = f"limit:{limit_count}" if limit_count else ""
            deployments_result = get_grm_deployments(query_param, timerange_hours)
            
            # Use Bedrock to generate a conversational response
            time_context = "past" if is_past_query else "upcoming"
            response_prompt = f"""You are GocBedrock, an AI assistant for Arlo infrastructure.

User question: "{question}"

Context: {time_context} deployments ({"last" if is_past_query else "next"} {abs(timerange_hours)} hours).

GRM Calendar Data:
{deployments_result}

Use this EXACT HTML - BE VISUAL:

<div style="font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; max-width: 100%;">
    
    <!-- Hero Header -->
    <div style="background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #d946ef 100%); padding: 28px 24px; border-radius: 12px; margin-bottom: 28px; box-shadow: 0 12px 48px rgba(139, 92, 246, 0.3);">
        <div style="display: flex; align-items: center; gap: 14px; margin-bottom: 12px;">
            <span style="font-size: 36px;">📅</span>
            <h1 style="margin: 0; color: white; font-size: 26px; font-weight: 800; letter-spacing: -0.8px;">Deployment Calendar</h1>
        </div>
        <div style="display: inline-block; background: rgba(255,255,255,0.25); padding: 8px 16px; border-radius: 24px; backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.3);">
            <span style="color: white; font-size: 14px; font-weight: 700;">📌 {time_context.capitalize()}</span>
        </div>
    </div>
    
    <!-- Count Card -->
    <div style="display: inline-flex; align-items: center; gap: 12px; background: linear-gradient(135deg, #f0f9ff, #e0f2fe); padding: 16px 24px; border-radius: 12px; border: 2px solid #38bdf8; margin-bottom: 28px; box-shadow: 0 6px 20px rgba(56, 189, 248, 0.2);">
        <span style="font-size: 28px;">📊</span>
        <div>
            <div style="font-size: 32px; font-weight: 900; color: #0369a1; letter-spacing: -1px; line-height: 1;">[X]</div>
            <div style="font-size: 12px; color: #075985; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;">DEPLOYMENTS</div>
        </div>
    </div>
    
    <!-- Deployments Table -->
    <div style="background: white; padding: 26px; border-radius: 12px; border: 1px solid #e5e7eb; box-shadow: 0 6px 20px rgba(0,0,0,0.1);">
        <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 20px;">
            <span style="font-size: 30px;">🚀</span>
            <h2 style="margin: 0; color: #0f172a; font-size: 22px; font-weight: 800;">Deployments</h2>
        </div>
        
        <table style="width: 100%; border-collapse: separate; border-spacing: 0; border-radius: 8px; overflow: hidden;">
            <thead>
                <tr style="background: linear-gradient(to right, #f8fafc, #f1f5f9);">
                    <th style="padding: 14px 16px; text-align: left; color: #475569; font-weight: 800; text-transform: uppercase; font-size: 11px; letter-spacing: 1px; border-bottom: 2px solid #cbd5e1;">🚀 Service</th>
                    <th style="padding: 14px 16px; text-align: left; color: #475569; font-weight: 800; text-transform: uppercase; font-size: 11px; letter-spacing: 1px; border-bottom: 2px solid #cbd5e1;">📅 Date/Time</th>
                    <th style="padding: 14px 16px; text-align: left; color: #475569; font-weight: 800; text-transform: uppercase; font-size: 11px; letter-spacing: 1px; border-bottom: 2px solid #cbd5e1;">📝 Details</th>
                </tr>
            </thead>
            <tbody>
                <tr style="border-bottom: 1px solid #f1f5f9;" onmouseover="this.style.background='#f9fafb'" onmouseout="this.style.background='white'">
                    <td style="padding: 14px 16px; color: #6366f1; font-weight: 700; font-size: 14px;">[Service]</td>
                    <td style="padding: 14px 16px; color: #64748b; font-weight: 500;">[Date]</td>
                    <td style="padding: 14px 16px; color: #475569;">[Details]</td>
                </tr>
                <!-- OR if no deployments -->
                <tr>
                    <td colspan="3" style="padding: 32px; text-align: center;">
                        <span style="font-size: 48px; display: block; margin-bottom: 12px;">✨</span>
                        <div style="color: #64748b; font-size: 15px; font-weight: 600;">No deployments in this period</div>
                    </td>
                </tr>
            </tbody>
        </table>
    </div>
    
</div>

Return ONLY the HTML (no markdown blocks)."""
            
            response_html = ask_bedrock(response_prompt, selected_tools=None)
            
            # Clean up any remaining markdown code blocks
            if '```' in response_html:
                match = re.search(r'```(?:html)?\s*\n(.*?)\n```', response_html, re.DOTALL)
                if match:
                    response_html = match.group(1)
            
            # Wrap response with tool info
            calendar_type = "Past Deployments" if is_past_query else "Upcoming Deployments"
            final_html = f"""
        <div style='background-color: white; padding: 16px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
            <div style='background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 12px; border-radius: 6px; margin-bottom: 16px;'>
                <h2 style='margin: 0; color: white; font-size: 16px;'>
                    🤖 GocBedrock Response (GRM Calendar - {calendar_type})
                </h2>
            </div>
            <div style='background-color: #f7fafc; padding: 16px; border-radius: 4px;'>
                {response_html}
            </div>
            {deployments_result}
        </div>
        """
            
            return final_html
        
        print("🔗 Connecting to MCP server via SSE (SDK)...")
        async with sse_client(MCP_SSE_ENDPOINT) as (read, write):
            async with ClientSession(read, write) as session:
                print("🔄 Initializing MCP session...")
                await session.initialize()
                
                print("📋 Fetching available tools from MCP...")
                mcp_tools_response = await session.list_tools()
                mcp_tools_list = mcp_tools_response.tools
                
                print(f"✅ Got {len(mcp_tools_list)} tools from MCP")
                
                if not mcp_tools_list:
                    raise Exception("No tools available from MCP server")
                
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
                
                # Step 1: Ask Bedrock to analyze and select tools
                print("\n🧠 Step 1: Asking Bedrock to analyze question and select MCP tools...")
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
- For Jira searches: use jql parameter like 'text ~ "keywords"' or 'summary ~ "keywords"'
- For Confluence searches: use cql parameter
- For Datadog: use query parameter with metric name
- If question asks for explanations/information, prioritize Confluence tools
- If question is conversational, set needs_tools=false
- Be selective - only call truly relevant tools
- Extract specific search terms from the question

Return ONLY the JSON object."""

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
                tool_results = []
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
                
                # Step 3: Generate conversational response with Bedrock
                print("\n💬 Step 3: Generating conversational response with Bedrock...")
                
                # Helper function to extract key metrics from HTML results
                def summarize_tool_results(tool_results_dict: dict) -> str:
                    """
                    Extract only key information from tool results, removing HTML/CSS/JS.
                    Dramatically reduces token count while preserving critical data.
                    """
                    summary = []
                    
                    for tool_name, html_result in tool_results_dict.items():
                        # Skip if result is empty or error
                        if not html_result or 'error' in html_result.lower()[:100]:
                            summary.append(f"**{tool_name}**: No data or error")
                            continue
                        
                        # Extract service metrics using regex
                        services_found = []
                        
                        # Pattern 1: Extract service names from various formats
                        service_patterns = [
                            r'<span[^>]*>([a-z0-9-]+)</span>\s*<span[^>]*>#([a-z]+)',  # backend-service #env
                            r'🔹\s*([a-z0-9-]+)\s*#([a-z]+)',  # 🔹 service-name #production
                            r'([a-z0-9-]+)\s*#([a-z]+)',  # service-name #production
                        ]
                        
                        for pattern in service_patterns:
                            matches = re.findall(pattern, html_result, re.IGNORECASE)
                            for service, env in matches:
                                if service not in [s['name'] for s in services_found]:
                                    # Try to extract metrics for this service
                                    metrics = {}
                                    
                                    # Look for Requests
                                    req_match = re.search(rf'{re.escape(service)}.*?(\d+(?:\.\d+)?)\s*req/s', html_result, re.DOTALL | re.IGNORECASE)
                                    if req_match:
                                        metrics['requests'] = req_match.group(1)
                                    
                                    # Look for Errors
                                    err_match = re.search(rf'{re.escape(service)}.*?(\d+)\s*\(([^)]+)\)', html_result, re.DOTALL | re.IGNORECASE)
                                    if err_match:
                                        metrics['errors'] = f"{err_match.group(1)} ({err_match.group(2)})"
                                    
                                    # Look for Latency
                                    lat_match = re.search(rf'{re.escape(service)}.*?(\d+(?:\.\d+)?)\s*ms\s+avg', html_result, re.DOTALL | re.IGNORECASE)
                                    if lat_match:
                                        metrics['latency'] = f"{lat_match.group(1)}ms"
                                    
                                    services_found.append({
                                        'name': service,
                                        'env': env,
                                        'metrics': metrics
                                    })
                        
                        # Build summary for this tool
                        if services_found:
                            tool_summary = f"**{tool_name}**: {len(services_found)} services\n"
                            
                            # Only include services with potential issues (high errors or high latency)
                            problematic_services = []
                            healthy_count = 0
                            
                            for svc in services_found:
                                metrics = svc['metrics']
                                is_problematic = False
                                
                                # Check for high error rate
                                if 'errors' in metrics:
                                    err_text = metrics['errors']
                                    # Extract percentage
                                    pct_match = re.search(r'(\d+(?:\.\d+)?)\s*%', err_text)
                                    if pct_match and float(pct_match.group(1)) > 1.0:
                                        is_problematic = True
                                
                                # Check for high latency
                                if 'latency' in metrics:
                                    lat_text = metrics['latency']
                                    lat_match = re.search(r'(\d+)', lat_text)
                                    if lat_match and float(lat_match.group(1)) > 1000:  # > 1 second
                                        is_problematic = True
                                
                                if is_problematic:
                                    problematic_services.append(
                                        f"  - {svc['name']} ({svc['env']}): "
                                        f"Req: {metrics.get('requests', 'N/A')}, "
                                        f"Err: {metrics.get('errors', 'N/A')}, "
                                        f"Lat: {metrics.get('latency', 'N/A')}"
                                    )
                                else:
                                    healthy_count += 1
                            
                            if problematic_services:
                                tool_summary += f"  ⚠️ {len(problematic_services)} services with issues:\n"
                                tool_summary += "\n".join(problematic_services[:10])  # Limit to top 10
                                if len(problematic_services) > 10:
                                    tool_summary += f"\n  ... and {len(problematic_services) - 10} more"
                            
                            if healthy_count > 0:
                                tool_summary += f"\n  ✅ {healthy_count} services healthy"
                            
                            summary.append(tool_summary)
                        else:
                            # If no services extracted, provide a basic summary
                            # Count some common patterns
                            widget_count = html_result.count('<canvas')
                            if widget_count > 0:
                                summary.append(f"**{tool_name}**: Dashboard with ~{widget_count} metric widgets")
                            else:
                                # Truncate HTML to first 500 chars
                                truncated = re.sub(r'<[^>]+>', '', html_result)[:500]
                                summary.append(f"**{tool_name}**: {truncated}...")
                    
                    return "\n\n".join(summary)
                
                # Build comprehensive context from both MCP and other tools
                all_context = ""
                
                # Add context from other tools (DD_Red_Metrics, DD_Search, etc.) if provided
                if context_from_other_tools:
                    print(f"📊 Including context from {len(context_from_other_tools)} other tool(s)")
                    
                    # Calculate original size
                    original_size = sum(len(str(v)) for v in context_from_other_tools.values())
                    print(f"   Original context size: {original_size:,} characters")
                    
                    # Summarize to reduce token count
                    summarized = summarize_tool_results(context_from_other_tools)
                    summarized_size = len(summarized)
                    
                    print(f"   Summarized context size: {summarized_size:,} characters (reduction: {100 * (1 - summarized_size/original_size):.1f}%)")
                    
                    all_context += "Additional data from monitoring tools:\n\n"
                    all_context += summarized + "\n\n"
                
                # Add context from MCP tools
                if tool_results:
                    all_context += "MCP Tool execution results:\n\n"
                    for tr in tool_results:
                        all_context += f"**{tr['tool']}** (called because: {tr['reason']}):\n{tr['result']}\n\n"
                
                if all_context:
                    # Check if this is a monitoring query (RED metrics, dashboards, etc.)
                    is_monitoring_query = any(keyword in all_context.lower() for keyword in 
                                            ['dd_red_metrics', 'dd_red_adt', 'dd_red_samsung', 'dd_red_metrics_us', 
                                             'services with issues', 'services healthy', 'req/s', 'latency'])
                    
                    if is_monitoring_query:
                        # For monitoring queries, generate a comprehensive dashboard view
                        response_prompt = f"""Analyze RED metrics monitoring data and create comprehensive HTML dashboard.

Question: "{question}"

MONITORING DATA:
{all_context}

TASK:
Generate a complete HTML dashboard with:
1. Hero header with overall summary
2. Dashboard overview cards (show ALL dashboards found with counts)
3. COMPLETE table of services with elevated metrics (ALL services with issues)
4. Summary of healthy services

HTML structure (USE THIS EXACT FORMAT):

<div style="font-family: 'Inter', sans-serif; padding: 20px;">

<!-- Hero Header -->
<div style="background: linear-gradient(135deg, #1e40af 0%, #3b82f6 50%, #60a5fa 100%); padding: 32px; border-radius: 16px; margin-bottom: 28px; box-shadow: 0 4px 20px rgba(59, 130, 246, 0.3);">
    <div style="font-size: 14px; color: rgba(255,255,255,0.9); margin-bottom: 8px;">🚀 RED METRICS — FULL PLATFORM OVERVIEW</div>
    <h1 style="color: white; font-size: 32px; margin: 0 0 12px 0; font-weight: 800;">RED Metrics: All Services Analysis</h1>
    <p style="color: rgba(255,255,255,0.9); margin: 0; font-size: 15px;">Arlo Platform • Production Environment • [Insert timestamp from data]</p>
</div>

<!-- Metrics Summary Cards -->
<div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 28px;">
    <div style="background: #dcfce7; padding: 24px; border-radius: 12px; border: 2px solid #22c55e;">
        <div style="font-size: 13px; color: #166534; font-weight: 700; margin-bottom: 8px;">✅ HEALTHY SERVICES</div>
        <div style="font-size: 42px; color: #14532d; font-weight: 900; line-height: 1;">[Count]</div>
        <div style="font-size: 12px; color: #166534; margin-top: 4px;">of [Total] total services</div>
    </div>
    <div style="background: #fef3c7; padding: 24px; border-radius: 12px; border: 2px solid #fbbf24;">
        <div style="font-size: 13px; color: #92400e; font-weight: 700; margin-bottom: 8px;">⚠️ SERVICES WITH ISSUES</div>
        <div style="font-size: 42px; color: #78350f; font-weight: 900; line-height: 1;">[Count]</div>
        <div style="font-size: 12px; color: #92400e; margin-top: 4px;">elevated latency / errors</div>
    </div>
    <div style="background: #fecaca; padding: 24px; border-radius: 12px; border: 2px solid #ef4444;">
        <div style="font-size: 13px; color: #991b1b; font-weight: 700; margin-bottom: 8px;">🔴 ACTIVE ALERTS</div>
        <div style="font-size: 42px; color: #7f1d1d; font-weight: 900; line-height: 1;">[Count]</div>
        <div style="font-size: 12px; color: #991b1b; margin-top: 4px;">failing in Datadog right now</div>
    </div>
    <div style="background: #e0e7ff; padding: 24px; border-radius: 12px; border: 2px solid #6366f1;">
        <div style="font-size: 13px; color: #3730a3; font-weight: 700; margin-bottom: 8px;">📊 RED DASHBOARDS</div>
        <div style="font-size: 42px; color: #312e81; font-weight: 900; line-height: 1;">[Count]</div>
        <div style="font-size: 12px; color: #3730a3; margin-top: 4px;">ADT • US • Samsung • Z1 • more</div>
    </div>
</div>

<!-- Dashboard Coverage Section -->
<div style="background: white; padding: 24px; border-radius: 16px; border: 1px solid #e5e7eb; margin-bottom: 24px;">
    <h2 style="font-size: 20px; margin: 0 0 16px 0; color: #1e293b;">📊 RED Metric Dashboard Coverage</h2>
    <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px;">
        <!-- List ALL dashboards found in the data -->
        <div style="padding: 14px; background: #f1f5f9; border-radius: 8px; border-left: 4px solid #8b5cf6;">
            <div style="font-weight: 600; color: #6d28d9; margin-bottom: 4px;">🟣 RED Metrics - ADT</div>
            <div style="font-size: 13px; color: #64748b;">PP-Prod • cum-ivw-92c</div>
        </div>
        <!-- Add more dashboard cards for US, Samsung, etc. based on data -->
    </div>
</div>

<!-- Services with Elevated Metrics (COMPLETE TABLE - ALL 15 SERVICES) -->
<div style="background: white; padding: 24px; border-radius: 16px; border: 1px solid #e5e7eb; margin-bottom: 24px;">
    <h2 style="font-size: 20px; margin: 0 0 16px 0; color: #1e293b;">⚠️ Services with Elevated Metrics ([Count] Services)</h2>
    <table style="width: 100%; border-collapse: collapse;">
        <thead>
            <tr style="background: #f8fafc; border-bottom: 2px solid #e2e8f0;">
                <th style="padding: 12px; text-align: left; font-size: 13px; color: #475569;">Service</th>
                <th style="padding: 12px; text-align: right; font-size: 13px; color: #475569;">Req/min</th>
                <th style="padding: 12px; text-align: right; font-size: 13px; color: #475569;">Error Rate</th>
                <th style="padding: 12px; text-align: right; font-size: 13px; color: #475569;">Latency (ms)</th>
                <th style="padding: 12px; text-align: center; font-size: 13px; color: #475569;">Latency Status</th>
            </tr>
        </thead>
        <tbody>
            <!-- CRITICAL: LIST ALL SERVICES FROM THE DATA - NOT JUST A FEW! -->
            <!-- Example rows showing different severity levels: -->
            
            <!-- HIGH LATENCY (> 5000ms) - Use RED for truly critical -->
            <tr style="border-bottom: 1px solid #f1f5f9;">
                <td style="padding: 12px; font-weight: 600; color: #1e293b;">backend-hmsdeviceshadow</td>
                <td style="padding: 12px; text-align: right; color: #0ea5e9;">4,742.7</td>
                <td style="padding: 12px; text-align: right; color: #10b981;"><span style="background: #dcfce7; padding: 4px 8px; border-radius: 6px; font-weight: 600;">0%</span></td>
                <td style="padding: 12px; text-align: right; color: #dc2626; font-weight: 700;">6,751.4</td>
                <td style="padding: 12px; text-align: center;"><span style="background: #fecaca; color: #991b1b; padding: 6px 12px; border-radius: 8px; font-weight: 700; font-size: 12px;">🔴 CRITICAL</span></td>
            </tr>
            
            <!-- ELEVATED LATENCY (1000-5000ms) - Use YELLOW/ORANGE for anomalies -->
            <tr style="border-bottom: 1px solid #f1f5f9;">
                <td style="padding: 12px; font-weight: 600; color: #1e293b;">backend-hmspayment</td>
                <td style="padding: 12px; text-align: right; color: #0ea5e9;">100.9</td>
                <td style="padding: 12px; text-align: right; color: #10b981;"><span style="background: #dcfce7; padding: 4px 8px; border-radius: 6px; font-weight: 600;">< 0.1%</span></td>
                <td style="padding: 12px; text-align: right; color: #d97706; font-weight: 700;">2,521.4</td>
                <td style="padding: 12px; text-align: center;"><span style="background: #fef3c7; color: #92400e; padding: 6px 12px; border-radius: 8px; font-weight: 700; font-size: 12px;">⚠️ HIGH LAT</span></td>
            </tr>
            
            <!-- Continue for ALL services in the data -->
        </tbody>
    </table>
</div>

</div>

CRITICAL REQUIREMENTS:
1. Fill in ALL placeholders with actual data from the monitoring summary
2. In the "Services with Elevated Metrics" table, include EVERY SINGLE service that has issues
3. DO NOT truncate the table - show ALL services mentioned in the data
4. Use the exact HTML structure above
5. NO Jira table (Python will add it if needed)
6. NO Tools Executed section
7. Return ONLY the HTML (start with <div, end with </div>)

LATENCY SEVERITY COLOR GUIDELINES:
- **🟢 HEALTHY** (< 1000ms): No row needed, count as healthy
- **🟡 HIGH LAT** (1000-5000ms): Use YELLOW/ORANGE colors
  - Background: #fef3c7 (light yellow)
  - Text: #92400e (dark orange)
  - Badge: "⚠️ HIGH LAT"
- **🔴 CRITICAL** (> 5000ms): Use RED colors only for truly critical
  - Background: #fecaca (light red)
  - Text: #991b1b (dark red)
  - Badge: "🔴 CRITICAL"

For Error Rate:
- < 0.1%: Green badge
- 0.1% - 1%: Yellow badge  
- > 1%: Orange badge
- > 5%: Red badge (critical)

PAGERDUTY ANALYSIS REQUIREMENTS:
- If NO active incidents (triggered/acknowledged), check "Recently Resolved (Last 24 hours)"
- Analyze patterns: If multiple incidents resolved in last 24 hours, flag as potential instability
- Recurring issues: If same service had 3+ incidents in 7 days, highlight as recurring pattern
- Include in Key Findings: Mention recently resolved incidents and any patterns detected
- Example: "No active PagerDuty alerts, but 5 incidents were resolved in the last 24 hours for hmspayment - potential recurring issue"

EXCLUSIONS - DO NOT REPORT AS PROBLEMS:
- Different versions across environments: It is NORMAL for services to have different versions in dev/qa/production
- DO NOT flag version differences as an issue, anomaly, or finding
- Only mention versions if specifically asked or if there is a deployment-related incident"""
                    else:
                        # For non-monitoring queries (Jira-focused), use the summary format
                        response_prompt = f"""Analyze service health data and create visual HTML report summary.

Question: "{question}"

DATA FROM TOOLS:
{all_context}

TASK:
Generate HTML report with:
1. Hero header (service name, gradient background)
2. Status metrics cards (count of tickets by status)
3. Key findings (3 most important insights)

IMPORTANT:
- DO NOT generate Jira tickets table (will be added separately)
- DO NOT generate Recommendations section (will be added separately)
- NO "Tools Executed" section
- Return ONLY HTML for the summary/analysis
- Focus on insights, NOT listing all tickets

PAGERDUTY ANALYSIS:
- Always check for recently resolved incidents (last 24 hours) even if no active alerts
- Flag recurring patterns: If 3+ incidents for same service in 7 days, mention as potential instability
- Include in Key Findings: Mention recently resolved incidents and any patterns detected

EXCLUSIONS - DO NOT REPORT AS PROBLEMS:
- Different versions across environments: It is NORMAL for services to have different versions in dev/qa/production
- DO NOT flag version differences as an issue or finding
- Only mention versions if specifically asked or if there is a deployment-related incident

HTML structure for summary:

<div style="font-family: 'Inter', sans-serif; padding: 20px;">

<!-- Hero Header -->
<div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 50%, #f093fb 100%); padding: 32px; border-radius: 16px; margin-bottom: 28px;">
    <h1 style="color: white; font-size: 28px; margin: 0;">🚀 Service Health: backend-hmsguard</h1>
</div>

<!-- Status Metrics Grid -->
<div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 28px;">
    <div style="background: #fef3c7; padding: 20px; border-radius: 12px; border: 2px solid #fbbf24;">
        <div style="font-size: 12px; color: #92400e; font-weight: 700;">⚠️ OPEN</div>
        <div style="font-size: 36px; color: #78350f; font-weight: 900;">15</div>
    </div>
    <div style="background: #dbeafe; padding: 20px; border-radius: 12px; border: 2px solid #3b82f6;">
        <div style="font-size: 12px; color: #1e40af; font-weight: 700;">🔧 IN PROGRESS</div>
        <div style="font-size: 36px; color: #1e3a8a; font-weight: 900;">8</div>
    </div>
    <div style="background: #dcfce7; padding: 20px; border-radius: 12px; border: 2px solid #22c55e;">
        <div style="font-size: 12px; color: #166534; font-weight: 700;">✅ RESOLVED</div>
        <div style="font-size: 36px; color: #14532d; font-weight: 900;">2</div>
    </div>
</div>

<!-- Key Findings -->
<div style="background: white; padding: 28px; border-radius: 16px; border: 1px solid #e5e7eb;">
    <h2 style="font-size: 22px; margin: 0 0 20px 0;">🔍 Key Findings</h2>
    <div style="padding: 18px; background: #fef2f2; border-radius: 10px; border-left: 5px solid #ef4444; margin-bottom: 12px;">
        <div style="font-weight: 700; color: #991b1b; margin-bottom: 6px;">⚠️ Critical Issue</div>
        <div style="color: #64748b;">Describe most critical issue from data</div>
    </div>
    <div style="padding: 18px; background: #fffbeb; border-radius: 10px; border-left: 5px solid #f59e0b; margin-bottom: 12px;">
        <div style="font-weight: 700; color: #92400e; margin-bottom: 6px;">📊 Active Work</div>
        <div style="color: #64748b;">Summarize ongoing work/tickets</div>
    </div>
    <div style="padding: 18px; background: #f0fdf4; border-radius: 10px; border-left: 5px solid #22c55e;">
        <div style="font-weight: 700; color: #166534; margin-bottom: 6px;">✅ Positive Signals</div>
        <div style="color: #64748b;">Any good news or healthy metrics</div>
    </div>
</div>

</div>

CRITICAL RULES:
- Return ONLY the HTML above (hero + metrics + findings)
- NO Jira table (Python will generate it)
- NO Recommendations (Python will add them)
- NO "Tools Executed" section
- Start with <div and end with </div>"""
                
                else:
                    # No tools needed - direct answer (simplified format)
                    response_prompt = f"""You are GocBedrock, an AI assistant for Arlo infrastructure.

User question: "{question}"

This is an informational question. Use this EXACT HTML structure - BE VISUAL:

<div style="font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; max-width: 100%;">
    
    <!-- Hero Header -->
    <div style="background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #d946ef 100%); padding: 28px 24px; border-radius: 12px; margin-bottom: 28px; box-shadow: 0 12px 48px rgba(139, 92, 246, 0.3);">
        <div style="display: flex; align-items: center; gap: 14px;">
            <span style="font-size: 36px;">💬</span>
            <h1 style="margin: 0; color: white; font-size: 26px; font-weight: 800; letter-spacing: -0.8px;">Information & Guidance</h1>
        </div>
    </div>
    
    <!-- Main Content Card - VISUAL -->
    <div style="background: white; padding: 26px; border-radius: 12px; border: 1px solid #e5e7eb; box-shadow: 0 6px 20px rgba(0,0,0,0.1);">
        <div style="color: #334155; font-size: 15px; line-height: 1.9; letter-spacing: 0.01em;">
            [Explanation with icons, visual elements, short paragraphs. Use <div> cards with emojis for key points]
        </div>
    </div>
    
</div>

RULES:
- Use emojis and icons generously
- Break into short visual cards with emoji icons
- Use <strong> for key terms
- Include examples in visual boxes
- Be concise - max 2-3 sentences per point

Return ONLY the HTML (no markdown blocks)."""
                
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
                for tr in tool_results:
                    if tr['tool'] == 'jira_search':
                        result_text = tr['result']
                        print(f"🎫 Extracting Jira tickets from jira_search result ({len(result_text)} chars)")
                        print(f"📄 First 500 chars: {result_text[:500]}")
                        
                        # MARKDOWN TABLE FORMAT (most common from MCP)
                        # | GOC-10809 | Alert Modification for MAR 2026 | In Progress | Amisha Kabra |
                        markdown_pattern = r'\|\s*([A-Z]+-\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|'
                        markdown_matches = list(re.finditer(markdown_pattern, result_text, re.MULTILINE))
                        
                        print(f"🔍 Found {len(markdown_matches)} markdown table rows")
                        
                        if markdown_matches:
                            # Skip header row (usually first match)
                            for match in markdown_matches[1:]:  # Skip first row (headers)
                                ticket_id = match.group(1).strip()
                                summary = match.group(2).strip()
                                status = match.group(3).strip()
                                assignee = match.group(4).strip()
                                
                                # Skip separator rows (---)
                                if '---' in ticket_id or 'Key' in ticket_id:
                                    continue
                                
                                jira_tickets.append({
                                    'id': ticket_id,
                                    'summary': summary,
                                    'status': status if status else 'Unknown',
                                    'assignee': assignee if assignee else 'Unassigned'
                                })
                                print(f"  ✓ {ticket_id}: {summary[:60]}... [{status}]")
                        
                        else:
                            # FALLBACK: Try other formats
                            # Pattern 1: Standard format "Key: XXX-123"
                            pattern1 = r'(?:Key|ID|Ticket):\s*([A-Z]+-\d+)[\s\S]{0,500}?Summary:\s*([^\n]+)'
                            # Pattern 2: Just ticket ID at start of line
                            pattern2 = r'^([A-Z]+-\d+)\s*[-:]\s*([^\n]+)'
                            
                            all_matches = []
                            all_matches.extend(list(re.finditer(pattern1, result_text, re.IGNORECASE | re.MULTILINE)))
                            all_matches.extend(list(re.finditer(pattern2, result_text, re.MULTILINE)))
                            
                            print(f"🔍 Fallback: Found {len(all_matches)} non-markdown matches")
                            
                            seen_ids = set()
                            for match in all_matches:
                                ticket_id = match.group(1).strip()
                                if ticket_id in seen_ids:
                                    continue
                                seen_ids.add(ticket_id)
                                
                                summary = match.group(2).strip() if len(match.groups()) >= 2 else 'No summary'
                                
                                # Extract status and assignee from context
                                ticket_context = result_text[match.start():match.start()+400]
                                status_match = re.search(r'Status:\s*([^\n]+)', ticket_context, re.IGNORECASE)
                                assignee_match = re.search(r'Assignee:\s*([^\n]+)', ticket_context, re.IGNORECASE)
                                
                                jira_tickets.append({
                                    'id': ticket_id,
                                    'summary': summary,
                                    'status': status_match.group(1).strip() if status_match else 'Unknown',
                                    'assignee': assignee_match.group(1).strip() if assignee_match else 'Unassigned'
                                })
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
                <td style='padding: 14px;'><a href='https://verisure.atlassian.net/browse/{ticket['id']}' target='_blank' style='color: #6366f1; font-weight: 700; text-decoration: none;'>{ticket['id']}</a></td>
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
    print(f"🌐 MCP Server: {MCP_SERVER_URL}")
    
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
        mcp_client = SimpleMCPClient(MCP_SERVER_URL)
        
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
- For Datadog: use query parameter with metric name
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
                context += f"**{tr['tool']}** (called because: {tr['reason']}):\n{tr['result']}\n\n"
            
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
