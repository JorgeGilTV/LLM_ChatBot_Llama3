# 🚀 Quick Start Guide - Arlo GenAI

## ✅ Prerequisites

- Docker installed and running
- Docker Compose installed
- Valid credentials for Atlassian/Confluence and Google Gemini

## 📦 Setup in 3 Steps

### Step 1: Verify `.env` file exists
```bash
ls -la .env
```

If it doesn't exist, the application will use the credentials already in your `.env` file.

### Step 2: Start the application
```bash
# Using Docker Compose (recommended)
docker-compose up -d

# OR using the helper script
./docker-run.sh start
```

### Step 3: Access the application
Open your browser at: **http://localhost:5001**

## 🎯 Quick Commands

```bash
# Start the application
./docker-run.sh start

# View logs
./docker-run.sh logs

# Stop the application
./docker-run.sh stop

# Restart after code changes
./docker-run.sh rebuild

# Check status
./docker-run.sh status

# Open shell in container
./docker-run.sh shell
```

## 🧪 Test the Application

1. **Open**: http://localhost:5001
2. **Check Status Monitor**: Sidebar shows real-time Arlo status (auto-refreshes every 3 minutes)
3. **Select** a tool checkbox (e.g., "DD_Red_Metrics" or "Arlo_Versions")
4. **Select Time Range** (if Datadog/Splunk tools selected)
5. **Type** a search term (optional, e.g., "streaming-service")
6. **Click** "Send"
7. **View results**: Interactive charts and formatted data

## 🔧 Troubleshooting

### Port 5001 already in use?
```bash
# Change port in docker-compose.yml
ports:
  - "8080:5001"  # Maps host 8080 to container 5001
```

### Container won't start?
```bash
# Check logs
docker-compose logs arlo-genai

# Rebuild without cache
docker-compose build --no-cache
docker-compose up -d
```

### Need to update credentials?
```bash
# Edit .env file
vi .env

# Restart container
./docker-run.sh restart
```

## 📊 Available Tools

### Monitoring & Metrics
- **DD_Red_Metrics**: Datadog RED Metrics dashboard with charts ⭐
- **DD_Red_ADT**: Datadog ADT dashboard
- **DD_Errors**: Show only services with errors
- **P0_Streaming**: Splunk P0 Streaming dashboard ⭐ NEW!

### Documentation & Knowledge
- **Wiki**: Search Arlo Confluence documentation
- **Ask_ARLOCHAT**: Ask Arlo Slack bot

### Service Management
- **Arlo_Versions**: Check service versions across environments (with search!)
- **Owners**: Find service owners
- **Holiday_Oncall**: Check who's on-call today and holidays

### AI-Powered
- **Suggestions**: AI-powered troubleshooting recommendations
- **Ask_Gemini**: General AI queries (if configured)

### Auto-Refresh Features
- **Status Monitor**: Always visible in sidebar
  - Updates every 3 minutes automatically
  - Shows system summary, core services, and last 7 incidents
  - No tool selection needed!

### 🆕 Integration Setup
To use monitoring features, configure your credentials:
- Datadog: See [DATADOG_SETUP.md](DATADOG_SETUP.md)
- Splunk: Add `SPLUNK_TOKEN` to `.env`
- Slack: Add `SLACK_BOT_TOKEN` to `.env`

## 📖 More Information

- Detailed Docker instructions: [DOCKER_README.md](DOCKER_README.md)
- Project overview: [README.md](README.md)

## 🆘 Need Help?

Contact the development team or check the logs:
```bash
docker-compose logs -f arlo-genai
```
