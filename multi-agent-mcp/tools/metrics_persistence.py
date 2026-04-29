"""
Metrics Persistence Module
Stores and retrieves historical service health metrics
"""
import sqlite3
import json
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
import os

# Database file location
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'metrics_history.db')


def _connect_db(timeout: Optional[float] = None):
    """Open SQLite with WAL for better concurrent read/write under load."""
    kw = {}
    if timeout is not None:
        kw["timeout"] = timeout
    conn = sqlite3.connect(DB_PATH, **kw)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    return conn


def init_database():
    """Initialize the SQLite database with required tables"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    conn = _connect_db()
    cursor = conn.cursor()
    
    # Main metrics table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS service_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            environment TEXT NOT NULL,
            service TEXT NOT NULL,
            status TEXT NOT NULL,
            requests INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0,
            error_rate REAL DEFAULT 0.0,
            p95_latency REAL DEFAULT NULL,
            p99_latency REAL DEFAULT NULL,
            baseline_requests INTEGER DEFAULT NULL,
            traffic_drop INTEGER DEFAULT 0,
            high_latency INTEGER DEFAULT 0,
            pd_incident INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create indexes for faster queries
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_service_time 
        ON service_metrics(service, environment, timestamp)
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_timestamp 
        ON service_metrics(timestamp)
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_status 
        ON service_metrics(status)
    ''')
    
    # Summary snapshots table (for dashboard-level stats)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS dashboard_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            environment TEXT,
            total_services INTEGER DEFAULT 0,
            healthy_count INTEGER DEFAULT 0,
            warning_count INTEGER DEFAULT 0,
            critical_count INTEGER DEFAULT 0,
            total_requests INTEGER DEFAULT 0,
            total_errors INTEGER DEFAULT 0,
            overall_error_rate REAL DEFAULT 0.0,
            pd_triggered INTEGER DEFAULT 0,
            pd_acknowledged INTEGER DEFAULT 0,
            pd_resolved INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_snapshot_time 
        ON dashboard_snapshots(timestamp, environment)
    ''')
    
    # PagerDuty incidents history
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pagerduty_incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id TEXT NOT NULL UNIQUE,
            incident_number INTEGER,
            title TEXT,
            status TEXT,
            urgency TEXT,
            created_at TEXT,
            resolved_at TEXT,
            service_id TEXT,
            service_name TEXT,
            affected_services TEXT,
            duration_minutes INTEGER,
            assignees TEXT,
            created_at_db TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_pd_incident_time 
        ON pagerduty_incidents(created_at, status)
    ''')
    
    # Service state changes tracking
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS service_state_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            service_name TEXT NOT NULL,
            environment TEXT NOT NULL,
            previous_state TEXT,
            new_state TEXT NOT NULL,
            trigger_reason TEXT,
            error_rate REAL,
            latency_p95 REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_state_change_time 
        ON service_state_changes(timestamp, service_name, environment)
    ''')
    
    # Performance baselines (weekly aggregates)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS service_baselines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_name TEXT NOT NULL,
            environment TEXT NOT NULL,
            week_start TEXT NOT NULL,
            avg_error_rate REAL,
            avg_latency_p95 REAL,
            avg_traffic_rpm REAL,
            peak_traffic_rpm REAL,
            incidents_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(service_name, environment, week_start)
        )
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_baseline_service 
        ON service_baselines(service_name, environment, week_start)
    ''')
    
    # Deployment history
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS deployments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            service_name TEXT NOT NULL,
            environment TEXT NOT NULL,
            version TEXT,
            deployer TEXT,
            status TEXT,
            duration_seconds INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_deployment_time 
        ON deployments(timestamp, service_name, environment)
    ''')
    
    # Service outage tracking
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS service_outages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_name TEXT NOT NULL,
            environment TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT,
            duration_minutes INTEGER,
            severity TEXT,
            root_cause TEXT,
            pagerduty_incident_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_outage_time 
        ON service_outages(start_time, service_name, environment)
    ''')
    
    # Tool usage analytics
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tool_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            query_text TEXT,
            user_ip TEXT,
            response_time_ms INTEGER,
            success BOOLEAN,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_tool_usage_time 
        ON tool_usage(timestamp, tool_name)
    ''')

    # Short-lived API response cache (Datadog per-service health, PagerDuty, Arlo) for status monitor
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS status_monitor_api_cache (
            cache_kind TEXT NOT NULL,
            cache_key TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (cache_kind, cache_key)
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_sm_api_cache_updated
        ON status_monitor_api_cache(cache_kind, updated_at)
    ''')

    # Persisted EKS cluster names per (service, env) — avoids repeated Datadog metrics queries on every load
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS service_eks_clusters (
            service_name TEXT NOT NULL,
            environment TEXT NOT NULL,
            clusters_json TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (service_name, environment)
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_service_eks_updated
        ON service_eks_clusters(updated_at)
    ''')
    
    conn.commit()
    conn.close()
    print(f"✅ Database initialized at: {DB_PATH}")


def save_service_metrics(metrics_data: List[Dict], timestamp: Optional[str] = None):
    """
    Save service metrics to database
    
    Args:
        metrics_data: List of service status dictionaries
        timestamp: ISO format timestamp (default: current time)
    """
    if not metrics_data:
        return
    
    if timestamp is None:
        timestamp = datetime.utcnow().isoformat()
    
    conn = _connect_db()
    cursor = conn.cursor()
    
    for metric in metrics_data:
        cursor.execute('''
            INSERT INTO service_metrics (
                timestamp, environment, service, status, 
                requests, errors, error_rate,
                p95_latency, p99_latency, baseline_requests,
                traffic_drop, high_latency, pd_incident
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            timestamp,
            metric.get('environment', 'unknown'),
            metric.get('service', 'unknown'),
            metric.get('status', 'unknown'),
            metric.get('requests', 0),
            metric.get('errors', 0),
            metric.get('error_rate', 0.0),
            metric.get('p95_latency'),
            metric.get('p99_latency'),
            metric.get('baseline_requests'),
            1 if metric.get('traffic_drop', False) else 0,
            1 if metric.get('high_latency', False) else 0,
            1 if metric.get('pd_incident', False) else 0
        ))
    
    conn.commit()
    conn.close()
    print(f"💾 Saved {len(metrics_data)} service metrics to database")


def save_dashboard_snapshot(summary_data: Dict, timestamp: Optional[str] = None):
    """
    Save dashboard summary snapshot
    
    Args:
        summary_data: Dictionary with dashboard-level statistics
        timestamp: ISO format timestamp (default: current time)
    """
    if timestamp is None:
        timestamp = datetime.utcnow().isoformat()
    
    conn = _connect_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO dashboard_snapshots (
            timestamp, environment,
            total_services, healthy_count, warning_count, critical_count,
            total_requests, total_errors, overall_error_rate,
            pd_triggered, pd_acknowledged, pd_resolved
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        timestamp,
        summary_data.get('environment'),
        summary_data.get('total_services', 0),
        summary_data.get('healthy_count', 0),
        summary_data.get('warning_count', 0),
        summary_data.get('critical_count', 0),
        summary_data.get('total_requests', 0),
        summary_data.get('total_errors', 0),
        summary_data.get('overall_error_rate', 0.0),
        summary_data.get('pd_triggered', 0),
        summary_data.get('pd_acknowledged', 0),
        summary_data.get('pd_resolved', 0)
    ))
    
    conn.commit()
    conn.close()


def get_service_history(service: str, environment: str, hours: int = 24) -> List[Dict]:
    """
    Get historical metrics for a specific service
    
    Args:
        service: Service name
        environment: Environment name (production, goldendev, goldenqa)
        hours: Number of hours to look back (default: 24)
    
    Returns:
        List of metric dictionaries sorted by timestamp
    """
    conn = _connect_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cutoff_time = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    
    cursor.execute('''
        SELECT * FROM service_metrics
        WHERE service = ? AND environment = ? AND timestamp >= ?
        ORDER BY timestamp ASC
    ''', (service, environment, cutoff_time))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]


def get_dashboard_history(environment: Optional[str] = None, hours: int = 24) -> List[Dict]:
    """
    Get historical dashboard snapshots
    
    Args:
        environment: Optional environment filter
        hours: Number of hours to look back (default: 24)
    
    Returns:
        List of snapshot dictionaries sorted by timestamp
    """
    conn = _connect_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cutoff_time = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    
    if environment:
        cursor.execute('''
            SELECT * FROM dashboard_snapshots
            WHERE environment = ? AND timestamp >= ?
            ORDER BY timestamp ASC
        ''', (environment, cutoff_time))
    else:
        cursor.execute('''
            SELECT * FROM dashboard_snapshots
            WHERE timestamp >= ?
            ORDER BY timestamp ASC
        ''', (cutoff_time,))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]


def get_service_trends(service: str, environment: str, hours: int = 24) -> Dict:
    """
    Calculate trends for a specific service (avg, min, max)
    
    Args:
        service: Service name
        environment: Environment name
        hours: Number of hours to look back
    
    Returns:
        Dictionary with trend statistics
    """
    history = get_service_history(service, environment, hours)
    
    if not history:
        return {
            'service': service,
            'environment': environment,
            'data_points': 0,
            'avg_error_rate': 0,
            'max_error_rate': 0,
            'avg_requests': 0,
            'trend': 'no_data'
        }
    
    error_rates = [h['error_rate'] for h in history if h['error_rate'] is not None]
    requests = [h['requests'] for h in history if h['requests'] is not None]
    
    # Calculate trend (comparing first half vs second half)
    mid_point = len(history) // 2
    if mid_point > 0:
        first_half_errors = [h['error_rate'] for h in history[:mid_point]]
        second_half_errors = [h['error_rate'] for h in history[mid_point:]]
        
        avg_first = sum(first_half_errors) / len(first_half_errors) if first_half_errors else 0
        avg_second = sum(second_half_errors) / len(second_half_errors) if second_half_errors else 0
        
        if avg_second > avg_first * 1.2:
            trend = 'degrading'
        elif avg_second < avg_first * 0.8:
            trend = 'improving'
        else:
            trend = 'stable'
    else:
        trend = 'insufficient_data'
    
    return {
        'service': service,
        'environment': environment,
        'data_points': len(history),
        'avg_error_rate': sum(error_rates) / len(error_rates) if error_rates else 0,
        'max_error_rate': max(error_rates) if error_rates else 0,
        'min_error_rate': min(error_rates) if error_rates else 0,
        'avg_requests': sum(requests) / len(requests) if requests else 0,
        'max_requests': max(requests) if requests else 0,
        'trend': trend,
        'time_range_hours': hours
    }


def get_all_services_current_status() -> List[Dict]:
    """
    Get the most recent status for all services
    
    Returns:
        List of most recent metrics for each service
    """
    conn = _connect_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get the most recent timestamp
    cursor.execute('SELECT MAX(timestamp) as latest FROM service_metrics')
    latest_time = cursor.fetchone()['latest']
    
    if not latest_time:
        conn.close()
        return []
    
    # Get all services from that timestamp
    cursor.execute('''
        SELECT * FROM service_metrics
        WHERE timestamp = ?
        ORDER BY environment, service
    ''', (latest_time,))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]


def cleanup_old_data(days: int = 30):
    """
    Remove data older than specified days
    
    Args:
        days: Number of days to retain (default: 30)
    """
    conn = _connect_db()
    cursor = conn.cursor()
    
    cutoff_time = (datetime.utcnow() - timedelta(days=days)).isoformat()
    
    cursor.execute('DELETE FROM service_metrics WHERE timestamp < ?', (cutoff_time,))
    deleted_metrics = cursor.rowcount
    
    cursor.execute('DELETE FROM dashboard_snapshots WHERE timestamp < ?', (cutoff_time,))
    deleted_snapshots = cursor.rowcount
    
    conn.commit()
    conn.close()
    
    print(f"🧹 Cleaned up old data: {deleted_metrics} metrics, {deleted_snapshots} snapshots")
    return {'deleted_metrics': deleted_metrics, 'deleted_snapshots': deleted_snapshots}


def get_critical_services_history(hours: int = 24) -> List[Dict]:
    """
    Get all critical status occurrences in the time range
    
    Args:
        hours: Number of hours to look back
    
    Returns:
        List of critical service occurrences
    """
    conn = _connect_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cutoff_time = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    
    cursor.execute('''
        SELECT * FROM service_metrics
        WHERE status = 'critical' AND timestamp >= ?
        ORDER BY timestamp DESC
    ''', (cutoff_time,))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]


def get_database_stats() -> Dict:
    """Get statistics about the database"""
    if not os.path.exists(DB_PATH):
        return {'exists': False}
    
    conn = _connect_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) as count FROM service_metrics')
    metrics_count = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) as count FROM dashboard_snapshots')
    snapshots_count = cursor.fetchone()[0]
    
    cursor.execute('SELECT MIN(timestamp) as oldest, MAX(timestamp) as newest FROM service_metrics')
    row = cursor.fetchone()
    oldest, newest = row
    
    file_size = os.path.getsize(DB_PATH)
    
    conn.close()
    
    return {
        'exists': True,
        'path': DB_PATH,
        'file_size_mb': round(file_size / (1024 * 1024), 2),
        'total_metrics': metrics_count,
        'total_snapshots': snapshots_count,
        'oldest_record': oldest,
        'newest_record': newest
    }


def save_pagerduty_incident(incident_data: Dict, timestamp: Optional[str] = None):
    """
    Save PagerDuty incident to database
    
    Args:
        incident_data: Incident dictionary with id, number, title, status, etc.
        timestamp: ISO format timestamp (default: current time)
    """
    if timestamp is None:
        timestamp = datetime.utcnow().isoformat()
    
    conn = _connect_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR REPLACE INTO pagerduty_incidents 
        (incident_id, incident_number, title, status, urgency, created_at, resolved_at, 
         service_id, service_name, affected_services, duration_minutes, assignees)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        incident_data.get('id'),
        incident_data.get('incident_number'),
        incident_data.get('title'),
        incident_data.get('status'),
        incident_data.get('urgency'),
        incident_data.get('created_at'),
        incident_data.get('resolved_at'),
        incident_data.get('service_id'),
        incident_data.get('service_name'),
        json.dumps(incident_data.get('affected_services', [])),
        incident_data.get('duration_minutes'),
        json.dumps(incident_data.get('assignees', []))
    ))
    
    conn.commit()
    conn.close()


def save_state_change(service_name: str, environment: str, previous_state: str, 
                     new_state: str, trigger_reason: str, error_rate: float = None, 
                     latency_p95: float = None, timestamp: Optional[str] = None):
    """Save service state change to database"""
    if timestamp is None:
        timestamp = datetime.utcnow().isoformat()
    
    conn = _connect_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO service_state_changes 
        (timestamp, service_name, environment, previous_state, new_state, 
         trigger_reason, error_rate, latency_p95)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (timestamp, service_name, environment, previous_state, new_state, 
          trigger_reason, error_rate, latency_p95))
    
    conn.commit()
    conn.close()


def save_baseline(service_name: str, environment: str, week_start: str,
                 avg_error_rate: float, avg_latency_p95: float, 
                 avg_traffic_rpm: float, peak_traffic_rpm: float, 
                 incidents_count: int = 0):
    """Save weekly baseline metrics"""
    conn = _connect_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR REPLACE INTO service_baselines 
        (service_name, environment, week_start, avg_error_rate, avg_latency_p95,
         avg_traffic_rpm, peak_traffic_rpm, incidents_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (service_name, environment, week_start, avg_error_rate, avg_latency_p95,
          avg_traffic_rpm, peak_traffic_rpm, incidents_count))
    
    conn.commit()
    conn.close()


def save_deployment(service_name: str, environment: str, version: str,
                   deployer: str, status: str, duration_seconds: int = None,
                   timestamp: Optional[str] = None):
    """Save deployment record"""
    if timestamp is None:
        timestamp = datetime.utcnow().isoformat()
    
    conn = _connect_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO deployments 
        (timestamp, service_name, environment, version, deployer, status, duration_seconds)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (timestamp, service_name, environment, version, deployer, status, duration_seconds))
    
    conn.commit()
    conn.close()


def save_outage(service_name: str, environment: str, start_time: str,
               end_time: str = None, duration_minutes: int = None,
               severity: str = 'critical', root_cause: str = None,
               pagerduty_incident_id: str = None):
    """Save service outage record"""
    conn = _connect_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO service_outages 
        (service_name, environment, start_time, end_time, duration_minutes,
         severity, root_cause, pagerduty_incident_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (service_name, environment, start_time, end_time, duration_minutes,
          severity, root_cause, pagerduty_incident_id))
    
    conn.commit()
    conn.close()


def save_tool_usage(tool_name: str, query_text: str = None, user_ip: str = None,
                   response_time_ms: int = None, success: bool = True,
                   timestamp: Optional[str] = None):
    """Save tool usage analytics"""
    if timestamp is None:
        timestamp = datetime.utcnow().isoformat()
    
    conn = _connect_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO tool_usage 
        (timestamp, tool_name, query_text, user_ip, response_time_ms, success)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (timestamp, tool_name, query_text, user_ip, response_time_ms, success))
    
    conn.commit()
    conn.close()


def get_recent_incidents(hours: int = 24, service_name: str = None) -> List[Dict]:
    """Get recent PagerDuty incidents from database"""
    conn = _connect_db()
    cursor = conn.cursor()
    
    cutoff_time = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    
    if service_name:
        cursor.execute('''
            SELECT incident_id, incident_number, title, status, urgency, 
                   created_at, resolved_at, service_name, duration_minutes
            FROM pagerduty_incidents
            WHERE created_at >= ? AND (service_name = ? OR affected_services LIKE ?)
            ORDER BY created_at DESC
        ''', (cutoff_time, service_name, f'%{service_name}%'))
    else:
        cursor.execute('''
            SELECT incident_id, incident_number, title, status, urgency,
                   created_at, resolved_at, service_name, duration_minutes
            FROM pagerduty_incidents
            WHERE created_at >= ?
            ORDER BY created_at DESC
        ''', (cutoff_time,))
    
    incidents = []
    for row in cursor.fetchall():
        incidents.append({
            'incident_id': row[0],
            'incident_number': row[1],
            'title': row[2],
            'status': row[3],
            'urgency': row[4],
            'created_at': row[5],
            'resolved_at': row[6],
            'service_name': row[7],
            'duration_minutes': row[8]
        })
    
    conn.close()
    return incidents


def get_state_changes(service_name: str, environment: str, hours: int = 24) -> List[Dict]:
    """Get state changes for a service"""
    conn = _connect_db()
    cursor = conn.cursor()
    
    cutoff_time = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    
    cursor.execute('''
        SELECT timestamp, previous_state, new_state, trigger_reason, error_rate, latency_p95
        FROM service_state_changes
        WHERE service_name = ? AND environment = ? AND timestamp >= ?
        ORDER BY timestamp DESC
    ''', (service_name, environment, cutoff_time))
    
    changes = []
    for row in cursor.fetchall():
        changes.append({
            'timestamp': row[0],
            'previous_state': row[1],
            'new_state': row[2],
            'trigger_reason': row[3],
            'error_rate': row[4],
            'latency_p95': row[5]
        })
    
    conn.close()
    return changes


def sm_api_cache_get(kind: str, key: str, max_age_secs: float) -> Optional[Any]:
    """
    Status monitor: read short-lived cached JSON (Datadog health row, PagerDuty blob, Arlo list).
    Returns None if missing, too old, or on error.
    """
    if max_age_secs <= 0:
        return None
    try:
        cutoff = time.time() - float(max_age_secs)
        conn = _connect_db(timeout=30)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT payload_json, updated_at FROM status_monitor_api_cache
            WHERE cache_kind = ? AND cache_key = ?
            """,
            (kind, key),
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        payload_json, updated_at = row
        if updated_at < cutoff:
            return None
        return json.loads(payload_json)
    except Exception as e:
        print(f"⚠️ sm_api_cache_get ({kind}): {e}")
        return None


def sm_api_cache_set(kind: str, key: str, payload: Any) -> None:
    """Persist JSON-serializable payload for reuse within TTL window."""
    try:
        blob = json.dumps(payload, default=str)
        now = time.time()
        conn = _connect_db(timeout=30)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO status_monitor_api_cache (cache_kind, cache_key, payload_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(cache_kind, cache_key) DO UPDATE SET
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (kind, key, blob, now),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ sm_api_cache_set ({kind}): {e}")


def get_service_eks_clusters(service_name: str, environment: str) -> Optional[tuple[list[str], float]]:
    """
    Returns (cluster_names, updated_at_unix) if a row exists, else None.
    Cluster names are non-empty strings; empty list is stored when DD returned no clusters.
    """
    try:
        conn = _connect_db(timeout=30)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT clusters_json, updated_at FROM service_eks_clusters
            WHERE service_name = ? AND environment = ?
            """,
            (service_name, environment),
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        raw, updated_at = row
        data = json.loads(raw)
        if not isinstance(data, list):
            return None
        clusters = [str(x).strip() for x in data if x is not None and str(x).strip()]
        return (clusters, float(updated_at))
    except Exception as e:
        print(f"⚠️ get_service_eks_clusters: {e}")
        return None


def set_service_eks_clusters(service_name: str, environment: str, clusters: list[str]) -> None:
    """Upsert resolved cluster names for reuse across dashboard/wall loads."""
    try:
        blob = json.dumps([str(x).strip() for x in clusters if x is not None and str(x).strip()], ensure_ascii=False)
        now = time.time()
        conn = _connect_db(timeout=30)
        conn.execute(
            """
            INSERT INTO service_eks_clusters (service_name, environment, clusters_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(service_name, environment) DO UPDATE SET
                clusters_json = excluded.clusters_json,
                updated_at = excluded.updated_at
            """,
            (service_name, environment, blob, now),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ set_service_eks_clusters: {e}")


def clear_status_monitor_api_cache() -> None:
    """Wipe status-monitor API cache (Datadog / PagerDuty / Arlo short TTL rows)."""
    try:
        conn = _connect_db(timeout=30)
        conn.execute("DELETE FROM status_monitor_api_cache")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ clear_status_monitor_api_cache: {e}")


# Initialize database on module load
try:
    init_database()
except Exception as e:
    print(f"⚠️ Database initialization error: {e}")
