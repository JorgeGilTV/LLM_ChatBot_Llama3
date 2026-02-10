import os
import html
import requests
import json
import time
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()


def format_timestamp_range_splunk(from_timestamp: int, to_timestamp: int) -> str:
    """Format timestamp range into readable format with date and time"""
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


def query_splunk(search_query: str, timerange_hours: int, public_ip: str = "Unknown") -> dict:
    """
    Helper function to query Splunk API
    Returns: {"success": bool, "data": list, "error": str}
    """
    splunk_host = os.getenv("SPLUNK_HOST", "arlo.splunkcloud.com")
    splunk_token = os.getenv("SPLUNK_TOKEN")
    
    if not splunk_token:
        return {"success": False, "error": "SPLUNK_TOKEN not configured", "data": []}
    
    headers = {
        "Authorization": f"Bearer {splunk_token}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    search_url = f"https://{splunk_host}:8089/services/search/jobs/export"
    earliest_time = f"-{timerange_hours}h@h"
    data = {
        "search": search_query,
        "earliest_time": earliest_time,
        "latest_time": "now",
        "output_mode": "json"
    }
    
    try:
        response = requests.post(search_url, headers=headers, data=data, verify=True, timeout=(10, 120))
        
        if response.status_code != 200:
            return {
                "success": False,
                "error": f"Status {response.status_code}: {response.text[:200]}",
                "data": [],
                "public_ip": public_ip
            }
        
        # Parse results
        timeseries_data = []
        for line in response.text.strip().split('\n'):
            if line:
                try:
                    result = json.loads(line)
                    if result.get("result") and result.get("preview") == False:
                        timeseries_data.append(result["result"])
                except json.JSONDecodeError:
                    continue
        
        return {"success": True, "data": timeseries_data, "error": None}
        
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}


def format_timestamps(timeseries_data: list) -> list:
    """Convert Splunk timestamps to HH:MM format"""
    timestamps = []
    for datapoint in timeseries_data:
        timestamp_raw = datapoint.get("_time", "")
        try:
            if timestamp_raw:
                try:
                    ts_epoch = float(timestamp_raw)
                    dt = datetime.fromtimestamp(ts_epoch)
                except:
                    dt = datetime.fromisoformat(timestamp_raw.replace(" GMT", "").replace("Z", ""))
                timestamps.append(dt.strftime("%H:%M"))
            else:
                timestamps.append("")
        except Exception as e:
            print(f"Error parsing timestamp {timestamp_raw}: {e}")
            timestamps.append(str(timestamp_raw))
    return timestamps


def generate_chart_html(chart_id: str, title: str, color: str, total_value: int, unit: str = "events") -> str:
    """Generate HTML for a single chart card"""
    return f"""
    <div style='background: white; padding: 12px; border-radius: 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
        <div style='margin-bottom: 10px;'>
            <div style='display: flex; justify-content: space-between; align-items: center;'>
                <div>
                    <span style='font-size: 13px; font-weight: bold; color: #2d3748;'>{title}</span>
                </div>
                <div style='text-align: right;'>
                    <span style='font-size: 10px; color: #6b7280;'>Total: </span>
                    <span style='font-size: 13px; font-weight: bold; color: {color};'>{total_value:,} {unit}</span>
                </div>
            </div>
        </div>
        <div style='position: relative; height: 150px;'>
            <canvas id="{chart_id}"></canvas>
        </div>
    </div>
    """


def generate_chart_script(chart_id: str, labels: list, data: list, color: str, chart_type: str = "line") -> str:
    """Generate Chart.js script for a single chart"""
    chart_data = {
        "labels": labels,
        "data": data
    }
    
    chart_data_json = json.dumps(chart_data)
    
    return f"""
    <script>
    (function() {{
        const chartData = {chart_data_json};
        const canvas = document.getElementById('{chart_id}');
        
        if (canvas) {{
            new Chart(canvas, {{
                type: '{chart_type}',
                data: {{
                    labels: chartData.labels,
                    datasets: [{{
                        data: chartData.data,
                        borderColor: '{color}',
                        backgroundColor: '{color}20',
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
                            ticks: {{ 
                                maxRotation: 45,
                                minRotation: 45,
                                font: {{ size: 9 }},
                                maxTicksLimit: 12
                            }},
                            grid: {{ display: true, color: 'rgba(0, 0, 0, 0.05)' }}
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
                            grid: {{ display: true, color: 'rgba(0, 0, 0, 0.05)' }}
                        }}
                    }}
                }}
            }});
        }}
    }})();
    </script>
    """


# Query templates for different metrics
QUERIES = {
    "recording_uploads": '''| tstats count where index=streaming_prod earliest=-{hours}h by _time, host span=1h
| rex field=host "-(?<zone>z[1-4])-"
| where isnotnull(zone)
| timechart span=1h sum(count) as events by zone
| fillnull value=0''',
    
    "active_servers": '''| tstats dc(host) as server_count where index=streaming_prod earliest=-{hours}h by _time, host span=1h
| rex field=host "-(?<zone>z[1-4])-"
| where isnotnull(zone)
| timechart span=1h dc(host) as servers by zone
| fillnull value=0''',
    
    "redistribution_failures": '''| search index=streaming_prod earliest=-{hours}h "SmartRedistribution" "failure"
| rex field=host "-(?<zone>z[1-4])-"
| where isnotnull(zone)
| timechart span=1h count by zone
| fillnull value=0''',
    
    "jvm_crashes": '''| search index=streaming_prod earliest=-{hours}h "JVM" ("crash" OR "OutOfMemoryError")
| rex field=host "-(?<zone>z[1-4])-"
| where isnotnull(zone)
| timechart span=1h count by zone
| fillnull value=0''',
    
    "s3p_recording_percentage": '''| search index=streaming_prod earliest=-{hours}h sourcetype="s3p:recording"
| timechart span=1h avg(percentage) as avg_percentage
| fillnull value=0''',
    
    "cvr_active_devices": '''| tstats dc(device_id) as device_count where index=streaming_prod earliest=-{hours}h by _time span=1h
| timechart span=1h sum(device_count) as devices
| fillnull value=0''',
    
    "cvr_success_rate": '''| search index=streaming_prod earliest=-{hours}h "CVR" status=*
| rex field=host "-(?<zone>z[1-4])-"
| where isnotnull(zone)
| eval is_success=if(status=="success",1,0)
| timechart span=1h avg(is_success)*100 as success_rate by zone
| fillnull value=0''',
    
    "cvr_connections": '''| search index=streaming_prod earliest=-{hours}h "CVR" "connection"
| timechart span=1h count as connections
| fillnull value=0''',
}
