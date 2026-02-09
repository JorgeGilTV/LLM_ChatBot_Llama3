# 🐳 Docker Deployment Guide for Arlo GenAI

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
   Open your browser at: http://localhost:5001

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
   docker build -t arlo-genai:latest .
   ```

2. **Run the container:**
   ```bash
   docker run -d \
     --name arlo-genai-app \
     -p 5001:5001 \
     --env-file .env \
     -v $(pwd)/logs:/app/logs \
     arlo-genai:latest
   ```

3. **View logs:**
   ```bash
   docker logs -f arlo-genai-app
   ```

4. **Stop the container:**
   ```bash
   docker stop arlo-genai-app
   docker rm arlo-genai-app
   ```

## Configuration

### Environment Variables

The application requires the following environment variables (set in `.env` file):

- `ATLASSIAN_EMAIL`: Your Atlassian/Arlo email
- `CONFLUENCE_TOKEN`: Your Confluence API token
- `GEMINI_API_KEY`: Google Gemini API key
- `SNOW_USER`: ServiceNow username (optional)
- `SNOW_PASSWORD`: ServiceNow password (optional)

### Port Configuration

By default, the application runs on port 5001. To change it:

**In docker-compose.yml:**
```yaml
ports:
  - "8080:5001"  # Maps host port 8080 to container port 5001
```

**Or in app.py:**
Change the port in the last line:
```python
flask_app.run(host='0.0.0.0', port=5001)
```

## Useful Commands

### Rebuild the container after code changes:
```bash
docker-compose up -d --build
```

### Execute commands inside the container:
```bash
docker-compose exec arlo-genai bash
```

### View real-time logs:
```bash
docker-compose logs -f arlo-genai
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
docker-compose logs arlo-genai
```

### Port already in use
Change the port mapping in `docker-compose.yml` or stop the conflicting service:
```bash
lsof -ti:5001 | xargs kill -9
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
CMD ["gunicorn", "--bind", "0.0.0.0:5001", "--workers", "4", "--timeout", "120", "app:flask_app"]
```

## Health Checks

The container includes a health check that pings the `/api/tools` endpoint every 30 seconds.

Check health status:
```bash
docker inspect --format='{{json .State.Health}}' arlo-genai-app | python -m json.tool
```

## Support

For issues or questions, contact the development team or check the main README.md file.
