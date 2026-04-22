"""
Status Monitor Dashboard Tool
Real-time service health monitoring across all environments using Datadog APM
"""

import os
import re
import time
import json
import html
import threading
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import metrics persistence
from urllib.parse import quote

from tools.metrics_persistence import (
    save_service_metrics,
    save_dashboard_snapshot,
    get_dashboard_history,
    sm_api_cache_get,
    sm_api_cache_set,
    clear_status_monitor_api_cache,
)

# Import Datadog dashboard utilities
from tools.datadog_dashboards import datadog_ui_origin, get_dashboard_details
from tools.status_monitor_service_lists import (
    ADT_MONITOR_SERVICES,
    GENERAL_MONITOR_SERVICES,
    SAMSUNG_MONITOR_SERVICES,
)


def _sm_cluster_tokens_z1_z2(cluster_blob: str) -> tuple[bool, bool]:
    """
    Arlo-style cluster naming: Z1 → Ireland, Z2 → Oregon.

    - Standalone token: z1 / z2 (not glued to other alphanumeric ids).
    - Prefix: z1* / z2* (e.g. z1w, z2w, z1-prod) — cluster name encodes region in the suffix.
    """
    if not cluster_blob or not cluster_blob.strip():
        return False, False

    def _zone(zone: str) -> bool:
        # Standalone z1 / z2 (word-ish token)
        if re.search(rf"(?<![a-z0-9]){zone}(?![a-z0-9])", cluster_blob, re.I):
            return True
        # Prefix z1* / z2* (e.g. z2w, z1a, z1-eks)
        if re.search(rf"(?<![a-z0-9]){zone}(?=[a-z0-9\-_./])", cluster_blob, re.I):
            return True
        return False

    return _zone("z1"), _zone("z2")


def _sm_infer_service_region(svc: dict, *, page_environment: str | None = None) -> str:
    """
    Infer service region from EKS cluster names and known tokens.
    Returns: Oregon, Ireland, or Multi-region.

    Cluster names: Z1 / z1* → Ireland, Z2 / z2* → Oregon (case-insensitive). If multiple clusters
    span both, Multi-region. If clusters exist but neither z1 nor z2 appears → default region.
    Falls back to AWS region hints (us-west-2, eu-west-1, …), then default.

    For ADT and Samsung pages/wall sections, every service is US-West (Oregon); unknown signal
    (e.g. HMSWEB with no cluster hint) defaults to Oregon instead of Ireland.
    """
    oregon_default = page_environment in ("adt", "samsung")

    def _default_unknown() -> str:
        return "Oregon" if oregon_default else "Ireland"

    clusters = svc.get("eks_clusters") or []
    if not isinstance(clusters, list):
        clusters = []
    cluster_blob = " ".join(str(x) for x in clusters if x)
    if cluster_blob.strip():
        oz1, oz2 = _sm_cluster_tokens_z1_z2(cluster_blob)
        if oz1 and oz2:
            return "Multi-region"
        if oz1:
            return "Ireland"
        if oz2:
            return "Oregon"
        return _default_unknown()

    haystacks = [str(x).lower() for x in clusters if x]
    for key in ("service", "environment"):
        v = svc.get(key)
        if isinstance(v, str) and v.strip():
            haystacks.append(v.lower())
    blob = " ".join(haystacks)
    if not blob:
        return _default_unknown()

    has_oregon = any(tok in blob for tok in ("us-west-2", "usw2", "oregon"))
    has_ireland = any(tok in blob for tok in ("eu-west-1", "euw1", "ireland"))
    if has_oregon and has_ireland:
        return "Multi-region"
    if has_oregon:
        return "Oregon"
    if has_ireland:
        return "Ireland"
    return _default_unknown()


def _sm_hover_json_attr(payload: dict) -> str:
    """HTML attribute value for data-sm-hover (JSON)."""
    return html.escape(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), quote=True)


def _status_monitor_int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(os.getenv(name, str(default)))
        return max(lo, min(hi, v))
    except (TypeError, ValueError):
        return default


def _sm_status_shows_issue_links(svc: dict) -> bool:
    """True when we show Splunk / PD issue links (problem signal)."""
    st = svc.get("status")
    if st in ("critical", "warning"):
        return True
    if svc.get("pd_incident"):
        return True
    if svc.get("traffic_drop"):
        return True
    if svc.get("high_latency"):
        return True
    return False


def _sm_splunk_service_search_url(service_name: str, timerange_hours: int = 24) -> str:
    """Splunk Cloud search URL for logs mentioning this APM service name."""
    sn = (service_name or "").strip()
    if not sn:
        return ""
    host = (os.getenv("SPLUNK_HOST") or "arlo.splunkcloud.com").strip()
    host = host.replace("https://", "").split("/")[0]
    locale = (os.getenv("SPLUNK_UI_LOCALE") or "en-US").strip()
    h = max(1, min(int(timerange_hours), 168))
    q = f"search index=* service={sn} earliest=-{h}h"
    return f"https://{host}/{locale}/app/search/search?q={quote(q, safe='')}"


def _sm_hover_service_payload(svc: dict, env: str, page_environment: str | None = None) -> dict:
    ek = svc.get("eks_clusters") or []
    if not isinstance(ek, list):
        ek = []
    spl_h = _status_monitor_int_env("STATUS_MONITOR_SPLUNK_SEARCH_HOURS", 24, 1, 168)
    spl_url = None
    if _sm_status_shows_issue_links(svc):
        su = _sm_splunk_service_search_url(str(svc.get("service") or ""), spl_h)
        spl_url = su if su else None
    pd_u = (svc.get("pd_incident_url") or "").strip()
    return {
        "type": "service",
        "service": svc.get("service"),
        "environment": env,
        "region": _sm_infer_service_region(svc, page_environment=page_environment),
        "status": svc.get("status"),
        "error_rate": float(svc.get("error_rate") or 0),
        "requests": svc.get("requests"),
        "errors": svc.get("errors"),
        "p95_latency": svc.get("p95_latency"),
        "p99_latency": svc.get("p99_latency"),
        "eks_clusters": ek[:12],
        "pd_incident": bool(svc.get("pd_incident")),
        "pd_incident_url": pd_u if pd_u else None,
        "splunk_url": spl_url,
        "traffic_drop": bool(svc.get("traffic_drop")),
        "traffic_variance": svc.get("traffic_variance"),
        "high_latency": bool(svc.get("high_latency")),
    }


# Simple in-memory cache for status monitor data
_status_cache = {}
_hub_summary_cache = {}
_wall_data_cache = {}
# When each in-memory cache entry was stored (for force_refresh grace window)
_mem_cache_saved_at = {}
# Longer default TTL + env override reduces repeated full DD fan-out (CPU + rate limits)
_cache_ttl = _status_monitor_int_env("STATUS_MONITOR_CACHE_SECS", 180, 60, 900)
# SQLite-backed API cache (per-service DD health, PagerDuty, Arlo) — shared across hub/wall/dashboard
_db_api_cache_ttl = _status_monitor_int_env("STATUS_MONITOR_DB_CACHE_SECS", 180, 30, 900)
# User clicked Refresh: still reuse full response + DB rows if younger than this (seconds)
_FORCE_REFRESH_GRACE_SECS = _status_monitor_int_env("STATUS_MONITOR_FORCE_REFRESH_GRACE_SECS", 30, 5, 300)
# Extra live Datadog attempts when status is unknown (transient errors)
_UNKNOWN_RETRY_COUNT = _status_monitor_int_env("STATUS_MONITOR_UNKNOWN_RETRIES", 2, 0, 5)

# Parallel Datadog health checks per dashboard/hub mode (each task ~2 HTTP calls)
STATUS_MONITOR_DD_MAX_WORKERS = _status_monitor_int_env("STATUS_MONITOR_DD_MAX_WORKERS", 10, 2, 32)
STATUS_MONITOR_DD_MIN_WORKERS = _status_monitor_int_env("STATUS_MONITOR_DD_MIN_WORKERS", 3, 1, 16)
# Hub: parallel tasks (1 batch main-3 + samsung + adt + red-us = 4 jobs). Higher = faster if DD allows.
STATUS_MONITOR_HUB_PARALLEL_ENVS = _status_monitor_int_env("STATUS_MONITOR_HUB_PARALLEL_ENVS", 4, 1, 6)
STATUS_MONITOR_EKS_MAX_WORKERS = _status_monitor_int_env("STATUS_MONITOR_EKS_MAX_WORKERS", 6, 1, 24)


def _dd_health_worker_count(num_tasks: int) -> int:
    cap = min(STATUS_MONITOR_DD_MAX_WORKERS, max(STATUS_MONITOR_DD_MIN_WORKERS, num_tasks))
    return cap


# Datadog query timeout (seconds) — too low causes false "no data" under parallel load
_DD_QUERY_TIMEOUT = 10

# Datadog monitor search (error-rate override): short TTL to limit API load
_DD_MONITOR_SEARCH_CACHE = {}
_DD_MONITOR_SEARCH_LOCK = threading.Lock()
_DD_MONITOR_SEARCH_TTL = _status_monitor_int_env("STATUS_MONITOR_DD_MONITOR_CACHE_SECS", 120, 30, 600)

# Cache for dashboard services (so we don't fetch dashboard details every time)
_dashboard_services_cache = {}
_dashboard_services_cache_ttl = 3600  # 1 hour


def _https_get_with_retries(
    url,
    *,
    headers=None,
    params=None,
    timeout=(12, 45),
    label="HTTPS",
    max_attempts=5,
):
    """
    GET with backoff for flaky TLS (VPN/proxy) — SSLEOF, reset by peer, etc.
    Same symptom often hits Datadog, PagerDuty, and status.arlo.com together.
    """
    import requests
    from requests.exceptions import ChunkedEncodingError, ConnectionError, SSLError, Timeout

    headers = headers or {}
    last_exc = None
    for attempt in range(max_attempts):
        try:
            return requests.get(url, headers=headers, params=params, timeout=timeout)
        except (SSLError, ConnectionError, Timeout, ChunkedEncodingError) as e:
            last_exc = e
            if attempt < max_attempts - 1:
                delay = 0.6 * (2**attempt)
                print(
                    f"⚠️ {label}: transient error, retry {attempt + 1}/{max_attempts} "
                    f"in {delay:.1f}s: {e!s}"
                )
                time.sleep(delay)
            else:
                raise
    raise last_exc


def clear_status_cache():
    """Clear the status monitor cache - useful after config changes"""
    global _status_cache, _hub_summary_cache, _wall_data_cache, _mem_cache_saved_at, _DD_MONITOR_SEARCH_CACHE
    _status_cache.clear()
    _hub_summary_cache.clear()
    _wall_data_cache.clear()
    _mem_cache_saved_at.clear()
    with _DD_MONITOR_SEARCH_LOCK:
        _DD_MONITOR_SEARCH_CACHE.clear()
    clear_status_monitor_api_cache()
    print("🧹 Status monitor cache cleared (memory + DB API cache)")


def _effective_db_cache_ttl_secs(force_refresh: bool) -> float:
    """SQLite API cache: on user Refresh use short TTL so stale rows refetch; normal uses full TTL."""
    if force_refresh:
        return float(_FORCE_REFRESH_GRACE_SECS)
    return float(_db_api_cache_ttl)


def _read_sm_mem_cache(cache_dict: dict, cache_key: str, force_refresh: bool):
    """
    In-memory HTML/JSON cache. If force_refresh, only return hit if saved within grace window.
    """
    if cache_key not in cache_dict:
        return None
    if not force_refresh:
        return cache_dict[cache_key]
    ts = _mem_cache_saved_at.get(cache_key)
    if ts is not None and time.time() - ts < _FORCE_REFRESH_GRACE_SECS:
        return cache_dict[cache_key]
    return None


def _write_sm_mem_cache(cache_dict: dict, cache_key: str, value) -> None:
    cache_dict[cache_key] = value
    _mem_cache_saved_at[cache_key] = time.time()


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
    """Yesterday AWS cost via Cost Explorer (IAM needs ce:GetCostAndUsage)."""
    del days  # unused; kept for call-site compatibility
    try:
        import boto3
        from datetime import datetime, timedelta

        aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
        aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        aws_region = os.getenv("AWS_REGION", "us-east-1")

        if not aws_access_key or not aws_secret_key:
            print("⚠️ AWS credentials not configured")
            return {
                "cost_today": 0,
                "cost_yesterday": 0,
                "error": "No credentials",
            }

        session = boto3.Session(
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=aws_region,
        )

        ce_client = session.client("ce", region_name="us-east-1")

        today = datetime.now().date()
        yesterday = today - timedelta(days=1)

        cost_response = ce_client.get_cost_and_usage(
            TimePeriod={"Start": str(yesterday), "End": str(today)},
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
        )

        cost_yesterday = 0.0
        if cost_response.get("ResultsByTime"):
            cost_yesterday = float(
                cost_response["ResultsByTime"][0]["Total"]["UnblendedCost"]["Amount"]
            )

        print(f"✅ AWS Cost Explorer: ${cost_yesterday:.2f} yesterday")

        return {
            "cost_yesterday": cost_yesterday,
            "error": None,
        }

    except Exception as e:
        print(f"❌ Error fetching AWS cost data: {e}")
        return {
            "cost_today": 0,
            "cost_yesterday": 0,
            "error": str(e),
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


def _sm_dd_monitor_error_override_enabled() -> bool:
    v = (os.getenv("STATUS_MONITOR_DD_MONITOR_ERROR_OVERRIDE") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _dd_monitor_states_allow_override(service_name, environment, dd_api_key, dd_app_key, dd_site):
    """
    Query Datadog monitor search (same facets as UI: service + env tags).
    Returns True if every matching monitor is OK-ish (no Alert/Warn).
    Returns False if any monitor is Alert or Warn.
    Returns None if no monitors match or the API fails — caller keeps error-rate status.
    """
    import requests

    cache_key = (service_name, environment, dd_site)
    now = time.time()
    with _DD_MONITOR_SEARCH_LOCK:
        hit = _DD_MONITOR_SEARCH_CACHE.get(cache_key)
        if hit and now - hit[0] < _DD_MONITOR_SEARCH_TTL:
            return hit[1]

    url = f"https://{dd_site}/api/v1/monitor/search"
    headers = {"DD-API-KEY": dd_api_key, "DD-APPLICATION-KEY": dd_app_key}
    bad_states = frozenset({"Alert", "Warn"})
    ok_states = frozenset({"OK", "No Data", "Skipped", "Ignored", "Unknown"})
    # Hyphenated service names: quoted per Datadog search reserved characters
    query_str = f'service:"{service_name}" env:{environment}'
    collected = []
    page = 0
    per_page = 100

    def _store(result):
        with _DD_MONITOR_SEARCH_LOCK:
            _DD_MONITOR_SEARCH_CACHE[cache_key] = (now, result)

    try:
        while page < 20:
            r = requests.get(
                url,
                headers=headers,
                params={"query": query_str, "page": page, "per_page": per_page},
                timeout=_DD_QUERY_TIMEOUT,
            )
            if r.status_code == 429:
                time.sleep(0.75)
                r = requests.get(
                    url,
                    headers=headers,
                    params={"query": query_str, "page": page, "per_page": per_page},
                    timeout=_DD_QUERY_TIMEOUT,
                )
            if r.status_code != 200:
                _store(None)
                return None
            data = r.json() if r.content else {}
            monitors = data.get("monitors") or []
            for m in monitors:
                st = m.get("overall_state")
                if st is None and isinstance(m.get("status"), str):
                    st = m["status"]
                if isinstance(st, str):
                    collected.append(st.strip())
            if not monitors or len(monitors) < per_page:
                break
            page += 1
    except Exception as e:
        print(f"⚠️ Datadog monitor search ({service_name}, {environment}): {e}")
        _store(None)
        return None

    if not collected:
        _store(None)
        return None
    if any(s in bad_states for s in collected):
        _store(False)
        return False
    if all(s in ok_states for s in collected):
        _store(True)
        return True
    _store(False)
    return False


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
            # Fast mode: try common APM hit patterns (no latency/baseline — still fast)
            metric_patterns = [
                ('trace.servlet.request.hits', 'trace.servlet.request.errors', 'trace.servlet.request.duration.by.service.95p'),
                ('trace.http.request.hits', 'trace.http.request.errors', 'trace.http.request.duration.by.service.95p'),
                ('trace.web.request.hits', 'trace.web.request.errors', 'trace.web.request.duration.by.service.95p'),
            ]
        
        # True if Datadog returned HTTP 200 at least once (empty series = no traffic, not timeout)
        dd_query_reachable = False
        
        # Try each pattern until we get data
        dd_query_url = f"https://{dd_site}/api/v1/query"
        for hits_metric, errors_metric, latency_metric in metric_patterns:
            hits_query = f"sum:{hits_metric}{{service:{service_name},env:{environment}}}.as_count()"
            err_query = f"sum:{errors_metric}{{service:{service_name},env:{environment}}}.as_count()"
            query = hits_query
            params = {
                "from": from_time,
                "to": to_time,
                "query": query
            }
            
            # Parallel hits + errors; one quick retry on transport errors (busy DD / parallel load)
            response = None
            err_response = None
            for attempt in (0, 1):
                fetch_out = {}

                def _dd_fetch(key, q):
                    try:
                        fetch_out[key] = requests.get(
                            dd_query_url,
                            headers=headers,
                            params={"from": from_time, "to": to_time, "query": q},
                            timeout=_DD_QUERY_TIMEOUT,
                        )
                    except Exception as ex:
                        fetch_out[key] = ex

                t_hits = threading.Thread(target=_dd_fetch, args=("hits", hits_query))
                t_err = threading.Thread(target=_dd_fetch, args=("err", err_query))
                t_hits.start()
                t_err.start()
                t_hits.join()
                t_err.join()
                response = fetch_out.get("hits")
                err_response = fetch_out.get("err")
                if not isinstance(response, Exception):
                    break
                if attempt == 0:
                    time.sleep(0.25)
            if isinstance(response, Exception):
                continue
            if isinstance(err_response, Exception):
                err_response = None
            
            if response.status_code == 200:
                dd_query_reachable = True
                data = response.json()
                if 'series' in data and len(data['series']) > 0:
                    points = data['series'][0].get('pointlist', [])
                    if points:
                        requests_count = sum(p[1] for p in points if p[1] is not None)
                        
                        if requests_count > 0 or enable_extended_metrics:
                            if err_response is not None and err_response.status_code == 200:
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
            # unreachable API / timeouts vs confirmed empty APM series
            status = 'inactive' if dd_query_reachable else 'unknown'
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

        dd_monitor_override = False
        if _sm_dd_monitor_error_override_enabled() and status in ("critical", "warning"):
            er_critical = status == "critical" and error_rate > 5 and not traffic_drop
            er_warning = status == "warning" and error_rate > 1 and not high_latency
            if er_critical or er_warning:
                m_all_ok = _dd_monitor_states_allow_override(
                    service_name, environment, dd_api_key, dd_app_key, dd_site
                )
                if m_all_ok is True:
                    prev = status
                    dd_monitor_override = True
                    status = "healthy"
                    print(
                        f"   ✅ {service_name} ({environment}): Datadog monitors all OK — "
                        f"overriding error-rate {prev} → healthy (ERR {error_rate:.2f}%)"
                    )
        
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
            'traffic_variance': round(traffic_variance, 1) if traffic_variance is not None else None,
            'dd_monitor_override': dd_monitor_override,
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
            'baseline_requests': 0,
            'dd_monitor_override': False,
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
        
        response = _https_get_with_retries(
            "https://api.pagerduty.com/incidents",
            headers=headers,
            params=params,
            timeout=(12, 45),
            label="PagerDuty",
            max_attempts=4,
        )
        
        if response.status_code == 200:
            data = response.json()
            incidents = data.get("incidents", [])
            return len(incidents)
        return 0
    except:
        return 0


def _pagerduty_fetch_slices(
    pd_api_key: str,
    force_refresh: bool,
    status_dashboard_id: str | None,
):
    """
    Single source for PagerDuty semaphore + /api/pagerduty/*-monitor widgets.

    - Account-wide (no board): last 24h — matches ops / correlation (active noise).
    - External status board (Samsung/ADT): very wide `since` (10y) — the public Resolved tab
      lists all-time resolved for the board; a 180d window omitted older demo/history rows and
      the API often omits `total` when `status_dashboard_ids[]` is set (use list length).

    total=true, same retries, SQLite cache.
    Returns (counts dict, incidents_by_status dict, active_incidents list).
    """
    ttl = _effective_db_cache_ttl_secs(force_refresh)
    # v3 account / v4 board: bump when PD list mapping changes (invalidate stale empty-row cache).
    cache_key = "v3" if not status_dashboard_id else f"board_{status_dashboard_id}_v6"
    cached = sm_api_cache_get("pagerduty_status", cache_key, ttl)
    if cached is not None and cached.get("incidents_by_status") is not None:
        scope = "board " + status_dashboard_id if status_dashboard_id else "account"
        print(
            f"🗄️ PagerDuty ({scope}): DB cache (≤{_db_api_cache_ttl}s) — "
            f"{cached['counts'].get('triggered', 0)} trg / "
            f"{cached['counts'].get('acknowledged', 0)} ack / "
            f"{cached['counts'].get('resolved', 0)} res"
        )
        return cached["counts"], cached["incidents_by_status"], cached["active_incidents"]

    counts = {}
    incidents_by_status = {"triggered": [], "acknowledged": [], "resolved": []}

    headers = {
        "Authorization": f"Token token={pd_api_key}",
        "Accept": "application/vnd.pagerduty+json;version=2",
    }

    if status_dashboard_id:
        # Match external status UI (e.g. Resolved tab): include old demos, not only last ~6 months.
        since_hours = 24 * 365 * 10
        window_lbl = "10y (board)"
    else:
        since_hours = 24
        window_lbl = "24h"
    since = (datetime.utcnow() - timedelta(hours=since_hours)).isoformat()
    statuses = ["triggered", "acknowledged", "resolved"]
    # External status boards (Samsung/ADT): paginate so lists include full history (typically small).
    _BOARD_PAGE = 100
    _BOARD_MAX_ROWS = 2500

    def _fetch_pd_incidents_for_status(status: str):
        params = {
            "statuses[]": [status],
            "since": since,
            "sort_by": "created_at:desc",
            "total": "true",
            "include[]": ["escalation_policies"],
        }
        if status_dashboard_id:
            params["status_dashboard_ids[]"] = status_dashboard_id
            params["limit"] = _BOARD_PAGE
        else:
            params["limit"] = 10

        if not status_dashboard_id:
            response = _https_get_with_retries(
                "https://api.pagerduty.com/incidents",
                headers=headers,
                params=params,
                timeout=(12, 45),
                label="PagerDuty",
                max_attempts=4,
            )
            if response.status_code != 200:
                return status, 0, []
            data = response.json()
            n = data.get("total", 0) if data.get("total") else len(data.get("incidents", []))
            inc_list = data.get("incidents", [])
            _sm_pd_attach_escalation_summaries(inc_list, data)
            return status, n, inc_list

        # Board: fetch all pages up to _BOARD_MAX_ROWS
        all_incidents: list = []
        offset = 0
        total_reported: int | None = None
        while len(all_incidents) < _BOARD_MAX_ROWS:
            p = dict(params)
            p["offset"] = offset
            response = _https_get_with_retries(
                "https://api.pagerduty.com/incidents",
                headers=headers,
                params=p,
                timeout=(12, 60),
                label="PagerDuty",
                max_attempts=4,
            )
            if response.status_code != 200:
                return status, 0, all_incidents
            data = response.json()
            if total_reported is None:
                raw_t = data.get("total")
                if raw_t is not None:
                    try:
                        total_reported = int(raw_t)
                    except (TypeError, ValueError):
                        total_reported = None
            page = data.get("incidents") or []
            _sm_pd_attach_escalation_summaries(page, data)
            all_incidents.extend(page)
            if not page or len(page) < _BOARD_PAGE:
                break
            if total_reported is not None and len(all_incidents) >= total_reported:
                break
            offset += _BOARD_PAGE
        # PD often omits or misreports `total` when filtering by status_dashboard_ids; paginated length is truth.
        n = len(all_incidents)
        return status, n, all_incidents

    # Parallel: external boards need 3 slices; sequential was ~3× latency for Samsung/ADT widgets.
    with ThreadPoolExecutor(max_workers=3) as pool:
        for status, n, inc_list in pool.map(_fetch_pd_incidents_for_status, statuses):
            counts[status] = n
            incidents_by_status[status] = inc_list

    active_incidents = incidents_by_status["triggered"] + incidents_by_status["acknowledged"]

    scope = "board " + status_dashboard_id if status_dashboard_id else "account"
    print(
        f"✅ PagerDuty ({scope}): {counts.get('triggered', 0)} trg, "
        f"{counts.get('acknowledged', 0)} ack, {counts.get('resolved', 0)} res ({window_lbl})"
    )
    print(f"🔗 Active incidents for correlation: {len(active_incidents)}")
    sm_api_cache_set(
        "pagerduty_status",
        cache_key,
        {
            "counts": counts,
            "active_incidents": active_incidents,
            "incidents_by_status": incidents_by_status,
        },
    )
    return counts, incidents_by_status, active_incidents


def get_pagerduty_status_counts(
    pd_api_key: str, force_refresh: bool = False, status_dashboard_id: str | None = None
):
    """Same PD query path as the traffic-light semaphore; optional external status board scope."""
    try:
        counts, _ibs, active_incidents = _pagerduty_fetch_slices(
            pd_api_key, force_refresh, status_dashboard_id
        )
        return counts, active_incidents
    except Exception as e:
        print(f"❌ Error fetching PagerDuty status counts: {e}")
        return {"triggered": 0, "acknowledged": 0, "resolved": 0}, []


def _pd_incident_to_monitor_dict(inc: dict) -> dict:
    """Shape expected by static/js PagerDuty monitor widgets."""
    num = inc.get("incident_number")
    if num is None:
        num = inc.get("number")
    svc = inc.get("service")
    if isinstance(svc, dict):
        svc_label = svc.get("summary") or "Unknown"
    elif svc is not None:
        svc_label = str(svc)
    else:
        svc_label = "Unknown"
    st = inc.get("status")
    if isinstance(st, str):
        st = st.lower()
    else:
        st = "unknown"
    return {
        "number": num if num is not None else "N/A",
        "title": inc.get("title") or "No title",
        "service": svc_label,
        "status": st,
        "url": inc.get("html_url") or "#",
    }


def build_pagerduty_monitor_api_payload(
    pd_api_key: str,
    status_dashboard_id: str | None,
    force_refresh: bool = False,
) -> dict:
    """
    JSON for Flask /api/pagerduty/monitor and samsung-/adt-monitor.
    Uses _pagerduty_fetch_slices: account-wide 24h; external boards use a wide since window
    and paginate the Incidents API so active + resolved lists include full history (capped in fetch).
    """
    try:
        counts, ibs, _active = _pagerduty_fetch_slices(
            pd_api_key, force_refresh, status_dashboard_id
        )
        # Do not filter by incident.status: PD responses are already bucketed by query; some payloads omit/mismatch status.
        tr = [_pd_incident_to_monitor_dict(i) for i in ibs["triggered"]]
        ack = [_pd_incident_to_monitor_dict(i) for i in ibs["acknowledged"]]
        res = [_pd_incident_to_monitor_dict(i) for i in ibs["resolved"]]
        if status_dashboard_id:
            active = tr + ack
            recently_resolved = res
        else:
            active = (tr + ack)[:10]
            recently_resolved = res[:10]
        payload = {
            "triggered": int(counts.get("triggered") or 0),
            "acknowledged": int(counts.get("acknowledged") or 0),
            "resolved": int(counts.get("resolved") or 0),
            "active": active,
            "recently_resolved": recently_resolved,
            "timestamp": time.strftime("%H:%M:%S"),
        }
        return payload
    except Exception as e:
        print(f"❌ build_pagerduty_monitor_api_payload: {e}")
        return {"error": str(e)}


def _is_meaningful_kube_cluster_name(name) -> bool:
    """Datadog may return kube_cluster_name:N/A when the tag is missing; hide those in the UI."""
    if name is None:
        return False
    s = str(name).strip()
    return bool(s) and s.upper() != "N/A"


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
                    if _is_meaningful_kube_cluster_name(cluster_name) and cluster_name not in clusters:
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


_EKS_ENV_TAG_VARIANTS = {
    "production": ["prod", "production", "samsung_prod"],
    "goldendev": ["goldendev", "dev"],
    "goldenqa": ["goldenqa", "qa"],
    "samsung_prod": ["samsung_prod", "production", "prod"],
    "adt_prod": ["adt_prod"],
}


def _resolve_eks_cluster_names(service_name: str, service_env: str, timerange_hours: int) -> list:
    """Try Datadog env tag variants (same as dashboard EKS lookup) until clusters are found."""
    for env_tag in _EKS_ENV_TAG_VARIANTS.get(service_env, [service_env]):
        found = get_service_clusters_from_metrics(service_name, env_tag, timerange_hours=timerange_hours)
        if found:
            return found
    return []


def _attach_eks_clusters_wall(statuses: list, timerange: int, eks_cache: dict | None = None) -> None:
    """Populate eks_clusters on wall rows (healthy / warning / critical) for tooltips."""
    if not statuses:
        return
    thr = max(1, int(timerange))
    cache = eks_cache if eks_cache is not None else {}
    lock = threading.Lock()

    def work(row: dict) -> None:
        if row.get("status") not in ("healthy", "warning", "critical"):
            return
        if row.get("eks_clusters"):
            return
        svc = row.get("service") or ""
        env = row.get("environment") or ""
        if not svc:
            return
        key = (svc, env)
        with lock:
            cached = key in cache
            if cached:
                names = cache[key]
        if not cached:
            resolved = _resolve_eks_cluster_names(svc, env, thr)
            with lock:
                if key not in cache:
                    cache[key] = resolved
                names = cache[key]
        if names:
            row["eks_clusters"] = names
            row["eks_cluster_count"] = len(names)

    with ThreadPoolExecutor(max_workers=STATUS_MONITOR_EKS_MAX_WORKERS) as ex:
        futs = [ex.submit(work, r) for r in statuses]
        for f in as_completed(futs):
            try:
                f.result()
            except Exception as e:
                print(f"⚠️ Status wall EKS lookup error: {e}")


def get_arlo_services_status(force_refresh: bool = False):
    """Scrape status.arlo.com to get platform service status"""
    try:
        cached = sm_api_cache_get("arlo_platform_status", "v1", _effective_db_cache_ttl_secs(force_refresh))
        if cached is not None:
            svcs = cached.get("services") or []
            print(f"🗄️ Arlo status: using DB cache (≤{_db_api_cache_ttl}s) — {len(svcs)} core services")
            return svcs

        import requests
        from bs4 import BeautifulSoup
        import logging
        
        url = "https://status.arlo.com"
        response = _https_get_with_retries(
            url,
            timeout=(10, 35),
            label="Arlo status",
            max_attempts=5,
        )
        
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
        
        sm_api_cache_set("arlo_platform_status", "v1", {"services": services})
        return services
    except Exception as e:
        print(f"❌ Error fetching Arlo platform status: {e}")
        return []


HUB_ENV_ROWS = [
    {"slug": "production", "label": "Production", "href": "/statusmonitor/production", "mode": "production"},
    {"slug": "goldendev", "label": "GoldenDev", "href": "/statusmonitor/goldendev", "mode": "goldendev"},
    {"slug": "goldenqa", "label": "GoldenQA", "href": "/statusmonitor/goldenqa", "mode": "goldenqa"},
    {"slug": "samsung", "label": "Samsung", "href": "/statusmonitor/samsung", "mode": "samsung"},
    {"slug": "adt", "label": "ADT", "href": "/statusmonitor/adt", "mode": "adt"},
    {"slug": "redmetrics-us", "label": "RED Metrics US", "href": "/statusmonitor/redmetrics-us", "mode": "redmetrics-us"},
]

# Full-screen wall: fixed section order (not the same as hub card order).
WALL_DISPLAY_GROUPS = [
    {"mode": "production", "slug": "production", "label": "Production"},
    {"mode": "adt", "slug": "adt", "label": "ADT"},
    {"mode": "samsung", "slug": "samsung", "label": "Samsung specific services"},
    {"mode": "goldenqa", "slug": "goldenqa", "label": "GoldenQA"},
    {"mode": "goldendev", "slug": "goldendev", "label": "GoldenDev"},
]


def _merge_samsung_dashboard_services(dynamic_services: list) -> list:
    """
    Always keep the 6 canonical Samsung APM service names, then add any extra
    names found on dashboard widgets that look Samsung-specific.

    Previously we replaced the canonical list entirely when the dashboard parse
    returned any services — that often yielded a partial list (or abbreviated
    names), so the monitor never queried all 6 active services.
    """
    base = list(SAMSUNG_MONITOR_SERVICES)
    seen = dict.fromkeys(base)
    out = list(base)
    for s in dynamic_services or []:
        if not s or not isinstance(s, str):
            continue
        if s in seen:
            continue
        if "samsung" not in s.lower():
            continue
        seen[s] = None
        out.append(s)
    return out


def _sm_resolve_services_and_environments(environment):
    """
    Same service list + Datadog env tag(s) as status_monitor_dashboard.
    environment None => all main envs (production, goldendev, goldenqa).
    """
    if environment is None:
        return list(GENERAL_MONITOR_SERVICES), ["production", "goldendev", "goldenqa"]
    if environment == "samsung":
        # README/APM: Samsung RED services use env tag samsung_prod (not "production").
        samsung_dd_env = (os.getenv("SAMSUNG_DD_ENV") or "samsung_prod").strip()
        if not samsung_dd_env:
            samsung_dd_env = "samsung_prod"
        dynamic_services = get_services_from_dashboard("wnz-fqh-z4f", cache_key="samsung_dashboard")
        services = _merge_samsung_dashboard_services(dynamic_services)
        return services, [samsung_dd_env]
    if environment == "adt":
        dynamic_services = get_services_from_dashboard("cum-ivw-92c", cache_key="adt_dashboard_v2")
        services = dynamic_services if dynamic_services else list(ADT_MONITOR_SERVICES)
        return services, ["adt_prod"]
    if environment == "redmetrics-us":
        dynamic_services = get_services_from_dashboard("qiz-7xc-fqr", cache_key="redmetrics_us_dashboard")
        services = dynamic_services if dynamic_services else list(GENERAL_MONITOR_SERVICES)
        return services, ["production"]
    if environment in ("production", "goldendev", "goldenqa"):
        return list(GENERAL_MONITOR_SERVICES), [environment]
    raise ValueError(f"Invalid environment '{environment}'")


def _dd_health_cache_key(service: str, env: str, timerange_hours: int, dd_site: str) -> str:
    return f"{service}\x1f{env}\x1f{int(timerange_hours)}\x1f{dd_site}"


def _sm_fetch_one_service_health_cached(
    service,
    env,
    dd_api_key,
    dd_app_key,
    dd_site,
    from_time,
    current_time,
    timerange_hours: int,
    force_refresh: bool = False,
):
    key = _dd_health_cache_key(service, env, timerange_hours, dd_site)
    ttl = _effective_db_cache_ttl_secs(force_refresh)
    cached = sm_api_cache_get("dd_service_health", key, ttl)
    if cached is not None:
        return cached, True
    out = get_service_health_status(
        service, env, dd_api_key, dd_app_key, dd_site, from_time, current_time, False
    )
    if out.get("status") == "unknown" and _UNKNOWN_RETRY_COUNT > 0:
        for attempt in range(_UNKNOWN_RETRY_COUNT):
            delay = 0.35 * (attempt + 1)
            time.sleep(delay)
            retry_out = get_service_health_status(
                service, env, dd_api_key, dd_app_key, dd_site, from_time, current_time, False
            )
            if retry_out.get("status") != "unknown":
                out = retry_out
                print(
                    f"   🔁 {service} ({env}): unknown → {retry_out.get('status')} after retry {attempt + 1}"
                )
                break
    sm_api_cache_set("dd_service_health", key, out)
    return out, False


def _sm_fetch_parallel_service_health(
    services,
    environments,
    dd_api_key,
    dd_app_key,
    dd_site,
    from_time,
    current_time,
    timerange_hours: int,
    force_refresh: bool = False,
):
    all_statuses = []
    n_health_tasks = len(services) * len(environments)
    max_workers = _dd_health_worker_count(n_health_tasks)
    eff_ttl = _effective_db_cache_ttl_secs(force_refresh)
    print(
        f"📡 DD worker pool size: {max_workers} (tasks={n_health_tasks}), "
        f"DB cache TTL={eff_ttl}s (force_refresh={force_refresh})"
    )
    cache_hits = 0
    cache_miss = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for service in services:
            for env in environments:
                futures.append(
                    executor.submit(
                        _sm_fetch_one_service_health_cached,
                        service,
                        env,
                        dd_api_key,
                        dd_app_key,
                        dd_site,
                        from_time,
                        current_time,
                        timerange_hours,
                        force_refresh,
                    )
                )
        for future in as_completed(futures):
            try:
                row, hit = future.result()
                all_statuses.append(row)
                if hit:
                    cache_hits += 1
                else:
                    cache_miss += 1
            except Exception as e:
                print(f"Error in parallel execution: {e}")
    if cache_hits:
        print(
            f"🗄️ Datadog service health: {cache_hits} from DB cache, {cache_miss} live API "
            f"(same timerange≤{eff_ttl}s)"
        )
    return all_statuses


def _sm_pd_attach_escalation_summaries(incidents: list, data: dict) -> None:
    """Rellena incident.escalation_policy.summary desde la lista hermana de la API (include=escalation_policies)."""
    if not incidents or not isinstance(data, dict):
        return
    raw_eps = list(data.get("escalation_policies") or [])
    if not raw_eps:
        for item in data.get("included") or []:
            if isinstance(item, dict) and item.get("type") == "escalation_policy":
                raw_eps.append(item)
    id_to_summary: dict = {}
    for ep in raw_eps:
        if not isinstance(ep, dict) or not ep.get("id"):
            continue
        label = (ep.get("summary") or ep.get("name") or "").strip()
        if label:
            id_to_summary[ep["id"]] = label
    for inc in incidents:
        if not isinstance(inc, dict):
            continue
        ep = inc.get("escalation_policy")
        if not isinstance(ep, dict) or not ep.get("id"):
            continue
        if (ep.get("summary") or "").strip():
            continue
        sid = ep["id"]
        if sid in id_to_summary:
            ep["summary"] = id_to_summary[sid]


def _sm_pd_escalation_policy_summary(incident: dict) -> str:
    ep = incident.get("escalation_policy")
    if isinstance(ep, dict):
        return (ep.get("summary") or ep.get("name") or "").strip()
    return ""


def _sm_pd_incident_looks_streaming(title_lower: str, service_summary_lower: str, blob: str) -> bool:
    """Heuristic: PagerDuty incident looks related to streaming / live video."""
    combined = f"{title_lower} {service_summary_lower} {blob.lower()}"
    if "streaming" in combined or "live stream" in combined:
        return True
    if re.search(r"\bstream\b", combined):
        return True
    if "p0_streaming" in combined or "p0 streaming" in combined:
        return True
    if any(x in combined for x in ("hmsmatter", "hmsrecording", "webrtc", "transcod", "kurento", "mediamtx")):
        return True
    if "cvr" in combined and ("stream" in combined or "recording" in combined or "video" in combined):
        return True
    return False


# Escalation policy name (e.g. savant-ep) → substring that should appear in the APM service name
_PD_STREAMING_EP_SVC_RULES = (
    ("savant", "savant"),
    ("cvr", "cvr"),
    ("adt", "adt"),
    ("samsung", "samsung"),
)


def _sm_pd_streaming_ep_team_services(ep_summary: str, services: list) -> list:
    """
    If the incident is streaming-related and the escalation policy names the team (e.g. savant-ep),
    return monitored services that belong to that stack.
    """
    if not ep_summary or not services:
        return []
    esl = ep_summary.lower().strip()
    found: list = []
    seen = set()
    for ep_sub, svc_sub in _PD_STREAMING_EP_SVC_RULES:
        if ep_sub not in esl:
            continue
        for svc in services:
            if svc_sub in svc.lower() and svc not in seen:
                seen.add(svc)
                found.append(svc)
        if found:
            return found
    return []


def _sm_pd_incident_search_blob(incident: dict) -> str:
    """Combined PagerDuty incident text for correlation (title, description, service)."""
    chunks = []
    for key in ("title", "description", "summary"):
        v = incident.get(key)
        if isinstance(v, str) and v.strip():
            chunks.append(v)
    body = incident.get("body")
    if isinstance(body, dict):
        det = body.get("details")
        if isinstance(det, str) and det.strip():
            chunks.append(det)
    so = incident.get("service")
    if isinstance(so, dict):
        for k in ("summary", "name", "description"):
            v = so.get(k)
            if isinstance(v, str) and v.strip():
                chunks.append(v)
    ep_sum = _sm_pd_escalation_policy_summary(incident)
    if ep_sum:
        chunks.append(ep_sum)
    return " ".join(chunks)


def _sm_pd_resolve_fuzzy_pd_services(search_blob: str, services: list) -> list:
    """
    When the PD title mentions Savant / smart notifications but not the exact DD name,
    infer monitored services and return names that exist in `services`.
    """
    if not search_blob or not services:
        return []
    combined_lower = search_blob.lower()
    triggers = (
        "smart notification",
        "smart notifications",
        "smart-notification",
        "savant",
    )
    if not any(t in combined_lower for t in triggers):
        return []
    found: list = []
    seen = set()

    def _add(svc: str) -> None:
        if svc in seen:
            return
        seen.add(svc)
        found.append(svc)

    # 1) Tokens estilo backend-* o servicio con guiones en el texto
    for m in re.finditer(
        r"\b(backend-[a-z0-9-]+|[a-z][a-z0-9]*-[a-z0-9-]{2,}[a-z0-9]*)\b",
        search_blob,
        re.I,
    ):
        tok = m.group(1).lower()
        for svc in services:
            sl = svc.lower()
            if sl == tok or sl.startswith(tok + "-") or tok == sl:
                _add(svc)

    # 2) Savant → cualquier servicio monitorizado con "savant" en el nombre
    if "savant" in combined_lower:
        for svc in services:
            if "savant" in svc.lower():
                _add(svc)

    # 3) Smart notification(s) → stack de notificaciones (nombres típicos en APM)
    if any(
        x in combined_lower
        for x in ("smart notification", "smart notifications", "smart-notification")
    ):
        subs = ("hmsnotification", "partner-notifications", "notificationservice")
        for svc in services:
            sl = svc.lower()
            if any(sub in sl for sub in subs):
                _add(svc)

    return found


# Hyphen segments shared by many monitored service names. If we match `part in title` for these,
# a title like "backend-hmsgoogleapi ..." flags every `backend-*` service (false positives).
_PD_FUZZY_PART_BLOCKLIST = frozenset(
    {
        "backend",
        "nginx",
        "device",
        "oauth",
        "partner",
        "proxy",
        "logger",
        "hmsweb",
        "secret",
        "mqtt",
        "broker",
        "privacy",
        "registration",
        "support",
        "discovery",
        "directory",
        "presence",
        "messaging",
        "history",
        "advisor",
        "geolocation",
        "mediamigrationscheduler",
        "automation",  # backend-hmsautomation, scheduler, arloautomation-leader, etc.
        "arlo",
    }
)


def _sm_apply_pagerduty_correlation(
    all_statuses, services, environments, environment_slug, pd_incidents, silent: bool = False
):
    """Mutates all_statuses in place (same rules as drill-down). pd_incidents: list or None."""
    pd_affected_services = set()
    pd_incident_urls: dict[tuple[str, str], str] = {}
    if pd_incidents:
        if not silent:
            print(f"🔍 Filtering {len(pd_incidents)} PagerDuty incidents (triggered + acknowledged only, last 24h)...")
        recent_active_incidents = []
        cutoff_time = datetime.utcnow() - timedelta(hours=24)
        for incident in pd_incidents:
            incident_status = incident.get("status", "").lower()
            if incident_status not in ["triggered", "acknowledged"]:
                continue
            created_at_str = incident.get("created_at", "")
            try:
                if created_at_str:
                    created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                    if created_at < cutoff_time:
                        continue
            except Exception as e:
                if not silent:
                    print(f"  ⚠️  Could not parse incident date: {e}")
            recent_active_incidents.append(incident)
            pd_url = (incident.get("html_url") or "").strip()

            def _note_pd_url(svc_name: str, env_name: str) -> None:
                if pd_url and (svc_name, env_name) not in pd_incident_urls:
                    pd_incident_urls[(svc_name, env_name)] = pd_url

            title_raw = incident.get("title", "")
            title = title_raw.lower() if isinstance(title_raw, str) else ""
            service_obj = incident.get("service", {})
            if isinstance(service_obj, dict):
                service_summary = service_obj.get("summary", "").lower()
            else:
                service_summary = ""
            detected_environments = []
            for env in environments:
                env_lower = env.lower()
                if env_lower in title or env_lower in service_summary:
                    detected_environments.append(env)
            if not detected_environments:
                if any(keyword in title or keyword in service_summary for keyword in ["prod", "production"]):
                    detected_environments.append("production")
                elif any(keyword in title or keyword in service_summary for keyword in ["dev", "development"]):
                    detected_environments.append("goldendev")
                elif any(keyword in title or keyword in service_summary for keyword in ["qa", "quality"]):
                    detected_environments.append("goldenqa")
                elif "samsung" in title or "samsung" in service_summary:
                    detected_environments.append(
                        environments[0] if environment_slug == "samsung" and environments else "samsung_prod"
                    )
                elif "adt" in title or "adt" in service_summary or "partnerprod" in title or "partnerprod" in service_summary:
                    detected_environments.append("adt_prod")
            if not detected_environments:
                detected_environments = environments.copy()
            matched_services = []
            for service in services:
                service_lower = service.lower()
                if service_lower in title or service_lower in service_summary:
                    for env in detected_environments:
                        pd_affected_services.add((service, env))
                        _note_pd_url(service, env)
                    matched_services.append(service)
            for service in services:
                if service not in matched_services:
                    service_parts = service.split("-")
                    for part in service_parts:
                        pl = part.lower()
                        if (
                            len(pl) > 4
                            and pl not in _PD_FUZZY_PART_BLOCKLIST
                            and pl in title
                        ):
                            for env in detected_environments:
                                pd_affected_services.add((service, env))
                                _note_pd_url(service, env)
                            matched_services.append(service)
                            break
            blob = _sm_pd_incident_search_blob(incident)
            if not matched_services:
                for svc in _sm_pd_resolve_fuzzy_pd_services(blob, services):
                    for env in detected_environments:
                        pd_affected_services.add((svc, env))
                        _note_pd_url(svc, env)
                    matched_services.append(svc)
            ep_summary = _sm_pd_escalation_policy_summary(incident)
            if (
                ep_summary
                and _sm_pd_incident_looks_streaming(title, service_summary, blob)
            ):
                ep_svcs = _sm_pd_streaming_ep_team_services(ep_summary, services)
                if ep_svcs:
                    for svc in ep_svcs:
                        for env in detected_environments:
                            pd_affected_services.add((svc, env))
                            _note_pd_url(svc, env)
                        if svc not in matched_services:
                            matched_services.append(svc)
                    if not silent:
                        print(
                            f"  📺 Streaming + escalation policy '{ep_summary}' → "
                            f"{', '.join(ep_svcs)}"
                        )
            if matched_services:
                env_str = ", ".join(detected_environments) if len(detected_environments) < len(environments) else "all envs"
                if not silent:
                    print(
                        f"  🚨 Incident [{incident_status.upper()}]: '{title[:60]}...' → "
                        f"{', '.join(matched_services)} in [{env_str}]"
                    )
        if not silent:
            print(
                f"🔗 PagerDuty correlation: {len(recent_active_incidents)} recent active incidents → "
                f"{len(pd_affected_services)} service-environment pairs affected"
            )
    else:
        if not silent:
            print("✅ No active PagerDuty incidents")
    if pd_affected_services and not silent:
        affected_display = [f"{svc} ({env})" for svc, env in list(pd_affected_services)[:10]]
        print(f"   Affected: {', '.join(affected_display)}")
    for status_obj in all_statuses:
        service_env_tuple = (status_obj["service"], status_obj["environment"])
        if service_env_tuple in pd_affected_services:
            status_obj["pd_incident_url"] = pd_incident_urls.get(service_env_tuple)
            current_status = status_obj["status"]
            if current_status == "critical":
                status_obj["pd_incident"] = True
                if not silent:
                    print(
                        f"🚨 {status_obj['service']} ({status_obj['environment']}): CRITICAL "
                        f"(metrics + PagerDuty alert)"
                    )
            elif current_status == "warning":
                status_obj["status"] = "critical"
                status_obj["pd_incident"] = True
                if not silent:
                    print(
                        f"⚠️→🚨 Escalating {status_obj['service']} ({status_obj['environment']}) to CRITICAL "
                        f"(warning metrics + PagerDuty)"
                    )
            else:
                status_obj["status"] = "warning"
                status_obj["pd_incident"] = True
                if not silent:
                    print(
                        f"✅→⚠️ {status_obj['service']} ({status_obj['environment']}): WARNING "
                        f"(healthy metrics but PagerDuty alert active)"
                    )
        else:
            status_obj["pd_incident"] = False
            status_obj["pd_incident_url"] = None


def collect_hub_statuses_aligned_with_dashboard(
    timerange: int, environment_slug: str, pd_incidents_preloaded=None, force_refresh: bool = False
) -> list:
    """
    Same per-service Datadog APM checks + PagerDuty overrides as /statusmonitor/<slug> Summary
    (no HTML / EKS / Splunk). Hub cards match drill-down counts for the same timerange.

    If pd_incidents_preloaded is not None, PagerDuty is not fetched again (hub summary passes one shared list).
    """
    dd_api_key = os.getenv("DATADOG_API_KEY")
    dd_app_key = os.getenv("DATADOG_APP_KEY")
    dd_site = os.getenv("DATADOG_SITE", "arlo.datadoghq.com")
    pd_api_key = os.getenv("PAGERDUTY_API_TOKEN")
    if not dd_api_key or not dd_app_key:
        return []
    try:
        services, environments = _sm_resolve_services_and_environments(environment_slug)
    except ValueError as e:
        print(f"⚠️ Hub aligned: {e}")
        return []
    current_time = int(time.time())
    from_time = current_time - (timerange * 3600)
    if pd_incidents_preloaded is None:
        print(
            f"🧭 Hub (aligned with dashboard): {environment_slug} — {len(services)} services × "
            f"{len(environments)} env(s), {timerange}h"
        )
    all_statuses = _sm_fetch_parallel_service_health(
        services,
        environments,
        dd_api_key,
        dd_app_key,
        dd_site,
        from_time,
        current_time,
        int(timerange),
        force_refresh,
    )
    if pd_incidents_preloaded is not None:
        pd_incidents = list(pd_incidents_preloaded)
    else:
        pd_incidents = []
        if pd_api_key:
            try:
                _pd_counts, pd_incidents = get_pagerduty_status_counts(pd_api_key, force_refresh)
            except Exception as e:
                print(f"⚠️ Hub: PagerDuty fetch failed: {e}")
    _sm_apply_pagerduty_correlation(
        all_statuses,
        services,
        environments,
        environment_slug,
        pd_incidents,
        silent=pd_incidents_preloaded is not None,
    )
    return all_statuses


def _hub_collect_main_three_envs_batched(timerange: int, pd_incidents_preloaded, force_refresh: bool = False) -> dict:
    """
    One Datadog wave for production + goldendev + goldenqa (same service list, 3 env tags).
    Faster than three separate hub passes; same results as three standalone collects.
    """
    dd_api_key = os.getenv("DATADOG_API_KEY")
    dd_app_key = os.getenv("DATADOG_APP_KEY")
    dd_site = os.getenv("DATADOG_SITE", "arlo.datadoghq.com")
    if not dd_api_key or not dd_app_key:
        return {"production": [], "goldendev": [], "goldenqa": []}
    services = list(GENERAL_MONITOR_SERVICES)
    environments = ["production", "goldendev", "goldenqa"]
    current_time = int(time.time())
    from_time = current_time - (timerange * 3600)
    n_tasks = len(services) * len(environments)
    if pd_incidents_preloaded is None:
        print(f"🧭 Hub batch: main 3 envs — {len(services)} × 3 = {n_tasks} DD tasks, {timerange}h")
    all_statuses = _sm_fetch_parallel_service_health(
        services,
        environments,
        dd_api_key,
        dd_app_key,
        dd_site,
        from_time,
        current_time,
        int(timerange),
        force_refresh,
    )
    pd_incidents = list(pd_incidents_preloaded) if pd_incidents_preloaded is not None else []
    if pd_incidents_preloaded is None:
        pd_api_key = os.getenv("PAGERDUTY_API_TOKEN")
        if pd_api_key:
            try:
                _pd_counts, pd_incidents = get_pagerduty_status_counts(pd_api_key, force_refresh)
            except Exception as e:
                print(f"⚠️ Hub batch: PagerDuty fetch failed: {e}")
    _sm_apply_pagerduty_correlation(
        all_statuses, services, environments, None, pd_incidents, silent=pd_incidents_preloaded is not None
    )
    out = {"production": [], "goldendev": [], "goldenqa": []}
    for s in all_statuses:
        env = s.get("environment")
        if env in out:
            out[env].append(s)
    return out


def _fetch_datadog_statuses_for_mode(
    timerange: int, mode: str, pd_incidents_preloaded=None, force_refresh: bool = False
) -> list:
    """Hub summary: same logic as drill-down for this environment slug."""
    return collect_hub_statuses_aligned_with_dashboard(
        timerange, mode, pd_incidents_preloaded, force_refresh
    )


def _hub_build_status_reason_lines(statuses_for_card: list, overall: str, max_lines: int = 2) -> list:
    """
    Compact English reason lines for Environment status cards when overall is warning/critical.
    """
    if overall == "healthy":
        return []
    if not statuses_for_card:
        return ["No service data available for this environment."]

    h = sum(1 for s in statuses_for_card if s.get("status") == "healthy")
    w = sum(1 for s in statuses_for_card if s.get("status") == "warning")
    c = sum(1 for s in statuses_for_card if s.get("status") == "critical")
    if c == 0 and w == 0:
        return ["No operational services (healthy/warning/critical). Check inactive/unknown."]

    bad = [s for s in statuses_for_card if s.get("status") in ("warning", "critical")]
    lines = []

    pd_count = sum(1 for s in bad if s.get("pd_incident"))
    if pd_count:
        lines.append(f"PagerDuty incidents impacting {pd_count} service(s).")

    er_crit = sum(1 for s in bad if float(s.get("error_rate") or 0) > 5 and not s.get("traffic_drop"))
    if er_crit:
        lines.append(f"Error rate above 5% on {er_crit} service(s).")

    er_warn = sum(1 for s in bad if 1 < float(s.get("error_rate") or 0) <= 5)
    if er_warn:
        lines.append(f"Error rate between 1% and 5% on {er_warn} service(s).")

    lat_count = sum(1 for s in bad if s.get("high_latency"))
    if lat_count:
        lines.append(f"High latency detected on {lat_count} service(s).")

    td_count = sum(1 for s in bad if s.get("traffic_drop"))
    if td_count:
        lines.append(f"Traffic drop vs baseline on {td_count} service(s).")

    if not lines and bad:
        lines.append("Open the environment page for per-service details.")

    return lines[:max_lines]


def _hub_collect_statuses_by_mode(
    timerange: int, log_label: str = "Hub summary", force_refresh: bool = False
) -> dict:
    """
    One shared PagerDuty pull + parallel Datadog per hub mode (same layout as hub drill-down).
    Returns dict mode -> list of per-service status dicts.
    """
    pd_api_key = os.getenv("PAGERDUTY_API_TOKEN")
    pd_incidents_shared = []
    if pd_api_key:
        try:
            _pd_counts_hub, pd_incidents_shared = get_pagerduty_status_counts(
                pd_api_key, force_refresh
            )
            print(
                f"🧭 {log_label}: shared PagerDuty pull — {len(pd_incidents_shared)} incident(s) "
                f"(triggered/ack for correlation)"
            )
        except Exception as e:
            print(f"⚠️ {log_label}: PagerDuty fetch failed: {e}")

    statuses_by_mode = {}
    with ThreadPoolExecutor(max_workers=STATUS_MONITOR_HUB_PARALLEL_ENVS) as ex:
        f_batch = ex.submit(
            _hub_collect_main_three_envs_batched, timerange, pd_incidents_shared, force_refresh
        )
        f_extra = {}
        for row in HUB_ENV_ROWS:
            if row["mode"] in ("production", "goldendev", "goldenqa"):
                continue
            f_extra[row["mode"]] = ex.submit(
                _fetch_datadog_statuses_for_mode,
                timerange,
                row["mode"],
                pd_incidents_shared,
                force_refresh,
            )
        try:
            main_three = f_batch.result()
            statuses_by_mode["production"] = main_three.get("production", [])
            statuses_by_mode["goldendev"] = main_three.get("goldendev", [])
            statuses_by_mode["goldenqa"] = main_three.get("goldenqa", [])
        except Exception as e:
            print(f"❌ Hub batch (main 3 envs) error: {e}")
            statuses_by_mode["production"] = []
            statuses_by_mode["goldendev"] = []
            statuses_by_mode["goldenqa"] = []
        for mode, fut in f_extra.items():
            try:
                statuses_by_mode[mode] = fut.result()
            except Exception as e:
                print(f"❌ {log_label} error for {mode}: {e}")
                statuses_by_mode[mode] = []
    return statuses_by_mode


def _wall_status_reason_plain(s: dict) -> str:
    """Plain-text alert context for status wall tooltip (same signals as command center reasons)."""
    parts = []
    if s.get("pd_incident"):
        parts.append("PagerDuty incident")
    if s.get("traffic_drop"):
        parts.append("Traffic drop vs 7d")
    if s.get("high_latency"):
        parts.append("High latency (APM)")
    er = float(s.get("error_rate") or 0)
    st = s.get("status")
    if st == "critical" and er > 5:
        parts.append("Error rate >5%")
    elif st == "warning" and er > 1:
        parts.append("Error rate >1%")
    tv = s.get("traffic_variance")
    if not s.get("traffic_drop") and tv is not None and abs(float(tv)) >= 12:
        parts.append(f"Traffic {float(tv):+.0f}% vs 7d")
    if not parts:
        if st == "healthy":
            parts.append("Healthy — within APM thresholds")
        elif st == "inactive":
            parts.append("No APM hits in selected time window")
        elif st == "unknown":
            parts.append("No data or APM query issue")
        elif st == "critical":
            parts.append("Critical (APM)")
        elif st == "warning":
            parts.append("Warning (APM)")
        else:
            parts.append("—")
    text = " · ".join(parts)
    if s.get("dd_monitor_override") and "override" not in text.lower():
        text = text + " · Datadog monitors OK (override)"
    return text


def _wall_service_sort_key(s: dict):
    """Critical/warning first (by error rate), then healthy alpha, then other."""
    st = s.get("status")
    er = float(s.get("error_rate") or 0)
    err_cnt = int(s.get("errors") or 0)
    name = (s.get("service") or "").lower()
    if st == "critical":
        return (0, -er, -err_cnt, name)
    if st == "warning":
        return (1, -er, -err_cnt, name)
    if st == "healthy":
        return (2, name, 0, "")
    return (3, name, 0, "")


def _wall_pd_semaphore_badge(counts: dict, label: str, scope_note: str) -> dict:
    """
    Same traffic-light rules as the main PagerDuty card near Arlo status:
    critical if triggered, warning if ack only, ok if clear.
    scope_note is shown in parentheses in detail text, e.g. '24h' or 'Samsung board PRBJIO4'.
    """
    tr = int(counts.get("triggered") or 0)
    ack = int(counts.get("acknowledged") or 0)
    if tr > 0:
        return {
            "label": label,
            "status": "critical",
            "short": f"{tr} trg",
            "detail": f"{tr} triggered, {ack} ack ({scope_note})",
        }
    if ack > 0:
        return {
            "label": label,
            "status": "warning",
            "short": f"{ack} ack",
            "detail": f"{ack} acknowledged ({scope_note})",
        }
    return {
        "label": label,
        "status": "ok",
        "short": "OK",
        "detail": f"No active incidents ({scope_note})",
    }


def _wall_pd_badge(counts: dict) -> dict:
    """Compact status for Status wall header (PagerDuty incidents, last 24h API window)."""
    return _wall_pd_semaphore_badge(counts, "PagerDuty", "24h")


def _wall_splunk_badge(payload: dict) -> dict:
    """P0 predict / outliers summary from splunk_outliers_monitor_payload."""
    if not payload.get("success"):
        err = payload.get("error") or "unavailable"
        return {"label": "Splunk", "status": "unknown", "short": "—", "detail": err}
    tools = payload.get("tools") or []
    tot = sum(int(t.get("total_outliers") or 0) for t in tools)
    th = int(payload.get("timerange_hours") or 0)
    if tot > 0:
        return {
            "label": "Splunk",
            "status": "warning",
            "short": f"{tot} out",
            "detail": f"P0 predict: {tot} outliers ({th}h)",
        }
    return {
        "label": "Splunk",
        "status": "ok",
        "short": "OK",
        "detail": f"P0 predict: no outliers ({th}h)",
    }


def _wall_split_services_by_region(services: list) -> tuple[list, list]:
    """Arlo Global (Oregon) vs Arlo EU (Ireland); Multi-region appears in both."""
    arlo_global = []
    arlo_eu = []
    for s in services:
        r = (s.get("region") or "Ireland").strip()
        if r == "Multi-region":
            arlo_global.append(s)
            arlo_eu.append(s)
        elif r == "Oregon":
            arlo_global.append(s)
        elif r == "Ireland":
            arlo_eu.append(s)
        else:
            arlo_eu.append(s)
    arlo_global.sort(key=_wall_service_sort_key)
    arlo_eu.sort(key=_wall_service_sort_key)
    return arlo_global, arlo_eu


def _samsung_status_dashboard_id_env() -> str | None:
    """Same rules as app._samsung_status_dashboard_id (external status board id)."""
    v = os.getenv("SAMSUNG_STATUS_DASHBOARD_ID")
    if v is None:
        return "PRBJIO4"
    s = str(v).strip()
    if not s or s.lower() in ("off", "false", "no", "0", "none", "*"):
        return None
    return s


def _wall_samsung_badge(pd_api_key: str | None) -> dict:
    """Samsung board pill — same traffic-light rules + PD path as main PagerDuty semaphore."""
    if not pd_api_key:
        return {
            "label": "Samsung",
            "status": "unknown",
            "short": "—",
            "detail": "PAGERDUTY_API_TOKEN not set",
        }
    bid = _samsung_status_dashboard_id_env()
    if not bid:
        return {
            "label": "Samsung",
            "status": "unknown",
            "short": "—",
            "detail": "Samsung board disabled (SAMSUNG_STATUS_DASHBOARD_ID)",
        }
    try:
        counts, _ = get_pagerduty_status_counts(pd_api_key, False, bid)
        return _wall_pd_semaphore_badge(counts, "Samsung", f"Samsung board {bid}")
    except Exception as e:
        return {"label": "Samsung", "status": "unknown", "short": "—", "detail": str(e)[:200]}


def _adt_status_dashboard_id_env() -> str | None:
    """Same rules as app._adt_status_dashboard_id."""
    v = os.getenv("ADT_STATUS_DASHBOARD_ID")
    if v is None:
        return "PK1QF1G"
    s = str(v).strip()
    if not s or s.lower() in ("off", "false", "no", "0", "none", "*"):
        return None
    return s


def _wall_adt_badge(pd_api_key: str | None) -> dict:
    """ADT board pill — same traffic-light rules + PD path as main PagerDuty semaphore."""
    if not pd_api_key:
        return {
            "label": "ADT",
            "status": "unknown",
            "short": "—",
            "detail": "PAGERDUTY_API_TOKEN not set",
        }
    bid = _adt_status_dashboard_id_env()
    if not bid:
        return {
            "label": "ADT",
            "status": "unknown",
            "short": "—",
            "detail": "ADT board disabled (ADT_STATUS_DASHBOARD_ID)",
        }
    try:
        counts, _ = get_pagerduty_status_counts(pd_api_key, False, bid)
        return _wall_pd_semaphore_badge(counts, "ADT", f"ADT board {bid}")
    except Exception as e:
        return {"label": "ADT", "status": "unknown", "short": "—", "detail": str(e)[:200]}


def _wall_fetch_monitor_badges(timerange: int, force_refresh: bool) -> dict:
    """PagerDuty + Splunk P0 + Samsung + ADT external boards for Status wall headers."""
    pd_badge = {
        "label": "PagerDuty",
        "status": "unknown",
        "short": "—",
        "detail": "PAGERDUTY_API_TOKEN not set",
    }
    pd_api_key = os.getenv("PAGERDUTY_API_TOKEN")
    if pd_api_key:
        try:
            counts, _ = get_pagerduty_status_counts(pd_api_key, force_refresh)
            pd_badge = _wall_pd_badge(counts)
        except Exception as e:
            pd_badge = {
                "label": "PagerDuty",
                "status": "unknown",
                "short": "—",
                "detail": str(e)[:200],
            }

    spl_badge = {
        "label": "Splunk",
        "status": "unknown",
        "short": "—",
        "detail": "SPLUNK_TOKEN not set",
    }
    try:
        from tools.splunk_tool import splunk_outliers_monitor_payload

        spl = splunk_outliers_monitor_payload(max(4, int(timerange)))
        spl_badge = _wall_splunk_badge(spl)
    except Exception as e:
        spl_badge = {
            "label": "Splunk",
            "status": "unknown",
            "short": "—",
            "detail": str(e)[:200],
        }

    samsung_badge = _wall_samsung_badge(pd_api_key)
    adt_badge = _wall_adt_badge(pd_api_key)

    return {
        "pagerduty": pd_badge,
        "splunk": spl_badge,
        "samsung": samsung_badge,
        "adt": adt_badge,
    }


def _wall_serialize_status(
    s: dict, dd_site: str, wall_mode: str | None = None, timerange_hours: int = 24
) -> dict:
    svc = s.get("service") or ""
    env = s.get("environment") or ""
    spl_url = None
    pd_u = (s.get("pd_incident_url") or "").strip() or None
    if _sm_status_shows_issue_links(s):
        su = _sm_splunk_service_search_url(str(svc), max(1, min(int(timerange_hours), 168)))
        spl_url = su if su else None
    return {
        "service": svc,
        "environment": env,
        "region": _sm_infer_service_region(s, page_environment=wall_mode),
        "status": s.get("status"),
        "error_rate": s.get("error_rate"),
        "requests": s.get("requests"),
        "errors": s.get("errors"),
        "pd_incident": bool(s.get("pd_incident")),
        "pd_incident_url": pd_u,
        "splunk_url": spl_url,
        "high_latency": bool(s.get("high_latency")),
        "traffic_drop": bool(s.get("traffic_drop")),
        "p95_latency": s.get("p95_latency"),
        "traffic_variance": s.get("traffic_variance"),
        "dd_monitor_override": bool(s.get("dd_monitor_override")),
        "status_reason": _wall_status_reason_plain(s),
        "eks_clusters": list(s.get("eks_clusters") or []),
        "p99_latency": s.get("p99_latency"),
        "apm_url": (
            f"{datadog_ui_origin(dd_site)}/apm/service/"
            f"{quote(str(svc), safe='')}/overview?env={quote(str(env), safe='')}"
        ),
    }


def status_monitor_wall_data(timerange: int = 1, force_refresh: bool = False) -> dict:
    """
    JSON for /statuswall: all hub environments in a fixed order, per-service tiles
    (same Datadog APM + PagerDuty semantics as the hub).

    Wall shows only operational tiles (healthy / warning / critical). inactive and
    unknown are omitted so the screen stays focused on live APM signal + issues.
    """
    global _wall_data_cache
    cache_version = "wall_v14_pd_semaphore_align"
    cache_key = f"{cache_version}_{timerange}_{int(time.time() // _cache_ttl)}"
    hit = _read_sm_mem_cache(_wall_data_cache, cache_key, force_refresh)
    if hit is not None:
        return dict(hit)

    statuses_by_mode = _hub_collect_statuses_by_mode(timerange, "Status wall", force_refresh)
    dd_site = os.getenv("DD_SITE", "datadoghq.com")
    monitors = _wall_fetch_monitor_badges(timerange, force_refresh)
    groups = []
    eks_wall_cache = {}
    for g in WALL_DISPLAY_GROUPS:
        mode = g["mode"]
        statuses = list(statuses_by_mode.get(mode) or [])
        if mode == "samsung":
            canon = set(SAMSUNG_MONITOR_SERVICES)
            statuses = [s for s in statuses if s.get("service") in canon]
        statuses = [
            s
            for s in statuses
            if s.get("status") in ("healthy", "warning", "critical")
        ]
        statuses.sort(key=_wall_service_sort_key)
        _attach_eks_clusters_wall(statuses, timerange, eks_wall_cache)

        h = sum(1 for s in statuses if s.get("status") == "healthy")
        w = sum(1 for s in statuses if s.get("status") == "warning")
        c = sum(1 for s in statuses if s.get("status") == "critical")
        unk = 0
        inn = 0
        if c > 0:
            overall = "critical"
        elif w > 0:
            overall = "warning"
        else:
            overall = "healthy"

        ser = [_wall_serialize_status(s, dd_site, mode, timerange) for s in statuses]
        if mode in ("adt", "samsung"):
            region_columns = [
                {
                    "key": "oregon_all",
                    "label": "Services",
                    "subtitle": "Oregon",
                    "services": list(ser),
                },
            ]
        else:
            ag, ar_eu = _wall_split_services_by_region(ser)
            region_columns = [
                {
                    "key": "arlo_global",
                    "label": "Arlo Global",
                    "subtitle": "Oregon",
                    "services": ag,
                },
                {
                    "key": "arlo_eu",
                    "label": "Arlo EU",
                    "subtitle": "Ireland",
                    "services": ar_eu,
                },
            ]
        groups.append(
            {
                "slug": g["slug"],
                "label": g["label"],
                "mode": mode,
                "overall": overall,
                "counts": {
                    "healthy": h,
                    "warning": w,
                    "critical": c,
                    "unknown": unk,
                    "inactive": inn,
                    "total": len(statuses),
                },
                "services": ser,
                "region_columns": region_columns,
            }
        )

    out = {"success": True, "timerange": timerange, "monitors": monitors, "groups": groups}
    _write_sm_mem_cache(_wall_data_cache, cache_key, out)
    return dict(out)


def status_monitor_hub_summary(timerange: int = 1, force_refresh: bool = False) -> dict:
    """
    JSON summary for the /statusmonitor hub: one card per environment.

    Same per-service Datadog APM checks and PagerDuty rules as /statusmonitor/<env>
    (Summary panel), for the same timerange — not aggregate-only queries.
    """
    global _hub_summary_cache
    cache_version = "hub_v12_status_reason_lines"
    cache_key = f"{cache_version}_{timerange}_{int(time.time() // _cache_ttl)}"
    hit = _read_sm_mem_cache(_hub_summary_cache, cache_key, force_refresh)
    if hit is not None:
        return dict(hit)

    statuses_by_mode = _hub_collect_statuses_by_mode(timerange, "Hub summary", force_refresh)

    env_payload = []
    for row in HUB_ENV_ROWS:
        statuses = statuses_by_mode.get(row["mode"], [])
        if row["slug"] == "samsung":
            canon = set(SAMSUNG_MONITOR_SERVICES)
            statuses_for_card = [s for s in statuses if s.get("service") in canon]
        else:
            statuses_for_card = statuses
        h = sum(1 for s in statuses_for_card if s.get("status") == "healthy")
        w = sum(1 for s in statuses_for_card if s.get("status") == "warning")
        c = sum(1 for s in statuses_for_card if s.get("status") == "critical")
        unk = sum(1 for s in statuses_for_card if s.get("status") == "unknown")
        inn = sum(1 for s in statuses_for_card if s.get("status") == "inactive")
        if c > 0:
            overall = "critical"
        elif w > 0:
            overall = "warning"
        elif len(statuses_for_card) > 0 and h == 0 and w == 0 and c == 0:
            overall = "warning"
        else:
            overall = "healthy"
        operational = h + w + c
        configured = len(statuses_for_card)
        entry = {
            "slug": row["slug"],
            "label": row["label"],
            "href": row["href"],
            "healthy": h,
            "warning": w,
            "critical": c,
            "unknown": unk,
            "inactive": inn,
            "operational": operational,
            "configured": configured,
            "monitored": configured,
            "overall": overall,
            "status_reason_lines": _hub_build_status_reason_lines(statuses_for_card, overall),
        }
        env_payload.append(entry)

    order = {row["slug"]: i for i, row in enumerate(HUB_ENV_ROWS)}
    env_payload.sort(key=lambda r: order.get(r["slug"], 99))

    out = {"success": True, "timerange": timerange, "environments": env_payload}
    _write_sm_mem_cache(_hub_summary_cache, cache_key, out)
    return dict(out)


def _splunk_p0_zone_hover_title(label: str, zones_list) -> str:
    """Multiline tooltip: label + z1–z4 outlier counts for one P0 tool."""
    zmap = {z.get("zone"): z for z in (zones_list or []) if z and z.get("zone")}
    lines = [f"{label} — outliers by zone (LLP predict):"]
    for zn in ("z1", "z2", "z3", "z4"):
        z = zmap.get(zn) or {}
        o = int(z.get("outliers") or 0)
        err = z.get("error")
        if err:
            lines.append(f"  {zn.upper()}: data unavailable")
        else:
            lines.append(f"  {zn.upper()}: {o} outlier(s)")
    return "\n".join(lines)


def _splunk_p0_semaphore_light_html(
    spl_by_id: dict,
    tid: str,
    short_label: str,
    tooltip_label: str,
    default_url: str,
) -> str:
    """Una luz compacta + etiqueta corta + Σ; hover = zonas z1–z4."""
    row = spl_by_id.get(tid) or {}
    url = (row.get("dashboard_url") or "").strip() or default_url
    url_e = html.escape(url, quote=True)
    tot = int(row.get("total_outliers") or 0)
    zones = row.get("zones") or []
    title = _splunk_p0_zone_hover_title(tooltip_label, zones)
    title_e = html.escape(title, quote=True)
    dot_bg = "#22c55e" if tot == 0 else "#ef4444"
    dot_sh = "rgba(34,197,94,0.55)" if tot == 0 else "rgba(239,68,68,0.55)"
    return (
        f'<a href="{url_e}" target="_blank" rel="noopener" title="{title_e}" '
        f'style="display:inline-flex;flex-direction:column;align-items:center;gap:2px;'
        f'text-decoration:none;color:#fff;min-width:40px;max-width:72px;">'
        f'<span style="width:12px;height:12px;border-radius:50%;background:{dot_bg};'
        f"box-shadow:0 0 8px {dot_sh};flex-shrink:0;\"></span>"
        f'<span style="font-size:9px;font-weight:800;opacity:0.95;line-height:1.1;">'
        f"{html.escape(short_label)}</span>"
        f'<span style="font-size:11px;font-weight:900;line-height:1;">{tot}</span>'
        f"</a>"
    )


def _splunk_p0_semaphore_bar_html(spl_by_id: dict) -> str:
    """Horizontal traffic-light row (Str · CVR · ADT · US); compact height."""
    items = [
        (
            "p0_streaming",
            "Str",
            "Streaming",
            "https://arlo.splunkcloud.com/en-US/app/arlo_sre/p0_streaming_dashboard",
        ),
        ("p0_cvr", "CVR", "CVR", "https://arlo.splunkcloud.com/en-US/app/arlo_sre/p0_cvr_dashboard"),
        (
            "p0_adt",
            "ADT",
            "ADT",
            "https://arlo.splunkcloud.com/en-US/app/search/p0_streaming_dashboard_pp",
        ),
        (
            "p0_streaming_us_infra",
            "US",
            "US infra",
            "https://arlo.splunkcloud.com/en-US/app/arlo_sre/p0_streaming_dashboard__us_infra",
        ),
    ]
    parts = [
        _splunk_p0_semaphore_light_html(spl_by_id, tid, short, tip, u)
        for tid, short, tip, u in items
    ]
    sep = '<span style="opacity:0.45;font-size:11px;font-weight:700;padding:0 1px;">|</span>'
    inner = sep.join(parts)
    return (
        f'<div class="spl-p0-sem" style="display:flex;flex-wrap:wrap;align-items:flex-end;'
        f'justify-content:center;gap:6px 10px;padding:2px 0;">{inner}</div>'
    )


def status_monitor_dashboard(timerange: int = 1, environment: str = None, force_refresh: bool = False) -> str:
    """
    Generate Status Monitor Dashboard HTML
    
    Args:
        timerange: Time range in hours (default 1)
        environment: Specific environment to display ('production', 'goldendev', 'goldenqa', or None for all)
    
    Returns:
        HTML string for the dashboard
    """
    global _status_cache
    
    # Check cache first - include version to invalidate cache when logic changes
    cache_version = "v3.4.14_oregon_adt_samsung_region"  # Change this when logic changes
    cache_key = f"{cache_version}_{timerange}_{environment}_{int(time.time() // _cache_ttl)}"
    hit = _read_sm_mem_cache(_status_cache, cache_key, force_refresh)
    if hit is not None:
        print(f"✅ Using cached dashboard data (cache key: {cache_key}, force_refresh={force_refresh})")
        return hit
    
    print(f"🔄 Cache miss - fetching fresh data (key: {cache_key}, force_refresh={force_refresh})")
    
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
        .cc-strip {
            background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
            border-radius: 10px;
            padding: 14px 16px;
            margin-bottom: 16px;
            border: 1px solid #e2e8f0;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
        }
        .cc-kpi-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 8px;
            margin-bottom: 14px;
        }
        .cc-kpi {
            background: #f1f5f9;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 10px 12px;
            text-align: center;
        }
        .cc-kpi-val {
            font-size: 26px;
            font-weight: 800;
            color: #0f172a;
            letter-spacing: -0.03em;
            line-height: 1.1;
        }
        .cc-kpi-lbl {
            font-size: 10px;
            font-weight: 600;
            color: #64748b;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            margin-top: 4px;
        }
        .cc-delta-up { color: #dc2626; font-size: 12px; font-weight: 700; }
        .cc-delta-down { color: #15803d; font-size: 12px; font-weight: 700; }
        .cc-delta-flat { color: #64748b; font-size: 12px; font-weight: 600; }
        .cc-attention-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 11px;
            color: #334155;
        }
        .cc-attention-table th {
            text-align: left;
            padding: 6px 8px;
            color: #64748b;
            font-weight: 700;
            text-transform: uppercase;
            font-size: 9px;
            letter-spacing: 0.05em;
            border-bottom: 1px solid #e2e8f0;
        }
        .cc-attention-table td {
            padding: 7px 8px;
            border-bottom: 1px solid #f1f5f9;
            vertical-align: middle;
        }
        .cc-attention-table tr:hover td {
            background: #f8fafc;
        }
        .cc-cluster-select {
            max-width: 168px;
            width: 100%;
            font-size: 10px;
            font-weight: 600;
            padding: 4px 8px;
            border-radius: 6px;
            border: 1px solid #cbd5e1;
            background: #ffffff;
            color: #0f172a;
            cursor: pointer;
        }
        .cc-cluster-select:focus {
            outline: 2px solid #38bdf8;
            outline-offset: 1px;
        }
        .cc-err-bar-wrap {
            width: 38px;
            max-width: 38px;
            flex-shrink: 0;
        }
        .cc-err-bar-track {
            width: 100%;
            min-width: 0;
            height: 10px;
            background: #e2e8f0;
            border-radius: 5px;
            overflow: hidden;
            box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.06);
        }
        .cc-err-bar-fill {
            height: 100%;
            border-radius: 5px;
            min-width: 0;
            transition: width 0.35s ease;
        }
        .cc-pill {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 999px;
            font-size: 9px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }
        .cc-pill-watch {
            background: #16a34a;
            color: #f0fdf4;
        }
        .cc-link-btn {
            display: inline-block;
            margin-top: 8px;
            padding: 6px 12px;
            background: #e0f2fe;
            border: 1px solid #7dd3fc;
            color: #0369a1;
            border-radius: 6px;
            font-size: 10px;
            font-weight: 700;
            text-decoration: none;
            cursor: pointer;
        }
        .cc-link-btn:hover {
            background: #bae6fd;
            color: #0c4a6e;
        }
        .sm-healthy-chips {
            display: flex;
            flex-wrap: wrap;
            gap: 5px;
            margin-top: 6px;
            align-items: center;
        }
        /* Operational: green squares (~10% smaller vs v3.2.1, tighter grid) */
        .sm-hub-cell.sm-tip-wrap { position: relative; overflow: visible; }
        .sm-tip-wrap {
            position: relative;
            min-width: 0;
            overflow: visible;
            display: flex;
            align-items: stretch;
        }
        .sm-tip-wrap > .sm-op-tile {
            flex: 1;
            width: 100%;
            min-width: 0;
        }
        .sm-op-tiles {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(96px, 1fr));
            gap: 5px;
            margin-top: 5px;
            justify-items: stretch;
        }
        .sm-op-tile {
            aspect-ratio: 1;
            min-height: 96px;
            max-height: 120px;
            border-radius: 12px;
            background: linear-gradient(165deg, #86efac 0%, #4ade80 32%, #22c55e 68%, #15803d 100%);
            border: 1px solid #166534;
            box-shadow: 0 3px 10px rgba(34, 197, 94, 0.5);
            color: #052e16;
            cursor: pointer;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            padding: 7px 6px;
            text-align: center;
            transition: transform 0.12s ease, box-shadow 0.12s ease;
            text-decoration: none;
            box-sizing: border-box;
        }
        a.sm-op-tile {
            text-decoration: none;
            color: inherit;
        }
        .sm-op-tile:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 22px rgba(34, 197, 94, 0.58);
            border-color: #14532d;
        }
        .sm-op-tile-icon {
            font-size: 15px;
            line-height: 1;
            margin-bottom: 5px;
            color: #f0fdf4;
            text-shadow: 0 1px 2px rgba(0,0,0,0.2);
        }
        .sm-op-tile-name {
            font-size: 10px;
            font-weight: 800;
            line-height: 1.15;
            color: #f0fdf4;
            text-shadow: 0 1px 2px rgba(0,0,0,0.25);
            word-break: break-word;
            overflow: hidden;
            display: -webkit-box;
            -webkit-line-clamp: 3;
            -webkit-box-orient: vertical;
            max-height: 3.45em;
            letter-spacing: -0.02em;
        }
        .sm-op-tile-metric {
            font-size: 14px;
            font-weight: 800;
            margin-top: 5px;
            color: #ecfdf5;
            letter-spacing: -0.03em;
            text-shadow: 0 1px 2px rgba(0,0,0,0.2);
        }
        .sm-op-tile-metric-lbl {
            font-size: 7.5px;
            font-weight: 700;
            color: #dcfce7;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-top: 1px;
        }
        /* Live issues: same footprint as operational tiles */
        .sm-op-tile--warn {
            background: linear-gradient(165deg, #fef08a 0%, #facc15 35%, #f59e0b 72%, #d97706 100%);
            border: 1px solid #b45309;
            box-shadow: 0 3px 10px rgba(245, 158, 11, 0.55);
        }
        .sm-op-tile--warn:hover {
            box-shadow: 0 8px 22px rgba(245, 158, 11, 0.55);
            border-color: #92400e;
        }
        .sm-op-tile--warn .sm-op-tile-icon,
        .sm-op-tile--warn .sm-op-tile-name,
        .sm-op-tile--warn .sm-op-tile-metric,
        .sm-op-tile--warn .sm-op-tile-metric-lbl {
            color: #422006;
            text-shadow: 0 1px 0 rgba(255,255,255,0.35);
        }
        .sm-op-tile--crit {
            background: linear-gradient(165deg, #fca5a5 0%, #f87171 32%, #ef4444 65%, #b91c1c 100%);
            border: 1px solid #7f1d1d;
            box-shadow: 0 3px 12px rgba(239, 68, 68, 0.55);
        }
        .sm-op-tile--crit:hover {
            box-shadow: 0 8px 24px rgba(220, 38, 38, 0.6);
            border-color: #450a0a;
        }
        .sm-op-tile--crit .sm-op-tile-icon,
        .sm-op-tile--crit .sm-op-tile-name,
        .sm-op-tile--crit .sm-op-tile-metric,
        .sm-op-tile--crit .sm-op-tile-metric-lbl {
            color: #fff7ed;
            text-shadow: 0 1px 2px rgba(0,0,0,0.25);
        }
        .sm-op-tile--crit.service-box-alert {
            animation: blink-alert 2s ease-in-out infinite;
        }
        .sm-op-tile--warn.service-box-alert {
            animation: blink-alert 2s ease-in-out infinite;
        }
        .sm-band-nosig {
            background: linear-gradient(180deg, #f8fafc 0%, #f1f5f9 100%);
            border: 1px solid #cbd5e1;
            border-radius: 10px;
            padding: 10px 10px 12px;
            margin-top: 8px;
        }
        .sm-op-tile--nosig {
            background: linear-gradient(165deg, #94a3b8 0%, #64748b 45%, #475569 100%);
            border: 1px solid #334155;
            box-shadow: 0 2px 8px rgba(51, 65, 85, 0.35);
        }
        .sm-op-tile--nosig:hover {
            box-shadow: 0 6px 18px rgba(51, 65, 85, 0.45);
            border-color: #1e293b;
        }
        .sm-op-tile--nosig .sm-op-tile-icon,
        .sm-op-tile--nosig .sm-op-tile-name,
        .sm-op-tile--nosig .sm-op-tile-metric,
        .sm-op-tile--nosig .sm-op-tile-metric-lbl {
            color: #f8fafc;
            text-shadow: 0 1px 2px rgba(0,0,0,0.25);
        }
        .sm-section-label {
            font-size: 10px;
            font-weight: 800;
            color: #475569;
            text-transform: uppercase;
            letter-spacing: 0.07em;
            margin-top: 12px;
            margin-bottom: 4px;
        }
        .sm-band-issues {
            background: linear-gradient(180deg, #fff1f2 0%, #ffe4e6 100%);
            border: 1px solid #fecdd3;
            border-radius: 8px;
            padding: 8px 8px 10px;
            margin-bottom: 8px;
        }
        .sm-band-healthy {
            background: #f7fef9;
            border: 1px solid #bbf7d0;
            border-radius: 10px;
            padding: 10px 10px 12px;
        }
        </style>
        <div style='max-width: 100%; margin: 0; padding: 0;'>
        """
    
    current_time = int(time.time())
    from_time = current_time - (timerange * 3600)

    try:
        services, environments = _sm_resolve_services_and_environments(environment)
    except ValueError:
        return f"<p style='color: #dc2626;'>⚠️ Error: Invalid environment '{html.escape(str(environment))}'</p>"

    dd_api_key = os.getenv("DATADOG_API_KEY")
    dd_app_key = os.getenv("DATADOG_APP_KEY")
    dd_site = os.getenv("DATADOG_SITE", "arlo.datadoghq.com")
    pd_api_key = os.getenv("PAGERDUTY_API_TOKEN")

    if not dd_api_key or not dd_app_key:
        return "<p style='color: #dc2626;'>⚠️ Error: Datadog credentials not configured</p>"

    print(f"📡 Fetching health for {len(services)} services across {len(environments)} environment(s): {environments}...")
    all_statuses = _sm_fetch_parallel_service_health(
        services,
        environments,
        dd_api_key,
        dd_app_key,
        dd_site,
        from_time,
        current_time,
        int(timerange),
        force_refresh,
    )

    print(f"🔄 Fetching PagerDuty and Arlo status (sequential, resilient)...")
    pd_counts = {"triggered": 0, "acknowledged": 0, "resolved": 0}
    pd_incidents = []
    arlo_services_status = []

    if pd_api_key:
        try:
            pd_counts, pd_incidents = get_pagerduty_status_counts(pd_api_key, force_refresh)
        except Exception as e:
            print(f"⚠️ Error fetching PagerDuty status: {e}")
    else:
        print(f"⚠️ PagerDuty API key not available")

    time.sleep(0.25)

    try:
        arlo_services_status = get_arlo_services_status(force_refresh)
        print(f"🎯 Arlo: {len(arlo_services_status)} core services")
    except Exception as e:
        print(f"⚠️ Error fetching Arlo status: {e}")

    pd_incidents_count = pd_counts["triggered"] + pd_counts["acknowledged"]
    _sm_apply_pagerduty_correlation(all_statuses, services, environments, environment, pd_incidents)

    total_no_dd = sum(1 for s in all_statuses if s.get('status') in ('inactive', 'unknown'))
    if total_no_dd:
        print(
            f"📋 {total_no_dd} service(s) with no APM signal (inactive=no hits; unknown=query errors/timeouts) "
            f"— not shown in service bands"
        )
    
    # EKS cluster lookup: all operational tiles (healthy / warning / critical) — same as Status wall
    # so hover tooltips show kube_cluster_name + region (z1/z2) instead of empty clusters.
    eks_tr_h = max(1, int(timerange))
    operational_ct = sum(
        1 for s in all_statuses if s.get("status") in ("healthy", "warning", "critical")
    )
    skip_eks_ct = sum(1 for s in all_statuses if s.get("status") in ("inactive", "unknown"))
    print(
        f"☸️  EKS cluster lookup: {operational_ct} operational service(s), "
        f"{skip_eks_ct} skipped (inactive/unknown), timerange={eks_tr_h}h (parallel)..."
    )
    cluster_service_map = {}

    def fetch_clusters_for_service(status_obj):
        """Fetch EKS clusters for a single service"""
        service_name = status_obj["service"]
        service_env = status_obj["environment"]
        if status_obj.get("status") in ("inactive", "unknown"):
            return (status_obj, [], service_name, service_env)
        cluster_names = _resolve_eks_cluster_names(service_name, service_env, eks_tr_h)
        return (status_obj, cluster_names, service_name, service_env)
    
    # Use ThreadPoolExecutor to fetch clusters in parallel
    eks_attached = 0
    with ThreadPoolExecutor(max_workers=STATUS_MONITOR_EKS_MAX_WORKERS) as executor:
        futures = [executor.submit(fetch_clusters_for_service, status_obj) for status_obj in all_statuses]
        
        for future in as_completed(futures):
            try:
                status_obj, cluster_names, service_name, service_env = future.result()
                
                if cluster_names:
                    status_obj['eks_clusters'] = cluster_names
                    status_obj['eks_cluster_count'] = len(cluster_names)
                    eks_attached += 1
                    
                    # Track which services run on which clusters
                    for cluster_name in cluster_names:
                        if cluster_name not in cluster_service_map:
                            cluster_service_map[cluster_name] = []
                        cluster_service_map[cluster_name].append(f"{service_name} ({service_env})")
                    
            except Exception as e:
                print(f"   ❌ Error fetching clusters: {e}")
    print(f"☸️  EKS: kube_cluster_name resolved for {eks_attached} / {operational_ct} operational service(s).")
    
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

    spl_data: dict = {
        "success": False,
        "tools": [],
        "error": None,
        "timerange_hours": 72,
    }
    try:
        from tools.splunk_tool import splunk_outliers_monitor_payload

        spl_data = splunk_outliers_monitor_payload(72)
    except Exception as e:
        print(f"⚠️ Splunk outliers (status monitor sidebar): {e}")
        spl_data = {
            "success": False,
            "tools": [],
            "error": str(e),
            "timerange_hours": 72,
        }

    print(f"☁️ Fetching AWS cost snapshot...")
    aws_data = get_aws_costs_and_changes(days=1)
    print(f"💰 AWS: ${aws_data.get('cost_yesterday', 0):.2f} yesterday")
    
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
                        <option value='1' selected>Last 1 hour</option>
                        <option value='2'>Last 2 hours</option>
                        <option value='4'>Last 4 hours</option>
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

    spl_tools_list = spl_data.get("tools") if spl_data.get("success") else []
    spl_by_id = {t.get("id"): t for t in (spl_tools_list or [])}

    def _spl_tid_outliers(tid: str) -> int:
        row = spl_by_id.get(tid) or {}
        return int(row.get("total_outliers") or 0)

    spl_o_stream = _spl_tid_outliers("p0_streaming")
    spl_o_cvr = _spl_tid_outliers("p0_cvr")
    spl_o_adt = _spl_tid_outliers("p0_adt")
    spl_o_us = _spl_tid_outliers("p0_streaming_us_infra")
    spl_o_grand = spl_o_stream + spl_o_cvr + spl_o_adt + spl_o_us
    spl_sem_tr = int(spl_data.get("timerange_hours") or 72)
    spl_p0_sem_bar_html = _splunk_p0_semaphore_bar_html(spl_by_id)

    if not spl_data.get("success"):
        spl_sem_bg_color = "#6b7280"
        spl_sem_icon = "⚪"
        spl_sem_text = "UNAVAILABLE"
        spl_sem_blink_class = ""
        spl_sem_err_html = (
            "<div style='margin-top:6px;font-size:9px;color:#b91c1c;text-align:center;font-weight:600;line-height:1.35;'>"
            + html.escape(str(spl_data.get("error") or "Unavailable")[:160])
            + "</div>"
        )
    elif spl_o_grand > 0:
        spl_sem_bg_color = "#dc2626"
        spl_sem_icon = "🔴"
        spl_sem_text = "OUTLIERS"
        spl_sem_blink_class = "pd-status-blink"
        spl_sem_err_html = ""
    else:
        spl_sem_bg_color = "#10b981"
        spl_sem_icon = "🟢"
        spl_sem_text = "CLEAR"
        spl_sem_blink_class = ""
        spl_sem_err_html = ""

    # Calculate summary statistics (all configured services, including no-telemetry)
    total_services = len(all_statuses)
    total_healthy = sum(1 for s in all_statuses if s['status'] == 'healthy')
    total_warning = sum(1 for s in all_statuses if s['status'] == 'warning')
    total_critical = sum(1 for s in all_statuses if s['status'] == 'critical')
    total_inactive = sum(1 for s in all_statuses if s['status'] == 'inactive')
    total_unknown = sum(1 for s in all_statuses if s['status'] == 'unknown')
    total_no_telemetry = total_inactive + total_unknown
    total_listed_ui = total_healthy + total_warning + total_critical
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
    
    # Command center strip: KPIs, ERR% delta vs last snapshot, attention queue
    snap_env = environment if environment else 'all'
    prev_err_delta_html = "<span class='cc-delta-flat'>vs last: n/a</span>"
    try:
        hist = get_dashboard_history(environment=snap_env, hours=72)
        if hist:
            prev = hist[-1]
            prev_rate = float(prev.get('overall_error_rate') or 0)
            delta = overall_error_rate - prev_rate
            if abs(delta) < 0.001:
                prev_err_delta_html = "<span class='cc-delta-flat'>vs last refresh: flat</span>"
            elif delta > 0:
                prev_err_delta_html = f"<span class='cc-delta-up'>▲ {delta:+.2f} pp vs last</span>"
            else:
                prev_err_delta_html = f"<span class='cc-delta-down'>▼ {abs(delta):.2f} pp vs last</span>"
    except Exception:
        pass
    
    def _attention_sort_key(s):
        tier = 0 if s['status'] == 'critical' else 1 if s['status'] == 'warning' else 2
        return (tier, -s['error_rate'], -s['errors'])
    
    bad_svcs = [s for s in all_statuses if s['status'] in ('critical', 'warning')]
    bad_svcs.sort(key=_attention_sort_key)
    seen_keys = set()
    attention_merged = []
    for s in bad_svcs:
        key = (s['service'], s['environment'])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        attention_merged.append(s)
        if len(attention_merged) >= 14:
            break
    
    # Attention queue: warning + critical only (no healthy "watch" rows)
    attn_missing_clusters = [s for s in attention_merged if not s.get('eks_clusters')]
    if attn_missing_clusters:
        def _clusters_for_attention_row(status_obj):
            service_name = status_obj['service']
            service_env = status_obj['environment']
            return _resolve_eks_cluster_names(service_name, service_env, 1)

        with ThreadPoolExecutor(max_workers=min(12, len(attn_missing_clusters))) as attn_cl_ex:
            fut_map = {
                attn_cl_ex.submit(_clusters_for_attention_row, row): row
                for row in attn_missing_clusters
            }
            for fut in as_completed(fut_map):
                row = fut_map[fut]
                try:
                    names = fut.result()
                    if names:
                        row['eks_clusters'] = names
                        row['eks_cluster_count'] = len(names)
                except Exception:
                    pass
    
    needs_attention_n = total_warning + total_critical
    pd_open = pd_triggered + pd_acknowledged
    dd_site_cc = os.getenv('DD_SITE', 'datadoghq.com')
    splunk_mqtt_q = (
        'index="*prod" sourcetype="kube:container:backend-log-server" LL=ERROR '
        '"DevicesSubscriptionsManagerMQTT" "Sequence timeout" earliest=-24h'
    )
    splunk_mqtt_url = f"https://arlo.splunkcloud.com/en-US/app/search/search?q={quote(splunk_mqtt_q)}"
    
    if total_requests >= 1_000_000:
        req_kpi_disp = f"{total_requests / 1e6:.2f}M"
    elif total_requests >= 1000:
        req_kpi_disp = f"{total_requests / 1e3:.1f}K"
    else:
        req_kpi_disp = f"{total_requests:,}"
    
    def _attention_reason_text(s):
        parts = []
        if s.get('pd_incident'):
            parts.append('PagerDuty incident')
        if s.get('traffic_drop'):
            parts.append('Traffic drop vs 7d')
        if s.get('high_latency'):
            parts.append('High latency (APM)')
        er = float(s.get('error_rate') or 0)
        st = s.get('status')
        if st == 'critical' and er > 5:
            parts.append('Error rate >5%')
        elif st == 'warning' and er > 1:
            parts.append('Error rate >1%')
        tv = s.get('traffic_variance')
        if not s.get('traffic_drop') and tv is not None and abs(tv) >= 12:
            parts.append(f'Traffic {tv:+.0f}% vs 7d')
        if not parts:
            if st == 'critical':
                parts.append('Critical (APM)')
            elif st == 'warning':
                parts.append('Warning (APM)')
            else:
                parts.append('Needs review')
        return html.escape(' · '.join(parts))

    attention_rows_html = ""
    if not attention_merged:
        attention_rows_html = (
            "<tr><td colspan='7' style='color:#64748b;padding:12px;'>"
            "No warning or critical services in this view.</td></tr>"
        )
    else:
        for s in attention_merged:
            st = s['status']
            if st == 'critical':
                pill = "<span class='cc-pill' style='background:#7f1d1d;color:#fecaca;'>Critical</span>"
            elif st == 'warning':
                pill = "<span class='cc-pill' style='background:#78350f;color:#fcd34d;'>Warning</span>"
            else:
                pill = f"<span class='cc-pill' style='background:#475569;color:#e2e8f0;'>{html.escape(st)}</span>"
            tv = s.get('traffic_variance')
            tv_txt = f"{tv:+.0f}% vs 7d" if tv is not None else '—'
            svc_url = (
                f"{datadog_ui_origin(dd_site_cc)}/apm/service/{s['service']}/overview?env={s['environment']}"
            )
            err_val = float(s.get('error_rate') or 0)
            bar_w = min(100.0, max(0.0, err_val))
            if err_val > 0 and bar_w < 2.0:
                bar_w = 2.0
            if err_val <= 0.3:
                bar_grad = 'linear-gradient(90deg,#4ade80,#22c55e)'
            elif err_val <= 1.0:
                bar_grad = 'linear-gradient(90deg,#fde047,#eab308)'
            else:
                bar_grad = 'linear-gradient(90deg,#f87171,#dc2626)'
            clusters = [c for c in (s.get('eks_clusters') or []) if _is_meaningful_kube_cluster_name(c)]
            if clusters:
                opt_lines = []
                for c in clusters:
                    opt_lines.append(
                        f'<option value="{html.escape(c, quote=True)}">{html.escape(c)}</option>'
                    )
                cluster_cell = (
                    f"<select class='cc-cluster-select' "
                    f"title='EKS clusters (kube_cluster_name) with traffic for this service' "
                    f"aria-label='Cluster names for {html.escape(s['service'], quote=True)}'>"
                    f"{''.join(opt_lines)}</select>"
                )
            else:
                cluster_cell = (
                    "<span style='color:#94a3b8;font-size:10px;font-weight:600;'>—</span>"
                )
            err_cell = (
                f"<div style='display:flex;align-items:center;gap:8px;max-width:96px;'>"
                f"<span style='font-weight:800;color:#0f172a;min-width:42px;flex-shrink:0;font-size:11px;'>"
                f"{err_val:.2f}%</span>"
                f"<div class='cc-err-bar-wrap'><div class='cc-err-bar-track' title='Error %'>"
                f"<div class='cc-err-bar-fill' style='width:{bar_w:.1f}%;background:{bar_grad};'></div>"
                f"</div></div></div>"
            )
            reason_cell = _attention_reason_text(s)
            attention_rows_html += f"""<tr>
                <td>{pill}</td>
                <td style='font-weight:700;color:#0f172a;'><a href="{svc_url}" target="_blank" rel="noopener" style="color:#0284c7;text-decoration:none;">{html.escape(s['service'])}</a></td>
                <td style="color:#475569;">{html.escape(s['environment'])}</td>
                <td style="vertical-align:middle;">{cluster_cell}</td>
                <td style="vertical-align:middle;">{err_cell}</td>
                <td style="font-size:10px;color:#0f172a;font-weight:600;line-height:1.35;max-width:200px;">{reason_cell}</td>
                <td style="color:#64748b;font-size:10px;">{s['requests']:,} req · {tv_txt}</td>
            </tr>"""
    
    err_kpi_color = '#f87171' if overall_error_rate > 1 else '#fbbf24' if overall_error_rate > 0.3 else '#4ade80'
    command_center_html = f"""
            <div class="cc-strip">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
                    <div>
                        <div style="font-size:11px;font-weight:800;color:#64748b;text-transform:uppercase;letter-spacing:0.12em;">Command center</div>
                        <div style="font-size:15px;font-weight:700;color:#0f172a;margin-top:2px;">At-a-glance</div>
                    </div>
                    <a class="cc-link-btn" href="{splunk_mqtt_url}" target="_blank" rel="noopener" title="iOS MQTT subscribe sequence timeouts (client signal, may not move API ERR%)">Splunk: iOS MQTT timeouts</a>
                </div>
                <div class="cc-kpi-grid">
                    <div class="cc-kpi">
                        <div class="cc-kpi-val" style="color:{'#f87171' if needs_attention_n else '#4ade80'};">{needs_attention_n}</div>
                        <div class="cc-kpi-lbl">Needs attention</div>
                        <div style="font-size:9px;color:#64748b;margin-top:4px;">warning + critical</div>
                    </div>
                    <div class="cc-kpi">
                        <div class="cc-kpi-val" style="color:{'#fbbf24' if pd_open else '#4ade80'};">{pd_open}</div>
                        <div class="cc-kpi-lbl">PagerDuty open</div>
                        <div style="font-size:9px;color:#64748b;margin-top:4px;">triggered + ack</div>
                    </div>
                    <div class="cc-kpi">
                        <div class="cc-kpi-val" style="color:{err_kpi_color};">{overall_error_rate:.2f}%</div>
                        <div class="cc-kpi-lbl">Global ERR rate</div>
                        <div style="margin-top:4px;">{prev_err_delta_html}</div>
                    </div>
                    <div class="cc-kpi">
                        <div class="cc-kpi-val" style="font-size:22px;">{req_kpi_disp}</div>
                        <div class="cc-kpi-lbl">Total requests</div>
                        <div style="font-size:9px;color:#64748b;margin-top:4px;">{timerange}h window</div>
                    </div>
                </div>
                <div style="font-size:10px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:6px;">Attention queue (warning &amp; critical)</div>
                <table class="cc-attention-table">
                    <thead><tr><th>Status</th><th>Service</th><th>Env</th><th>Clusters</th><th>ERR%</th><th>Reason</th><th>Context</th></tr></thead>
                    <tbody>{attention_rows_html}</tbody>
                </table>
            </div>
    """
    
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
                                <div style='color: #111827; font-weight: 600; font-size: 11px; letter-spacing: -0.01em;'>Total listed</div>
                                <div style='color: #6b7280; font-weight: 700; font-size: 16px;'>{total_listed_ui}</div>
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

            <!-- Samsung external board: semaphore + full incident lists (API paginates board scope) -->
            <div style='background: #ffffff; padding: 16px; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04); border: 1px solid #e5e7eb;'>
                <div style='margin-bottom: 12px;'>
                    <h3 style='font-size: 15px; font-weight: 700; color: #0c4a6e; margin: 0; letter-spacing: -0.02em;'>📱 Samsung</h3>
                </div>
                <div id='ss-summary' class='pd-status-summary' style='display: flex; justify-content: space-between; gap: 12px; padding: 12px; background: #10b981; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.1);'>
                    <div style='text-align: center; flex: 1;'>
                        <div id='ss-triggered-count' style='font-size: 24px; font-weight: 700; color: white; letter-spacing: -0.01em;'>0</div>
                        <div style='font-size: 10px; color: rgba(255,255,255,0.9); font-weight: 600;'>Triggered</div>
                    </div>
                    <div style='text-align: center; flex: 1;'>
                        <div id='ss-ack-count-number' style='font-size: 24px; font-weight: 700; color: white; letter-spacing: -0.01em;'>0</div>
                        <div style='font-size: 10px; color: rgba(255,255,255,0.9); font-weight: 600;'>Ack</div>
                    </div>
                    <div style='text-align: center; flex: 1;'>
                        <div id='ss-resolved-count-number' style='font-size: 24px; font-weight: 700; color: white; letter-spacing: -0.01em;'>0</div>
                        <div style='font-size: 10px; color: rgba(255,255,255,0.9); font-weight: 600;'>Resolved</div>
                    </div>
                </div>
                <div id='ss-board-links' style='font-size: 11px; color: #64748b; margin: 10px 0; line-height: 1.4; padding: 6px 8px; border-radius: 6px; background: #f0f9ff; border: 1px solid #bae6fd;'>Loading…</div>
                <div style='display: grid; grid-template-columns: 1fr 1fr; gap: 10px;'>
                    <div>
                        <h4 style='font-size: 12px; margin: 0 0 6px 0; color: #64748b; font-weight: 700;'>🔴 Active</h4>
                        <ul id='ss-active' style='list-style: none; padding: 0; margin: 0; font-size: 11px; max-height: 200px; overflow-y: auto;'>
                            <li style='padding: 6px; color: #999; background: #f8fafc; border-radius: 4px;'>Loading…</li>
                        </ul>
                    </div>
                    <div>
                        <h4 style='font-size: 12px; margin: 0 0 6px 0; color: #64748b; font-weight: 700;'>🟢 Resolved</h4>
                        <ul id='ss-resolved' style='list-style: none; padding: 0; margin: 0; font-size: 11px; max-height: 200px; overflow-y: auto;'>
                            <li style='padding: 6px; color: #999; background: #f8fafc; border-radius: 4px;'>Loading…</li>
                        </ul>
                    </div>
                </div>
                <div style='margin-top: 8px; font-size: 10px; color: #64748b; text-align: center;'><span id='ss-time'>Last updated: --:--:--</span></div>
            </div>

            <!-- ADT external board -->
            <div style='background: #ffffff; padding: 16px; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04); border: 1px solid #e5e7eb;'>
                <div style='margin-bottom: 12px;'>
                    <h3 style='font-size: 15px; font-weight: 700; color: #3730a3; margin: 0; letter-spacing: -0.02em;'>🏠 ADT</h3>
                </div>
                <div id='adt-summary' class='pd-status-summary' style='display: flex; justify-content: space-between; gap: 12px; padding: 12px; background: #10b981; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.1);'>
                    <div style='text-align: center; flex: 1;'>
                        <div id='adt-triggered-count' style='font-size: 24px; font-weight: 700; color: white; letter-spacing: -0.01em;'>0</div>
                        <div style='font-size: 10px; color: rgba(255,255,255,0.9); font-weight: 600;'>Triggered</div>
                    </div>
                    <div style='text-align: center; flex: 1;'>
                        <div id='adt-ack-count-number' style='font-size: 24px; font-weight: 700; color: white; letter-spacing: -0.01em;'>0</div>
                        <div style='font-size: 10px; color: rgba(255,255,255,0.9); font-weight: 600;'>Ack</div>
                    </div>
                    <div style='text-align: center; flex: 1;'>
                        <div id='adt-resolved-count-number' style='font-size: 24px; font-weight: 700; color: white; letter-spacing: -0.01em;'>0</div>
                        <div style='font-size: 10px; color: rgba(255,255,255,0.9); font-weight: 600;'>Resolved</div>
                    </div>
                </div>
                <div id='adt-board-links' style='font-size: 11px; color: #64748b; margin: 10px 0; line-height: 1.4; padding: 6px 8px; border-radius: 6px; background: #eef2ff; border: 1px solid #c7d2fe;'>Loading…</div>
                <div style='display: grid; grid-template-columns: 1fr 1fr; gap: 10px;'>
                    <div>
                        <h4 style='font-size: 12px; margin: 0 0 6px 0; color: #64748b; font-weight: 700;'>🔴 Active</h4>
                        <ul id='adt-active' style='list-style: none; padding: 0; margin: 0; font-size: 11px; max-height: 200px; overflow-y: auto;'>
                            <li style='padding: 6px; color: #999; background: #f8fafc; border-radius: 4px;'>Loading…</li>
                        </ul>
                    </div>
                    <div>
                        <h4 style='font-size: 12px; margin: 0 0 6px 0; color: #64748b; font-weight: 700;'>🟢 Resolved</h4>
                        <ul id='adt-resolved' style='list-style: none; padding: 0; margin: 0; font-size: 11px; max-height: 200px; overflow-y: auto;'>
                            <li style='padding: 6px; color: #999; background: #f8fafc; border-radius: 4px;'>Loading…</li>
                        </ul>
                    </div>
                </div>
                <div style='margin-top: 8px; font-size: 10px; color: #64748b; text-align: center;'><span id='adt-time'>Last updated: --:--:--</span></div>
            </div>

            <!-- Splunk P0 predict outliers (same 72h window as home Splunk monitor) -->
            <div style='background: #ffffff; padding: 16px; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04); border: 1px solid #e5e7eb;'>
                <div style='margin-bottom: 12px;'>
                    <h3 style='font-size: 15px; font-weight: 700; color: #111827; margin: 0; letter-spacing: -0.02em;'>📊 Splunk Outliers</h3>
                </div>
                <div class='{spl_sem_blink_class}' style='display: flex; flex-direction: column; align-items: center; gap: 10px; padding: 12px; background: {spl_sem_bg_color}; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.1);'>
                    {spl_p0_sem_bar_html}
                </div>
                <div style='margin-top: 8px; font-size: 9px; color: #6b7280; text-align: center; font-weight: 600; line-height: 1.35;'>
                    P0 predict (LLP) · {spl_sem_tr}h · {spl_sem_icon} {spl_sem_text}
                </div>
                {spl_sem_err_html}
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
            
            <!-- AWS cost (Cost Explorer) -->
            <div style='background: white; padding: 6px; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
                <div style='background: #ff9900; color: white; padding: 3px 4px; border-radius: 3px; margin-bottom: 4px; text-align: center;'>
                    <span style='font-size: 9px; font-weight: bold;'>☁️ AWS cost</span>
                </div>
    """
    
    if not aws_data.get("error"):
        cost_yesterday = aws_data.get("cost_yesterday", 0)

        if cost_yesterday > 1000:
            cost_bg = '#dc2626'
        elif cost_yesterday > 500:
            cost_bg = '#f59e0b'
        else:
            cost_bg = '#10b981'

        aws_console_url = 'https://console.aws.amazon.com/cost-management/home'

        output += f"""
                <div style='background: {cost_bg}; padding: 4px; border-radius: 3px; color: white; cursor: pointer; transition: opacity 0.2s;'
                     onclick="window.open('{aws_console_url}', '_blank')"
                     title='Click to view AWS Cost Explorer'
                     onmouseover="this.style.opacity='0.9'"
                     onmouseout="this.style.opacity='1'">
                    <div style='text-align: center;'>
                        <div style='font-size: 6px; opacity: 0.9; margin-bottom: 2px;'>Yesterday cost</div>
                        <div style='font-size: 14px; font-weight: bold;'>${cost_yesterday:.2f}</div>
                    </div>
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
        // Pie chart: services with APM signal (inactive/unknown omitted from tiles)
        window.chartData = {{
            healthy: {total_healthy},
            warning: {total_warning},
            critical: {total_critical},
            total: {total_listed_ui}
        }};
        
        // Signal that data is ready
        if (window.initializePieChart) {{
            window.initializePieChart(window.chartData);
        }}
    </script>
    {command_center_html}
    """
    
    # Build environment layout (1 or 3 columns depending on mode)
    # Using same blue color for all environments to avoid confusion
    env_config = {
        'production': {'icon': '🔵', 'color': '#3b82f6'},
        'goldendev': {'icon': '🔵', 'color': '#3b82f6'},
        'goldenqa': {'icon': '🔵', 'color': '#3b82f6'},
        'samsung_prod': {'icon': '📱', 'color': '#0ea5e9'},
        'adt_prod': {'icon': '🏠', 'color': '#8b5cf6'},
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
        if environment == 'samsung':
            config = {'icon': '📱', 'color': '#0ea5e9'}
        else:
            config = env_config[env]
        env_services = [s for s in all_statuses if s['environment'] == env]
        
        # Sort services alphabetically
        env_services.sort(key=lambda x: x['service'].lower())
        
        # Samsung: group by APM naming — hmsguard-samsung-{env}, backend-pp-samsung-{env}, etc.
        if environment == 'samsung':
            def _sk(s):
                return (s.get('service') or '').lower()

            def _is_samsung_partner(sk):
                return (
                    sk.startswith('backend-pp')
                    or 'pp-samsung' in sk
                    or 'pp_samsung' in sk
                )

            def _is_samsung_hmsguard(sk):
                return 'hmsguard' in sk

            partner_svcs = [s for s in env_services if _is_samsung_partner(_sk(s))]
            _pkeys = {(s['service'], s['environment']) for s in partner_svcs}
            hmg_svcs = [
                s for s in env_services
                if _is_samsung_hmsguard(_sk(s)) and (s['service'], s['environment']) not in _pkeys
            ]
            _hkeys = {(s['service'], s['environment']) for s in hmg_svcs}
            other_svcs = [
                s for s in env_services
                if (s['service'], s['environment']) not in _pkeys
                and (s['service'], s['environment']) not in _hkeys
            ]
            for _grp in (partner_svcs, hmg_svcs, other_svcs):
                _grp.sort(key=lambda x: (x.get('service') or '').lower())

            service_groups = []
            if hmg_svcs:
                service_groups.append({
                    'name': 'HMSGUARD',
                    'icon': '🛡️',
                    'color': '#0ea5e9',
                    'services': hmg_svcs,
                })
            if partner_svcs:
                service_groups.append({
                    'name': 'Partner Platform',
                    'icon': '🤝',
                    'color': '#0284c7',
                    'services': partner_svcs,
                })
            if other_svcs:
                service_groups.append({
                    'name': 'Samsung services',
                    'icon': '📱',
                    'color': '#06b6d4',
                    'services': other_svcs,
                })
            if not service_groups and env_services:
                service_groups.append({
                    'name': 'Samsung',
                    'icon': '📱',
                    'color': '#0ea5e9',
                    'services': list(env_services),
                })
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
            
            # Count statuses for this group (○ = inactive/unknown, header only — not listed in bands)
            group_healthy = sum(1 for s in group_services if s['status'] == 'healthy')
            group_warning = sum(1 for s in group_services if s['status'] == 'warning')
            group_critical = sum(1 for s in group_services if s['status'] == 'critical')
            group_nosig = sum(1 for s in group_services if s['status'] in ('inactive', 'unknown'))
            output += f"""
        <div>
            <!-- Group Header -->
            <div style='background: {group['color']}; color: white; padding: 6px 8px; border-radius: 5px 5px 0 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
                <div style='display: flex; justify-content: space-between; align-items: center;'>
                    <div style='font-size: 11px; font-weight: bold;'>
                        {group['icon']} {group['name']}
                    </div>
                    <div style='font-size: 8px; opacity: 0.9;'>
                        ✓ {group_healthy} | ⚠ {group_warning} | ✗ {group_critical} | ○ {group_nosig}
                    </div>
                </div>
            </div>
            
            <!-- Services: single operational band — warning/critical tiles first, then healthy -->
            <div style='padding: {'12px' if len(environments) == 1 else '10px'}; background: #f8fafc; border-radius: 0 0 8px 8px; min-height: 80px;'>
        """
            issue_svcs = sorted(
                [s for s in group_services if s['status'] in ('critical', 'warning')],
                key=lambda s: (0 if s['status'] == 'critical' else 1, -s['error_rate'], -s['errors']),
            )
            healthy_svcs = sorted(
                [s for s in group_services if s['status'] == 'healthy'],
                key=lambda x: x['service'].lower(),
            )
            dd_site_chips = os.getenv('DD_SITE', 'datadoghq.com')
            op_tile_count = len(issue_svcs) + len(healthy_svcs)

            if op_tile_count:
                output += f"""
            <div class='sm-band-healthy'>
            <div class='sm-section-label' style='margin-top:0;'>Operational — {op_tile_count}</div>
            <div class='sm-op-tiles'>
        """
            for svc in issue_svcs:
                dd_site_tile = os.getenv('DD_SITE', 'datadoghq.com')
                service_name = svc['service']
                dd_url = (
                    f"{datadog_ui_origin(dd_site_tile)}/apm/service/"
                    f"{quote(service_name, safe='')}/overview?env={quote(env, safe='')}"
                )
                is_crit = svc['status'] == 'critical'
                tile_mod = 'sm-op-tile--crit' if is_crit else 'sm-op-tile--warn'
                alert_tile = ' service-box-alert' if svc['status'] in ('warning', 'critical') else ''
                icon = '✕' if is_crit else '⚠'
                tip_bits = [
                    f"APM: {service_name}",
                    f"{svc['requests']:,} req · ERR {svc['error_rate']}%",
                ]
                if svc.get('pd_incident'):
                    tip_bits.append('PagerDuty')
                if svc.get('high_latency') and svc.get('p95_latency'):
                    tip_bits.append(f"P95 {svc['p95_latency']:.0f}ms")
                if svc.get('traffic_drop'):
                    tip_bits.append('Traffic drop vs 7d')
                elif svc.get('traffic_variance') is not None:
                    tip_bits.append(f"Traffic {svc['traffic_variance']:+.0f}% vs 7d")
                t_name = html.escape(svc['service'])
                t_err = html.escape(f"{svc['error_rate']}")
                url_attr = html.escape(dd_url, quote=True)
                hover_j = _sm_hover_json_attr(_sm_hover_service_payload(svc, env, page_environment=environment))
                output += f"""
                <div class='sm-tip-wrap' data-sm-hover="{hover_j}">
                <a class='sm-op-tile {tile_mod}{alert_tile}' href="{url_attr}" target="_blank" rel="noopener">
                    <div class='sm-op-tile-icon'>{icon}</div>
                    <div class='sm-op-tile-name'>{t_name}</div>
                    <div class='sm-op-tile-metric'>{t_err}%</div>
                    <div class='sm-op-tile-metric-lbl'>ERR</div>
                </a>
                </div>
                """
            for hsvc in healthy_svcs:
                t_url = f"{datadog_ui_origin(dd_site_chips)}/apm/service/{hsvc['service']}/overview?env={env}"
                t_name = html.escape(hsvc['service'])
                t_err = html.escape(f"{hsvc['error_rate']}")
                hover_j = _sm_hover_json_attr(_sm_hover_service_payload(hsvc, env, page_environment=environment))
                output += f"""
                <div class='sm-tip-wrap' data-sm-hover="{hover_j}">
                <div class='sm-op-tile' onclick="window.open('{t_url}', '_blank')">
                    <div class='sm-op-tile-icon'>✓</div>
                    <div class='sm-op-tile-name'>{t_name}</div>
                    <div class='sm-op-tile-metric'>{t_err}%</div>
                    <div class='sm-op-tile-metric-lbl'>ERR</div>
                </div>
                </div>
                """
            if op_tile_count:
                output += """
            </div>
            </div>
        """
            # Samsung: show inactive/unknown as tiles too (operational band omits them; wrong DD env used to hide all six).
            nosig_svcs = sorted(
                [s for s in group_services if s.get("status") in ("inactive", "unknown")],
                key=lambda x: (x.get("service") or "").lower(),
            )
            if environment == "samsung" and nosig_svcs:
                dd_site_nosig = os.getenv("DD_SITE", "datadoghq.com")
                output += f"""
            <div class="sm-band-nosig">
            <div class="sm-section-label" style="margin-top:0;">No APM signal — {len(nosig_svcs)}</div>
            <div class="sm-op-tiles">
        """
                for svc in nosig_svcs:
                    service_name = svc["service"]
                    dd_url = (
                        f"{datadog_ui_origin(dd_site_nosig)}/apm/service/"
                        f"{quote(service_name, safe='')}/overview?env={quote(env, safe='')}"
                    )
                    st = svc.get("status") or "unknown"
                    icon = "○" if st == "inactive" else "?"
                    t_name = html.escape(service_name)
                    url_attr = html.escape(dd_url, quote=True)
                    lbl = "idle" if st == "inactive" else "unknown"
                    hover_j = _sm_hover_json_attr(_sm_hover_service_payload(svc, env, page_environment=environment))
                    output += f"""
                <div class="sm-tip-wrap" data-sm-hover="{hover_j}">
                <a class="sm-op-tile sm-op-tile--nosig" href="{url_attr}" target="_blank" rel="noopener">
                    <div class="sm-op-tile-icon">{icon}</div>
                    <div class="sm-op-tile-name">{t_name}</div>
                    <div class="sm-op-tile-metric">—</div>
                    <div class="sm-op-tile-metric-lbl">{html.escape(lbl)}</div>
                </a>
                </div>
                """
                output += """
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
    
    print(
        f"✅ Dashboard generated: {total_services} services "
        f"({total_healthy} healthy, {total_warning} warn, {total_critical} critical, {total_no_telemetry} no telemetry)"
    )
    
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
    _write_sm_mem_cache(_status_cache, cache_key, output)
    
    # Clean old cache entries (keep only last 5)
    if len(_status_cache) > 5:
        oldest_key = min(_status_cache.keys())
        del _status_cache[oldest_key]
        _mem_cache_saved_at.pop(oldest_key, None)
    
    return output
