
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import time
import sys
import logging
# Importar tools
from tools.gemini_tool import ask_gemini
from tools.llama_tool import ask_llama
from tools.wiki_tool import wiki_search
from tools.tickets_tool import read_tickets
from tools.history_tool import add_to_history, get_history
from tools.suggestions_tool import AI_suggestions

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
    "Read_Itrack": {"description": "Read workarounds from AT&T itrack", "function": read_tickets},
    "Read_Wiki": {"description": "Read documents from AT&T Wiki", "function": wiki_search},
    "How_to_fix": {"description": "Generate recommendations using LLaMA with JIRA, Grafana, and Wiki", "function": AI_suggestions},
    "MCP_Connect": {"description": "Check if MCP server is active", "function": lambda _: "✅ MCP server is active and functional"},
    "Ask_Gemini": {"description": "Ask LLM Gemini about anything", "function": ask_gemini}
}
registered_tools = [(name, tool["description"]) for name, tool in TOOLS.items()]

# ✅ Flask App
flask_app = Flask(__name__, template_folder='templates')
CORS(flask_app)

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
    selected_tools = data.get('tools', [])
    if not selected_tools:
        selected_tools = ['How_to_fix']
    results = []
    start = time.time()
    for tool_name in selected_tools:
        func = TOOLS.get(tool_name, {}).get('function')
        if not func:
            results.append(f"<pre>No tool found for {tool_name}</pre>")
            continue
        try:
            if tool_name == 'Ask_Gemini':
                res = func(input_text, selected_tools)
            else:
                res = func(input_text)
            if not res:
                res = "<pre>No response from tool</pre>"
            results.append(f"<div class='llama-response'><h3>{tool_name}</h3>{res}</div>")
        except Exception as e:
            results.append(f"<pre>Error executing tool '{tool_name}': {e}</pre>")
    exec_time = round(time.time() - start, 2)
    final_result = "<br>".join(results)
    add_to_history(input_text, final_result)
    return jsonify({'result': final_result, 'exec_time': exec_time})

if __name__ == '__main__':
    flask_app.run(host='0.0.0.0', port=5000)
