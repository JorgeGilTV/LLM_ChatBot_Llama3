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
2. **Select** a tool checkbox (e.g., "Read_Versions")
3. **Type** a search term (e.g., "clientapi") or leave empty for all results
4. **Click** "Send"

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

- **Read_Confluence**: Search Arlo Wiki documentation
- **Read_Versions**: Check service versions across environments (with search!)
- **Datadog_Dashboards**: List and search Datadog dashboards ⭐ NEW!
- **Service_Owners**: Find service owners
- **NOC_KT**: Check NOC knowledge transfer pages
- **Read_Arlo_status**: Verify Arlo system status
- **Oncall_Support**: Check who's on-call today
- **How_to_fix**: AI-powered troubleshooting recommendations
- **Ask_Gemini**: General AI queries
- **Ask_ARLOCHAT**: Ask Arlo Chat
- **MCP_Connect**: Check MCP server status

### 🆕 Datadog Integration
To use the Datadog Dashboards feature, you need to configure your Datadog credentials.
See [DATADOG_SETUP.md](DATADOG_SETUP.md) for detailed setup instructions.

## 📖 More Information

- Detailed Docker instructions: [DOCKER_README.md](DOCKER_README.md)
- Project overview: [README.md](README.md)

## 🆘 Need Help?

Contact the development team or check the logs:
```bash
docker-compose logs -f arlo-genai
```
