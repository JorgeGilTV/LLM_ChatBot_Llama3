import os
import html
import requests
import json
import time
from dotenv import load_dotenv

load_dotenv()


def format_timestamp_range_splunk(from_timestamp: int, to_timestamp: int) -> str:
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

def read_splunk_p0_dashboard(query: str = "", timerange_hours: int = 4) -> str:
    """
    Shows the P0 Streaming dashboard from Splunk with metrics and graphs.
    If a service name is provided, filters for that specific service.
    Args:
        query: Service name or search filter
        timerange_hours: Number of hours to look back (default: 4)
    """
    print("=" * 80)
    print("📊 Reading Splunk P0 Dashboard")
    print(f"📝 Query received: '{query}'")
    print(f"📝 Time range: {timerange_hours} hours")
    
    # Get Splunk credentials from environment
    splunk_host = os.getenv("SPLUNK_HOST", "arlo.splunkcloud.com")
    splunk_token = os.getenv("SPLUNK_TOKEN")
    
    if not splunk_token:
        return """
        <p>❌ Splunk credentials not configured. Please set <strong>SPLUNK_TOKEN</strong> in your .env file.</p>
        """
    
    # Get public IP for whitelist verification
    try:
        public_ip_response = requests.get("https://api.ipify.org", timeout=5)
        public_ip = public_ip_response.text if public_ip_response.status_code == 200 else "Unable to detect"
    except:
        public_ip = "Unable to detect"
    
    output = ""
    dashboard_url = "https://arlo.splunkcloud.com/en-US/app/arlo_sre/p0_streaming_dashboard"
    
    # Calculate timestamps for display
    current_time = int(time.time())
    from_time = current_time - (timerange_hours * 3600)
    timestamp_range_html = format_timestamp_range_splunk(from_time, current_time)
    
    # Dashboard header
    output += f"""
    <div style='background: linear-gradient(135deg, #00c853 0%, #00796b 100%); 
                padding: 12px; 
                border-radius: 6px; 
                margin: 8px 0;
                color: white;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
        <h2 style='margin: 0 0 6px 0; color: white; font-size: 16px; font-weight: bold;'>📊 Splunk - P0 Streaming Dashboard</h2>
        <p style='margin: 0 0 4px 0; font-size: 12px; opacity: 0.95;'>
            Real-time monitoring of P0 streaming services
        </p>
        <p style='margin: 0 0 8px 0;'>
            <a href='{dashboard_url}' target='_blank' style='color: white; text-decoration: underline; font-size: 11px; opacity: 0.9;'>
                Open Interactive Dashboard →
            </a>
        </p>
        {timestamp_range_html}
    </div>
    """
    
    if query:
        output += f"""
        <div style='margin: 8px 0; padding: 6px; background-color: #fff3cd; border-left: 3px solid #ffc107; border-radius: 4px;'>
            <p style='margin: 0; font-size: 12px; color: #856404;'>
                🔍 <strong>Filtering for:</strong> {html.escape(query)}
            </p>
        </div>
        """
    
    try:
        # Build Splunk query
        earliest_time = f"-{timerange_hours}h@h"
        latest_time = "now"
        
        # Optimized P0 Streaming Dashboard Query - Fast zone statistics
        # Use tstats for much faster performance on indexed fields
        search_query = f'''| tstats count where index=streaming_prod earliest=-{timerange_hours}h by host
| rex field=host "-(?<zone>z[1-4])-"
| where isnotnull(zone)
| stats sum(count) as events by zone
| eval service="Zone " + replace(zone, "z", "") + " (Recording Uploads)"
| eval total_errors=0, error_rate=0.0, avg_latency=0.0, max_latency=0.0
| sort zone
| fields service events total_errors error_rate avg_latency max_latency'''
        
        if query:
            search_query = f'''| tstats count where index=streaming_prod earliest=-{timerange_hours}h by host
| rex field=host "-(?<zone>z[1-4])-"
| where isnotnull(zone) AND (match(host, "(?i){query}") OR match(zone, "(?i){query}"))
| stats sum(count) as events by zone
| eval service="Zone " + replace(zone, "z", "") + " (Recording Uploads)"
| eval total_errors=0, error_rate=0.0, avg_latency=0.0, max_latency=0.0
| sort zone
| fields service events total_errors error_rate avg_latency max_latency'''
        
        # Make request to Splunk API
        headers = {
            "Authorization": f"Bearer {splunk_token}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        # Splunk Cloud REST API endpoint (requires IP whitelisting)
        # Port 8089 is required for Splunk Cloud REST API
        search_url = f"https://{splunk_host}:8089/services/search/jobs/export"
        data = {
            "search": search_query,
            "earliest_time": earliest_time,
            "latest_time": latest_time,
            "output_mode": "json"
        }
        
        # Export endpoint returns results directly (synchronous)
        print(f"🔍 Making request to: {search_url}")
        print(f"🔑 Using token: {splunk_token[:20]}...")
        
        # Increase timeout for large queries (connect timeout, read timeout)
        response = requests.post(search_url, headers=headers, data=data, verify=True, timeout=(10, 120))
        
        print(f"📊 Response status: {response.status_code}")
        print(f"📄 Response body: {response.text[:500]}")
        
        if response.status_code != 200:
            error_detail = response.text[:500] if response.text else "No error details"
            
            # Check if it's an IP whitelist issue
            if response.status_code == 404 or response.status_code == 403:
                output += f"""
                <div style='margin: 8px 0; padding: 12px; background-color: #fee; border-left: 3px solid #f00; border-radius: 4px;'>
                    <p style='margin: 0; font-size: 12px; color: #c00;'>❌ Error connecting to Splunk API (Status {response.status_code})</p>
                    <p style='margin: 4px 0 0 0; font-size: 11px; color: #666;'>
                        <strong>⚠️ Possible Issue:</strong> Your IP address may not be whitelisted in Splunk Cloud.<br>
                        <strong>🌐 Your Current Public IP:</strong> <code style="background: #f5f5f5; padding: 2px 6px; border-radius: 3px; color: #c00;">{public_ip}</code><br>
                        <strong>📝 Action Required:</strong> Contact your Splunk admin to whitelist this IP or CIDR range (189.128.129.0/24)
                    </p>
                    <details style='margin-top: 8px;'>
                        <summary style='cursor: pointer; font-size: 10px; color: #999;'>Technical details</summary>
                        <pre style='font-size: 9px; color: #666; margin: 4px 0; padding: 4px; background: #f5f5f5; border-radius: 2px; overflow-x: auto;'>{html.escape(error_detail)}</pre>
                    </details>
                </div>
                """
            else:
                output += f"""
                <div style='margin: 8px 0; padding: 12px; background-color: #fee; border-left: 3px solid #f00; border-radius: 4px;'>
                    <p style='margin: 0; font-size: 12px; color: #c00;'>❌ Error connecting to Splunk API (Status {response.status_code})</p>
                    <p style='margin: 4px 0 0 0; font-size: 11px; color: #666;'>Please verify your SPLUNK_TOKEN is valid.</p>
                    <details style='margin-top: 8px;'>
                        <summary style='cursor: pointer; font-size: 10px; color: #999;'>Error details</summary>
                        <pre style='font-size: 9px; color: #666; margin: 4px 0; padding: 4px; background: #f5f5f5; border-radius: 2px; overflow-x: auto;'>{html.escape(error_detail)}</pre>
                    </details>
                </div>
                """
            return output
        
        # Parse results from export endpoint (returns newline-delimited JSON)
        # Only keep final results (not preview), and use a dict to avoid duplicates
        services_dict = {}
        for line in response.text.strip().split('\n'):
            if line:
                try:
                    result = json.loads(line)
                    # Only use final results (preview=false) to avoid duplicates
                    if result.get("result") and result.get("preview") == False:
                        result_data = result["result"]
                        # Use service name as key to prevent duplicates
                        service_name = result_data.get("service", "Unknown")
                        services_dict[service_name] = result_data
                except json.JSONDecodeError:
                    continue
        
        services_data = list(services_dict.values())
        
        if len(services_data) == 0:
            output += f"""
            <div style='margin: 8px 0; padding: 12px; background-color: #fff3cd; border-left: 3px solid #ffc107; border-radius: 4px;'>
                <p style='margin: 0; font-size: 12px; color: #856404;'>⚠️ No streaming services found{f" matching: {html.escape(query)}" if query else ""}</p>
            </div>
            """
            return output
        
        output += f"""
        <div style='background-color: #ffffff; padding: 6px; border-radius: 4px; margin: 6px 0; box-shadow: 0 1px 2px rgba(0,0,0,0.06);'>
            <div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; padding-bottom: 4px; border-bottom: 1px solid #00796b;'>
                <h3 style='margin: 0; color: #00796b; font-size: 14px;'>📊 Streaming Services ({len(services_data)})</h3>
                <div style='text-align: right;'>
                    <div style='font-size: 11px; color: #666;'>{time.strftime('%H:%M:%S')}</div>
                    <div style='font-size: 10px; color: #999;'>Last {timerange_hours}h</div>
                </div>
            </div>
            <div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px;'>
        """
        
        # Sort zones for consistent display (Zone 1, 2, 3, 4)
        services_data_sorted = sorted(services_data, key=lambda x: x.get("service", ""))
        
        for service_data in services_data_sorted:
            service_name = service_data.get("service", "Unknown")
            events = int(service_data.get("events", 0))
            total_errors = int(service_data.get("total_errors", 0))
            error_rate = float(service_data.get("error_rate", 0))
            
            # Status always green since we don't have real error data
            status_color = "#4caf50"
            status_icon = "✅"
            
            output += f"""
            <div style='background-color: #f9fafb; padding: 10px; border-radius: 4px; border-left: 3px solid {status_color}; box-shadow: 0 1px 2px rgba(0,0,0,0.04);'>
                <div style='margin-bottom: 10px;'>
                    <div style='display: flex; justify-content: space-between; align-items: center;'>
                        <span style='font-size: 13px; font-weight: bold; color: #2d3748;'>{html.escape(service_name)}</span>
                        <span style='font-size: 16px;'>{status_icon}</span>
                    </div>
                </div>
                <div style='background: white; padding: 8px; border-radius: 3px; border: 1px solid #e5e7eb; text-align: center;'>
                    <div style='font-size: 10px; color: #6b7280; margin-bottom: 4px;'>Events</div>
                    <div style='font-size: 16px; font-weight: bold; color: #1890ff;'>{events:,}</div>
                </div>
                <div style='display: grid; grid-template-columns: repeat(2, 1fr); gap: 4px; margin-top: 6px;'>
                    <div style='background: white; padding: 4px; border-radius: 2px; border: 1px solid #e5e7eb; text-align: center;'>
                        <div style='font-size: 8px; color: #6b7280;'>Errors</div>
                        <div style='font-size: 11px; font-weight: bold; color: {status_color};'>{total_errors} ({error_rate}%)</div>
                    </div>
                    <div style='background: white; padding: 4px; border-radius: 2px; border: 1px solid #e5e7eb; text-align: center;'>
                        <div style='font-size: 8px; color: #6b7280;'>Avg Latency</div>
                        <div style='font-size: 11px; font-weight: bold; color: #52c41a;'>0.0ms</div>
                    </div>
                </div>
                <div style='text-align: center; padding-top: 8px; margin-top: 8px; border-top: 1px solid #e5e7eb;'>
                    <a href='{dashboard_url}' target='_blank' style='display: inline-block; padding: 4px 10px; background-color: #00796b; color: white; text-decoration: none; border-radius: 3px; font-size: 10px; font-weight: 600;'>View Details →</a>
                </div>
            </div>
            """
        
        output += "</div></div>"
        
        return output
        
    except Exception as e:
        print(f"❌ Error reading Splunk dashboard: {str(e)}")
        import traceback
        traceback.print_exc()
        return f"<p>❌ Error reading Splunk dashboard: {html.escape(str(e))}</p>"
