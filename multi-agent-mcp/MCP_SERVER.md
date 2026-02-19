# OneView GOC AI - MCP Server

This application now functions as both an **MCP Client** and **MCP Server**, allowing other applications to consume its integrated tools.

## 🎯 What is MCP?

MCP (Model Context Protocol) is a standardized protocol that allows AI applications to expose and consume tools/functions. Think of it as an API standard for AI assistants.

## 🚀 Features as MCP Server

OneView GOC AI exposes **15 integrated tools** via MCP:

### Monitoring & Observability
- `datadog_red_metrics` - Get RED metrics (Rate, Errors, Duration)
- `datadog_red_adt` - ADT-specific RED metrics
- `datadog_errors` - Services with errors > 0
- `datadog_failed_pods` - Kubernetes pod failures
- `datadog_403_errors` - 403 Forbidden errors monitoring
- `splunk_p0_streaming` - P0 Streaming dashboard
- `splunk_p0_cvr` - P0 CVR dashboard
- `splunk_p0_adt` - P0 ADT dashboard

### Incident Management
- `pagerduty_incidents` - Active/recent incidents
- `pagerduty_analytics` - Analytics with charts
- `pagerduty_insights` - Incident trends and insights

### Documentation & Operations
- `wiki_search` - Search Confluence documentation
- `service_owners` - Find service owners/teams
- `arlo_versions` - Service version information
- `oncall_schedule` - Current on-call schedule

## 📡 MCP Endpoints

### Information Endpoint
```bash
GET http://localhost:8080/mcp/info
```

Returns server metadata and available tools.

### SSE Endpoint (MCP Transport)
```bash
GET/POST http://localhost:8080/mcp/sse
```

Server-Sent Events endpoint for MCP protocol communication.

## 🔧 How to Use with Claude Desktop

1. **Locate Claude Desktop config file:**
   - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - Windows: `%APPDATA%\Claude\claude_desktop_config.json`

2. **Add OneView GOC AI server:**
```json
{
  "mcpServers": {
    "oneview-goc-ai": {
      "url": "http://localhost:8080/mcp/sse",
      "transport": "sse"
    }
  }
}
```

3. **Restart Claude Desktop**

4. **Use the tools:**
```
Ask Claude: "Use oneview-goc-ai to get datadog red metrics for streaming-service"
```

## 🔧 How to Use with Cursor

1. **Open Cursor Settings** (Cmd+,)

2. **Go to Features > Model Context Protocol**

3. **Add server:**
```json
{
  "name": "OneView GOC AI",
  "url": "http://localhost:8080/mcp/sse",
  "transport": "sse"
}
```

4. **Use in Cursor:** The tools will be available in your AI assistant context.

## 📋 Testing the MCP Server

### Test 1: Get Server Info
```bash
curl http://localhost:8080/mcp/info | python -m json.tool
```

Expected output:
```json
{
  "name": "oneview-goc-ai",
  "version": "3.0.0",
  "total_tools": 15,
  "tools": [...]
}
```

### Test 2: Test with MCP Inspector
```bash
# Install MCP Inspector
npx @modelcontextprotocol/inspector http://localhost:8080/mcp/sse
```

This opens a web UI to test all MCP tools interactively.

## 🔒 Security Notes

- The MCP server runs on localhost:8080 by default
- For production, add authentication/authorization
- Consider rate limiting for MCP endpoints
- Credentials are loaded from environment variables

## 🐛 Troubleshooting

### Server doesn't start
- Check logs: `tail -f agent_tool_logs.log`
- Verify dependencies: `pip install -r requirements.txt`
- Check port availability: `lsof -i :8080`

### Tools return errors
- Verify environment variables are set (.env file)
- Check API credentials (Datadog, PagerDuty, Jira)
- Review tool-specific logs

### Claude Desktop can't connect
- Ensure OneView is running: http://localhost:8080
- Test `/mcp/info` endpoint manually
- Check Claude config file syntax
- Restart Claude Desktop after config changes

## 📚 Architecture

```
┌─────────────────────────────────────┐
│   Claude Desktop / Cursor / etc.    │
│         (MCP Clients)               │
└──────────────┬──────────────────────┘
               │ MCP Protocol (SSE)
               │
┌──────────────▼──────────────────────┐
│      OneView GOC AI Server          │
│  ┌───────────────────────────────┐  │
│  │   MCP Server (mcp_server.py)  │  │
│  │   - 15 Tools Exposed          │  │
│  └───────────────────────────────┘  │
│  ┌───────────────────────────────┐  │
│  │   MCP Client (ask_arlochat)   │  │
│  │   - 73+ Tools from ArloChat   │  │
│  └───────────────────────────────┘  │
│  ┌───────────────────────────────┐  │
│  │   Flask Web UI                │  │
│  │   - Interactive Dashboard     │  │
│  └───────────────────────────────┘  │
└─────────────────────────────────────┘
```

## 🎉 What Makes This Special

Your OneView GOC AI is now a **bidirectional MCP hub**:

1. **As MCP Client**: Consumes 73+ tools from ArloChat MCP
2. **As MCP Server**: Exposes 15 integrated tools to other AI assistants
3. **As Web UI**: Provides human-friendly interface with monitoring widgets

This makes it a powerful bridge between:
- Human operators (via Web UI)
- AI assistants (via MCP Server)
- External systems (Datadog, PagerDuty, Jira, Splunk, Confluence)

## 📖 Next Steps

1. Install dependencies: `pip install -r requirements.txt`
2. Test server info: `curl http://localhost:8080/mcp/info`
3. Configure Claude Desktop with the server
4. Start using your tools from Claude!

---

**Questions?** Check the logs or test the `/mcp/info` endpoint for debugging.
