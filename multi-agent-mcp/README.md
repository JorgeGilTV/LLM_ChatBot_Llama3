# 🧠 GenDox AI - Multi-Agent Operations Dashboard

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Flask](https://img.shields.io/badge/Flask-3.1+-green.svg)](https://flask.palletsprojects.com/)
[![Docker](https://img.shields.io/badge/Docker-Ready-brightgreen.svg)](https://www.docker.com/)
[![Datadog](https://img.shields.io/badge/Datadog-Integration-purple.svg)](https://www.datadoghq.com/)
[![License](https://img.shields.io/badge/License-Proprietary-red.svg)]()

GenDox AI is a comprehensive web-based platform that integrates **real-time monitoring**, **documentation search**, and **AI-powered recommendations**. Designed for DevOps, SRE, and support teams, it streamlines troubleshooting workflows by combining multiple data sources and AI tools into a single intelligent interface.

**Key Highlight:** Interactive Datadog dashboard integration with real-time metrics visualization, showing Requests, Errors, and Latency for all your services in a beautiful 3-column grid layout with Chart.js powered visualizations.

---

## 📋 Table of Contents
- [What Does This Project Do?](#-what-does-this-project-do)
- [Key Features](#-key-features)
- [How It Works](#-how-it-works)
- [Technologies Used](#-technologies-used)
- [Installation](#-installation)
- [Configuration](#️-configuration)
- [How to Use](#-how-to-use)
- [Project Structure](#-project-structure)
- [UI/UX Features](#-uiux-features)
- [Troubleshooting](#-troubleshooting)
- [Performance](#-performance)
- [Security](#-security)

---

## 🎯 What Does This Project Do?

GenDox AI serves as a **centralized operations hub** that:

1. **Real-Time Monitoring**: Connects to Datadog to display live service metrics (requests, errors, latency) with interactive charts
2. **Intelligent Search**: Searches through Confluence documentation, service versions, and knowledge bases
3. **Service Discovery**: Identifies service owners, on-call engineers, and system status
4. **AI Assistance**: Provides troubleshooting recommendations using LLaMA 3 and Google Gemini
5. **Error Detection**: Automatically identifies and highlights services experiencing errors

## 🚀 Key Features

### 📊 Datadog Integration (Featured)
- **Datadog_Dashboards**: 
  - Displays RED metrics (Requests, Errors, Latency) for all services
  - Interactive bar charts with Chart.js visualization
  - Real-time data from Datadog API
  - Filter by service name
  - Configurable time ranges (1, 2, 4 hours, 2 days, 1 week)
  - Shows Average, Minimum, and Maximum latency percentiles
  - 3-column grid layout for efficient space usage
  - Direct links to Datadog service pages

- **Datadog_Errors**: 
  - Filters and displays ONLY services with active errors
  - Shows error count and percentage
  - Same comprehensive metrics as Dashboards
  - Quick error triage and investigation

### 🔍 Documentation & Knowledge
- **Read_Confluence**: Search Arlo Wiki documentation with intelligent ranking
- **NOC_KT**: Access NOC knowledge transfer documentation
- **Ask_ARLOCHAT**: Interact with Arlo's chat system for questions

### 📦 Service Management
- **Read_Versions**: Check service versions across all environments with search capabilities
- **Read_Arlo_Status**: Monitor Arlo system health and service status  
- **Service_Owners**: Identify service ownership and responsibilities
- **Oncall_Support**: Check current on-call engineers and escalation paths

### 🤖 AI-Powered Tools
- **How_to_fix**: AI-powered troubleshooting recommendations using LLaMA 3
- **Suggestions_Tool**: Contextual suggestions based on your query
- **Tickets_Tool**: ServiceNow ticket integration and analysis

## 🔧 How It Works

### Architecture Overview
```
User Browser → Flask Web Server → Multiple Tool Modules → External APIs
                                                         ├─ Datadog API
                                                         ├─ Confluence API
                                                         ├─ ServiceNow API
                                                         ├─ LLaMA 3 (Ollama)
                                                         └─ Google Gemini
```

### Datadog Dashboard Flow
1. **User Input**: Select "Datadog_Dashboards" and optionally enter service name
2. **API Query**: Fetches "RED - Metrics" dashboard from Datadog
3. **Widget Filtering**: Filters widgets by service name (if provided)
4. **Data Collection**: For each service, queries:
   - Requests: `trace.servlet.request.hits` (as count)
   - Errors: `trace.servlet.request.errors` (as count + percentage)
   - Latency: `trace.servlet.request.duration` (avg, min, max in milliseconds)
5. **Visualization**: Generates interactive bar charts using Chart.js
6. **Display**: Shows widgets in 3-column grid with real-time metrics

### Time Range Selection
- Dynamically shown only when Datadog tools are selected
- Options: 1 hour, 2 hours, 4 hours, 2 days, 1 week
- Affects data queries to Datadog API

### Error Detection (Datadog_Errors)
1. Queries all services from RED dashboard
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
- **Confluence API**: Documentation search and retrieval
- **ServiceNow API**: Ticket management integration
- **LLaMA 3 (via Ollama)**: Local AI model for troubleshooting
- **Google Gemini**: Cloud AI for general queries

### DevOps
- **Docker + Docker Compose**: Containerized deployment
- **Environment Variables**: Secure credential management
- **Git**: Version control and collaboration

🖥️ Web Interface
Dark theme with gradient header
Sidebar with selectable tools (checkboxes)
Input box and live execution timer
Results displayed in styled cards per tool
“New Chat” button resets input and selections

## 📦 Installation

### Prerequisites
- Docker and Docker Compose (for containerized deployment)
- OR Python 3.12+ (for local installation)
- Datadog API and Application keys (for monitoring features)
- Confluence credentials (for documentation search)
- ServiceNow credentials (for ticket integration)

### Option 1: Docker (Recommended)

#### Quick Start
```bash
# 1. Clone the repository
git clone https://github.com/your-username/gendox-ai.git
cd multi-agent-mcp

# 2. Configure environment variables
cp .env.example .env
# Edit .env with your credentials (see Configuration section below)

# 3. Start with Docker Compose
docker-compose up -d

# 4. Access the application
# Open http://localhost:5001 in your browser
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
git clone https://github.com/your-username/gendox-ai.git
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
1. **Access the application**: Open http://localhost:5001 in your browser
2. **Select tools**: Choose one or more tools from the checkbox list
3. **Configure options**: 
   - **Time Range** (for Datadog tools): Select from dropdown (1h, 2h, 4h, 2d, 1w)
   - Auto-shows when Datadog_Dashboards or Datadog_Errors is selected
4. **Enter query**: Type your search (service name, keyword, etc.)
5. **Execute**: Click "Send" button
6. **View results**: See formatted results with interactive charts
7. **New search**: Click "New Chat" to reset

### 📊 Using Datadog Dashboards

#### View All Services
```
1. Check "Datadog_Dashboards"
2. Select time range (default: 4 hours)
3. Leave query empty
4. Click "Send"
→ Shows all services with metrics and charts
```

#### Filter by Service Name
```
1. Check "Datadog_Dashboards"
2. Select time range
3. Enter service name: "oauth" or "backend-arlosafeapi"
4. Click "Send"
→ Shows only matching service widgets
```

#### Find Services with Errors
```
1. Check "Datadog_Errors"
2. Select time range
3. Leave query empty (or filter by service)
4. Click "Send"
→ Shows only services experiencing errors
→ Displays error count and percentage
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
CONFLUENCE_USER=your-email@company.com
CONFLUENCE_API_TOKEN=your_confluence_token
```

#### ServiceNow Configuration
```bash
SERVICENOW_INSTANCE=your-instance.service-now.com
SERVICENOW_USER=your_username
SERVICENOW_PASSWORD=your_password
```

#### Optional: AI Models
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
- **Dark theme**: Reduces eye strain for extended use
- **Gradient header**: Purple gradient design
- **Card-based results**: Each tool displays results in styled cards
- **Real-time feedback**: Live execution timer and loading indicators
- **Interactive charts**: Hover over charts to see exact values
- **Responsive layout**: 3-column grid adapts to screen size

### User Experience
- **Multi-select tools**: Run multiple tools simultaneously
- **Smart time range**: Auto-shows for Datadog queries
- **Search history**: Track previous queries
- **Direct links**: Quick access to Datadog, Confluence, etc.
- **Error highlighting**: Services with errors shown in red
- **New Chat button**: Quick reset for new searches

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
# Or kill process: lsof -ti:5001 | xargs kill -9
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

- **[QUICK_START.md](QUICK_START.md)**: Fast setup guide
- **[DOCKER_README.md](DOCKER_README.md)**: Detailed Docker instructions
- **[DATADOG_SETUP.md](DATADOG_SETUP.md)**: Datadog configuration guide

## 🤝 Contributing

Contributions are welcome! Areas for improvement:
- Additional monitoring integrations (Prometheus, Grafana, etc.)
- More AI model options
- Enhanced chart types and visualizations
- Mobile-responsive improvements
- Additional tool integrations

## 📝 License

This project is proprietary software developed for internal use.

## 👤 Author

**Jorge Gil**  
Software Engineering Manager  
Expertise: DevOps, SRE, Operational Resilience, and AI-driven tooling for technical teams

## 🙏 Acknowledgments

- **Chart.js**: For beautiful, interactive charts
- **Flask**: Lightweight and powerful web framework
- **Datadog**: Comprehensive monitoring platform
- **Ollama**: Local LLaMA 3 deployment
- **Google Gemini**: Advanced AI capabilities

---

⭐ **Star this repository** if you find it helpful!  
🐛 **Report issues** to improve the tool  
💡 **Suggest features** to enhance functionality