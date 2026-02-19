# 🧠 OneView GOC AI - Multi-Agent Operations Dashboard

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Flask](https://img.shields.io/badge/Flask-3.1+-green.svg)](https://flask.palletsprojects.com/)
[![Docker](https://img.shields.io/badge/Docker-Ready-brightgreen.svg)](https://www.docker.com/)
[![Datadog](https://img.shields.io/badge/Datadog-Integration-purple.svg)](https://www.datadoghq.com/)
[![License](https://img.shields.io/badge/License-Proprietary-red.svg)]()

OneView GOC AI is a comprehensive web-based platform that integrates **real-time monitoring**, **documentation search**, and **AI-powered recommendations**. Designed for DevOps, SRE, and support teams, it streamlines troubleshooting workflows by combining multiple data sources and AI tools into a single intelligent interface.

**Key Highlights:** 
- 🚨 **PagerDuty Integration**: Full incident management with 3 specialized tools (incidents list, analytics dashboard, insights & trends)
- 📊 **Interactive Datadog**: Real-time metrics visualization with Chart.js powered charts
- 🔄 **Auto-Refresh Monitors**: Both Arlo Status and PagerDuty Status update every 3 minutes
- 🎯 **Smart Layout**: Two-column main area with centered branding and prominent status cards

## 🆕 What's New in v3.0

### 🌐 MCP Server Capability (NEW!)
- ✅ **Bidirectional MCP Hub**: Functions as both MCP Client AND MCP Server
- ✅ **15 Tools Exposed**: All integrated tools available via MCP protocol
- ✅ **SSE Transport**: Server-Sent Events for real-time communication
- ✅ **stdio Mode**: Alternative transport for Claude Desktop integration
- ✅ **Standard Protocol**: Compatible with Claude Desktop, Cursor, and any MCP client
- ✅ **Full Documentation**: Step-by-step guides for integration

### PagerDuty Integration Suite
- ✅ **3 PagerDuty Tools**: Incidents list, Analytics dashboard, Insights & trends
- ✅ **Real-Time Status Card**: Auto-refresh monitor in main area (next to "How to use")
- ✅ **Full Pagination**: Fetches ALL incidents (up to 1000) for accurate counts
- ✅ **Clickable Incidents**: Direct links to PagerDuty from status card
- ✅ **Visual Traffic Light**: Labeled status indicators (Triggered, Acknowledged, Resolved)
- ✅ **Alert Indicators**: Visual 🚨 alerts for triggered/acknowledged incidents

### UI/UX Improvements
- ✅ **Centered Branding**: "OneView GOC AI" prominently centered in header
- ✅ **Compact History**: Collapsible history section with arrow toggle
- ✅ **Next Deployments**: 24-hour deployment calendar with LIVE indicator
- ✅ **Two-Column Layout**: Status services displayed in efficient grid
- ✅ **Unified Colors**: Consistent teal/green theme across all section titles
- ✅ **Service Alerts**: Orange/red/yellow indicators for service status

### Performance & Features
- ✅ **API Pagination**: PagerDuty monitor fetches complete data sets
- ✅ **Custom Scrollbars**: Purple-themed for PagerDuty, consistent styling
- ✅ **Hover Effects**: Visual feedback on all interactive elements
- ✅ **Smart Search**: History search shows all matches, not limited to 3
- ✅ **Timezone Aware**: CST-based deployment scheduling with local time display

---

## 📋 Table of Contents
- [What's New in v2.0](#-whats-new-in-v20)
- [What Does This Project Do?](#-what-does-this-project-do)
- [Key Features](#-key-features)
- [How It Works](#-how-it-works)
- [Technologies Used](#-technologies-used)
- [Installation](#-installation)
- [Configuration](#️-configuration)
- [How to Use](#-how-to-use)
- [Project Structure](#-project-structure)
- [UI/UX Features](#-uiux-features)
- [Interface Layout](#-interface-layout)
- [Troubleshooting](#-troubleshooting)
- [Performance](#-performance)
- [Security](#-security)
- [Additional Documentation](#-additional-documentation)

---

## 🎯 What Does This Project Do?

OneView GOC AI serves as a **centralized operations hub** that:

1. **Real-Time Monitoring**: Connects to Datadog to display live service metrics (requests, errors, latency) with interactive charts
2. **Intelligent Search**: Searches through Confluence documentation, service versions, and knowledge bases
3. **Service Discovery**: Identifies service owners, on-call engineers, and system status
4. **AI Assistance**: Provides troubleshooting recommendations using LLaMA 3 and Google Gemini
5. **Error Detection**: Automatically identifies and highlights services experiencing errors
6. **🆕 MCP Server**: Exposes all integrated tools via Model Context Protocol for consumption by Claude Desktop, Cursor, and other AI assistants

## 🚀 Key Features

### 📊 Monitoring & Metrics

#### Automatic Status Monitors

##### Arlo Status (Sidebar)
- **Real-time monitoring**: Updates every 3 minutes automatically
- **Arlo Status Overview**: Shows system-wide operational status
- **Core Services**: Displays ALL main services (Log In, Notifications, Library, Live Streaming, Video Recording, Arlo Store, Community) with visual indicators (✅/⚠️) - no scrolling required
- **Past Incidents**: Shows last 7 incidents from status.arlo.com (scrollable)
- **Single column layout**: Easy to read vertical list
- **Always visible**: Permanently displayed in sidebar

##### PagerDuty Status (Main Area)
- **Real-time monitoring**: Updates every 3 minutes automatically
- **Smart positioning**: Located next to "How to use" section in main area
- **Status summary card**: Visual traffic light display with counts
  - 🔴 Triggered incidents (real count via pagination)
  - 🟡 Acknowledged incidents (real count)
  - 🟢 Resolved incidents (real count - last 7 days)
- **Active incidents**: Top 5 most recent triggered/acknowledged
- **Recently resolved**: Last 5 resolved incidents
- **Clickable incidents**: Click any incident to open in PagerDuty
- **Two-column layout**: Active | Resolved for easy comparison
- **Full pagination**: Fetches ALL incidents (up to 1000) for accurate counts
- **Custom scrollbar**: Purple-themed for better aesthetics

#### Datadog Integration
- **DD_Red_Metrics**: 
  - Displays RED metrics (Requests, Errors, Latency) for all services
  - Interactive bar charts with Chart.js visualization
  - Real-time data from Datadog API
  - Filter by service name
  - Configurable time ranges (1, 2, 4 hours, 2 days, 1 week)
  - Shows Average, Minimum, and Maximum latency percentiles
  - 3-column grid layout for efficient space usage
  - Direct links to Datadog service pages

- **DD_Red_ADT**: 
  - Shows RED Metrics from ADT dashboard
  - Same comprehensive metrics as DD_Red_Metrics
  - Alternative dashboard view

- **DD_Errors**: 
  - Filters and displays ONLY services with active errors
  - Shows error count and percentage
  - Combines data from both RED Metrics and ADT dashboards
  - Quick error triage and investigation

#### Splunk Integration
- **P0_Streaming**: 
  - Shows P0 Streaming dashboard from Splunk
  - Displays streaming services metrics and status
  - Filter by service name
  - Configurable time ranges
  - Direct link to Splunk dashboard

#### PagerDuty Integration (3 Tools Available)

##### 1. PagerDuty (Incidents List)
- **Comprehensive incident table**: Full list with pagination
- **All statuses**: Triggered, acknowledged, and resolved incidents
- **Smart filtering**: By service name or incident number
- **Time range**: Last 7 days of incidents
- **Direct links**: Clickable incident numbers open in PagerDuty
- **Color-coded rows**: Visual hierarchy by status
  - 🔴 Red background for triggered
  - 🟡 Yellow background for acknowledged
  - 🟢 Green background for resolved
- **Detailed columns**: Status, #, Title, Service, Urgency, Created, Assigned To
- **Summary statistics**: Shows count by status at top

##### 2. PagerDuty_Dashboards (Analytics View)
- **Analytics dashboard** with interactive Chart.js visualizations
- **Overview metrics card**: Total, triggered, acknowledged, resolved (gradient design)
- **Three interactive charts**:
  - Incidents by Status (Donut Chart with percentages)
  - Incidents by Urgency (Donut Chart with percentages)
  - Top 10 Services by Incident Count (Horizontal Bar Chart)
- **Time range**: Last 30 days of data
- **Real-time rendering**: Dynamic chart generation
- **Responsive design**: Grid layout adapts to screen size

##### 3. PagerDuty_Insights (Advanced Analytics)
- **Key insights card**: Total incidents, avg resolution time, busiest day/hour (purple/pink gradient)
- **Pattern analysis charts**:
  - Incidents by Day of Week (Bar Chart)
  - Incidents by Hour of Day (Line Chart with area fill)
- **Top 5 users ranking**: Medal system (🥇🥈🥉) for incident assignments
- **Resolution time metrics**: P50 (Median), P90, P95 percentiles
- **Top service highlight**: Service with most incidents (callout box)
- **Time range**: Last 30 days
- **Trend identification**: Helps identify incident patterns

##### PagerDuty Status Card (Main Area - Auto-Refresh)
- **Always visible**: Located next to "How to use" section
- **Real-time counters**: Shows true count via full pagination
- **Visual traffic light**: 3-column grid with labeled counts
- **Quick access lists**: Top 5 active and top 5 resolved
- **Clickable incidents**: Direct links to PagerDuty
- **Auto-refresh**: Every 3 minutes
- **Manual refresh**: Button available

### 🔍 Documentation & Knowledge
- **Wiki**: Search Arlo Confluence documentation with intelligent ranking
- **Ask_ARLOCHAT**: Interact with Arlo's Slack chat system for questions

### 📦 Service Management
- **Arlo_Versions**: Check service versions across all environments with search capabilities
- **Owners**: Identify service ownership and responsibilities
- **Holiday_Oncall**: Check current on-call engineers, holidays, and escalation paths

### 🤖 AI-Powered Tools
- **Suggestions**: AI-powered troubleshooting recommendations using LLaMA 3
- **Ask_Gemini**: Google Gemini integration for general queries

### 🌐 MCP Server (NEW in v3.0)

OneView GOC AI now functions as a **full MCP (Model Context Protocol) Server**, exposing all integrated tools for consumption by other AI assistants:

#### Available as MCP Server
- **15 Integrated Tools**: All monitoring and operations tools exposed via MCP
- **SSE Transport**: Real-time communication using Server-Sent Events
- **stdio Mode**: Alternative transport for direct integration
- **Compatible Clients**: Works with Claude Desktop, Cursor, and any MCP-compatible client

#### MCP Endpoints
- `GET /mcp/info` - Server metadata and tool listing
- `GET/POST /mcp/sse` - MCP protocol endpoint (SSE transport)

#### Quick Start with Claude Desktop
1. Copy `claude_desktop_config.json` to your Claude config directory
2. Restart Claude Desktop
3. All OneView tools available in Claude!

**See [MCP_SERVER.md](MCP_SERVER.md) for complete setup instructions.**

## 🔧 How It Works

### Architecture Overview
```
┌─────────────────────────────────────────────────────────────┐
│              AI Assistants (MCP Clients)                     │
│         Claude Desktop / Cursor / etc.                       │
└───────────────────────┬─────────────────────────────────────┘
                        │ MCP Protocol (SSE/stdio)
┌───────────────────────▼─────────────────────────────────────┐
│                 OneView GOC AI Server                        │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  MCP Server (exposes 15 tools)                         │ │
│  └────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  Flask Web UI (human interface)                        │ │
│  └────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  MCP Client (consumes ArloChat 73+ tools)              │ │
│  └────────────────────────────────────────────────────────┘ │
└───────────────────────┬─────────────────────────────────────┘
                        │
           ┌────────────┴───────────────┐
           │                            │
┌──────────▼───────┐        ┌──────────▼──────────┐
│  External APIs   │        │   ArloChat MCP      │
│  - Datadog       │        │   (73+ tools)       │
│  - PagerDuty     │        └─────────────────────┘
│  - Confluence    │
│  - Splunk        │
│  - Jira          │
└──────────────────┘

User Browser → Flask Web Server → Multiple Tool Modules → External APIs
                                                        ├─ Datadog API
                                                        ├─ PagerDuty API
                                                        ├─ Confluence API
                                                        ├─ ServiceNow API
                                                        ├─ LLaMA 3 (Ollama)
                                                        └─ Google Gemini
```

### Auto-Refresh Monitors (Background Updates)

#### Arlo Status Monitor (Sidebar)
1. **Auto-load**: Loads immediately on page load
2. **Scraping**: Fetches data from status.arlo.com
3. **Parsing**: Extracts summary, core services status, and past incidents
4. **Display**: Shows in sidebar with visual indicators
5. **Auto-refresh**: Updates every 3 minutes (180 seconds) automatically
6. **Visual Indicators**: 
   - ✅ Green checkmark for "All Good" services
   - ⚠️ Red warning for services with issues

#### PagerDuty Status Monitor (Main Area)
1. **Auto-load**: Loads immediately on page load
2. **API Pagination**: Fetches ALL incidents from last 7 days (up to 1000 max)
3. **Categorization**: Separates by status (triggered, acknowledged, resolved)
4. **Display**: Shows in main area next to "How to use" section
5. **Auto-refresh**: Updates every 3 minutes (180 seconds) automatically
6. **Features**:
   - Real incident counts (not limited to 100)
   - Top 5 active and top 5 resolved displayed
   - Clickable incidents open in PagerDuty
   - Color-coded borders by status

### Datadog Dashboard Flow
1. **User Input**: Select "DD_Red_Metrics" or "DD_Red_ADT" and optionally enter service name
2. **Time Range**: Select time range (auto-shown when Datadog tools selected)
3. **API Query**: Fetches dashboard data from Datadog
4. **Widget Filtering**: Filters widgets by service name (if provided)
5. **Data Collection**: For each service, queries:
   - Requests: `trace.servlet.request.hits` (as count)
   - Errors: `trace.servlet.request.errors` (as count + percentage)
   - Latency: `trace.servlet.request.duration` (avg, min, max in milliseconds)
6. **Visualization**: Generates interactive bar charts using Chart.js
7. **Display**: Shows widgets in 3-column grid with real-time metrics

### Time Range Selection
- Dynamically shown only when Datadog or Splunk tools are selected
- Options: 1 hour, 2 hours, 4 hours, 2 days, 1 week
- Affects data queries to Datadog/Splunk APIs
- Smart UI: Only appears when needed

### Error Detection (DD_Errors)
1. Queries all services from both RED Metrics and ADT dashboards
2. Filters services where `errors > 0`
3. Calculates error percentage: `(errors / requests) × 100`
4. Displays only services with active errors
5. Provides quick links to Datadog for investigation

## 🧰 Technologies Used

### Backend
- **Python 3.12+**: Core application language
- **Flask**: Web framework for HTTP server and API endpoints
- **FastMCP**: Modular execution engine for AI tools (optional)
- **Requests**: HTTP client for API integrations

### Frontend
- **HTML5 + CSS3**: Modern dark-themed UI
- **JavaScript (ES6+)**: Dynamic interactions and AJAX calls
- **Chart.js 4.4.0**: Interactive charts for metrics visualization
- **Responsive Grid Layout**: 3-column layout for optimal space usage

### Integrations
- **Datadog API**: Real-time metrics and dashboard data
  - Metrics Query API for time-series data
  - Dashboard API for widget metadata
- **Splunk API**: P0 Streaming dashboard and logs
- **Confluence API**: Documentation search and retrieval
- **Slack API**: Integration with ArloChat bot
- **Arlo Status Page**: Real-time system status monitoring
- **LLaMA 3 (via Ollama)**: Local AI model for troubleshooting
- **Google Gemini**: Cloud AI for general queries

### DevOps
- **Docker + Docker Compose**: Containerized deployment
- **Environment Variables**: Secure credential management
- **Git**: Version control and collaboration

### 🖥️ Web Interface
- **Dark theme** with gradient header
- **Sidebar** with:
  - "New Chat" button for quick resets
  - History of past searches (last 10 queries)
  - **Auto-refresh Status Monitor** (updates every 3 minutes)
    - System summary
    - Core services status with visual indicators
    - Last 7 past incidents
- **Main area** with:
  - Clear 3-step usage instructions
  - Tool selection checkboxes with improved naming
  - Smart time range selector (appears only when needed)
  - Input box for queries
  - Live execution timer
  - Results displayed in styled cards per tool
- **Smart history**: Shows tool names when no search query provided

## 📦 Installation

### Prerequisites
- Docker and Docker Compose (for containerized deployment)
- OR Python 3.12+ (for local installation)
- Datadog API and Application keys (for monitoring features)
- Splunk token (for P0 Streaming dashboard)
- Confluence credentials (for documentation search)
- Slack Bot Token (for ArloChat integration)

### Option 1: Docker (Recommended)

#### Quick Start
```bash
# 1. Clone the repository
git clone https://github.com/JorgeGilTV/LLM_ChatBot_Llama3.git
cd multi-agent-mcp

# 2. Configure environment variables
cp .env.example .env
# Edit .env with your credentials (see Configuration section below)

# 3. Start with Docker Compose
docker-compose up -d

# 4. Access the application
# Open http://localhost:8080 in your browser
```

### Using the helper script
```bash
./docker-run.sh start    # Start the application
./docker-run.sh logs     # View logs
./docker-run.sh stop     # Stop the application
./docker-run.sh restart  # Restart the application
```

For detailed Docker instructions, see [DOCKER_README.md](DOCKER_README.md)

## Option 2: Local Installation

### Prerequisites
- Python 3.12 or higher
- pip

### Steps
```bash
# 1. Clone the repository
git clone https://github.com/JorgeGilTV/LLM_ChatBot_Llama3.git
cd multi-agent-mcp

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set environment variables
cp .env.example .env
# Edit .env with your credentials

# 4. Start the server
python3 app.py
```

## 🧪 How to Use

### Basic Usage
1. **Access the application**: Open http://localhost:8080 in your browser
2. **Monitor real-time status**: 
   - **Sidebar (Arlo Status)**: Check Arlo system status (updates every 3 minutes)
   - **Main Area (PagerDuty Status)**: View active/resolved incidents (updates every 3 minutes)
   - Both monitors update automatically and have manual refresh buttons (🔄)
3. **Select tools**: Choose one or more tools from the checkbox list
4. **Configure options**: 
   - **Time Range** (for DD_Red_Metrics, DD_Red_ADT, DD_Errors): Select from dropdown (1h, 2h, 4h, 2d, 1w)
   - Auto-shows only when these tools are selected
5. **Enter query** (optional): Type your search (service name, keyword, etc.)
   - Some tools work without a query
   - PagerDuty tools can filter by service or incident number
6. **Execute**: Click "Send" button
7. **View results**: See formatted results with interactive charts
8. **Interact with PagerDuty**:
   - Click any incident in PagerDuty Status card to open in PagerDuty
   - Use PagerDuty checkboxes for detailed analysis
9. **Check history**: 
   - View last 3 searches in sidebar
   - Click "Show X more" to expand
   - Click previous queries to reload results
   - Use search box to filter history
10. **New search**: Click "New Chat" to reset

### Tool Names Reference

#### Monitoring & Metrics
- **DD_Red_Metrics**: Datadog RED Metrics dashboard with charts
- **DD_Red_ADT**: Datadog ADT dashboard with metrics
- **DD_Errors**: Services with errors only (filtered view)
- **P0_Streaming**: Splunk P0 Streaming dashboard

#### PagerDuty Tools
- **PagerDuty**: Incidents list with full details (last 7 days)
- **PagerDuty_Dashboards**: Analytics dashboard with interactive charts (last 30 days)
- **PagerDuty_Insights**: Advanced insights and trends analysis (last 30 days)

#### Documentation & Knowledge
- **Wiki**: Confluence documentation search
- **Arlo_Versions**: Service version checker across environments
- **Owners**: Service ownership and responsibility information
- **Holiday_Oncall**: On-call engineers, holidays, and escalation paths

#### AI & Support
- **Suggestions**: AI-powered troubleshooting recommendations
- **Ask_ARLOCHAT**: Slack bot integration for queries

### 📊 Using Monitoring Tools

#### View All Services (Datadog)
```
1. Check "DD_Red_Metrics"
2. Select time range (default: 4 hours)
3. Leave query empty
4. Click "Send"
→ Shows all services with metrics and charts
```

#### Filter by Service Name
```
1. Check "DD_Red_Metrics" or "P0_Streaming"
2. Select time range
3. Enter service name: "oauth" or "streaming-service"
4. Click "Send"
→ Shows only matching service widgets
```

#### Find Services with Errors
```
1. Check "DD_Errors"
2. Select time range
3. Leave query empty (or filter by service)
4. Click "Send"
→ Shows only services experiencing errors
→ Displays error count and percentage
```

#### Monitor System Status (Automatic)
```
Arlo Status (Sidebar):
- Look at the sidebar Status Monitor
- Updated automatically every 3 minutes
- Shows:
  - System operational status
  - ALL core services with ✅/⚠️ indicators (no scrolling)
  - Last 7 incidents (scrollable)
- No action required, always visible

PagerDuty Status (Main Area):
- Look at the PagerDuty card next to "How to use"
- Updated automatically every 3 minutes
- Shows:
  - Real counts: Triggered, Acknowledged, Resolved (last 7 days)
  - Top 5 active incidents
  - Last 5 resolved incidents
- Click any incident to open in PagerDuty
- No action required, always visible
```

### 🚨 Using PagerDuty Tools

#### View All Active Incidents (Detailed List)
```
1. Check "PagerDuty"
2. Leave query empty
3. Click "Send"
→ Shows comprehensive table with all incidents (last 7 days)
→ Organized by status: Triggered → Acknowledged → Resolved
→ Clickable links to PagerDuty
```

#### Filter PagerDuty by Service
```
1. Check "PagerDuty"
2. Enter service name: "streaming-service" or "backend-hmspayment"
3. Click "Send"
→ Shows only incidents related to that service
```

#### View Analytics Dashboard
```
1. Check "PagerDuty_Dashboards"
2. Click "Send" (no query needed)
→ Shows interactive charts:
  - Incidents by Status (Donut Chart)
  - Incidents by Urgency (Donut Chart)
  - Top 10 Services (Bar Chart)
→ Last 30 days of data
```

#### View Insights and Trends
```
1. Check "PagerDuty_Insights"
2. Click "Send" (no query needed)
→ Shows advanced analytics:
  - Average resolution time
  - Busiest day/hour patterns
  - Top 5 users by assignments
  - Resolution time percentiles (P50, P90, P95)
→ Last 30 days of data
```

### 📊 Understanding the Metrics

Each service widget displays:

- **Requests**: Total number of requests (bar chart with "Hits" label)
- **Errors**: Error count with percentage (e.g., "5 (< 0.1%)")
- **Latency**: 
  - Average, Minimum, Maximum latency in milliseconds
  - Multi-line bar chart showing all three metrics
  - Legend at bottom identifies each metric

Charts are:
- **Interactive**: Hover to see exact values
- **Time-based**: X-axis shows timestamps
- **Color-coded**: 
  - Blue = Requests
  - Red = Errors
  - Blue/Green/Orange = Latency (Avg/Min/Max)

### 🔍 Search Examples

#### Datadog Queries
- `oauth` - Show metrics for oauth service
- `backend-arlosafeapi` - Show specific backend service
- _(empty)_ - Show all services
- Use **Datadog_Errors** - Show only services with errors

#### Other Tools
- **Read_Versions**: 
  - `clientapi` - Find all clientapi services
  - `1.43.0` - Find services with specific version
  - _(empty)_ - Show all services across environments
  
- **Read_Confluence**: 
  - `SSL certificate` - Search wiki documentation
  - `deployment process` - Find process docs
  
- **How_to_fix**: 
  - `SSL error` - Get AI troubleshooting suggestions
  - `high latency` - Get performance recommendations

### 🛠️ Multi-Tool Queries

You can combine multiple tools:
```
✓ Datadog_Dashboards + Datadog_Errors
→ See all metrics + highlight error services

✓ Read_Confluence + How_to_fix
→ Search docs + get AI recommendations

✓ Service_Owners + Oncall_Support
→ Find who owns service + who's on call
```

## ⚙️ Configuration

### Environment Variables

Create a `.env` file in the root directory with the following variables:

#### Datadog Configuration (Required for Monitoring)
```bash
DATADOG_API_KEY=your_datadog_api_key_here
DATADOG_APP_KEY=your_datadog_application_key_here
DATADOG_SITE=datadoghq.com  # Or your custom Datadog subdomain
```

**How to get Datadog keys:**
1. Log into your Datadog account
2. Go to Organization Settings → API Keys
3. Copy your API Key
4. Go to Organization Settings → Application Keys
5. Create/copy your Application Key

#### Confluence Configuration
```bash
CONFLUENCE_URL=https://your-company.atlassian.net
ATLASSIAN_EMAIL=your-email@company.com
CONFLUENCE_TOKEN=your_confluence_token
```

#### Splunk Configuration
```bash
SPLUNK_HOST=arlo.splunkcloud.com
SPLUNK_TOKEN=your_splunk_token_here
```

**How to get Splunk token:**
1. Log in to your Splunk instance
2. Go to Settings → Tokens
3. Create a new token with appropriate permissions

#### PagerDuty Configuration
```bash
PAGERDUTY_API_TOKEN=your_pagerduty_api_token_here
```

**How to get PagerDuty token:**
1. Log in to your PagerDuty account
2. Go to User Settings → User API Tokens
3. Click "Create API User Token"
4. Give it a name (e.g., "OneView_GOC_AI")
5. Copy the token immediately (it won't be shown again)

📖 For detailed PagerDuty setup instructions, see [PAGERDUTY_SETUP.md](PAGERDUTY_SETUP.md)

#### Slack Configuration (for ArloChat)
```bash
SLACK_BOT_TOKEN=your_slack_bot_token_here
```

**How to get Slack token:**
1. Go to https://api.slack.com/apps
2. Select your app or create a new one
3. Go to OAuth & Permissions
4. Copy the Bot User OAuth Token

#### AI Models Configuration
```bash
OLLAMA_HOST=http://localhost:11434  # For local LLaMA 3
GEMINI_API_KEY=your_gemini_api_key  # For Google Gemini
```

### Configuration Files

- **`.env`**: Environment variables (not committed to git)
- **`.env.example`**: Template for environment variables
- **`pyproject.toml`**: Python dependencies and project metadata
- **`requirements.txt`**: Python package requirements
- **`docker-compose.yml`**: Docker orchestration configuration

## 📁 Project Structure

```
multi-agent-mcp/
├── app.py                      # Flask web server and API routes
├── pyproject.toml              # Python project configuration
├── requirements.txt            # Python dependencies
├── .env.example               # Environment variables template
├── Dockerfile                 # Docker image definition
├── docker-compose.yml         # Docker orchestration
├── docker-run.sh             # Docker helper script
│
├── templates/                 # HTML templates
│   ├── index.html            # Main chat interface
│   ├── about.html            # About page
│   ├── help.html             # Help documentation
│   └── settings.html         # Settings page
│
├── static/                    # Frontend assets
│   ├── css/
│   │   └── styles.css        # Dark theme styling
│   ├── js/
│   │   └── scripts.js        # Interactive functionality
│   └── search_history.json   # Search history storage
│
├── tools/                     # Backend tool modules
│   ├── datadog_dashboards.py # Datadog metrics & charts
│   ├── datadog_connect.py    # Datadog API connection
│   ├── pagerduty_tool.py     # PagerDuty incidents
│   ├── pagerduty_analytics.py# PagerDuty analytics & dashboards
│   ├── pagerduty_insights.py # PagerDuty insights & trends
│   ├── confluence_tool.py    # Confluence search
│   ├── read_versions.py      # Service version checker
│   ├── read_arlo_status.py   # System health monitor
│   ├── service_owners.py     # Service ownership
│   ├── oncall_support.py     # On-call information
│   ├── noc_kt.py            # NOC knowledge base
│   ├── ask_arlochat.py      # Arlo chat integration
│   ├── llama_tool.py        # LLaMA 3 AI integration
│   ├── gemini_tool.py       # Google Gemini integration
│   ├── suggestions_tool.py   # Contextual suggestions
│   ├── tickets_tool.py       # ServiceNow integration
│   └── history_tool.py       # Search history manager
│
├── chrome-extension/          # Browser extension (optional)
│   ├── manifest.json
│   ├── popup.html
│   └── popup.js
│
├── README.md                  # This documentation
├── QUICK_START.md            # Quick start guide
├── DOCKER_README.md          # Docker-specific guide
├── DATADOG_SETUP.md          # Datadog setup instructions
└── agent_tool_logs.log       # Application logs
```

## 🎨 UI/UX Features

### Modern Interface
- **Dual theme support**: Dark/Light theme toggle (🌓 button)
- **Centered branding**: "🧠 OneView GOC AI 🧠" prominently centered in header
- **Gradient header**: Teal-to-green gradient design
- **Two-column main layout**: "How to use" + PagerDuty Status side-by-side
- **Card-based results**: Each tool displays results in styled cards
- **Real-time feedback**: Live execution timer and loading indicators
- **Interactive charts**: Hover over Chart.js visualizations for exact values
- **Responsive layout**: Grid systems adapt to screen size
- **Color consistency**: Unified teal/green (#20d6ca) for all section titles

### Sidebar Features
- **Compact History**: Shows last 3 searches by default
  - "Show X more" button to expand
  - "Show less" to collapse
  - Search filter to find specific queries
  - Auto-collapses when cleared
- **Arlo Status Monitor**: Single-column vertical layout
  - All core services visible without scrolling
  - Past incidents scrollable (max 7)
  - Manual refresh button (🔄)
  - Auto-refresh every 3 minutes

### Main Area Features
- **PagerDuty Status Card**: Prominent real-time incident tracking
  - Three-column traffic light display with labels
  - Shows true counts via API pagination (up to 1000 incidents)
  - Two-column incident lists (Active | Resolved)
  - Clickable incidents open in new PagerDuty tab
  - Manual refresh button
  - Auto-refresh every 3 minutes
  - Purple/pink gradient summary card
  - Custom purple scrollbar

### User Experience
- **Multi-select tools**: Run multiple tools simultaneously
- **Smart time range**: Auto-shows for Datadog queries
- **Intelligent history**: Compact by default, expandable on demand
- **Direct links**: Quick access to Datadog, PagerDuty, Confluence, etc.
- **Error highlighting**: Services with errors shown in red
- **New Chat button**: Quick reset for new searches
- **Visual feedback**: Hover effects, transitions, and click states
- **Tooltips**: Full text on hover for truncated items

## 🐛 Troubleshooting

### Common Issues

**Charts not displaying:**
```bash
# Clear browser cache (Safari: Cmd+Shift+R)
# Verify Chart.js is loaded in browser console
# Check browser console for JavaScript errors
```

**Datadog connection errors:**
```bash
# Verify API keys in .env file
# Check DATADOG_SITE is correct (datadoghq.com or custom subdomain)
# Test API keys: curl -H "DD-API-KEY: your_key" https://api.datadoghq.com/api/v1/validate
```

**No data showing:**
```bash
# Check service name spelling
# Verify time range (some services may have no data in selected range)
# Check Datadog dashboard exists: "RED - Metrics"
```

**Port already in use:**
```bash
# Change port in app.py: flask_app.run(port=5002)
# Or kill process: lsof -ti:8080 | xargs kill -9
```

## 📈 Performance

- **Initial load**: ~3-5 seconds for 30 services
- **Full dashboard**: ~15-30 seconds for all services (depends on service count)
- **Chart rendering**: Client-side using Chart.js (instant)
- **API caching**: Datadog responses cached for faster repeated queries
- **Concurrent queries**: Multiple metrics fetched in parallel

## 🔒 Security

- **Environment variables**: All credentials stored in `.env` (not committed)
- **API key validation**: Keys validated before queries
- **HTTPS support**: Can be configured with reverse proxy
- **No data storage**: No persistent storage of sensitive data
- **Docker isolation**: Containerized deployment for security

## 🚦 Monitoring & Observability

The application itself includes:
- **Execution logs**: `agent_tool_logs.log`
- **API call tracking**: Debug output for all Datadog queries
- **Error handling**: Graceful degradation on API failures
- **Response times**: Logged for performance monitoring

## 📚 Additional Documentation

- **[MCP_SERVER.md](MCP_SERVER.md)**: 🆕 Complete MCP Server setup guide for Claude Desktop and Cursor integration
- **[QUICK_START.md](QUICK_START.md)**: Fast setup guide for getting started in minutes
- **[DOCKER_README.md](DOCKER_README.md)**: Detailed Docker deployment instructions
- **[DATADOG_SETUP.md](DATADOG_SETUP.md)**: Datadog configuration and API setup guide
- **[PAGERDUTY_SETUP.md](PAGERDUTY_SETUP.md)**: PagerDuty integration setup and usage guide
- **[CHANGELOG.md](CHANGELOG.md)**: Version history and release notes

## 🎨 Interface Layout

### Main Interface Structure
```
┌─────────────────────────────────────────────────────────────┐
│                   🧠 OneView GOC AI 🧠                  🌓  │ ← Centered title
├──────────────┬────────────────────────────┬─────────────────┤
│ Sidebar      │ Main Content Area          │                 │
│              │                            │                 │
│ ➕ New Chat │ ┌─────────────────────────┬─────────────────┐│
│              │ │ How to use:             │ 🚨 PagerDuty   ││
│ 📜 History   │ │ 1️⃣ Select tools        │    Status       ││
│ • Query 1    │ │ 2️⃣ Type query           │                 ││
│ • Query 2    │ │ 3️⃣ Click Send           │  🔴 0  🟡 0     ││
│ • Query 3    │ │ 💡 Example...           │     🟢 107      ││
│ ▼ Show more  │ │                         │                 ││
│              │ │                         │ Active | Resolv ││
│ 🌐 Arlo      │ └─────────────────────────┴─────────────────┘│
│    Status    │                                              │
│ ✅ Summary   │ [Tool Checkboxes]                           │
│ Services:    │ [Time Range Selector]                       │
│ • All Good   │ [Text Input]                                │
│ Incidents:   │ [Send Button]                               │
│ • Last 7     │                                              │
│              │ [Results Area with Charts & Tables]         │
└──────────────┴──────────────────────────────────────────────┘
```

### Key Layout Features
- **Centered Header**: OneView GOC AI branding centered for professional appearance
- **Three-Section Layout**: Sidebar | Main Content | (Expandable for results)
- **Side-by-Side Top Section**: "How to use" paired with PagerDuty Status
- **Auto-Refresh Monitors**: Both Arlo and PagerDuty update independently
- **Compact History**: Expandable from 3 to all searches
- **Responsive Design**: Adapts to different screen sizes

## 🔄 Upgrading from v1.x to v2.0

If you're upgrading from version 1.x (Arlo_AgenticAI), follow these steps:

### 1. Update Environment Variables
Add the new PagerDuty configuration to your `.env` file:
```bash
PAGERDUTY_API_TOKEN=your_pagerduty_api_token_here
```

### 2. Pull Latest Changes
```bash
git pull origin main
```

### 3. Rebuild Docker Container (if using Docker)
```bash
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

### 4. Restart Local Application (if running locally)
```bash
# Kill existing process
lsof -ti:8080 | xargs kill -9

# Restart
python3 app.py
```

### 5. Verify New Features
- Check PagerDuty Status card in main area
- Verify History shows only last 3 by default
- Confirm centered "OneView GOC AI" title
- Test PagerDuty tools (checkboxes)

### Breaking Changes
- Application name changed from `Arlo_AgenticAI` to `OneView GOC AI`
- Docker image name changed from `arlo-agenticai` to `goc-agenticai` (update your scripts)
- PagerDuty Status moved from sidebar to main area

## 🤝 Contributing

Contributions are welcome! Areas for improvement:
- Additional monitoring integrations (Prometheus, Grafana, New Relic, etc.)
- More AI model options
- Enhanced chart types and visualizations
- Mobile-responsive improvements
- Additional PagerDuty analytics
- Notification system for critical alerts
- Multi-language support
- Custom alert thresholds

## 📝 License

This project is proprietary software developed for internal use.

## 👤 Author

**Jorge Gil**  
Software Engineering Manager  
Expertise: DevOps, SRE, Operational Resilience, and AI-driven tooling for technical teams

## 🙏 Acknowledgments

- **Chart.js**: For beautiful, interactive charts and visualizations
- **Flask**: Lightweight and powerful web framework
- **Datadog**: Comprehensive monitoring and metrics platform
- **PagerDuty**: Incident management and on-call scheduling
- **Confluence**: Knowledge base and documentation
- **Ollama**: Local LLaMA 3 deployment for AI recommendations
- **Google Gemini**: Advanced AI capabilities
- **Beautiful Soup**: HTML parsing for status scraping

---

⭐ **Star this repository** if you find it helpful!  
🐛 **Report issues** to improve the tool  
💡 **Suggest features** to enhance functionality