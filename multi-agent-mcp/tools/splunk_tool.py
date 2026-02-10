import os
import html
import requests
import json
import time
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed

load_dotenv()


def execute_splunk_query(query_key, query_data, splunk_host, splunk_token, earliest_time, latest_time):
    """Execute a single Splunk query - helper for parallel execution"""
    try:
        headers = {
            "Authorization": f"Bearer {splunk_token}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        search_url = f"https://{splunk_host}:8089/services/search/jobs/export"
        data = {
            "search": query_data,
            "earliest_time": earliest_time,
            "latest_time": latest_time,
            "output_mode": "json"
        }
        
        response = requests.post(search_url, headers=headers, data=data, verify=True, timeout=(10, 120))
        
        if response.status_code == 200:
            # Parse JSON results from export endpoint
            results = []
            for line in response.text.strip().split('\n'):
                if line:
                    try:
                        result = json.loads(line)
                        if result.get("result") and result.get("preview") == False:
                            results.append(result["result"])
                    except json.JSONDecodeError:
                        continue
            return query_key, results, None
        else:
            return query_key, None, f"HTTP {response.status_code}: {response.text[:200]}"
    
    except Exception as e:
        return query_key, None, str(e)


def execute_splunk_queries_parallel(queries_dict, splunk_host, splunk_token, earliest_time, latest_time, max_workers=3):
    """Execute multiple Splunk queries in parallel"""
    results = {}
    
    if not queries_dict:
        return results
    
    print(f"🚀 Executing {len(queries_dict)} Splunk queries in parallel...")
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_key = {}
        for query_key, query_data in queries_dict.items():
            future = executor.submit(
                execute_splunk_query,
                query_key,
                query_data,
                splunk_host,
                splunk_token,
                earliest_time,
                latest_time
            )
            future_to_key[future] = query_key
        
        for future in as_completed(future_to_key):
            query_key = future_to_key[future]
            try:
                key, data, error = future.result()
                if error:
                    print(f"❌ Query '{key}' failed: {error}")
                    results[key] = None
                else:
                    results[key] = data
                    print(f"✅ Query '{key}' completed: {len(data) if data else 0} results")
            except Exception as e:
                print(f"❌ Query '{query_key}' exception: {str(e)}")
                results[query_key] = None
    
    elapsed = time.time() - start_time
    print(f"✅ All Splunk queries completed in {elapsed:.2f}s")
    
    return results


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
        
        # PARALLEL OPTIMIZATION: Collect all queries and execute in parallel
        all_queries = {}
        
        # Query 1: Recording Uploads (main query)
        recording_query = f'''| tstats count where index=streaming_prod earliest=-{timerange_hours}h by _time, host span=1h
| rex field=host "-(?<zone>z[1-4])-"
| where isnotnull(zone)
| timechart span=1h sum(count) as events by zone
| fillnull value=0'''
        
        if query:
            recording_query = f'''| tstats count where index=streaming_prod earliest=-{timerange_hours}h by _time, host span=1h
| rex field=host "-(?<zone>z[1-4])-"
| where isnotnull(zone) AND (match(host, "(?i){query}") OR match(zone, "(?i){query}"))
| timechart span=1h sum(count) as events by zone
| fillnull value=0'''
        
        all_queries['recording'] = recording_query
        
        # Query 2: Active Servers
        all_queries['servers'] = f'''| tstats dc(host) as server_count where index=streaming_prod earliest=-{timerange_hours}h by _time, host span=1h
| rex field=host "-(?<zone>z[1-4])-"
| where isnotnull(zone)
| timechart span=1h dc(host) as servers by zone
| fillnull value=0'''
        
        # Query 3: JVM Crashes
        all_queries['jvm'] = f'''| search index=streaming_prod earliest=-{timerange_hours}h ("JVM" OR "OutOfMemoryError" OR "crash")
| rex field=host "-(?<zone>z[1-4])-"
| where isnotnull(zone)
| timechart span=1h count by zone
| fillnull value=0'''
        
        # Execute all queries in parallel
        all_results = execute_splunk_queries_parallel(all_queries, splunk_host, splunk_token, earliest_time, latest_time, max_workers=3)
        
        # Get recording uploads results
        timeseries_data = all_results.get('recording') or []

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
            # Get timestamp - Splunk returns epoch timestamp as string
            timestamp_raw = datapoint.get("_time", "")
            
            # Convert Splunk timestamp to JavaScript-friendly format
            try:
                # Splunk timestamps are in epoch seconds
                from datetime import datetime
                if timestamp_raw:
                    # Parse as epoch or ISO format
                    try:
                        ts_epoch = float(timestamp_raw)
                        dt = datetime.fromtimestamp(ts_epoch)
                    except:
                        dt = datetime.fromisoformat(timestamp_raw.replace(" GMT", "").replace("Z", ""))
                    
                    # Format as HH:MM for display
                    timestamp_formatted = dt.strftime("%H:%M")
                    timestamps.append(timestamp_formatted)
                else:
                    timestamps.append("")
            except Exception as e:
                print(f"Error parsing timestamp {timestamp_raw}: {e}")
                timestamps.append(str(timestamp_raw))
            
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
            chart_id = f"chart_p0_{zone_key}"
            
            # Calculate outliers/errors (0 for now, can be enhanced later)
            outliers = 0
            error_percentage = 0.0 if total_events == 0 else (outliers / total_events * 100)
            
            output += f"""
            <div style='background: white; padding: 12px; border-radius: 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
                <div style='margin-bottom: 10px;'>
                    <div style='display: flex; justify-content: space-between; align-items: center;'>
                        <div>
                            <span style='font-size: 13px; font-weight: bold; color: #2d3748;'>{zone_name}</span>
                        </div>
                        <div style='text-align: right;'>
                            <div style='font-size: 20px; font-weight: bold; color: #dc2626;'>{outliers}</div>
                            <div style='font-size: 9px; color: #6b7280;'>outliers</div>
                        </div>
                    </div>
                    <div style='margin-top: 6px; display: flex; justify-content: space-between; align-items: center;'>
                        <div>
                            <span style='font-size: 10px; color: #6b7280;'>Events: </span>
                            <span style='font-size: 13px; font-weight: bold; color: {zone_color};'>{total_events:,}</span>
                        </div>
                        <div style='text-align: right;'>
                            <span style='font-size: 12px; font-weight: bold; color: #dc2626;'>{error_percentage:.1f}%</span>
                            <span style='font-size: 9px; color: #6b7280;'> errors</span>
                        </div>
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
            
            // Timestamps are already formatted as HH:MM in Python
            const formattedLabels = data.timestamps;
            
            Object.keys(data.zones).forEach((zone, idx) => {{
                const zoneNum = zone.replace('z', '');
                const chartId = `chart_p0_${{zone}}`;
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
        
        # ========== ADDITIONAL METRICS ==========
        
        # 2. Active Servers by Zone
        output += "<h3 style='margin: 20px 0 10px 0; color: #2d3748; font-size: 14px;'>📡 Active Servers</h3>"
        
        servers_data = all_results.get('servers') or []
        if len(servers_data) > 0:
            servers_data = []
            for line in response_servers.text.strip().split('\n'):
                if line:
                    try:
                        result = json.loads(line)
                        if result.get("result") and result.get("preview") == False:
                            servers_data.append(result["result"])
                    except json.JSONDecodeError:
                        continue
            
            if len(servers_data) > 0:
                timestamps_servers = []
                zone_servers = {"z1": [], "z2": [], "z3": [], "z4": []}
                
                for datapoint in servers_data:
                    timestamp_raw = datapoint.get("_time", "")
                    try:
                        from datetime import datetime
                        if timestamp_raw:
                            try:
                                ts_epoch = float(timestamp_raw)
                                dt = datetime.fromtimestamp(ts_epoch)
                            except:
                                dt = datetime.fromisoformat(timestamp_raw.replace(" GMT", "").replace("Z", ""))
                            timestamps_servers.append(dt.strftime("%H:%M"))
                        else:
                            timestamps_servers.append("")
                    except:
                        timestamps_servers.append(str(timestamp_raw))
                    
                    for zone in ["z1", "z2", "z3", "z4"]:
                        count = int(datapoint.get(zone, 0))
                        zone_servers[zone].append(count)
                
                zone_server_totals = {zone: sum(counts) for zone, counts in zone_servers.items()}
                
                output += "<div style='display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 12px 0;'>"
                
                for zone_num in ["1", "2", "3", "4"]:
                    zone_key = f"z{zone_num}"
                    total_servers = zone_server_totals.get(zone_key, 0)
                    zone_color = zone_colors.get(zone_key, "#4e79a7")
                    chart_id_servers = f"chart_p0_servers_{zone_key}"
                    
                    output += f"""
                    <div style='background: white; padding: 12px; border-radius: 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
                        <div style='margin-bottom: 10px; text-align: center;'>
                            <span style='font-size: 12px; font-weight: bold; color: #2d3748;'>Zone {zone_num}</span>
                            <div style='font-size: 20px; font-weight: bold; color: {zone_color}; margin-top: 4px;'>{total_servers}</div>
                            <div style='font-size: 9px; color: #6b7280;'>servers</div>
                        </div>
                        <div style='position: relative; height: 100px;'>
                            <canvas id="{chart_id_servers}"></canvas>
                        </div>
                    </div>
                    """
                
                output += "</div>"
                
                # Chart.js script for Active Servers
                servers_data_json = json.dumps({
                    "timestamps": timestamps_servers,
                    "zones": zone_servers
                })
                
                output += f"""
                <script>
                (function() {{
                    const data = {servers_data_json};
                    const colors = {{
                        "z1": "#4e79a7",
                        "z2": "#f28e2c",
                        "z3": "#e15759",
                        "z4": "#76b7b2"
                    }};
                    
                    Object.keys(data.zones).forEach((zone) => {{
                        const chartId = `chart_p0_servers_${{zone}}`;
                        const canvas = document.getElementById(chartId);
                        
                        if (canvas) {{
                            new Chart(canvas, {{
                                type: 'line',
                                data: {{
                                    labels: data.timestamps,
                                    datasets: [{{
                                        data: data.zones[zone],
                                        borderColor: colors[zone],
                                        backgroundColor: colors[zone] + '20',
                                        fill: true,
                                        tension: 0.4,
                                        borderWidth: 2,
                                        pointRadius: 2
                                    }}]
                                }},
                                options: {{
                                    responsive: true,
                                    maintainAspectRatio: false,
                                    plugins: {{ legend: {{ display: false }} }},
                                    scales: {{
                                        x: {{ display: false }},
                                        y: {{ display: true, beginAtZero: true, ticks: {{ font: {{ size: 8 }} }} }}
                                    }}
                                }}
                            }});
                        }}
                    }});
                }})();
                </script>
                """
        
        # 3. JVM Crashes
        output += "<h3 style='margin: 20px 0 10px 0; color: #2d3748; font-size: 14px;'>🔥 JVM Crash - Error Count</h3>"
        
        jvm_data = all_results.get('jvm') or []
        if len(jvm_data) > 0:
            jvm_data = []
            for line in response_jvm.text.strip().split('\n'):
                if line:
                    try:
                        result = json.loads(line)
                        if result.get("result") and result.get("preview") == False:
                            jvm_data.append(result["result"])
                    except json.JSONDecodeError:
                        continue
            
            if len(jvm_data) > 0:
                timestamps_jvm = []
                zone_jvm = {"z1": [], "z2": [], "z3": [], "z4": []}
                
                for datapoint in jvm_data:
                    timestamp_raw = datapoint.get("_time", "")
                    try:
                        from datetime import datetime
                        if timestamp_raw:
                            try:
                                ts_epoch = float(timestamp_raw)
                                dt = datetime.fromtimestamp(ts_epoch)
                            except:
                                dt = datetime.fromisoformat(timestamp_raw.replace(" GMT", "").replace("Z", ""))
                            timestamps_jvm.append(dt.strftime("%H:%M"))
                        else:
                            timestamps_jvm.append("")
                    except:
                        timestamps_jvm.append(str(timestamp_raw))
                    
                    for zone in ["z1", "z2", "z3", "z4"]:
                        count = int(datapoint.get(zone, 0))
                        zone_jvm[zone].append(count)
                
                zone_jvm_totals = {zone: sum(counts) for zone, counts in zone_jvm.items()}
                total_jvm_errors = sum(zone_jvm_totals.values())
                
                output += f"""
                <div style='background: white; padding: 16px; border-radius: 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin: 12px 0;'>
                    <div style='margin-bottom: 10px;'>
                        <span style='font-size: 13px; font-weight: bold; color: #2d3748;'>Total JVM Errors: </span>
                        <span style='font-size: 20px; font-weight: bold; color: #dc2626;'>{total_jvm_errors}</span>
                    </div>
                    <div style='position: relative; height: 200px;'>
                        <canvas id="chart_p0_jvm"></canvas>
                    </div>
                </div>
                """
                
                jvm_data_json = json.dumps({
                    "timestamps": timestamps_jvm,
                    "zones": zone_jvm
                })
                
                output += f"""
                <script>
                (function() {{
                    const data = {jvm_data_json};
                    const colors = {{
                        "z1": "#4e79a7",
                        "z2": "#f28e2c",
                        "z3": "#e15759",
                        "z4": "#76b7b2"
                    }};
                    
                    const datasets = [];
                    Object.keys(data.zones).forEach((zone) => {{
                        datasets.push({{
                            label: 'Zone ' + zone.replace('z', ''),
                            data: data.zones[zone],
                            borderColor: colors[zone],
                            backgroundColor: colors[zone] + '80',
                            borderWidth: 2
                        }});
                    }});
                    
                    const canvas = document.getElementById('chart_p0_jvm');
                    if (canvas) {{
                        new Chart(canvas, {{
                            type: 'bar',
                            data: {{
                                labels: data.timestamps,
                                datasets: datasets
                            }},
                            options: {{
                                responsive: true,
                                maintainAspectRatio: false,
                                plugins: {{
                                    legend: {{ display: true, position: 'top' }}
                                }},
                                scales: {{
                                    x: {{ stacked: false }},
                                    y: {{ stacked: false, beginAtZero: true }}
                                }}
                            }}
                        }});
                    }}
                }})();
                </script>
                """
            else:
                output += "<p style='color: #6b7280; font-size: 12px; margin: 12px 0;'>✅ No JVM errors found</p>"
        
        return output
        
    except Exception as e:
        print(f"❌ Error reading Splunk dashboard: {str(e)}")
        import traceback
        traceback.print_exc()
        return f"<p>❌ Error reading Splunk dashboard: {html.escape(str(e))}</p>"


def read_splunk_p0_cvr_dashboard(query: str = "", timerange_hours: int = 4) -> str:
    """
    Shows the P0 CVR Streaming dashboard from Splunk with metrics and graphs.
    If a service name is provided, filters for that specific service.
    Args:
        query: Service name or search filter
        timerange_hours: Number of hours to look back (default: 4)
    """
    print("=" * 80)
    print("📊 Reading Splunk P0 CVR Dashboard")
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
    dashboard_url = "https://arlo.splunkcloud.com/en-US/app/arlo_sre/p0_cvr_dashboard"
    
    # Calculate timestamps for display
    current_time = int(time.time())
    from_time = current_time - (timerange_hours * 3600)
    timestamp_range_html = format_timestamp_range_splunk(from_time, current_time)
    
    # Dashboard header with different color scheme
    output += f"""
    <div style='background: linear-gradient(135deg, #9c27b0 0%, #6a1b9a 100%); 
                padding: 12px; 
                border-radius: 6px; 
                margin: 8px 0;
                color: white;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
        <h2 style='margin: 0 0 6px 0; color: white; font-size: 16px; font-weight: bold;'>📊 Splunk - P0 CVR Streaming Dashboard</h2>
        <p style='margin: 0 0 4px 0; font-size: 12px; opacity: 0.95;'>
            Real-time monitoring of P0 CVR streaming services
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
        # Build Splunk query for CVR
        earliest_time = f"-{timerange_hours}h@h"
        latest_time = "now"
        
        # PARALLEL OPTIMIZATION: Collect all queries and execute in parallel
        all_queries = {}
        
        # Query 1: Recording Uploads (main query)
        recording_query = f'''| tstats count where index=streaming_prod earliest=-{timerange_hours}h by _time, host span=1h
| rex field=host "-(?<zone>z[1-4])-"
| where isnotnull(zone)
| timechart span=1h sum(count) as events by zone
| fillnull value=0'''
        
        if query:
            recording_query = f'''| tstats count where index=streaming_prod earliest=-{timerange_hours}h by _time, host span=1h
| rex field=host "-(?<zone>z[1-4])-"
| where isnotnull(zone) AND (match(host, "(?i){query}") OR match(zone, "(?i){query}"))
| timechart span=1h sum(count) as events by zone
| fillnull value=0'''
        
        all_queries['recording'] = recording_query
        
        # Query 2: CVR Active Devices
        all_queries['devices'] = f'''| tstats dc(device_id) as device_count where index=streaming_prod earliest=-{timerange_hours}h "CVR" by _time span=1h
| timechart span=1h sum(device_count) as devices
| fillnull value=0'''
        
        # Query 3: CVR Connections Count
        all_queries['connections'] = f'''| search index=streaming_prod earliest=-{timerange_hours}h "CVR" "connection"
| timechart span=1h count as connections
| fillnull value=0'''
        
        # Execute all queries in parallel
        all_results = execute_splunk_queries_parallel(all_queries, splunk_host, splunk_token, earliest_time, latest_time, max_workers=3)
        
        # Get recording uploads results
        timeseries_data = all_results.get('recording') or []

        if len(timeseries_data) == 0:
            output += f"""
            <div style='margin: 8px 0; padding: 12px; background-color: #fff3cd; border-left: 3px solid #ffc107; border-radius: 4px;'>
                <p style='margin: 0; font-size: 12px; color: #856404;'>⚠️ No CVR streaming data found{f" matching: {html.escape(query)}" if query else ""}</p>
            </div>
            """
            return output
        
        # Transform timeseries data into Chart.js format
        timestamps = []
        zone_data = {"z1": [], "z2": [], "z3": [], "z4": []}
        
        for datapoint in timeseries_data:
            # Get timestamp
            timestamp_raw = datapoint.get("_time", "")
            
            # Convert Splunk timestamp to HH:MM format
            try:
                from datetime import datetime
                if timestamp_raw:
                    # Parse as epoch or ISO format
                    try:
                        ts_epoch = float(timestamp_raw)
                        dt = datetime.fromtimestamp(ts_epoch)
                    except:
                        dt = datetime.fromisoformat(timestamp_raw.replace(" GMT", "").replace("Z", ""))
                    
                    # Format as HH:MM for display
                    timestamp_formatted = dt.strftime("%H:%M")
                    timestamps.append(timestamp_formatted)
                else:
                    timestamps.append("")
            except Exception as e:
                print(f"Error parsing timestamp {timestamp_raw}: {e}")
                timestamps.append(str(timestamp_raw))
            
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
            zone_name = f"Zone {zone_num} (CVR Uploads)"
            total_events = zone_totals.get(zone_key, 0)
            zone_color = zone_colors.get(zone_key, "#4e79a7")
            chart_id = f"chart_cvr_{zone_key}"
            
            # Calculate outliers/errors (0 for now, can be enhanced later)
            outliers = 0
            error_percentage = 0.0 if total_events == 0 else (outliers / total_events * 100)
            
            output += f"""
            <div style='background: white; padding: 12px; border-radius: 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
                <div style='margin-bottom: 10px;'>
                    <div style='display: flex; justify-content: space-between; align-items: center;'>
                        <div>
                            <span style='font-size: 13px; font-weight: bold; color: #2d3748;'>{zone_name}</span>
                        </div>
                        <div style='text-align: right;'>
                            <div style='font-size: 20px; font-weight: bold; color: #dc2626;'>{outliers}</div>
                            <div style='font-size: 9px; color: #6b7280;'>outliers</div>
                        </div>
                    </div>
                    <div style='margin-top: 6px; display: flex; justify-content: space-between; align-items: center;'>
                        <div>
                            <span style='font-size: 10px; color: #6b7280;'>Events: </span>
                            <span style='font-size: 13px; font-weight: bold; color: {zone_color};'>{total_events:,}</span>
                        </div>
                        <div style='text-align: right;'>
                            <span style='font-size: 12px; font-weight: bold; color: #dc2626;'>{error_percentage:.1f}%</span>
                            <span style='font-size: 9px; color: #6b7280;'> errors</span>
                        </div>
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
            
            // Timestamps are already formatted as HH:MM in Python
            const formattedLabels = data.timestamps;
            
            Object.keys(data.zones).forEach((zone, idx) => {{
                const zoneNum = zone.replace('z', '');
                const chartId = `chart_cvr_${{zone}}`;
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
        
        # ========== ADDITIONAL CVR METRICS ==========
        
        # CVR Active Devices
        output += "<h3 style='margin: 20px 0 10px 0; color: #2d3748; font-size: 14px;'>📱 CVR Active Devices</h3>"
        
        devices_data = all_results.get('devices') or []
        if len(devices_data) > 0:
            devices_data = []
            for line in response_devices.text.strip().split('\n'):
                if line:
                    try:
                        result = json.loads(line)
                        if result.get("result") and result.get("preview") == False:
                            devices_data.append(result["result"])
                    except json.JSONDecodeError:
                        continue
            
            if len(devices_data) > 0:
                timestamps_devices = []
                device_counts = []
                
                for datapoint in devices_data:
                    timestamp_raw = datapoint.get("_time", "")
                    try:
                        from datetime import datetime
                        if timestamp_raw:
                            try:
                                ts_epoch = float(timestamp_raw)
                                dt = datetime.fromtimestamp(ts_epoch)
                            except:
                                dt = datetime.fromisoformat(timestamp_raw.replace(" GMT", "").replace("Z", ""))
                            timestamps_devices.append(dt.strftime("%H:%M"))
                        else:
                            timestamps_devices.append("")
                    except:
                        timestamps_devices.append(str(timestamp_raw))
                    
                    device_counts.append(int(datapoint.get("devices", 0)))
                
                total_devices = sum(device_counts)
                avg_devices = total_devices // len(device_counts) if device_counts else 0
                
                output += f"""
                <div style='background: white; padding: 16px; border-radius: 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin: 12px 0;'>
                    <div style='margin-bottom: 10px;'>
                        <span style='font-size: 13px; color: #2d3748;'>Total Active Devices: </span>
                        <span style='font-size: 20px; font-weight: bold; color: #9c27b0;'>{total_devices:,}</span>
                        <span style='font-size: 12px; color: #6b7280; margin-left: 12px;'>Avg: {avg_devices:,}</span>
                    </div>
                    <div style='position: relative; height: 200px;'>
                        <canvas id="chart_cvr_devices"></canvas>
                    </div>
                </div>
                """
                
                devices_data_json = json.dumps({
                    "timestamps": timestamps_devices,
                    "devices": device_counts
                })
                
                output += f"""
                <script>
                (function() {{
                    const data = {devices_data_json};
                    const canvas = document.getElementById('chart_cvr_devices');
                    
                    if (canvas) {{
                        new Chart(canvas, {{
                            type: 'line',
                            data: {{
                                labels: data.timestamps,
                                datasets: [{{
                                    label: 'Active Devices',
                                    data: data.devices,
                                    borderColor: '#9c27b0',
                                    backgroundColor: '#9c27b020',
                                    fill: true,
                                    tension: 0.4,
                                    borderWidth: 2,
                                    pointRadius: 3
                                }}]
                            }},
                            options: {{
                                responsive: true,
                                maintainAspectRatio: false,
                                plugins: {{
                                    legend: {{ display: false }}
                                }},
                                scales: {{
                                    x: {{
                                        ticks: {{
                                            maxRotation: 45,
                                            minRotation: 45,
                                            font: {{ size: 9 }}
                                        }}
                                    }},
                                    y: {{
                                        beginAtZero: true,
                                        ticks: {{
                                            callback: function(value) {{
                                                return value >= 1000 ? (value/1000).toFixed(1) + 'k' : value;
                                            }}
                                        }}
                                    }}
                                }}
                            }}
                        }});
                    }}
                }})();
                </script>
                """
            else:
                output += "<p style='color: #6b7280; font-size: 12px; margin: 12px 0;'>No device data found</p>"
        
        # CVR Connections Count
        output += "<h3 style='margin: 20px 0 10px 0; color: #2d3748; font-size: 14px;'>🔌 CVR Connections Count</h3>"
        
        connections_data = all_results.get('connections') or []
        if len(connections_data) > 0:
            connections_data = []
            for line in response_connections.text.strip().split('\n'):
                if line:
                    try:
                        result = json.loads(line)
                        if result.get("result") and result.get("preview") == False:
                            connections_data.append(result["result"])
                    except json.JSONDecodeError:
                        continue
            
            if len(connections_data) > 0:
                timestamps_conn = []
                connection_counts = []
                
                for datapoint in connections_data:
                    timestamp_raw = datapoint.get("_time", "")
                    try:
                        from datetime import datetime
                        if timestamp_raw:
                            try:
                                ts_epoch = float(timestamp_raw)
                                dt = datetime.fromtimestamp(ts_epoch)
                            except:
                                dt = datetime.fromisoformat(timestamp_raw.replace(" GMT", "").replace("Z", ""))
                            timestamps_conn.append(dt.strftime("%H:%M"))
                        else:
                            timestamps_conn.append("")
                    except:
                        timestamps_conn.append(str(timestamp_raw))
                    
                    connection_counts.append(int(datapoint.get("connections", 0)))
                
                total_connections = sum(connection_counts)
                
                output += f"""
                <div style='background: white; padding: 16px; border-radius: 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin: 12px 0;'>
                    <div style='margin-bottom: 10px;'>
                        <span style='font-size: 13px; color: #2d3748;'>Total Connections: </span>
                        <span style='font-size: 20px; font-weight: bold; color: #ff9800;'>{total_connections:,}</span>
                    </div>
                    <div style='position: relative; height: 180px;'>
                        <canvas id="chart_cvr_connections"></canvas>
                    </div>
                </div>
                """
                
                connections_data_json = json.dumps({
                    "timestamps": timestamps_conn,
                    "connections": connection_counts
                })
                
                output += f"""
                <script>
                (function() {{
                    const data = {connections_data_json};
                    const canvas = document.getElementById('chart_cvr_connections');
                    
                    if (canvas) {{
                        new Chart(canvas, {{
                            type: 'line',
                            data: {{
                                labels: data.timestamps,
                                datasets: [{{
                                    label: 'Connections',
                                    data: data.connections,
                                    borderColor: '#ff9800',
                                    backgroundColor: '#ff980020',
                                    fill: true,
                                    tension: 0.4,
                                    borderWidth: 2,
                                    pointRadius: 2
                                }}]
                            }},
                            options: {{
                                responsive: true,
                                maintainAspectRatio: false,
                                plugins: {{ legend: {{ display: false }} }},
                                scales: {{
                                    x: {{
                                        ticks: {{
                                            maxRotation: 45,
                                            minRotation: 45,
                                            font: {{ size: 9 }}
                                        }}
                                    }},
                                    y: {{ beginAtZero: true }}
                                }}
                            }}
                        }});
                    }}
                }})();
                </script>
                """
            else:
                output += "<p style='color: #6b7280; font-size: 12px; margin: 12px 0;'>No connection data found</p>"
        
        return output
        
    except Exception as e:
        print(f"❌ Error reading Splunk CVR dashboard: {str(e)}")
        import traceback
        traceback.print_exc()
        return f"<p>❌ Error reading Splunk CVR dashboard: {html.escape(str(e))}</p>"


def read_splunk_p0_adt_dashboard(query: str = "", timerange_hours: int = 4) -> str:
    """
    Shows the P0 ADT Streaming dashboard from Splunk with metrics and graphs.
    If a service name is provided, filters for that specific service.
    Args:
        query: Service name or search filter
        timerange_hours: Number of hours to look back (default: 4)
    """
    print("=" * 80)
    print("📊 Reading Splunk P0 ADT Dashboard")
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
    dashboard_url = "https://arlo.splunkcloud.com/en-US/app/search/p0_streaming_dashboard_pp"
    
    # Calculate timestamps for display
    current_time = int(time.time())
    from_time = current_time - (timerange_hours * 3600)
    timestamp_range_html = format_timestamp_range_splunk(from_time, current_time)
    
    # Dashboard header with orange/red theme
    output += f"""
    <div style='background: linear-gradient(135deg, #ff6f00 0%, #e65100 100%); 
                padding: 12px; 
                border-radius: 6px; 
                margin: 8px 0;
                color: white;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
        <h2 style='margin: 0 0 6px 0; color: white; font-size: 16px; font-weight: bold;'>📊 Splunk - P0 ADT Streaming Dashboard</h2>
        <p style='margin: 0 0 4px 0; font-size: 12px; opacity: 0.95;'>
            Real-time monitoring of P0 ADT streaming services
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
        # Build Splunk query for ADT
        earliest_time = f"-{timerange_hours}h@h"
        latest_time = "now"
        
        # PARALLEL OPTIMIZATION: Collect all queries and execute in parallel
        all_queries = {}
        
        # Query 1: Recording Uploads (main query)
        recording_query = f'''| tstats count where index=streaming_prod earliest=-{timerange_hours}h by _time, host span=1h
| rex field=host "-(?<zone>z[1-4])-"
| where isnotnull(zone)
| timechart span=1h sum(count) as events by zone
| fillnull value=0'''
        
        if query:
            recording_query = f'''| tstats count where index=streaming_prod earliest=-{timerange_hours}h by _time, host span=1h
| rex field=host "-(?<zone>z[1-4])-"
| where isnotnull(zone) AND (match(host, "(?i){query}") OR match(zone, "(?i){query}"))
| timechart span=1h sum(count) as events by zone
| fillnull value=0'''
        
        all_queries['recording'] = recording_query
        
        # Query 2: Active Servers
        all_queries['servers'] = f'''| tstats dc(host) as server_count where index=streaming_prod earliest=-{timerange_hours}h by _time, host span=1h
| rex field=host "-(?<zone>z[1-4])-"
| where isnotnull(zone)
| timechart span=1h dc(host) as servers by zone
| fillnull value=0'''
        
        # Query 3: JVM Crashes
        all_queries['jvm'] = f'''| search index=streaming_prod earliest=-{timerange_hours}h ("JVM" OR "OutOfMemoryError" OR "crash")
| rex field=host "-(?<zone>z[1-4])-"
| where isnotnull(zone)
| timechart span=1h count by zone
| fillnull value=0'''
        
        # Execute all queries in parallel
        all_results = execute_splunk_queries_parallel(all_queries, splunk_host, splunk_token, earliest_time, latest_time, max_workers=3)
        
        # Get recording uploads results
        timeseries_data = all_results.get('recording') or []

        if len(timeseries_data) == 0:
            output += f"""
            <div style='margin: 8px 0; padding: 12px; background-color: #fff3cd; border-left: 3px solid #ffc107; border-radius: 4px;'>
                <p style='margin: 0; font-size: 12px; color: #856404;'>⚠️ No ADT streaming data found{f" matching: {html.escape(query)}" if query else ""}</p>
            </div>
            """
            return output
        
        # Transform timeseries data into Chart.js format
        timestamps = []
        zone_data = {"z1": [], "z2": [], "z3": [], "z4": []}
        
        for datapoint in timeseries_data:
            # Get timestamp
            timestamp_raw = datapoint.get("_time", "")
            
            # Convert Splunk timestamp to HH:MM format
            try:
                from datetime import datetime
                if timestamp_raw:
                    # Parse as epoch or ISO format
                    try:
                        ts_epoch = float(timestamp_raw)
                        dt = datetime.fromtimestamp(ts_epoch)
                    except:
                        dt = datetime.fromisoformat(timestamp_raw.replace(" GMT", "").replace("Z", ""))
                    
                    # Format as HH:MM for display
                    timestamp_formatted = dt.strftime("%H:%M")
                    timestamps.append(timestamp_formatted)
                else:
                    timestamps.append("")
            except Exception as e:
                print(f"Error parsing timestamp {timestamp_raw}: {e}")
                timestamps.append(str(timestamp_raw))
            
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
            zone_name = f"Zone {zone_num} (ADT Uploads)"
            total_events = zone_totals.get(zone_key, 0)
            zone_color = zone_colors.get(zone_key, "#4e79a7")
            chart_id = f"chart_adt_{zone_key}"
            
            # Calculate outliers/errors (0 for now, can be enhanced later)
            outliers = 0
            error_percentage = 0.0 if total_events == 0 else (outliers / total_events * 100)
            
            output += f"""
            <div style='background: white; padding: 12px; border-radius: 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
                <div style='margin-bottom: 10px;'>
                    <div style='display: flex; justify-content: space-between; align-items: center;'>
                        <div>
                            <span style='font-size: 13px; font-weight: bold; color: #2d3748;'>{zone_name}</span>
                        </div>
                        <div style='text-align: right;'>
                            <div style='font-size: 20px; font-weight: bold; color: #dc2626;'>{outliers}</div>
                            <div style='font-size: 9px; color: #6b7280;'>outliers</div>
                        </div>
                    </div>
                    <div style='margin-top: 6px; display: flex; justify-content: space-between; align-items: center;'>
                        <div>
                            <span style='font-size: 10px; color: #6b7280;'>Events: </span>
                            <span style='font-size: 13px; font-weight: bold; color: {zone_color};'>{total_events:,}</span>
                        </div>
                        <div style='text-align: right;'>
                            <span style='font-size: 12px; font-weight: bold; color: #dc2626;'>{error_percentage:.1f}%</span>
                            <span style='font-size: 9px; color: #6b7280;'> errors</span>
                        </div>
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
            
            // Timestamps are already formatted as HH:MM in Python
            const formattedLabels = data.timestamps;
            
            Object.keys(data.zones).forEach((zone, idx) => {{
                const zoneNum = zone.replace('z', '');
                const chartId = `chart_adt_${{zone}}`;
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
        
        # ========== ADDITIONAL ADT METRICS ==========
        
        # Active Servers by Zone
        output += "<h3 style='margin: 20px 0 10px 0; color: #2d3748; font-size: 14px;'>📡 Active Servers</h3>"
        
        servers_data = all_results.get('servers') or []
        if len(servers_data) > 0:
            servers_data = []
            for line in response_servers.text.strip().split('\n'):
                if line:
                    try:
                        result = json.loads(line)
                        if result.get("result") and result.get("preview") == False:
                            servers_data.append(result["result"])
                    except json.JSONDecodeError:
                        continue
            
            if len(servers_data) > 0:
                timestamps_servers = []
                zone_servers = {"z1": [], "z2": [], "z3": [], "z4": []}
                
                for datapoint in servers_data:
                    timestamp_raw = datapoint.get("_time", "")
                    try:
                        from datetime import datetime
                        if timestamp_raw:
                            try:
                                ts_epoch = float(timestamp_raw)
                                dt = datetime.fromtimestamp(ts_epoch)
                            except:
                                dt = datetime.fromisoformat(timestamp_raw.replace(" GMT", "").replace("Z", ""))
                            timestamps_servers.append(dt.strftime("%H:%M"))
                        else:
                            timestamps_servers.append("")
                    except:
                        timestamps_servers.append(str(timestamp_raw))
                    
                    for zone in ["z1", "z2", "z3", "z4"]:
                        count = int(datapoint.get(zone, 0))
                        zone_servers[zone].append(count)
                
                zone_server_totals = {zone: sum(counts) for zone, counts in zone_servers.items()}
                
                output += "<div style='display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 12px 0;'>"
                
                zone_colors = {
                    "z1": "#4e79a7",
                    "z2": "#f28e2c",
                    "z3": "#e15759",
                    "z4": "#76b7b2"
                }
                
                for zone_num in ["1", "2", "3", "4"]:
                    zone_key = f"z{zone_num}"
                    total_servers = zone_server_totals.get(zone_key, 0)
                    zone_color = zone_colors.get(zone_key, "#4e79a7")
                    chart_id_servers = f"chart_adt_servers_{zone_key}"
                    
                    output += f"""
                    <div style='background: white; padding: 12px; border-radius: 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
                        <div style='margin-bottom: 10px; text-align: center;'>
                            <span style='font-size: 12px; font-weight: bold; color: #2d3748;'>Zone {zone_num}</span>
                            <div style='font-size: 20px; font-weight: bold; color: {zone_color}; margin-top: 4px;'>{total_servers}</div>
                            <div style='font-size: 9px; color: #6b7280;'>servers</div>
                        </div>
                        <div style='position: relative; height: 100px;'>
                            <canvas id="{chart_id_servers}"></canvas>
                        </div>
                    </div>
                    """
                
                output += "</div>"
                
                servers_data_json = json.dumps({
                    "timestamps": timestamps_servers,
                    "zones": zone_servers
                })
                
                output += f"""
                <script>
                (function() {{
                    const data = {servers_data_json};
                    const colors = {{
                        "z1": "#4e79a7",
                        "z2": "#f28e2c",
                        "z3": "#e15759",
                        "z4": "#76b7b2"
                    }};
                    
                    Object.keys(data.zones).forEach((zone) => {{
                        const chartId = `chart_adt_servers_${{zone}}`;
                        const canvas = document.getElementById(chartId);
                        
                        if (canvas) {{
                            new Chart(canvas, {{
                                type: 'line',
                                data: {{
                                    labels: data.timestamps,
                                    datasets: [{{
                                        data: data.zones[zone],
                                        borderColor: colors[zone],
                                        backgroundColor: colors[zone] + '20',
                                        fill: true,
                                        tension: 0.4,
                                        borderWidth: 2,
                                        pointRadius: 2
                                    }}]
                                }},
                                options: {{
                                    responsive: true,
                                    maintainAspectRatio: false,
                                    plugins: {{ legend: {{ display: false }} }},
                                    scales: {{
                                        x: {{ display: false }},
                                        y: {{ display: true, beginAtZero: true, ticks: {{ font: {{ size: 8 }} }} }}
                                    }}
                                }}
                            }});
                        }}
                    }});
                }})();
                </script>
                """
        
        # JVM Crashes
        output += "<h3 style='margin: 20px 0 10px 0; color: #2d3748; font-size: 14px;'>🔥 JVM Crash - Error Count</h3>"
        
        jvm_data = all_results.get('jvm') or []
        if len(jvm_data) > 0:
            jvm_data = []
            for line in response_jvm.text.strip().split('\n'):
                if line:
                    try:
                        result = json.loads(line)
                        if result.get("result") and result.get("preview") == False:
                            jvm_data.append(result["result"])
                    except json.JSONDecodeError:
                        continue
            
            if len(jvm_data) > 0:
                timestamps_jvm = []
                zone_jvm = {"z1": [], "z2": [], "z3": [], "z4": []}
                
                for datapoint in jvm_data:
                    timestamp_raw = datapoint.get("_time", "")
                    try:
                        from datetime import datetime
                        if timestamp_raw:
                            try:
                                ts_epoch = float(timestamp_raw)
                                dt = datetime.fromtimestamp(ts_epoch)
                            except:
                                dt = datetime.fromisoformat(timestamp_raw.replace(" GMT", "").replace("Z", ""))
                            timestamps_jvm.append(dt.strftime("%H:%M"))
                        else:
                            timestamps_jvm.append("")
                    except:
                        timestamps_jvm.append(str(timestamp_raw))
                    
                    for zone in ["z1", "z2", "z3", "z4"]:
                        count = int(datapoint.get(zone, 0))
                        zone_jvm[zone].append(count)
                
                zone_jvm_totals = {zone: sum(counts) for zone, counts in zone_jvm.items()}
                total_jvm_errors = sum(zone_jvm_totals.values())
                
                output += f"""
                <div style='background: white; padding: 16px; border-radius: 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin: 12px 0;'>
                    <div style='margin-bottom: 10px;'>
                        <span style='font-size: 13px; font-weight: bold; color: #2d3748;'>Total JVM Errors: </span>
                        <span style='font-size: 20px; font-weight: bold; color: #dc2626;'>{total_jvm_errors}</span>
                    </div>
                    <div style='position: relative; height: 200px;'>
                        <canvas id="chart_adt_jvm"></canvas>
                    </div>
                </div>
                """
                
                jvm_data_json = json.dumps({
                    "timestamps": timestamps_jvm,
                    "zones": zone_jvm
                })
                
                output += f"""
                <script>
                (function() {{
                    const data = {jvm_data_json};
                    const colors = {{
                        "z1": "#4e79a7",
                        "z2": "#f28e2c",
                        "z3": "#e15759",
                        "z4": "#76b7b2"
                    }};
                    
                    const datasets = [];
                    Object.keys(data.zones).forEach((zone) => {{
                        datasets.push({{
                            label: 'Zone ' + zone.replace('z', ''),
                            data: data.zones[zone],
                            borderColor: colors[zone],
                            backgroundColor: colors[zone] + '80',
                            borderWidth: 2
                        }});
                    }});
                    
                    const canvas = document.getElementById('chart_adt_jvm');
                    if (canvas) {{
                        new Chart(canvas, {{
                            type: 'bar',
                            data: {{
                                labels: data.timestamps,
                                datasets: datasets
                            }},
                            options: {{
                                responsive: true,
                                maintainAspectRatio: false,
                                plugins: {{
                                    legend: {{ display: true, position: 'top' }}
                                }},
                                scales: {{
                                    x: {{ stacked: false }},
                                    y: {{ stacked: false, beginAtZero: true }}
                                }}
                            }}
                        }});
                    }}
                }})();
                </script>
                """
            else:
                output += "<p style='color: #6b7280; font-size: 12px; margin: 12px 0;'>✅ No JVM errors found</p>"
        
        return output
        
    except Exception as e:
        print(f"❌ Error reading Splunk ADT dashboard: {str(e)}")
        import traceback
        traceback.print_exc()
        return f"<p>❌ Error reading Splunk ADT dashboard: {html.escape(str(e))}</p>"
