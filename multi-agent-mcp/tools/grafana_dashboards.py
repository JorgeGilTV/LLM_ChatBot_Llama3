"""
Grafana Dashboard Integration
Fetch and display metrics from Grafana monitoring dashboards
"""

import os
import requests
from datetime import datetime, timedelta

# Grafana configuration
GRAFANA_URL = os.getenv('GRAFANA_URL', 'https://monitoring-prod.arlocloud.com')
GRAFANA_API_KEY = os.getenv('GRAFANA_API_KEY', '')  # Optional, for API access

# Dashboard configurations
DASHBOARDS = {
    'dns_mapper_z4': {
        'uid': 'dbzOsas7s3Zk',
        'name': 'DNS Mapper - PROD (hmsstreaming, cvrstreaming) IP Usage z4',
        'url': f'{GRAFANA_URL}/d/dbzOsas7s3Zk/dns-mapper-prod-hmsstreaming-cvrstreaming-ip-usage-z4?orgId=1',
        'description': 'Monitor DNS mapper IP usage for streaming services (Zone 4)',
        'zone': 'z4',
        'services': ['hmsstreaming', 'cvrstreaming']
    },
    'savant_z2': {
        'uid': '9Yk1U5_Nk',
        'name': 'Savant z2 - Harlem',
        'url': f'{GRAFANA_URL}/d/9Yk1U5_Nk/savant-z2-harlem?orgId=1',
        'description': 'Monitor Savant infrastructure in Harlem datacenter (Zone 2)',
        'zone': 'z2',
        'services': ['savant']
    }
}


def get_grafana_dns_mapper(query="", timerange=4):
    """
    Get DNS Mapper dashboard information from Grafana (Zone 4)
    
    Args:
        query: Search query (optional)
        timerange: Time range in hours (default: 4)
    
    Returns:
        HTML formatted dashboard information
    """
    dashboard = DASHBOARDS['dns_mapper_z4']
    
    # Calculate time range
    now = datetime.now()
    from_time = now - timedelta(hours=timerange)
    
    # Build dashboard URL with time range
    dashboard_url = f"{dashboard['url']}&from={int(from_time.timestamp() * 1000)}&to={int(now.timestamp() * 1000)}"
    
    # Build embedded iframe URL (kiosk mode for clean display)
    iframe_url = f"{dashboard_url}&kiosk=tv"
    
    # Try to fetch dashboard data if API key is available
    dashboard_data = None
    if GRAFANA_API_KEY:
        try:
            headers = {
                'Authorization': f'Bearer {GRAFANA_API_KEY}',
                'Content-Type': 'application/json'
            }
            api_url = f"{GRAFANA_URL}/api/dashboards/uid/{dashboard['uid']}"
            response = requests.get(api_url, headers=headers, timeout=10)
            if response.status_code == 200:
                dashboard_data = response.json()
        except Exception as e:
            print(f"⚠️  Could not fetch dashboard data: {e}")
    
    # Build HTML response
    html = f"""
    <div style='background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 16px; border-radius: 8px; margin: 12px 0; color: white; box-shadow: 0 4px 6px rgba(0,0,0,0.1);'>
        <h2 style='margin: 0 0 8px 0; color: white; font-size: 18px; font-weight: bold;'>
            📊 Grafana - {dashboard['name']}
        </h2>
        <p style='margin: 0; font-size: 13px; opacity: 0.95;'>
            {dashboard['description']}
        </p>
    </div>
    
    <div style='background-color: #f7fafc; padding: 16px; margin: 8px 0; border-radius: 6px; border-left: 4px solid #667eea;'>
        <div style='margin-bottom: 12px;'>
            <strong style='color: #2d3748;'>⏰ Time Range:</strong> 
            <span style='color: #4a5568;'>Last {timerange} hours</span>
        </div>
        
        <div style='margin-bottom: 12px;'>
            <strong style='color: #2d3748;'>🔗 Dashboard URL:</strong><br>
            <a href='{dashboard_url}' target='_blank' style='color: #667eea; text-decoration: none; word-break: break-all;'>
                {dashboard_url}
            </a>
        </div>
        
        <div style='background-color: #edf2f7; padding: 12px; border-radius: 4px; margin-top: 12px;'>
            <p style='margin: 0 0 8px 0; color: #2d3748; font-weight: bold;'>📍 Monitored Services:</p>
            <ul style='margin: 8px 0; padding-left: 20px; color: #4a5568;'>
                <li><strong>hmsstreaming</strong> - HMS Streaming Service IP Usage</li>
                <li><strong>cvrstreaming</strong> - CVR Streaming Service IP Usage</li>
                <li><strong>Zone:</strong> z4 (Production)</li>
            </ul>
        </div>
    """
    
    # Add panel information if available from API
    if dashboard_data and 'dashboard' in dashboard_data:
        panels = dashboard_data['dashboard'].get('panels', [])
        if panels:
            html += """
            <div style='margin-top: 12px; background-color: #e6fffa; padding: 12px; border-radius: 4px; border-left: 3px solid #38b2ac;'>
                <p style='margin: 0 0 8px 0; color: #234e52; font-weight: bold;'>📈 Available Metrics:</p>
                <ul style='margin: 8px 0; padding-left: 20px; color: #2c7a7b;'>
            """
            for panel in panels[:10]:  # Show first 10 panels
                panel_title = panel.get('title', 'Untitled Panel')
                html += f"<li>{panel_title}</li>"
            
            if len(panels) > 10:
                html += f"<li><em>... and {len(panels) - 10} more panels</em></li>"
            
            html += """
                </ul>
            </div>
            """
    
    # Add button to open in Grafana (avoids iframe auth issues)
    html += f"""
        <div style='margin-top: 16px; background-color: white; padding: 16px; border-radius: 6px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);'>
            <div style='text-align: center;'>
                <a href='{dashboard_url}' target='_blank' style='text-decoration: none;'>
                    <button style='
                        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                        color: white;
                        border: none;
                        padding: 16px 32px;
                        font-size: 16px;
                        font-weight: bold;
                        border-radius: 8px;
                        cursor: pointer;
                        box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
                        transition: transform 0.2s, box-shadow 0.2s;
                    ' onmouseover='this.style.transform="translateY(-2px)"; this.style.boxShadow="0 6px 16px rgba(102, 126, 234, 0.6)"' 
                       onmouseout='this.style.transform="translateY(0)"; this.style.boxShadow="0 4px 12px rgba(102, 126, 234, 0.4)"'>
                        📊 Open Dashboard in Grafana
                    </button>
                </a>
                <p style='margin: 12px 0 0 0; color: #4a5568; font-size: 13px;'>
                    Click the button to view interactive graphs with full Grafana functionality
                </p>
            </div>
        </div>
        
        <div style='margin-top: 12px; background-color: #e6fffa; padding: 12px; border-radius: 4px; border-left: 3px solid #38b2ac;'>
            <p style='margin: 0; color: #234e52;'>
                <strong>✨ Features in Grafana:</strong><br>
                • Real-time data with auto-refresh<br>
                • Interactive graphs (zoom, hover, legends)<br>
                • Multiple panels and metrics<br>
                • Export and share capabilities
            </p>
        </div>
    </div>
    """
    
    return html


def get_grafana_savant_z2(query="", timerange=4):
    """
    Get Savant z2 (Harlem) dashboard information from Grafana
    
    Args:
        query: Search query (optional)
        timerange: Time range in hours (default: 4)
    
    Returns:
        HTML formatted dashboard information
    """
    dashboard = DASHBOARDS['savant_z2']
    
    # Calculate time range
    now = datetime.now()
    from_time = now - timedelta(hours=timerange)
    
    # Build dashboard URL with time range
    dashboard_url = f"{dashboard['url']}&from={int(from_time.timestamp() * 1000)}&to={int(now.timestamp() * 1000)}&refresh=5s"
    
    # Build embedded iframe URL (kiosk mode for clean display)
    iframe_url = f"{dashboard_url}&kiosk=tv"
    
    # Try to fetch dashboard data if API key is available
    dashboard_data = None
    if GRAFANA_API_KEY:
        try:
            headers = {
                'Authorization': f'Bearer {GRAFANA_API_KEY}',
                'Content-Type': 'application/json'
            }
            api_url = f"{GRAFANA_URL}/api/dashboards/uid/{dashboard['uid']}"
            response = requests.get(api_url, headers=headers, timeout=10)
            if response.status_code == 200:
                dashboard_data = response.json()
        except Exception as e:
            print(f"⚠️  Could not fetch dashboard data: {e}")
    
    # Build HTML response
    html = f"""
    <div style='background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%); padding: 16px; border-radius: 8px; margin: 12px 0; color: white; box-shadow: 0 4px 6px rgba(0,0,0,0.1);'>
        <h2 style='margin: 0 0 8px 0; color: white; font-size: 18px; font-weight: bold;'>
            📊 Grafana - {dashboard['name']}
        </h2>
        <p style='margin: 0; font-size: 13px; opacity: 0.95;'>
            {dashboard['description']}
        </p>
    </div>
    
    <div style='background-color: #f7fafc; padding: 16px; margin: 8px 0; border-radius: 6px; border-left: 4px solid #f59e0b;'>
        <div style='margin-bottom: 12px;'>
            <strong style='color: #2d3748;'>⏰ Time Range:</strong> 
            <span style='color: #4a5568;'>Last {timerange} hours</span>
        </div>
        
        <div style='margin-bottom: 12px;'>
            <strong style='color: #2d3748;'>🔗 Dashboard URL:</strong><br>
            <a href='{dashboard_url}' target='_blank' style='color: #f59e0b; text-decoration: none; word-break: break-all;'>
                {dashboard_url}
            </a>
        </div>
        
        <div style='background-color: #edf2f7; padding: 12px; border-radius: 4px; margin-top: 12px;'>
            <p style='margin: 0 0 8px 0; color: #2d3748; font-weight: bold;'>📍 Monitored Infrastructure:</p>
            <ul style='margin: 8px 0; padding-left: 20px; color: #4a5568;'>
                <li><strong>Service:</strong> Savant</li>
                <li><strong>Datacenter:</strong> Harlem</li>
                <li><strong>Zone:</strong> z2 (Production)</li>
                <li><strong>Refresh:</strong> Auto-refresh every 5 seconds</li>
            </ul>
        </div>
    """
    
    # Add panel information if available from API
    if dashboard_data and 'dashboard' in dashboard_data:
        panels = dashboard_data['dashboard'].get('panels', [])
        if panels:
            html += """
            <div style='margin-top: 12px; background-color: #e6fffa; padding: 12px; border-radius: 4px; border-left: 3px solid #38b2ac;'>
                <p style='margin: 0 0 8px 0; color: #234e52; font-weight: bold;'>📈 Available Metrics:</p>
                <ul style='margin: 8px 0; padding-left: 20px; color: #2c7a7b;'>
            """
            for panel in panels[:10]:  # Show first 10 panels
                panel_title = panel.get('title', 'Untitled Panel')
                html += f"<li>{panel_title}</li>"
            
            if len(panels) > 10:
                html += f"<li><em>... and {len(panels) - 10} more panels</em></li>"
            
            html += """
                </ul>
            </div>
            """
    
    # Add button to open in Grafana (avoids iframe auth issues)
    html += f"""
        <div style='margin-top: 16px; background-color: white; padding: 16px; border-radius: 6px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);'>
            <div style='text-align: center;'>
                <a href='{dashboard_url}' target='_blank' style='text-decoration: none;'>
                    <button style='
                        background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%);
                        color: white;
                        border: none;
                        padding: 16px 32px;
                        font-size: 16px;
                        font-weight: bold;
                        border-radius: 8px;
                        cursor: pointer;
                        box-shadow: 0 4px 12px rgba(245, 158, 11, 0.4);
                        transition: transform 0.2s, box-shadow 0.2s;
                    ' onmouseover='this.style.transform="translateY(-2px)"; this.style.boxShadow="0 6px 16px rgba(245, 158, 11, 0.6)"' 
                       onmouseout='this.style.transform="translateY(0)"; this.style.boxShadow="0 4px 12px rgba(245, 158, 11, 0.4)"'>
                        📊 Open Dashboard in Grafana
                    </button>
                </a>
                <p style='margin: 12px 0 0 0; color: #4a5568; font-size: 13px;'>
                    Click the button to view interactive graphs with full Grafana functionality
                </p>
            </div>
        </div>
        
        <div style='margin-top: 12px; background-color: #e6fffa; padding: 12px; border-radius: 4px; border-left: 3px solid #38b2ac;'>
            <p style='margin: 0; color: #234e52;'>
                <strong>✨ Features in Grafana:</strong><br>
                • Real-time data with auto-refresh (5s)<br>
                • Interactive graphs (zoom, hover, legends)<br>
                • Multiple panels and metrics<br>
                • Export and share capabilities
            </p>
        </div>
    </div>
    """
    
    return html


def get_grafana_dashboard_list():
    """
    Get list of all available Grafana dashboards
    
    Returns:
        HTML formatted list of dashboards
    """
    html = """
    <div style='background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 16px; border-radius: 8px; margin: 12px 0; color: white; box-shadow: 0 4px 6px rgba(0,0,0,0.1);'>
        <h2 style='margin: 0 0 8px 0; color: white; font-size: 18px; font-weight: bold;'>
            📊 Available Grafana Dashboards
        </h2>
    </div>
    
    <div style='background-color: #f7fafc; padding: 16px; margin: 8px 0; border-radius: 6px;'>
    """
    
    for key, dashboard in DASHBOARDS.items():
        html += f"""
        <div style='background-color: white; padding: 12px; margin-bottom: 12px; border-radius: 4px; border-left: 4px solid #667eea; box-shadow: 0 1px 3px rgba(0,0,0,0.1);'>
            <h3 style='margin: 0 0 8px 0; color: #2d3748; font-size: 16px;'>{dashboard['name']}</h3>
            <p style='margin: 0 0 8px 0; color: #4a5568; font-size: 13px;'>{dashboard['description']}</p>
            <a href='{dashboard['url']}' target='_blank' style='color: #667eea; text-decoration: none; font-size: 13px;'>
                🔗 Open Dashboard
            </a>
        </div>
        """
    
    html += "</div>"
    return html


# Export functions
__all__ = ['get_grafana_dns_mapper', 'get_grafana_savant_z2', 'get_grafana_dashboard_list']
