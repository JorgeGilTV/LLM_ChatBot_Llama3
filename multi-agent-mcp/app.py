
from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
import time
import sys
import os
import logging
import io
import base64
import html
from datetime import datetime

# Load secure embedded credentials (for compiled executable)
try:
    from config_secure import load_secure_env
    load_secure_env()
    print("✅ Loaded embedded credentials")
except ImportError:
    # If not compiled, will use .env file below
    print("ℹ️  Using .env file for credentials")
    pass
try:
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False
    print("⚠️ python-docx not installed. Download feature will be disabled.")

# Importar tools existentes
from tools.gemini_tool import ask_gemini
#from tools.llama_tool import ask_llama
from tools.confluence_tool import confluence_search
#from tools.tickets_tool import read_tickets
from tools.history_tool import add_to_history, get_history
#from tools.suggestions_tool import AI_suggestions

# Import ask_arlochat (auto-detects best mode: SDK async or HTTP fallback)
try:
    from tools.ask_arlochat import ask_arlo, MCP_SDK_AVAILABLE
    ARLOCHAT_AVAILABLE = True
    if MCP_SDK_AVAILABLE:
        print("✅ ArloChat MCP loaded (SDK Async mode - Python 3.10+)")
    else:
        print("✅ ArloChat MCP loaded (HTTP Fallback mode - Python 3.9+)")
except ImportError as e:
    print(f"⚠️  WARNING: ArloChat import failed: {e}")
    ARLOCHAT_AVAILABLE = False
    MCP_SDK_AVAILABLE = False
    
    # Create a placeholder function
    def ask_arlo(question: str = "") -> str:
        return f"""
        <div style='background-color: #fee; padding: 12px; border-left: 4px solid #f56565; border-radius: 4px; margin: 8px 0;'>
            <p style='margin: 0; color: #c53030;'>
                ❌ <strong>ArloChat module failed to load</strong><br><br>
                Error: {html.escape(str(e))}<br><br>
                Please check the logs for more details.
            </p>
        </div>
        """

from tools.service_owners import service_owners_search
from tools.noc_kt import noc_kt_search
from tools.read_arlo_status import read_arlo_status
from tools.oncall_support import confluence_oncall_today
from tools.read_versions import read_versions
from tools.datadog_dashboards import read_datadog_dashboards, read_datadog_errors_only, read_datadog_adt, read_datadog_adt_errors_only, read_datadog_all_errors, read_datadog_failed_pods, read_datadog_403_errors
from tools.splunk_tool import read_splunk_p0_dashboard, read_splunk_p0_cvr_dashboard, read_splunk_p0_adt_dashboard
from tools.pagerduty_tool import get_pagerduty_incidents
from tools.pagerduty_analytics import get_pagerduty_analytics
from tools.pagerduty_insights import get_pagerduty_insights

# 📋 Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("agent_tool_logs.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

# ✅ Tools
TOOLS = {
    #"Wiki": {"description": "Read workarounds from Confluece", "function": read_tickets},
    "Wiki": {"description": "Read documents from Arlo confluence", "function": confluence_search},
    "Owners": {"description": "Verfiy who is owner of all services","function": service_owners_search},
    "Arlo_Versions": {"description": "Read version information from versions.arlocloud.com", "function": read_versions},
    "DD_Red_Metrics": {"description": "List and search Datadog dashboards", "function": read_datadog_dashboards},
    "DD_Red_ADT": {"description": "Show RED Metrics - ADT dashboard from Datadog", "function": read_datadog_adt},
    "DD_Errors": {"description": "Show services with errors > 0 from RED Metrics & ADT dashboards", "function": read_datadog_all_errors},
    "DD_Failed_Pods": {"description": "Monitor Kubernetes pods with failures (ImagePullBackOff, CrashLoop) causing 4xx errors", "function": read_datadog_failed_pods},
    "DD_403_Errors": {"description": "Monitor 403 Forbidden errors from APM traces (Artifactory, authentication issues)", "function": read_datadog_403_errors},
    "P0_Streaming": {"description": "Show P0 Streaming dashboard from Splunk", "function": read_splunk_p0_dashboard},
    "P0_CVR_Streaming": {"description": "Show P0 CVR Streaming dashboard from Splunk", "function": read_splunk_p0_cvr_dashboard},
    "P0_ADT_Streaming": {"description": "Show P0 ADT Streaming dashboard from Splunk", "function": read_splunk_p0_adt_dashboard},
    "Holiday_Oncall": {"description": "Verify status in ARLO webpage", "function": confluence_oncall_today},
    "PagerDuty": {"description": "Get active incidents from PagerDuty", "function": get_pagerduty_incidents},
    "PagerDuty_Dashboards": {"description": "Show PagerDuty analytics with charts and metrics", "function": get_pagerduty_analytics},
    "PagerDuty_Insights": {"description": "Show incident activity insights and trends", "function": get_pagerduty_insights},
    "Ask_ARLOCHAT": {"description": "Ask ARLO CHAT via MCP", "function": ask_arlo}
}
registered_tools = [(name, tool["description"]) for name, tool in TOOLS.items()]

# ✅ Flask App
flask_app = Flask(__name__, template_folder='templates')
CORS(flask_app)

alerts_db = []  # {id, text, priority, ack, cause}

def classify_alert(text):
    text_lower = text.lower()
    if any(k in text_lower for k in ['Sev1', 'Sev0']):
        return 'Alta'
    if any(k in text_lower for k in ['Sev3','Sev2']):
        return 'Media'
    return 'Baja'


def identify_cause(text):
    try:
        # Ejecutar las funciones reales con el texto del alerta
        #wiki_info = TOOLS["Wiki"]["function"](text)
        confluence_info = TOOLS["Wiki"]["function"](text)
        suggestion = TOOLS["Suggestions"]["function"](text)

        return f""" 
        <div>
            <h4>Sugested root cause:</h4>{suggestion}
            <br><h4>Confluence:</h4>{confluence_info}
        </div>
        """
    except Exception as e:
        return f"<pre>Error identifying root cause: {e}</pre>"



@flask_app.route('/')
def index():
    return send_from_directory('templates', 'index.html')

@flask_app.route('/api/history')
def api_history():
    return jsonify(get_history())

@flask_app.route('/api/tools')
def api_tools():
    return jsonify([{'name': name, 'desc': desc} for name, desc in registered_tools])

@flask_app.route('/api/run', methods=['POST'])
def api_run():
    data = request.json
    input_text = data.get('input', '')
    selected_tools = data.get('tools', []) or ['Suggestions']
    timerange = data.get('timerange', 4)  # Default to 4 hours
    results = []
    tabs = []
    start = time.time()
    
    for idx, tool_name in enumerate(selected_tools):
        func = TOOLS.get(tool_name, {}).get('function')
        print(func)
        if not func:
            results.append(f"<pre>No tool found for {tool_name}</pre>")
            continue
        try:
            # Pass timerange to Datadog and Splunk tools
            if tool_name in ['DD_Red_Metrics', 'DD_Errors', 'DD_Red_ADT', 'DD_Failed_Pods', 'DD_403_Errors', 'P0_Streaming', 'P0_CVR_Streaming', 'P0_ADT_Streaming']:
                res = func(input_text, timerange)
            else:
                res = func(input_text)
            
            # Create tab button
            tab_id = f"tool-tab-{idx}"
            content_id = f"tool-content-{idx}"
            is_active = "active" if idx == 0 else ""
            
            tabs.append(f"""
                <button class='tab-btn {is_active}' onclick='switchTab("{content_id}", this)' data-tab='{content_id}'>
                    {tool_name}
                </button>
            """)
            
            # Create tab content - wrap in container to ensure proper isolation
            display_style = "block" if idx == 0 else "none"
            results.append(f"""
                <div class='tab-content' id='{content_id}' style='display: {display_style}; position: relative; overflow: hidden;'>
                    <div class='tab-content-wrapper'>
                        {res}
                    </div>
                </div>
            """)
            
        except Exception as e:
            results.append(f"<pre>Error executing '{tool_name}': {e}</pre>")
    
    exec_time = round(time.time() - start, 2)
    
    # Build tabs container
    tabs_html = f"""
    <div class='tabs-container'>
        <div class='tabs-header'>
            {''.join(tabs)}
        </div>
        <div class='tabs-body'>
            {''.join(results)}
        </div>
    </div>
    """
    
    final_result = tabs_html
    
    # Create a descriptive query name for history
    if input_text.strip():
        history_query = input_text
    else:
        # If no input text, use the tool names
        if len(selected_tools) == 1:
            history_query = selected_tools[0]
        else:
            history_query = " + ".join(selected_tools)
    
    add_to_history(history_query, final_result)
    return jsonify({'result': final_result, 'exec_time': exec_time})

@flask_app.route('/api/alerts', methods=['POST'])
def api_alerts():
    data = request.json
    alert_text = data.get('text', '')
    alert_id = len(alerts_db) + 1
    priority = classify_alert(alert_text)
    cause = identify_cause(alert_text)
    alert = {'id': alert_id, 'text': alert_text, 'priority': priority, 'ack': False, 'cause': cause}
    alerts_db.append(alert)
    return jsonify({'status': 'received', 'alert': alert})

@flask_app.route('/api/alerts/ack/<int:alert_id>', methods=['POST'])
def api_ack(alert_id):
    for alert in alerts_db:
        if alert['id'] == alert_id:
            alert['ack'] = True
            return jsonify({'status': 'acknowledged', 'alert': alert})
    return jsonify({'error': 'alert not found'}), 404

@flask_app.route('/api/alerts/status')
def api_alert_status():
    return jsonify(alerts_db)

@flask_app.route('/api/download/docx', methods=['POST'])
def download_docx():
    """Generate and download results as Word document with screenshot image"""
    if not DOCX_AVAILABLE:
        return jsonify({'error': 'Document generation not available. Please install python-docx'}), 503
    
    try:
        data = request.json
        screenshot_image = data.get('screenshot_image', '')
        
        if not screenshot_image:
            return jsonify({'error': 'No screenshot provided'}), 400
        
        # Create Word document
        doc = Document()
        
        # Add title
        title = doc.add_heading('OneView GOC AI Results', level=0)
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        # Add timestamp
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        time_para = doc.add_paragraph(f'Generated: {timestamp}')
        time_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        time_para.runs[0].font.size = Pt(10)
        time_para.runs[0].font.color.rgb = RGBColor(128, 128, 128)
        
        doc.add_paragraph()  # Add spacing
        
        # Decode and insert screenshot image
        try:
            # Remove the data:image/png;base64, prefix
            img_data = screenshot_image.split(',')[1]
            img_bytes = base64.b64decode(img_data)
            
            # Add image to document (full width)
            img_stream = io.BytesIO(img_bytes)
            doc.add_picture(img_stream, width=Inches(6.5))
            
        except Exception as e:
            logging.error(f"Could not add screenshot image: {e}")
            return jsonify({'error': f'Failed to process screenshot: {str(e)}'}), 500
        
        # Save document to BytesIO
        doc_io = io.BytesIO()
        doc.save(doc_io)
        doc_io.seek(0)
        
        # Generate filename
        filename = f"arlo_agenticai_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
        
        return send_file(
            doc_io,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        logging.error(f"Error generating document: {str(e)}")
        return jsonify({'error': f'Failed to generate document: {str(e)}'}), 500

@flask_app.route('/api/status/monitor')
def api_status_monitor():
    """Endpoint for automatic status monitoring - returns compact status info"""
    try:
        import requests
        from bs4 import BeautifulSoup
        
        url = "https://status.arlo.com"
        resp = requests.get(url, timeout=10)
        
        if resp.status_code != 200:
            return jsonify({'error': f'HTTP {resp.status_code}'})
        
        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text("\n", strip=True)
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        
        # Extract summary
        summary = next((l for l in lines if "operational" in l.lower()), "Status unknown")
        
        # Extract core services
        core_services = []
        for i, l in enumerate(lines):
            if l in ["Log In","Notifications","Library","Live Streaming","Video Recording","Arlo Store","Community"]:
                if i+1 < len(lines):
                    core_services.append({"service": l, "status": lines[i+1]})
        
        # Extract past incidents (last 7 only)
        past_incidents = []
        for i, l in enumerate(lines):
            if any(day in l.lower() for day in ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]):
                if i+1 < len(lines) and len(past_incidents) < 7:
                    past_incidents.append({"date": l, "detail": lines[i+1]})
        
        return jsonify({
            'summary': summary,
            'services': core_services,
            'incidents': past_incidents,
            'timestamp': time.strftime('%H:%M:%S')
        })
    except Exception as e:
        return jsonify({'error': str(e)})

@flask_app.route('/api/pagerduty/monitor')
def api_pagerduty_monitor():
    """Endpoint for PagerDuty monitoring - returns compact incident info"""
    try:
        import requests
        from datetime import datetime, timedelta
        
        api_token = os.getenv("PAGERDUTY_API_TOKEN")
        if not api_token:
            return jsonify({'error': 'PagerDuty token not configured'})
        
        headers = {
            "Authorization": f"Token token={api_token}",
            "Accept": "application/vnd.pagerduty+json;version=2",
            "Content-Type": "application/json"
        }
        
        # Get incidents from last 7 days
        since = (datetime.utcnow() - timedelta(days=7)).isoformat() + "Z"
        
        url = "https://api.pagerduty.com/incidents"
        
        # Fetch ALL incidents using pagination
        all_incidents = []
        offset = 0
        limit = 100
        more = True
        
        while more:
            params = {
                "since": since,
                "limit": limit,
                "offset": offset,
                "sort_by": "created_at:desc"
            }
            
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            
            if resp.status_code != 200:
                return jsonify({'error': f'PagerDuty API error {resp.status_code}'})
            
            data = resp.json()
            batch_incidents = data.get("incidents", [])
            all_incidents.extend(batch_incidents)
            
            # Check if there are more incidents to fetch
            more = data.get("more", False)
            offset += limit
            
            # Continue fetching all incidents without artificial limits
            if offset >= 10000:  # Safety limit to prevent truly infinite loops
                logging.warning(f"PagerDuty monitor: Reached safety limit of 10000 incidents")
                break
        
        incidents = all_incidents
        logging.info(f"✅ PagerDuty monitor: Fetched {len(incidents)} total incidents")
        
        # Separate by status
        triggered = []
        acknowledged = []
        resolved = []
        
        for inc in incidents:
            status = inc.get("status", "unknown")
            incident_data = {
                "number": inc.get("incident_number", "N/A"),
                "title": inc.get("title", "No title"),
                "service": inc.get("service", {}).get("summary", "Unknown"),
                "status": status,
                "url": inc.get("html_url", "#")
            }
            
            if status == "triggered":
                triggered.append(incident_data)
            elif status == "acknowledged":
                acknowledged.append(incident_data)
            elif status == "resolved":
                resolved.append(incident_data)
        
        # Get active (triggered + acknowledged) and limit to 5 each
        active = (triggered + acknowledged)[:5]
        recently_resolved = resolved[:5]
        
        return jsonify({
            'triggered': len(triggered),
            'acknowledged': len(acknowledged),
            'resolved': len(resolved),
            'active': active,
            'recently_resolved': recently_resolved,
            'timestamp': time.strftime('%H:%M:%S')
        })
    except Exception as e:
        logging.error(f"Error in PagerDuty monitor: {e}")
        return jsonify({'error': str(e)})

@flask_app.route('/api/public-ip', methods=['GET'])
def get_public_ip():
    """Get the current public IP address"""
    try:
        import requests
        # Try multiple services for reliability
        services = [
            'https://api.ipify.org?format=json',
            'https://ipinfo.io/json',
            'https://ifconfig.me/all.json'
        ]
        
        for service in services:
            try:
                response = requests.get(service, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    # Different services use different keys
                    ip = data.get('ip') or data.get('ip_addr')
                    if ip:
                        return jsonify({'ip': ip, 'service': service})
            except:
                continue
        
        return jsonify({'error': 'Unable to fetch public IP'}), 500
    except Exception as e:
        logging.error(f"Error fetching public IP: {e}")
        return jsonify({'error': str(e)}), 500

@flask_app.route('/api/deployments/upcoming')
def api_deployments_upcoming():
    """Endpoint for upcoming deployments from Confluence GRM Calendar"""
    try:
        import requests
        from datetime import datetime, timedelta, timezone
        from zoneinfo import ZoneInfo
        from bs4 import BeautifulSoup
        import re
        
        cst = ZoneInfo('America/Chicago')
        
        email = os.getenv("ATLASSIAN_EMAIL")
        token = os.getenv("CONFLUENCE_TOKEN")
        
        if not email or not token:
            return jsonify({'error': 'Confluence credentials not configured'})
        
        auth = (email, token)
        today = datetime.now(timezone.utc)
        
        # Try to get calendar events via Team Calendars API
        # Team Calendars subcalendar ID for GRM Calendar
        deployments = []
        
        # First try: Get events via Team Calendars REST API
        try:
            # The calendar page has events we can try to extract
            calendar_api_url = "https://arlo.atlassian.net/wiki/rest/calendar-services/1.0/calendar/events.json"
            
            # Get events for next 7 days
            start_date = today.strftime('%Y-%m-%d')
            end_date = (today + timedelta(days=7)).strftime('%Y-%m-%d')
            
            params = {
                'start': start_date,
                'end': end_date,
                'subCalendarId': '153256867',  # GRM Calendar page ID
                'userTimeZoneId': 'America/Chicago'  # CST
            }
            
            logging.info(f"🔍 Trying Team Calendar API with params: {params}")
            cal_resp = requests.get(calendar_api_url, auth=auth, params=params, timeout=10)
            
            if cal_resp.status_code == 200:
                events = cal_resp.json()
                logging.info(f"📅 Got {len(events)} events from Calendar API")
                
                for event in events:
                    try:
                        title = event.get('title', 'Untitled Deployment')
                        start_time = event.get('start', '')
                        
                        if start_time:
                            # Parse ISO format timestamp and convert to CST
                            deploy_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                            deploy_dt_cst = deploy_dt.astimezone(cst)
                            
                            deployments.append({
                                'date': deploy_dt_cst.strftime('%b %d, %Y'),
                                'service': title,
                                'timestamp': deploy_dt_cst.isoformat()
                            })
                    except Exception as e:
                        logging.error(f"Error parsing event: {e}")
                        continue
                        
        except Exception as e:
            logging.warning(f"Team Calendar API failed: {e}, trying page scraping...")
        
        # If API didn't work, try scraping the page content
        if not deployments:
            logging.info(f"📄 Falling back to page content scraping")
            page_id = "153256867"
            page_url = f"https://arlo.atlassian.net/wiki/rest/api/content/{page_id}?expand=body.storage"
            
            resp = requests.get(page_url, auth=auth, timeout=10)
            
            if resp.status_code == 200:
                page_data = resp.json()
                content_html = page_data.get('body', {}).get('storage', {}).get('value', '')
                
                if content_html:
                    soup = BeautifulSoup(content_html, 'html.parser')
                    logging.info(f"📄 Content HTML length: {len(content_html)} chars")
                    
                    # Look for calendar macro and its subcalendar attribute
                    calendar_macro = soup.find('ac:structured-macro', {'ac:name': 'calendar'})
                    
                    if calendar_macro:
                        # Try to extract subcalendar IDs
                        subcalendars = calendar_macro.find_all('ac:parameter', {'ac:name': 'subcalendarid'})
                        logging.info(f"📅 Found {len(subcalendars)} subcalendars in macro")
                        
                        # For now, log what we find for debugging
                        for sub in subcalendars:
                            logging.info(f"Subcalendar: {sub.get_text()}")
        
        # If still no deployments found, load from local JSON file
        if not deployments:
            logging.warning("⚠️ No deployments found via API or scraping, loading from deployments.json")
            
            try:
                import json
                from pathlib import Path
                
                json_file = Path(__file__).parent / 'deployments.json'
                
                if json_file.exists():
                    with open(json_file, 'r') as f:
                        data = json.load(f)
                        
                    for item in data.get('deployments', []):
                        date_str = item.get('date')
                        time_str = item.get('time', '12:00')
                        service = item.get('service', 'Unknown')
                        
                        # Create timestamp in CST
                        timestamp = f"{date_str}T{time_str}:00-06:00"
                        
                        deployments.append({
                            'date': datetime.fromisoformat(timestamp).strftime('%b %d, %Y'),
                            'service': service,
                            'timestamp': timestamp
                        })
                    
                    logging.info(f"📂 Loaded {len(deployments)} deployments from JSON file")
                else:
                    logging.error("❌ deployments.json file not found")
                    deployments = [
                        {'date': 'Feb 18, 2026', 'service': 'Check Confluence Calendar or update deployments.json', 'timestamp': '2026-02-18T12:00:00-06:00'}
                    ]
                    
            except Exception as e:
                logging.error(f"❌ Error loading deployments.json: {e}")
                deployments = [
                    {'date': 'Feb 18, 2026', 'service': 'Error loading deployments data', 'timestamp': '2026-02-18T12:00:00-06:00'}
                ]
        
        # Filter deployments from now to next 24 hours
        next_window = today + timedelta(hours=24)
        filtered_deployments = []
        
        for deploy in deployments:
            try:
                deploy_date = datetime.fromisoformat(deploy['timestamp'])
                # Include only future deployments within next 24 hours
                if deploy_date >= today and deploy_date <= next_window:
                    filtered_deployments.append(deploy)
            except Exception as e:
                logging.error(f"Error filtering deployment: {e}")
                pass
        
        deployments = filtered_deployments
        
        # Sort by time
        deployments.sort(key=lambda x: x.get('timestamp', ''))
        
        # Limit to next 15 deployments
        upcoming = deployments[:15]
        
        logging.info(f"✅ Deployments: Found {len(upcoming)} deployment(s) in next 24h")
        
        return jsonify({
            'deployments': upcoming,
            'total': len(deployments),
            'timestamp': time.strftime('%H:%M:%S')
        })
        
    except Exception as e:
        logging.error(f"Error fetching deployments: {e}")
        return jsonify({'error': str(e)})


# ============================================
# MCP SERVER ENDPOINTS
# ============================================

@flask_app.route('/mcp/sse', methods=['GET', 'POST'])
async def mcp_sse_endpoint():
    """
    MCP Server SSE endpoint
    Exposes OneView GOC AI tools as MCP server for consumption by Claude Desktop, Cursor, etc.
    """
    try:
        from mcp_server import get_mcp_server
        from mcp.server.sse import sse_server
        from starlette.requests import Request as StarletteRequest
        from starlette.responses import StreamingResponse
        
        # Get the MCP server instance
        server = get_mcp_server()
        
        # Convert Flask request to Starlette request format
        # This is needed because MCP SDK uses Starlette
        from werkzeug.datastructures import Headers
        
        # Create a simple adapter
        if request.method == 'GET':
            # For SSE connections
            async def event_stream():
                async with sse_server() as streams:
                    send, receive = streams
                    
                    # Handle the SSE connection
                    async for message in server.handle_sse(receive, send):
                        yield f"data: {json.dumps(message)}\n\n"
            
            return flask_app.response_class(
                event_stream(),
                mimetype='text/event-stream',
                headers={
                    'Cache-Control': 'no-cache',
                    'X-Accel-Buffering': 'no'
                }
            )
        else:
            # Handle POST messages
            data = request.get_json()
            # Process the message through MCP server
            result = await server.handle_request(data)
            return jsonify(result)
            
    except Exception as e:
        logging.error(f"❌ MCP Server endpoint error: {e}")
        return jsonify({'error': str(e)}), 500


@flask_app.route('/mcp/info')
def mcp_info():
    """
    MCP Server information endpoint
    Returns available tools and server metadata
    """
    from mcp_server import TOOL_REGISTRY
    
    return jsonify({
        'name': 'oneview-goc-ai',
        'version': '3.0.0',
        'description': 'OneView GOC AI - Unified monitoring and operations platform',
        'protocol': 'mcp',
        'transport': 'sse',
        'endpoint': '/mcp/sse',
        'tools': [
            {
                'name': name,
                'description': info['description']
            }
            for name, info in TOOL_REGISTRY.items()
        ],
        'total_tools': len(TOOL_REGISTRY)
    })


if __name__ == '__main__':
    # Use port 8080
    port = int(os.getenv('PORT', 8080))
    flask_app.run(host='0.0.0.0', port=port)
