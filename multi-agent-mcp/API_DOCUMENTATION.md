# OneView API Documentation

REST API endpoints for accessing service health metrics and historical data.

## Base URL
```
http://localhost:5000
```

---

## 📊 Current Status Endpoints

### Get Current Status (All Services)
```http
GET /api/status/current
```

**Query Parameters:**
- `environment` (optional): Filter by environment (`production`, `goldendev`, `goldenqa`)

**Response:**
```json
{
  "success": true,
  "timestamp": "2026-02-17T19:30:00.123456",
  "total_services": 45,
  "services": [
    {
      "service": "arlo-api",
      "environment": "production",
      "status": "healthy",
      "requests": 125000,
      "errors": 45,
      "error_rate": 0.036,
      "p95_latency": 125.5,
      "p99_latency": 245.8,
      "traffic_drop": false,
      "high_latency": false,
      "pd_incident": false
    }
  ]
}
```

**Example:**
```bash
curl "http://localhost:5000/api/status/current"
curl "http://localhost:5000/api/status/current?environment=production"
```

---

### Get Status by Environment
```http
GET /api/status/{environment}
```

**Path Parameters:**
- `environment`: `production`, `goldendev`, or `goldenqa`

**Response:**
```json
{
  "success": true,
  "timestamp": "2026-02-17T19:30:00.123456",
  "environment": "production",
  "summary": {
    "total_services": 18,
    "healthy": 15,
    "warning": 2,
    "critical": 1
  },
  "services": [...]
}
```

**Example:**
```bash
curl "http://localhost:5000/api/status/production"
```

---

## 📈 Historical Data Endpoints

### Get Service History
```http
GET /api/history/service/{service_name}
```

**Path Parameters:**
- `service_name`: Name of the service

**Query Parameters:**
- `environment` (required): `production`, `goldendev`, or `goldenqa`
- `hours` (optional): Hours to look back (default: 24, max: 720 = 30 days)

**Response:**
```json
{
  "success": true,
  "service": "arlo-api",
  "environment": "production",
  "hours": 24,
  "data_points": 288,
  "history": [
    {
      "timestamp": "2026-02-17T18:00:00",
      "status": "healthy",
      "requests": 125000,
      "errors": 45,
      "error_rate": 0.036,
      "p95_latency": 125.5,
      "p99_latency": 245.8
    }
  ]
}
```

**Example:**
```bash
curl "http://localhost:5000/api/history/service/arlo-api?environment=production&hours=48"
```

---

### Get Dashboard History
```http
GET /api/history/dashboard
```

**Query Parameters:**
- `environment` (optional): Filter by environment
- `hours` (optional): Hours to look back (default: 24)

**Response:**
```json
{
  "success": true,
  "environment": "production",
  "hours": 24,
  "data_points": 288,
  "history": [
    {
      "timestamp": "2026-02-17T18:00:00",
      "total_services": 18,
      "healthy_count": 16,
      "warning_count": 1,
      "critical_count": 1,
      "total_requests": 2500000,
      "total_errors": 1250,
      "overall_error_rate": 0.05,
      "pd_triggered": 1,
      "pd_acknowledged": 0,
      "pd_resolved": 5
    }
  ]
}
```

**Example:**
```bash
curl "http://localhost:5000/api/history/dashboard?environment=production&hours=24"
curl "http://localhost:5000/api/history/dashboard?hours=168"
```

---

### Get Service Trends
```http
GET /api/trends/service/{service_name}
```

**Path Parameters:**
- `service_name`: Name of the service

**Query Parameters:**
- `environment` (required): `production`, `goldendev`, or `goldenqa`
- `hours` (optional): Hours to analyze (default: 24)

**Response:**
```json
{
  "success": true,
  "trends": {
    "service": "arlo-api",
    "environment": "production",
    "data_points": 288,
    "avg_error_rate": 0.042,
    "max_error_rate": 0.15,
    "min_error_rate": 0.01,
    "avg_requests": 120000,
    "max_requests": 145000,
    "trend": "stable",
    "time_range_hours": 24
  }
}
```

**Trend Values:**
- `improving`: Error rate decreased >20%
- `stable`: Error rate within ±20%
- `degrading`: Error rate increased >20%
- `insufficient_data`: Not enough data points

**Example:**
```bash
curl "http://localhost:5000/api/trends/service/arlo-api?environment=production&hours=24"
```

---

### Get Critical Services History
```http
GET /api/critical/history
```

**Query Parameters:**
- `hours` (optional): Hours to look back (default: 24)

**Response:**
```json
{
  "success": true,
  "hours": 24,
  "total_incidents": 15,
  "incidents": [
    {
      "timestamp": "2026-02-17T15:30:00",
      "service": "arlo-api",
      "environment": "production",
      "status": "critical",
      "error_rate": 5.2,
      "requests": 95000,
      "errors": 4940,
      "pd_incident": true,
      "traffic_drop": false,
      "high_latency": true
    }
  ]
}
```

**Example:**
```bash
curl "http://localhost:5000/api/critical/history?hours=48"
```

---

## 🏥 Health & Monitoring

### Health Check
```http
GET /api/health
```

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2026-02-17T19:30:00.123456",
  "database": {
    "exists": true,
    "path": "/path/to/metrics_history.db",
    "file_size_mb": 12.5,
    "total_metrics": 150000,
    "total_snapshots": 5000,
    "oldest_record": "2026-01-17T00:00:00",
    "newest_record": "2026-02-17T19:30:00"
  },
  "version": "3.0.1"
}
```

**Example:**
```bash
curl "http://localhost:5000/api/health"
```

---

## 🔧 Usage Examples

### Python
```python
import requests

# Get current production status
response = requests.get('http://localhost:5000/api/status/production')
data = response.json()

for service in data['services']:
    print(f"{service['service']}: {service['status']} ({service['error_rate']:.2f}%)")

# Get service history
response = requests.get(
    'http://localhost:5000/api/history/service/arlo-api',
    params={'environment': 'production', 'hours': 48}
)
history = response.json()['history']
```

### JavaScript/fetch
```javascript
// Get current status
fetch('/api/status/current?environment=production')
  .then(res => res.json())
  .then(data => {
    console.log(`Total services: ${data.total_services}`);
    data.services.forEach(svc => {
      console.log(`${svc.service}: ${svc.status}`);
    });
  });

// Get service trends
fetch('/api/trends/service/arlo-api?environment=production&hours=24')
  .then(res => res.json())
  .then(data => {
    const trend = data.trends;
    console.log(`Trend: ${trend.trend}`);
    console.log(`Avg Error Rate: ${trend.avg_error_rate}%`);
  });
```

### curl
```bash
# Health check
curl http://localhost:5000/api/health

# Current status
curl http://localhost:5000/api/status/current

# Production status
curl http://localhost:5000/api/status/production

# Service history (24 hours)
curl "http://localhost:5000/api/history/service/arlo-api?environment=production&hours=24"

# Dashboard history (7 days)
curl "http://localhost:5000/api/history/dashboard?environment=production&hours=168"

# Service trends
curl "http://localhost:5000/api/trends/service/arlo-api?environment=production&hours=24"

# Critical incidents (last 48 hours)
curl "http://localhost:5000/api/critical/history?hours=48"
```

---

## 📝 Notes

1. **Data Retention**: Historical data is retained for 30 days by default
2. **Caching**: Current status queries use the database's latest snapshot
3. **Time Format**: All timestamps are in ISO 8601 UTC format
4. **Rate Limiting**: No rate limiting currently implemented
5. **Authentication**: No authentication currently required (add before production use)
6. **CORS**: Enabled for all origins (configure restrictively for production)

---

## 🔄 Data Collection

Metrics are automatically saved to the database every time the status monitor dashboard is refreshed:
- Service-level metrics saved every refresh
- Dashboard snapshots saved every refresh
- Typical refresh interval: 2 minutes (via UI auto-refresh)
- Data points per day: ~720 per service per environment

---

## 🚀 Integration Examples

### Grafana Dashboard
You can use these APIs to create Grafana dashboards using the JSON API datasource:

1. Install JSON API plugin in Grafana
2. Add datasource: `http://your-server:5000`
3. Create panels using endpoints like `/api/status/production`

### CI/CD Pipeline
```bash
#!/bin/bash
# Check if production services are healthy before deployment

STATUS=$(curl -s http://localhost:5000/api/status/production)
CRITICAL=$(echo $STATUS | jq '.summary.critical')

if [ "$CRITICAL" -gt 0 ]; then
  echo "❌ Cannot deploy: $CRITICAL critical services"
  exit 1
fi

echo "✅ All services healthy, proceeding with deployment"
```

### Slack Bot
```python
import requests
import slack_sdk

def check_and_alert():
    response = requests.get('http://localhost:5000/api/critical/history?hours=1')
    data = response.json()
    
    if data['total_incidents'] > 0:
        slack_client.chat_postMessage(
            channel='#alerts',
            text=f"🚨 {data['total_incidents']} critical incidents detected!"
        )
```
