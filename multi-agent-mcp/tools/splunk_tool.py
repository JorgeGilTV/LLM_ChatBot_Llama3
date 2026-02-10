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
        # Sample data (replace with actual Splunk API calls)
        sample_services = [
            {"service": "streaming-video-service", "events": 15420, "total_errors": 23, "error_rate": 0.15, "avg_latency": 45.2, "max_latency": 156.8},
            {"service": "streaming-audio-service", "events": 8932, "total_errors": 5, "error_rate": 0.06, "avg_latency": 32.1, "max_latency": 98.3},
            {"service": "streaming-metadata-service", "events": 24567, "total_errors": 0, "error_rate": 0.0, "avg_latency": 12.5, "max_latency": 45.2}
        ]
        
        if query:
            sample_services = [s for s in sample_services if query.lower() in s["service"].lower()]
        
        if len(sample_services) == 0:
            output += f"<p style='margin: 8px 0; padding: 6px; background-color: #fff3cd; border-radius: 4px; color: #856404;'>⚠️ No services found matching: {html.escape(query)}</p>"
            return output
        
        output += f"""
        <div style='background-color: #ffffff; padding: 6px; border-radius: 4px; margin: 6px 0; box-shadow: 0 1px 2px rgba(0,0,0,0.06);'>
            <div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; padding-bottom: 4px; border-bottom: 1px solid #00796b;'>
                <h3 style='margin: 0; color: #00796b; font-size: 14px;'>📊 Streaming Services ({len(sample_services)})</h3>
                <div style='text-align: right;'>
                    <div style='font-size: 11px; color: #666;'>{time.strftime('%H:%M:%S')}</div>
                    <div style='font-size: 10px; color: #999;'>Last {timerange_hours}h</div>
                </div>
            </div>
            <div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px;'>
        """
        
        for service_data in sample_services:
            service_name = service_data["service"]
            events = service_data["events"]
            total_errors = service_data["total_errors"]
            error_rate = service_data["error_rate"]
            avg_latency = service_data["avg_latency"]
            max_latency = service_data["max_latency"]
            
            status_color = "#4caf50"
            status_icon = "✅"
            if total_errors > 10:
                status_color = "#ff5252"
                status_icon = "🔴"
            elif total_errors > 0:
                status_color = "#ff9800"
                status_icon = "⚠️"
            
            output += f"""
            <div style='background-color: #f9fafb; padding: 8px; border-radius: 4px; border-left: 3px solid {status_color}; box-shadow: 0 1px 2px rgba(0,0,0,0.04);'>
                <div style='margin-bottom: 8px;'>
                    <div style='display: flex; justify-content: space-between; align-items: center;'>
                        <span style='font-size: 12px; font-weight: bold; color: #2d3748;'>{html.escape(service_name)}</span>
                        <span style='font-size: 14px;'>{status_icon}</span>
                    </div>
                </div>
                <div style='display: grid; grid-template-columns: repeat(2, 1fr); gap: 4px; margin-bottom: 6px;'>
                    <div style='background: white; padding: 4px; border-radius: 2px; border: 1px solid #e5e7eb;'>
                        <div style='font-size: 9px; color: #6b7280;'>Events</div>
                        <div style='font-size: 13px; font-weight: bold; color: #1890ff;'>{events:,}</div>
                    </div>
                    <div style='background: white; padding: 4px; border-radius: 2px; border: 1px solid #e5e7eb;'>
                        <div style='font-size: 9px; color: #6b7280;'>Errors</div>
                        <div style='font-size: 13px; font-weight: bold; color: {status_color};'>{total_errors} ({error_rate}%)</div>
                    </div>
                    <div style='background: white; padding: 4px; border-radius: 2px; border: 1px solid #e5e7eb;'>
                        <div style='font-size: 9px; color: #6b7280;'>Avg Latency</div>
                        <div style='font-size: 13px; font-weight: bold; color: #52c41a;'>{avg_latency:.1f}ms</div>
                    </div>
                    <div style='background: white; padding: 4px; border-radius: 2px; border: 1px solid #e5e7eb;'>
                        <div style='font-size: 9px; color: #6b7280;'>Max Latency</div>
                        <div style='font-size: 13px; font-weight: bold; color: #f6ad55;'>{max_latency:.1f}ms</div>
                    </div>
                </div>
                <div style='text-align: center; padding-top: 4px; border-top: 1px solid #e5e7eb;'>
                    <a href='{dashboard_url}?form.service={html.escape(service_name)}' target='_blank' style='display: inline-block; padding: 3px 8px; background-color: #00796b; color: white; text-decoration: none; border-radius: 2px; font-size: 10px; font-weight: 600;'>View Details →</a>
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
