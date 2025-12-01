
from mcp.server.fastmcp import FastMCP
from flask import Flask, request, jsonify, send_from_directory
import requests
import subprocess
import logging
import os
import html
import json
import re
import time
from bs4 import BeautifulSoup
from flask_cors import CORS
from jira import JIRA
import datetime
import sys
import google.generativeai as genai

# 📋 Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("agent_tool_logs.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

# 🧠 Gemini
def ask_gemini(prompt: str, selected_tools: list) -> str:
    try:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return "Error: GEMINI_API_KEY is not defined in env file."
        if "Ask_Gemini" in selected_tools:
            prompt = f"Execute the following prompt, i need legible like html formated but preserving the current style.:\n{prompt}"
        model = "models/gemini-2.5-pro"
        url = f"https://generativelanguage.googleapis.com/v1beta/{model}:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            return f"Error {response.status_code}: {response.text}"
        data = response.json()
        output = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        return output if output else "Gemini is not working properly."
    except Exception as e:
        return f"Error executing Gemini: {e}"

# 🧠 LLaMA
def ask_llama(prompt: str) -> str:
    try:
        result = subprocess.run(
            ["ollama", "run", "llama3"],
            input=prompt.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True
        )
        output = result.stdout.decode("utf-8").strip()
        if ">" in output:
            output = output.split(">", 1)[-1].strip()
        return output
    except Exception as e:
        return f"Error running LLaMA: {e}"

# 📚 Wiki Search
def wiki_search(query: str) -> str:
    token = os.getenv("WIKI_TOKEN")
    if not token:
        return "<p>Error: WIKI_TOKEN not defined for environment variables.</p>"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    summary_context = None
    if query.startswith("CDEX-") and query[5:].isdigit():
        jira_session = requests.Session()
        jira_session.auth = (os.getenv("ITRACK_USER"), os.getenv("ITRACK_PASSWORD"))
        jira_url = f"https://itrack.web.att.com/rest/api/2/issue/{query}"
        jira_response = jira_session.get(jira_url)
        if jira_response.status_code != 200:
            return f"<p>Error fetching Jira ticket {query}: {jira_response.status_code} {jira_response.reason}</p>"
        jira_data = jira_response.json()
        summary_context = jira_data["fields"]["summary"]
        trimmed_query = summary_context[:50].strip()
    else:
        trimmed_query = query
    cql = f'(text ~ "{trimmed_query}" OR title ~ "{trimmed_query}") AND type = "page" AND title !~ ".jpg"'
    search_url = f"https://wiki.web.att.com/rest/api/content/search?cql={cql}"
    response = requests.get(search_url, headers=headers)
    if response.status_code != 200:
        return f"<p>Error {response.status_code}: {response.reason}</p>"
    data = response.json()
    results = data.get("results", [])
    if not results:
        return f"<p>No documents found related to: <strong>{html.escape(trimmed_query)}</strong></p>"
    output = "<h2>📚 Wiki Search Results</h2>"
    if summary_context:
        output += f"<p><strong>Search Context (Ticket Summary):</strong> {html.escape(summary_context)}</p>"
    output += "<table border='1'><tr><th>Title</th><th>Link</th></tr>"
    for item in results[:10]:
        title = item.get("title", "No title")
        page_id = item.get("id")
        url = f"https://wiki.web.att.com/pages/viewpage.action?pageId={page_id}"
        output += f"<tr><td>{html.escape(title)}</td><td>{url}Open</a></td></tr>"
    output += "</table>"
    return output

# ✅ read_tickets (completa)
def read_tickets(query: str) -> str:
    session = requests.Session()
    session.auth = (os.getenv("ITRACK_USER"), os.getenv("ITRACK_PASSWORD"))
    if "accepted_tickets" not in globals():
        globals()["accepted_tickets"] = {}
    # ✅ Caso directo: query es un ticket ID tipo CDEX-xxxxx
    if query.startswith("CDEX-") and query[5:].isdigit():
        ticket_url = f"https://itrack.web.att.com/rest/api/2/issue/{query}?expand=comments"
        response = session.get(ticket_url)
        if response.status_code != 200:
            return f"<p>Error {response.status_code}: {response.reason}</p>"
        issue = response.json()
        key = issue["key"]
        status = issue["fields"]["status"]["name"]
        summary = issue["fields"]["summary"]
        description = issue["fields"].get("description", "No description available")
        comments = issue["fields"].get("comment", {}).get("comments", [])
        last_two_comments = [c.get("body", "") for c in comments[-2:]] if comments else ["No comments available"]
        url = f"https://itrack.web.att.com/projects/CDEX/issues/{key}"
        if status.lower() == "accepted":
            globals()["accepted_tickets"][key] = {
                "status": status,
                "summary": summary,
                "description": description,
                "comments": last_two_comments,
                "url": url
            }
            return f"<p>Ticket {key} almacenado en Accepted (no mostrado en tabla).</p>"
        # ✅ Mostrar solo si empieza con CDEX
        if key.startswith("CDEX-"):
            return _render_table([{
                "key": key,
                "status": status,
                "summary": summary,
                "description": description,
                "last_comments": last_two_comments,
                "url": url
            }])
        else:
            return f"<p>Ticket {key} no mostrado (no es CDEX).</p>"
    # ✅ Caso normal: búsqueda por texto en summary/description
    jql = f'(description ~ "{query}")'
    search_url = f'https://itrack.web.att.com/rest/api/2/search?jql={jql}&maxResults=50'
    response = session.get(search_url)
    if response.status_code != 200:
        return f"<p>Error {response.status_code}: {response.reason}</p>"
    data = response.json()
    issues = data.get("issues", [])
    if not issues:
        return f"<p>No se encontró información para: '{html.escape(query)}'</p>"
    tabla = []
    for issue in issues:
        key = issue["key"]
        # 🔎 Segunda llamada para traer comentarios completos
        issue_url = f'https://itrack.web.att.com/rest/api/2/issue/{key}?expand=comments'
        issue_resp = session.get(issue_url)
        if issue_resp.status_code != 200:
            continue
        full_issue = issue_resp.json()
        status = full_issue["fields"]["status"]["name"]
        summary = full_issue["fields"]["summary"]
        description = full_issue["fields"].get("description", "No description available")
        comments = full_issue["fields"].get("comment", {}).get("comments", [])
        last_two_comments = [c.get("body", "") for c in comments[-2:]] if comments else ["No comments available"]
        url = f"https://itrack.web.att.com/projects/CDEX/issues/{key}"
        if status.lower() == "accepted" or status.lower() == "closed" or status.lower() == "test complete" or status.lower() == "dev complete" or status.lower() == "cancelled":
            globals()["accepted_tickets"][key] = {
                "status": status,
                "summary": summary,
                "description": description,
                "comments": last_two_comments,
                "url": url
            }
            continue
        # ✅ Mostrar solo si empieza con CDEX
        if key.startswith("CDEX-"):
            tabla.append({
                "key": key,
                "status": status,
                "summary": summary,
                "description": description,
                "last_comments": last_two_comments,
                "url": url
            })
    if not tabla:
        return f"<p>We didnt found any ticket open for: '{html.escape(query)}'</p>"
    return _render_table(tabla)
def _render_table(tabla):
    output = f"""
        <style>
        .ticket-table {{
            border-collapse: collapse;
            width: 100%;
            font-family: Arial, sans-serif;
            font-size: 14px;
        }}
        .ticket-table th, .ticket-table td {{
            border: 1px solid #ccc;
            padding: 8px;
            text-align: left;
            vertical-align: top;
        }}
        .ticket-table th {{
            background-color: #f2f2f2;
        }}
        .ticket-table tr:nth-child(even) {{
            background-color: #fafafa;
        }}
        .status-In\\ Progress {{ background-color: #fff3cd; }}
        .status-To\\ Do {{ background-color: #e0f7fa; }}
        .status-Open {{ background-color: #e0f7fa; }}
        </style>
        <p><strong>Tickets Found:</strong> {len(tabla)}</p>
        <table class="ticket-table">
            <tr>
                <th>TICKET</th>
                <th>STATUS</th>
                <th>SUMMARY</th>
                <th>DESCRIPTION</th>
                <th>LAST 2 COMMENTS</th>
                <th>LINK</th>
                <th>URL</th>
            </tr>
    """
    for row in tabla:
        status_value = row.get('status') or "Unknown"
        status_class = f"status-{status_value.replace(' ', '\\ ')}"
        output += f"<tr class='{status_class}'>"
        output += f"<td>{html.escape(row['key'])}</td>"
        output += f"<td>{html.escape(status_value)}</td>"
        output += f"<td>{html.escape(row['summary'])}</td>"
        output += f"<td>{html.escape(row['description'])}</td>"
        output += f"<td>{"<br>".join([html.escape(c) for c in row['last_comments']])}</td>"
        output += f"<td><a href='{row['url']}' target='_blank'>Open</a></td>"
        output += f"<td>{html.escape(row['url'])}</td>"
        output += "</tr>"
    output += "</table>"
    return output

# ✅ History
HISTORY_FILE = 'search_history.json'
def add_to_history(query, result):
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            history = json.load(f)
    except:
        history = []
    history.append({'query': query, 'result': result})
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history[-10:], f)

def get_history():
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return []

if not os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump([], f)

# ✅ AI Suggestions (completa)

def AI_suggestions(query: str) -> str:
    html_tickets = read_tickets(query)
    soup = BeautifulSoup(html_tickets, "html.parser")
    rows = soup.select("table.ticket-table tr")[1:]
    resumen_tickets = []
    for row in rows:
        cols = row.find_all("td")
        if len(cols) >= 5:
            ticket_id = cols[0].text.strip()
            status = cols[1].text.strip()
            summary = cols[2].text.strip()
            description = cols[3].text.strip()
            last_comment = cols[4].text.strip()
            # ✅ only tickets In Progress, To Do, Retest
            if status.lower() in ["in progress", "to do", "retest"]:
                resumen = f"{ticket_id}\n{status}\n{summary}\n{last_comment}"
                resumen_tickets.append(resumen)
            # ✅ for Accepted, include last 2 comments
            elif status.lower() == "accepted":
                comments = row.find_all("td")[4].text.strip().split("\n")
                last_two = comments[-2:] if len(comments) >= 2 else comments
                resumen = f"{ticket_id}\n{status}\n{summary}\nLast 2 Comments: {'\n'.join(last_two)}"
                resumen_tickets.append(resumen)
    texto_tickets = "\n".join(resumen_tickets)
    prompt = f"""
    You are a senior DevOps service engineer specializing in troubleshooting and ticket analysis.
    Your task is to generate structured recommendations to resolve each issue based on the provided tickets.
    ⚠️ Rules:
    - Only include tickets with status "In Progress", "To Do", or "Retest" for recommendations.
    - For grouped tickets include tickets in "Accepted" status , and list the last 2 comments available.
    - Always use HTML tags exactly as shown in the format below.
    - Do not use Markdown.
    - Do not collapse fields into single lines.
    - Do not add commentary, explanations, or text outside the required structure.
    - Include only the valid suggestions but always inside <ul><li>.
    - Group similar tickets together by technical context (e.g., SSL errors, ELK cleanup).
    Each ticket must follow this exact block:
    <div style="border:1px solid #ccc; padding:1rem; margin-bottom:1rem; border-radius:8px; background-color:#fdfdfd;">
    <h3>🎫 <strong>Ticket ID:</strong> CDEX-xxxxx</h3>
    <p><strong>Status:</strong> In Progress</p>
    <p><strong>Summary:</strong> Short description of the issue</p>
    <p><strong>Last comments:</strong> Last Comments</p>
    <p><strong>Ticket URL:</strong> <a href="https://itrtt.com/projects/CDEX/issues/CDEX-xxxxxOpen</a></p>
    <p><strong>Recommendations:</strong></p>
    <ul>
    <li>First recommendation</li>
    <li>Second recommendation</li>
    <li>Third recommendation</li>
    </ul>
    </div>
    Before listing individual tickets, include a section for grouped tickets:
    <h2>🔗 Similar Tickets (Grouped)</h2>
    <p><strong>Ticket Group:</strong> Short description</p>
    <ul>
    <li>CDEX-xxxxx: Summary</li>
    <p><strong>Last 2 Comments:</strong></p>
    <ul>
    <li>Comment 1</li>
    <li>Comment 2</li>
    </ul>
    </ul>
    <p><strong>Recommendations:</strong></p>
    <ul>
    <li>Group recommendation 1</li>
    <li>Group recommendation 2</li>
    <li>Group recommendation 3</li>
    </ul>
    At the end, include a section for external documentation links suggested by GenAI related to the applications or technologies mentioned in the tickets.
    Only use official documentation sources (Microsoft, Elastic, Kubernetes, etc.).
    Query context: {query}
    Tickets:
    {texto_tickets}
    """
    raw_response = ask_gemini(prompt, ["How_to_fix"])
    wiki_html = wiki_search(query[:20])
    raw_response += f"""
    <p><strong>Related Wiki Pages:</strong></p>
    {wiki_html}
    </div>
    """

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
