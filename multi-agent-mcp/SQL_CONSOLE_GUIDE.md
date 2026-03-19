# 🗄️ SQL Console Guide

## Overview

The SQL Console is a web-based interface for querying the historical metrics database. It provides a secure, read-only way to explore service health data, critical incidents, and performance trends.

---

## 🌐 Access

**URL:** `http://localhost:8080/admin/sql`

**From UI:**
1. **Main Page** → Click "📊 Status Monitor ▼" dropdown → "🗄️ SQL Console"
2. **Status Monitor Page** → Click "🗄️ SQL Console" button in header
3. **Direct URL** → Navigate to `/admin/sql`

---

## 🔒 Security

### Read-Only Mode
- ✅ Only `SELECT` queries are allowed
- ❌ Blocks: `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `TRUNCATE`, `REPLACE`
- ✅ Safe for production use - cannot modify or delete data

### Query Validation
```
✅ SELECT * FROM service_metrics LIMIT 10;
❌ DELETE FROM service_metrics;  (Blocked)
❌ DROP TABLE service_metrics;   (Blocked)
❌ UPDATE service_metrics SET status='critical';  (Blocked)
```

---

## 📋 Database Schema

### Table: `service_metrics`
Individual service health measurements

| Column | Type | Description |
|--------|------|-------------|
| `timestamp` | TEXT | ISO 8601 timestamp (UTC) |
| `service` | TEXT | Service name (e.g., "backend-auth") |
| `environment` | TEXT | Environment (production/goldendev/goldenqa) |
| `status` | TEXT | Health status (healthy/warning/critical/inactive) |
| `requests` | INTEGER | Total requests in time window |
| `errors` | INTEGER | Total errors in time window |
| `error_rate` | REAL | Error rate percentage (0-100) |
| `p95_latency` | REAL | 95th percentile latency (milliseconds) |
| `p99_latency` | REAL | 99th percentile latency (milliseconds) |
| `traffic_drop` | INTEGER | Boolean: 1 if traffic dropped >85% vs 7-day avg |
| `high_latency` | INTEGER | Boolean: 1 if latency exceeds thresholds |
| `pd_incident` | INTEGER | Boolean: 1 if active PagerDuty incident |

### Table: `dashboard_snapshots`
Overall dashboard health summaries

| Column | Type | Description |
|--------|------|-------------|
| `timestamp` | TEXT | ISO 8601 timestamp (UTC) |
| `environment` | TEXT | Environment or "all" |
| `total_services` | INTEGER | Number of active services |
| `healthy` | INTEGER | Services in healthy state |
| `warning` | INTEGER | Services in warning state |
| `critical` | INTEGER | Services in critical state |
| `total_requests` | INTEGER | Sum of all requests |
| `total_errors` | INTEGER | Sum of all errors |
| `overall_error_rate` | REAL | Overall error rate percentage |

---

## 💡 Pre-built Query Examples

### 1. Recent Metrics
```sql
SELECT 
    timestamp, 
    service, 
    environment, 
    status,
    requests,
    errors,
    error_rate,
    p95_latency
FROM service_metrics 
ORDER BY timestamp DESC 
LIMIT 50;
```

### 2. Critical Services (Last 24h)
```sql
SELECT 
    timestamp,
    service,
    environment,
    error_rate,
    requests,
    errors,
    CASE 
        WHEN pd_incident = 1 THEN 'PagerDuty Alert'
        WHEN traffic_drop = 1 THEN 'Traffic Drop'
        WHEN error_rate > 5 THEN 'High Error Rate'
        ELSE 'Unknown'
    END as reason
FROM service_metrics 
WHERE status = 'critical'
    AND timestamp > datetime('now', '-24 hours')
ORDER BY timestamp DESC;
```

### 3. Error Rate Trends for Specific Service
```sql
SELECT 
    datetime(timestamp) as time,
    service,
    environment,
    error_rate,
    requests,
    errors
FROM service_metrics 
WHERE service = 'backend-auth'  -- Change service name
    AND timestamp > datetime('now', '-7 days')
ORDER BY timestamp ASC;
```

### 4. Service Summary (Averages)
```sql
SELECT 
    service,
    environment,
    COUNT(*) as measurements,
    ROUND(AVG(error_rate), 2) as avg_error_rate,
    ROUND(AVG(requests), 0) as avg_requests,
    ROUND(AVG(p95_latency), 2) as avg_p95_latency,
    SUM(CASE WHEN status = 'critical' THEN 1 ELSE 0 END) as critical_count
FROM service_metrics 
WHERE timestamp > datetime('now', '-24 hours')
GROUP BY service, environment
ORDER BY critical_count DESC, avg_error_rate DESC;
```

### 5. Dashboard Snapshots History
```sql
SELECT 
    timestamp,
    environment,
    total_services,
    healthy,
    warning,
    critical,
    ROUND(overall_error_rate, 2) as error_rate,
    total_requests,
    total_errors
FROM dashboard_snapshots 
ORDER BY timestamp DESC 
LIMIT 50;
```

### 6. High Latency Services
```sql
SELECT 
    timestamp,
    service,
    environment,
    p95_latency,
    p99_latency,
    requests,
    error_rate
FROM service_metrics 
WHERE p95_latency > 1000  -- > 1 second
    AND timestamp > datetime('now', '-24 hours')
ORDER BY p95_latency DESC;
```

### 7. Traffic Analysis by Hour
```sql
SELECT 
    strftime('%Y-%m-%d %H:00', timestamp) as hour,
    environment,
    SUM(requests) as total_requests,
    COUNT(DISTINCT service) as active_services,
    ROUND(AVG(error_rate), 2) as avg_error_rate
FROM service_metrics 
WHERE timestamp > datetime('now', '-48 hours')
GROUP BY hour, environment
ORDER BY hour DESC;
```

---

## 🎯 Common Use Cases

### Troubleshooting a Service
```sql
-- View all metrics for a specific service
SELECT * 
FROM service_metrics 
WHERE service = 'backend-camsdk-webserver'
    AND environment = 'goldenqa'
    AND timestamp > datetime('now', '-24 hours')
ORDER BY timestamp DESC;
```

### Identify Problem Services
```sql
-- Find services with high error rates
SELECT 
    service,
    environment,
    AVG(error_rate) as avg_error_rate,
    MAX(error_rate) as peak_error_rate,
    COUNT(*) as measurements
FROM service_metrics
WHERE timestamp > datetime('now', '-7 days')
GROUP BY service, environment
HAVING avg_error_rate > 2
ORDER BY avg_error_rate DESC;
```

### Traffic Drop Analysis
```sql
-- Services with traffic drops
SELECT 
    timestamp,
    service,
    environment,
    requests,
    error_rate
FROM service_metrics
WHERE traffic_drop = 1
    AND timestamp > datetime('now', '-7 days')
ORDER BY timestamp DESC;
```

### Environment Health Comparison
```sql
-- Compare environments
SELECT 
    environment,
    COUNT(DISTINCT service) as total_services,
    AVG(error_rate) as avg_error_rate,
    SUM(requests) as total_requests,
    SUM(errors) as total_errors
FROM service_metrics
WHERE timestamp > datetime('now', '-24 hours')
GROUP BY environment;
```

### PagerDuty Correlation History
```sql
-- Services with PagerDuty incidents
SELECT 
    timestamp,
    service,
    environment,
    error_rate,
    requests
FROM service_metrics
WHERE pd_incident = 1
    AND timestamp > datetime('now', '-7 days')
ORDER BY timestamp DESC;
```

---

## ⌨️ Keyboard Shortcuts

- **Ctrl/Cmd + Enter**: Execute query
- **Tab**: Standard text editing

---

## 🎨 UI Features

### Results Display
- **Color-coded Status**: Green (healthy), Yellow (warning), Red (critical)
- **Formatted Numbers**: Thousands separators for large values
- **Precision**: Decimals for rates and latencies
- **NULL Handling**: Grayed out NULL values
- **Responsive Tables**: Horizontal scroll for wide results

### Query Editor
- **Monospace Font**: Monaco/Menlo for better code readability
- **Auto-resize**: Vertical resize handle
- **Focus Highlighting**: Blue border on active editor
- **Clear Button**: Quick reset

### Metadata Display
- **Row Count**: Total results returned
- **Execution Time**: Query performance in milliseconds
- **Empty State**: Friendly message when no results

---

## 📊 Data Retention

- **Default**: 30 days
- **Cleanup**: Automatic (runs daily)
- **Manual Cleanup**: Use API endpoint `POST /api/cleanup?days=30`

---

## 🚨 Common Queries

### Quick Health Check
```sql
SELECT COUNT(*) as total_measurements FROM service_metrics;
SELECT COUNT(*) as total_snapshots FROM dashboard_snapshots;
```

### Latest Status for All Services
```sql
SELECT 
    service,
    environment,
    status,
    error_rate,
    requests,
    timestamp
FROM service_metrics
WHERE timestamp = (
    SELECT MAX(timestamp) 
    FROM service_metrics
)
ORDER BY 
    CASE status 
        WHEN 'critical' THEN 1
        WHEN 'warning' THEN 2
        WHEN 'healthy' THEN 3
        ELSE 4
    END,
    service;
```

### Time-based Aggregations
```sql
-- Hourly stats for last 24h
SELECT 
    strftime('%Y-%m-%d %H:00', timestamp) as hour,
    COUNT(*) as measurements,
    AVG(error_rate) as avg_error,
    SUM(requests) as total_requests
FROM service_metrics
WHERE timestamp > datetime('now', '-24 hours')
GROUP BY hour
ORDER BY hour DESC;
```

---

## 🔧 Tips & Best Practices

1. **Use LIMIT**: Always add `LIMIT` to queries to avoid overwhelming results
2. **Filter by Time**: Use `datetime('now', '-24 hours')` for recent data
3. **Index Usage**: Queries on `timestamp`, `service`, `environment` are optimized
4. **Join Tables**: You can join `service_metrics` and `dashboard_snapshots` by timestamp
5. **Aggregations**: Use `GROUP BY` for summaries and trends
6. **Date Functions**: SQLite's `strftime()` is powerful for time-based grouping

---

## 🐛 Troubleshooting

### Query Returns No Results
- ✅ Check database has data: `SELECT COUNT(*) FROM service_metrics;`
- ✅ Verify time range in WHERE clause
- ✅ Check service/environment names are exact matches

### Slow Query
- ✅ Add `LIMIT` clause
- ✅ Filter by time to reduce dataset
- ✅ Check if indexes exist: `PRAGMA index_list('service_metrics');`

### Blocked Query
- ✅ Ensure query starts with `SELECT`
- ✅ Remove any INSERT/UPDATE/DELETE/DROP keywords
- ✅ Check error message for specific blocked keyword

---

## 📖 SQLite Resources

- **Date Functions**: [SQLite Date & Time](https://www.sqlite.org/lang_datefunc.html)
- **Aggregate Functions**: [SQLite Aggregates](https://www.sqlite.org/lang_aggfunc.html)
- **String Functions**: [SQLite String Functions](https://www.sqlite.org/lang_corefunc.html)

---

## 🔗 Related Documentation

- [API_DOCUMENTATION.md](API_DOCUMENTATION.md) - REST API endpoints
- [STATUS_MONITOR_CONFIG.md](STATUS_MONITOR_CONFIG.md) - Monitoring thresholds and configuration
- [CHANGELOG_v3.0.2.md](CHANGELOG_v3.0.2.md) - Version history and features
