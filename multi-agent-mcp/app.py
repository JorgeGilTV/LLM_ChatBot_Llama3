
from flask import Flask, request, jsonify, send_from_directory, send_file, render_template, Response
from flask_cors import CORS
import time
import sys
import os
import logging
import io
import threading
import uuid
import base64
import html
import json
import re
from datetime import datetime
from typing import Optional
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
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass
try:
    from tools.aws_secrets_env import load_aws_secrets_manager_into_environ
except ImportError:
    pass
else:
    try:
        load_aws_secrets_manager_into_environ()
    except Exception as e:
        if (os.getenv("AWS_SECRETS_MANAGER_REQUIRED") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            raise
        print(f"⚠️  AWS Secrets Manager (optional): {e}")
try:
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False
    print("⚠️ python-docx not installed. Download feature will be disabled.")

# Integrated tools
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
from tools.deployed_fw_versions import read_deployed_fw_versions
from tools.deployments_calendar import get_grm_deployments
from tools.datadog_dashboards import read_datadog_dashboards, read_datadog_errors_only, read_datadog_adt, read_datadog_adt_errors_only, read_datadog_samsung, read_datadog_samsung_errors_only, read_datadog_redmetrics_us, read_datadog_all_errors, read_datadog_failed_pods, read_datadog_403_errors, search_datadog_dashboards, search_datadog_services
from tools.splunk_tool import (
    read_splunk_p0_dashboard,
    read_splunk_p0_cvr_dashboard,
    read_splunk_p0_adt_dashboard,
    read_splunk_p0_us_infra_dashboard,
)
from tools.pagerduty_tool import get_pagerduty_incidents
from tools.pagerduty_analytics import get_pagerduty_analytics
from tools.pagerduty_insights import get_pagerduty_insights
from tools.grafana_dashboards import get_grafana_dns_mapper, get_grafana_savant_z2, get_grafana_dashboard_list
from tools.slack_http import format_slack_connection_error, post_incoming_webhook, post_slack_api

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
    "Owners": {"description": "Verify who owns each service", "function": service_owners_search},
    "Arlo_Versions": {"description": "Read version information from versions.arlocloud.com", "function": read_versions},
    "Deployed_FW_Versions": {"description": "Read deployed firmware/version matrix from deployed-fw-versions.arlocloud.com", "function": read_deployed_fw_versions},
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
    "P0_Streaming_US": {"description": "Show P0 Streaming US infra dashboard from Splunk", "function": read_splunk_p0_us_infra_dashboard},
    "Grafana_DNS_Mapper": {"description": "Monitor DNS Mapper IP usage for HMS/CVR streaming (z4)", "function": get_grafana_dns_mapper},
    "Grafana_Savant_z2": {"description": "Monitor Savant infrastructure in Harlem datacenter (z2)", "function": get_grafana_savant_z2},
    "Holiday_Oncall": {"description": "Get on-call schedule for holidays", "function": confluence_oncall_today},
    "PagerDuty": {"description": "Get active incidents from PagerDuty", "function": get_pagerduty_incidents},
    "PagerDuty_Dashboards": {"description": "Show PagerDuty analytics with charts and metrics", "function": get_pagerduty_analytics},
    "PagerDuty_Insights": {"description": "Show incident activity insights and trends", "function": get_pagerduty_insights},
    "Ask_Bedrock": {"description": "Ask AWS Bedrock (Claude 3.5 Sonnet) for AI-powered responses", "function": ask_bedrock},
    "Bedrock_Report": {"description": "AI-powered comprehensive analysis and synthesis", "function": ask_arlo},
}
registered_tools = [(name, tool["description"]) for name, tool in TOOLS.items()]

# Tools that share one Bedrock intent analysis per /api/run request (avoid N duplicate LLM calls)
_SERVICE_SPECIFIC_TOOLS = frozenset(
    {
        "DD_Errors",
        "DD_Red_Metrics",
        "DD_Failed_Pods",
        "DD_403_Errors",
        "DD_Red_ADT",
        "DD_Red_Samsung",
        "DD_Red_Metrics_US",
        "DD_Search",
        "DD_Services",
        "Arlo_Versions",
        "Owners",
    }
)
_TOOLS_WITH_TIMERANGE = frozenset(
    {
        "DD_Search",
        "DD_Services",
        "DD_Red_Metrics",
        "DD_Errors",
        "DD_Red_ADT",
        "DD_Red_Samsung",
        "DD_Red_Metrics_US",
        "DD_Failed_Pods",
        "DD_403_Errors",
        "P0_Streaming",
        "P0_CVR_Streaming",
        "P0_ADT_Streaming",
        "P0_Streaming_US",
        "Grafana_DNS_Mapper",
        "Grafana_Savant_z2",
    }
)

_bedrock_runtime_lock = threading.Lock()
_bedrock_runtime_client = None


def _get_bedrock_runtime_client():
    """Reuse boto3 client across requests (thread-safe for typical invoke_model usage)."""
    global _bedrock_runtime_client
    if _bedrock_runtime_client is not None:
        return _bedrock_runtime_client
    bedrock_api_key = os.getenv("BEDROCK_API_KEY")
    if not bedrock_api_key:
        return None
    with _bedrock_runtime_lock:
        if _bedrock_runtime_client is not None:
            return _bedrock_runtime_client
        _bedrock_runtime_client = boto3.client(
            service_name="bedrock-runtime",
            region_name="us-east-1",
            aws_access_key_id=bedrock_api_key.split(":")[0] if ":" in bedrock_api_key else bedrock_api_key,
            aws_secret_access_key=bedrock_api_key.split(":")[1]
            if ":" in bedrock_api_key and len(bedrock_api_key.split(":")) > 1
            else "",
        )
        return _bedrock_runtime_client


def _monitoring_tool_input_from_analysis(user_query: str, analysis: dict) -> str:
    if analysis.get("is_general_query"):
        return ""
    if analysis.get("service_name"):
        return analysis["service_name"]
    return user_query


# ✅ Flask App
flask_app = Flask(__name__, template_folder='templates')
CORS(flask_app)

# Unified message: Docker image omits .env (.dockerignore); inject vars or mount .env on the host.
_SLACK_WEBHOOK_MISSING_MSG = (
    "SLACK_WEBHOOK_URL is not set. "
    "The Docker image does not include .env: set SLACK_WEBHOOK_URL on the host and run "
    "docker run ... --env-file .env, or use docker-compose (env_file: .env). "
    "See .env.example."
)

# Short-lived PNG cache for Slack (webhook has no multipart; Slack fetches image_url)
_SLACK_SCREENSHOT_CACHE = {}
_SLACK_SCREENSHOT_LOCK = threading.Lock()
_SLACK_SCREENSHOT_TTL_SEC = 300


def _slack_screenshot_prune_locked():
    now = time.time()
    dead = [k for k, (_, exp) in _SLACK_SCREENSHOT_CACHE.items() if exp < now]
    for k in dead:
        del _SLACK_SCREENSHOT_CACHE[k]


def _slack_screenshot_store_png(data: bytes) -> str:
    sid = uuid.uuid4().hex
    exp = time.time() + _SLACK_SCREENSHOT_TTL_SEC
    with _SLACK_SCREENSHOT_LOCK:
        _slack_screenshot_prune_locked()
        _SLACK_SCREENSHOT_CACHE[sid] = (data, exp)
    return sid


def _slack_screenshot_get_png(sid: str):
    with _SLACK_SCREENSHOT_LOCK:
        _slack_screenshot_prune_locked()
        t = _SLACK_SCREENSHOT_CACHE.get(sid)
        if not t:
            return None
        data, exp = t
        if time.time() > exp:
            del _SLACK_SCREENSHOT_CACHE[sid]
            return None
        return data


def _slack_screenshot_public_base_url():
    """HTTPS base URL where Slack can download the PNG (this app reachable from the internet)."""
    base = (
        os.getenv("PUBLIC_BASE_URL")
        or os.getenv("SLACK_IMAGE_PUBLIC_BASE_URL")
        or ""
    ).strip().rstrip("/")
    if base:
        return base
    return request.url_root.rstrip("/")


alerts_db = []  # {id, text, priority, ack, cause}

def classify_alert(text):
    text_lower = text.lower()
    if any(k in text_lower for k in ['Sev1', 'Sev0']):
        return 'High'
    if any(k in text_lower for k in ['Sev3', 'Sev2']):
        return 'Medium'
    return 'Low'


def identify_cause(text):
    try:
        # Run real tools with the alert text
        # wiki_info = TOOLS["Wiki"]["function"](text)
        confluence_info = TOOLS["Wiki"]["function"](text)
        suggestion = TOOLS["Suggestions"]["function"](text)

        return f""" 
        <div>
            <h4>Suggested root cause:</h4>{suggestion}
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
    """Hub: overview of each environment; click through to /statusmonitor/<env>."""
    return render_template('statusmonitor.html', environment=None)


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


@flask_app.route('/statusmonitor/qa')
def statusmonitor_qa_page():
    """Serve the status monitor dashboard for env:qa (cluster/platform list)"""
    return render_template('statusmonitor.html', environment='qa')


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


@flask_app.route('/statuswall')
def statuswall_page():
    """Full-screen wall: all environments as status tiles only (no hub chrome)."""
    return render_template('statuswall.html')


@flask_app.route('/statuswall/preview')
def statuswall_preview_page():
    """Static mock of the status wall layout (no Datadog/PagerDuty)."""
    return render_template('statuswall_preview.html')


@flask_app.route('/apm-services')
def apm_services_page():
    """
    APM Status Wall: APM + PagerDuty for a chosen APM `env` tag
    (production, goldendev, goldenqa, adt_prod, qa, samsung_prod: bundled lists). ?dd_env= or APM_STATUS_WALL_DD_ENV.
    See SOFTWARE_CATALOG_* and lists/*_apm_services.txt.
    """
    from tools.status_monitor import normalize_software_catalog_wall_dd_env
    import re

    q = (request.args.get("dd_env") or os.environ.get("APM_STATUS_WALL_DD_ENV") or "").strip()
    wall_dd_env = normalize_software_catalog_wall_dd_env(q or "production")
    dd_base = (os.environ.get("DATADOG_APM_SOFTWARE_BASE") or "").strip()
    if not dd_base:
        datadog_software_href = (
            f"https://arlo.datadoghq.com/software?env={wall_dd_env}&fromUser=true"
        )
    else:
        if re.search(r"[?&]env=", dd_base):
            datadog_software_href = re.sub(
                r"env=[^&]*", f"env={wall_dd_env}", dd_base, count=1
            )
        else:
            sep = "&" if "?" in dd_base else "?"
            datadog_software_href = f"{dd_base}{sep}env={wall_dd_env}"
            if "fromUser" not in datadog_software_href:
                qm = "?" if "?" not in datadog_software_href else "&"
                datadog_software_href = f"{datadog_software_href}{qm}fromUser=true"
    _slack = (
        f"APM Status Wall — {wall_dd_env}" if wall_dd_env != "production" else
        "APM Status Wall — production"
    )
    return render_template(
        "statuswall.html",
        wall_title="APM Status Wall",
        wall_api="/api/statusmonitor/software-catalog-wall",
        wall_nav="apm_wall",
        wall_slack_title=_slack,
        wall_show_apm_env=True,
        wall_dd_env=wall_dd_env,
        datadog_software_href=datadog_software_href,
    )


@flask_app.route('/api/statusmonitor', methods=['POST'])
def api_statusmonitor():
    """API endpoint for status monitor dashboard data"""
    try:
        from tools.status_monitor import status_monitor_dashboard
        
        data = request.get_json() or {}
        timerange = data.get('timerange', 1)
        environment = data.get('environment', None)  # Optional: specific environment
        force_refresh = bool(data.get('force_refresh') or data.get('forceRefresh'))
        
        html_content = status_monitor_dashboard(
            timerange=timerange, environment=environment, force_refresh=force_refresh
        )
        
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


@flask_app.route('/api/statusmonitor/hub-summary', methods=['POST'])
def api_statusmonitor_hub_summary():
    """JSON summary for /statusmonitor hub (one row per environment, Datadog health)."""
    try:
        from tools.status_monitor import status_monitor_hub_summary

        data = request.get_json() or {}
        timerange = int(data.get('timerange', 1))
        force_refresh = bool(data.get('force_refresh') or data.get('forceRefresh'))
        return jsonify(status_monitor_hub_summary(timerange=timerange, force_refresh=force_refresh))
    except Exception as e:
        logging.error(f"Error in status monitor hub summary: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@flask_app.route('/api/statusmonitor/wall', methods=['POST'])
def api_statusmonitor_wall():
    """JSON payload for /statuswall (grouped service tiles, same health logic as hub)."""
    try:
        from tools.status_monitor import status_monitor_wall_data

        data = request.get_json() or {}
        timerange = int(data.get('timerange', 1))
        force_refresh = bool(data.get('force_refresh') or data.get('forceRefresh'))
        return jsonify(status_monitor_wall_data(timerange=timerange, force_refresh=force_refresh))
    except Exception as e:
        logging.error(f"Error in status monitor wall: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@flask_app.route("/api/statusmonitor/software-catalog-wall", methods=["POST"])
def api_statusmonitor_software_catalog_wall():
    """
    JSON for /apm-services: APM Status Wall × env:production|goldendev|goldenqa|adt_prod|qa|samsung_prod; same
    APM+PD rules as the main status wall. Body: dd_env (optional, default production).
    """
    try:
        from tools.status_monitor import (
            normalize_software_catalog_wall_dd_env,
            status_monitor_software_catalog_wall_data,
        )

        data = request.get_json() or {}
        timerange = int(data.get("timerange", 1))
        force_refresh = bool(
            data.get("force_refresh") or data.get("forceRefresh")
        )
        raw_dd = data.get("dd_env") or data.get("ddEnv")
        dd_e = normalize_software_catalog_wall_dd_env(
            (raw_dd if raw_dd is not None else "production")
        )
        return jsonify(
            status_monitor_software_catalog_wall_data(
                timerange=timerange, force_refresh=force_refresh, dd_env=dd_e
            )
        )
    except Exception as e:
        logging.error(f"Error in software catalog wall: {e}")
        import traceback

        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


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
    """
    Liveness-style check for load balancers and Docker HEALTHCHECK.

    Always returns HTTP 200 if this worker can handle requests. SQLite metrics
    are optional: failures are reported as status 'degraded' so ALB/Ingress
    does not drop the target (which would surface as 503 for every route).
    """
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
        logging.warning("Health check: metrics DB unavailable: %s", e)
        return jsonify({
            'status': 'degraded',
            'timestamp': datetime.utcnow().isoformat(),
            'database': {'error': str(e)},
            'version': '3.0.2'
        })


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
- Deployed_FW_Versions: Deployed firmware / version matrix from deployed-fw-versions.arlocloud.com
- Owners: Service ownership information

**AI ASSISTANTS:**
- Ask_Bedrock: General AI explanations (no data lookup)
- Bedrock_Report: Intelligent MCP tool selection and execution

🎯 SELECTION STRATEGY:

For COMPREHENSIVE SERVICE INFO (cluster, owner, metrics, errors):
→ Consider: Bedrock_Report, DD_Red_Metrics, DD_Search, DD_Services, DD_Errors, DD_Failed_Pods, Arlo_Versions, Deployed_FW_Versions, Owners

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
   → Can include: Arlo_Versions, Deployed_FW_Versions, Owners, DD_Failed_Pods, PagerDuty

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
            bedrock_runtime = _get_bedrock_runtime_client()
            if not bedrock_runtime:
                logging.warning("⚠️ BEDROCK_API_KEY not available for query analysis")
                return {'is_general_query': False, 'service_name': user_query, 'confidence': 'low'}
            
            analysis_prompt = f"""Analyze this user query and extract the intent for monitoring tool execution.

User Query: "{user_query}"

Determine:
1. Is this a GENERAL query asking for ALL services/dashboards? (e.g., "all services", "all zones", "general status", "show everything", "red metrics for all regions")
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
- "give me results for all regions" → {{"is_general_query": true, "service_name": "", "confidence": "high"}}
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
    
    def execute_tool(idx, tool_name, context_from_other_tools=None, monitoring_input_override=None):
        """Execute a single tool and store result. monitoring_input_override avoids duplicate Bedrock calls."""
        func = TOOLS.get(tool_name, {}).get('function')
        if not func:
            return idx, tool_name, f"<pre>No tool found for {tool_name}</pre>", True
        
        try:
            tool_input = input_text
            if tool_name in _SERVICE_SPECIFIC_TOOLS:
                if monitoring_input_override is not None:
                    tool_input = monitoring_input_override
                else:
                    analysis = analyze_query_with_bedrock(input_text)
                    tool_input = _monitoring_tool_input_from_analysis(input_text, analysis)
            
            if tool_name in _TOOLS_WITH_TIMERANGE:
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
    
    monitoring_input_override = None
    if data_tool_indices and any(t in _SERVICE_SPECIFIC_TOOLS for _, t in data_tool_indices):
        _analysis = analyze_query_with_bedrock(input_text)
        monitoring_input_override = _monitoring_tool_input_from_analysis(input_text, _analysis)
        logging.info(
            "🤖 Bedrock query analysis (once per request): general=%s service=%r confidence=%s",
            _analysis.get("is_general_query"),
            _analysis.get("service_name", ""),
            _analysis.get("confidence", ""),
        )
    
    # Phase 1: Execute data tools in parallel
    context_for_synthesis = {}
    if data_tool_indices:
        logging.info(f"📊 Phase 1: Executing {len(data_tool_indices)} data tool(s) in parallel...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_to_tool = {
                executor.submit(execute_tool, idx, tool_name, None, monitoring_input_override): (idx, tool_name)
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
                executor.submit(
                    execute_tool,
                    idx,
                    tool_name,
                    context_for_synthesis if context_for_synthesis else None,
                    None,
                ): (idx, tool_name)
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

def _pagerduty_external_status_incidents_url(dashboard_id: str, tab=None):
    """Public UI URL: …/external-status-dashboard/{id}/incidents?tab=… (active=ongoing, resolved, pending)."""
    raw = (os.getenv("PAGERDUTY_SUBDOMAIN") or "arlo").strip()
    raw = raw.replace("https://", "").replace("http://", "").split("/")[0]
    sub = (raw.split(".")[0] if raw else "arlo") or "arlo"
    base = f"https://{sub}.pagerduty.com/external-status-dashboard/{dashboard_id}/incidents"
    if tab in ("active", "resolved", "pending"):
        base += f"?tab={tab}"
    return base


def _samsung_status_dashboard_id():
    """External status board id for Samsung widget (REST: status_dashboard_ids[])."""
    v = os.getenv("SAMSUNG_STATUS_DASHBOARD_ID")
    if v is None:
        return "PRBJIO4"
    s = str(v).strip()
    if not s or s.lower() in ("off", "false", "no", "0", "none", "*"):
        return None
    return s


def _adt_status_dashboard_id():
    """External status board id for ADT status widget (REST: status_dashboard_ids[])."""
    v = os.getenv("ADT_STATUS_DASHBOARD_ID")
    if v is None:
        return "PK1QF1G"
    s = str(v).strip()
    if not s or s.lower() in ("off", "false", "no", "0", "none", "*"):
        return None
    return s


def _pagerduty_monitor_payload(dashboard_id):
    """
    Same PagerDuty logic as the status monitor semaphore: get_pagerduty_status_counts /
    build_pagerduty_monitor_api_payload in tools.status_monitor (24h window, total=true,
    retries, SQLite cache). dashboard_id=None = whole account; else external status board.
    """
    try:
        api_token = os.getenv("PAGERDUTY_API_TOKEN")
        if not api_token:
            return {"error": "PagerDuty token not configured"}
        from tools.status_monitor import build_pagerduty_monitor_api_payload

        payload = build_pagerduty_monitor_api_payload(api_token, dashboard_id)
        if payload.get("error"):
            return payload
        if dashboard_id:
            payload["status_dashboard_id"] = dashboard_id
            payload["status_dashboard_url"] = _pagerduty_external_status_incidents_url(dashboard_id)
            payload["status_dashboard_url_active"] = _pagerduty_external_status_incidents_url(
                dashboard_id, tab="active"
            )
            payload["status_dashboard_url_resolved"] = _pagerduty_external_status_incidents_url(
                dashboard_id, tab="resolved"
            )
            payload["status_dashboard_url_pending"] = _pagerduty_external_status_incidents_url(
                dashboard_id, tab="pending"
            )
        return payload
    except Exception as e:
        logging.error(f"Error in PagerDuty monitor payload: {e}")
        error_msg = str(e)
        if "ProxyError" in error_msg or "Tunnel connection failed" in error_msg or "403 Forbidden" in error_msg:
            error_msg = "Proxy blocked (check network settings)"
        elif "Max retries exceeded" in error_msg:
            error_msg = "Connection timeout (check network)"
        elif "Connection refused" in error_msg:
            error_msg = "Service unavailable"
        return {"error": error_msg}


def _jsonify_pagerduty_monitor(data: dict):
    if data.get("error"):
        return jsonify(data)
    return jsonify(data)


@flask_app.route('/api/pagerduty/monitor')
def api_pagerduty_monitor():
    """All-account PagerDuty incidents (no external status board filter)."""
    return _jsonify_pagerduty_monitor(_pagerduty_monitor_payload(None))


@flask_app.route('/api/pagerduty/samsung-monitor')
def api_pagerduty_samsung_monitor():
    """Samsung external status board: same UI data as monitor but scoped to SAMSUNG_STATUS_DASHBOARD_ID."""
    bid = _samsung_status_dashboard_id()
    if not bid:
        return jsonify(
            {
                "error": "Samsung status disabled",
                "disabled": True,
                "hint": "Set SAMSUNG_STATUS_DASHBOARD_ID (default PRBJIO4) or remove it to use the default.",
            }
        )
    return _jsonify_pagerduty_monitor(_pagerduty_monitor_payload(bid))


@flask_app.route('/api/pagerduty/adt-monitor')
def api_pagerduty_adt_monitor():
    """ADT external status board: scoped to ADT_STATUS_DASHBOARD_ID (default PK1QF1G)."""
    bid = _adt_status_dashboard_id()
    if not bid:
        return jsonify(
            {
                "error": "ADT status disabled",
                "disabled": True,
                "hint": "Set ADT_STATUS_DASHBOARD_ID (default PK1QF1G) or remove it to use the default.",
            }
        )
    return _jsonify_pagerduty_monitor(_pagerduty_monitor_payload(bid))


@flask_app.route("/api/slack/send-results", methods=["POST"])
def api_slack_send_results():
    """Send result to Slack (Incoming Webhook): plain text or Block Kit with mrkdwn (no raw HTML)."""
    webhook = (os.getenv("SLACK_WEBHOOK_URL") or "").strip()
    if not webhook:
        return jsonify(
            {"success": False, "error": _SLACK_WEBHOOK_MISSING_MSG}
        ), 503

    payload = request.get_json(silent=True) or {}
    text = payload.get("text")
    if text is None:
        text = ""
    if not isinstance(text, str):
        text = str(text)
    text = text.strip()

    mrkdwn_raw = payload.get("mrkdwn")
    mrkdwn_body = ""
    if isinstance(mrkdwn_raw, str):
        mrkdwn_body = mrkdwn_raw.strip()

    if not text and not mrkdwn_body:
        return jsonify({"success": False, "error": "No text to send"}), 400

    max_len = 38000
    if len(mrkdwn_body) > max_len:
        mrkdwn_body = mrkdwn_body[:max_len] + "\n\n_[... truncated for Slack ...]_"
    if mrkdwn_body and len(text) > 4000:
        text = text[:3997] + "..."

    try:
        if mrkdwn_body:
            fallback = text or (mrkdwn_body[:500] + ("…" if len(mrkdwn_body) > 500 else ""))
            if len(fallback) > 4000:
                fallback = fallback[:3997] + "..."
            chunk_size = 2900
            blocks = [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "OneView GOC AI", "emoji": True},
                },
                {"type": "divider"},
            ]
            i = 0
            while i < len(mrkdwn_body) and len(blocks) < 49:
                blocks.append(
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": mrkdwn_body[i : i + chunk_size]},
                    }
                )
                i += chunk_size
            if i < len(mrkdwn_body):
                blocks.append(
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": "_[... additional content omitted (Slack limit) ...]_"},
                    }
                )
            slack_payload = {"text": fallback, "blocks": blocks}
        else:
            main = text
            if len(main) > max_len:
                main = main[:max_len] + "\n\n[... truncated for Slack ...]"
            slack_payload = {"text": main}

        r = post_incoming_webhook(webhook, slack_payload, timeout=(15, 45))
        if r.status_code != 200:
            logging.warning("Slack webhook HTTP %s: %s", r.status_code, (r.text or "")[:300])
            return jsonify(
                {
                    "success": False,
                    "error": f"Slack returned HTTP {r.status_code}",
                }
            ), 502
        return jsonify({"success": True})
    except Exception as e:
        logging.error("Slack webhook: %s", e)
        return jsonify({"success": False, "error": format_slack_connection_error(e)}), 500


def _slack_safe_mrkdwn_snippet(s: str, max_len: int = 400) -> str:
    """Avoid breaking Slack mrkdwn in context lines (* and _ are special)."""
    if not s:
        return ""
    t = str(s).replace("&", "&amp;").replace("*", "·").replace("_", " ").replace("`", "'")
    t = " ".join(t.split())
    return t[:max_len] + ("…" if len(t) > max_len else "")


def _slack_summary_mrkdwn_chunks(summary: str, max_chunk: int = 2900) -> list[str]:
    """Split on paragraphs when possible so mrkdwn (*bold*) is less often cut mid-token."""
    summary = (summary or "").strip()
    if not summary:
        return []
    if len(summary) <= max_chunk:
        return [summary]
    paras = summary.split("\n\n")
    out = []
    buf = ""
    for p in paras:
        p = p.strip()
        if not p:
            continue
        candidate = (buf + "\n\n" + p) if buf else p
        if len(candidate) <= max_chunk:
            buf = candidate
            continue
        if buf:
            out.append(buf)
            buf = ""
        if len(p) <= max_chunk:
            buf = p
        else:
            start = 0
            while start < len(p):
                out.append(p[start : start + max_chunk])
                start += max_chunk
    if buf:
        out.append(buf)
    return out


def _slack_summary_blocks_payload(summary: str, page_title: str, source_url: str) -> dict:
    """Block Kit: header, context, divider, section(s) with mrkdwn for a richer Slack message."""
    chunks = _slack_summary_mrkdwn_chunks(summary, 2900)
    if not chunks:
        chunks = [""]

    fallback = "📊 OneView — AI summary (Bedrock)"
    if page_title:
        fallback += " · " + _slack_safe_mrkdwn_snippet(page_title, 120)

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "📊 OneView · AI summary", "emoji": True},
        },
    ]

    ctx_lines = []
    if page_title:
        ctx_lines.append(f"📌 {_slack_safe_mrkdwn_snippet(page_title, 350)}")
    if source_url:
        su = source_url.strip().replace("|", "%7C")
        ctx_lines.append(f"<{su}|Open in browser>")
    if ctx_lines:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "\n".join(ctx_lines)}],
            }
        )

    blocks.append({"type": "divider"})

    for part in chunks:
        part = (part or "").strip()
        if not part:
            continue
        if len(blocks) >= 48:
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "_…message truncated for Slack…_"},
                }
            )
            break
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": part}})

    return {"text": fallback[:4000], "blocks": blocks}


@flask_app.route("/api/slack/summarize-and-send", methods=["POST"])
def api_slack_summarize_and_send():
    """
    Plain text from client → AWS Bedrock summary (English, mrkdwn-friendly) → Slack webhook with Block Kit.
    Requires SLACK_WEBHOOK_URL and BEDROCK_API_KEY.
    """
    webhook = (os.getenv("SLACK_WEBHOOK_URL") or "").strip()
    if not webhook:
        return jsonify(
            {"success": False, "error": _SLACK_WEBHOOK_MISSING_MSG}
        ), 503

    data = request.get_json(silent=True) or {}
    raw = data.get("page_text")
    if raw is None:
        raw = ""
    if not isinstance(raw, str):
        raw = str(raw)
    page_text = raw.strip()

    pt = data.get("page_title")
    page_title = pt.strip() if isinstance(pt, str) else ""
    su = data.get("source_url")
    source_url = su.strip() if isinstance(su, str) else ""

    if not page_text:
        return jsonify({"success": False, "error": "page_text is empty"}), 400

    try:
        max_in = int(os.getenv("SLACK_SUMMARY_INPUT_MAX_CHARS", "100000"))
    except (TypeError, ValueError):
        max_in = 100000
    max_in = max(5000, min(max_in, 200000))
    if len(page_text) > max_in:
        page_text = page_text[:max_in] + "\n\n[... content truncated for summary ...]"

    try:
        from tools.bedrock_tool import summarize_page_for_slack

        summary = summarize_page_for_slack(page_text, page_title, source_url)
    except Exception as e:
        logging.exception("Slack summarize Bedrock")
        return jsonify({"success": False, "error": str(e)}), 500

    err_markers = (
        summary.startswith("Error:"),
        summary.startswith("AWS Bedrock Error"),
        "Bedrock is not working" in summary,
    )
    if any(err_markers):
        return jsonify({"success": False, "error": summary[:2000]}), 502

    slack_payload = _slack_summary_blocks_payload(summary, page_title, source_url)

    try:
        r = post_incoming_webhook(webhook, slack_payload, timeout=(15, 90))
        if r.status_code != 200:
            logging.warning("Slack webhook HTTP %s: %s", r.status_code, (r.text or "")[:300])
            return jsonify(
                {"success": False, "error": f"Slack returned HTTP {r.status_code}"},
            ), 502
        return jsonify({"success": True})
    except Exception as e:
        logging.error("Slack summarize webhook post: %s", e)
        return jsonify({"success": False, "error": format_slack_connection_error(e)}), 500


@flask_app.route("/api/slack/screenshot-image/<sid>", methods=["GET"])
def api_slack_screenshot_image(sid):
    """Temporary PNG URL for Slack (Incoming Webhook) to fetch image_url."""
    if not re.match(r"^[a-f0-9]{32}$", sid):
        return jsonify({"error": "invalid id"}), 400
    data = _slack_screenshot_get_png(sid)
    if not data:
        return jsonify({"error": "expired or not found"}), 404
    return Response(
        data,
        mimetype="image/png",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@flask_app.route("/api/slack/send-screenshot", methods=["POST"])
def api_slack_send_screenshot():
    """
    Screenshots via the same Slack Incoming Webhook as text:
    stores the PNG for a few minutes and posts an attachment with image_url (Slack fetches the URL).

    The app must be reachable from the internet over HTTPS (set PUBLIC_BASE_URL if needed).
    Optional: files.upload only if SLACK_BOT_TOKEN starts with xoxb- and SLACK_CHANNEL_ID is set.
    """
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file received (field: file)"}), 400
    upload = request.files["file"]
    if not upload or not upload.filename:
        return jsonify({"success": False, "error": "Empty file"}), 400

    data = upload.read()
    max_bytes = 8 * 1024 * 1024
    if len(data) > max_bytes:
        return jsonify({"success": False, "error": "Image too large (max 8 MB)"}), 400

    caption = (request.form.get("caption") or "").strip() or "OneView GOC AI — screenshot"
    caption = caption[:500]

    webhook = (os.getenv("SLACK_WEBHOOK_URL") or "").strip()

    try:
        if webhook:
            sid = _slack_screenshot_store_png(data)
            base = _slack_screenshot_public_base_url()
            if not base:
                return jsonify(
                    {"success": False, "error": "Could not resolve public base URL for this server"}), 500
            image_url = f"{base}/api/slack/screenshot-image/{sid}"
            # Slack only embeds image_url over public HTTPS. With http://localhost the message
            # still delivers if we include the link in text (open PNG in browser).
            if image_url.startswith("https://"):
                payload = {
                    "text": caption,
                    "attachments": [
                        {
                            "image_url": image_url,
                            "fallback": "OneView screenshot",
                        }
                    ],
                }
            else:
                logging.warning(
                    "screenshot Slack: URL not HTTPS (%s) — sending link in text (Slack does not embed HTTP). "
                    "In production set PUBLIC_BASE_URL=https://your-domain",
                    image_url[:160],
                )
                payload = {
                    "text": (
                        f"{caption}\n\n"
                        f"PNG screenshot (temporary ~{_SLACK_SCREENSHOT_TTL_SEC}s): {image_url}"
                    ),
                }
            r = post_incoming_webhook(webhook, payload, timeout=(15, 45))
            if r.status_code != 200:
                logging.warning(
                    "Slack webhook screenshot HTTP %s: %s",
                    r.status_code,
                    (r.text or "")[:400],
                )
                return jsonify(
                    {
                        "success": False,
                        "error": f"Slack returned HTTP {r.status_code}: {(r.text or '')[:200]}",
                    }
                ), 502
            return jsonify({"success": True})

        token = (os.getenv("SLACK_BOT_TOKEN") or "").strip()
        channel = (
            os.getenv("SLACK_CHANNEL_ID") or os.getenv("SLACK_SCREENSHOT_CHANNEL") or ""
        ).strip()
        if token.startswith("xoxb-") and channel:
            files_part = {
                "file": ("oneview-screenshot.png", io.BytesIO(data), "image/png"),
            }
            r = post_slack_api(
                "https://slack.com/api/files.upload",
                headers={"Authorization": f"Bearer {token}"},
                data={
                    "channels": channel,
                    "initial_comment": caption,
                },
                files=files_part,
                timeout=(15, 90),
            )
            try:
                body = r.json()
            except Exception:
                logging.warning("Slack files.upload no-JSON: %s", (r.text or "")[:400])
                return jsonify(
                    {"success": False, "error": f"Slack returned HTTP {r.status_code}"}
                ), 502
            if not body.get("ok"):
                err = body.get("error") or "unknown_error"
                logging.warning("Slack files.upload error: %s", err)
                return jsonify(
                    {"success": False, "error": f"Slack: {err}"}
                ), 502
            return jsonify({"success": True})

        return jsonify(
            {
                "success": False,
                "error": (
                    "Set SLACK_WEBHOOK_URL (recommended for screenshots) or a xoxb- bot + SLACK_CHANNEL_ID. "
                    "In Docker use --env-file .env or env_file in compose; the image does not ship .env."
                ),
            }
        ), 503
    except Exception as e:
        logging.error("Slack screenshot upload: %s", e)
        return jsonify({"success": False, "error": format_slack_connection_error(e)}), 500


@flask_app.route("/api/splunk/monitor")
def api_splunk_monitor():
    """Sidebar: P0 Splunk tools — outlier counts per zone (predict band), same SPL as chat tools."""
    try:
        from tools.splunk_tool import splunk_outliers_monitor_payload

        tr = request.args.get("timerange", default=72, type=int)
        if tr is None or tr < 4:
            tr = 72
        return jsonify(splunk_outliers_monitor_payload(tr))
    except Exception as e:
        logging.error("Error in Splunk monitor: %s", e)
        return jsonify(
            {
                "success": False,
                "error": str(e),
                "tools": [],
            }
        )


@flask_app.route('/api/public-ip', methods=['GET'])
def get_public_ip():
    """Public egress IP plus hints to reach this app (SSH, curl, base URL)."""
    try:
        import socket

        import requests

        services = [
            'https://api.ipify.org?format=json',
            'https://ipinfo.io/json',
            'https://ifconfig.me/all.json',
        ]

        ip = None
        service_used = None
        for service in services:
            try:
                response = requests.get(service, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    ip = data.get('ip') or data.get('ip_addr')
                    if ip:
                        service_used = service
                        break
            except Exception:
                continue

        if not ip:
            return jsonify({'error': 'Unable to fetch public IP'}), 500

        base = request.url_root.rstrip('/')
        try:
            host_name = socket.gethostname()
        except Exception:
            host_name = ''

        connect = {
            'hostname': host_name,
            'app_base_url': base,
            'note': (
                'This is the server public egress IP (useful for firewall rules). '
                'If you access via load balancer or domain, use the browser URL. '
                'SSH works only if port 22 is open and you have user/key access.'
            ),
            'ssh_examples': [
                {
                    'label': 'SSH (replace user and auth method)',
                    'command': f'ssh <user>@{ip}',
                },
                {
                    'label': 'Amazon Linux / AL2023 (typical on EC2)',
                    'command': f'ssh ec2-user@{ip}',
                },
                {
                    'label': 'Ubuntu cloud AMI',
                    'command': f'ssh ubuntu@{ip}',
                },
            ],
            'curl_health': f'curl -sS "{base}/api/health"',
            'docker_hint': (
                '# If the app runs in Docker on this host (adjust container name):\n'
                '# docker ps\n'
                '# docker exec -it <container_name> /bin/sh'
            ),
        }

        return jsonify({'ip': ip, 'service': service_used, 'connect': connect})
    except Exception as e:
        logging.error(f"Error fetching public IP: {e}")
        return jsonify({'error': str(e)}), 500

def _normalize_team_calendar_events(payload) -> list:
    """Team Calendar /events.json may return a list or an object with nested arrays."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("events", "data", "values", "results", "items", "eventList"):
            v = payload.get(key)
            if isinstance(v, list):
                return v
        if payload.get("start") or payload.get("title"):
            return [payload]
    return []


def _parse_team_calendar_datetime(val) -> Optional[datetime]:
    """Parse start/end from Team Calendar: ISO string, epoch ms, or nested dict."""
    from datetime import datetime, timezone

    if val is None:
        return None
    if isinstance(val, (int, float)):
        sec = val / 1000.0 if val > 1e12 else float(val)
        try:
            return datetime.fromtimestamp(sec, tz=timezone.utc)
        except (OSError, ValueError, OverflowError):
            return None
    if isinstance(val, dict):
        for k in ("iso", "iso8601", "dateTime", "date", "value", "epochMillis", "millis"):
            if k in val and val[k] not in (None, ""):
                parsed = _parse_team_calendar_datetime(val[k])
                if parsed:
                    return parsed
        return None
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        # All-day events: YYYY-MM-DD only
        if len(s) == 10 and s[4] == "-" and s[7] == "-":
            try:
                from datetime import date as date_cls

                d0 = date_cls.fromisoformat(s)
                return datetime(d0.year, d0.month, d0.day, tzinfo=timezone.utc)
            except ValueError:
                pass
        s = s.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
        try:
            dt = datetime.fromisoformat(s.replace(" ", "T", 1))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None
    return None


def _team_calendar_event_start_end(event: dict) -> tuple[Optional[datetime], Optional[datetime]]:
    """Return (start, end) as timezone-aware UTC from various Team Calendar field names."""
    from datetime import timedelta

    start_dt = None
    end_dt = None
    for sk in ("start", "startDate", "startTime", "from", "begin", "startMillis"):
        if sk in event and event[sk] not in (None, ""):
            start_dt = _parse_team_calendar_datetime(event[sk])
            if start_dt:
                break
    if start_dt is None and isinstance(event.get("start"), dict):
        start_dt = _parse_team_calendar_datetime(event["start"])
    for ek in ("end", "endDate", "endTime", "to", "finish", "endMillis"):
        if ek in event and event[ek] not in (None, ""):
            end_dt = _parse_team_calendar_datetime(event[ek])
            if end_dt:
                break
    if end_dt is None and isinstance(event.get("end"), dict):
        end_dt = _parse_team_calendar_datetime(event["end"])
    if start_dt and end_dt is None:
        end_dt = start_dt + timedelta(hours=2)
    return start_dt, end_dt


def _grm_event_to_deployment(event: dict, cst) -> Optional[dict]:
    """Build one deployment dict from Team Calendar API event (start/end in ISO UTC)."""
    title = (
        event.get("title")
        or event.get("summary")
        or event.get("name")
        or "Untitled Deployment"
    )
    if not isinstance(title, str):
        title = str(title)

    start_dt, end_dt = _team_calendar_event_start_end(event)
    if start_dt is None:
        return None

    deploy_dt_cst = start_dt.astimezone(cst)
    if end_dt is None:
        from datetime import timedelta

        end_dt = start_dt + timedelta(hours=2)
    end_cst = end_dt.astimezone(cst)
    return {
        "date": deploy_dt_cst.strftime("%b %d, %Y"),
        "service": title,
        "timestamp": deploy_dt_cst.isoformat(),
        "end_timestamp": end_cst.isoformat(),
    }


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
        use_mock_deployments = os.getenv("DEPLOYMENTS_USE_MOCK_DATA", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        
        email = (os.getenv("ATLASSIAN_EMAIL") or "").strip()
        token = (os.getenv("CONFLUENCE_TOKEN") or "").strip()
        sub_calendar_id = (os.getenv("DEPLOYMENTS_SUBCALENDAR_ID") or "153256867").strip()

        if not email or not token:
            return jsonify(
                {
                    "deployments": [],
                    "total": 0,
                    "timestamp": time.strftime("%H:%M:%S"),
                    "source": "no_credentials",
                    "warning": (
                        "Confluence is not configured: set ATLASSIAN_EMAIL and CONFLUENCE_TOKEN "
                        "in .env (or container env) and restart."
                    ),
                }
            )

        auth = (email, token)
        today = datetime.now(timezone.utc)

        diag = {
            "sub_calendar_id": sub_calendar_id,
            "primary_http_status": None,
            "alt_http_status": None,
            "raw_events_primary": 0,
            "raw_events_alt": 0,
            "calendar_start_date": None,
            "calendar_end_date": None,
            "sample_event_keys": None,
        }

        deployments = []

        # Date-only range for Team Calendar API: must span enough calendar days (CST) so events
        # are not dropped when UTC day boundaries differ from Chicago. Filter overlap happens later.
        now_cst = datetime.now(cst)
        start_date = (now_cst - timedelta(days=1)).strftime("%Y-%m-%d")
        end_date = (now_cst + timedelta(days=3)).strftime("%Y-%m-%d")
        diag["calendar_start_date"] = start_date
        diag["calendar_end_date"] = end_date

        # First try: Get events via Team Calendars REST API
        try:
            calendar_api_url = (
                "https://arlo.atlassian.net/wiki/rest/calendar-services/1.0/calendar/events.json"
            )

            params = {
                "start": start_date,
                "end": end_date,
                "subCalendarId": sub_calendar_id,
                "userTimeZoneId": "America/Chicago",
            }

            logging.info(f"🔍 Team Calendar API: {calendar_api_url} params={params}")
            cal_resp = requests.get(calendar_api_url, auth=auth, params=params, timeout=25)
            diag["primary_http_status"] = cal_resp.status_code
            logging.info(f"📡 Calendar API status: {cal_resp.status_code}")

            if cal_resp.status_code == 200:
                raw = cal_resp.json()
                events = _normalize_team_calendar_events(raw)
                diag["raw_events_primary"] = len(events)
                logging.info(f"📅 Normalized {len(events)} event(s) from primary Calendar API")
                if events and isinstance(events[0], dict):
                    diag["sample_event_keys"] = list(events[0].keys())[:25]
                    logging.info(f"📝 Sample event keys: {diag['sample_event_keys']}")

                for event in events:
                    try:
                        row = _grm_event_to_deployment(event, cst)
                        if row:
                            deployments.append(row)
                            logging.info(
                                f"✓ Added: {row['service']} at "
                                f"{row['timestamp'][:16]} → {row.get('end_timestamp', '')[:16]}"
                            )
                    except Exception as e:
                        logging.error(f"Error parsing event: {e}")
                        continue
            else:
                logging.warning(
                    f"❌ Calendar API HTTP {cal_resp.status_code}: {(cal_resp.text or '')[:300]}"
                )

        except Exception as e:
            logging.warning(f"⚠️ Team Calendar API failed: {e}, trying alternate params...")

        # Second try: calendarId parameter (some installs expect this name)
        if not deployments:
            try:
                space_calendar_url = (
                    "https://arlo.atlassian.net/wiki/rest/calendar-services/1.0/calendar/events.json"
                )
                params_alt = {
                    "calendarId": sub_calendar_id,
                    "start": start_date,
                    "end": end_date,
                    "userTimeZoneId": "America/Chicago",
                }
                logging.info(f"🔍 Calendar API (alt): {params_alt}")
                alt_resp = requests.get(
                    space_calendar_url, auth=auth, params=params_alt, timeout=25
                )
                diag["alt_http_status"] = alt_resp.status_code
                logging.info(f"📡 Alt Calendar API status: {alt_resp.status_code}")

                if alt_resp.status_code == 200:
                    alt_raw = alt_resp.json()
                    alt_events = _normalize_team_calendar_events(alt_raw)
                    diag["raw_events_alt"] = len(alt_events)
                    logging.info(f"📅 Normalized {len(alt_events)} event(s) from alt API")

                    for event in alt_events:
                        try:
                            row = _grm_event_to_deployment(event, cst)
                            if row:
                                deployments.append(row)
                                logging.info(f"✓ Added from alt API: {row['service']}")
                        except Exception as e:
                            logging.error(f"Error parsing alt event: {e}")
                            continue
                else:
                    logging.warning(
                        f"Alt Calendar API HTTP {alt_resp.status_code}: {(alt_resp.text or '')[:300]}"
                    )
            except Exception as e:
                logging.warning(f"Alt Calendar API failed: {e}")

        # Deduplicate (primary + alt may return the same events)
        if deployments:
            seen = set()
            deduped = []
            for d in deployments:
                k = (d.get("service"), d.get("timestamp"))
                if k in seen:
                    continue
                seen.add(k)
                deduped.append(d)
            deployments = deduped

        deployment_source = "calendar_api" if deployments else "empty"
        # Demo rows only when explicitly enabled (never pass as real GRM data)
        if not deployments and use_mock_deployments:
            logging.warning(
                "⚠️ DEPLOYMENTS_USE_MOCK_DATA=1 — generating sample rows (not from GRM calendar)"
            )
            deployment_source = "mock"
            try:
                sample_services = [
                    "HMS Core Services",
                    "Nginx ClientAPI DeviceAPI",
                    "Backend-hmsdevicemanagement",
                    "Advisor Service",
                    "Directory Service",
                    "Backend-hmspubsub",
                    "OAuth Service",
                    "Web Client Release",
                ]
                deployment_times = [-2, -1, 2, 6, 12, 18, 22]
                for idx, hour_offset in enumerate(deployment_times):
                    if idx >= len(sample_services):
                        break
                    deploy_dt = today + timedelta(hours=hour_offset)
                    deploy_dt_cst = deploy_dt.astimezone(cst)
                    end_dt_cst = (today + timedelta(hours=hour_offset + 2)).astimezone(cst)
                    deployments.append(
                        {
                            "date": deploy_dt_cst.strftime("%b %d, %Y"),
                            "service": f"GRM: {sample_services[idx]}",
                            "timestamp": deploy_dt_cst.isoformat(),
                            "end_timestamp": end_dt_cst.isoformat(),
                        }
                    )
                logging.info(f"📂 Generated {len(deployments)} mock deployments")
            except Exception as e:
                logging.error(f"❌ Error generating mock deployments: {e}")
                deployment_source = "empty"
        
        # Filter: events that overlap [now-2h, now+24h]. is_past = window fully ended (not "start in past").
        past_window = today - timedelta(hours=2)
        next_window = today + timedelta(hours=24)
        filtered_deployments = []

        def _aware(dt):
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt

        for deploy in deployments:
            try:
                deploy_start = _aware(
                    datetime.fromisoformat(deploy["timestamp"].replace("Z", "+00:00"))
                )
                if deploy.get("end_timestamp"):
                    deploy_end = _aware(
                        datetime.fromisoformat(deploy["end_timestamp"].replace("Z", "+00:00"))
                    )
                else:
                    deploy_end = deploy_start + timedelta(hours=2)
                    deploy["end_timestamp"] = deploy_end.isoformat()
                # Overlap with filter window
                if deploy_end < past_window or deploy_start > next_window:
                    continue
                deploy["is_past"] = deploy_end < today
                filtered_deployments.append(deploy)
            except Exception as e:
                logging.error(f"Error filtering deployment: {e}")

        deployments = filtered_deployments
        deployments.sort(key=lambda x: x.get("timestamp", ""))
        upcoming = deployments[:20]

        logging.info(
            f"✅ Deployments ({deployment_source}): {len(upcoming)} row(s) in last 2h + next 24h window"
        )

        payload = {
            "deployments": upcoming,
            "total": len(deployments),
            "timestamp": time.strftime("%H:%M:%S"),
            "source": deployment_source,
            "diagnostics": {
                "sub_calendar_id": sub_calendar_id,
                "calendar_start_date": diag.get("calendar_start_date"),
                "calendar_end_date": diag.get("calendar_end_date"),
                "primary_http_status": diag.get("primary_http_status"),
                "alt_http_status": diag.get("alt_http_status"),
                "raw_events_primary": diag.get("raw_events_primary"),
                "raw_events_alt": diag.get("raw_events_alt"),
                "rows_after_filter": len(upcoming),
                "sample_event_keys": diag.get("sample_event_keys"),
            },
        }
        if deployment_source == "empty":
            ps = diag.get("primary_http_status")
            alt_s = diag.get("alt_http_status")
            raw_total = (diag.get("raw_events_primary") or 0) + (diag.get("raw_events_alt") or 0)
            ok_200 = ps == 200 or alt_s == 200
            if ps in (401, 403) or alt_s in (401, 403):
                payload["warning"] = (
                    "Confluence API returned 401/403: the email/API token pair is invalid or the token "
                    "lacks Confluence access. Create an API token at id.atlassian.com and ensure "
                    "ATLASSIAN_EMAIL matches the Atlassian account."
                )
            elif ps == 404 or alt_s == 404:
                payload["warning"] = (
                    f"Calendar endpoint returned 404. Check DEPLOYMENTS_SUBCALENDAR_ID (current: {sub_calendar_id}) "
                    "via Team Calendars subcalendars API in Confluence."
                )
            elif ok_200 and raw_total > 0:
                payload["warning"] = (
                    "Calendar returned events but none could be parsed into deployment rows "
                    "(expected fields: start, title). Check server logs for a sample event."
                )
            elif ok_200 and raw_total == 0:
                payload["warning"] = (
                    "API returned OK but 0 events in the selected date range. "
                    f"Verify DEPLOYMENTS_SUBCALENDAR_ID={sub_calendar_id} matches the GRM sub-calendar, "
                    "or that deployments exist in Confluence for these dates."
                )
            elif ps is not None or alt_s is not None:
                payload["warning"] = (
                    f"Team Calendar API did not return 200 (primary HTTP {ps}, alt HTTP {alt_s}). "
                    "See server logs."
                )
            else:
                payload["warning"] = (
                    "Could not reach Team Calendar API (exception before HTTP response). See server logs. "
                    "Optional: DEPLOYMENTS_USE_MOCK_DATA=1 for demo rows only."
                )
            payload["diagnostics"] = {**payload["diagnostics"], **diag}
        return jsonify(payload)
        
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
    # Default port 8080 (override with env PORT)
    port = int(os.getenv('PORT', 8080))
    
    # Enable threading for concurrent requests
    # This allows multiple users to use the tool simultaneously
    flask_app.run(
        host='0.0.0.0', 
        port=port,
        threaded=True,  # Enable multi-threading for concurrent requests
        debug=False     # Disable debug mode for better performance
    )
