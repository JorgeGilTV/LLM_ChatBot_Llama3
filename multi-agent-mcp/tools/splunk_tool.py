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
        
        # Optimized P0 Streaming Dashboard Query - Time series data for charts
        # Use tstats with timechart to get data points over time
        search_query = f'''| tstats count where index=streaming_prod earliest=-{timerange_hours}h by _time, host span=1h
| rex field=host "-(?<zone>z[1-4])-"
| where isnotnull(zone)
| timechart span=1h sum(count) as events by zone
| fillnull value=0'''
        
        if query:
            search_query = f'''| tstats count where index=streaming_prod earliest=-{timerange_hours}h by _time, host span=1h
| rex field=host "-(?<zone>z[1-4])-"
| where isnotnull(zone) AND (match(host, "(?i){query}") OR match(zone, "(?i){query}"))
| timechart span=1h sum(count) as events by zone
| fillnull value=0'''
        
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
        
        # Parse timechart results from export endpoint
        timeseries_data = []
        for line in response.text.strip().split('\n'):
            if line:
                try:
                    result = json.loads(line)
                    # Only use final results (preview=false)
                    if result.get("result") and result.get("preview") == False:
                        timeseries_data.append(result["result"])
                except json.JSONDecodeError:
                    continue
        
        if len(timeseries_data) == 0:
            output += f"""
            <div style='margin: 8px 0; padding: 12px; background-color: #fff3cd; border-left: 3px solid #ffc107; border-radius: 4px;'>
                <p style='margin: 0; font-size: 12px; color: #856404;'>⚠️ No streaming data found{f" matching: {html.escape(query)}" if query else ""}</p>
            </div>
            """
            return output
        
        # Transform timeseries data into Chart.js format
        # Timeseries data has format: [{_time: "...", z1: count, z2: count, z3: count, z4: count}, ...]
        timestamps = []
        zone_data = {"z1": [], "z2": [], "z3": [], "z4": []}
        
        for datapoint in timeseries_data:
            # Get timestamp
            timestamp = datapoint.get("_time", "")
            timestamps.append(timestamp)
            
            # Get counts for each zone
            for zone in ["z1", "z2", "z3", "z4"]:
                count = int(datapoint.get(zone, 0))
                zone_data[zone].append(count)
        
        # Calculate total events per zone
        zone_totals = {zone: sum(counts) for zone, counts in zone_data.items()}
        
        # Generate Chart.js charts for each zone
        output += f"""
        <div style='display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin: 12px 0;'>
        """
        
        zone_colors = {
            "z1": "#4e79a7",
            "z2": "#f28e2c",
            "z3": "#e15759",
            "z4": "#76b7b2"
        }
        
        for zone_num in ["1", "2", "3", "4"]:
            zone_key = f"z{zone_num}"
            zone_name = f"Zone {zone_num} (Recording Uploads)"
            total_events = zone_totals.get(zone_key, 0)
            zone_color = zone_colors.get(zone_key, "#4e79a7")
            chart_id = f"chart_{zone_key}_{int(time.time())}"
            
            output += f"""
            <div style='background: white; padding: 12px; border-radius: 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
                <div style='margin-bottom: 10px;'>
                    <div style='display: flex; justify-content: space-between; align-items: center;'>
                        <span style='font-size: 13px; font-weight: bold; color: #2d3748;'>{zone_name}</span>
                        <span style='font-size: 14px;'>✅</span>
                    </div>
                    <div style='margin-top: 4px;'>
                        <span style='font-size: 11px; color: #6b7280;'>Total: </span>
                        <span style='font-size: 14px; font-weight: bold; color: {zone_color};'>{total_events:,}</span>
                        <span style='font-size: 11px; color: #6b7280;'> events</span>
                    </div>
                </div>
                <div style='position: relative; height: 150px;'>
                    <canvas id="{chart_id}"></canvas>
                </div>
            </div>
            """
        
        output += "</div>"
        
        # Generate Chart.js script
        chart_data_json = json.dumps({
            "timestamps": timestamps,
            "zones": {
                "z1": zone_data["z1"],
                "z2": zone_data["z2"],
                "z3": zone_data["z3"],
                "z4": zone_data["z4"]
            }
        })
        
        output += f"""
        <script>
        (function() {{
            const data = {chart_data_json};
            const colors = {{
                "z1": "#4e79a7",
                "z2": "#f28e2c",
                "z3": "#e15759",
                "z4": "#76b7b2"
            }};
            
            // Format timestamps to show only time (HH:MM)
            const formattedLabels = data.timestamps.map(ts => {{
                try {{
                    const date = new Date(ts);
                    const hours = date.getHours().toString().padStart(2, '0');
                    const minutes = date.getMinutes().toString().padStart(2, '0');
                    return `${{hours}}:${{minutes}}`;
                }} catch (e) {{
                    return ts;
                }}
            }});
            
            Object.keys(data.zones).forEach((zone, idx) => {{
                const zoneNum = zone.replace('z', '');
                const chartId = `chart_${{zone}}_` + Math.floor(Date.now() / 1000);
                const canvas = document.getElementById(chartId);
                
                if (canvas) {{
                    new Chart(canvas, {{
                        type: 'line',
                        data: {{
                            labels: formattedLabels,
                            datasets: [{{
                                label: `Zone ${{zoneNum}}`,
                                data: data.zones[zone],
                                borderColor: colors[zone],
                                backgroundColor: colors[zone] + '20',
                                fill: true,
                                tension: 0.4,
                                borderWidth: 2,
                                pointRadius: 2,
                                pointHoverRadius: 4
                            }}]
                        }},
                        options: {{
                            responsive: true,
                            maintainAspectRatio: false,
                            plugins: {{
                                legend: {{ display: false }},
                                tooltip: {{
                                    callbacks: {{
                                        title: function(context) {{
                                            return 'Time: ' + context[0].label;
                                        }},
                                        label: function(context) {{
                                            return context.parsed.y.toLocaleString() + ' events';
                                        }}
                                    }}
                                }}
                            }},
                            scales: {{
                                x: {{
                                    display: true,
                                    title: {{
                                        display: false
                                    }},
                                    ticks: {{ 
                                        maxRotation: 45,
                                        minRotation: 45,
                                        font: {{ size: 9 }},
                                        maxTicksLimit: 12
                                    }},
                                    grid: {{
                                        display: true,
                                        color: 'rgba(0, 0, 0, 0.05)'
                                    }}
                                }},
                                y: {{
                                    display: true,
                                    beginAtZero: true,
                                    ticks: {{ 
                                        font: {{ size: 9 }},
                                        callback: function(value) {{
                                            return value >= 1000 ? (value/1000).toFixed(1) + 'k' : value;
                                        }}
                                    }},
                                    grid: {{
                                        display: true,
                                        color: 'rgba(0, 0, 0, 0.05)'
                                    }}
                                }}
                            }}
                        }}
                    }});
                }}
            }});
        }})();
        </script>
        """
        
        return output
        
    except Exception as e:
        print(f"❌ Error reading Splunk dashboard: {str(e)}")
        import traceback
        traceback.print_exc()
        return f"<p>❌ Error reading Splunk dashboard: {html.escape(str(e))}</p>"
