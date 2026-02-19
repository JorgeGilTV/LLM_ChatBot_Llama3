import os
import html
import requests
import time
import json
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

load_dotenv()

def format_timerange(hours: int) -> str:
    """Format timerange hours into readable text"""
    if hours == 1:
        return "last hour"
    elif hours < 24:
        return f"last {hours} hours"
    elif hours == 24:
        return "last day"
    elif hours < 168:
        days = hours // 24
        return f"last {days} day{'s' if days > 1 else ''}"
    elif hours == 168:
        return "last week"
    else:
        weeks = hours // 168
        return f"last {weeks} week{'s' if weeks > 1 else ''}"

def format_timestamp_range(from_timestamp: int, to_timestamp: int) -> str:
    """Format timestamp range into readable format with date and time"""
    from datetime import datetime
    
    # Convert to datetime objects
    from_dt = datetime.fromtimestamp(from_timestamp)
    to_dt = datetime.fromtimestamp(to_timestamp)
    
    # Format with date and time
    from_str = from_dt.strftime("%Y-%m-%d %H:%M:%S")
    to_str = to_dt.strftime("%Y-%m-%d %H:%M:%S")
    
    # Also include day of week for context
    from_day = from_dt.strftime("%A")
    to_day = to_dt.strftime("%A")
    
    return f"""
    <div style='display: flex; justify-content: space-around; background: rgba(255,255,255,0.1); padding: 8px; border-radius: 4px; margin-top: 8px;'>
        <div style='text-align: center;'>
            <div style='font-size: 10px; opacity: 0.8;'>From</div>
            <div style='font-weight: bold; font-size: 11px;'>{from_str}</div>
            <div style='font-size: 9px; opacity: 0.7;'>{from_day}</div>
        </div>
        <div style='display: flex; align-items: center; font-size: 16px;'>→</div>
        <div style='text-align: center;'>
            <div style='font-size: 10px; opacity: 0.8;'>To</div>
            <div style='font-weight: bold; font-size: 11px;'>{to_str}</div>
            <div style='font-size: 9px; opacity: 0.7;'>{to_day}</div>
        </div>
    </div>
    """

def get_metric_data(dd_api_key, dd_app_key, dd_site, query, from_time, to_time):
    """Get actual metric data from Datadog - with reduced timeout for faster failures"""
    if dd_site.startswith('arlo.') or '.' in dd_site.split('.')[0]:
        base_domain = '.'.join(dd_site.split('.')[-2:])
        api_url = f"https://api.{base_domain}/api/v1/query"
    else:
        api_url = f"https://api.{dd_site}/api/v1/query"
    
    headers = {
        "DD-API-KEY": dd_api_key,
        "DD-APPLICATION-KEY": dd_app_key,
    }
    
    params = {
        "query": query,
        "from": from_time,
        "to": to_time
    }
    
    try:
        # Reduced timeout from 15 to 10 seconds
        response = requests.get(api_url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            return response.json()
        return None
    except:
        return None

def get_metrics_parallel(dd_api_key, dd_app_key, dd_site, queries_dict, from_time, to_time, max_workers=10):
    """
    Fetch multiple metrics in parallel for faster performance
    Args:
        queries_dict: Dictionary with {key: query_string} format
        max_workers: Maximum number of parallel requests (default: 10)
    Returns:
        Dictionary with {key: metric_data} format
    """
    results = {}
    
    def fetch_single_metric(key, query):
        try:
            data = get_metric_data(dd_api_key, dd_app_key, dd_site, query, from_time, to_time)
            return key, data
        except Exception as e:
            print(f"Error fetching metric {key}: {e}")
            return key, None
    
    # Use ThreadPoolExecutor for parallel HTTP requests
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_key = {
            executor.submit(fetch_single_metric, key, query): key 
            for key, query in queries_dict.items()
        }
        
        # Collect results as they complete
        for future in as_completed(future_to_key):
            key, data = future.result()
            results[key] = data
    
    return results

def create_graph_snapshot(dd_api_key, dd_app_key, dd_site, metric_query, from_time, to_time, title=""):
    """Create a snapshot image of a graph using Datadog API"""
    if dd_site.startswith('arlo.') or '.' in dd_site.split('.')[0]:
        base_domain = '.'.join(dd_site.split('.')[-2:])
        api_url = f"https://api.{base_domain}/api/v1/graph/snapshot"
    else:
        api_url = f"https://api.{dd_site}/api/v1/graph/snapshot"
    
    headers = {
        "DD-API-KEY": dd_api_key,
        "DD-APPLICATION-KEY": dd_app_key,
        "Content-Type": "application/json"
    }
    
    # Create graph definition
    graph_def = {
        "metric_query": metric_query,
        "start": from_time,
        "end": to_time,
        "title": title
    }
    
    try:
        response = requests.post(api_url, headers=headers, json=graph_def, timeout=20)
        if response.status_code == 200:
            data = response.json()
            return data.get('snapshot_url', None)
        return None
    except Exception as e:
        print(f"Error creating snapshot: {e}")
        return None

def get_dashboard_details(dd_api_key, dd_app_key, dd_site, dashboard_id):
    """Get detailed information about a specific dashboard including widgets"""
    # Handle custom subdomains (e.g., arlo.datadoghq.com)
    if dd_site.startswith('arlo.') or '.' in dd_site.split('.')[0]:
        # Custom subdomain - use the main API endpoint
        base_domain = '.'.join(dd_site.split('.')[-2:])  # Extract datadoghq.com
        api_url = f"https://api.{base_domain}/api/v1/dashboard/{dashboard_id}"
    else:
        api_url = f"https://api.{dd_site}/api/v1/dashboard/{dashboard_id}"
    
    headers = {
        "DD-API-KEY": dd_api_key,
        "DD-APPLICATION-KEY": dd_app_key,
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.get(api_url, headers=headers, timeout=15)
        print(f"📡 Response status: {response.status_code}")
        
        if response.status_code == 200:
            print(f"✅ Dashboard details retrieved successfully")
            return response.json()
        else:
            print(f"❌ Failed to get dashboard: {response.status_code} - {response.text[:200]}")
            return None
    except Exception as e:
        print(f"❌ Exception getting dashboard details: {str(e)}")
        return None

def read_datadog_dashboards(query: str, timerange_hours: int = 4) -> str:
    """
    Shows the RED - Metrics dashboard by default with embedded graphs.
    If a service name is provided (e.g., backend-arlosafeapi), filters widgets for that specific service.
    Args:
        query: Service name to filter or dashboard ID
        timerange_hours: Number of hours to look back (default: 4)
    """
    print("=" * 80)
    print("🔎 Reading Datadog Dashboards")
    print(f"📝 Query received: '{query}'")
    print(f"📝 Time range: {timerange_hours} hours")
    print(f"📝 Query type: {type(query)}")
    print(f"📝 Query length: {len(query) if query else 0}")
    
    # Get Datadog credentials from environment
    dd_api_key = os.getenv("DATADOG_API_KEY")
    dd_app_key = os.getenv("DATADOG_APP_KEY")
    dd_site = os.getenv("DATADOG_SITE", "datadoghq.com")  # Default to US1
    
    # Default RED dashboard ID - show directly if no query
    default_red_dashboard_id = "mpd-2aw-sfe"  # RED - Metrics dashboard
    show_specific_dashboard = False
    service_filter = None  # Filter for specific service widgets
    original_query = query  # Save original query for messages
    
    # If no query or query is "RED", show the main RED dashboard directly
    if not query or query.strip().upper() == "RED" or query.strip() == "":
        query = default_red_dashboard_id
        show_specific_dashboard = True
        print("📊 Showing RED - Metrics dashboard directly")
    else:
        # Assume query is a service name and show RED dashboard filtered by that service
        service_filter = query.strip().lower()
        original_query = service_filter  # Keep the service name for display
        query = default_red_dashboard_id
        show_specific_dashboard = True
        print(f"🔍 Filtering RED dashboard for service: {service_filter}")
    
    # Sanitize dd_site - remove any protocol or path
    if dd_site:
        # Remove http://, https://, and any path/query strings
        dd_site = dd_site.replace("https://", "").replace("http://", "")
        if "/" in dd_site:
            dd_site = dd_site.split("/")[0]
        if "?" in dd_site:
            dd_site = dd_site.split("?")[0]
        dd_site = dd_site.strip()
        
        # If it still looks wrong, use default
        if not dd_site or "." not in dd_site:
            dd_site = "datadoghq.com"
    
    if not dd_api_key or not dd_app_key:
        return """
        <p>❌ Error: Datadog credentials not found.</p>
        <p>Please set the following in your .env file:</p>
        <ul>
            <li>DATADOG_API_KEY</li>
            <li>DATADOG_APP_KEY</li>
            <li>DATADOG_SITE (optional, defaults to datadoghq.com)</li>
        </ul>
        """
    
    # API endpoint - handle custom subdomains
    if dd_site.startswith('arlo.') or '.' in dd_site.split('.')[0]:
        # Custom subdomain like arlo.datadoghq.com
        # Extract base domain (datadoghq.com) and use standard API endpoint
        base_domain = '.'.join(dd_site.split('.')[-2:])  # Extract datadoghq.com
        api_url = f"https://api.{base_domain}/api/v1/dashboard"
        print(f"Using API URL: {api_url} for custom subdomain: {dd_site}")
    else:
        # Standard site like datadoghq.com, us5.datadoghq.com
        api_url = f"https://api.{dd_site}/api/v1/dashboard"
    
    headers = {
        "DD-API-KEY": dd_api_key,
        "DD-APPLICATION-KEY": dd_app_key,
        "Content-Type": "application/json"
    }
    
    try:
        # Initialize search_query for later use
        search_query = query.strip().lower() if query and not show_specific_dashboard else ""
        
        # If showing specific dashboard, get it directly by ID
        if show_specific_dashboard:
            print(f"📊 Fetching dashboard directly by ID: {query}")
            print(f"📊 Service filter: {service_filter}")
            print(f"🔑 Using API key: {dd_api_key[:10]}..." if dd_api_key else "❌ No API key")
            print(f"🌐 Datadog site: {dd_site}")
            
            details = get_dashboard_details(dd_api_key, dd_app_key, dd_site, query)
            
            print(f"📊 Dashboard details received: {details is not None}")
            if details:
                print(f"📊 Dashboard title: {details.get('title', 'N/A')}")
            
            if not details:
                error_msg = f"""
                <div style='padding: 20px; margin: 20px 0; background-color: #fee; border: 2px solid #f00; border-radius: 4px;'>
                    <h3 style='color: #c00;'>❌ Error: Could not load the RED - Metrics dashboard</h3>
                    <p><strong>Dashboard ID:</strong> {query}</p>
                    <p><strong>Site:</strong> {dd_site}</p>
                    <p>Possible causes:</p>
                    <ul>
                        <li>Invalid dashboard ID</li>
                        <li>Insufficient permissions</li>
                        <li>Dashboard does not exist</li>
                    </ul>
                    <p>Please check your Datadog credentials and dashboard ID in the .env file.</p>
                </div>
                """
                return error_msg
            
            # Create a fake dashboard object to use with existing rendering code
            filtered_dashboards = [{
                "id": query,
                "title": details.get("title", "RED - Metrics"),
                "url": f"https://{dd_site}/dashboard/{query}",
                "layout_type": details.get("layout_type", "ordered"),
                "author_name": details.get("author_name", "Unknown")
            }]
            print(f"📊 Created filtered_dashboards with {len(filtered_dashboards)} item(s)")
        else:
            # Fetch all dashboards and filter
            response = requests.get(api_url, headers=headers, timeout=15)
            
            if response.status_code == 403:
                return "<p>❌ Error 403: Access forbidden. Please verify your Datadog API and Application keys have the correct permissions.</p>"
            elif response.status_code == 401:
                return "<p>❌ Error 401: Authentication failed. Please verify your Datadog credentials.</p>"
            elif response.status_code != 200:
                return f"<p>❌ Error {response.status_code}: {html.escape(response.reason)}</p>"
            
            data = response.json()
            dashboards = data.get("dashboards", [])
            
            if not dashboards:
                return "<p>No dashboards found in your Datadog account.</p>"
            
            # Filter dashboards based on query
            filtered_dashboards = []
            
            for dashboard in dashboards:
                dash_title = dashboard.get("title", "").lower()
                dash_id = dashboard.get("id", "")
                
                # If search query is provided, filter more precisely
                if not search_query:
                    filtered_dashboards.append(dashboard)
                else:
                    # Check for exact match first, then partial match
                    if (search_query == dash_title or 
                        search_query == dash_id or 
                        search_query in dash_title or 
                        search_query in dash_id):
                        filtered_dashboards.append(dashboard)
        
        # Build HTML output
        output = f"""
        <style>
            .datadog-table {{
                border-collapse: collapse;
                width: 100%;
                font-family: Arial, sans-serif;
                font-size: 13px;
                margin-top: 10px;
                margin-bottom: 20px;
            }}
            .datadog-table th {{
                border: 1px solid #444;
                padding: 10px;
                text-align: left;
                background-color: #632ca6;
                color: white;
                font-weight: bold;
            }}
            .datadog-table td {{
                border: 1px solid #ddd;
                padding: 8px;
                text-align: left;
                vertical-align: top;
            }}
            .datadog-table tr:nth-child(even) {{
                background-color: #f9f9f9;
            }}
            .datadog-table tr:hover {{
                background-color: #f0e6ff;
            }}
            .dashboard-title {{
                font-weight: bold;
                color: #632ca6;
            }}
            .dashboard-link {{
                color: #632ca6;
                text-decoration: none;
                font-weight: bold;
            }}
            .dashboard-link:hover {{
                text-decoration: underline;
            }}
            .datadog-header {{
                color: #632ca6;
                margin-top: 20px;
                margin-bottom: 10px;
                border-bottom: 2px solid #632ca6;
                padding-bottom: 5px;
            }}
            .search-info {{
                padding: 10px;
                background-color: #f0e6ff;
                border-left: 4px solid #632ca6;
                border-radius: 3px;
                margin-bottom: 15px;
            }}
            .widget-box {{
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 6px;
                padding: 8px;
                margin: 8px 0;
                box-shadow: 0 1px 3px rgba(0,0,0,0.08);
            }}
            .widget-title {{
                font-weight: bold;
                color: #632ca6;
                font-size: 16px;
                margin-bottom: 10px;
                padding-bottom: 8px;
                border-bottom: 2px solid #f0e6ff;
            }}
            .widget-box code {{
                background-color: #f5f5f5;
                padding: 2px 6px;
                border-radius: 3px;
                font-size: 12px;
                color: #333;
            }}
            .widget-box ul {{
                margin: 10px 0;
                padding-left: 20px;
            }}
            .widget-box li {{
                margin: 5px 0;
            }}
            .metric-value {{
                font-size: 24px;
                font-weight: bold;
                color: #632ca6;
                margin: 10px 0;
            }}
            .metric-label {{
                font-size: 12px;
                color: #666;
                text-transform: uppercase;
                letter-spacing: 1px;
            }}
        </style>
        <div class="response-area">
            <h2>📊 Datadog Dashboards</h2>
            <p>Source: <a href="https://{dd_site}/dashboard/lists" target="_blank" class="dashboard-link">Datadog Dashboard List</a></p>
            <p style="font-size: 12px; color: #666;">Site: {html.escape(dd_site)}</p>
        """
        
        if search_query and not show_specific_dashboard:
            output += f"""
            <div class='search-info'>
                <strong>🔍 Filter applied:</strong> Showing dashboards matching "<strong>{html.escape(original_query)}</strong>"
            </div>
            """
        
        if not filtered_dashboards:
            output += f"""
            <div style='padding: 10px; margin: 10px 0; background-color: #fff3cd; border: 2px solid #ffc107; border-radius: 4px; text-align: center;'>
                <strong style='font-size: 12px;'>⚠️ No dashboards found matching "{html.escape(original_query)}"</strong>
                <p style='font-size: 11px; margin: 5px 0 0 0;'>Try a different search term or leave it empty to see all dashboards.</p>
            </div>
            """
        elif show_specific_dashboard or len(filtered_dashboards) == 1 or (search_query and len(filtered_dashboards) <= 3):
            # Show detailed view for single dashboard or very specific search
            for dashboard in filtered_dashboards:
                dash_id = dashboard.get("id", "")
                dash_title = dashboard.get("title", "Untitled")
                dash_url = dashboard.get("url", f"https://{dd_site}/dashboard/{dash_id}")
                
                # Calculate timestamp range for display
                import time
                current_time_header = int(time.time())
                from_time_header = current_time_header - (timerange_hours * 3600)
                timestamp_range_html = format_timestamp_range(from_time_header, current_time_header)
                
                # Dashboard header
                output += f"""
                <div style='background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                            padding: 12px; 
                            border-radius: 6px; 
                            margin: 8px 0;
                            color: white;
                            box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
                    <h2 style='margin: 0 0 6px 0; color: white; font-size: 16px; font-weight: bold;'>📊 {html.escape(dash_title)}</h2>
                    <p style='margin: 0 0 4px 0; font-size: 12px; opacity: 0.95;'>
                        Real-time metrics and performance monitoring
                    </p>
                    <p style='margin: 0 0 8px 0;'>
                        <a href='{html.escape(dash_url)}' target='_blank' style='color: white; text-decoration: underline; font-size: 11px; opacity: 0.9;'>
                            Open Interactive Dashboard →
                        </a>
                    </p>
                    {timestamp_range_html}
                </div>
                
                """
                
                # Add service filter info if applicable
                if service_filter:
                    output += f"""
                <div style='margin: 8px 0; padding: 6px; background-color: #fff3cd; border-left: 3px solid #ffc107; border-radius: 4px;'>
                    <p style='margin: 0; font-size: 12px; color: #856404;'>
                        🔍 <strong>Filtering for service:</strong> {html.escape(service_filter)}
                    </p>
                </div>
                """
                
                timerange_text = format_timerange(timerange_hours)
                output += f"""
                <div style='margin: 8px 0; padding: 6px; background-color: #e3f2fd; border-left: 3px solid #2196f3; border-radius: 4px;'>
                    <h4 style='margin: 0 0 4px 0; color: #1976d2; font-size: 14px;'>📊 Dashboard Widgets</h4>
                    <p style='margin: 0; font-size: 11px; color: #666;'>
                        Metrics from the <strong>{timerange_text}</strong>. Click "Open Interactive Dashboard" above for live graphs.
                    </p>
                </div>
                """
                
                # Get dashboard details to show widgets as images
                details = get_dashboard_details(dd_api_key, dd_app_key, dd_site, dash_id)
                
                if details and 'widgets' in details:
                    import time
                    current_time = int(time.time())
                    from_time = current_time - (timerange_hours * 3600)  # Convert hours to seconds
                    
                    # Expand group widgets to get individual widgets
                    expanded_widgets = []
                    for widget in details['widgets']:
                        widget_def = widget.get('definition', {})
                        widget_type = widget_def.get('type', 'unknown')
                        
                        # If it's a group widget, expand its children
                        if widget_type == 'group':
                            group_widgets = widget_def.get('widgets', [])
                            print(f"📦 Found group widget with {len(group_widgets)} children")
                            for child_widget in group_widgets:
                                expanded_widgets.append(child_widget)
                        else:
                            expanded_widgets.append(widget)
                    
                    print(f"📊 Total widgets after expansion: {len(expanded_widgets)}")
                    
                    # Process each widget and display it
                    # Filter widgets by service if specified
                    widgets_to_show = []  # Initialize here to avoid reference errors
                    
                    if service_filter:
                        filtered_widgets = []
                        available_services = set()  # Track all available services
                        
                        for widget in expanded_widgets:
                            widget_def = widget.get('definition', {})
                            widget_title = widget_def.get('title', '').lower()
                            widget_type = widget_def.get('type', 'unknown')
                            
                            # For trace_service widgets, check the service name
                            if widget_type == 'trace_service':
                                service_name = widget_def.get('service', '').lower()
                                available_services.add(service_name)
                                
                                # More flexible matching
                                if (service_filter in service_name or 
                                    service_name in service_filter or
                                    service_filter in widget_title or
                                    service_filter.replace('-', '_') in service_name or
                                    service_filter.replace('_', '-') in service_name):
                                    filtered_widgets.append(widget)
                            # For other widgets, check the title
                            elif service_filter in widget_title:
                                filtered_widgets.append(widget)
                        
                        print(f"Found {len(filtered_widgets)} widgets matching '{service_filter}'")
                        
                        widgets_to_show = filtered_widgets  # Show all filtered widgets
                    else:
                        widgets_to_show = expanded_widgets  # Show all widgets
                    
                    # Check if no widgets found after filtering
                    if len(widgets_to_show) == 0 and service_filter:
                        print(f"⚠️ No widgets found for service: {service_filter}")
                        # Show available services in the error message
                        services_list = ', '.join(sorted(list(available_services)[:10]))
                        output += f"""
                        <div style='padding: 15px; margin: 10px 0; background-color: #fff3cd; border: 2px solid #ffc107; border-radius: 4px;'>
                            <strong style='font-size: 12px;'>⚠️ No widgets found for service "{html.escape(service_filter)}"</strong>
                            <p style='font-size: 11px; margin: 5px 0;'>Available services (first 10):</p>
                            <p style='font-size: 10px; margin: 5px 0; font-family: monospace; color: #666;'>{html.escape(services_list)}</p>
                            <p style='font-size: 11px; margin: 5px 0 0 0;'>Try one of the above or leave empty to see all services.</p>
                        </div>
                        """
                    else:
                        # Add widget header with correct count
                        output += f"""
                        <div style='background-color: #ffffff; padding: 6px; border-radius: 4px; margin: 6px 0; box-shadow: 0 1px 2px rgba(0,0,0,0.06);'>
                            <div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; padding-bottom: 4px; border-bottom: 1px solid #632ca6;'>
                                <h3 style='margin: 0; color: #632ca6; font-size: 14px;'>📊 Dashboard Widgets ({len(widgets_to_show)})</h3>
                                <div style='text-align: right;'>
                                    <div style='font-size: 11px; color: #666;'>{time.strftime('%H:%M:%S')}</div>
                                </div>
                            </div>
                            <div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px;'>
                        """
                    
                    widget_count = 0
                    chart_scripts = []  # Accumulate all chart scripts
                    
                    # OPTIMIZATION: First pass - collect all metric queries for parallel execution
                    print(f"🚀 Phase 1: Collecting all metric queries for parallel execution...")
                    all_queries = {}  # Dictionary to store all queries: {key: query_string}
                    widget_metadata = []  # Store widget info for second pass
                    
                    for widget in widgets_to_show:
                        widget_def = widget.get('definition', {})
                        widget_type = widget_def.get('type', 'unknown')
                        
                        # Skip non-trace_service widgets for now (they'll be processed normally)
                        if widget_type != 'trace_service':
                            widget_metadata.append({'widget': widget, 'queries_keys': None})
                            continue
                        
                        # Extract service information
                        service = widget_def.get('service', 'Unknown')
                        env = widget_def.get('env', 'production')
                        show_hits = widget_def.get('show_hits', True)
                        show_errors = widget_def.get('show_errors', True)
                        show_latency = widget_def.get('show_latency', True)
                        
                        # Generate unique keys for this service's metrics
                        queries_keys = {
                            'requests': f"{service}_{env}_requests",
                            'errors': f"{service}_{env}_errors",
                            'latency_avg': f"{service}_{env}_latency_avg",
                            'latency_min': f"{service}_{env}_latency_min",
                            'latency_max': f"{service}_{env}_latency_max"
                        }
                        
                        # Add queries to the batch
                        if show_hits:
                            all_queries[queries_keys['requests']] = f"trace.servlet.request.hits{{service:{service},env:{env}}}.as_count()"
                        if show_errors:
                            all_queries[queries_keys['errors']] = f"trace.servlet.request.errors{{service:{service},env:{env}}}.as_count()"
                        if show_latency:
                            all_queries[queries_keys['latency_avg']] = f"avg:trace.servlet.request.duration{{service:{service},env:{env}}}"
                            all_queries[queries_keys['latency_min']] = f"min:trace.servlet.request.duration{{service:{service},env:{env}}}"
                            all_queries[queries_keys['latency_max']] = f"max:trace.servlet.request.duration{{service:{service},env:{env}}}"
                        
                        widget_metadata.append({'widget': widget, 'queries_keys': queries_keys})
                    
                    # OPTIMIZATION: Execute all queries in parallel
                    print(f"🚀 Phase 2: Executing {len(all_queries)} metric queries in parallel...")
                    parallel_start = time.time()
                    all_results = get_metrics_parallel(dd_api_key, dd_app_key, dd_site, all_queries, from_time, current_time, max_workers=15)
                    parallel_time = time.time() - parallel_start
                    print(f"✅ Parallel execution completed in {parallel_time:.2f}s")
                    
                    # OPTIMIZATION: Second pass - render widgets using pre-fetched data
                    print(f"🚀 Phase 3: Rendering widgets with pre-fetched data...")
                    
                    # Iterate through widgets and display them
                    for meta in widget_metadata:
                        # Get widget and check if it has pre-fetched data
                        widget = meta['widget']
                        queries_keys = meta['queries_keys']
                        
                        widget_def = widget.get('definition', {})
                        widget_type = widget_def.get('type', 'unknown')
                        widget_title = widget_def.get('title', 'Untitled Widget')
                        
                        # Skip non-graphable widgets
                        if widget_type in ['note', 'free_text', 'iframe']:
                            continue
                        
                        widget_count += 1
                        
                        # Determine icon based on widget title
                        metric_icon = "📊"
                        if 'request' in widget_title.lower() or 'rate' in widget_title.lower():
                            metric_icon = "📈"
                        elif 'error' in widget_title.lower():
                            metric_icon = "⚠️"
                        elif 'latency' in widget_title.lower() or 'duration' in widget_title.lower():
                            metric_icon = "⏱️"
                        
                        # Try to get the metric query for snapshot
                        widget_requests = widget_def.get('requests', [])
                        query = None
                        service_info = None
                        
                        if widget_requests and len(widget_requests) > 0:
                            req = widget_requests[0]
                            query = req.get('q', '') or req.get('query', '')
                        
                        # For trace_service widgets, extract service information
                        if widget_type == 'trace_service':
                            service_info = {
                                'service': widget_def.get('service', 'Unknown'),
                                'env': widget_def.get('env', 'production'),
                                'span_name': widget_def.get('span_name', 'N/A'),
                                'show_hits': widget_def.get('show_hits', True),
                                'show_errors': widget_def.get('show_errors', True),
                                'show_latency': widget_def.get('show_latency', True),
                                'show_breakdown': widget_def.get('show_breakdown', True),
                                'show_distribution': widget_def.get('show_distribution', True),
                                'show_resource_list': widget_def.get('show_resource_list', False),
                            }
                        
                        # Build a graph URL
                        graph_url = None
                        if query:
                            import urllib.parse
                            params = {
                                'height': '300',
                                'width': '600',
                                'legend': 'true',
                                'title': widget_title,
                                'start': str(from_time),
                                'end': str(current_time),
                                'query': query
                            }
                            graph_url = f"https://{dd_site}/dashboard/{dash_id}"
                        
                        output += f"""
                        <div style='background-color: #f7fafc; 
                                    padding: 4px; 
                                    border-radius: 3px; 
                                    border: 1px solid #e2e8f0; 
                                    box-shadow: 0 1px 2px rgba(0,0,0,0.04);'>
                            <div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 3px;'>
                                <h4 style='margin: 0; color: #2d3748; font-size: 13px; font-weight: 600;'>
                                    {metric_icon} {html.escape(widget_title)}
                                </h4>
                                <span style='font-size: 10px; color: #718096; background-color: #e2e8f0; padding: 1px 3px; border-radius: 2px;'>
                                    {html.escape(widget_type)}
                                </span>
                            </div>
                        """
                        
                        if service_info and queries_keys:
                            # Special display for trace_service widgets using pre-fetched data
                            service = service_info['service']
                            env = service_info['env']
                            
                            import json
                            metrics_data = {}
                            chart_data = {}
                            print(f"📊 Rendering metrics for service: {service}, env: {env}")
                            
                            # Get requests data from pre-fetched results
                            if service_info['show_hits'] and queries_keys['requests'] in all_results:
                                requests_data = all_results[queries_keys['requests']]
                                if requests_data and 'series' in requests_data and len(requests_data['series']) > 0:
                                    series = requests_data['series'][0]
                                    if 'pointlist' in series and len(series['pointlist']) > 0:
                                        last_value = series['pointlist'][-1][1] if len(series['pointlist'][-1]) > 1 else 0
                                        metrics_data['requests'] = f"{last_value:.1f} req/s" if last_value else "0.0 req/s"
                                        # Store full series for charting
                                        chart_data['requests'] = {
                                            'labels': [int(p[0]) for p in series['pointlist']],
                                            'values': [p[1] if len(p) > 1 and p[1] is not None else 0 for p in series['pointlist']]
                                        }
                                    else:
                                        metrics_data['requests'] = "0.0 req/s"
                                        chart_data['requests'] = {'labels': [], 'values': []}
                                else:
                                    metrics_data['requests'] = "0.0 req/s"
                                    chart_data['requests'] = {'labels': [], 'values': []}
                                
                                print(f"📊 Requests chart_data labels count: {len(chart_data.get('requests', {}).get('labels', []))}")
                            
                            # Get error data from pre-fetched results
                            if service_info['show_errors'] and queries_keys['errors'] in all_results:
                                errors_data = all_results[queries_keys['errors']]
                                error_count = 0
                                if errors_data and 'series' in errors_data and len(errors_data['series']) > 0:
                                    series = errors_data['series'][0]
                                    if 'pointlist' in series and len(series['pointlist']) > 0:
                                        error_count = series['pointlist'][-1][1] if len(series['pointlist'][-1]) > 1 else 0
                                        chart_data['errors'] = {
                                            'labels': [int(p[0]) for p in series['pointlist']],
                                            'values': [p[1] if len(p) > 1 and p[1] is not None else 0 for p in series['pointlist']]
                                        }
                                    else:
                                        chart_data['errors'] = {'labels': [], 'values': []}
                                else:
                                    chart_data['errors'] = {'labels': [], 'values': []}
                                
                                # Calculate error percentage
                                total_requests = 0
                                if chart_data.get('requests', {}).get('values'):
                                    total_requests = sum(chart_data['requests']['values'])
                                
                                if total_requests > 0 and error_count > 0:
                                    error_percentage = (error_count / total_requests) * 100
                                    if error_percentage < 0.1:
                                        metrics_data['errors'] = f"{error_count:.0f} (< 0.1%)"
                                    elif error_percentage < 1:
                                        metrics_data['errors'] = f"{error_count:.0f} ({error_percentage:.1f}%)"
                                    else:
                                        metrics_data['errors'] = f"{error_count:.0f} ({error_percentage:.1f}%)"
                                elif error_count > 0:
                                    metrics_data['errors'] = f"{error_count:.0f}"
                                else:
                                    metrics_data['errors'] = "0 (0%)"
                            
                            # Get latency data from pre-fetched results
                            if service_info['show_latency']:
                                # Latency metrics configuration
                                latency_metrics = {
                                    'avg': {'label': 'Average', 'color': '#4299e1', 'bg': 'rgba(66, 153, 225, 0.6)', 'key': 'latency_avg'},
                                    'min': {'label': 'Minimum', 'color': '#48bb78', 'bg': 'rgba(72, 187, 120, 0.6)', 'key': 'latency_min'},
                                    'max': {'label': 'Maximum', 'color': '#f6ad55', 'bg': 'rgba(246, 173, 85, 0.6)', 'key': 'latency_max'}
                                }
                                chart_data['latency'] = {'labels': [], 'datasets': []}
                                latency_values = {}
                                
                                for metric, config in latency_metrics.items():
                                    result_key = queries_keys[config['key']]
                                    latency_data = all_results.get(result_key) if result_key in all_results else None
                                    
                                    if latency_data and 'series' in latency_data and len(latency_data['series']) > 0 and latency_data.get('status') != 'error':
                                        series = latency_data['series'][0]
                                        if 'pointlist' in series and len(series['pointlist']) > 0:
                                            # Store labels from first successful query
                                            if not chart_data['latency']['labels']:
                                                chart_data['latency']['labels'] = [int(p[0]) for p in series['pointlist']]
                                            
                                            # Extract values and convert to ms if needed
                                            values = []
                                            last_value = 0
                                            for p in series['pointlist']:
                                                if len(p) > 1 and p[1] is not None:
                                                    val = p[1]
                                                    # Convert to ms if in seconds
                                                    if val < 10:
                                                        val = val * 1000
                                                    values.append(val)
                                                    last_value = val
                                                else:
                                                    values.append(0)
                                            
                                            latency_values[metric] = last_value
                                            
                                            # Add dataset for this metric
                                            chart_data['latency']['datasets'].append({
                                                'label': config['label'],
                                                'data': values,
                                                'borderColor': config['color'],
                                                'backgroundColor': config['bg']
                                            })
                                
                                # Display average latency as main value
                                if 'avg' in latency_values:
                                    metrics_data['latency'] = f"{latency_values['avg']:.1f}ms avg"
                                else:
                                    metrics_data['latency'] = "0ms avg"
                            
                            chart_id = f"chart_{service.replace('-', '_')}_{widget_count}"
                            
                            output += f"""
                            <div style='background-color: #f9fafb; 
                                        padding: 3px; 
                                        border-radius: 2px; 
                                        border-left: 1px solid #632ca6;
                                        box-shadow: 0 1px 2px rgba(0,0,0,0.04);
                                        max-width: 100%;'>
                                <div style='margin-bottom: 2px;'>
                                    <span style='font-size: 12px; font-weight: bold; color: #2d3748;'>
                                        {html.escape(service)}
                                    </span>
                                    <span style='font-size: 10px; color: #718096; margin-left: 2px;'>
                                        #{html.escape(env)}
                                    </span>
                                </div>
                                <div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 3px;'>
                            """
                            
                            # Show metrics with charts - always show even if no data
                            if service_info['show_hits']:
                                value = metrics_data.get('requests', 'N/A')
                                chart_requests_id = f"{chart_id}_requests"
                                output += f"""
                                <div style='background: white; padding: 2px; border-radius: 2px; border: 1px solid #e5e7eb;'>
                                    <div style='margin-bottom: 1px;'>
                                        <div style='font-size: 10px; color: #6b7280;'>Requests</div>
                                        <div style='font-size: 13px; font-weight: bold; color: #1890ff;'>{html.escape(value)}</div>
                                    </div>
                                    <div style='height: 110px; position: relative; background: #f9f9f9;'>
                                        <canvas id='{chart_requests_id}' width='100' height='110'></canvas>
                                    </div>
                                </div>
                                """
                                # Always generate chart script (with default data if empty)
                                requests_data = chart_data.get('requests', {'labels': [], 'values': []})
                                if not requests_data.get('labels'):
                                    requests_data = {'labels': [''], 'values': [0]}
                                
                                chart_scripts.append(f"""
                                    (function() {{
                                        const ctx = document.getElementById('{chart_requests_id}');
                                        if (!ctx) return;
                                        const data = {json.dumps(requests_data)};
                                        try {{
                                            const chartLabels = data.labels.length > 0 && data.labels[0] !== '' 
                                                ? data.labels.map(t => new Date(t).toLocaleTimeString('en-US', {{hour: '2-digit', minute: '2-digit'}}))
                                                : [''];
                                            new Chart(ctx, {{
                                                type: 'bar',
                                                data: {{
                                                    labels: chartLabels,
                                                    datasets: [{{
                                                        label: 'Hits',
                                                        data: data.values,
                                                        backgroundColor: 'rgba(24, 144, 255, 0.7)',
                                                        borderColor: '#1890ff',
                                                        borderWidth: 1
                                                    }}]
                                                }},
                                                options: {{
                                                    responsive: true,
                                                    maintainAspectRatio: false,
                                                    plugins: {{ 
                                                        legend: {{ 
                                                            display: true,
                                                            position: 'bottom',
                                                            labels: {{
                                                                boxWidth: 10,
                                                                font: {{ size: 8 }},
                                                                padding: 4
                                                            }}
                                                        }} 
                                                    }},
                                                    scales: {{
                                                        x: {{ display: false }},
                                                        y: {{ 
                                                            beginAtZero: true, 
                                                            display: true,
                                                            ticks: {{ 
                                                                font: {{ size: 8 }}
                                                            }}
                                                        }}
                                                    }}
                                                }}
                                            }});
                                        }} catch(e) {{
                                            console.error('Chart error:', e.message);
                                        }}
                                    }})();
                                """)
                            
                            if service_info['show_errors']:
                                value = metrics_data.get('errors', 'N/A')
                                chart_errors_id = f"{chart_id}_errors"
                                output += f"""
                                <div style='background: white; padding: 2px; border-radius: 2px; border: 1px solid #e5e7eb;'>
                                    <div style='margin-bottom: 1px;'>
                                        <div style='font-size: 10px; color: #6b7280;'>Errors</div>
                                        <div style='font-size: 13px; font-weight: bold; color: #ff4d4f;'>{html.escape(value)}</div>
                                    </div>
                                    <div style='height: 110px; position: relative; background: #f9f9f9;'>
                                        <canvas id='{chart_errors_id}' width='100' height='110'></canvas>
                                    </div>
                                </div>
                                """
                                # Always generate chart script for Errors
                                errors_data = chart_data.get('errors', {'labels': [], 'values': []})
                                if not errors_data.get('labels'):
                                    errors_data = {'labels': [''], 'values': [0]}
                                
                                chart_scripts.append(f"""
                                    (function() {{
                                        const ctx = document.getElementById('{chart_errors_id}');
                                        if (!ctx) return;
                                        const data = {json.dumps(errors_data)};
                                        try {{
                                            const chartLabels = data.labels.length > 0 && data.labels[0] !== '' 
                                                ? data.labels.map(t => new Date(t).toLocaleTimeString('en-US', {{hour: '2-digit', minute: '2-digit'}}))
                                                : [''];
                                            new Chart(ctx, {{
                                                type: 'bar',
                                                data: {{
                                                    labels: chartLabels,
                                                    datasets: [{{
                                                        label: 'Errors',
                                                        data: data.values,
                                                        backgroundColor: 'rgba(255, 77, 79, 0.7)',
                                                        borderColor: '#ff4d4f',
                                                        borderWidth: 1
                                                    }}]
                                                }},
                                                options: {{
                                                    responsive: true,
                                                    maintainAspectRatio: false,
                                                    plugins: {{ 
                                                        legend: {{ 
                                                            display: true,
                                                            position: 'bottom',
                                                            labels: {{
                                                                boxWidth: 10,
                                                                font: {{ size: 8 }},
                                                                padding: 4
                                                            }}
                                                        }} 
                                                    }},
                                                    scales: {{
                                                        x: {{ display: false }},
                                                        y: {{ 
                                                            beginAtZero: true, 
                                                            display: true,
                                                            ticks: {{ 
                                                                font: {{ size: 8 }}
                                                            }}
                                                        }}
                                                    }}
                                                }}
                                            }});
                                        }} catch(e) {{
                                            console.error('Chart error:', e.message);
                                        }}
                                    }})();
                                """)
                            
                            if service_info['show_latency']:
                                value = metrics_data.get('latency', 'N/A')
                                chart_latency_id = f"{chart_id}_latency"
                                output += f"""
                                <div style='background: white; padding: 2px; border-radius: 2px; border: 1px solid #e5e7eb;'>
                                    <div style='margin-bottom: 1px;'>
                                        <div style='font-size: 10px; color: #6b7280;'>Latency</div>
                                        <div style='font-size: 13px; font-weight: bold; color: #52c41a;'>{html.escape(value)}</div>
                                    </div>
                                    <div style='height: 110px; position: relative; background: #f9f9f9;'>
                                        <canvas id='{chart_latency_id}' width='100' height='110'></canvas>
                                    </div>
                                </div>
                                """
                                # Always generate chart script for Latency
                                latency_chart_data = chart_data.get('latency', {'labels': [], 'datasets': []})
                                if not latency_chart_data.get('labels'):
                                    latency_chart_data = {'labels': [''], 'datasets': [{'label': 'Average', 'data': [0], 'borderColor': '#4299e1', 'backgroundColor': 'rgba(66, 153, 225, 0.6)'}]}
                                
                                chart_scripts.append(f"""
                                    (function() {{
                                        const ctx = document.getElementById('{chart_latency_id}');
                                        if (!ctx) return;
                                        const data = {json.dumps(latency_chart_data)};
                                        try {{
                                            const chartLabels = data.labels.length > 0 && data.labels[0] !== '' 
                                                ? data.labels.map(t => new Date(t).toLocaleTimeString('en-US', {{hour: '2-digit', minute: '2-digit'}}))
                                                : [''];
                                            
                                            new Chart(ctx, {{
                                                type: 'bar',
                                                data: {{
                                                    labels: chartLabels,
                                                    datasets: data.datasets.map(ds => ({{
                                                        label: ds.label,
                                                        data: ds.data,
                                                        borderColor: ds.borderColor,
                                                        backgroundColor: ds.backgroundColor,
                                                        borderWidth: 1
                                                    }}))
                                                }},
                                                options: {{
                                                    responsive: true,
                                                    maintainAspectRatio: false,
                                                    plugins: {{ 
                                                        legend: {{ 
                                                            display: true,
                                                            position: 'bottom',
                                                            labels: {{
                                                                boxWidth: 8,
                                                                font: {{ size: 8 }},
                                                                padding: 3
                                                            }}
                                                        }} 
                                                    }},
                                                    scales: {{
                                                        x: {{ display: false }},
                                                        y: {{ 
                                                            beginAtZero: true, 
                                                            display: true, 
                                                            ticks: {{ 
                                                                font: {{ size: 8 }},
                                                                callback: function(value) {{
                                                                    return value.toFixed(0) + 'ms';
                                                                }}
                                                            }} 
                                                        }}
                                                    }}
                                                }}
                                            }});
                                        }} catch(e) {{
                                            console.error('Latency chart error:', e.message);
                                        }}
                                    }})();
                                """)
                            
                            output += f"""
                                </div>
                                <div style='text-align: center; margin-top: 2px; padding-top: 2px; border-top: 1px solid #e5e7eb;'>
                                    <a href='https://{dd_site}/apm/service/{html.escape(service)}?env={html.escape(env)}' target='_blank' 
                                       style='display: inline-block; padding: 3px 6px; background-color: #632ca6; color: white; 
                                              text-decoration: none; border-radius: 2px; font-size: 11px; font-weight: 600;'>
                                        View →
                                    </a>
                                </div>
                            </div>
                            """
                        elif query:
                            # Try to create a graph snapshot for metric queries
                            print(f"Creating snapshot for: {widget_title}")
                            snapshot_url = create_graph_snapshot(dd_api_key, dd_app_key, dd_site, query, from_time, current_time, widget_title)
                            
                            if snapshot_url:
                                # Show the actual graph image
                                output += f"""
                                <div style='background-color: #ffffff; 
                                            padding: 4px; 
                                            border-radius: 3px; 
                                            border: 1px solid #e2e8f0;'>
                                    <img src='{html.escape(snapshot_url)}' 
                                         alt='{html.escape(widget_title)}' 
                                         style='width: 100%; height: auto; max-height: 150px; object-fit: contain; border-radius: 2px;'
                                         onerror="this.parentElement.innerHTML='<div style=\\'padding: 10px; text-align: center; color: #e53e3e; font-size: 10px;\\'>❌ Failed to load</div>';">
                                    <div style='margin-top: 3px; text-align: center;'>
                                        <a href='{html.escape(graph_url)}' target='_blank' 
                                           style='font-size: 9px; color: #632ca6; text-decoration: none;'>
                                            View Graph →
                                        </a>
                                    </div>
                                </div>
                                """
                            else:
                                # Fallback if snapshot fails
                                output += f"""
                                <div style='background-color: #ffffff; 
                                            padding: 8px; 
                                            border-radius: 3px; 
                                            border: 1px dashed #cbd5e0; 
                                            text-align: center;
                                            min-height: 110px;
                                            display: flex;
                                            flex-direction: column;
                                            justify-content: center;
                                            align-items: center;'>
                                    <div style='font-size: 24px; margin-bottom: 4px;'>📊</div>
                                    <p style='margin: 0 0 4px 0; font-size: 11px; color: #4a5568; font-weight: 500;'>
                                        {html.escape(widget_title[:50])}{'...' if len(widget_title) > 50 else ''}
                                    </p>
                                    <a href='{html.escape(graph_url)}' target='_blank' 
                                       style='display: inline-block; padding: 3px 8px; background-color: #632ca6; color: white; 
                                              text-decoration: none; border-radius: 2px; font-size: 9px; font-weight: 600;'>
                                        View Graph →
                                    </a>
                                </div>
                                """
                        else:
                            output += f"""
                            <div style='background-color: #ffffff; padding: 5px; border-radius: 3px; border: 1px solid #e2e8f0;'>
                                <p style='margin: 0; font-size: 10px; color: #718096;'>
                                    {html.escape(widget_type)} - <a href='https://{dd_site}/dashboard/{dash_id}' target='_blank' style='color: #632ca6; font-size: 9px;'>View →</a>
                                </p>
                            </div>
                            """
                        
                        output += "</div>"
                    
                    # Close grid and container only if we opened them (i.e., if we have widgets to show)
                    if len(widgets_to_show) > 0:
                        output += "</div></div>"  # Close grid and container
                    
                    # Add all chart scripts (Chart.js is now loaded globally in index.html)
                    if chart_scripts:
                        output += """
                        <script>
                        // Wait for DOM to be fully ready after innerHTML insertion
                        setTimeout(function() {
                            if (typeof Chart === 'undefined') {
                                console.error('Chart.js not found');
                                return;
                            }
                        """
                        output += "\n".join(chart_scripts)
                        output += """
                        }, 300);
                        </script>
                        """
                    else:
                        print("⚠️ No chart scripts to add!")
                else:
                    output += """
                    <div style='padding: 10px; text-align: center; background-color: #fff3cd; border-radius: 3px; margin: 6px 0;'>
                        <p style='margin: 0; color: #856404; font-size: 11px;'>
                            ⚠️ Unable to load widget details
                        </p>
                    </div>
                    """
                
                output += "<hr style='margin: 6px 0; border: none; border-top: 1px solid #e2e8f0;'>"
        else:
            output += f"<h3 class='datadog-header'>📋 Found {len(filtered_dashboards)} Dashboard(s)</h3>"
            output += "<table class='datadog-table'>"
            output += "<tr><th>Title</th><th>ID</th><th>Type</th><th>Author</th><th>URL</th></tr>"
            
            for dashboard in sorted(filtered_dashboards, key=lambda x: x.get("title", "").lower()):
                title = dashboard.get("title", "Untitled")
                dash_id = dashboard.get("id", "N/A")
                dash_type = dashboard.get("layout_type", "N/A")
                author = dashboard.get("author_name", "Unknown")
                dash_url = dashboard.get("url", "")
                
                # Construct full URL if not provided
                if not dash_url.startswith("http"):
                    dash_url = f"https://{dd_site}/dashboard/{dash_id}"
                
                output += f"""
                <tr>
                    <td class='dashboard-title'>{html.escape(title)}</td>
                    <td><code>{html.escape(dash_id)}</code></td>
                    <td>{html.escape(dash_type.capitalize())}</td>
                    <td>{html.escape(author)}</td>
                    <td><a href="{html.escape(dash_url)}" target="_blank" class="dashboard-link">Open Dashboard</a></td>
                </tr>
                """
            
            output += "</table>"
            
            # Add summary
            output += f"""
            <div style='padding: 10px; margin: 20px 0; background-color: #d4edda; border-left: 4px solid #28a745; border-radius: 3px;'>
                <strong>✅ Total:</strong> {len(filtered_dashboards)} dashboard(s) displayed
            </div>
            """
        
        output += "</div>"
        return output
        
    except requests.exceptions.Timeout:
        return "<p>❌ Error: Request timed out. Please try again.</p>"
    except requests.exceptions.RequestException as e:
        return f"<p>❌ Error connecting to Datadog API: {html.escape(str(e))}</p>"
    except Exception as e:
        return f"<p>❌ Unexpected error: {html.escape(str(e))}</p>"


def read_datadog_adt(query: str, timerange_hours: int = 4) -> str:
    """
    Shows the RED - Metrics - ADT dashboard with embedded graphs.
    If a service name is provided, filters widgets for that specific service.
    Args:
        query: Service name to filter or dashboard ID
        timerange_hours: Number of hours to look back (default: 4)
    """
    print("=" * 80)
    print("🔎 Reading Datadog ADT Dashboard")
    print(f"📝 Query received: '{query}'")
    print(f"📝 Time range: {timerange_hours} hours")
    
    # Get Datadog credentials from environment
    dd_api_key = os.getenv("DATADOG_API_KEY")
    dd_app_key = os.getenv("DATADOG_APP_KEY")
    dd_site = os.getenv("DATADOG_SITE", "datadoghq.com")
    
    # Default RED - Metrics - ADT dashboard ID
    default_adt_dashboard_id = "cum-ivw-92c"  # RED Metrics - partnerprod (ADT)
    service_filter = None
    
    # If query provided, use it as service filter
    if query and query.strip():
        service_filter = query.strip()
        print(f"🔍 Filtering ADT dashboard for service: {service_filter}")
    else:
        print("📊 Showing RED - Metrics - ADT dashboard")
    
    if not dd_api_key or not dd_app_key:
        return "<p>❌ Datadog API keys not configured. Please set DATADOG_API_KEY and DATADOG_APP_KEY in your .env file.</p>"
    
    output = ""
    
    try:
        # Calculate time range
        current_time = int(time.time())
        from_time = current_time - (timerange_hours * 3600)  # Convert hours to seconds
        
        # Get dashboard details
        print(f"📊 Fetching ADT dashboard: {default_adt_dashboard_id}")
        details = get_dashboard_details(dd_api_key, dd_app_key, dd_site, default_adt_dashboard_id)
        
        if not details or 'widgets' not in details:
            return "<p>❌ Could not fetch ADT dashboard details. Please verify the dashboard ID.</p>"
        
        dash_id = default_adt_dashboard_id
        dash_title = details.get('title', 'RED - Metrics - ADT')
        dash_url = f"https://{dd_site}/dashboard/{dash_id}"
        
        # Generate timestamp range display
        timestamp_range_html = format_timestamp_range(from_time, current_time)
        
        # Dashboard header
        output += f"""
        <div style='background: linear-gradient(135deg, #7c3aed 0%, #5b21b6 100%); 
                    padding: 12px; 
                    border-radius: 6px; 
                    margin: 0 0 8px 0;
                    color: white;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
            <h2 style='margin: 0 0 6px 0; color: white; font-size: 16px; font-weight: bold;'>📊 {html.escape(dash_title)}</h2>
            <p style='margin: 0 0 4px 0; font-size: 12px; opacity: 0.95;'>
                Real-time monitoring dashboard from Datadog
            </p>
            <p style='margin: 0 0 8px 0;'>
                <a href='{html.escape(dash_url)}' target='_blank' style='color: white; text-decoration: underline; font-size: 11px; opacity: 0.9;'>
                    Open Interactive Dashboard →
                </a>
            </p>
            {timestamp_range_html}
        </div>
        """
        
        if service_filter:
            output += f"""
            <div style='margin: 8px 0; padding: 6px; background-color: #fff3cd; border-left: 3px solid #ffc107; border-radius: 4px;'>
                <p style='margin: 0; font-size: 12px; color: #856404;'>
                    🔍 <strong>Filtering for service:</strong> {html.escape(service_filter)}
                </p>
            </div>
            """
        
        # Extract and expand widgets
        widgets = details.get('widgets', [])
        expanded_widgets = []
        
        for widget in widgets:
            widget_def = widget.get('definition', {})
            widget_type = widget_def.get('type', 'unknown')
            
            # Expand group widgets
            if widget_type == 'group':
                group_widgets = widget_def.get('widgets', [])
                for group_widget in group_widgets:
                    expanded_widgets.append(group_widget)
            else:
                expanded_widgets.append(widget)
        
        # Filter widgets if service filter provided
        if service_filter:
            filtered_widgets = []
            for widget in expanded_widgets:
                widget_def = widget.get('definition', {})
                widget_title = widget_def.get('title', '').lower()
                
                # Check if service name appears in widget title
                if service_filter.lower() in widget_title:
                    filtered_widgets.append(widget)
            
            widgets_to_show = filtered_widgets
            print(f"Found {len(filtered_widgets)} ADT widgets matching '{service_filter}'")
        else:
            widgets_to_show = expanded_widgets
        
        if len(widgets_to_show) == 0:
            return f"<p>⚠️ No widgets found{' for service: ' + service_filter if service_filter else ''}</p>"
        
        # Similar widget rendering as read_datadog_dashboards
        # (reuse the same rendering logic)
        output += f"""
        <div style='background-color: #ffffff; padding: 6px; border-radius: 4px; margin: 6px 0; box-shadow: 0 1px 2px rgba(0,0,0,0.06);'>
            <div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; padding-bottom: 4px; border-bottom: 1px solid #7c3aed;'>
                <h3 style='margin: 0; color: #7c3aed; font-size: 14px;'>📊 ADT Dashboard Widgets ({len(widgets_to_show)})</h3>
                <div style='text-align: right;'>
                    <div style='font-size: 11px; color: #666;'>{time.strftime('%H:%M:%S')}</div>
                </div>
            </div>
            <div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px;'>
        """
        
        # OPTIMIZATION: Three-phase processing for parallel API calls
        widget_count = 0
        chart_scripts = []
        
        # Phase 1: Collect all queries
        print(f"🚀 ADT Phase 1: Collecting metric queries...")
        all_queries = {}
        widget_metadata = []
        
        for widget in widgets_to_show:
            widget_def = widget.get('definition', {})
            widget_type = widget_def.get('type', 'unknown')
            
            if widget_type != 'trace_service':
                widget_metadata.append({'widget': widget, 'queries_keys': None})
                continue
            
            service = widget_def.get('service', 'Unknown')
            env = widget_def.get('env', 'production')
            show_hits = widget_def.get('show_hits', True)
            show_errors = widget_def.get('show_errors', True)
            show_latency = widget_def.get('show_latency', True)
            
            queries_keys = {
                'requests': f"{service}_{env}_requests",
                'errors': f"{service}_{env}_errors",
                'latency_avg': f"{service}_{env}_latency_avg",
                'latency_min': f"{service}_{env}_latency_min",
                'latency_max': f"{service}_{env}_latency_max"
            }
            
            if show_hits:
                all_queries[queries_keys['requests']] = f"trace.servlet.request.hits{{service:{service},env:{env}}}.as_count()"
            if show_errors:
                all_queries[queries_keys['errors']] = f"trace.servlet.request.errors{{service:{service},env:{env}}}.as_count()"
            if show_latency:
                all_queries[queries_keys['latency_avg']] = f"avg:trace.servlet.request.duration{{service:{service},env:{env}}}"
                all_queries[queries_keys['latency_min']] = f"min:trace.servlet.request.duration{{service:{service},env:{env}}}"
                all_queries[queries_keys['latency_max']] = f"max:trace.servlet.request.duration{{service:{service},env:{env}}}"
            
            widget_metadata.append({'widget': widget, 'queries_keys': queries_keys})
        
        # Phase 2: Execute all queries in parallel
        print(f"🚀 ADT Phase 2: Executing {len(all_queries)} queries in parallel...")
        parallel_start = time.time()
        all_results = get_metrics_parallel(dd_api_key, dd_app_key, dd_site, all_queries, from_time, current_time, max_workers=15)
        print(f"✅ ADT parallel execution: {time.time() - parallel_start:.2f}s")
        
        # Phase 3: Render widgets with pre-fetched data
        print(f"🚀 ADT Phase 3: Rendering widgets...")
        
        for meta in widget_metadata:
            widget = meta['widget']
            queries_keys = meta['queries_keys']
            
            widget_def = widget.get('definition', {})
            widget_type = widget_def.get('type', 'unknown')
            widget_title = widget_def.get('title', 'Untitled Widget')
            
            if widget_type in ['note', 'free_text', 'iframe']:
                continue
            
            widget_count += 1
            
            service_info = None
            if widget_type == 'trace_service':
                service_info = {
                    'service': widget_def.get('service', 'Unknown'),
                    'env': widget_def.get('env', 'production'),
                    'span_name': widget_def.get('span_name', 'N/A'),
                    'show_hits': widget_def.get('show_hits', True),
                    'show_errors': widget_def.get('show_errors', True),
                    'show_latency': widget_def.get('show_latency', True),
                    'show_breakdown': widget_def.get('show_breakdown', True),
                    'show_distribution': widget_def.get('show_distribution', True),
                    'show_resource_list': widget_def.get('show_resource_list', False),
                }
            
            if service_info and queries_keys:
                service = service_info['service']
                env = service_info['env']
                
                metrics_data = {}
                chart_data = {'requests': {}, 'errors': {}, 'latency': {}}
                
                # Get requests data from pre-fetched results
                if service_info['show_hits'] and queries_keys['requests'] in all_results:
                    requests_data = all_results[queries_keys['requests']]
                    
                    if requests_data and 'series' in requests_data and len(requests_data['series']) > 0:
                        series = requests_data['series'][0]
                        if 'pointlist' in series and len(series['pointlist']) > 0:
                            chart_data['requests']['labels'] = [int(p[0]) for p in series['pointlist']]
                            chart_data['requests']['values'] = [p[1] if len(p) > 1 else 0 for p in series['pointlist']]
                            total_requests = series['pointlist'][-1][1] if len(series['pointlist'][-1]) > 1 else 0
                            metrics_data['requests'] = f"{total_requests:.1f} req/s"
                        else:
                            metrics_data['requests'] = "0 req/s"
                    else:
                        metrics_data['requests'] = "0 req/s"
                
                # Get errors data from pre-fetched results
                if service_info['show_errors'] and queries_keys['errors'] in all_results:
                    errors_data = all_results[queries_keys['errors']]
                    error_count = 0
                    
                    if errors_data and 'series' in errors_data and len(errors_data['series']) > 0:
                        series = errors_data['series'][0]
                        if 'pointlist' in series and len(series['pointlist']) > 0:
                            error_count = series['pointlist'][-1][1] if len(series['pointlist'][-1]) > 1 else 0
                            chart_data['errors'] = {
                                'labels': [int(p[0]) for p in series['pointlist']],
                                'values': [p[1] if len(p) > 1 else 0 for p in series['pointlist']]
                            }
                    
                    # Calculate error percentage
                    total_requests = 0
                    if chart_data.get('requests', {}).get('values'):
                        total_requests = sum(chart_data['requests']['values'])
                    
                    if total_requests > 0 and error_count > 0:
                        error_percentage = (error_count / total_requests) * 100
                        if error_percentage < 0.1:
                            metrics_data['errors'] = f"{error_count:.0f} (< 0.1%)"
                        else:
                            metrics_data['errors'] = f"{error_count:.0f} ({error_percentage:.1f}%)"
                    elif error_count > 0:
                        metrics_data['errors'] = f"{error_count:.0f}"
                    else:
                        metrics_data['errors'] = "0 (0%)"
                
                # Get latency data from pre-fetched results
                if service_info['show_latency']:
                    latency_metrics = {
                        'avg': {'label': 'Average', 'color': '#4299e1', 'bg': 'rgba(66, 153, 225, 0.6)', 'key': 'latency_avg'},
                        'min': {'label': 'Minimum', 'color': '#48bb78', 'bg': 'rgba(72, 187, 120, 0.6)', 'key': 'latency_min'},
                        'max': {'label': 'Maximum', 'color': '#f6ad55', 'bg': 'rgba(246, 173, 85, 0.6)', 'key': 'latency_max'}
                    }
                    chart_data['latency'] = {'labels': [], 'datasets': []}
                    latency_values = {}
                    
                    for metric, config in latency_metrics.items():
                        result_key = queries_keys[config['key']]
                        latency_data = all_results.get(result_key) if result_key in all_results else None
                        
                        if latency_data and 'series' in latency_data and len(latency_data['series']) > 0 and latency_data.get('status') != 'error':
                            series = latency_data['series'][0]
                            if 'pointlist' in series and len(series['pointlist']) > 0:
                                if not chart_data['latency']['labels']:
                                    chart_data['latency']['labels'] = [int(p[0]) for p in series['pointlist']]
                                
                                values = []
                                last_value = 0
                                for p in series['pointlist']:
                                    if len(p) > 1 and p[1] is not None:
                                        val = p[1]
                                        if val < 10:
                                            val = val * 1000
                                        values.append(val)
                                        last_value = val
                                    else:
                                        values.append(0)
                                
                                latency_values[metric] = last_value
                                chart_data['latency']['datasets'].append({
                                    'label': config['label'],
                                    'data': values,
                                    'borderColor': config['color'],
                                    'backgroundColor': config['bg']
                                })
                    
                    if 'avg' in latency_values:
                        metrics_data['latency'] = f"{latency_values['avg']:.1f}ms avg"
                    else:
                        metrics_data['latency'] = "0ms avg"
                
                # Generate widget HTML (same structure as read_datadog_dashboards)
                chart_id = f"adt_chart_{service.replace('-', '_')}_{widget_count}"
                
                output += f"""
                <div style='background-color: #f9fafb; 
                            padding: 3px; 
                            border-radius: 2px; 
                            border-left: 1px solid #7c3aed;
                            box-shadow: 0 1px 2px rgba(0,0,0,0.04);
                            max-width: 100%;'>
                    <div style='margin-bottom: 2px;'>
                        <span style='font-size: 12px; font-weight: bold; color: #2d3748;'>
                            {html.escape(service)}
                        </span>
                        <span style='font-size: 10px; color: #718096; margin-left: 2px;'>
                            #{html.escape(env)}
                        </span>
                    </div>
                    <div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 2px;'>
                """
                
                # Requests chart
                if service_info['show_hits']:
                    value = metrics_data.get('requests', 'N/A')
                    chart_requests_id = f"{chart_id}_requests"
                    output += f"""
                    <div style='background: white; padding: 2px; border-radius: 2px; border: 1px solid #e5e7eb;'>
                        <div style='margin-bottom: 1px;'>
                            <div style='font-size: 10px; color: #6b7280;'>Requests</div>
                            <div style='font-size: 13px; font-weight: bold; color: #1890ff;'>{html.escape(value)}</div>
                        </div>
                        <div style='height: 110px; position: relative; background: #f9f9f9;'>
                            <canvas id='{chart_requests_id}' width='100' height='110'></canvas>
                        </div>
                    </div>
                    """
                    
                    requests_data = chart_data.get('requests', {'labels': [], 'values': []})
                    if not requests_data.get('labels'):
                        requests_data = {'labels': [''], 'values': [0]}
                    
                    chart_scripts.append(f"""
                        (function() {{
                            const ctx = document.getElementById('{chart_requests_id}');
                            if (!ctx) return;
                            const data = {json.dumps(requests_data)};
                            try {{
                                const chartLabels = data.labels.length > 0 && data.labels[0] !== '' 
                                    ? data.labels.map(t => new Date(t).toLocaleTimeString('en-US', {{hour: '2-digit', minute: '2-digit'}}))
                                    : [''];
                                new Chart(ctx, {{
                                    type: 'bar',
                                    data: {{
                                        labels: chartLabels,
                                        datasets: [{{
                                            label: 'Hits',
                                            data: data.values,
                                            backgroundColor: 'rgba(24, 144, 255, 0.7)',
                                            borderColor: '#1890ff',
                                            borderWidth: 1
                                        }}]
                                    }},
                                    options: {{
                                        responsive: true,
                                        maintainAspectRatio: false,
                                        plugins: {{ 
                                            legend: {{ 
                                                display: true,
                                                position: 'bottom',
                                                labels: {{
                                                    boxWidth: 10,
                                                    font: {{ size: 8 }},
                                                    padding: 4
                                                }}
                                            }} 
                                        }},
                                        scales: {{
                                            x: {{ display: false }},
                                            y: {{ 
                                                beginAtZero: true, 
                                                display: true,
                                                ticks: {{ 
                                                    font: {{ size: 8 }}
                                                }}
                                            }}
                                        }}
                                    }}
                                }});
                            }} catch(e) {{
                                console.error('Chart error:', e.message);
                            }}
                        }})();
                    """)
                
                # Errors chart
                if service_info['show_errors']:
                    value = metrics_data.get('errors', 'N/A')
                    chart_errors_id = f"{chart_id}_errors"
                    output += f"""
                    <div style='background: white; padding: 2px; border-radius: 2px; border: 1px solid #e5e7eb;'>
                        <div style='margin-bottom: 1px;'>
                            <div style='font-size: 10px; color: #6b7280;'>Errors</div>
                            <div style='font-size: 13px; font-weight: bold; color: #ff4d4f;'>{html.escape(value)}</div>
                        </div>
                        <div style='height: 110px; position: relative; background: #f9f9f9;'>
                            <canvas id='{chart_errors_id}' width='100' height='110'></canvas>
                        </div>
                    </div>
                    """
                    
                    errors_data = chart_data.get('errors', {'labels': [], 'values': []})
                    if not errors_data.get('labels'):
                        errors_data = {'labels': [''], 'values': [0]}
                    
                    chart_scripts.append(f"""
                        (function() {{
                            const ctx = document.getElementById('{chart_errors_id}');
                            if (!ctx) return;
                            const data = {json.dumps(errors_data)};
                            try {{
                                const chartLabels = data.labels.length > 0 && data.labels[0] !== '' 
                                    ? data.labels.map(t => new Date(t).toLocaleTimeString('en-US', {{hour: '2-digit', minute: '2-digit'}}))
                                    : [''];
                                new Chart(ctx, {{
                                    type: 'bar',
                                    data: {{
                                        labels: chartLabels,
                                        datasets: [{{
                                            label: 'Errors',
                                            data: data.values,
                                            backgroundColor: 'rgba(255, 77, 79, 0.7)',
                                            borderColor: '#ff4d4f',
                                            borderWidth: 1
                                        }}]
                                    }},
                                    options: {{
                                        responsive: true,
                                        maintainAspectRatio: false,
                                        plugins: {{ 
                                            legend: {{ 
                                                display: true,
                                                position: 'bottom',
                                                labels: {{
                                                    boxWidth: 10,
                                                    font: {{ size: 8 }},
                                                    padding: 4
                                                }}
                                            }} 
                                        }},
                                        scales: {{
                                            x: {{ display: false }},
                                            y: {{ 
                                                beginAtZero: true, 
                                                display: true,
                                                ticks: {{ 
                                                    font: {{ size: 8 }}
                                                }}
                                            }}
                                        }}
                                    }}
                                }});
                            }} catch(e) {{
                                console.error('Chart error:', e.message);
                            }}
                        }})();
                    """)
                
                # Latency chart
                if service_info['show_latency']:
                    value = metrics_data.get('latency', 'N/A')
                    chart_latency_id = f"{chart_id}_latency"
                    output += f"""
                    <div style='background: white; padding: 2px; border-radius: 2px; border: 1px solid #e5e7eb;'>
                        <div style='margin-bottom: 1px;'>
                            <div style='font-size: 10px; color: #6b7280;'>Latency</div>
                            <div style='font-size: 13px; font-weight: bold; color: #52c41a;'>{html.escape(value)}</div>
                        </div>
                        <div style='height: 110px; position: relative; background: #f9f9f9;'>
                            <canvas id='{chart_latency_id}' width='100' height='110'></canvas>
                        </div>
                    </div>
                    """
                    
                    latency_chart_data = chart_data.get('latency', {'labels': [], 'datasets': []})
                    if not latency_chart_data.get('labels'):
                        latency_chart_data = {'labels': [''], 'datasets': [{'label': 'Average', 'data': [0], 'borderColor': '#4299e1', 'backgroundColor': 'rgba(66, 153, 225, 0.6)'}]}
                    
                    chart_scripts.append(f"""
                        (function() {{
                            const ctx = document.getElementById('{chart_latency_id}');
                            if (!ctx) return;
                            const data = {json.dumps(latency_chart_data)};
                            try {{
                                const chartLabels = data.labels.length > 0 && data.labels[0] !== '' 
                                    ? data.labels.map(t => new Date(t).toLocaleTimeString('en-US', {{hour: '2-digit', minute: '2-digit'}}))
                                    : [''];
                                
                                new Chart(ctx, {{
                                    type: 'bar',
                                    data: {{
                                        labels: chartLabels,
                                        datasets: data.datasets.map(ds => ({{
                                            label: ds.label,
                                            data: ds.data,
                                            borderColor: ds.borderColor,
                                            backgroundColor: ds.backgroundColor,
                                            borderWidth: 1
                                        }}))
                                    }},
                                    options: {{
                                        responsive: true,
                                        maintainAspectRatio: false,
                                        plugins: {{ 
                                            legend: {{ 
                                                display: true,
                                                position: 'bottom',
                                                labels: {{
                                                    boxWidth: 8,
                                                    font: {{ size: 8 }},
                                                    padding: 3
                                                }}
                                            }} 
                                        }},
                                        scales: {{
                                            x: {{ display: false }},
                                            y: {{ 
                                                beginAtZero: true, 
                                                display: true, 
                                                ticks: {{ 
                                                    font: {{ size: 8 }},
                                                    callback: function(value) {{
                                                        return value.toFixed(0) + 'ms';
                                                    }}
                                                }} 
                                            }}
                                        }}
                                    }}
                                }});
                            }} catch(e) {{
                                console.error('Latency chart error:', e.message);
                            }}
                        }})();
                    """)
                
                output += f"""
                    </div>
                    <div style='text-align: center; margin-top: 2px; padding-top: 2px; border-top: 1px solid #e5e7eb;'>
                        <a href='https://{dd_site}/apm/service/{html.escape(service)}?env={html.escape(env)}' target='_blank' 
                           style='display: inline-block; padding: 3px 6px; background-color: #7c3aed; color: white; 
                                  text-decoration: none; border-radius: 2px; font-size: 11px; font-weight: 600;'>
                            View Service →
                        </a>
                    </div>
                </div>
                """
        
        output += "</div></div>"  # Close grid and container
        
        # Add chart scripts
        if chart_scripts:
            output += """
            <script>
            setTimeout(function() {
                if (typeof Chart === 'undefined') {
                    console.error('Chart.js not found');
                    return;
                }
            """
            output += "\n".join(chart_scripts)
            output += """
            }, 300);
            </script>
            """
        
        return output
        
    except Exception as e:
        print(f"❌ Error reading ADT dashboard: {str(e)}")
        import traceback
        traceback.print_exc()
        return f"<p>❌ Error reading ADT dashboard: {html.escape(str(e))}</p>"


def read_datadog_errors_only(query: str = "", timerange_hours: int = 4) -> str:
    """
    Read Datadog dashboards and show ONLY widgets with errors > 0
    Similar to read_datadog_dashboards but filters for error conditions
    Args:
        query: Service name to filter
        timerange_hours: Number of hours to look back (default: 4)
    """
    import time
    import json
    
    dd_api_key = os.getenv("DATADOG_API_KEY")
    dd_app_key = os.getenv("DATADOG_APP_KEY")
    dd_site = os.getenv("DATADOG_SITE", "datadoghq.com")
    
    if not dd_api_key or not dd_app_key:
        return "<p>❌ Error: DATADOG_API_KEY or DATADOG_APP_KEY not configured in .env</p>"
    
    try:
        output = "<div class='datadog-results'>"
        
        # Always use the RED - Metrics dashboard
        default_red_dashboard_id = "mpd-2aw-sfe"
        service_filter = query.strip().lower() if query else None
        
        if service_filter:
            print(f"🔍 Filtering RED dashboard for services with errors: {service_filter}")
        else:
            print(f"🚨 Showing all services with errors > 0 (last {timerange_hours} hours)")
        
        # Get dashboard details
        details = get_dashboard_details(dd_api_key, dd_app_key, dd_site, default_red_dashboard_id)
        
        if not details or 'widgets' not in details:
            return "<p>❌ Could not fetch dashboard details</p>"
        
        dash_id = default_red_dashboard_id
        dash_title = details.get('title', 'RED - Metrics')
        dash_url = f"https://{dd_site}/dashboard/{dash_id}"
        
        # Calculate timestamps for display
        import time
        current_time = int(time.time())
        from_time = current_time - (timerange_hours * 3600)
        timestamp_range_html = format_timestamp_range(from_time, current_time)
        
        # Dashboard header
        output += f"""
        <div style='background: linear-gradient(135deg, #dc2626 0%, #991b1b 100%); 
                    padding: 12px; 
                    border-radius: 6px; 
                    margin: 8px 0;
                    color: white;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
            <h2 style='margin: 0 0 6px 0; color: white; font-size: 16px; font-weight: bold;'>🚨 Services with Errors</h2>
            <p style='margin: 0 0 4px 0; font-size: 12px; opacity: 0.95;'>
                Services with active errors from RED Metrics dashboard
            </p>
            <p style='margin: 0 0 8px 0;'>
                <a href='{html.escape(dash_url)}' target='_blank' style='color: white; text-decoration: underline; font-size: 11px; opacity: 0.9;'>
                    Open Interactive Dashboard →
                </a>
            </p>
            {timestamp_range_html}
        </div>
        """
        
        if service_filter:
            output += f"""
            <div style='margin: 8px 0; padding: 6px; background-color: #fff3cd; border-left: 3px solid #ffc107; border-radius: 4px;'>
                <p style='margin: 0; font-size: 12px; color: #856404;'>
                    🔍 <strong>Filtering for service:</strong> {html.escape(service_filter)}
                </p>
            </div>
            """
        
        timerange_text = format_timerange(timerange_hours)
        output += f"""
        <div style='margin: 8px 0; padding: 6px; background-color: #fee2e2; border-left: 3px solid #dc2626; border-radius: 4px;'>
            <h4 style='margin: 0 0 4px 0; color: #991b1b; font-size: 14px;'>🚨 Error Widgets</h4>
            <p style='margin: 0; font-size: 11px; color: #666;'>
                Showing only widgets with <strong>errors > 0</strong> from the {timerange_text}.
            </p>
        </div>
        """
        
        # Extract widgets
        widgets = details.get('widgets', [])
        expanded_widgets = []
        for widget in widgets:
            widget_def = widget.get('definition', {})
            widget_type = widget_def.get('type', '')
            
            if widget_type == 'group':
                group_widgets = widget_def.get('widgets', [])
                expanded_widgets.extend(group_widgets)
            else:
                expanded_widgets.append(widget)
        
        # Filter by service if specified
        if service_filter:
            filtered_widgets = []
            for widget in expanded_widgets:
                widget_def = widget.get('definition', {})
                widget_type = widget_def.get('type', 'unknown')
                widget_title = widget_def.get('title', '').lower()
                
                if widget_type == 'trace_service':
                    service_name = widget_def.get('service', '').lower()
                    if (service_filter in service_name or 
                        service_name in service_filter or
                        service_filter in widget_title or
                        service_filter.replace('-', '_') in service_name or
                        service_filter.replace('_', '-') in service_name):
                        filtered_widgets.append(widget)
                elif service_filter in widget_title:
                    filtered_widgets.append(widget)
            
            widgets_to_check = filtered_widgets[:50]
        else:
            widgets_to_check = expanded_widgets[:50]
        
        # OPTIMIZATION: Three-phase processing for parallel API calls
        current_time = int(time.time())
        from_time = current_time - (timerange_hours * 3600)
        
        widgets_with_errors = []
        chart_scripts = []
        
        # Phase 1: Collect all queries
        print(f"🚀 Errors Phase 1: Collecting queries for {len(widgets_to_check)} widgets...")
        all_queries = {}
        widget_metadata = []
        
        for widget in widgets_to_check:
            widget_def = widget.get('definition', {})
            widget_type = widget_def.get('type', 'unknown')
            
            if widget_type != 'trace_service':
                continue
            
            service = widget_def.get('service', 'Unknown')
            env = widget_def.get('env', 'production')
            
            queries_keys = {
                'requests': f"{service}_{env}_requests",
                'errors': f"{service}_{env}_errors",
                'latency_avg': f"{service}_{env}_latency_avg",
                'latency_min': f"{service}_{env}_latency_min",
                'latency_max': f"{service}_{env}_latency_max"
            }
            
            all_queries[queries_keys['requests']] = f"trace.servlet.request.hits{{service:{service},env:{env}}}.as_count()"
            all_queries[queries_keys['errors']] = f"trace.servlet.request.errors{{service:{service},env:{env}}}.as_count()"
            all_queries[queries_keys['latency_avg']] = f"avg:trace.servlet.request.duration{{service:{service},env:{env}}}"
            all_queries[queries_keys['latency_min']] = f"min:trace.servlet.request.duration{{service:{service},env:{env}}}"
            all_queries[queries_keys['latency_max']] = f"max:trace.servlet.request.duration{{service:{service},env:{env}}}"
            
            widget_metadata.append({'widget': widget, 'queries_keys': queries_keys})
        
        # Phase 2: Execute all queries in parallel
        print(f"🚀 Errors Phase 2: Executing {len(all_queries)} queries in parallel...")
        parallel_start = time.time()
        all_results = get_metrics_parallel(dd_api_key, dd_app_key, dd_site, all_queries, from_time, current_time, max_workers=15)
        print(f"✅ Errors parallel execution: {time.time() - parallel_start:.2f}s")
        
        # Phase 3: Filter and render widgets with errors
        print(f"🚀 Errors Phase 3: Filtering widgets with errors...")
        
        for meta in widget_metadata:
            widget = meta['widget']
            queries_keys = meta['queries_keys']
            
            widget_def = widget.get('definition', {})
            widget_type = widget_def.get('type', 'unknown')
            widget_title = widget_def.get('title', 'Untitled Widget')
            
            service = widget_def.get('service', 'Unknown')
            env = widget_def.get('env', 'production')
            
            metrics_data = {}
            chart_data = {}
            
            # Get requests data from pre-fetched results
            requests_response = all_results.get(queries_keys['requests'])
            if requests_response and 'series' in requests_response and len(requests_response['series']) > 0:
                series = requests_response['series'][0]
                if 'pointlist' in series and len(series['pointlist']) > 0:
                    last_value = series['pointlist'][-1][1] if len(series['pointlist'][-1]) > 1 else 0
                    metrics_data['requests'] = f"{last_value:.1f} req/s" if last_value else "0.0 req/s"
                    chart_data['requests'] = {
                        'labels': [int(p[0]) for p in series['pointlist']],
                        'values': [p[1] if len(p) > 1 and p[1] is not None else 0 for p in series['pointlist']]
                    }
                else:
                    metrics_data['requests'] = "0.0 req/s"
                    chart_data['requests'] = {'labels': [], 'values': []}
            else:
                metrics_data['requests'] = "0.0 req/s"
                chart_data['requests'] = {'labels': [], 'values': []}
            
            # Get errors data from pre-fetched results
            errors_response = all_results.get(queries_keys['errors'])
            
            has_errors = False
            error_count = 0
            if errors_response and 'series' in errors_response and len(errors_response['series']) > 0:
                series = errors_response['series'][0]
                if 'pointlist' in series and len(series['pointlist']) > 0:
                    error_count = series['pointlist'][-1][1] if len(series['pointlist'][-1]) > 1 else 0
                    if error_count and error_count > 0:
                        has_errors = True
                    chart_data['errors'] = {
                        'labels': [int(p[0]) for p in series['pointlist']],
                        'values': [p[1] if len(p) > 1 and p[1] is not None else 0 for p in series['pointlist']]
                    }
                else:
                    chart_data['errors'] = {'labels': [], 'values': []}
            else:
                chart_data['errors'] = {'labels': [], 'values': []}
            
            # Calculate error percentage
            total_requests = 0
            if chart_data.get('requests', {}).get('values'):
                total_requests = sum(chart_data['requests']['values'])
            
            if total_requests > 0 and error_count > 0:
                error_percentage = (error_count / total_requests) * 100
                if error_percentage < 0.1:
                    metrics_data['errors'] = f"{error_count:.0f} (< 0.1%)"
                elif error_percentage < 1:
                    metrics_data['errors'] = f"{error_count:.0f} ({error_percentage:.1f}%)"
                else:
                    metrics_data['errors'] = f"{error_count:.0f} ({error_percentage:.1f}%)"
            elif error_count > 0:
                metrics_data['errors'] = f"{error_count:.0f}"
            else:
                metrics_data['errors'] = "0 (0%)"
            
            # Get latency data from pre-fetched results
            latency_metrics = {
                'avg': {'label': 'Average', 'color': '#4299e1', 'bg': 'rgba(66, 153, 225, 0.6)', 'key': 'latency_avg'},
                'min': {'label': 'Minimum', 'color': '#48bb78', 'bg': 'rgba(72, 187, 120, 0.6)', 'key': 'latency_min'},
                'max': {'label': 'Maximum', 'color': '#f6ad55', 'bg': 'rgba(246, 173, 85, 0.6)', 'key': 'latency_max'}
            }
            chart_data['latency'] = {'labels': [], 'datasets': []}
            latency_values = {}
            
            for metric, config in latency_metrics.items():
                result_key = queries_keys[config['key']]
                latency_response = all_results.get(result_key) if result_key in all_results else None
                
                if latency_response and 'series' in latency_response and len(latency_response['series']) > 0 and latency_response.get('status') != 'error':
                    series = latency_response['series'][0]
                    if 'pointlist' in series and len(series['pointlist']) > 0:
                        # Store labels from first successful query
                        if not chart_data['latency']['labels']:
                            chart_data['latency']['labels'] = [int(p[0]) for p in series['pointlist']]
                        
                        # Extract values and convert to ms if needed
                        values = []
                        last_value = 0
                        for p in series['pointlist']:
                            if len(p) > 1 and p[1] is not None:
                                val = p[1]
                                # Convert to ms if in seconds
                                if val < 10:
                                    val = val * 1000
                                values.append(val)
                                last_value = val
                            else:
                                values.append(0)
                        
                        latency_values[metric] = last_value
                        
                        # Add dataset for this metric
                        chart_data['latency']['datasets'].append({
                            'label': config['label'],
                            'data': values,
                            'borderColor': config['color'],
                            'backgroundColor': config['bg']
                        })
            
            # Display average latency as main value
            if 'avg' in latency_values:
                metrics_data['latency'] = f"{latency_values['avg']:.1f}ms avg"
            else:
                metrics_data['latency'] = "0ms avg"
            
            # Only add widgets with errors > 0
            if has_errors:
                widgets_with_errors.append({
                    'widget': widget,
                    'service': service,
                    'env': env,
                    'widget_title': widget_title,
                    'metrics_data': metrics_data,
                    'chart_data': chart_data
                })
        
        print(f"🚨 Found {len(widgets_with_errors)} widgets with errors > 0")
        
        if len(widgets_with_errors) == 0:
            timerange_text = format_timerange(timerange_hours)
            output += f"""
            <div style='margin: 20px 0; padding: 20px; background-color: #d4edda; border: 1px solid #28a745; border-radius: 4px; text-align: center;'>
                <h3 style='margin: 0 0 8px 0; color: #155724; font-size: 16px;'>✅ No Errors Found!</h3>
                <p style='margin: 0; font-size: 13px; color: #155724;'>All services are running without errors in the {timerange_text}.</p>
            </div>
            """
        else:
            # Display widgets with errors
            output += f"""
            <div style='background-color: #ffffff; padding: 6px; border-radius: 4px; margin: 6px 0; box-shadow: 0 1px 2px rgba(0,0,0,0.06);'>
                <div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; padding-bottom: 4px; border-bottom: 1px solid #dc2626;'>
                    <h3 style='margin: 0; color: #dc2626; font-size: 14px;'>🚨 Services with Errors ({len(widgets_with_errors)})</h3>
                    <div style='text-align: right;'>
                        <div style='font-size: 11px; color: #666;'>{time.strftime('%H:%M:%S')}</div>
                    </div>
                </div>
                <div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px;'>
            """
            
            for widget_info in widgets_with_errors:
                service = widget_info['service']
                env = widget_info['env']
                widget_title = widget_info['widget_title']
                metrics_data = widget_info['metrics_data']
                chart_data = widget_info['chart_data']
                
                chart_id = f"chart_{service.replace('-', '_').replace('.', '_')}_{env}"
                chart_requests_id = f"{chart_id}_requests"
                chart_errors_id = f"{chart_id}_errors"
                chart_latency_id = f"{chart_id}_latency"
                
                output += f"""
                <div style='background-color: #f7fafc; 
                            padding: 4px; 
                            border-radius: 3px; 
                            border: 1px solid #e2e8f0; 
                            box-shadow: 0 1px 2px rgba(0,0,0,0.04);
                            border-left: 3px solid #dc2626;'>
                    <div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 3px;'>
                        <h4 style='margin: 0; color: #2d3748; font-size: 13px; font-weight: 600;'>
                            🚨 {html.escape(widget_title)}
                        </h4>
                        <span style='font-size: 10px; color: #718096; background-color: #fee2e2; padding: 1px 3px; border-radius: 2px;'>
                            ERROR
                        </span>
                    </div>
                    <div style='padding: 2px; 
                                border-radius: 2px; 
                                background-color: rgba(220, 38, 38, 0.05);
                                max-width: 100%;'>
                        <div style='margin-bottom: 2px;'>
                            <span style='font-size: 12px; font-weight: bold; color: #2d3748;'>
                                {html.escape(service)}
                            </span>
                            <span style='font-size: 10px; color: #718096; margin-left: 2px;'>
                                #{html.escape(env)}
                            </span>
                        </div>
                        <div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 3px;'>
                            <!-- Requests -->
                            <div style='background: white; padding: 2px; border-radius: 2px; border: 1px solid #e5e7eb;'>
                                <div style='margin-bottom: 1px;'>
                                    <div style='font-size: 10px; color: #6b7280;'>Requests</div>
                                    <div style='font-size: 13px; font-weight: bold; color: #1890ff;'>{html.escape(metrics_data.get('requests', 'N/A'))}</div>
                                </div>
                                <div style='height: 110px; position: relative; background: #f9f9f9;'>
                                    <canvas id='{chart_requests_id}' width='100' height='110'></canvas>
                                </div>
                            </div>
                            <!-- Errors -->
                            <div style='background: white; padding: 2px; border-radius: 2px; border: 1px solid #fecaca;'>
                                <div style='margin-bottom: 1px;'>
                                    <div style='font-size: 10px; color: #991b1b;'>Errors</div>
                                    <div style='font-size: 13px; font-weight: bold; color: #dc2626;'>{html.escape(metrics_data.get('errors', 'N/A'))}</div>
                                </div>
                                <div style='height: 110px; position: relative; background: #fef2f2;'>
                                    <canvas id='{chart_errors_id}' width='100' height='110'></canvas>
                                </div>
                            </div>
                            <!-- Latency -->
                            <div style='background: white; padding: 2px; border-radius: 2px; border: 1px solid #e5e7eb;'>
                                <div style='margin-bottom: 1px;'>
                                    <div style='font-size: 10px; color: #6b7280;'>Latency</div>
                                    <div style='font-size: 13px; font-weight: bold; color: #52c41a;'>{html.escape(metrics_data.get('latency', 'N/A'))}</div>
                                </div>
                                <div style='height: 110px; position: relative; background: #f9f9f9;'>
                                    <canvas id='{chart_latency_id}' width='100' height='110'></canvas>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div style='text-align: center; margin-top: 2px; padding-top: 2px; border-top: 1px solid #e5e7eb;'>
                        <a href='https://{dd_site}/apm/service/{html.escape(service)}?env={html.escape(env)}' target='_blank' 
                           style='display: inline-block; padding: 3px 6px; background-color: #dc2626; color: white; 
                                  text-decoration: none; border-radius: 2px; font-size: 11px; font-weight: 600;'>
                            View Service →
                        </a>
                    </div>
                </div>
                """
                
                # Add chart scripts for all three metrics
                # Requests
                requests_data = chart_data.get('requests', {'labels': [], 'values': []})
                if not requests_data.get('labels'):
                    requests_data = {'labels': [''], 'values': [0]}
                chart_scripts.append(f"""
                    (function() {{
                        const ctx = document.getElementById('{chart_requests_id}');
                        if (!ctx) return;
                        const data = {json.dumps(requests_data)};
                        try {{
                            const chartLabels = data.labels.length > 0 && data.labels[0] !== '' 
                                ? data.labels.map(t => new Date(t).toLocaleTimeString('en-US', {{hour: '2-digit', minute: '2-digit'}}))
                                : [''];
                            new Chart(ctx, {{
                                type: 'bar',
                                data: {{
                                    labels: chartLabels,
                                    datasets: [{{
                                        label: 'Hits',
                                        data: data.values,
                                        backgroundColor: 'rgba(24, 144, 255, 0.7)',
                                        borderColor: '#1890ff',
                                        borderWidth: 1
                                    }}]
                                }},
                                options: {{
                                    responsive: true,
                                    maintainAspectRatio: false,
                                    plugins: {{ 
                                        legend: {{ 
                                            display: true,
                                            position: 'bottom',
                                            labels: {{
                                                boxWidth: 10,
                                                font: {{ size: 8 }},
                                                padding: 4
                                            }}
                                        }} 
                                    }},
                                    scales: {{
                                        x: {{ display: false }},
                                        y: {{ 
                                            beginAtZero: true, 
                                            display: true,
                                            ticks: {{ 
                                                font: {{ size: 8 }}
                                            }}
                                        }}
                                    }}
                                }}
                            }});
                        }} catch(e) {{
                            console.error('Chart error:', e.message);
                        }}
                    }})();
                """)
                
                # Errors
                errors_data = chart_data.get('errors', {'labels': [], 'values': []})
                if not errors_data.get('labels'):
                    errors_data = {'labels': [''], 'values': [0]}
                chart_scripts.append(f"""
                    (function() {{
                        const ctx = document.getElementById('{chart_errors_id}');
                        if (!ctx) return;
                        const data = {json.dumps(errors_data)};
                        try {{
                            const chartLabels = data.labels.length > 0 && data.labels[0] !== '' 
                                ? data.labels.map(t => new Date(t).toLocaleTimeString('en-US', {{hour: '2-digit', minute: '2-digit'}}))
                                : [''];
                            new Chart(ctx, {{
                                type: 'bar',
                                data: {{
                                    labels: chartLabels,
                                    datasets: [{{
                                        label: 'Errors',
                                        data: data.values,
                                        backgroundColor: 'rgba(220, 38, 38, 0.7)',
                                        borderColor: '#dc2626',
                                        borderWidth: 2
                                    }}]
                                }},
                                options: {{
                                    responsive: true,
                                    maintainAspectRatio: false,
                                    plugins: {{ 
                                        legend: {{ 
                                            display: true,
                                            position: 'bottom',
                                            labels: {{
                                                boxWidth: 10,
                                                font: {{ size: 8 }},
                                                padding: 4
                                            }}
                                        }} 
                                    }},
                                    scales: {{
                                        x: {{ display: false }},
                                        y: {{ 
                                            beginAtZero: true, 
                                            display: true,
                                            ticks: {{ 
                                                font: {{ size: 8 }}
                                            }}
                                        }}
                                    }}
                                }}
                            }});
                        }} catch(e) {{
                            console.error('Chart error:', e.message);
                        }}
                    }})();
                """)
                
                # Latency
                latency_chart_data = chart_data.get('latency', {'labels': [], 'datasets': []})
                if not latency_chart_data.get('labels'):
                    latency_chart_data = {'labels': [''], 'datasets': [{'label': 'Average', 'data': [0], 'borderColor': '#4299e1', 'backgroundColor': 'rgba(66, 153, 225, 0.6)'}]}
                
                chart_scripts.append(f"""
                    (function() {{
                        const ctx = document.getElementById('{chart_latency_id}');
                        if (!ctx) return;
                        const data = {json.dumps(latency_chart_data)};
                        try {{
                            const chartLabels = data.labels.length > 0 && data.labels[0] !== '' 
                                ? data.labels.map(t => new Date(t).toLocaleTimeString('en-US', {{hour: '2-digit', minute: '2-digit'}}))
                                : [''];
                            
                            new Chart(ctx, {{
                                type: 'bar',
                                data: {{
                                    labels: chartLabels,
                                    datasets: data.datasets.map(ds => ({{
                                        label: ds.label,
                                        data: ds.data,
                                        borderColor: ds.borderColor,
                                        backgroundColor: ds.backgroundColor,
                                        borderWidth: 1
                                    }}))
                                }},
                                options: {{
                                    responsive: true,
                                    maintainAspectRatio: false,
                                    plugins: {{ 
                                        legend: {{ 
                                            display: true,
                                            position: 'bottom',
                                            labels: {{
                                                boxWidth: 8,
                                                font: {{ size: 8 }},
                                                padding: 3
                                            }}
                                        }} 
                                    }},
                                    scales: {{
                                        x: {{ display: false }},
                                        y: {{ 
                                            beginAtZero: true, 
                                            display: true, 
                                            ticks: {{ 
                                                font: {{ size: 8 }},
                                                callback: function(value) {{
                                                    return value.toFixed(0) + 'ms';
                                                }}
                                            }}
                                        }}
                                    }}
                                }}
                            }});
                        }} catch(e) {{
                            console.error('Latency chart error:', e.message);
                        }}
                    }})();
                """)
            
            output += "</div></div>"  # Close grid and container
        
        # Add chart scripts
        if chart_scripts:
            output += """
            <script>
            setTimeout(function() {
                if (typeof Chart === 'undefined') {
                    console.error('Chart.js not found');
                    return;
                }
            """
            output += "\n".join(chart_scripts)
            output += """
            }, 300);
            </script>
            """
        
        output += "</div>"
        return output
        
    except Exception as e:
        return f"<p>❌ Unexpected error: {html.escape(str(e))}</p>"


def read_datadog_adt_errors_only(query: str = "", timerange_hours: int = 4) -> str:
    """
    Shows only services from RED Metrics - ADT dashboard with errors > 0.
    If a service name is provided, filters widgets for that specific service.
    Args:
        query: Service name to filter
        timerange_hours: Number of hours to look back (default: 4)
    """
    print("=" * 80)
    print("🚨 Reading Datadog ADT Errors Only")
    print(f"📝 Query received: '{query}'")
    print(f"📝 Time range: {timerange_hours} hours")
    
    # Get Datadog credentials from environment
    dd_api_key = os.getenv("DATADOG_API_KEY")
    dd_app_key = os.getenv("DATADOG_APP_KEY")
    dd_site = os.getenv("DATADOG_SITE", "datadoghq.com")
    
    # Default RED - Metrics - ADT dashboard ID
    default_adt_dashboard_id = "cum-ivw-92c"  # RED Metrics - partnerprod (ADT)
    service_filter = None
    
    # If query provided, use it as service filter
    if query and query.strip():
        service_filter = query.strip()
        print(f"🔍 Filtering ADT errors for service: {service_filter}")
    else:
        print(f"🚨 Showing all ADT services with errors > 0 (last {timerange_hours} hours)")
    
    if not dd_api_key or not dd_app_key:
        return "<p>❌ Datadog API keys not configured. Please set DATADOG_API_KEY and DATADOG_APP_KEY in your .env file.</p>"
    
    output = ""
    
    try:
        # Get dashboard details
        details = get_dashboard_details(dd_api_key, dd_app_key, dd_site, default_adt_dashboard_id)
        
        if not details or 'widgets' not in details:
            return "<p>❌ Could not fetch ADT dashboard details</p>"
        
        dash_id = default_adt_dashboard_id
        dash_title = details.get('title', 'RED - Metrics - ADT')
        dash_url = f"https://{dd_site}/dashboard/{dash_id}"
        
        # Calculate timestamps for display
        import time
        current_time = int(time.time())
        from_time = current_time - (timerange_hours * 3600)
        timestamp_range_html = format_timestamp_range(from_time, current_time)
        
        # Dashboard header
        output += f"""
        <div style='background: linear-gradient(135deg, #7c3aed 0%, #5b21b6 100%); 
                    padding: 12px; 
                    border-radius: 6px; 
                    margin: 0 0 8px 0;
                    color: white;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
            <h2 style='margin: 0 0 6px 0; color: white; font-size: 16px; font-weight: bold;'>🚨 ADT Services with Errors</h2>
            <p style='margin: 0 0 4px 0; font-size: 12px; opacity: 0.95;'>
                Services with active errors from ADT dashboard
            </p>
            <p style='margin: 0 0 8px 0;'>
                <a href='{html.escape(dash_url)}' target='_blank' style='color: white; text-decoration: underline; font-size: 11px; opacity: 0.9;'>
                    Open Interactive Dashboard →
                </a>
            </p>
            {timestamp_range_html}
        </div>
        """
        
        if service_filter:
            output += f"""
            <div style='margin: 8px 0; padding: 6px; background-color: #fff3cd; border-left: 3px solid #ffc107; border-radius: 4px;'>
                <p style='margin: 0; font-size: 12px; color: #856404;'>
                    🔍 <strong>Filtering for service:</strong> {html.escape(service_filter)}
                </p>
            </div>
            """
        
        timerange_text = format_timerange(timerange_hours)
        output += f"""
        <div style='margin: 8px 0; padding: 6px; background-color: #fee2e2; border-left: 3px solid #dc2626; border-radius: 4px;'>
            <h4 style='margin: 0 0 4px 0; color: #991b1b; font-size: 14px;'>🚨 ADT Error Widgets</h4>
            <p style='margin: 0; font-size: 11px; color: #666;'>
                Showing only ADT widgets with <strong>errors > 0</strong> from the {timerange_text}.
            </p>
        </div>
        """
        
        # Extract widgets
        widgets = details.get('widgets', [])
        expanded_widgets = []
        for widget in widgets:
            widget_def = widget.get('definition', {})
            widget_type = widget_def.get('type', '')
            
            if widget_type == 'group':
                group_widgets = widget_def.get('widgets', [])
                expanded_widgets.extend(group_widgets)
            else:
                expanded_widgets.append(widget)
        
        # Filter by service if specified
        if service_filter:
            filtered_widgets = []
            for widget in expanded_widgets:
                widget_def = widget.get('definition', {})
                widget_type = widget_def.get('type', 'unknown')
                widget_title = widget_def.get('title', '').lower()
                
                if widget_type == 'trace_service':
                    service_name = widget_def.get('service', '').lower()
                    if (service_filter in service_name or 
                        service_name in service_filter or
                        service_filter in widget_title or
                        service_filter.replace('-', '_') in service_name or
                        service_filter.replace('_', '-') in service_name):
                        filtered_widgets.append(widget)
                elif service_filter in widget_title:
                    filtered_widgets.append(widget)
            
            widgets_to_check = filtered_widgets[:50]
        else:
            widgets_to_check = expanded_widgets[:50]
        
        # OPTIMIZATION: Three-phase processing for parallel API calls
        current_time = int(time.time())
        from_time = current_time - (timerange_hours * 3600)
        
        widgets_with_errors = []
        chart_scripts = []
        
        # Phase 1: Collect all queries
        print(f"🚀 ADT Errors Phase 1: Collecting queries for {len(widgets_to_check)} widgets...")
        all_queries = {}
        widget_metadata = []
        
        for widget in widgets_to_check:
            widget_def = widget.get('definition', {})
            widget_type = widget_def.get('type', 'unknown')
            
            if widget_type != 'trace_service':
                continue
            
            service = widget_def.get('service', 'Unknown')
            env = widget_def.get('env', 'production')
            
            queries_keys = {
                'requests': f"{service}_{env}_requests",
                'errors': f"{service}_{env}_errors",
                'latency_avg': f"{service}_{env}_latency_avg",
                'latency_min': f"{service}_{env}_latency_min",
                'latency_max': f"{service}_{env}_latency_max"
            }
            
            all_queries[queries_keys['requests']] = f"trace.servlet.request.hits{{service:{service},env:{env}}}.as_count()"
            all_queries[queries_keys['errors']] = f"trace.servlet.request.errors{{service:{service},env:{env}}}.as_count()"
            all_queries[queries_keys['latency_avg']] = f"avg:trace.servlet.request.duration{{service:{service},env:{env}}}"
            all_queries[queries_keys['latency_min']] = f"min:trace.servlet.request.duration{{service:{service},env:{env}}}"
            all_queries[queries_keys['latency_max']] = f"max:trace.servlet.request.duration{{service:{service},env:{env}}}"
            
            widget_metadata.append({'widget': widget, 'queries_keys': queries_keys})
        
        # Phase 2: Execute all queries in parallel
        print(f"🚀 ADT Errors Phase 2: Executing {len(all_queries)} queries in parallel...")
        parallel_start = time.time()
        all_results = get_metrics_parallel(dd_api_key, dd_app_key, dd_site, all_queries, from_time, current_time, max_workers=15)
        print(f"✅ ADT Errors parallel execution: {time.time() - parallel_start:.2f}s")
        
        # Phase 3: Filter and render widgets with errors
        print(f"🚀 ADT Errors Phase 3: Filtering widgets with errors...")
        
        for meta in widget_metadata:
            widget = meta['widget']
            queries_keys = meta['queries_keys']
            
            widget_def = widget.get('definition', {})
            widget_type = widget_def.get('type', 'unknown')
            
            service = widget_def.get('service', 'Unknown')
            env = widget_def.get('env', 'production')
            
            # Get errors data from pre-fetched results
            errors_response = all_results.get(queries_keys['errors'])
            
            has_errors = False
            error_count = 0
            errors_chart_data = {'labels': [], 'values': []}
            
            if errors_response and 'series' in errors_response and len(errors_response['series']) > 0:
                series = errors_response['series'][0]
                if 'pointlist' in series and len(series['pointlist']) > 0:
                    error_count = series['pointlist'][-1][1] if len(series['pointlist'][-1]) > 1 else 0
                    if error_count > 0:
                        has_errors = True
                        errors_chart_data = {
                            'labels': [int(p[0]) for p in series['pointlist']],
                            'values': [p[1] if len(p) > 1 else 0 for p in series['pointlist']]
                        }
            
            # Only include widgets with errors > 0
            if not has_errors:
                continue
            
            # Get requests data from pre-fetched results
            requests_response = all_results.get(queries_keys['requests'])
            
            total_requests = 0
            requests_chart_data = {'labels': [], 'values': []}
            
            if requests_response and 'series' in requests_response and len(requests_response['series']) > 0:
                series = requests_response['series'][0]
                if 'pointlist' in series and len(series['pointlist']) > 0:
                    total_requests = series['pointlist'][-1][1] if len(series['pointlist'][-1]) > 1 else 0
                    requests_chart_data = {
                        'labels': [int(p[0]) for p in series['pointlist']],
                        'values': [p[1] if len(p) > 1 else 0 for p in series['pointlist']]
                    }
            
            # Get latency data from pre-fetched results
            latency_metrics = {
                'avg': {'label': 'Average', 'color': '#4299e1', 'bg': 'rgba(66, 153, 225, 0.6)', 'key': 'latency_avg'},
                'min': {'label': 'Minimum', 'color': '#48bb78', 'bg': 'rgba(72, 187, 120, 0.6)', 'key': 'latency_min'},
                'max': {'label': 'Maximum', 'color': '#f6ad55', 'bg': 'rgba(246, 173, 85, 0.6)', 'key': 'latency_max'}
            }
            latency_chart_data = {'labels': [], 'datasets': []}
            latency_values = {}
            
            for metric, config in latency_metrics.items():
                result_key = queries_keys[config['key']]
                latency_response = all_results.get(result_key) if result_key in all_results else None
                
                if latency_response and 'series' in latency_response and len(latency_response['series']) > 0 and latency_response.get('status') != 'error':
                    series = latency_response['series'][0]
                    if 'pointlist' in series and len(series['pointlist']) > 0:
                        if not latency_chart_data['labels']:
                            latency_chart_data['labels'] = [int(p[0]) for p in series['pointlist']]
                        
                        values = []
                        last_value = 0
                        for p in series['pointlist']:
                            if len(p) > 1 and p[1] is not None:
                                val = p[1]
                                if val < 10:
                                    val = val * 1000
                                values.append(val)
                                last_value = val
                            else:
                                values.append(0)
                        
                        latency_values[metric] = last_value
                        latency_chart_data['datasets'].append({
                            'label': config['label'],
                            'data': values,
                            'borderColor': config['color'],
                            'backgroundColor': config['bg']
                        })
            
            # Calculate error percentage
            error_percentage_str = ""
            if total_requests > 0 and error_count > 0:
                error_percentage = (error_count / total_requests) * 100
                if error_percentage < 0.1:
                    error_percentage_str = f"{error_count:.0f} (< 0.1%)"
                else:
                    error_percentage_str = f"{error_count:.0f} ({error_percentage:.1f}%)"
            elif error_count > 0:
                error_percentage_str = f"{error_count:.0f}"
            else:
                error_percentage_str = "0 (0%)"
            
            # Build widget HTML
            chart_id = f"adt_err_chart_{service.replace('-', '_')}_{len(widgets_with_errors)}"
            
            widget_html = f"""
            <div style='background-color: #f9fafb; 
                        padding: 3px; 
                        border-radius: 2px; 
                        border-left: 1px solid #dc2626;
                        box-shadow: 0 1px 2px rgba(0,0,0,0.04);
                        max-width: 100%;'>
                <div style='margin-bottom: 2px;'>
                    <span style='font-size: 12px; font-weight: bold; color: #2d3748;'>
                        {html.escape(service)}
                    </span>
                    <span style='font-size: 10px; color: #718096; margin-left: 2px;'>
                        #{html.escape(env)}
                    </span>
                </div>
                <div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 2px;'>
            """
            
            # Requests chart
            chart_requests_id = f"{chart_id}_requests"
            widget_html += f"""
            <div style='background: white; padding: 2px; border-radius: 2px; border: 1px solid #e5e7eb;'>
                <div style='margin-bottom: 1px;'>
                    <div style='font-size: 10px; color: #6b7280;'>Requests</div>
                    <div style='font-size: 13px; font-weight: bold; color: #1890ff;'>{total_requests:.1f} req/s</div>
                </div>
                <div style='height: 110px; position: relative; background: #f9f9f9;'>
                    <canvas id='{chart_requests_id}' width='100' height='110'></canvas>
                </div>
            </div>
            """
            
            if not requests_chart_data.get('labels'):
                requests_chart_data = {'labels': [''], 'values': [0]}
            
            chart_scripts.append(f"""
                (function() {{
                    const ctx = document.getElementById('{chart_requests_id}');
                    if (!ctx) return;
                    const data = {json.dumps(requests_chart_data)};
                    try {{
                        const chartLabels = data.labels.length > 0 && data.labels[0] !== '' 
                            ? data.labels.map(t => new Date(t).toLocaleTimeString('en-US', {{hour: '2-digit', minute: '2-digit'}}))
                            : [''];
                        new Chart(ctx, {{
                            type: 'bar',
                            data: {{
                                labels: chartLabels,
                                datasets: [{{
                                    label: 'Hits',
                                    data: data.values,
                                    backgroundColor: 'rgba(24, 144, 255, 0.7)',
                                    borderColor: '#1890ff',
                                    borderWidth: 1
                                }}]
                            }},
                            options: {{
                                responsive: true,
                                maintainAspectRatio: false,
                                plugins: {{ 
                                    legend: {{ 
                                        display: true,
                                        position: 'bottom',
                                        labels: {{
                                            boxWidth: 10,
                                            font: {{ size: 8 }},
                                            padding: 4
                                        }}
                                    }} 
                                }},
                                scales: {{
                                    x: {{ display: false }},
                                    y: {{ 
                                        beginAtZero: true, 
                                        display: true,
                                        ticks: {{ 
                                            font: {{ size: 8 }}
                                        }}
                                    }}
                                }}
                            }}
                        }});
                    }} catch(e) {{
                        console.error('Chart error:', e.message);
                    }}
                }})();
            """)
            
            # Errors chart
            chart_errors_id = f"{chart_id}_errors"
            widget_html += f"""
            <div style='background: white; padding: 2px; border-radius: 2px; border: 1px solid #e5e7eb;'>
                <div style='margin-bottom: 1px;'>
                    <div style='font-size: 10px; color: #6b7280;'>Errors</div>
                    <div style='font-size: 13px; font-weight: bold; color: #ff4d4f;'>{html.escape(error_percentage_str)}</div>
                </div>
                <div style='height: 110px; position: relative; background: #f9f9f9;'>
                    <canvas id='{chart_errors_id}' width='100' height='110'></canvas>
                </div>
            </div>
            """
            
            if not errors_chart_data.get('labels'):
                errors_chart_data = {'labels': [''], 'values': [0]}
            
            chart_scripts.append(f"""
                (function() {{
                    const ctx = document.getElementById('{chart_errors_id}');
                    if (!ctx) return;
                    const data = {json.dumps(errors_chart_data)};
                    try {{
                        const chartLabels = data.labels.length > 0 && data.labels[0] !== '' 
                            ? data.labels.map(t => new Date(t).toLocaleTimeString('en-US', {{hour: '2-digit', minute: '2-digit'}}))
                            : [''];
                        new Chart(ctx, {{
                            type: 'bar',
                            data: {{
                                labels: chartLabels,
                                datasets: [{{
                                    label: 'Errors',
                                    data: data.values,
                                    backgroundColor: 'rgba(255, 77, 79, 0.7)',
                                    borderColor: '#ff4d4f',
                                    borderWidth: 1
                                }}]
                            }},
                            options: {{
                                responsive: true,
                                maintainAspectRatio: false,
                                plugins: {{ 
                                    legend: {{ 
                                        display: true,
                                        position: 'bottom',
                                        labels: {{
                                            boxWidth: 10,
                                            font: {{ size: 8 }},
                                            padding: 4
                                        }}
                                    }} 
                                }},
                                scales: {{
                                    x: {{ display: false }},
                                    y: {{ 
                                        beginAtZero: true, 
                                        display: true,
                                        ticks: {{ 
                                            font: {{ size: 8 }}
                                        }}
                                    }}
                                }}
                            }}
                        }});
                    }} catch(e) {{
                        console.error('Chart error:', e.message);
                    }}
                }})();
            """)
            
            # Latency chart
            latency_avg = latency_values.get('avg', 0)
            chart_latency_id = f"{chart_id}_latency"
            widget_html += f"""
            <div style='background: white; padding: 2px; border-radius: 2px; border: 1px solid #e5e7eb;'>
                <div style='margin-bottom: 1px;'>
                    <div style='font-size: 10px; color: #6b7280;'>Latency</div>
                    <div style='font-size: 13px; font-weight: bold; color: #52c41a;'>{latency_avg:.1f}ms avg</div>
                </div>
                <div style='height: 110px; position: relative; background: #f9f9f9;'>
                    <canvas id='{chart_latency_id}' width='100' height='110'></canvas>
                </div>
            </div>
            """
            
            if not latency_chart_data.get('labels'):
                latency_chart_data = {'labels': [''], 'datasets': [{'label': 'Average', 'data': [0], 'borderColor': '#4299e1', 'backgroundColor': 'rgba(66, 153, 225, 0.6)'}]}
            
            chart_scripts.append(f"""
                (function() {{
                    const ctx = document.getElementById('{chart_latency_id}');
                    if (!ctx) return;
                    const data = {json.dumps(latency_chart_data)};
                    try {{
                        const chartLabels = data.labels.length > 0 && data.labels[0] !== '' 
                            ? data.labels.map(t => new Date(t).toLocaleTimeString('en-US', {{hour: '2-digit', minute: '2-digit'}}))
                            : [''];
                        
                        new Chart(ctx, {{
                            type: 'bar',
                            data: {{
                                labels: chartLabels,
                                datasets: data.datasets.map(ds => ({{
                                    label: ds.label,
                                    data: ds.data,
                                    borderColor: ds.borderColor,
                                    backgroundColor: ds.backgroundColor,
                                    borderWidth: 1
                                }}))
                            }},
                            options: {{
                                responsive: true,
                                maintainAspectRatio: false,
                                plugins: {{ 
                                    legend: {{ 
                                        display: true,
                                        position: 'bottom',
                                        labels: {{
                                            boxWidth: 8,
                                            font: {{ size: 8 }},
                                            padding: 3
                                        }}
                                    }} 
                                }},
                                scales: {{
                                    x: {{ display: false }},
                                    y: {{ 
                                        beginAtZero: true, 
                                        display: true, 
                                        ticks: {{ 
                                            font: {{ size: 8 }},
                                            callback: function(value) {{
                                                return value.toFixed(0) + 'ms';
                                            }}
                                        }} 
                                    }}
                                }}
                            }}
                        }});
                    }} catch(e) {{
                        console.error('Latency chart error:', e.message);
                    }}
                }})();
            """)
            
            widget_html += f"""
                </div>
                <div style='text-align: center; margin-top: 2px; padding-top: 2px; border-top: 1px solid #e5e7eb;'>
                    <a href='https://{dd_site}/apm/service/{html.escape(service)}?env={html.escape(env)}' target='_blank' 
                       style='display: inline-block; padding: 3px 6px; background-color: #dc2626; color: white; 
                              text-decoration: none; border-radius: 2px; font-size: 11px; font-weight: 600;'>
                        View Service →
                    </a>
                </div>
            </div>
            """
            
            widgets_with_errors.append(widget_html)
        
        # Display results
        if len(widgets_with_errors) == 0:
            output += f"<p style='margin: 8px 0; padding: 6px; background-color: #d4edda; border-left: 3px solid #28a745; border-radius: 4px; color: #155724;'>✅ No ADT services with errors found{' for service: ' + service_filter if service_filter else ''}!</p>"
        else:
            output += f"""
            <div style='background-color: #ffffff; padding: 6px; border-radius: 4px; margin: 6px 0; box-shadow: 0 1px 2px rgba(0,0,0,0.06);'>
                <div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; padding-bottom: 4px; border-bottom: 1px solid #dc2626;'>
                    <h3 style='margin: 0; color: #dc2626; font-size: 14px;'>🚨 ADT Services with Errors ({len(widgets_with_errors)})</h3>
                    <div style='text-align: right;'>
                        <div style='font-size: 11px; color: #666;'>{time.strftime('%H:%M:%S')}</div>
                    </div>
                </div>
                <div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px;'>
            """
            
            output += "\n".join(widgets_with_errors)
            output += "</div></div>"
        
        # Add chart scripts
        if chart_scripts:
            output += """
            <script>
            setTimeout(function() {
                if (typeof Chart === 'undefined') {
                    console.error('Chart.js not found');
                    return;
                }
            """
            output += "\n".join(chart_scripts)
            output += """
            }, 300);
            </script>
            """
        
        return output
        
    except Exception as e:
        print(f"❌ Error reading ADT errors: {str(e)}")
        import traceback
        traceback.print_exc()
        return f"<p>❌ Error reading ADT errors: {html.escape(str(e))}</p>"


def read_datadog_all_errors(query: str = "", timerange_hours: int = 4) -> str:
    """
    Shows services with errors > 0 from BOTH RED Metrics and RED Metrics - ADT dashboards.
    Results are shown in separate sections for easy comparison.
    If a service name is provided, filters widgets for that specific service in both dashboards.
    Args:
        query: Service name to filter
        timerange_hours: Number of hours to look back (default: 4)
    """
    print("=" * 80)
    print("🚨 Reading ALL Datadog Errors (RED + ADT)")
    print(f"📝 Query received: '{query}'")
    print(f"📝 Time range: {timerange_hours} hours")
    
    # Start with wrapper div to contain everything
    output = "<div class='dd-errors-wrapper' style='position: relative;'>"
    
    # Calculate timestamps for display
    import time
    current_time = int(time.time())
    from_time = current_time - (timerange_hours * 3600)
    timestamp_range_html = format_timestamp_range(from_time, current_time)
    
    # Generate unique IDs for subsections to avoid conflicts when multiple tabs exist
    unique_id = str(current_time)
    
    # Add main header
    timerange_text = format_timerange(timerange_hours)
    output += f"""
    <div style='background: linear-gradient(135deg, #dc2626 0%, #7c3aed 100%); 
                padding: 12px; 
                border-radius: 6px; 
                margin: 0 0 8px 0;
                color: white;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
        <h2 style='margin: 0 0 6px 0; color: white; font-size: 16px; font-weight: bold;'>🚨 All Services with Errors</h2>
        <p style='margin: 0 0 4px 0; font-size: 12px; opacity: 0.95;'>
            Showing errors from <strong>RED Metrics</strong> and <strong>RED Metrics - ADT</strong> dashboards
        </p>
        <p style='margin: 0 0 8px 0; font-size: 11px; opacity: 0.9;'>
            📊 Time range: {timerange_text}
            {f" | 🔍 Filter: {html.escape(query)}" if query else ""}
        </p>
        {timestamp_range_html}
    </div>
    """
    
    try:
        # Section 1: RED Metrics Errors
        print("\n" + "=" * 80)
        print("📊 Section 1: Fetching RED Metrics Errors")
        print("=" * 80)
        
        red_errors = read_datadog_errors_only(query, timerange_hours)
        
        # Remove the header sections from red_errors (keep only widget content)
        # Find the start of the "Services with Errors" section which contains the grid
        # This section starts with a specific div that has the count
        red_errors_clean = red_errors
        
        # Remove only the top-level gradient header (title, description, dates)
        # Keep everything from "Services with Errors (count)" onwards
        match = re.search(r'(<div[^>]*>\s*<div style=\'display: flex[^>]*>\s*<h3[^>]*>🚨 Services with Errors)', red_errors_clean, re.DOTALL)
        if match:
            # Keep from this point forward
            red_errors_clean = red_errors_clean[match.start():]
        
        red_subsection_id = f'dd-red-errors-subsection-{unique_id}'
        output += f"""
        <div class='subsection-collapsible' id='{red_subsection_id}' style='margin: 12px 0; border: 1px solid #fecaca; border-radius: 6px; overflow: hidden;'>
            <div class='subsection-header' style='background-color: #fff5f5; border-left: 4px solid #dc2626; padding: 8px; cursor: pointer; display: flex; justify-content: space-between; align-items: center;' onclick='toggleSubsection("{red_subsection_id}")'>
                <h3 style='margin: 0; color: #dc2626; font-size: 15px; font-weight: bold;'>
                    📊 RED Metrics - Errors
                </h3>
                <button class='subsection-toggle-btn' style='background: rgba(220, 38, 38, 0.1); border: none; color: #dc2626; cursor: pointer; padding: 4px 10px; border-radius: 4px; font-size: 12px; font-weight: bold;'>▼</button>
            </div>
            <div class='subsection-content' style='display: block; padding: 10px; background: white;'>
        """
        output += red_errors_clean
        output += """
            </div>
        </div>
        """
        
        # Section 2: ADT Errors
        print("\n" + "=" * 80)
        print("🔮 Section 2: Fetching ADT Errors")
        print("=" * 80)
        
        adt_errors = read_datadog_adt_errors_only(query, timerange_hours)
        
        # Remove the header sections from adt_errors (keep only widget content)
        # Find the start of the "ADT Services with Errors" section which contains the grid
        # This section starts with a specific div that has the count
        adt_errors_clean = adt_errors
        
        # Remove only the top-level gradient header (title, description, dates)
        # Keep everything from "ADT Services with Errors (count)" onwards
        match = re.search(r'(<div[^>]*>\s*<div style=\'display: flex[^>]*>\s*<h3[^>]*>🚨 ADT Services with Errors)', adt_errors_clean, re.DOTALL)
        if match:
            # Keep from this point forward
            adt_errors_clean = adt_errors_clean[match.start():]
        
        adt_subsection_id = f'dd-adt-errors-subsection-{unique_id}'
        output += f"""
        <div class='subsection-collapsible' id='{adt_subsection_id}' style='margin: 20px 0 12px 0; border: 1px solid #e9d5ff; border-radius: 6px; overflow: hidden;'>
            <div class='subsection-header' style='background-color: #faf5ff; border-left: 4px solid #7c3aed; padding: 8px; cursor: pointer; display: flex; justify-content: space-between; align-items: center;' onclick='toggleSubsection("{adt_subsection_id}")'>
                <h3 style='margin: 0; color: #7c3aed; font-size: 15px; font-weight: bold;'>
                    🔮 RED Metrics - ADT - Errors
                </h3>
                <button class='subsection-toggle-btn' style='background: rgba(124, 58, 237, 0.1); border: none; color: #7c3aed; cursor: pointer; padding: 4px 10px; border-radius: 4px; font-size: 12px; font-weight: bold;'>▼</button>
            </div>
            <div class='subsection-content' style='display: block; padding: 10px; background: white;'>
        """
        output += adt_errors_clean
        output += """
            </div>
        </div>
        """
        
        # Close wrapper div
        output += "</div>"
        
        return output
        
    except Exception as e:
        print(f"❌ Error reading all errors: {str(e)}")
        import traceback
        traceback.print_exc()
        return f"<p>❌ Error reading all errors: {html.escape(str(e))}</p>"


def read_datadog_failed_pods(query: str = "", timerange_hours: int = 4) -> str:
    """
    Get Kubernetes pods with failures (ImagePullBackOff, CrashLoopBackOff, etc.)
    that could be causing 4xx errors
    """
    print("=" * 80)
    print("🚨 Datadog Failed Pods Monitor")
    print("=" * 80)
    
    dd_api_key = os.getenv("DATADOG_API_KEY")
    dd_app_key = os.getenv("DATADOG_APP_KEY")
    dd_site = os.getenv("DATADOG_SITE", "datadoghq.com")
    
    if not dd_api_key or not dd_app_key:
        return "<p>⚠️ Datadog credentials not configured</p>"
    
    try:
        # Calculate time range
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=timerange_hours)
        
        from_ts = int(start_time.timestamp())
        to_ts = int(end_time.timestamp())
        
        print(f"📅 Time range: {start_time.strftime('%Y-%m-%d %H:%M')} to {end_time.strftime('%Y-%m-%d %H:%M')}")
        
        # Query for failed pods
        # Using Datadog metrics API to get pod status
        base_url = f"https://{dd_site}/api/v1"
        headers = {
            "DD-API-KEY": dd_api_key,
            "DD-APPLICATION-KEY": dd_app_key,
            "Content-Type": "application/json"
        }
        
        # Query for pods with error states
        pod_queries = [
            "kubernetes.pods.running{pod_status:imagepullbackoff,env:production}",
            "kubernetes.pods.running{pod_status:crashloopbackoff,env:production}",
            "kubernetes.pods.running{pod_status:error,env:production}",
            "kubernetes.pods.running{pod_status:pending,env:production}",
        ]
        
        failed_pods = []
        
        for query_str in pod_queries:
            query_url = f"{base_url}/query"
            params = {
                "from": from_ts,
                "to": to_ts,
                "query": query_str
            }
            
            response = requests.get(query_url, headers=headers, params=params, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('series'):
                    for series in data['series']:
                        pod_name = series.get('scope', 'unknown')
                        status = series.get('tag_set', [])
                        
                        # Extract namespace and pod info from tags
                        namespace = next((tag.split(':')[1] for tag in status if tag.startswith('kube_namespace:')), 'unknown')
                        pod_status = next((tag.split(':')[1] for tag in status if tag.startswith('pod_status:')), 'unknown')
                        
                        failed_pods.append({
                            'namespace': namespace,
                            'pod': pod_name,
                            'status': pod_status,
                            'points': series.get('pointlist', [])
                        })
        
        # Build HTML output
        output = f"""
        <div style='background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%); 
                    padding: 16px; 
                    border-radius: 8px; 
                    margin: 12px 0;
                    color: white;
                    box-shadow: 0 4px 6px rgba(0,0,0,0.1);'>
            <h2 style='margin: 0 0 8px 0; color: white; font-size: 20px; font-weight: bold;'>
                🚨 Failed Pods Monitor - Potential 4xx Error Sources
            </h2>
            <p style='margin: 0; font-size: 13px; opacity: 0.95;'>
                Monitoring Kubernetes pods with failure states that may cause service errors
            </p>
            <div style='margin-top: 8px; padding: 8px; background: rgba(255,255,255,0.15); border-radius: 4px; font-size: 12px;'>
                <strong>📅 Time Range:</strong> {start_time.strftime('%Y-%m-%d %H:%M')} to {end_time.strftime('%Y-%m-%d %H:%M')} ({timerange_hours}h)
            </div>
        </div>
        """
        
        if not failed_pods:
            output += """
            <div style='background-color: #d1fae5; padding: 16px; border-left: 4px solid #10b981; border-radius: 4px; margin: 12px 0;'>
                <p style='margin: 0; color: #065f46; font-weight: bold;'>
                    ✅ No failed pods detected in the specified time range
                </p>
                <p style='margin: 8px 0 0 0; color: #047857; font-size: 13px;'>
                    All pods are running normally in production environment.
                </p>
            </div>
            """
        else:
            # Group by namespace
            pods_by_namespace = {}
            for pod in failed_pods:
                ns = pod['namespace']
                if ns not in pods_by_namespace:
                    pods_by_namespace[ns] = []
                pods_by_namespace[ns].append(pod)
            
            output += f"""
            <div style='background-color: #fee2e2; padding: 12px; border-left: 4px solid #ef4444; border-radius: 4px; margin: 12px 0;'>
                <p style='margin: 0; color: #991b1b; font-weight: bold; font-size: 15px;'>
                    ⚠️ Found {len(failed_pods)} failed pod(s) across {len(pods_by_namespace)} namespace(s)
                </p>
            </div>
            """
            
            # Display pods grouped by namespace
            for namespace, pods in sorted(pods_by_namespace.items()):
                output += f"""
                <div style='background: white; border: 2px solid #fca5a5; border-radius: 8px; padding: 16px; margin: 12px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.05);'>
                    <h3 style='margin: 0 0 12px 0; color: #dc2626; font-size: 16px; border-bottom: 2px solid #fca5a5; padding-bottom: 8px;'>
                        📦 Namespace: {html.escape(namespace)}
                        <span style='background: #fef2f2; color: #991b1b; padding: 2px 8px; border-radius: 4px; font-size: 12px; margin-left: 8px;'>
                            {len(pods)} pod(s)
                        </span>
                    </h3>
                """
                
                for pod in pods:
                    status_color = {
                        'imagepullbackoff': '#dc2626',
                        'crashloopbackoff': '#ea580c',
                        'error': '#dc2626',
                        'pending': '#f59e0b'
                    }.get(pod['status'].lower(), '#6b7280')
                    
                    status_icon = {
                        'imagepullbackoff': '🔴',
                        'crashloopbackoff': '🔄',
                        'error': '❌',
                        'pending': '⏳'
                    }.get(pod['status'].lower(), '⚠️')
                    
                    output += f"""
                    <div style='background: #fef2f2; border-left: 4px solid {status_color}; padding: 12px; margin: 8px 0; border-radius: 4px;'>
                        <div style='display: flex; justify-content: space-between; align-items: center;'>
                            <div style='flex: 1;'>
                                <p style='margin: 0 0 4px 0; font-weight: bold; color: #1f2937; font-size: 14px;'>
                                    {status_icon} {html.escape(pod['pod'])}
                                </p>
                                <p style='margin: 0; font-size: 12px; color: #6b7280;'>
                                    Status: <span style='color: {status_color}; font-weight: bold;'>{pod['status'].upper()}</span>
                                </p>
                            </div>
                            <div>
                                <span style='background: {status_color}; color: white; padding: 4px 12px; border-radius: 4px; font-size: 11px; font-weight: bold;'>
                                    FAILED
                                </span>
                            </div>
                        </div>
                    </div>
                    """
                
                output += "</div>"
        
        # Add link to Datadog
        datadog_url = "https://arlo.datadoghq.com/orchestration/explorer/pod?query=env%3Aproduction%20pod_status%3Aimagepullbackoff"
        output += f"""
        <div style='background: #f3f4f6; padding: 12px; border-radius: 6px; margin: 16px 0; border: 1px solid #d1d5db;'>
            <p style='margin: 0 0 8px 0; color: #374151; font-size: 13px; font-weight: bold;'>
                📊 View in Datadog:
            </p>
            <a href='{datadog_url}' target='_blank' 
               style='display: inline-block; background: #632ca6; color: white; padding: 8px 16px; 
                      border-radius: 4px; text-decoration: none; font-size: 13px; font-weight: bold;'>
                🔗 Open Kubernetes Pod Explorer
            </a>
        </div>
        
        <div style='background: #fffbeb; padding: 12px; border-left: 4px solid #f59e0b; border-radius: 4px; margin: 16px 0;'>
            <p style='margin: 0 0 8px 0; color: #92400e; font-weight: bold; font-size: 13px;'>
                💡 Common Causes of Failed Pods & 4xx Errors:
            </p>
            <ul style='margin: 4px 0 0 0; padding-left: 20px; color: #78350f; font-size: 12px; line-height: 1.6;'>
                <li><strong>ImagePullBackOff:</strong> Docker image not found or no access to registry</li>
                <li><strong>CrashLoopBackOff:</strong> Application crashes repeatedly after starting</li>
                <li><strong>Pending:</strong> Not enough resources or scheduling issues</li>
                <li><strong>Impact:</strong> Failed pods → Service unavailable → 502/503/504 errors</li>
            </ul>
        </div>
        """
        
        print(f"✅ Completed: Found {len(failed_pods)} failed pods")
        return output
        
    except Exception as e:
        print(f"❌ Error reading failed pods: {str(e)}")
        import traceback
        traceback.print_exc()
        return f"""
        <div style='background-color: #fee2e2; padding: 16px; border-left: 4px solid #ef4444; border-radius: 4px; margin: 12px 0;'>
            <p style='margin: 0; color: #991b1b; font-weight: bold;'>
                ❌ Error reading failed pods: {html.escape(str(e))}
            </p>
        </div>
        """


def read_datadog_403_errors(query: str = "", timerange_hours: int = 4) -> str:
    """
    Monitor 403 Forbidden errors from Datadog APM traces
    Specifically for Artifactory and other services returning 403 errors
    """
    print("=" * 80)
    print("🚫 Datadog 403 Errors Monitor (APM Traces)")
    print("=" * 80)
    
    dd_api_key = os.getenv("DATADOG_API_KEY")
    dd_app_key = os.getenv("DATADOG_APP_KEY")
    dd_site = os.getenv("DATADOG_SITE", "arlo.datadoghq.com")
    
    if not dd_api_key or not dd_app_key:
        return "<p>⚠️ Datadog credentials not configured</p>"
    
    try:
        # Calculate time range
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=timerange_hours)
        
        from_ts = int(start_time.timestamp() * 1000)  # APM uses milliseconds
        to_ts = int(end_time.timestamp() * 1000)
        
        print(f"📅 Time range: {start_time.strftime('%Y-%m-%d %H:%M')} to {end_time.strftime('%Y-%m-%d %H:%M')}")
        
        # Query APM for 403 errors
        base_url = f"https://{dd_site}/api/v1"
        headers = {
            "DD-API-KEY": dd_api_key,
            "DD-APPLICATION-KEY": dd_app_key,
            "Content-Type": "application/json"
        }
        
        # Search for traces with 403 status code
        search_url = f"{base_url}/trace/search"
        
        # Query for 403 errors across all services
        body = {
            "query": "@http.status_code:403 env:production",
            "start": from_ts,
            "end": to_ts
        }
        
        response = requests.post(search_url, headers=headers, json=body, timeout=30)
        
        traces_403 = []
        
        if response.status_code == 200:
            data = response.json()
            traces = data.get('data', [])
            
            for trace in traces[:100]:  # Limit to 100 most recent
                # Extract relevant info from trace
                service = trace.get('service', 'unknown')
                resource = trace.get('resource', 'unknown')
                duration = trace.get('duration', 0)
                timestamp = trace.get('start', 0)
                
                traces_403.append({
                    'service': service,
                    'resource': resource,
                    'duration': duration / 1000000,  # Convert to ms
                    'timestamp': datetime.fromtimestamp(timestamp / 1000).strftime('%Y-%m-%d %H:%M:%S')
                })
        
        # Build HTML output
        output = f"""
        <div style='background: linear-gradient(135deg, #dc2626 0%, #991b1b 100%); 
                    padding: 16px; 
                    border-radius: 8px; 
                    margin: 12px 0;
                    color: white;
                    box-shadow: 0 4px 6px rgba(0,0,0,0.1);'>
            <h2 style='margin: 0 0 8px 0; color: white; font-size: 20px; font-weight: bold;'>
                🚫 403 Forbidden Errors Monitor (APM Traces)
            </h2>
            <p style='margin: 0; font-size: 13px; opacity: 0.95;'>
                Monitoring HTTP 403 Forbidden responses from production services
            </p>
            <div style='margin-top: 8px; padding: 8px; background: rgba(255,255,255,0.15); border-radius: 4px; font-size: 12px;'>
                <strong>📅 Time Range:</strong> {start_time.strftime('%Y-%m-%d %H:%M')} to {end_time.strftime('%Y-%m-%d %H:%M')} ({timerange_hours}h)
            </div>
        </div>
        """
        
        if not traces_403:
            output += """
            <div style='background-color: #d1fae5; padding: 16px; border-left: 4px solid #10b981; border-radius: 4px; margin: 12px 0;'>
                <p style='margin: 0; color: #065f46; font-weight: bold;'>
                    ✅ No 403 errors detected in the specified time range
                </p>
                <p style='margin: 8px 0 0 0; color: #047857; font-size: 13px;'>
                    All requests are being authorized successfully.
                </p>
            </div>
            """
        else:
            # Group by service
            errors_by_service = {}
            for trace in traces_403:
                svc = trace['service']
                if svc not in errors_by_service:
                    errors_by_service[svc] = []
                errors_by_service[svc].append(trace)
            
            output += f"""
            <div style='background-color: #fee2e2; padding: 12px; border-left: 4px solid #dc2626; border-radius: 4px; margin: 12px 0;'>
                <p style='margin: 0; color: #991b1b; font-weight: bold; font-size: 15px;'>
                    ⚠️ Found {len(traces_403)} 403 error(s) across {len(errors_by_service)} service(s)
                </p>
            </div>
            """
            
            # Display errors grouped by service
            for service, errors in sorted(errors_by_service.items(), key=lambda x: len(x[1]), reverse=True):
                output += f"""
                <div style='background: white; border: 2px solid #fca5a5; border-radius: 8px; padding: 16px; margin: 12px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.05);'>
                    <h3 style='margin: 0 0 12px 0; color: #dc2626; font-size: 16px; border-bottom: 2px solid #fca5a5; padding-bottom: 8px;'>
                        🔴 Service: {html.escape(service)}
                        <span style='background: #fee2e2; color: #991b1b; padding: 2px 8px; border-radius: 4px; font-size: 12px; margin-left: 8px;'>
                            {len(errors)} error(s)
                        </span>
                    </h3>
                """
                
                # Show top 5 errors for this service
                for error in errors[:5]:
                    output += f"""
                    <div style='background: #fef2f2; border-left: 4px solid #dc2626; padding: 12px; margin: 8px 0; border-radius: 4px;'>
                        <div style='margin-bottom: 6px;'>
                            <span style='background: #dc2626; color: white; padding: 2px 8px; border-radius: 3px; font-size: 11px; font-weight: bold;'>
                                403 FORBIDDEN
                            </span>
                            <span style='color: #6b7280; font-size: 11px; margin-left: 8px;'>
                                {error['timestamp']}
                            </span>
                        </div>
                        <p style='margin: 4px 0; font-size: 13px; color: #1f2937; word-break: break-all;'>
                            <strong>Resource:</strong> {html.escape(error['resource'])}
                        </p>
                        <p style='margin: 4px 0; font-size: 12px; color: #6b7280;'>
                            <strong>Duration:</strong> {error['duration']:.2f}ms
                        </p>
                    </div>
                    """
                
                if len(errors) > 5:
                    output += f"""
                    <p style='margin: 8px 0 0 0; color: #6b7280; font-size: 12px; font-style: italic;'>
                        ... and {len(errors) - 5} more error(s)
                    </p>
                    """
                
                output += "</div>"
        
        # Add link to Datadog APM
        datadog_url = "https://arlo.datadoghq.com/apm/traces?query=env%3Aproduction%20service%3Aartifactory%20operation_name%3Aservlet.request%20%40http.status_code%3A403"
        output += f"""
        <div style='background: #f3f4f6; padding: 12px; border-radius: 6px; margin: 16px 0; border: 1px solid #d1d5db;'>
            <p style='margin: 0 0 8px 0; color: #374151; font-size: 13px; font-weight: bold;'>
                📊 View in Datadog APM:
            </p>
            <a href='{datadog_url}' target='_blank' 
               style='display: inline-block; background: #632ca6; color: white; padding: 8px 16px; 
                      border-radius: 4px; text-decoration: none; font-size: 13px; font-weight: bold;'>
                🔗 Open APM Traces Explorer (403 Errors)
            </a>
        </div>
        
        <div style='background: #fffbeb; padding: 12px; border-left: 4px solid #f59e0b; border-radius: 4px; margin: 16px 0;'>
            <p style='margin: 0 0 8px 0; color: #92400e; font-weight: bold; font-size: 13px;'>
                💡 Common Causes of 403 Forbidden Errors:
            </p>
            <ul style='margin: 4px 0 0 0; padding-left: 20px; color: #78350f; font-size: 12px; line-height: 1.6;'>
                <li><strong>Authentication:</strong> Invalid or expired API tokens/credentials</li>
                <li><strong>Authorization:</strong> User lacks required permissions for the resource</li>
                <li><strong>IP Whitelist:</strong> Source IP not allowed to access the service</li>
                <li><strong>Rate Limiting:</strong> Too many requests from the same source</li>
                <li><strong>Artifactory:</strong> Repository permissions or license issues</li>
            </ul>
        </div>
        """
        
        print(f"✅ Completed: Found {len(traces_403)} 403 errors")
        return output
        
    except Exception as e:
        print(f"❌ Error reading 403 errors: {str(e)}")
        import traceback
        traceback.print_exc()
        return f"""
        <div style='background-color: #fee2e2; padding: 16px; border-left: 4px solid #ef4444; border-radius: 4px; margin: 12px 0;'>
            <p style='margin: 0; color: #991b1b; font-weight: bold;'>
                ❌ Error reading 403 errors: {html.escape(str(e))}
            </p>
        </div>
        """
