# 🚀 GOC_AgenticAI - Deployment Guide

## 📦 Docker Image Distribution

This guide explains how to deploy the GOC_AgenticAI Docker image on a new system.

---

## 🔒 Security Notice

**IMPORTANT:** The Docker image does NOT contain any credentials or API tokens. You must configure them separately using environment variables.

---

## 📥 Loading the Docker Image

### Option 1: From .tar file

If you received a `goc-agenticai-v2.0.0.tar` file:

```bash
# Load the image into Docker
docker load -i goc-agenticai-v2.0.0.tar

# Verify the image was loaded
docker images | grep goc-agenticai
```

### Option 2: Build from source

```bash
# Make the build script executable
chmod +x build-docker.sh

# Run the build script
./build-docker.sh
```

---

## ⚙️ Configuration

### 1. Create `.env` file

Copy the example and configure your credentials:

```bash
cp .env.example .env
```

### 2. Edit `.env` with your credentials

```bash
nano .env
```

**Required variables:**

```bash
# Confluence
ATLASSIAN_EMAIL=your_email@arlo.com
CONFLUENCE_TOKEN=your_confluence_token

# Gemini AI
GEMINI_API_KEY=your_gemini_api_key

# Datadog
DATADOG_API_KEY=your_datadog_api_key
DATADOG_APP_KEY=your_datadog_app_key
DATADOG_SITE=arlo.datadoghq.com

# PagerDuty
PAGERDUTY_API_TOKEN=your_pagerduty_token
```

**Optional (currently disabled):**

```bash
# Splunk (requires IP whitelisting)
SPLUNK_HOST=arlo.splunkcloud.com
SPLUNK_TOKEN=your_splunk_token

# Slack (requires proper OAuth scopes)
SLACK_BOT_TOKEN=your_slack_token
```

---

## 🚀 Running the Container

### Using docker-compose (Recommended)

```bash
# Start the container
docker-compose up -d

# View logs
docker logs -f arlo-agenticai-app

# Stop the container
docker-compose down
```

### Using docker run

```bash
docker run -d \
  --name goc-agenticai \
  -p 8080:8080 \
  --env-file .env \
  multi-agent-mcp-arlo-agenticai:latest
```

---

## 🌐 Accessing the Application

Once the container is running:

**Web Interface:** http://localhost:8080

**Health Check:** http://localhost:8080/api/tools

---

## 🔧 Troubleshooting

### Check container status

```bash
docker ps | grep arlo
```

### View container logs

```bash
docker logs arlo-agenticai-app --tail 50
```

### Check health status

```bash
docker inspect arlo-agenticai-app --format='{{.State.Health.Status}}'
```

### Restart container

```bash
docker-compose restart
```

---

## 📊 Available Tools

The application includes these integrated tools:

- ✅ **Wiki** - Confluence documentation search
- ✅ **Owners** - Service ownership information
- ✅ **Arlo_Versions** - Version tracking
- ✅ **DD_Red_Metrics** - Datadog dashboards
- ✅ **DD_Red_ADT** - Datadog ADT metrics
- ✅ **DD_Errors** - Error monitoring
- ✅ **Holiday_Oncall** - On-call schedule
- ✅ **PagerDuty** - Incident management
- ✅ **PagerDuty_Dashboards** - Analytics
- ✅ **PagerDuty_Insights** - Trends

**Currently Disabled (pending configuration):**

- ⏸️ **P0_Streaming** - Splunk (requires IP whitelist)
- ⏸️ **Ask_ARLOCHAT** - Slack integration (requires OAuth scopes)

---

## 🔐 Security Best Practices

1. **Never commit `.env` file** - It's in `.gitignore`
2. **Use environment-specific tokens** - Don't share production tokens
3. **Rotate tokens regularly** - Update API keys periodically
4. **Limit token permissions** - Use least-privilege principle
5. **Keep image up to date** - Rebuild when dependencies change

---

## 📝 Version Information

- **Version:** v2.0.0
- **Base Image:** python:3.12-slim
- **Port:** 8080
- **Health Check:** Enabled (30s interval)

---

## 🆘 Support

For issues or questions:

1. Check logs: `docker logs arlo-agenticai-app`
2. Verify `.env` configuration
3. Ensure all required tokens are valid
4. Check network connectivity to APIs

---

## 📄 License

Internal Arlo use only.
