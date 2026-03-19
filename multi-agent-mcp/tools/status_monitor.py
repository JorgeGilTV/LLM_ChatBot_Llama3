"""
Status Monitor Dashboard Tool
Real-time service health monitoring across all environments using Datadog APM
"""

import os
import time
import json
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import metrics persistence
from tools.metrics_persistence import save_service_metrics, save_dashboard_snapshot

# Import Datadog dashboard utilities
from tools.datadog_dashboards import get_dashboard_details

# Simple in-memory cache for status monitor data
_status_cache = {}
_cache_ttl = 120  # 2 minutes cache

# Cache for dashboard services (so we don't fetch dashboard details every time)
_dashboard_services_cache = {}
_dashboard_services_cache_ttl = 3600  # 1 hour

def clear_status_cache():
    """Clear the status monitor cache - useful after config changes"""
    global _status_cache
    _status_cache.clear()
    print("🧹 Status monitor cache cleared")


def get_services_from_dashboard(dashboard_id: str, cache_key: str = None) -> list:
    """
    Extract all service names from a Datadog dashboard dynamically
    
    Args:
        dashboard_id: Datadog dashboard ID (e.g., 'cum-ivw-92c' for ADT)
        cache_key: Optional cache key (default: dashboard_id)
    
    Returns:
        List of service names found in the dashboard
    """
    if cache_key is None:
        cache_key = dashboard_id
    
    # Check cache first
    if cache_key in _dashboard_services_cache:
        cached_data = _dashboard_services_cache[cache_key]
        if time.time() - cached_data['timestamp'] < _dashboard_services_cache_ttl:
            print(f"📦 Using cached services for dashboard {dashboard_id}: {len(cached_data['services'])} services")
            return cached_data['services']
    
    # Fetch dashboard details
    dd_api_key = os.getenv("DATADOG_API_KEY")
    dd_app_key = os.getenv("DATADOG_APP_KEY")
    dd_site = os.getenv("DATADOG_SITE", "datadoghq.com")
    
    if not dd_api_key or not dd_app_key:
        print("⚠️ Datadog credentials not available, using fallback service list")
        return []
    
    try:
        print(f"🔍 Fetching services from Datadog dashboard {dashboard_id}...")
        details = get_dashboard_details(dd_api_key, dd_app_key, dd_site, dashboard_id)
        
        if not details or 'widgets' not in details:
            print(f"⚠️ Could not fetch dashboard {dashboard_id}")
            return []
        
        services = set()
        
        # Helper function to extract service from queries
        def extract_services_from_queries(queries):
            if not queries:
                return []
            found = []
            for query in queries:
                if isinstance(query, dict):
                    query_str = query.get('query', '')
                elif isinstance(query, str):
                    query_str = query
                else:
                    continue
                
                # Extract service from query string like "service:backend-pp" or "service:backend-pp-samsung-prod"
                import re
                matches = re.findall(r'service:([a-zA-Z0-9\-_]+)', query_str)
                found.extend(matches)
            return found
        
        # Extract services from all widgets (recursive for nested widgets)
        def process_widget(widget_def, depth=0):
            """Process a single widget and extract services (handles nested widgets)"""
            widget_type = widget_def.get('type', '')
            found = []
            
            # Check trace_service widgets
            if widget_type == 'trace_service':
                service = widget_def.get('service', '')
                if service:
                    found.append(service)
            
            # Check all query-based widgets
            if widget_type in ['timeseries', 'query_value', 'query_table', 'toplist', 'heatmap', 'distribution', 'change']:
                # Extract from queries
                requests = widget_def.get('requests', [])
                found.extend(extract_services_from_queries(requests))
                
                # Also check formulas/queries array
                if 'queries' in widget_def:
                    found.extend(extract_services_from_queries(widget_def['queries']))
                
                # Extract service name from widget title (e.g., "backend-hmsguard -> Requests")
                title = widget_def.get('title', '')
                if title:
                    import re
                    title_match = re.match(r'^([a-zA-Z0-9\-_]+)\s*->', title)
                    if title_match:
                        service_name = title_match.group(1)
                        found.append(service_name)
            
            # Check group widgets (contain nested widgets)
            if widget_type == 'group':
                nested_widgets = widget_def.get('widgets', [])
                for nested_widget in nested_widgets:
                    nested_def = nested_widget.get('definition', {})
                    found.extend(process_widget(nested_def, depth + 1))
            
            # Check powerpack widgets (may contain nested widgets)
            if widget_type == 'powerpack':
                template_variables = widget_def.get('template_variables', [])
                for var in template_variables:
                    if isinstance(var, dict) and 'defaults' in var:
                        defaults = var['defaults']
                        if isinstance(defaults, list):
                            for default in defaults:
                                if isinstance(default, str) and not default.startswith('$'):
                                    found.append(default)
            
            return found
        
        # Process all top-level widgets
        for widget in details.get('widgets', []):
            widget_def = widget.get('definition', {})
            widget_type = widget_def.get('type', '')
            
            # Process this widget
            found = process_widget(widget_def)
            services.update(found)
            
            # Check group widgets (nested widgets)
            if widget_type == 'group':
                for group_widget in widget_def.get('widgets', []):
                    group_def = group_widget.get('definition', {})
                    found = process_widget(group_def)
                    services.update(found)
            
            # Check split_graph widgets (also can have nested widgets)
            if widget_type == 'split_graph':
                for split_widget in widget_def.get('source_widget_definition', {}).get('widgets', []):
                    split_def = split_widget.get('definition', {})
                    found = process_widget(split_def)
                    services.update(found)
        
        services_list = sorted(list(services))
        print(f"✅ Found {len(services_list)} services in dashboard {dashboard_id}")
        
        # Cache the results
        _dashboard_services_cache[cache_key] = {
            'services': services_list,
            'timestamp': time.time()
        }
        
        return services_list
        
    except Exception as e:
        print(f"⚠️ Error fetching services from dashboard {dashboard_id}: {e}")
        return []


def get_aws_costs_and_changes(days=1):
    """Get AWS costs and recent changes from CloudTrail"""
    try:
        import boto3
        from datetime import datetime, timedelta
        
        # AWS credentials from environment
        aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
        aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        aws_region = os.getenv("AWS_REGION", "us-east-1")
        
        if not aws_access_key or not aws_secret_key:
            print("⚠️ AWS credentials not configured")
            return {
                "cost_today": 0,
                "cost_yesterday": 0,
                "recent_changes": [],
                "error": "No credentials"
            }
        
        # Initialize AWS clients
        session = boto3.Session(
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=aws_region
        )
        
        # Get costs using Cost Explorer
        ce_client = session.client('ce', region_name='us-east-1')  # Cost Explorer is always us-east-1
        
        today = datetime.now().date()
        yesterday = today - timedelta(days=1)
        
        # Query today's cost
        cost_response = ce_client.get_cost_and_usage(
            TimePeriod={
                'Start': str(yesterday),
                'End': str(today)
            },
            Granularity='DAILY',
            Metrics=['UnblendedCost']
        )
        
        cost_yesterday = 0
        if cost_response['ResultsByTime']:
            cost_yesterday = float(cost_response['ResultsByTime'][0]['Total']['UnblendedCost']['Amount'])
        
        # Get recent CloudTrail events (last 24h)
        cloudtrail_client = session.client('cloudtrail', region_name=aws_region)
        
        start_time = datetime.now() - timedelta(days=days)
        events_response = cloudtrail_client.lookup_events(
            LookupAttributes=[
                {'AttributeKey': 'ReadOnly', 'AttributeValue': 'false'}
            ],
            StartTime=start_time,
            MaxResults=10
        )
        
        recent_changes = []
        for event in events_response.get('Events', [])[:5]:
            recent_changes.append({
                'event_name': event.get('EventName', 'Unknown'),
                'user': event.get('Username', 'Unknown'),
                'time': event.get('EventTime').strftime('%H:%M:%S') if event.get('EventTime') else 'Unknown',
                'resource': event.get('Resources', [{}])[0].get('ResourceName', 'N/A') if event.get('Resources') else 'N/A'
            })
        
        print(f"✅ AWS: ${cost_yesterday:.2f} yesterday, {len(recent_changes)} recent changes")
        
        return {
            "cost_yesterday": cost_yesterday,
            "recent_changes": recent_changes,
            "error": None
        }
        
    except Exception as e:
        print(f"❌ Error fetching AWS data: {e}")
        return {
            "cost_today": 0,
            "cost_yesterday": 0,
            "recent_changes": [],
            "error": str(e)
        }


def get_splunk_infra_exceptions(timerange_hours=4):
    """Get US Infrastructure Exceptions count from Splunk"""
    try:
        import requests
        
        splunk_host = os.getenv("SPLUNK_HOST", "arlo.splunkcloud.com")
        splunk_token = os.getenv("SPLUNK_TOKEN")
        
        if not splunk_token:
            print("⚠️ Splunk token not configured")
            return 0, []
        
        # Query for US infra exceptions
        search_query = f'''search index=* (exception OR Exception OR ERROR OR error)
earliest=-{timerange_hours}h latest=now
| stats count by service, error_message
| sort -count
| head 10'''
        
        headers = {
            "Authorization": f"Bearer {splunk_token}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        search_url = f"https://{splunk_host}:8089/services/search/jobs/export"
        data = {
            "search": search_query,
            "earliest_time": f"-{timerange_hours}h",
            "latest_time": "now",
            "output_mode": "json"
        }
        
        print(f"🔍 Querying Splunk for US Infra Exceptions (last {timerange_hours}h)...")
        response = requests.post(search_url, headers=headers, data=data, verify=True, timeout=(15, 60))
        
        if response.status_code == 200:
            results = []
            total_count = 0
            for line in response.text.strip().split('\n'):
                if line:
                    try:
                        result = json.loads(line)
                        if result.get("result") and result.get("preview") == False:
                            res_data = result["result"]
                            results.append(res_data)
                            total_count += int(res_data.get("count", 0))
                    except json.JSONDecodeError:
                        continue
            print(f"✅ Found {total_count} US Infra Exceptions")
            return total_count, results[:10]
        else:
            print(f"❌ Splunk API returned status {response.status_code}")
            return 0, []
    except Exception as e:
        print(f"❌ Error fetching Splunk Infra Exceptions: {e}")
        return 0, []


def get_splunk_outliers(timerange_hours=4):
    """Get outliers/anomalies from Splunk for key services"""
    try:
        import requests
        
        splunk_host = os.getenv("SPLUNK_HOST", "arlo.splunkcloud.com")
        splunk_token = os.getenv("SPLUNK_TOKEN")
        
        if not splunk_token:
            print("⚠️ Splunk token not configured")
            return []
        
        # Query for top errors/exceptions across streaming, advisor, oauth services
        # More specific: only ERROR/FATAL/CRITICAL log levels and exceptions, with minimum threshold
        search_query = f'''search index=streaming_prod OR index=advisor_prod OR index=oauth_prod OR index=aria_prod 
(log_level=ERROR OR log_level=FATAL OR log_level=CRITICAL)
earliest=-{timerange_hours}h latest=now
| rex field=_raw "(?<error_type>Exception|Error|Failed|Timeout|Unavailable)"
| eval service=coalesce(service, sourcetype, "Unknown")
| stats count by service, error_type
| where count > 5
| sort -count
| head 8'''
        
        headers = {
            "Authorization": f"Bearer {splunk_token}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        search_url = f"https://{splunk_host}:8089/services/search/jobs/export"
        data = {
            "search": search_query,
            "earliest_time": f"-{timerange_hours}h",
            "latest_time": "now",
            "output_mode": "json"
        }
        
        print(f"🔍 Querying Splunk for outliers (last {timerange_hours}h)...")
        response = requests.post(search_url, headers=headers, data=data, verify=True, timeout=(15, 90))
        
        if response.status_code == 200:
            results = []
            for line in response.text.strip().split('\n'):
                if line:
                    try:
                        result = json.loads(line)
                        if result.get("result") and result.get("preview") == False:
                            results.append(result["result"])
                    except json.JSONDecodeError:
                        continue
            print(f"✅ Found {len(results)} Splunk outliers")
            return results[:8]  # Return top 8 outliers
        else:
            print(f"❌ Splunk API returned status {response.status_code}")
            return []
    except Exception as e:
        print(f"❌ Error fetching Splunk outliers: {e}")
        return []


def get_service_health_status(service_name, environment, dd_api_key, dd_app_key, dd_site, from_time, to_time, enable_extended_metrics=False):
    """Get comprehensive health status for a single service using multiple Datadog APM metrics
    
    Args:
        enable_extended_metrics: If True, fetch latency and baseline (slower but more comprehensive)
    """
    try:
        import requests
        
        headers = {
            "DD-API-KEY": dd_api_key,
            "DD-APPLICATION-KEY": dd_app_key
        }
        
        # Initialize metrics
        requests_count = 0
        errors_count = 0
        p95_latency = None
        p99_latency = None
        baseline_requests = 0
        baseline_from = 0
        baseline_to = 0
        
        # Use primary metric pattern (servlet) - optimized for speed
        # Only try alternative patterns if extended metrics are enabled
        if enable_extended_metrics:
            metric_patterns = [
                ('trace.servlet.request.hits', 'trace.servlet.request.errors', 'trace.servlet.request.duration.by.service.95p'),
                ('trace.http.request.hits', 'trace.http.request.errors', 'trace.http.request.duration.by.service.95p'),
                ('trace.web.request.hits', 'trace.web.request.errors', 'trace.web.request.duration.by.service.95p'),
            ]
        else:
            # Fast mode: only try servlet pattern
            metric_patterns = [
                ('trace.servlet.request.hits', 'trace.servlet.request.errors', 'trace.servlet.request.duration.by.service.95p'),
            ]
        
        # Try each pattern until we get data
        for hits_metric, errors_metric, latency_metric in metric_patterns:
            query = f"sum:{hits_metric}{{service:{service_name},env:{environment}}}.as_count()"
            params = {
                "from": from_time,
                "to": to_time,
                "query": query
            }
            
            # Get request count (fast timeout for better performance)
            response = requests.get(
                f"https://{dd_site}/api/v1/query",
                headers=headers,
                params=params,
                timeout=5
            )
            
            if response.status_code == 200:
                data = response.json()
                if 'series' in data and len(data['series']) > 0:
                    points = data['series'][0].get('pointlist', [])
                    if points:
                        requests_count = sum(p[1] for p in points if p[1] is not None)
                        
                        # If we got data, also fetch errors with this pattern
                        if requests_count > 0 or enable_extended_metrics:
                            # Get error count
                            params['query'] = f"sum:{errors_metric}{{service:{service_name},env:{environment}}}.as_count()"
                            err_response = requests.get(
                                f"https://{dd_site}/api/v1/query",
                                headers=headers,
                                params=params,
                                timeout=5
                            )
                            
                            if err_response.status_code == 200:
                                err_data = err_response.json()
                                if 'series' in err_data and len(err_data['series']) > 0:
                                    err_points = err_data['series'][0].get('pointlist', [])
                                    if err_points:
                                        errors_count = sum(p[1] for p in err_points if p[1] is not None)
                            
                            # Only fetch extended metrics if enabled (to improve performance)
                            if enable_extended_metrics:
                                # Try multiple latency metric patterns
                                latency_patterns_to_try = [
                                    (latency_metric, 'primary'),
                                    (latency_metric.replace('.by.service.', '.by.resource_service.'), 'resource_service'),
                                ]
                                
                                for lat_metric_pattern, pattern_name in latency_patterns_to_try:
                                    # Get p95 latency
                                    params['query'] = f"avg:{lat_metric_pattern}{{service:{service_name},env:{environment}}}"
                                    
                                    print(f"   🔍 Trying latency metric ({pattern_name}): {lat_metric_pattern}")
                                    
                                    lat_response = requests.get(
                                        f"https://{dd_site}/api/v1/query",
                                        headers=headers,
                                        params=params,
                                        timeout=5
                                    )
                                    
                                    if lat_response.status_code == 200:
                                        lat_data = lat_response.json()
                                        print(f"   📡 Response status: 200, series count: {len(lat_data.get('series', []))}")
                                        
                                        if 'series' in lat_data and len(lat_data['series']) > 0:
                                            lat_points = lat_data['series'][0].get('pointlist', [])
                                            print(f"   📊 Points received: {len(lat_points)}")
                                            
                                            if lat_points:
                                                valid_latencies = [p[1] for p in lat_points if p[1] is not None and p[1] > 0]
                                                print(f"   📊 Valid latency points: {len(valid_latencies)}, values: {valid_latencies[:5] if len(valid_latencies) > 5 else valid_latencies}")
                                                
                                                if valid_latencies:
                                                    avg_latency = sum(valid_latencies) / len(valid_latencies)
                                                    
                                                    # Datadog APM duration.by.service metrics ALWAYS return in SECONDS
                                                    # Convert to milliseconds (check if already seems to be in ms to avoid double conversion)
                                                    if avg_latency > 100:
                                                        # Values > 100 are likely already in milliseconds (100s = 100000ms is unrealistic)
                                                        p95_latency = avg_latency
                                                        print(f"   ✅ {service_name} ({environment}): P95 = {p95_latency:.2f}ms (detected as already in ms)")
                                                    else:
                                                        # Standard case: convert seconds to milliseconds
                                                        p95_latency = avg_latency * 1000
                                                        print(f"   ✅ {service_name} ({environment}): P95 = {p95_latency:.2f}ms (converted from {avg_latency:.3f}s)")
                                                    
                                                    print(f"      Pattern: {pattern_name}, Data points: {len(valid_latencies)}")
                                                    break  # Found data, stop trying patterns
                                    else:
                                        print(f"   ⚠️  API returned status: {lat_response.status_code}")
                                
                                # Get baseline (7 days) for traffic comparison - weekly average is more stable
                                baseline_from = from_time - (7 * 86400)  # 7 days before current period
                                baseline_to = from_time  # Up to the start of current period
                                params['query'] = query
                                params['from'] = baseline_from
                                params['to'] = baseline_to
                                
                                print(f"   📊 Fetching 7-day baseline for traffic comparison...")
                                
                                baseline_response = requests.get(
                                    f"https://{dd_site}/api/v1/query",
                                    headers=headers,
                                    params=params,
                                    timeout=8
                                )
                                
                                if baseline_response.status_code == 200:
                                    baseline_data = baseline_response.json()
                                    if 'series' in baseline_data and len(baseline_data['series']) > 0:
                                        baseline_points = baseline_data['series'][0].get('pointlist', [])
                                        if baseline_points:
                                            baseline_requests = sum(p[1] for p in baseline_points if p[1] is not None)
                                            print(f"   📈 Baseline (7 days): {baseline_requests:,} requests")
                            
                            break  # Found working metric pattern
        
        # Calculate error rate
        error_rate = (errors_count / requests_count * 100) if requests_count > 0 else 0
        
        # Detect traffic drop (> 85% drop from weekly average) - only if extended metrics enabled
        traffic_drop = False
        if enable_extended_metrics and baseline_requests > 5000:  # Only check if baseline had significant traffic over the week
            # Calculate rates per hour for comparison
            current_time_window_hours = (to_time - from_time) / 3600
            baseline_time_window_hours = (baseline_to - baseline_from) / 3600  # 7 days = 168 hours
            
            current_rate = requests_count / current_time_window_hours  # requests per hour (current)
            baseline_avg_rate = baseline_requests / baseline_time_window_hours  # requests per hour (7-day avg)
            
            # Compare current rate against weekly average
            # Only flag if MAJOR drop (>85%) compared to weekly pattern
            if baseline_avg_rate > 0 and current_rate < (baseline_avg_rate * 0.15):  # 85% drop
                drop_percentage = ((baseline_avg_rate - current_rate) / baseline_avg_rate) * 100
                traffic_drop = True
                print(f"   🚨 TRAFFIC DROP: {service_name} current={current_rate:.0f}/h vs 7-day avg={baseline_avg_rate:.0f}/h (drop: {drop_percentage:.0f}%)")
            else:
                # Log comparison for monitoring (even if not critical)
                if baseline_avg_rate > 0:
                    variance = ((current_rate - baseline_avg_rate) / baseline_avg_rate) * 100
                    print(f"   📊 {service_name} traffic: current={current_rate:.0f}/h vs 7-day avg={baseline_avg_rate:.0f}/h (variance: {variance:+.0f}%)")
        
        # Detect high latency - only if extended metrics enabled
        high_latency = False
        if enable_extended_metrics:
            if p95_latency and p95_latency > 2000:  # 2 seconds in ms
                high_latency = True
            if p99_latency and p99_latency > 5000:  # 5 seconds in ms
                high_latency = True
        
        # Determine status based on multiple factors
        # Be more conservative to avoid false positives
        if requests_count == 0:
            status = 'inactive'
        elif traffic_drop:
            status = 'critical'  # Sudden traffic drop is critical
        elif error_rate > 5:  # Increased from 3% to 5% to be less aggressive
            status = 'critical'
            print(f"   🚨 {service_name} ({environment}): CRITICAL - Error rate {error_rate:.2f}% (>{requests_count:,} requests, {errors_count:,} errors)")
        elif error_rate > 1:  # Increased from 0.5% to 1%
            status = 'warning'
            print(f"   ⚠️  {service_name} ({environment}): WARNING - Error rate {error_rate:.2f}%")
        elif high_latency:
            status = 'warning'
            print(f"   ⚠️  {service_name} ({environment}): WARNING - High latency {p95_latency:.0f}ms")
        else:
            status = 'healthy'
        
        # Calculate traffic variance for context
        traffic_variance = None
        if enable_extended_metrics and baseline_requests > 0:
            current_time_window_hours = (to_time - from_time) / 3600
            baseline_time_window_hours = (baseline_to - baseline_from) / 3600 if baseline_from > 0 else 168
            current_rate = requests_count / current_time_window_hours
            baseline_avg_rate = baseline_requests / baseline_time_window_hours
            if baseline_avg_rate > 0:
                traffic_variance = ((current_rate - baseline_avg_rate) / baseline_avg_rate) * 100
        
        return {
            'service': service_name,
            'environment': environment,
            'status': status,
            'requests': int(requests_count),
            'errors': int(errors_count),
            'error_rate': round(error_rate, 2),
            'p95_latency': round(p95_latency, 2) if p95_latency else None,
            'p99_latency': round(p99_latency, 2) if p99_latency else None,
            'traffic_drop': traffic_drop,
            'high_latency': high_latency,
            'baseline_requests': int(baseline_requests),
            'traffic_variance': round(traffic_variance, 1) if traffic_variance is not None else None
        }
        
    except Exception as e:
        print(f"Error fetching status for {service_name} in {environment}: {e}")
        return {
            'service': service_name,
            'environment': environment,
            'status': 'unknown',
            'requests': 0,
            'errors': 0,
            'error_rate': 0,
            'p95_latency': None,
            'p99_latency': None,
            'traffic_drop': False,
            'high_latency': False,
            'baseline_requests': 0
        }


def get_pagerduty_incidents_count(pd_api_key: str):
    """Get count of active PagerDuty incidents"""
    try:
        import requests
        headers = {
            "Authorization": f"Token token={pd_api_key}",
            "Accept": "application/vnd.pagerduty+json;version=2"
        }
        
        # Get triggered and acknowledged incidents (active)
        params = {
            "statuses[]": ["triggered", "acknowledged"],
            "limit": 100
        }
        
        response = requests.get(
            "https://api.pagerduty.com/incidents",
            headers=headers,
            params=params,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            incidents = data.get("incidents", [])
            return len(incidents)
        return 0
    except:
        return 0


def get_pagerduty_status_counts(pd_api_key: str):
    """Get PagerDuty incidents counts by status - OPTIMIZED for speed"""
    try:
        import requests
        from datetime import timedelta
        headers = {
            "Authorization": f"Token token={pd_api_key}",
            "Accept": "application/vnd.pagerduty+json;version=2"
        }

        since = (datetime.utcnow() - timedelta(hours=24)).isoformat()
        statuses = ["triggered", "acknowledged", "resolved"]
        counts = {}
        incidents_by_status = {
            "triggered": [],
            "acknowledged": [],
            "resolved": []
        }

        for status in statuses:
            params = {
                "statuses[]": [status],
                "since": since,
                "limit": 10,  # Get only recent 10 for display
                "sort_by": "created_at:desc",
                "total": "true"
            }

            response = requests.get(
                "https://api.pagerduty.com/incidents",
                headers=headers,
                params=params,
                timeout=8
            )

            if response.status_code == 200:
                data = response.json()
                counts[status] = data.get("total", 0) if data.get("total") else len(data.get("incidents", []))
                incidents_by_status[status] = data.get("incidents", [])
            else:
                counts[status] = 0
        
        # Return ALL incidents for display purposes (for the widget lists)
        recent_incidents_display = (
            incidents_by_status["triggered"][:5] + 
            incidents_by_status["acknowledged"][:5] + 
            incidents_by_status["resolved"][:5]
        )
        
        # But only return ACTIVE incidents (triggered + acknowledged) for service correlation
        active_incidents = (
            incidents_by_status["triggered"] + 
            incidents_by_status["acknowledged"]
        )
        
        print(f"✅ PagerDuty Status: {counts['triggered']} triggered, {counts['acknowledged']} acknowledged, {counts['resolved']} resolved (last 24h)")
        print(f"🔗 Active incidents for correlation: {len(active_incidents)} (triggered + acknowledged only)")
        return counts, active_incidents
        
    except Exception as e:
        print(f"❌ Error fetching PagerDuty status counts: {e}")
        return {"triggered": 0, "acknowledged": 0, "resolved": 0}, []


def get_service_clusters_from_metrics(service_name: str, env: str, timerange_hours: int = 1):
    """
    Get ALL EKS cluster names where a service is running
    
    Args:
        service_name: Service name
        env: Environment tag
        timerange_hours: Hours to look back
    
    Returns:
        List of cluster names or empty list
    """
    try:
        import requests
        
        dd_api_key = os.getenv("DATADOG_API_KEY")
        dd_app_key = os.getenv("DATADOG_APP_KEY")
        dd_site = os.getenv("DATADOG_SITE", "datadoghq.com")
        
        if not dd_api_key or not dd_app_key:
            return []
        
        current_time = int(time.time())
        from_time = current_time - (timerange_hours * 3600)
        
        # Try multiple metrics to find cluster info
        # Order: APM traces first (most accurate), then Kubernetes metrics, then system metrics
        metrics_to_try = [
            # APM Trace metrics (best for application services)
            f"avg:trace.servlet.request.hits{{service:{service_name},env:{env}}} by {{kube_cluster_name}}",
            f"avg:trace.flask.request.hits{{service:{service_name},env:{env}}} by {{kube_cluster_name}}",
            f"avg:trace.http.request.hits{{service:{service_name},env:{env}}} by {{kube_cluster_name}}",
            f"avg:trace.web.request{{service:{service_name},env:{env}}} by {{kube_cluster_name}}",
            f"avg:trace.express.request{{service:{service_name},env:{env}}} by {{kube_cluster_name}}",
            f"avg:trace.django.request{{service:{service_name},env:{env}}} by {{kube_cluster_name}}",
            # Kubernetes pod metrics (good for all k8s services)
            f"avg:kubernetes.cpu.usage.total{{kube_service:{service_name},env:{env}}} by {{kube_cluster_name}}",
            f"avg:kubernetes.memory.usage{{kube_service:{service_name},env:{env}}} by {{kube_cluster_name}}",
            f"avg:kubernetes_state.pod.ready{{kube_service:{service_name},env:{env}}} by {{kube_cluster_name}}",
            # Container metrics (works for containerized services)
            f"avg:container.cpu.usage{{container_name:{service_name},env:{env}}} by {{kube_cluster_name}}",
            f"avg:docker.cpu.usage{{container_name:{service_name},env:{env}}} by {{kube_cluster_name}}",
            # System metrics with service tag (fallback)
            f"avg:system.cpu.user{{service:{service_name},env:{env}}} by {{kube_cluster_name}}",
        ]
        
        clusters = []
        
        for query in metrics_to_try:
            params = {
                "from": from_time,
                "to": current_time,
                "query": query
            }
            
            try:
                response = requests.get(
                    f"https://{dd_site}/api/v1/query",
                    headers={
                        "DD-API-KEY": dd_api_key,
                        "DD-APPLICATION-KEY": dd_app_key
                    },
                    params=params,
                    timeout=10  # Increased timeout for more reliable results
                )
                
                if response.status_code != 200:
                    continue
            except Exception:
                # Network or timeout error, try next metric
                continue
            
            data = response.json()
            
            if not data.get('series') or len(data['series']) == 0:
                continue
            
            # Extract ALL cluster names from all series
            for series in data['series']:
                scope = series.get('scope', '')
                if 'kube_cluster_name:' in scope:
                    cluster_name = scope.split('kube_cluster_name:')[1].split(',')[0].strip()
                    if cluster_name and cluster_name not in clusters:
                        clusters.append(cluster_name)
            
            # If we found clusters, log which metric worked and stop trying
            if clusters:
                # Extract metric name for logging
                metric_name = query.split(':')[1].split('{')[0] if ':' in query else 'unknown'
                print(f"      ✓ Found clusters via {metric_name}")
                break
        
        return clusters
        
    except Exception as e:
        print(f"      ✗ Error in get_service_clusters_from_metrics: {e}")
        return []


def get_arlo_services_status():
    """Scrape status.arlo.com to get platform service status"""
    try:
        import requests
        from bs4 import BeautifulSoup
        import logging
        
        url = "https://status.arlo.com"
        response = requests.get(url, timeout=5)
        
        if response.status_code != 200:
            return []
        
        soup = BeautifulSoup(response.text, "html.parser")
        text = soup.get_text("\n", strip=True)
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        
        # Core services to monitor (same as in app.py)
        core_services_names = ["Log In", "Notifications", "Library", "Live Streaming", 
                               "Video Recording", "Arlo Store", "Community"]
        
        # Extract services with deduplication logic (same as app.py)
        services = []
        seen_services = set()
        
        for i, line in enumerate(lines):
            if line in core_services_names:
                if i + 1 < len(lines) and line not in seen_services:
                    status_text = lines[i + 1]
                    
                    # Skip if next line is also a service name (means status wasn't captured)
                    if status_text in core_services_names:
                        logging.warning(f"⚠️ Arlo Platform Status: {line} → status not found (next line is another service: {status_text})")
                        continue
                    
                    status_lower = status_text.lower()
                    
                    # Determine status (be conservative - default to healthy)
                    if "outage" in status_lower or "down" in status_lower or "major" in status_lower:
                        status = "critical"
                    elif "degraded" in status_lower or "partial" in status_lower or "disruption" in status_lower:
                        status = "warning"
                    else:
                        # Default to healthy (includes "operational", "all good" and any unknown states)
                        status = "healthy"
                    
                    logging.info(f"✅ Arlo Platform Status: {line} → {status_text}")
                    services.append({
                        "name": line,
                        "status": status,
                        "status_text": status_text
                    })
                    seen_services.add(line)
        
        return services
    except Exception as e:
        print(f"❌ Error fetching Arlo platform status: {e}")
        return []


def status_monitor_dashboard(timerange: int = 4, environment: str = None) -> str:
    """
    Generate Status Monitor Dashboard HTML
    
    Args:
        timerange: Time range in hours (default 4)
        environment: Specific environment to display ('production', 'goldendev', 'goldenqa', or None for all)
    
    Returns:
        HTML string for the dashboard
    """
    global _status_cache
    
    # Check cache first - include version to invalidate cache when logic changes
    cache_version = "v3.0.3_redmetrics_us"  # Change this when logic changes
    cache_key = f"{cache_version}_{timerange}_{environment}_{int(time.time() // _cache_ttl)}"
    if cache_key in _status_cache:
        print(f"✅ Using cached dashboard data (cache key: {cache_key})")
        return _status_cache[cache_key]
    
    print(f"🔄 Cache miss - fetching fresh data (key: {cache_key})")
    
    # Header HTML
    output = """
    <style>
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.7; }
        }
        @keyframes blink-alert {
            0%, 100% { opacity: 1; box-shadow: 0 1px 2px rgba(0,0,0,0.12); }
            50% { opacity: 0.85; box-shadow: 0 0 8px rgba(255,255,255,0.5), 0 0 12px rgba(255,255,255,0.3); }
        }
        .service-box-clickable {
            cursor: pointer;
            transition: all 0.2s;
        }
        .service-box-clickable:hover {
            transform: translateY(-2px);
            box-shadow: 0 3px 8px rgba(0,0,0,0.25) !important;
        }
        .service-box-alert {
            animation: blink-alert 2s ease-in-out infinite;
        }
        /* Custom scrollbar for cluster lists */
        div[style*="overflow-y: auto"]::-webkit-scrollbar {
            width: 4px;
        }
        div[style*="overflow-y: auto"]::-webkit-scrollbar-track {
            background: rgba(255, 255, 255, 0.1);
            border-radius: 2px;
        }
        div[style*="overflow-y: auto"]::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.4);
            border-radius: 2px;
        }
        div[style*="overflow-y: auto"]::-webkit-scrollbar-thumb:hover {
            background: rgba(255, 255, 255, 0.6);
        }
        /* Firefox scrollbar */
        div[style*="overflow-y: auto"] {
            scrollbar-width: thin;
            scrollbar-color: rgba(255, 255, 255, 0.4) rgba(255, 255, 255, 0.1);
        }
        </style>
        <div style='max-width: 100%; margin: 0; padding: 0;'>
        """
    
    current_time = int(time.time())
    from_time = current_time - (timerange * 3600)
    
    # Define Samsung-specific services (from Samsung dashboard wnz-fqh-z4f)
    samsung_services = [
        # Samsung Partner Integration Services
        # Note: Samsung services include environment in the service name (backend-pp-samsung-prod)
        # and all use #env:samsung_prod tag
        'backend-pp-samsung-prod',
        'backend-pp-samsung-qa', 
        'backend-pp-samsung-dev',
        'hmsguard-samsung-prod',
        'hmsguard-samsung-qa',
        'hmsguard-samsung-dev',
    ]
    
    adt_services = [
        # ADT Partner Integration Services from dashboard cum-ivw-92c
        # All services use #env:adt_prod tag
        'backend-hmsweb-device',
        'nginx-deviceapi-partner',
        'nginx-clientapi-partner',
        'backend-hmsweb-media',
        'backend-hmsweb-web',
        'presence',
        'partner-proxy',
        'oauth-proxy',
        'oauth',
        'logger',
        'geolocation',
        'discovery',
        'directory',
        'device-location',
        'backend-videoservice-lb',
        'backend-videoservice-discovery',
        'backend-inapppayments',
        'backend-hmspayment',
        'history',
        'backend-supporttool',
        'support',
        'secret-manager',
        'registration',
        'policy',
        'ocapi',
        'hmsfeedg',
        'hmsweb',
        'mqtt-auth',
        'messaging',
        'device-authentication',
        'broker-service',
        'backend-ajpserver-app',
        'backend-partnerplatform',
        'backend-partnercloud',
        'backend-partner-notifications',
        'backend-log-server',
        'backend-hmsvideooverification',
        'backend-hmspubsub',
        'backend-hmsnotification',
        'backend-hmsdeviceshadow',
        'backend-hmsdeviceevents',
        'backend-hmsdevicesauth',
        'backend-hmscspubsub',
        'backend-hmscscapi',
        'backend-hmsclientsauth',
        'backend-hmsautomation',
        'backend-hmsautomation-job',
        'backend-hmsapi',
        'backend-hmsam',
        'backend-arloautomation-leader',
        'advisor',
        'backend-hmsdeviceversioncontrol',
        'backend-hmsdevicemanagement',
        'backend-hmsclientmanagement',
        'backend-videoservice',
        'device-service',
        'backend-ajp',
        'backend-cloudplatform',
        'backend-notificationservice',
        'backend-partner-api',
        'backend-hmsalerts',
        'backend-hmssecurity',
    ]
    
    # Define all general services to monitor (from Service Impact Matrix - Datadog only)
    general_services = [
        # XCloud Platform Services
        'broker-service',
        'device-authentication',
        'device-location',
        'directory',
        'mqtt-auth',
        'history',
        'logger',
        'advisor',
        'messaging',
        'discovery',
        'oauth-proxy',
        'partner-proxy',
        'presence',
        'privacy-policy',
        'geolocation',
        'registration',
        'secret-manager',
        'support',
        'nginx-clientapi',
        
        # HMS Backend Services
        'mediamigrationscheduler',
        'backend-sipserver-app',
        'hmsfeeds',
        'backend-hmsdeviceevents',
        'backend-hmsnotification',
        'backend-hmsvideoverification',
        'backend-feedsearch',
        'backend-hmsdevicemanagement',
        'backend-arlosafeapi',
        'backend-hmsautomation-scheduler',
        'backend-hmsautomation',
        'backend-hmspubsub',
        'backend-hmscspubsub',
        'backend-hmsclientauth',
        'backend-hmscsapi',
        'backend-hmsautomation-job',
        'backend-arloautomation-leader',
        'backend-hmsdeviceshadow',
        'backend-hmsgoogleapi',
        'backend-hmsguard',
        'backend-partnerplatform',
        'backend-hmsapi',
        'backend-inapppayments',
        'backend-supporttool',
        'backend-hmsreportingservice',
        'backend-arlosafelocations',
        'backend-hmsentityauth',
        'backend-hmshomekit-app',
        'backend-hmshomekit-scheduler',
        'backend-hmsifttt',
        'backend-partnercloud',
        'backend-partner-notifications',
        
        # Payment & Subscription
        'hmspayment',
        'hmsam',
        'aria',
        
        # Web & API
        'hmsweb',
        'hmsalexaapi',
        
        # Infrastructure
        'dnsmapper',
        'savant-sagemaker',
        'asl-java',
        'ocapi',
        'camsdk-webserver',
    ]
    
    # Determine which services and environments to query
    if environment == 'samsung':
        # Samsung-specific mode: Extract services dynamically from dashboard
        # Dashboard ID: wnz-fqh-z4f (RED Metrics - Samsung)
        # All services use env:samsung_prod tag
        dynamic_services = get_services_from_dashboard('wnz-fqh-z4f', cache_key='samsung_dashboard')
        services = dynamic_services if dynamic_services else samsung_services  # Fallback to hardcoded list
        environments = ['samsung_prod']  # All Samsung services use this single env tag
        print(f"📱 Samsung Mode: Monitoring {len(services)} Samsung network services (env:samsung_prod)")
        if dynamic_services:
            print(f"   📊 Services extracted from dashboard wnz-fqh-z4f")
        else:
            print(f"   ⚠️  Using fallback hardcoded service list")
    elif environment == 'adt':
        # ADT-specific mode: Extract services dynamically from dashboard
        # Dashboard ID: cum-ivw-92c (RED Metrics - partnerprod)
        # All services use env:adt_prod tag
        dynamic_services = get_services_from_dashboard('cum-ivw-92c', cache_key='adt_dashboard_v2')
        services = dynamic_services if dynamic_services else adt_services  # Fallback to hardcoded list
        environments = ['adt_prod']  # All ADT services use this single env tag
        print(f"🏠 ADT Mode: Monitoring {len(services)} ADT partner services (env:adt_prod)")
        if dynamic_services:
            print(f"   📊 Services extracted from dashboard cum-ivw-92c")
        else:
            print(f"   ⚠️  Using fallback hardcoded service list")
    elif environment == 'redmetrics-us':
        # RED Metrics US mode: Extract services dynamically from dashboard
        # Dashboard ID: qiz-7xc-fqr (RED Metrics - US)
        dynamic_services = get_services_from_dashboard('qiz-7xc-fqr', cache_key='redmetrics_us_dashboard')
        services = dynamic_services if dynamic_services else general_services  # Fallback to general services
        environments = ['production']  # US services typically use production env
        print(f"🇺🇸 RED Metrics US Mode: Monitoring {len(services)} US region services")
        if dynamic_services:
            print(f"   📊 Services extracted from dashboard qiz-7xc-fqr")
        else:
            print(f"   ⚠️  Using fallback general service list")
    elif environment:
        # Single environment mode (production, goldendev, goldenqa)
        if environment not in ['production', 'goldendev', 'goldenqa']:
            return f"<p style='color: #dc2626;'>⚠️ Error: Invalid environment '{environment}'</p>"
        services = general_services
        environments = [environment]
    else:
        # All environments mode
        services = general_services
        environments = ['production', 'goldendev', 'goldenqa']
    
    # Get credentials
    dd_api_key = os.getenv("DATADOG_API_KEY")
    dd_app_key = os.getenv("DATADOG_APP_KEY")
    dd_site = os.getenv("DATADOG_SITE", "arlo.datadoghq.com")
    pd_api_key = os.getenv("PAGERDUTY_API_TOKEN")  # Changed from PAGERDUTY_API_KEY to match other tools
    
    if not dd_api_key or not dd_app_key:
        return "<p style='color: #dc2626;'>⚠️ Error: Datadog credentials not configured</p>"
    
    # Fetch service health data in parallel
    print(f"📡 Fetching health for {len(services)} services across {len(environments)} environment(s): {environments}...")
    all_statuses = []
    
    # Use reasonable worker count to avoid overwhelming APIs (15 for good balance)
    max_workers = 15
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for service in services:
            for env in environments:
                future = executor.submit(
                    get_service_health_status,
                    service, env, dd_api_key, dd_app_key, dd_site,
                    from_time, current_time,
                    False  # enable_extended_metrics=False to avoid false positives (faster, more stable)
                )
                futures.append(future)
        
        for future in as_completed(futures):
            try:
                result = future.result()
                all_statuses.append(result)
            except Exception as e:
                print(f"Error in parallel execution: {e}")
    
    # Get PagerDuty status and Arlo status in parallel
    print(f"🔄 Fetching PagerDuty and Arlo status in parallel...")
    pd_counts = {"triggered": 0, "acknowledged": 0, "resolved": 0}
    pd_incidents = []
    arlo_services_status = []
    
    with ThreadPoolExecutor(max_workers=2) as executor:
        # Submit both tasks in parallel
        pd_future = executor.submit(get_pagerduty_status_counts, pd_api_key) if pd_api_key else None
        arlo_future = executor.submit(get_arlo_services_status)
        
        # Get results
        if pd_future:
            try:
                pd_counts, pd_incidents = pd_future.result()
            except Exception as e:
                print(f"⚠️ Error fetching PagerDuty status: {e}")
        else:
            print(f"⚠️ PagerDuty API key not available")
        
        try:
            arlo_services_status = arlo_future.result()
            print(f"🎯 Arlo: {len(arlo_services_status)} core services")
        except Exception as e:
            print(f"⚠️ Error fetching Arlo status: {e}")
    
    pd_incidents_count = pd_counts["triggered"] + pd_counts["acknowledged"]
    
    # Correlate PagerDuty incidents with services
    # Only use ACTIVE incidents (triggered or acknowledged), not resolved
    # Also filter by time - only recent incidents (last 24 hours)
    from datetime import timedelta
    cutoff_time = datetime.utcnow() - timedelta(hours=24)
    
    pd_affected_services = set()
    if pd_incidents:
        print(f"🔍 Filtering {len(pd_incidents)} PagerDuty incidents (triggered + acknowledged only, last 24h)...")
        recent_active_incidents = []
        
        for incident in pd_incidents:
            # Double-check status - only process triggered or acknowledged
            incident_status = incident.get('status', '').lower()
            if incident_status not in ['triggered', 'acknowledged']:
                continue  # Skip resolved or other statuses
            
            # Check incident age - only consider incidents from last 4 hours
            created_at_str = incident.get('created_at', '')
            try:
                if created_at_str:
                    # Parse ISO format: 2024-02-17T10:30:00Z
                    created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                    if created_at < cutoff_time:
                        continue  # Skip old incidents
            except Exception as e:
                print(f"  ⚠️  Could not parse incident date: {e}")
                # If we can't parse the date, still include it to be safe
            
            recent_active_incidents.append(incident)
            
            # Safely get title (always a string)
            title_raw = incident.get('title', '')
            title = title_raw.lower() if isinstance(title_raw, str) else ''
            
            # PagerDuty 'service' field is a dict with 'summary' key
            service_obj = incident.get('service', {})
            if isinstance(service_obj, dict):
                service_summary = service_obj.get('summary', '').lower()
            else:
                service_summary = ''
            
            # Detect environment from incident title or service summary
            detected_environments = []
            for env in environments:
                env_lower = env.lower()
                if env_lower in title or env_lower in service_summary:
                    detected_environments.append(env)
            
            # If no environment detected, check for common environment keywords
            if not detected_environments:
                if any(keyword in title or keyword in service_summary for keyword in ['prod', 'production']):
                    detected_environments.append('production')
                elif any(keyword in title or keyword in service_summary for keyword in ['dev', 'development']):
                    detected_environments.append('goldendev')
                elif any(keyword in title or keyword in service_summary for keyword in ['qa', 'quality']):
                    detected_environments.append('goldenqa')
                elif 'samsung' in title or 'samsung' in service_summary:
                    detected_environments.append('samsung_prod')
                elif 'adt' in title or 'adt' in service_summary or 'partnerprod' in title or 'partnerprod' in service_summary:
                    detected_environments.append('adt_prod')
            
            # If still no environment detected, assume it affects all environments being monitored
            if not detected_environments:
                detected_environments = environments.copy()
            
            # Try to match service names from incident title or service summary
            matched_services = []
            for service in services:
                service_lower = service.lower()
                if service_lower in title or service_lower in service_summary:
                    # Add (service, environment) tuple for each detected environment
                    for env in detected_environments:
                        pd_affected_services.add((service, env))
                    matched_services.append(service)
            
            # Also check for common patterns like "backend-X is down" or "X service error"
            for service in services:
                if service not in matched_services:  # Avoid duplicates
                    service_parts = service.split('-')
                    for part in service_parts:
                        if len(part) > 4 and part in title:  # Match significant parts
                            # Add (service, environment) tuple for each detected environment
                            for env in detected_environments:
                                pd_affected_services.add((service, env))
                            matched_services.append(service)
                            break
            
            # Log matched services for this incident
            if matched_services:
                env_str = ', '.join(detected_environments) if len(detected_environments) < len(environments) else 'all envs'
                print(f"  🚨 Incident [{incident_status.upper()}]: '{title[:60]}...' → {', '.join(matched_services)} in [{env_str}]")
        
        print(f"🔗 PagerDuty correlation: {len(recent_active_incidents)} recent active incidents → {len(pd_affected_services)} service-environment pairs affected")
    else:
        print(f"✅ No active PagerDuty incidents")
    if pd_affected_services:
        # Show first 10 affected (service, env) pairs
        affected_display = [f"{svc} ({env})" for svc, env in list(pd_affected_services)[:10]]
        print(f"   Affected: {', '.join(affected_display)}")
    
    # Override status for PagerDuty-affected services (balanced approach)
    # Now checking (service, environment) tuples to only affect specific environments
    for status_obj in all_statuses:
        service_env_tuple = (status_obj['service'], status_obj['environment'])
        if service_env_tuple in pd_affected_services:
            current_status = status_obj['status']
            
            # Balanced PagerDuty escalation logic:
            # - If already critical (high errors/latency) → keep critical
            # - If warning or healthy but has PD alert → escalate to warning (not critical)
            # This prevents false positives where PD alert exists but metrics are fine
            
            if current_status == 'critical':
                # Already critical due to metrics, keep it
                status_obj['pd_incident'] = True
                print(f"🚨 {status_obj['service']} ({status_obj['environment']}): CRITICAL (metrics + PagerDuty alert)")
            elif current_status == 'warning':
                # Already warning, escalate to critical due to PD
                status_obj['status'] = 'critical'
                status_obj['pd_incident'] = True
                print(f"⚠️→🚨 Escalating {status_obj['service']} ({status_obj['environment']}) to CRITICAL (warning metrics + PagerDuty)")
            else:  # healthy
                # Healthy metrics but PD alert → set to warning (yellow), not critical
                status_obj['status'] = 'warning'
                status_obj['pd_incident'] = True
                print(f"✅→⚠️ {status_obj['service']} ({status_obj['environment']}): WARNING (healthy metrics but PagerDuty alert active)")
        else:
            status_obj['pd_incident'] = False
    
    # Filter out inactive and unknown services (no data to show)
    total_services_before = len(all_statuses)
    all_statuses = [s for s in all_statuses if s['status'] not in ['inactive', 'unknown']]
    filtered_count = total_services_before - len(all_statuses)
    if filtered_count > 0:
        print(f"🔍 Filtered out {filtered_count} inactive/unknown services (no traffic/data)")
    
    # Get EKS cluster info for all services in all environments (PARALLEL)
    print(f"☸️  Extracting EKS cluster names for all services (parallel)...")
    cluster_service_map = {}
    
    # Map environment names to their possible env tags for Datadog (try multiple variants)
    env_tag_variants = {
        'production': ['prod', 'production'],
        'goldendev': ['goldendev', 'dev'],
        'goldenqa': ['goldenqa', 'qa'],
        'samsung_prod': ['samsung_prod'],
        'adt_prod': ['adt_prod']
    }
    
    def fetch_clusters_for_service(status_obj):
        """Fetch EKS clusters for a single service"""
        service_name = status_obj['service']
        service_env = status_obj['environment']
        env_tags_to_try = env_tag_variants.get(service_env, [service_env])
        
        print(f"   🔍 Checking {service_name} [{service_env}] with tags: {env_tags_to_try}")
        
        cluster_names = []
        for env_tag in env_tags_to_try:
            cluster_names = get_service_clusters_from_metrics(service_name, env_tag, timerange_hours=1)
            if cluster_names:
                print(f"      ✓ Found {len(cluster_names)} cluster(s) with tag '{env_tag}'")
                break  # Found clusters, no need to try other variants
            else:
                print(f"      ✗ No clusters found with tag '{env_tag}'")
        
        return (status_obj, cluster_names, service_name, service_env)
    
    # Use ThreadPoolExecutor to fetch clusters in parallel
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(fetch_clusters_for_service, status_obj) for status_obj in all_statuses]
        
        for future in as_completed(futures):
            try:
                status_obj, cluster_names, service_name, service_env = future.result()
                
                if cluster_names:
                    status_obj['eks_clusters'] = cluster_names
                    status_obj['eks_cluster_count'] = len(cluster_names)
                    
                    # Track which services run on which clusters
                    for cluster_name in cluster_names:
                        if cluster_name not in cluster_service_map:
                            cluster_service_map[cluster_name] = []
                        cluster_service_map[cluster_name].append(f"{service_name} ({service_env})")
                    
                    clusters_str = ', '.join(cluster_names) if len(cluster_names) <= 3 else f"{len(cluster_names)} clusters"
                    # Already logged above
                    pass
                # Already logged above if no clusters found
            except Exception as e:
                print(f"   ❌ Error fetching clusters: {e}")
    
    if cluster_service_map:
        print(f"☸️  EKS Summary (All Environments):")
        for cluster_name, services in sorted(cluster_service_map.items()):
            print(f"   • {cluster_name}: {len(services)} services")
    
    # Get Splunk outliers (DISABLED)
    # print(f"📊 Fetching Splunk outliers...")
    # splunk_outliers = get_splunk_outliers(timerange)
    # print(f"🔍 Splunk: {len(splunk_outliers)} outliers found")
    splunk_outliers = []  # Disabled temporarily
    
    # Get US Infra Exceptions count (DISABLED)
    # print(f"🏗️ Fetching US Infra Exceptions...")
    # infra_exceptions_count, infra_exceptions_details = get_splunk_infra_exceptions(timerange)
    # print(f"🚨 US Infra Exceptions: {infra_exceptions_count} found")
    infra_exceptions_count = 0  # Disabled temporarily
    infra_exceptions_details = []
    
    # Get AWS costs and changes
    print(f"☁️ Fetching AWS costs and changes...")
    aws_data = get_aws_costs_and_changes(days=1)
    print(f"💰 AWS: ${aws_data.get('cost_yesterday', 0):.2f} yesterday, {len(aws_data.get('recent_changes', []))} changes")
    
    # Build dashboard
    # Get current time (will be replaced by client-side timezone)
    current_dt = datetime.utcnow()
    
    # Dashboard title based on environment mode
    if environment == 'samsung':
        dashboard_title = "Samsung"
        dashboard_subtitle = "Real-time health status for Samsung partner network services"
    elif environment == 'adt':
        dashboard_title = "ADT"
        dashboard_subtitle = "Real-time health status for ADT partner network services"
    elif environment == 'redmetrics-us':
        dashboard_title = "🇺🇸 RED Metrics US"
        dashboard_subtitle = "Real-time health status for US region services"
    elif environment:
        dashboard_title = f"📊 {environment.upper()} Status Monitor"
        dashboard_subtitle = f"Real-time health status for {environment}"
    else:
        dashboard_title = "📊 Service Status Monitor"
        dashboard_subtitle = "Real-time health status across all environments"
    
    output += f"""
    <div style='background: #ffffff; padding: 8px 20px; border-bottom: 1px solid #e5e7eb; margin: -24px -24px 12px -24px;'>
        <div style='display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px;'>
            <div>
                <h1 style='margin: 0; color: #111827; font-size: 18px; font-weight: 700; margin-bottom: 2px; letter-spacing: -0.02em;'>
                    {dashboard_title.replace('📊 ', '')}
                </h1>
                <p style='margin: 0; font-size: 12px; color: #6b7280; font-weight: 500; letter-spacing: -0.01em;'>
                    {dashboard_subtitle} <span style='margin-left: 12px; font-size: 11px; color: #374151; font-weight: 600;'>📊 Baseline: 7-day avg | Threshold: &gt;5%↑1%</span>
                </p>
            </div>
            
            <div style='display: flex; align-items: center; gap: 8px;'>
                <div style='display: flex; align-items: center; gap: 6px;'>
                    <label for='timerange' style='font-size: 12px; font-weight: 600; color: #374151;'>Time Range:</label>
                    <select id='timerange' style='padding: 5px 10px; border: 1px solid #d1d5db; border-radius: 5px; font-size: 12px; cursor: pointer; background: #ffffff; color: #111827; outline: none; font-weight: 500;'>
                        <option value='1'>Last 1 hour</option>
                        <option value='2'>Last 2 hours</option>
                        <option value='4' selected>Last 4 hours</option>
                        <option value='8'>Last 8 hours</option>
                        <option value='12'>Last 12 hours</option>
                        <option value='24'>Last 24 hours</option>
                    </select>
                </div>
                
                <button onclick='loadDashboard()' style='padding: 5px 12px; background: #0095da; color: #ffffff; border: none; border-radius: 5px; font-size: 12px; font-weight: 700; cursor: pointer; transition: all 0.15s ease-in-out;' onmouseover="this.style.background='#0088c7'" onmouseout="this.style.background='#0095da'">
                    🔄 Refresh
                </button>
                
                <button onclick='clearCacheAndReload()' style='padding: 5px 12px; background: #dc2626; color: #ffffff; border: none; border-radius: 5px; font-size: 12px; font-weight: 700; cursor: pointer; transition: all 0.15s ease-in-out;' onmouseover="this.style.background='#b91c1c'" onmouseout="this.style.background='#dc2626'" title='Clear cache and force fresh data from Datadog'>
                    🧹 Force Refresh
                </button>
                
                <div style='text-align: right; padding-left: 8px; border-left: 1px solid #e5e7eb;'>
                    <div style='font-size: 9px; color: #9ca3af; font-weight: 500;'>Last updated</div>
                    <div style='font-size: 12px; font-weight: 700; color: #111827;'>--:--:--</div>
                    <div style='font-size: 9px; color: #9ca3af; font-weight: 500;'>-----xx-xx</div>
                </div>
            </div>
        </div>
    </div>
    """
    
    # Main layout container
    output += """
    <!-- Main Container: Sidebar + Content -->
    <div style='display: grid; grid-template-columns: 260px 1fr; gap: 24px; margin-bottom: 20px;'>
        <!-- Left Sidebar -->
        <div style='display: flex; flex-direction: column; gap: 16px;'>
    """
    
    # PagerDuty Status Widget - use counts from API
    pd_triggered = pd_counts["triggered"]
    pd_acknowledged = pd_counts["acknowledged"]
    pd_resolved = pd_counts["resolved"]
    
    # Determine background color and blink behavior based on status
    if pd_triggered > 0:
        pd_bg_color = '#dc2626'  # Red
        pd_status_icon = '🔴'
        pd_status_text = 'CRITICAL'
        pd_blink_class = 'pd-status-blink'
    elif pd_acknowledged > 0:
        pd_bg_color = '#f59e0b'  # Yellow/Orange
        pd_status_icon = '🟡'
        pd_status_text = 'WARNING'
        pd_blink_class = 'pd-status-blink'
    else:
        pd_bg_color = '#10b981'  # Green
        pd_status_icon = '🟢'
        pd_status_text = 'HEALTHY'
        pd_blink_class = ''  # No blink when healthy
    
    # Calculate summary statistics (after filtering)
    total_services = len(all_statuses)
    total_healthy = sum(1 for s in all_statuses if s['status'] == 'healthy')
    total_warning = sum(1 for s in all_statuses if s['status'] == 'warning')
    total_critical = sum(1 for s in all_statuses if s['status'] == 'critical')
    total_inactive = 0  # Inactive services are now filtered out
    total_requests = sum(s['requests'] for s in all_statuses)
    total_errors = sum(s['errors'] for s in all_statuses)
    overall_error_rate = (total_errors / total_requests * 100) if total_requests > 0 else 0
    
    # Detailed logging of status distribution
    print(f"\n{'='*80}")
    print(f"📊 STATUS SUMMARY for {environment}")
    print(f"{'='*80}")
    print(f"Total Active Services: {total_services}")
    
    if total_services > 0:
        print(f"  ✅ Healthy: {total_healthy} ({total_healthy/total_services*100:.1f}%)")
        print(f"  ⚠️  Warning: {total_warning} ({total_warning/total_services*100:.1f}%)")
        print(f"  🚨 Critical: {total_critical} ({total_critical/total_services*100:.1f}%)")
        print(f"Overall: {total_requests:,} requests, {total_errors:,} errors, {overall_error_rate:.2f}% error rate")
    else:
        print(f"  ⚠️  No services found for this environment")
        print(f"  💡 Check service names and environment tags in Datadog")
    
    # List all critical services with details
    if total_critical > 0:
        print(f"\n🚨 CRITICAL SERVICES ({total_critical}):")
        critical_services = [s for s in all_statuses if s['status'] == 'critical']
        for svc in critical_services:
            error_rate = (svc['errors'] / svc['requests'] * 100) if svc['requests'] > 0 else 0
            reasons = []
            if error_rate > 5:
                reasons.append(f"Error: {error_rate:.2f}%")
            if svc.get('traffic_drop'):
                reasons.append(f"Traffic Drop: {svc.get('traffic_variance', 'N/A')}")
            if svc.get('pd_incident'):
                reasons.append("PagerDuty Alert")
            reason_str = " | ".join(reasons) if reasons else "Unknown"
            print(f"  • {svc['service']} ({svc['environment']}): {reason_str}")
            print(f"    Requests: {svc['requests']:,}, Errors: {svc['errors']:,}")
    
    # List all warning services with details
    if total_warning > 0:
        print(f"\n⚠️  WARNING SERVICES ({total_warning}):")
        warning_services = [s for s in all_statuses if s['status'] == 'warning']
        for svc in warning_services:
            error_rate = (svc['errors'] / svc['requests'] * 100) if svc['requests'] > 0 else 0
            reasons = []
            if error_rate > 1:
                reasons.append(f"Error: {error_rate:.2f}%")
            if svc.get('high_latency') and not svc.get('traffic_drop'):
                reasons.append("High Latency")
            reason_str = " | ".join(reasons) if reasons else "Unknown"
            print(f"  • {svc['service']} ({svc['environment']}): {reason_str}")
            print(f"    Requests: {svc['requests']:,}, Errors: {svc['errors']:,}")
    
    # List healthy services (names only)
    if total_healthy > 0:
        print(f"\n✅ HEALTHY SERVICES ({total_healthy}):")
        healthy_services = [s['service'] for s in all_statuses if s['status'] == 'healthy']
        print(f"  {', '.join(healthy_services)}")
    
    print(f"{'='*80}\n")
    
    # First: Overall Summary
    output += f"""
            <!-- Overall Summary -->
            <div style='background: #ffffff; padding: 12px; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04); border: 1px solid #e5e7eb;'>
                <div style='margin-bottom: 10px;'>
                    <h3 style='font-size: 14px; font-weight: 700; color: #111827; margin: 0; letter-spacing: -0.02em;'>📈 Summary</h3>
                </div>
                
                <!-- 3 Column Layout: Status Labels + Metrics -->
                <div style='display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px;'>
                    <!-- Column 1: Healthy & Warning -->
                    <div style='display: flex; flex-direction: column; gap: 8px;'>
                        <div style='display: flex; align-items: center; gap: 6px;'>
                            <div style='width: 10px; height: 10px; background: #10b981; border-radius: 2px; flex-shrink: 0;'></div>
                            <div style='flex: 1; min-width: 0;'>
                                <div style='color: #111827; font-weight: 600; font-size: 11px; letter-spacing: -0.01em;'>Healthy</div>
                                <div style='color: #10b981; font-weight: 700; font-size: 16px;'>{total_healthy}</div>
                            </div>
                        </div>
                        <div style='display: flex; align-items: center; gap: 6px;'>
                            <div style='width: 10px; height: 10px; background: #f59e0b; border-radius: 2px; flex-shrink: 0;'></div>
                            <div style='flex: 1; min-width: 0;'>
                                <div style='color: #111827; font-weight: 600; font-size: 11px; letter-spacing: -0.01em;'>Warning</div>
                                <div style='color: #f59e0b; font-weight: 700; font-size: 16px;'>{total_warning}</div>
                            </div>
                        </div>
                    </div>
                    
                    <!-- Column 2: Critical & Total -->
                    <div style='display: flex; flex-direction: column; gap: 8px;'>
                        <div style='display: flex; align-items: center; gap: 6px;'>
                            <div style='width: 10px; height: 10px; background: #dc2626; border-radius: 2px; flex-shrink: 0;'></div>
                            <div style='flex: 1; min-width: 0;'>
                                <div style='color: #111827; font-weight: 600; font-size: 11px; letter-spacing: -0.01em;'>Critical</div>
                                <div style='color: #dc2626; font-weight: 700; font-size: 16px;'>{total_critical}</div>
                            </div>
                        </div>
                        <div style='display: flex; align-items: center; gap: 6px;'>
                            <div style='width: 10px; height: 10px; background: #e5e7eb; border-radius: 2px; flex-shrink: 0;'></div>
                            <div style='flex: 1; min-width: 0;'>
                                <div style='color: #111827; font-weight: 600; font-size: 11px; letter-spacing: -0.01em;'>Total</div>
                                <div style='color: #6b7280; font-weight: 700; font-size: 16px;'>{total_services}</div>
                            </div>
                        </div>
                    </div>
                    
                    <!-- Column 3: Metrics -->
                    <div style='background: #f9fafb; padding: 8px; border-radius: 6px; display: flex; flex-direction: column; justify-content: center; gap: 6px;'>
                        <div style='display: flex; justify-content: space-between;'>
                            <span style='color: #6b7280; font-weight: 500; font-size: 10px;'>REQ:</span>
                            <span style='color: #111827; font-weight: 700; font-size: 11px; letter-spacing: -0.01em;'>{total_requests:,}</span>
                        </div>
                        <div style='display: flex; justify-content: space-between;'>
                            <span style='color: #6b7280; font-weight: 500; font-size: 10px;'>ERR:</span>
                            <span style='color: #dc2626; font-weight: 700; font-size: 11px; letter-spacing: -0.01em;'>{total_errors:,}</span>
                        </div>
                        <div style='display: flex; justify-content: space-between;'>
                            <span style='color: #6b7280; font-weight: 500; font-size: 10px;'>ERR%:</span>
                            <span style='color: {"#dc2626" if overall_error_rate > 1 else "#10b981"}; font-weight: 700; font-size: 11px; letter-spacing: -0.01em;'>{overall_error_rate:.2f}%</span>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- PagerDuty Status -->
            <div style='background: #ffffff; padding: 16px; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04); border: 1px solid #e5e7eb;'>
                <div style='margin-bottom: 12px;'>
                    <h3 style='font-size: 15px; font-weight: 700; color: #111827; margin: 0; letter-spacing: -0.02em;'>🚨 PagerDuty</h3>
                </div>
                <div class='{pd_blink_class}' style='display: flex; justify-content: space-between; gap: 12px; padding: 12px; background: {pd_bg_color}; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.1);'>
                    <div style='text-align: center; flex: 1;'>
                        <div style='font-size: 24px; font-weight: 700; color: white; letter-spacing: -0.01em;'>{pd_triggered}</div>
                        <div style='font-size: 10px; color: rgba(255,255,255,0.9); font-weight: 600;'>Triggered</div>
                    </div>
                    <div style='text-align: center; flex: 1;'>
                        <div style='font-size: 24px; font-weight: 700; color: white; letter-spacing: -0.01em;'>{pd_acknowledged}</div>
                        <div style='font-size: 10px; color: rgba(255,255,255,0.9); font-weight: 600;'>Ack</div>
                    </div>
                    <div style='text-align: center; flex: 1;'>
                        <div style='font-size: 24px; font-weight: 700; color: white; letter-spacing: -0.01em;'>{pd_resolved}</div>
                        <div style='font-size: 10px; color: rgba(255,255,255,0.9); font-weight: 600;'>Resolved</div>
                    </div>
                </div>
            </div>
            
            <!-- Arlo Platform Status -->
            <div style='background: #ffffff; padding: 16px; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04); border: 1px solid #e5e7eb;'>
                <div style='margin-bottom: 12px;'>
                    <h3 style='font-size: 15px; font-weight: 700; color: #111827; margin: 0; letter-spacing: -0.02em;'>🎯 Arlo Platform</h3>
                </div>
                <div style='display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px;'>
    """
    
    if arlo_services_status:
        for service in arlo_services_status:
            service_name = service['name']
            status = service['status']
            status_text = service.get('status_text', 'Unknown')
            
            # Color based on status
            if status == 'critical':
                bg_color = '#dc2626'  # Red
            elif status == 'warning':
                bg_color = '#f59e0b'  # Orange
            else:
                bg_color = '#10b981'  # Green (default)
            
            # Shorter service names
            short_name = service_name.replace('Live ', '').replace('Video ', '')
            
            output += f"""
                    <div style='background: {bg_color}; padding: 7px 8px; border-radius: 5px; text-align: center;'>
                        <div style='font-size: 10px; color: white; font-weight: 700; line-height: 1.2; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; letter-spacing: -0.01em;'>{short_name}</div>
                    </div>
            """
    else:
        output += """
                    <div style='grid-column: 1 / -1; text-align: center; padding: 12px; color: #6b7280; font-size: 11px; font-weight: 500;'>
                        No data available
                    </div>
        """
    
    output += f"""
                </div>
            </div>
    """
    
    output += f"""
            
            <!-- US Infra Exceptions -->
            <div style='background: white; padding: 6px; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
                <div style='background: #00c853; color: white; padding: 3px 4px; border-radius: 3px; margin-bottom: 4px; text-align: center;'>
                    <span style='font-size: 9px; font-weight: bold;'>🏗️ US Infra Exceptions</span>
                </div>
    """
    
    # Determine color based on exception count
    if infra_exceptions_count > 100:
        infra_bg_color = '#dc2626'  # Red
        infra_icon = '🔴'
        infra_status = 'CRITICAL'
    elif infra_exceptions_count > 50:
        infra_bg_color = '#f59e0b'  # Orange
        infra_icon = '🟡'
        infra_status = 'WARNING'
    elif infra_exceptions_count > 0:
        infra_bg_color = '#fb923c'  # Light orange
        infra_icon = '🟠'
        infra_status = 'ATTENTION'
    else:
        infra_bg_color = '#10b981'  # Green
        infra_icon = '🟢'
        infra_status = 'HEALTHY'
    
    # US Infra Exceptions dashboard URL
    infra_dashboard_url = 'https://arlo.splunkcloud.com/en-GB/app/search/us_infra_exceptions'
    
    output += f"""
                <div style='background: {infra_bg_color}; padding: 5px; border-radius: 3px; color: white; cursor: pointer; transition: all 0.2s;' 
                     onclick="window.open('{infra_dashboard_url}', '_blank')" 
                     title='Click to view US Infra Exceptions dashboard in Splunk'
                     onmouseover="this.style.opacity='0.9'; this.style.transform='scale(1.02)'" 
                     onmouseout="this.style.opacity='1'; this.style.transform='scale(1)'">
                    <div style='text-align: center; margin-bottom: 3px;'>
                        <div style='font-size: 16px;'>{infra_icon}</div>
                        <div style='font-size: 7px; font-weight: bold; opacity: 0.95;'>{infra_status}</div>
                    </div>
                    <div style='text-align: center; border-top: 1px solid rgba(255,255,255,0.3); padding-top: 3px;'>
                        <div style='font-size: 14px; font-weight: bold;'>{infra_exceptions_count:,}</div>
                        <div style='font-size: 6px; opacity: 0.9;'>Exceptions (last {timerange}h)</div>
                    </div>
                </div>
            </div>
            
            <!-- Streaming Outliers -->
            <div style='background: white; padding: 6px; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
                <div style='background: #00c853; color: white; padding: 3px 4px; border-radius: 3px; margin-bottom: 4px; text-align: center;'>
                    <span style='font-size: 9px; font-weight: bold;'>🔍 Streaming Outliers</span>
                </div>
                <div style='display: flex; flex-direction: column; gap: 2px; max-height: 180px; overflow-y: auto;'>
    """
    
    if splunk_outliers and len(splunk_outliers) > 0:
        for outlier in splunk_outliers:
            service = outlier.get('service', 'Unknown')
            count = int(outlier.get('count', 0))
            error_type = outlier.get('error_type', 'Error')
            
            # Truncate service name if too long
            service_display = service.split('.')[-1] if '.' in service else service
            service_display = service_display[:20] if len(service_display) > 20 else service_display
            
            # Color based on count severity
            if count > 100:
                bg_color = '#dc2626'  # Red - Critical
            elif count > 50:
                bg_color = '#f59e0b'  # Orange - Warning
            elif count > 10:
                bg_color = '#fb923c'  # Light orange
            else:
                bg_color = '#6b7280'  # Gray - Low
            
            # Build Splunk search link
            splunk_search_url = f"https://arlo.splunkcloud.com/en-US/app/search/search?q=search%20index%3D*%20service%3D{service}%20earliest%3D-{timerange_hours}h"
            
            output += f"""
                    <div style='background: {bg_color}; padding: 3px 4px; border-radius: 3px; display: flex; justify-content: space-between; align-items: center; cursor: pointer; transition: opacity 0.2s;' 
                         onclick="window.open('{splunk_search_url}', '_blank')" 
                         title='Click to view {service} errors in Splunk ({error_type}: {count} occurrences)'
                         onmouseover="this.style.opacity='0.85'" 
                         onmouseout="this.style.opacity='1'">
                        <div style='font-size: 7px; color: white; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1;'>{service_display}</div>
                        <div style='font-size: 9px; color: white; font-weight: bold; margin-left: 4px;'>{count}</div>
                    </div>
            """
    else:
        output += """
                    <div style='text-align: center; padding: 8px; color: #6b7280; font-size: 7px;'>
                        ✅ No outliers detected
                    </div>
        """
    
    output += f"""
                </div>
            </div>
            
            <!-- AWS Costs & Changes -->
            <div style='background: white; padding: 6px; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
                <div style='background: #ff9900; color: white; padding: 3px 4px; border-radius: 3px; margin-bottom: 4px; text-align: center;'>
                    <span style='font-size: 9px; font-weight: bold;'>☁️ AWS Monitor</span>
                </div>
    """
    
    if not aws_data.get("error"):
        cost_yesterday = aws_data.get("cost_yesterday", 0)
        recent_changes = aws_data.get("recent_changes", [])
        
        # Cost indicator color
        if cost_yesterday > 1000:
            cost_bg = '#dc2626'  # Red - High cost
        elif cost_yesterday > 500:
            cost_bg = '#f59e0b'  # Orange - Medium cost
        else:
            cost_bg = '#10b981'  # Green - Low cost
        
        # AWS Console URL
        aws_console_url = 'https://console.aws.amazon.com/cost-management/home'
        
        output += f"""
                <!-- Cost Summary -->
                <div style='background: {cost_bg}; padding: 4px; border-radius: 3px; color: white; margin-bottom: 4px; cursor: pointer; transition: opacity 0.2s;'
                     onclick="window.open('{aws_console_url}', '_blank')"
                     title='Click to view AWS Cost Explorer'
                     onmouseover="this.style.opacity='0.9'" 
                     onmouseout="this.style.opacity='1'">
                    <div style='text-align: center;'>
                        <div style='font-size: 6px; opacity: 0.9; margin-bottom: 2px;'>Yesterday Cost</div>
                        <div style='font-size: 14px; font-weight: bold;'>${cost_yesterday:.2f}</div>
                    </div>
                </div>
                
                <!-- Recent Changes -->
                <div style='font-size: 7px; color: #2d3748; font-weight: 600; margin-bottom: 2px; padding: 0 2px;'>Recent Changes:</div>
                <div style='display: flex; flex-direction: column; gap: 2px; max-height: 120px; overflow-y: auto;'>
        """
        
        if recent_changes:
            for change in recent_changes[:5]:
                event_name = change.get('event_name', 'Unknown')
                user = change.get('user', 'Unknown')
                event_time = change.get('time', '--:--')
                
                # Truncate for display
                event_display = event_name[:18] if len(event_name) > 18 else event_name
                user_display = user.split('/')[-1][:12] if '/' in user else user[:12]
                
                output += f"""
                    <div style='background: #f8fafc; padding: 3px 4px; border-radius: 2px; border-left: 2px solid #ff9900;'>
                        <div style='font-size: 7px; color: #2d3748; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;'>{event_display}</div>
                        <div style='font-size: 6px; color: #6b7280; display: flex; justify-content: space-between;'>
                            <span title='{user}'>{user_display}</span>
                            <span>{event_time}</span>
                        </div>
                    </div>
                """
        else:
            output += """
                    <div style='text-align: center; padding: 6px; color: #6b7280; font-size: 7px;'>
                        No recent changes
                    </div>
            """
        
        output += """
                </div>
        """
    else:
        output += f"""
                <div style='text-align: center; padding: 8px; color: #6b7280; font-size: 7px;'>
                    ⚠️ Not configured
                </div>
        """
    
    output += f"""
            </div>
        </div>
        
        <!-- Main Content Area -->
        <div>
    
    <script>
        // Store chart data for initialization (inactive services are filtered out)
        window.chartData = {{
            healthy: {total_healthy},
            warning: {total_warning},
            critical: {total_critical},
            inactive: 0,
            total: {total_services}
        }};
        
        // Signal that data is ready
        if (window.initializePieChart) {{
            window.initializePieChart(window.chartData);
        }}
    </script>
    """
    
    # Build environment layout (1 or 3 columns depending on mode)
    # Using same blue color for all environments to avoid confusion
    env_config = {
        'production': {'icon': '🔵', 'color': '#2563eb'},      # Same blue
        'goldendev': {'icon': '🔵', 'color': '#2563eb'},       # Same blue
        'goldenqa': {'icon': '🔵', 'color': '#2563eb'},        # Same blue
        'samsung_prod': {'icon': '📱', 'color': '#3b82f6'},    # Samsung blue
        'adt_prod': {'icon': '🏠', 'color': '#7c3aed'}         # ADT purple
    }
    
    # Adjust grid columns based on number of environments
    # For Samsung, we'll stack sections vertically (no grid, just block layout)
    if environment == 'samsung':
        output += """
    <div style='display: flex; flex-direction: column; gap: 12px;'>
    """
    else:
        num_cols = len(environments)
        grid_template = f"repeat({num_cols}, 1fr)" if num_cols > 1 else "1fr"
        output += f"""
    <div style='display: grid; grid-template-columns: {grid_template}; gap: 3px;'>
    """
    
    for env in environments:
        config = env_config[env]
        env_services = [s for s in all_statuses if s['environment'] == env]
        
        # Sort services alphabetically
        env_services.sort(key=lambda x: x['service'].lower())
        
        # For Samsung, split into Partner Platform and HMSGUARD sections
        if environment == 'samsung':
            service_groups = [
                {
                    'name': 'Partner Platform',
                    'icon': '🤝',
                    'color': '#3b82f6',
                    'services': [s for s in env_services if 'pp-samsung' in s['service']]
                },
                {
                    'name': 'HMSGUARD',
                    'icon': '🛡️',
                    'color': '#8b5cf6',
                    'services': [s for s in env_services if 'hmsguard' in s['service']]
                }
            ]
        else:
            # For other environments, use single group
            service_groups = [{
                'name': env.upper(),
                'icon': config['icon'],
                'color': config['color'],
                'services': env_services
            }]
        
        # Render each service group
        for group in service_groups:
            group_services = group['services']
            
            if not group_services:
                continue
            
            # Count statuses for this group
            group_healthy = sum(1 for s in group_services if s['status'] == 'healthy')
            group_warning = sum(1 for s in group_services if s['status'] == 'warning')
            group_critical = sum(1 for s in group_services if s['status'] == 'critical')
            
            output += f"""
        <div>
            <!-- Group Header -->
            <div style='background: {group['color']}; color: white; padding: 6px 8px; border-radius: 5px 5px 0 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
                <div style='display: flex; justify-content: space-between; align-items: center;'>
                    <div style='font-size: 11px; font-weight: bold;'>
                        {group['icon']} {group['name']}
                    </div>
                    <div style='font-size: 8px; opacity: 0.9;'>
                        ✓ {group_healthy} | ⚠ {group_warning} | ✗ {group_critical}
                    </div>
                </div>
            </div>
            
            <!-- Services Grid -->
            <div style='display: grid; grid-template-columns: repeat(auto-fit, minmax({'180px' if len(environments) == 1 else '160px'}, 1fr)); gap: {'12px' if len(environments) == 1 else '8px'}; padding: {'16px' if len(environments) == 1 else '12px'}; background: #f8fafc; border-radius: 0 0 8px 8px; min-height: 200px;'>
        """
        
            # Render service boxes - larger size for single environment view
            is_single_env = len(environments) == 1
        
            for svc in group_services:
                status_colors = {
                    'healthy': '#10b981',
                    'warning': '#f59e0b',
                    'critical': '#dc2626',
                    'inactive': '#6b7280',
                    'unknown': '#9ca3af'
                }
                
                bg_color = status_colors.get(svc['status'], '#9ca3af')
                
                # Generate Datadog APM link for all services
                dd_site = os.getenv('DD_SITE', 'datadoghq.com')
                service_name = svc['service']
                dd_url = f"https://app.{dd_site}/apm/service/{service_name}/overview?env={env}"
                
                # Build status indicators
                status_badges = []
                if svc.get('pd_incident'):
                    status_badges.append('🚨')  # PagerDuty alert
                if svc.get('traffic_drop'):
                    status_badges.append('📉')  # Traffic drop
                if svc.get('high_latency'):
                    status_badges.append('⏱️')  # High latency
                
                badges_str = ' '.join(status_badges) if status_badges else ''
                
                # Add alert animation class for warning/critical
                alert_class = " service-box-alert" if svc['status'] in ['warning', 'critical'] else ""
                
                # Build tooltip with detailed info
                tooltip_parts = [f"Click to view {service_name} in Datadog APM"]
                if svc.get('p95_latency'):
                    tooltip_parts.append(f"P95 Latency: {svc['p95_latency']:.0f}ms")
                if svc.get('p99_latency'):
                    tooltip_parts.append(f"P99 Latency: {svc['p99_latency']:.0f}ms")
                if svc.get('traffic_drop'):
                    tooltip_parts.append("🚨 Traffic dropped >85% vs 7-day average")
                elif svc.get('traffic_variance') is not None:
                    variance = svc.get('traffic_variance')
                    tooltip_parts.append(f"Traffic: {variance:+.0f}% vs 7-day avg")
                if svc.get('pd_incident'):
                    tooltip_parts.append("⚠️ Active PagerDuty incident")
                tooltip = ' | '.join(tooltip_parts)
                
                # Adjust sizes based on view mode
                if is_single_env:
                    # Larger boxes for single environment (8 columns)
                    box_padding = '8px 4px'
                    box_min_height = '85px'
                    service_font_size = '12px'
                    label_font_size = '8px'
                    value_font_size = '13px'
                    inner_padding = '3px 4px'
                    gap_size = '3px'
                    badge_font_size = '11px'
                else:
                    # Compact boxes for all environments (4 columns per env)
                    box_padding = '4px 2px'
                    box_min_height = '52px'
                    service_font_size = '9px'
                    label_font_size = '6px'
                    value_font_size = '9px'
                    inner_padding = '1.5px 2px'
                    gap_size = '1.5px'
                    badge_font_size = '9px'
                
                # Determine which metrics to show
                show_latency = svc.get('p95_latency') is not None
                metrics_grid = '1fr 1fr 1fr' if show_latency else '1fr 1fr'
                
                # Check if this service has EKS cluster info
                eks_cluster_html = ""
                if svc.get('eks_clusters'):
                    cluster_names = svc['eks_clusters']
                    cluster_count = svc.get('eks_cluster_count', len(cluster_names))
                    
                    # Adjust font sizes based on view mode
                    cluster_font_size = '8px' if is_single_env else '6px'
                    cluster_value_size = '10px' if is_single_env else '7px'
                    
                    # Build cluster lines - each cluster on its own line
                    cluster_lines = '<br>'.join([f"☸️ {name}" for name in cluster_names])
                    
                    # Calculate max-height: show first cluster + scrollbar for rest
                    # Line height is 1.4em, so one line ≈ 1.4 * font_size
                    # For 10px font: 1.4 * 10 = 14px per line
                    # Show ~1.5 lines (one full cluster + hint of second)
                    max_cluster_height = '24px' if is_single_env else '18px'
                    
                    eks_cluster_html = f"""
                        <div style='background: rgba(255,255,255,0.25); padding: {inner_padding}; border-radius: 2px; border: 1px solid rgba(255,255,255,0.4); margin-bottom: {gap_size};'>
                            <div style='font-size: {cluster_value_size}; font-weight: 600; color: white; line-height: 1.4; max-height: {max_cluster_height}; overflow-y: auto; overflow-x: hidden;'>
                                {cluster_lines}
                            </div>
                        </div>
                    """
                
                output += f"""
                    <div class='service-box-clickable{alert_class}' style='background: {bg_color}; padding: {box_padding}; border-radius: 3px; box-shadow: 0 1px 2px rgba(0,0,0,0.12); min-height: {box_min_height}; display: flex; flex-direction: column; justify-content: space-between;' onclick="window.open('{dd_url}', '_blank')" title='{tooltip}'>
                        <div style='margin-bottom: 4px;'>
                            <div style='display: flex; justify-content: space-between; align-items: start;'>
                                <div style='font-size: {service_font_size}; font-weight: 600; color: white; line-height: 1.2; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1;'>
                                    {svc['service']}
                                </div>
                                {f"<div style='font-size: {badge_font_size}; line-height: 1;'>{badges_str}</div>" if badges_str else ""}
                            </div>
                        </div>
                        {eks_cluster_html}
                        <div style='display: grid; grid-template-columns: {metrics_grid}; gap: {gap_size};'>
                            <div style='background: rgba(255,255,255,0.25); padding: {inner_padding}; border-radius: 2px; border: 1px solid rgba(255,255,255,0.4);'>
                                <div style='font-size: {label_font_size}; color: rgba(255,255,255,0.95); text-transform: uppercase; margin-bottom: 2px;'>REQ</div>
                                <div style='font-size: {value_font_size}; font-weight: bold; color: white;'>{svc['requests']:,}</div>
                            </div>
                            <div style='background: rgba(255,255,255,0.25); padding: {inner_padding}; border-radius: 2px; border: 1px solid rgba(255,255,255,0.4);'>
                                <div style='font-size: {label_font_size}; color: rgba(255,255,255,0.95); text-transform: uppercase; margin-bottom: 2px;'>ERR %</div>
                                <div style='font-size: {value_font_size}; font-weight: bold; color: white;'>{svc['error_rate']}%</div>
                            </div>
                            {f'''<div style='background: rgba(255,255,255,0.25); padding: {inner_padding}; border-radius: 2px; border: 1px solid rgba(255,255,255,0.4);'>
                                <div style='font-size: {label_font_size}; color: rgba(255,255,255,0.95); text-transform: uppercase; margin-bottom: 2px;'>P95</div>
                                <div style='font-size: {value_font_size}; font-weight: bold; color: white;'>{int(svc['p95_latency'])}ms</div>
                            </div>''' if show_latency else ''}
                        </div>
                    </div>
                """
        
            output += """
            </div>
        </div>
        """
    
    output += """
    </div>
    </div>
    </div>
    </div>
    """
    
    # Generate detailed alert summary for logging
    critical_services = [s for s in all_statuses if s['status'] == 'critical']
    warning_services = [s for s in all_statuses if s['status'] == 'warning']
    
    print(f"✅ Dashboard generated: {total_services} active services ({total_healthy} healthy, {total_warning} warning, {total_critical} critical)")
    
    if critical_services:
        print(f"\n🚨 CRITICAL SERVICES ({len(critical_services)}):")
        for svc in critical_services:
            reasons = []
            if svc.get('pd_incident'):
                reasons.append("PagerDuty Alert")
            if svc.get('traffic_drop'):
                reasons.append("Traffic Drop")
            if svc['error_rate'] > 3:
                reasons.append(f"High Error Rate: {svc['error_rate']}%")
            if svc.get('high_latency'):
                reasons.append(f"High Latency: P95={svc.get('p95_latency')}ms")
            print(f"   • {svc['service']} ({svc['environment']}): {', '.join(reasons)}")
    
    if warning_services:
        print(f"\n⚠️ WARNING SERVICES ({len(warning_services)}):")
        for svc in warning_services[:10]:  # Show first 10
            reasons = []
            if svc['error_rate'] > 0.5:
                reasons.append(f"Error Rate: {svc['error_rate']}%")
            if svc.get('high_latency'):
                reasons.append(f"High Latency: P95={svc.get('p95_latency')}ms")
            print(f"   • {svc['service']} ({svc['environment']}): {', '.join(reasons)}")
    
    # Save metrics to database for historical analysis
    try:
        current_timestamp = datetime.utcnow().isoformat()
        
        # Save individual service metrics
        save_service_metrics(all_statuses, current_timestamp)
        
        # Save dashboard snapshot
        dashboard_snapshot = {
            'environment': environment if environment else 'all',
            'total_services': total_services,
            'healthy_count': total_healthy,
            'warning_count': total_warning,
            'critical_count': total_critical,
            'total_requests': total_requests,
            'total_errors': total_errors,
            'overall_error_rate': overall_error_rate,
            'pd_triggered': pd_triggered,
            'pd_acknowledged': pd_acknowledged,
            'pd_resolved': pd_resolved
        }
        save_dashboard_snapshot(dashboard_snapshot, current_timestamp)
    except Exception as e:
        print(f"⚠️ Error saving metrics to database: {e}")
    
    # Cache the result
    _status_cache[cache_key] = output
    
    # Clean old cache entries (keep only last 5)
    if len(_status_cache) > 5:
        oldest_key = min(_status_cache.keys())
        del _status_cache[oldest_key]
    
    return output
