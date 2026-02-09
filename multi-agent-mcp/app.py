
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import time
import sys
import logging

# Importar tools existentes
from tools.gemini_tool import ask_gemini
from tools.llama_tool import ask_llama
from tools.confluence_tool import confluence_search
#from tools.tickets_tool import read_tickets
from tools.history_tool import add_to_history, get_history
from tools.suggestions_tool import AI_suggestions
from tools.ask_arlochat import ask_arlo
from tools.service_owners import service_owners_search
from tools.noc_kt import noc_kt_search
from tools.read_arlo_status import read_arlo_status
from tools.oncall_support import confluence_oncall_today
from tools.read_versions import read_versions
from tools.datadog_dashboards import read_datadog_dashboards, read_datadog_errors_only, read_datadog_adt, read_datadog_adt_errors_only, read_datadog_all_errors
from tools.splunk_tool import read_splunk_p0_dashboard

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
    "P0_Streaming": {"description": "Show P0 Streaming dashboard from Splunk", "function": read_splunk_p0_dashboard},
    "Holiday_Oncall": {"description": "Verify status in ARLO webpage", "function": confluence_oncall_today},
    "Suggestions": {"description": "Generate recommendations using LLaMA with JIRA, Grafana, and Wiki", "function": AI_suggestions},
    "Ask_ARLOCHAT": {"description": "Ask ARLO CHAT about anything", "function": ask_arlo}
    
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
            <h4>Causa raíz sugerida:</h4>{suggestion}
            <br><h4>Confluence:</h4>{confluence_info}
        </div>
        """
    except Exception as e:
        return f"<pre>Error identificando causa: {e}</pre>"



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
    start = time.time()
    for tool_name in selected_tools:
        func = TOOLS.get(tool_name, {}).get('function')
        print(func)
        if not func:
            results.append(f"<pre>No tool found for {tool_name}</pre>")
            continue
        try:
            # Pass timerange to Datadog and Splunk tools
            if tool_name in ['DD_Red_Metrics', 'DD_Errors', 'DD_Red_ADT', 'P0_Streaming']:
                res = func(input_text, timerange)
            else:
                res = func(input_text)
            results.append(f"<div class='llama-response'><h3>{tool_name}</h3>{res}</div>")
        except Exception as e:
            results.append(f"<pre>Error ejecutando '{tool_name}': {e}</pre>")
    exec_time = round(time.time() - start, 2)
    final_result = "<br>".join(results)
    
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

if __name__ == '__main__':
    flask_app.run(host='0.0.0.0', port=5001)
