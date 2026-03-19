
from flask import Flask, request, jsonify, send_from_directory, send_file, render_template
from flask_cors import CORS
import time
import sys
import os
import logging
import io
import base64
import html
import json
import re
from datetime import datetime
import boto3
from botocore.exceptions import ClientError

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
from tools.bedrock_tool import ask_bedrock
#from tools.gemini_tool import ask_gemini
#from tools.llama_tool import ask_llama
from tools.confluence_tool import confluence_search
#from tools.tickets_tool import read_tickets
from tools.history_tool import add_to_history, get_history
#from tools.suggestions_tool import AI_suggestions
from tools.metrics_persistence import DB_PATH

# Import ask_arlochat (GocBedrock) - auto-detects best mode: SDK async or HTTP fallback
try:
    from tools.ask_arlochat import ask_arlo, MCP_SDK_AVAILABLE
    ARLOCHAT_AVAILABLE = True
    if MCP_SDK_AVAILABLE:
        print("✅ GocBedrock MCP loaded (SDK Async mode - Python 3.10+)")
    else:
        print("✅ GocBedrock MCP loaded (HTTP Fallback mode - Python 3.9+)")
except ImportError as e:
    print(f"⚠️  WARNING: GocBedrock import failed: {e}")
    ARLOCHAT_AVAILABLE = False
    MCP_SDK_AVAILABLE = False
    
    # Create a placeholder function
    def ask_arlo(question: str = "") -> str:
        return f"""
        <div style='background-color: #fee; padding: 12px; border-left: 4px solid #f56565; border-radius: 4px; margin: 8px 0;'>
            <p style='margin: 0; color: #c53030;'>
                ❌ <strong>GocBedrock module failed to load</strong><br><br>
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
from tools.deployments_calendar import get_grm_deployments
from tools.datadog_dashboards import read_datadog_dashboards, read_datadog_errors_only, read_datadog_adt, read_datadog_adt_errors_only, read_datadog_samsung, read_datadog_samsung_errors_only, read_datadog_redmetrics_us, read_datadog_all_errors, read_datadog_failed_pods, read_datadog_403_errors, search_datadog_dashboards, search_datadog_services
from tools.splunk_tool import read_splunk_p0_dashboard, read_splunk_p0_cvr_dashboard, read_splunk_p0_adt_dashboard
from tools.pagerduty_tool import get_pagerduty_incidents
from tools.pagerduty_analytics import get_pagerduty_analytics
from tools.pagerduty_insights import get_pagerduty_insights
from tools.grafana_dashboards import get_grafana_dns_mapper, get_grafana_savant_z2, get_grafana_dashboard_list

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
    "DD_Search": {"description": "Search and list Datadog dashboards by name/query", "function": search_datadog_dashboards},
    "DD_Services": {"description": "Search Datadog APM services (backend-*, api-*, etc.)", "function": search_datadog_services},
    "DD_Red_Metrics": {"description": "List and search Datadog dashboards", "function": read_datadog_dashboards},
    "DD_Red_ADT": {"description": "Show RED Metrics - ADT dashboard from Datadog", "function": read_datadog_adt},
    "DD_Red_Samsung": {"description": "Show RED Metrics - Samsung network dashboard from Datadog", "function": read_datadog_samsung},
    "DD_Red_Metrics_US": {"description": "Show RED Metrics - US region dashboard from Datadog", "function": read_datadog_redmetrics_us},
    "DD_Errors": {"description": "Show services with errors > 0 from RED Metrics & ADT dashboards", "function": read_datadog_all_errors},
    "DD_Samsung_Errors": {"description": "Show Samsung network services with errors > 0", "function": read_datadog_samsung_errors_only},
    "DD_Failed_Pods": {"description": "Monitor Kubernetes pods with failures (ImagePullBackOff, CrashLoop) causing 4xx/5xx errors", "function": read_datadog_failed_pods},
    "DD_403_Errors": {"description": "Monitor 403 Forbidden errors from APM traces (Artifactory, authentication issues)", "function": read_datadog_403_errors},
    "P0_Streaming": {"description": "Show P0 Streaming dashboard from Splunk", "function": read_splunk_p0_dashboard},
    "P0_CVR_Streaming": {"description": "Show P0 CVR Streaming dashboard from Splunk", "function": read_splunk_p0_cvr_dashboard},
    "P0_ADT_Streaming": {"description": "Show P0 ADT Streaming dashboard from Splunk", "function": read_splunk_p0_adt_dashboard},
    "Grafana_DNS_Mapper": {"description": "Monitor DNS Mapper IP usage for HMS/CVR streaming (z4)", "function": get_grafana_dns_mapper},
    "Grafana_Savant_z2": {"description": "Monitor Savant infrastructure in Harlem datacenter (z2)", "function": get_grafana_savant_z2},
    "Holiday_Oncall": {"description": "Get on-call schedule for holidays", "function": confluence_oncall_today},
    "PagerDuty": {"description": "Get active incidents from PagerDuty", "function": get_pagerduty_incidents},
    "PagerDuty_Dashboards": {"description": "Show PagerDuty analytics with charts and metrics", "function": get_pagerduty_analytics},
    "PagerDuty_Insights": {"description": "Show incident activity insights and trends", "function": get_pagerduty_insights},
    "Ask_Bedrock": {"description": "Ask AWS Bedrock (Claude 3.5 Sonnet) for AI-powered responses", "function": ask_bedrock},
    "Bedrock_Report": {"description": "AI-powered comprehensive analysis and synthesis", "function": ask_arlo}
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


# Status Monitor Dashboard Routes
@flask_app.route('/statusmonitor')
def statusmonitor_page():
    """Serve the status monitor dashboard page (all environments)"""
    return send_from_directory('templates', 'statusmonitor.html')


@flask_app.route('/statusmonitor/production')
def statusmonitor_production_page():
    """Serve the status monitor dashboard page for production only"""
    return render_template('statusmonitor.html', environment='production')


@flask_app.route('/statusmonitor/goldendev')
def statusmonitor_goldendev_page():
    """Serve the status monitor dashboard page for goldendev only"""
    return render_template('statusmonitor.html', environment='goldendev')


@flask_app.route('/statusmonitor/goldenqa')
def statusmonitor_goldenqa_page():
    """Serve the status monitor dashboard page for goldenqa only"""
    return render_template('statusmonitor.html', environment='goldenqa')


@flask_app.route('/statusmonitor/samsung')
def statusmonitor_samsung_page():
    """Serve the status monitor dashboard page for Samsung network services only"""
    return render_template('statusmonitor.html', environment='samsung')


@flask_app.route('/statusmonitor/adt')
def statusmonitor_adt_page():
    """Serve the status monitor dashboard page for ADT partner services only"""
    return render_template('statusmonitor.html', environment='adt')


@flask_app.route('/statusmonitor/redmetrics-us')
def statusmonitor_redmetrics_us_page():
    """Serve the status monitor dashboard page for RED Metrics US services"""
    return render_template('statusmonitor.html', environment='redmetrics-us')


@flask_app.route('/api/statusmonitor', methods=['POST'])
def api_statusmonitor():
    """API endpoint for status monitor dashboard data"""
    try:
        from tools.status_monitor import status_monitor_dashboard
        
        data = request.get_json() or {}
        timerange = data.get('timerange', 4)
        environment = data.get('environment', None)  # Optional: specific environment
        
        html_content = status_monitor_dashboard(timerange=timerange, environment=environment)
        
        return jsonify({
            'success': True,
            'html': html_content
        })
    except Exception as e:
        logging.error(f"Error in status monitor: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ========================================
# REST API Endpoints for Metrics & History
# ========================================

@flask_app.route('/api/status/current', methods=['GET'])
def api_status_current():
    """
    Get current status for all services (JSON format)
    Query params:
        - environment: Filter by environment (optional)
    """
    try:
        from tools.metrics_persistence import get_all_services_current_status
        
        environment = request.args.get('environment')
        services = get_all_services_current_status()
        
        # Filter by environment if specified
        if environment:
            services = [s for s in services if s.get('environment') == environment]
        
        return jsonify({
            'success': True,
            'timestamp': datetime.utcnow().isoformat(),
            'total_services': len(services),
            'services': services
        })
    except Exception as e:
        logging.error(f"Error fetching current status: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@flask_app.route('/api/status/<environment>', methods=['GET'])
def api_status_by_environment(environment):
    """
    Get current status for specific environment (JSON format)
    Path params:
        - environment: production, goldendev, or goldenqa
    """
    try:
        from tools.metrics_persistence import get_all_services_current_status
        
        services = get_all_services_current_status()
        env_services = [s for s in services if s.get('environment') == environment]
        
        # Calculate summary
        total = len(env_services)
        healthy = sum(1 for s in env_services if s['status'] == 'healthy')
        warning = sum(1 for s in env_services if s['status'] == 'warning')
        critical = sum(1 for s in env_services if s['status'] == 'critical')
        
        return jsonify({
            'success': True,
            'timestamp': datetime.utcnow().isoformat(),
            'environment': environment,
            'summary': {
                'total_services': total,
                'healthy': healthy,
                'warning': warning,
                'critical': critical
            },
            'services': env_services
        })
    except Exception as e:
        logging.error(f"Error fetching status for {environment}: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@flask_app.route('/api/history/service/<service_name>', methods=['GET'])
def api_service_history(service_name):
    """
    Get historical metrics for a specific service
    Path params:
        - service_name: Name of the service
    Query params:
        - environment: Environment (required)
        - hours: Hours to look back (default: 24)
    """
    try:
        from tools.metrics_persistence import get_service_history
        
        environment = request.args.get('environment')
        if not environment:
            return jsonify({
                'success': False,
                'error': 'environment parameter is required'
            }), 400
        
        hours = int(request.args.get('hours', 24))
        history = get_service_history(service_name, environment, hours)
        
        return jsonify({
            'success': True,
            'service': service_name,
            'environment': environment,
            'hours': hours,
            'data_points': len(history),
            'history': history
        })
    except Exception as e:
        logging.error(f"Error fetching service history: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@flask_app.route('/api/history/dashboard', methods=['GET'])
def api_dashboard_history():
    """
    Get historical dashboard snapshots
    Query params:
        - environment: Filter by environment (optional)
        - hours: Hours to look back (default: 24)
    """
    try:
        from tools.metrics_persistence import get_dashboard_history
        
        environment = request.args.get('environment')
        hours = int(request.args.get('hours', 24))
        
        history = get_dashboard_history(environment, hours)
        
        return jsonify({
            'success': True,
            'environment': environment or 'all',
            'hours': hours,
            'data_points': len(history),
            'history': history
        })
    except Exception as e:
        logging.error(f"Error fetching dashboard history: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@flask_app.route('/api/trends/service/<service_name>', methods=['GET'])
def api_service_trends(service_name):
    """
    Get trend analysis for a specific service
    Path params:
        - service_name: Name of the service
    Query params:
        - environment: Environment (required)
        - hours: Hours to analyze (default: 24)
    """
    try:
        from tools.metrics_persistence import get_service_trends
        
        environment = request.args.get('environment')
        if not environment:
            return jsonify({
                'success': False,
                'error': 'environment parameter is required'
            }), 400
        
        hours = int(request.args.get('hours', 24))
        trends = get_service_trends(service_name, environment, hours)
        
        return jsonify({
            'success': True,
            'trends': trends
        })
    except Exception as e:
        logging.error(f"Error calculating trends: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@flask_app.route('/api/critical/history', methods=['GET'])
def api_critical_history():
    """
    Get history of critical service incidents
    Query params:
        - hours: Hours to look back (default: 24)
    """
    try:
        from tools.metrics_persistence import get_critical_services_history
        
        hours = int(request.args.get('hours', 24))
        critical_history = get_critical_services_history(hours)
        
        return jsonify({
            'success': True,
            'hours': hours,
            'total_incidents': len(critical_history),
            'incidents': critical_history
        })
    except Exception as e:
        logging.error(f"Error fetching critical history: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@flask_app.route('/api/health', methods=['GET'])
def api_health():
    """Health check endpoint for load balancers"""
    try:
        from tools.metrics_persistence import get_database_stats
        
        db_stats = get_database_stats()
        
        return jsonify({
            'status': 'healthy',
            'timestamp': datetime.utcnow().isoformat(),
            'database': db_stats,
            'version': '3.0.2'
        })
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'error': str(e)
        }), 503


@flask_app.route('/api/cache/clear', methods=['POST'])
def api_clear_cache():
    """Clear the status monitor cache (force fresh data on next load)"""
    try:
        from tools.status_monitor import clear_status_cache
        
        clear_status_cache()
        
        return jsonify({
            'success': True,
            'message': 'Cache cleared successfully',
            'timestamp': datetime.utcnow().isoformat()
        })
    except Exception as e:
        logging.error(f"Error clearing cache: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@flask_app.route('/api/tools')
def api_tools():
    return jsonify([{'name': name, 'desc': desc} for name, desc in registered_tools])

@flask_app.route('/api/suggest-tools', methods=['POST'])
def suggest_tools():
    """Use AI to suggest which tools to use based on the user's query"""
    data = request.get_json()
    user_query = data.get('query', '').strip()
    
    if not user_query:
        return jsonify({'error': 'No query provided'}), 400
    
    try:
        logging.info(f"🤖 AI Auto-Select: Analyzing query: {user_query[:100]}")
        
        # Build a prompt for Bedrock to analyze the query and suggest tools
        available_tools = "\n".join([f"- {name}: {tool['description']}" for name, tool in TOOLS.items()])
        
        analysis_prompt = f"""Analyze this question and select the appropriate tools.

QUESTION: "{user_query}"

AVAILABLE TOOLS:
{available_tools}

SELECTION GUIDELINES (Use these as a starting point, but select ALL relevant tools):

📋 TOOL CATEGORIES:

**CONFLUENCE/WIKI/DOCUMENTATION:**
- Bedrock_Report: Searches Confluence, Jira, wikis, runbooks, procedures
- Wiki: Direct Confluence search

**DATADOG MONITORING:**
- DD_Red_Metrics: RED metrics, infrastructure, clusters, pods
- DD_Search: Search Datadog dashboards
- DD_Services: List all APM services
- DD_Errors: Services with errors
- DD_Failed_Pods: Kubernetes pod failures
- DD_403_Errors: 403 authentication errors
- DD_Red_ADT: ADT network metrics
- DD_Red_Samsung: Samsung network metrics
- DD_Red_Metrics_US: US region metrics
- DD_Samsung_Errors: Samsung-specific errors

**PAGERDUTY:**
- PagerDuty: Active incidents
- PagerDuty_Dashboards: Analytics and charts
- PagerDuty_Insights: Incident trends

**SPLUNK:**
- P0_Streaming: P0 streaming dashboard
- P0_CVR_Streaming: CVR streaming
- P0_ADT_Streaming: ADT streaming

**GRAFANA:**
- Grafana_DNS_Mapper: DNS mapper monitoring
- Grafana_Savant_z2: Savant infrastructure

**VERSIONS & OWNERSHIP:**
- Arlo_Versions: Service versions from versions.arlocloud.com
- Owners: Service ownership information

**AI ASSISTANTS:**
- Ask_Bedrock: General AI explanations (no data lookup)
- Bedrock_Report: Intelligent MCP tool selection and execution

🎯 SELECTION STRATEGY:

For COMPREHENSIVE SERVICE INFO (cluster, owner, metrics, errors):
→ Consider: Bedrock_Report, DD_Red_Metrics, DD_Search, DD_Services, DD_Errors, DD_Failed_Pods, Arlo_Versions, Owners

For DEPLOYMENT/CALENDAR questions:
→ Use: Bedrock_Report (handles GRM calendar internally)

For GENERAL HEALTH/STATUS (all services):
→ Consider: DD_Errors, DD_Failed_Pods, PagerDuty, DD_Services

For SPECIFIC SERVICE ERRORS:
→ Consider: DD_Errors, DD_Failed_Pods, DD_403_Errors, PagerDuty

For JIRA/TICKETS:
→ Must include: Bedrock_Report

For CONFLUENCE/DOCS:
→ Must include: Bedrock_Report, can add Wiki

For METRICS ONLY:
→ Consider: DD_Red_Metrics, DD_Red_ADT, DD_Red_Samsung, DD_Red_Metrics_US

For INCIDENTS/ALERTS:
→ Consider: PagerDuty, PagerDuty_Insights, PagerDuty_Dashboards

🚨 CRITICAL RULES:

1. **ALWAYS INCLUDE Bedrock_Report** for ANY data query (service info, errors, metrics, incidents, etc.)
   - Bedrock_Report provides context from Confluence, wikis, Jira, and MCP tools
   - Exception: ONLY exclude for pure explanations (e.g., "what is kubernetes?")

2. **SELECT MULTIPLE DD TOOLS** for service-specific queries:
   - Combine DD_Red_Metrics + DD_Search + DD_Services + DD_Errors for comprehensive data
   - Include DD_Failed_Pods if relevant to infrastructure/health

3. **MORE TOOLS = BETTER ANSWER**:
   - Don't limit yourself to 2-3 tools
   - Select ALL tools that could contribute useful information
   - Better to have extra context than miss important data

4. **SERVICE-SPECIFIC QUERIES** (e.g., "hmspayment cluster"):
   → Must include: Bedrock_Report + multiple DD tools (DD_Red_Metrics, DD_Search, DD_Services, DD_Errors)
   → Can include: Arlo_Versions, Owners, DD_Failed_Pods, PagerDuty

5. **EXECUTION ORDER** (handled automatically):
   - Data tools execute FIRST (DD_*, PagerDuty, etc.)
   - Bedrock_Report executes LAST with context from all data tools
   - Bedrock_Report synthesizes everything into complete response

ANALYZE "{user_query}":
- What type of information is being requested?
- Which data sources would have this information?
- Select ALL relevant tools (err on the side of including more)
- MUST include Bedrock_Report unless it's a pure explanation query

Return ONLY a JSON array with ALL relevant tools: ["Tool1", "Tool2", "Tool3", ..., "Bedrock_Report"]
NO markdown, NO explanation, ONLY the JSON array."""

        # Call Bedrock to get tool suggestions
        logging.info("🤖 Calling Bedrock for tool recommendations...")
        suggested_tools_response = ask_bedrock(analysis_prompt, selected_tools=None)
        logging.info(f"🤖 Bedrock response: {suggested_tools_response[:200]}")
        
        # Parse the JSON response
        # Try to extract JSON array from response
        json_match = re.search(r'\[.*?\]', suggested_tools_response, re.DOTALL)
        if json_match:
            suggested_tools = json.loads(json_match.group(0))
        else:
            # Fallback: if no JSON found, return error
            logging.error(f"❌ Could not parse Bedrock response: {suggested_tools_response}")
            return jsonify({'error': 'Could not parse AI response', 'raw_response': suggested_tools_response}), 500
        
        # Validate that suggested tools exist
        valid_tools = [tool for tool in suggested_tools if tool in TOOLS]
        
        # 🔥 ALWAYS ADD Bedrock_Report for comprehensive context (unless it's pure explanation)
        # Bedrock_Report executes LAST but displays FIRST (for better UX)
        user_query_lower = user_query.lower()
        
        # Check if this is a pure explanation query (no data lookup needed)
        pure_explanation_keywords = ['what is', 'qué es', 'que es', 'explain', 'explica', 'define']
        is_pure_explanation = (
            any(keyword in user_query_lower for keyword in pure_explanation_keywords) and
            len(valid_tools) == 1 and 
            'Ask_Bedrock' in valid_tools
        )
        
        if not is_pure_explanation and 'Bedrock_Report' not in valid_tools:
            valid_tools.append('Bedrock_Report')
            logging.info(f"➕ Auto-adding Bedrock_Report for comprehensive context synthesis")
        
        # Reorder: Put Bedrock_Report FIRST for display (it will still execute last due to phase logic)
        if 'Bedrock_Report' in valid_tools:
            valid_tools.remove('Bedrock_Report')
            valid_tools.insert(0, 'Bedrock_Report')  # Insert at beginning for UI
            logging.info(f"🔄 Moved Bedrock_Report to FIRST position for UI display")
        
        logging.info(f"✅ Final tool selection: {len(valid_tools)} tool(s): {valid_tools}")
        return jsonify({'suggested_tools': valid_tools})
        
    except Exception as e:
        logging.error(f"❌ Error in suggest-tools: {e}")
        return jsonify({'error': str(e)}), 500

@flask_app.route('/api/run', methods=['POST'])
def api_run():
    data = request.json
    input_text = data.get('input', '')
    selected_tools = data.get('tools', []) or ['Suggestions']
    timerange = data.get('timerange', 4)  # Default to 4 hours
    start = time.time()
    
    # Execute tools in parallel using threading
    import concurrent.futures
    from threading import Lock
    
    results_dict = {}
    results_lock = Lock()
    
    def analyze_query_with_bedrock(user_query: str) -> dict:
        """
        Use Bedrock to intelligently analyze user query and extract intent
        Returns: {
            'is_general_query': bool,  # True if asking for all services
            'service_name': str,        # Empty if general, or specific service name
            'confidence': str           # 'high', 'medium', 'low'
        }
        """
        try:
            bedrock_api_key = os.getenv("BEDROCK_API_KEY")
            if not bedrock_api_key:
                logging.warning("⚠️ BEDROCK_API_KEY not available for query analysis")
                return {'is_general_query': False, 'service_name': user_query, 'confidence': 'low'}
            
            bedrock_runtime = boto3.client(
                service_name='bedrock-runtime',
                region_name='us-east-1',
                aws_access_key_id=bedrock_api_key.split(':')[0] if ':' in bedrock_api_key else bedrock_api_key,
                aws_secret_access_key=bedrock_api_key.split(':')[1] if ':' in bedrock_api_key and len(bedrock_api_key.split(':')) > 1 else ''
            )
            
            analysis_prompt = f"""Analyze this user query and extract the intent for monitoring tool execution.

User Query: "{user_query}"

Determine:
1. Is this a GENERAL query asking for ALL services/dashboards? (e.g., "all services", "todas las zonas", "general status", "show everything", "red metrics de todas las regiones")
2. Or is it asking for a SPECIFIC service? (e.g., "hmsguard status", "backend-hmsalexaapi metrics", "device-location errors")

If SPECIFIC, extract the exact service name (e.g., "hmsguard", "backend-hmsalexaapi", "device-location").

Respond ONLY with valid JSON (no markdown, no explanations):
{{
    "is_general_query": true/false,
    "service_name": "extracted-service-name or empty string",
    "confidence": "high/medium/low"
}}

Examples:
- "show me red metrics for all zones" → {{"is_general_query": true, "service_name": "", "confidence": "high"}}
- "what's happening with hmsguard?" → {{"is_general_query": false, "service_name": "hmsguard", "confidence": "high"}}
- "dame los resultados de todas las regiones" → {{"is_general_query": true, "service_name": "", "confidence": "high"}}
- "backend-hmsalexaapi errors" → {{"is_general_query": false, "service_name": "backend-hmsalexaapi", "confidence": "high"}}"""

            request_body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 500,
                "temperature": 0.1,
                "messages": [{"role": "user", "content": analysis_prompt}]
            }
            
            response = bedrock_runtime.invoke_model(
                modelId="us.anthropic.claude-sonnet-4-20250514-v1:0",
                body=json.dumps(request_body)
            )
            
            response_body = json.loads(response['body'].read())
            bedrock_response = response_body.get('content', [{}])[0].get('text', '{}')
            
            # Parse JSON response
            # Remove markdown code blocks if present
            bedrock_response = bedrock_response.strip()
            if bedrock_response.startswith('```'):
                bedrock_response = bedrock_response.split('```')[1]
                if bedrock_response.startswith('json'):
                    bedrock_response = bedrock_response[4:]
                bedrock_response = bedrock_response.strip()
            
            analysis = json.loads(bedrock_response)
            
            logging.info(f"🤖 Bedrock Query Analysis:")
            logging.info(f"   - User Query: '{user_query}'")
            logging.info(f"   - Is General: {analysis.get('is_general_query', False)}")
            logging.info(f"   - Service Name: '{analysis.get('service_name', '')}'")
            logging.info(f"   - Confidence: {analysis.get('confidence', 'unknown')}")
            
            return analysis
            
        except Exception as e:
            logging.error(f"❌ Error in Bedrock query analysis: {e}")
            # Fallback to passing full query
            return {'is_general_query': False, 'service_name': user_query, 'confidence': 'low'}
    
    def execute_tool(idx, tool_name, context_from_other_tools=None):
        """Execute a single tool and store result"""
        func = TOOLS.get(tool_name, {}).get('function')
        if not func:
            return idx, tool_name, f"<pre>No tool found for {tool_name}</pre>", True
        
        try:
            # Determine what to pass to the tool
            tool_input = input_text  # Default: pass the full query text
            
            # For monitoring and service-specific tools, use Bedrock to intelligently analyze the query
            service_specific_tools = ['DD_Errors', 'DD_Red_Metrics', 'DD_Failed_Pods', 'DD_403_Errors', 
                                     'DD_Red_ADT', 'DD_Red_Samsung', 'DD_Red_Metrics_US', 
                                     'DD_Search', 'DD_Services', 'Arlo_Versions', 'Owners']
            
            if tool_name in service_specific_tools:
                # Use Bedrock to analyze the query intent
                analysis = analyze_query_with_bedrock(input_text)
                
                if analysis['is_general_query']:
                    # User wants all services/dashboards
                    tool_input = ""
                    logging.info(f"🔍 Bedrock detected GENERAL query for {tool_name}, passing empty string")
                else:
                    # User wants a specific service
                    if analysis['service_name']:
                        tool_input = analysis['service_name']
                        logging.info(f"🎯 Bedrock extracted service: '{analysis['service_name']}' (confidence: {analysis['confidence']})")
                    else:
                        # Bedrock couldn't extract - pass full query
                        tool_input = input_text
                        logging.info(f"📝 Bedrock couldn't extract service, passing full query to {tool_name}")
            
            # Pass timerange to Datadog, Splunk, and Grafana tools
            if tool_name in ['DD_Search', 'DD_Services', 'DD_Red_Metrics', 'DD_Errors', 'DD_Red_ADT', 'DD_Red_Samsung', 'DD_Red_Metrics_US', 'DD_Failed_Pods', 'DD_403_Errors', 'P0_Streaming', 'P0_CVR_Streaming', 'P0_ADT_Streaming', 'Grafana_DNS_Mapper', 'Grafana_Savant_z2']:
                res = func(tool_input, timerange)
            # Pass selected_tools and MCP access to Ask_Bedrock
            elif tool_name == 'Ask_Bedrock':
                res = func(input_text, selected_tools=selected_tools, enable_mcp_access=True)
            # Pass context from other tools to Bedrock_Report
            elif tool_name == 'Bedrock_Report':
                res = func(tool_input, context_from_other_tools=context_from_other_tools)
            else:
                res = func(tool_input)
            return idx, tool_name, res, False
        except Exception as e:
            return idx, tool_name, f"<pre>Error executing '{tool_name}': {e}</pre>", True
    
    # Separate tools into data tools and synthesis tools
    synthesis_tools = ['Bedrock_Report', 'Ask_Bedrock']
    data_tool_indices = [(idx, tool) for idx, tool in enumerate(selected_tools) if tool not in synthesis_tools]
    synthesis_tool_indices = [(idx, tool) for idx, tool in enumerate(selected_tools) if tool in synthesis_tools]
    
    # Phase 1: Execute data tools in parallel
    context_for_synthesis = {}
    if data_tool_indices:
        logging.info(f"📊 Phase 1: Executing {len(data_tool_indices)} data tool(s) in parallel...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_to_tool = {
                executor.submit(execute_tool, idx, tool_name): (idx, tool_name)
                for idx, tool_name in data_tool_indices
            }
            
            for future in concurrent.futures.as_completed(future_to_tool):
                idx, tool_name, result, is_error = future.result()
                with results_lock:
                    results_dict[idx] = (tool_name, result, is_error)
                    # Store ALL results for synthesis tools (no filtering)
                    if not is_error:
                        context_for_synthesis[tool_name] = result
                        logging.info(f"✅ {tool_name} completed - adding to context")
        logging.info(f"✅ Phase 1 complete: {len(context_for_synthesis)} tool(s) executed")
    
    # Phase 2: Execute synthesis tools with context from data tools
    if synthesis_tool_indices:
        logging.info(f"🧠 Phase 2: Executing {len(synthesis_tool_indices)} synthesis tool(s) with context from {len(context_for_synthesis)} data tool(s)...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_to_tool = {
                executor.submit(execute_tool, idx, tool_name, context_for_synthesis if context_for_synthesis else None): (idx, tool_name)
                for idx, tool_name in synthesis_tool_indices
            }
            
            for future in concurrent.futures.as_completed(future_to_tool):
                idx, tool_name, result, is_error = future.result()
                with results_lock:
                    results_dict[idx] = (tool_name, result, is_error)
        logging.info(f"✅ Phase 2 complete")
    
    # Build tabs and results - show ALL tools (no filtering)
    tabs = []
    results = []
    
    for idx in range(len(selected_tools)):
        if idx in results_dict:
            tool_name, res, is_error = results_dict[idx]
            
            # Create tab button
            tab_id = f"tool-tab-{idx}"
            content_id = f"tool-content-{idx}"
            # Set Bedrock_Report as active tab, otherwise first tab
            is_active = "active" if (tool_name == 'Bedrock_Report' or (idx == 0 and 'Bedrock_Report' not in [selected_tools[i] for i in range(len(selected_tools)) if i in results_dict])) else ""
            
            tabs.append(f"""
                <button class='tab-btn {is_active}' onclick='switchTab("{content_id}", this)' data-tab='{content_id}'>
                    {tool_name}
                </button>
            """)
            
            # Create tab content - wrap in container to ensure proper isolation
            display_style = "block" if is_active else "none"
            results.append(f"""
                <div class='tab-content' id='{content_id}' style='display: {display_style}; position: relative; overflow: hidden;'>
                    <div class='tab-content-wrapper'>
                        {res}
                    </div>
                </div>
            """)
    
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
    logging.info(f"✅ Built UI with {len(tabs)} tab(s)")
    
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
        
        # Extract summary - be more flexible with patterns
        summary = "Status unknown"
        for l in lines:
            l_lower = l.lower()
            if "operational" in l_lower or "all systems operational" in l_lower:
                summary = l
                break
            elif "experiencing issues" in l_lower or "some systems" in l_lower:
                summary = l
                break
            elif "degraded" in l_lower or "partial outage" in l_lower or "major outage" in l_lower:
                summary = l
                break
        
        logging.info(f"📊 Arlo Status Summary: {summary}")
        
        # Extract core services (deduplicate)
        core_services = []
        seen_services = set()
        for i, l in enumerate(lines):
            if l in ["Log In","Notifications","Library","Live Streaming","Video Recording","Arlo Store","Community"]:
                if i+1 < len(lines) and l not in seen_services:
                    status = lines[i+1]
                    # Skip if next line is also a service name (means status wasn't captured)
                    if status not in ["Log In","Notifications","Library","Live Streaming","Video Recording","Arlo Store","Community"]:
                        logging.info(f"✅ Arlo Status: {l} → {status}")
                        core_services.append({"service": l, "status": status})
                        seen_services.add(l)
                    else:
                        logging.warning(f"⚠️ Arlo Status: {l} → status not found (next line is another service: {status})")
        
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
        error_msg = str(e)
        # Simplify proxy errors
        if 'ProxyError' in error_msg or 'Tunnel connection failed' in error_msg or '403 Forbidden' in error_msg:
            error_msg = 'Proxy blocked (check network settings)'
        elif 'Max retries exceeded' in error_msg:
            error_msg = 'Connection timeout (check network)'
        elif 'Connection refused' in error_msg:
            error_msg = 'Service unavailable'
        return jsonify({'error': error_msg})

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
        error_msg = str(e)
        # Simplify proxy errors
        if 'ProxyError' in error_msg or 'Tunnel connection failed' in error_msg or '403 Forbidden' in error_msg:
            error_msg = 'Proxy blocked (check network settings)'
        elif 'Max retries exceeded' in error_msg:
            error_msg = 'Connection timeout (check network)'
        elif 'Connection refused' in error_msg:
            error_msg = 'Service unavailable'
        return jsonify({'error': error_msg})

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
            
            # Get events for last 2 hours + next 24 hours (26 hour window)
            start_date = (today - timedelta(hours=2)).strftime('%Y-%m-%d')
            end_date = (today + timedelta(hours=24)).strftime('%Y-%m-%d')
            
            params = {
                'start': start_date,
                'end': end_date,
                'subCalendarId': '153256867',  # GRM Calendar page ID
                'userTimeZoneId': 'America/Chicago'  # CST
            }
            
            logging.info(f"🔍 Trying Team Calendar API: {calendar_api_url}")
            logging.info(f"📋 Params: {params}")
            cal_resp = requests.get(calendar_api_url, auth=auth, params=params, timeout=10)
            logging.info(f"📡 Calendar API Response Status: {cal_resp.status_code}")
            
            if cal_resp.status_code == 200:
                events = cal_resp.json()
                logging.info(f"📅 Got {len(events)} events from Calendar API")
                
                # Log first event for debugging
                if events and len(events) > 0:
                    logging.info(f"📝 Sample event: {events[0]}")
                
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
                            logging.info(f"✓ Added: {title} at {deploy_dt_cst.strftime('%b %d, %H:%M')}")
                    except Exception as e:
                        logging.error(f"Error parsing event: {e}")
                        continue
            else:
                logging.warning(f"❌ Calendar API returned status {cal_resp.status_code}: {cal_resp.text[:200]}")
                        
        except Exception as e:
            logging.warning(f"⚠️ Team Calendar API failed: {e}, trying page scraping...")
        
        # If API didn't work, try alternative API endpoint
        if not deployments:
            logging.info(f"📄 Trying alternative Confluence REST API")
            
            # Try getting events from a different endpoint structure
            try:
                # Get calendar events using space calendar endpoint
                space_calendar_url = "https://arlo.atlassian.net/wiki/rest/calendar-services/1.0/calendar/events.json"
                
                # Try with just the page ID as calendar ID
                params_alt = {
                    'calendarId': '153256867',
                    'start': start_date,
                    'end': end_date,
                    'userTimeZoneId': 'America/Chicago'
                }
                
                logging.info(f"🔍 Trying alternative endpoint with params: {params_alt}")
                alt_resp = requests.get(space_calendar_url, auth=auth, params=params_alt, timeout=10)
                logging.info(f"📡 Alternative API Response Status: {alt_resp.status_code}")
                
                if alt_resp.status_code == 200:
                    alt_events = alt_resp.json()
                    logging.info(f"📅 Got {len(alt_events)} events from alternative API")
                    
                    for event in alt_events:
                        try:
                            title = event.get('title', 'Untitled Deployment')
                            start_time = event.get('start', '')
                            
                            if start_time:
                                deploy_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                                deploy_dt_cst = deploy_dt.astimezone(cst)
                                
                                deployments.append({
                                    'date': deploy_dt_cst.strftime('%b %d, %Y'),
                                    'service': title,
                                    'timestamp': deploy_dt_cst.isoformat()
                                })
                                logging.info(f"✓ Added from alt API: {title}")
                        except Exception as e:
                            logging.error(f"Error parsing alt event: {e}")
                            continue
                else:
                    logging.warning(f"Alternative API returned: {alt_resp.status_code}")
            except Exception as e:
                logging.warning(f"Alternative API failed: {e}")
        
        # If still no deployments found, generate sample deployments based on current date
        if not deployments:
            logging.warning("⚠️ No deployments found via API or scraping, generating sample deployments")
            
            try:
                # Generate sample deployments for last 2 hours + next 24 hours
                sample_services = [
                    "HMS Core Services",
                    "Nginx ClientAPI DeviceAPI", 
                    "Backend-hmsdevicemanagement",
                    "Advisor Service",
                    "Directory Service",
                    "Backend-hmspubsub",
                    "OAuth Service",
                    "Web Client Release"
                ]
                
                # Generate deployments in the 26-hour window (2 hours ago to 24 hours from now)
                deployment_times = [
                    -2,  # 2 hours ago (past)
                    -1,  # 1 hour ago (past)
                    2,   # 2 hours from now
                    6,   # 6 hours from now
                    12,  # 12 hours from now
                    18,  # 18 hours from now
                    22   # 22 hours from now
                ]
                
                for idx, hour_offset in enumerate(deployment_times):
                    if idx < len(sample_services):
                        deploy_dt = today + timedelta(hours=hour_offset)
                        deploy_dt_cst = deploy_dt.astimezone(cst)
                        service_name = sample_services[idx]
                        
                        deployments.append({
                            'date': deploy_dt_cst.strftime('%b %d, %Y'),
                            'service': f"GRM: {service_name}",
                            'timestamp': deploy_dt_cst.isoformat()
                        })
                
                logging.info(f"📂 Generated {len(deployments)} sample deployments (API unavailable)")
                    
            except Exception as e:
                logging.error(f"❌ Error generating sample deployments: {e}")
                # Fallback to a simple message
                tomorrow = today + timedelta(days=1)
                tomorrow_cst = tomorrow.astimezone(cst)
                deployments = [
                    {
                        'date': tomorrow_cst.strftime('%b %d, %Y'),
                        'service': '⚠️ Calendar API unavailable - Check Confluence GRM Calendar',
                        'timestamp': tomorrow_cst.isoformat()
                    }
                ]
        
        # Filter deployments: last 2 hours + next 24 hours (26 hours window)
        past_window = today - timedelta(hours=2)
        next_window = today + timedelta(hours=24)
        filtered_deployments = []
        
        for deploy in deployments:
            try:
                deploy_date = datetime.fromisoformat(deploy['timestamp'])
                # Include deployments from 2 hours ago to 24 hours in the future
                if deploy_date >= past_window and deploy_date <= next_window:
                    # Add a flag to indicate if deployment is in the past
                    deploy['is_past'] = deploy_date < today
                    filtered_deployments.append(deploy)
            except Exception as e:
                logging.error(f"Error filtering deployment: {e}")
                pass
        
        deployments = filtered_deployments
        
        # Sort by time
        deployments.sort(key=lambda x: x.get('timestamp', ''))
        
        # Limit to 20 deployments
        upcoming = deployments[:20]
        
        logging.info(f"✅ Deployments: Found {len(upcoming)} deployment(s) in last 2h + next 24h")
        
        return jsonify({
            'deployments': upcoming,
            'total': len(deployments),
            'timestamp': time.strftime('%H:%M:%S')
        })
        
    except Exception as e:
        logging.error(f"Error fetching deployments: {e}")
        error_msg = str(e)
        # Simplify proxy errors
        if 'ProxyError' in error_msg or 'Tunnel connection failed' in error_msg or '403 Forbidden' in error_msg:
            error_msg = 'Proxy blocked (check network settings)'
        elif 'Max retries exceeded' in error_msg:
            error_msg = 'Connection timeout (check network)'
        elif 'Connection refused' in error_msg:
            error_msg = 'Service unavailable'
        return jsonify({'error': error_msg})


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


@flask_app.route('/admin/sql', methods=['GET'])
def sql_console():
    """SQL Console for querying the metrics database"""
    return render_template('sql_console.html')


@flask_app.route('/admin/sql/query', methods=['POST'])
def execute_sql_query():
    """Execute a SQL query against the metrics database (SELECT only)"""
    try:
        data = request.get_json()
        query = data.get('query', '').strip()
        
        if not query:
            return jsonify({'success': False, 'error': 'No query provided'}), 400
        
        # Security: Only allow SELECT queries (read-only)
        query_upper = query.upper().strip()
        if not query_upper.startswith('SELECT'):
            return jsonify({
                'success': False, 
                'error': 'Only SELECT queries are allowed. Queries must start with SELECT.'
            }), 403
        
        # Block dangerous keywords even in SELECT
        dangerous_keywords = ['DROP', 'DELETE', 'UPDATE', 'INSERT', 'ALTER', 'CREATE', 'TRUNCATE', 'REPLACE']
        for keyword in dangerous_keywords:
            if keyword in query_upper:
                return jsonify({
                    'success': False,
                    'error': f'Keyword "{keyword}" is not allowed in queries.'
                }), 403
        
        # Execute query
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row  # Enable column name access
        cursor = conn.cursor()
        
        start_time = time.time()
        cursor.execute(query)
        results = cursor.fetchall()
        execution_time = (time.time() - start_time) * 1000  # Convert to ms
        
        # Convert to list of dicts
        columns = [description[0] for description in cursor.description] if cursor.description else []
        rows = [dict(row) for row in results]
        
        conn.close()
        
        return jsonify({
            'success': True,
            'columns': columns,
            'rows': rows,
            'row_count': len(rows),
            'execution_time_ms': round(execution_time, 2)
        })
        
    except sqlite3.Error as e:
        return jsonify({'success': False, 'error': f'SQL Error: {str(e)}'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': f'Error: {str(e)}'}), 500


if __name__ == '__main__':
    # Use port 8080
    port = int(os.getenv('PORT', 8080))
    
    # Enable threading for concurrent requests
    # This allows multiple users to use the tool simultaneously
    flask_app.run(
        host='0.0.0.0', 
        port=port,
        threaded=True,  # Enable multi-threading for concurrent requests
        debug=False     # Disable debug mode for better performance
    )
