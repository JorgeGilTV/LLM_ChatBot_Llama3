# 🐳 Docker Deployment Guide for GOC AgenticAI

## Prerequisites

- Docker installed (version 20.10 or higher)
- Docker Compose installed (version 2.0 or higher)

## Quick Start

### Option 1: Using Docker Compose (Recommended)

1. **Ensure your `.env` file is configured** with the necessary credentials:
   ```bash
   cp .env.example .env
   # Edit .env with your actual credentials
   ```

2. **Build and run the container:**
   ```bash
   docker-compose up -d
   ```

3. **Access the application:**
   Open your browser at: http://localhost:8080

4. **View logs:**
   ```bash
   docker-compose logs -f
   ```

5. **Stop the application:**
   ```bash
   docker-compose down
   ```

### Option 2: Using Docker directly

1. **Build the Docker image:**
   ```bash
   docker build -t arlo-agenticai:latest .
   ```

2. **Run the container:**
   ```bash
   docker run -d \
     --name arlo-agenticai-app \
     -p 8080:8080 \
     --env-file .env \
     -v $(pwd)/logs:/app/logs \
     arlo-agenticai:latest
   ```

3. **View logs:**
   ```bash
   docker logs -f arlo-agenticai-app
   ```

4. **Stop the container:**
   ```bash
   docker stop arlo-agenticai-app
   docker rm arlo-agenticai-app
   ```

## Configuration

### Environment Variables

The application requires the following environment variables (set in `.env` file):

#### Required for Core Functionality
- `ATLASSIAN_EMAIL`: Your Atlassian/Arlo email
- `CONFLUENCE_TOKEN`: Your Confluence API token
- `GEMINI_API_KEY`: Google Gemini API key

#### Required for Monitoring
- `DATADOG_API_KEY`: Datadog API key
- `DATADOG_APP_KEY`: Datadog application key
- `DATADOG_SITE`: Datadog site (e.g., arlo.datadoghq.com)
- `PAGERDUTY_API_TOKEN`: PagerDuty API token (for incident monitoring)

#### Optional
- `SPLUNK_HOST`: Splunk host URL
- `SPLUNK_TOKEN`: Splunk authentication token
- `SLACK_BOT_TOKEN`: Slack bot token for ArloChat
- `SNOW_USER`: ServiceNow username
- `SNOW_PASSWORD`: ServiceNow password

### Port Configuration

By default, the application runs on port 8080. To change it:

**In docker-compose.yml:**
```yaml
ports:
  - "8080:8080"  # Maps host port 8080 to container port 8080
```

**Or in app.py:**
Change the port in the last line:
```python
flask_app.run(host='0.0.0.0', port=8080)
```

## Useful Commands

### Rebuild the container after code changes:
```bash
docker-compose up -d --build
```

### Execute commands inside the container:
```bash
docker-compose exec arlo-agenticai bash
```

### View real-time logs:
```bash
docker-compose logs -f arlo-agenticai
```

### Check container health:
```bash
docker-compose ps
```

### Remove all containers and volumes:
```bash
docker-compose down -v
```

## Development Mode

For development with hot-reload, uncomment the volume mounts in `docker-compose.yml`:

```yaml
volumes:
  - ./logs:/app/logs
  - ./app.py:/app/app.py
  - ./tools:/app/tools
  - ./templates:/app/templates
  - ./static:/app/static
```

Then restart the container:
```bash
docker-compose down
docker-compose up -d
```

## Troubleshooting

### Container won't start
Check logs for errors:
```bash
docker-compose logs arlo-agenticai
```

### Port already in use
Change the port mapping in `docker-compose.yml` or stop the conflicting service:
```bash
lsof -ti:8080 | xargs kill -9
```

### Permission issues with logs
Ensure the logs directory is writable:
```bash
mkdir -p logs
chmod 755 logs
```

### Environment variables not loading
Verify `.env` file exists and is in the same directory as `docker-compose.yml`:
```bash
ls -la .env
```

## Production Deployment

For production deployment, consider:

1. **Use a reverse proxy (nginx) in front of Flask**
2. **Use a production WSGI server (gunicorn)**
3. **Add SSL/TLS certificates**
4. **Set up log rotation**
5. **Use Docker secrets for sensitive data**

Example with gunicorn:

**Add to requirements.txt:**
```
gunicorn>=21.2.0
```

**Update CMD in Dockerfile:**
```dockerfile
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "4", "--timeout", "120", "app:flask_app"]
```

## Health Checks

The container includes a health check that pings the `/api/tools` endpoint every 30 seconds.

Check health status:
```bash
docker inspect --format='{{json .State.Health}}' arlo-agenticai-app | python -m json.tool
```

## 🆕 New Features in v2.0

### PagerDuty Integration
The Docker container now includes full PagerDuty integration:

- **Auto-Refresh Monitor**: Real-time incident tracking in main area
- **API Pagination**: Fetches all incidents for accurate counts
- **Three Tools**: 
  - PagerDuty: Detailed incidents list
  - PagerDuty_Dashboards: Analytics with charts
  - PagerDuty_Insights: Trends and patterns
- **Clickable Incidents**: Direct links to PagerDuty platform

### UI Enhancements
- **Centered Branding**: GOC_AgenticAI prominently displayed
- **Smart History**: Shows last 3, expandable to all
- **Optimized Layout**: Two-column main area for better space usage
- **Unified Colors**: Consistent teal/green theme

### Environment Configuration
Make sure to add `PAGERDUTY_API_TOKEN` to your `.env` file before deploying.

## Support

For issues or questions, contact the development team or check the main README.md file.
