from mcp.server.fastmcp import FastMCP
from flask import Flask, request, render_template_string, jsonify
import requests
import subprocess
import logging
import os
import html
import re
import time
from bs4 import BeautifulSoup
from flask import Flask, request, render_template
from flask_cors import CORS
from jira import JIRA
import datetime
import sys
import google.generativeai as genai


# 🔐 Configuración
from config import (
    OPENWEATHER_API_KEY,
    SERPAPI_KEY,
    FIRECRAWL_KEY,
    SOURCEGRAPH_TOKEN
)

# 📋 Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("agent_tool_logs.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)  # ✅ Usa stdout que respeta UTF-8 en la mayoría de entornos
    ]
)

# 🧠 Gemini (Google AI Studio)
def ask_gemini(prompt: str) -> str:
    #client = genai.Client()
    try:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return "Error: GEMINI_API_KEY no está definido en las variables de entorno."

        # Usa un modelo válido, por ejemplo gemini-1.5-flash-latest
        model="models/gemini-2.5-pro"
        url = f"https://generativelanguage.googleapis.com/v1beta/{model}:generateContent?key={api_key}"

        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [
                {"parts": [{"text": prompt}]}
            ]
        }

        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            return f"Error {response.status_code}: {response.text}"

        data = response.json()
        output = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        return output.strip() if output else "No se recibió respuesta de Gemini."

    except Exception as e:
        return f"Error ejecutando Gemini: {e}"


# 🧠 LLaMA 3
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
    
# 📚 AT&T Wiki
def wiki_search(query: str) -> str:
    token = os.getenv("WIKI_TOKEN")
    if not token:
        return "<p>Error: WIKI_TOKEN not defined for environment variables.</p>"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    summary_context = None
    # ✅ Si query es un ticket ID tipo CDEX-xxxxx, obtener el summary desde Jira
    if query.startswith("CDEX-") and query[5:].isdigit():
        jira_session = requests.Session()
        jira_session.auth = (os.getenv("ITRACK_USER"), os.getenv("ITRACK_PASSWORD"))
        jira_url = f"https://itrack.web.att.com/rest/api/2/issue/{query}"
        jira_response = jira_session.get(jira_url)

        if jira_response.status_code != 200:
            return f"<p>Error fetching Jira ticket {query}: {jira_response.status_code} {jira_response.reason}</p>"

        jira_data = jira_response.json()
        summary_context = jira_data["fields"]["summary"]
        trimmed_query = summary_context[:50].strip()  # usar summary como query, recortado
    else:
        # 🔍 Usar solo las primeras 20 letras del query normal
        trimmed_query = query

    # 🧠 CQL con filtros: buscar en texto y título, excluir imágenes
    cql = (
        f'(text ~ "{trimmed_query}" OR title ~ "{trimmed_query}") '
        f'AND type = "page" '
        f'AND title !~ ".jpg"'
    )

    search_url = f"https://wiki.web.att.com/rest/api/content/search?cql={cql}"

    try:
        response = requests.get(search_url, headers=headers)
    except Exception as e:
        return f"<p>Error connecting to Wiki API: {html.escape(str(e))}</p>"

    if response.status_code != 200:
        return f"<p>Error {response.status_code}: {response.reason}</p>"

    data = response.json()
    results = data.get("results", [])

    if not results:
        return f"<p>No documents found related to: <strong>{html.escape(trimmed_query)}</strong></p>"

    # 🔍 Palabras clave para priorizar relevancia
    keywords = ["troubleshooting", "debug", "issue", "error", "fix", "failure", "incident", "how-to"]

    def relevance_score(item):
        title = item.get("title", "").lower()
        labels = item.get("metadata", {}).get("labels", [])
        score = sum(1 for kw in keywords if kw in title)
        score += sum(1 for kw in keywords if kw in labels)
        score += 2 if trimmed_query.lower() in title else 0

        # 📅 Priorizar por fecha si disponible
        last_modified = item.get("version", {}).get("when")
        if last_modified:
            try:
                dt = datetime.datetime.strptime(last_modified[:10], "%Y-%m-%d")
                days_ago = (datetime.datetime.now() - dt).days
                score += max(0, 30 - days_ago) // 10
            except:
                pass

        return score

    # 🔽 Ordenar por score descendente y limitar a 10
    scored_results = sorted(results, key=relevance_score, reverse=True)[:10]

    if not scored_results:
        return f"<p>No relevant troubleshooting documents found for: <strong>{html.escape(trimmed_query)}</strong></p>"

    # 🧾 Construir tabla HTML con encabezado de contexto
    output = "<h2>📚 Wiki Search Results</h2>"
    if summary_context:
        output += f"<p><strong>Search Context (Ticket Summary):</strong> {html.escape(summary_context)}</p>"

    output += """
    <table border="1" cellpadding="5" cellspacing="0">
        <tr>
            <th>Title</th>
            <th>Link</th>
        </tr>
    """
    for item in scored_results:
        title = item.get("title", "No title")
        page_id = item.get("id")
        url = f"https://wiki.web.att.com/pages/viewpage.action?pageId={page_id}"
        output += f"""
        <tr>
            <td>{html.escape(title)}</td>
            <td><a href="{url}" target="_blank">Open</a></td>
        </tr>
        """
    output += "</table>"

    return output

#read itrack
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


# 🧠 AI Suggestions
def llama_suggestions(query: str) -> str:
    html_tickets = read_tickets(query)

    soup = BeautifulSoup(html_tickets, "html.parser")
    rows = soup.select("table.ticket-table tr")[1:]  # omitir encabezado
    resumen_tickets = []

    for row in rows:
        cols = row.find_all("td")
        if len(cols) >= 5:  # aseguramos que haya comentarios
            ticket_id = cols[0].text.strip()
            status = cols[1].text.strip()
            summary = cols[2].text.strip()
            description = cols[3].text.strip()
            last_comment = cols[4].text.strip()

            # ✅ Solo incluir tickets en In Progress, To Do, Retest
            if status.lower() in ["in progress", "to do", "retest"]:
                resumen = f"{ticket_id} | {status} | {summary} | {last_comment}"
                resumen_tickets.append(resumen)

            # ✅ Para Accepted, incluir últimos 2 comentarios
            elif status.lower() == "accepted":
                comments = row.find_all("td")[4].text.strip().split("\n")
                last_two = comments[-2:] if len(comments) >= 2 else comments
                resumen = f"{ticket_id} | {status} | {summary} | Last 2 Comments: {' | '.join(last_two)}"
                resumen_tickets.append(resumen)

    texto_tickets = "\n".join(resumen_tickets)

    # Prompt ajustado
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
    - Include only the valid siggestions but always inside <ul><li>.
    - Group similar tickets together by technical context (e.g., SSL errors, ELK cleanup).

    Each ticket must follow this exact block:

    <div style="border:1px solid #ccc; padding:1rem; margin-bottom:1rem; border-radius:8px; background-color:#fdfdfd;">
        <h3>🎫 <strong>Ticket ID:</strong> CDEX-xxxxx</h3>
        <p><strong>Status:</strong> In Progress</p>
        <p><strong>Summary:</strong> Short description of the issue</p>
        <p><strong>Last comments:</strong> Last Comments</p>
        <p><strong>Ticket URL:</strong> <a href="https://itrack.web.att.com/projects/CDEX/issues/CDEX-xxxxx" target="_blank">Open</a></p>
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
            <li>CDEX-xxxxx: Summary</li>
            <p><strong>Last 2 Comments:</strong></p>
        <ul>
            <li>Comment 1</li>
            <li>Comment 2</li>
        </ul>
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
    print(prompt)
    
    #raw_response = ask_llama(prompt)
    raw_response = ask_gemini(prompt)
    wiki_html = wiki_search(query[:20])

    raw_response += f"""
            <p><strong>Related Wiki Pages:</strong></p>
            {wiki_html}
        </div>
        """
    print(raw_response)
    return raw_response

# ✅ MCP Server
mcp = FastMCP("MCP Server with tools for agents")
registered_tools = []

# 🧰 Available Tools
TOOLS = {
    "Read_Itrack": {
        "description": "Read workarounds from AT&T itrack",
        "function": read_tickets
    },
    "Read_Wiki": {
        "description": "Read documents from AT&T Wiki",
        "function": wiki_search
    },
    "How_to_fix": {
        "description": "Generate recommendations using LLaMA with JIRA, Grafana, and Wiki",
        "function": llama_suggestions
    },
    "MCP_Connect": {
        "description": "Check if MCP server is active",
        "function": lambda _: "✅ MCP server is active and functional"
    }
}

# 🔧 Tool Registration
def register_tool(name, tool_def):
    @mcp.tool(name=name, description=tool_def["description"])
    def tool_wrapper(input: str):
        start_time = time.perf_counter()
        logging.info(f"Tool executed: {name}")
        logging.info(f"Input received: {input}")
        try:
            result = tool_def["function"](input)
            duration = time.perf_counter() - start_time
            logging.info(f"Result: {result[:500]}")
            logging.info(f"Full result from '{name}':\n{result}")
            logging.info(f"Execution time: {duration:.2f} seconds")
            return result
        except Exception as e:
            logging.error(f"Error executing '{name}': {e}")
            return f"Error executing tool '{name}': {e}"

    registered_tools.append((name, tool_def["description"]))

for name, tool_def in TOOLS.items():
    register_tool(name, tool_def)

# 🌐 Flask Interface
flask_app = Flask(__name__)
CORS(flask_app)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>GenDox AI</title>
    <style>
        .llama-response {
            background-color: #fdfdfd;
            color: #222; /* texto oscuro para fondo claro */
            padding: 1rem;
            border-radius: 8px;
            margin-bottom: 1rem;
        }
        .llama-response h3 {
            color: #6f2da8; /* encabezado llamativo */
        }
        .llama-response p {
            color: #333;
        }
        .llama-response ul {
            margin-left: 1rem;
        }
        .llama-response li {
            margin-bottom: 0.5rem;
            color: #333;
        }
        .llama-response a {
            color: #0077cc;
        }
        body { margin:0; font-family:"Segoe UI", Roboto, sans-serif; background:#1e1e1e; color:#f0f0f0; display:flex; height:100vh; }
        aside.sidebar { width:240px; background:#2c2c2c; padding:1rem; display:flex; flex-direction:column; justify-content:space-between; }
        .sidebar .new-chat { background:#6f2da8; color:white; border:none; padding:0.75rem; border-radius:8px; cursor:pointer; }
        .sidebar .section { margin-top:1.5rem; font-weight:bold; color:#aaa; }
        .sidebar ul { list-style:none; padding:0; margin:0.5rem 0; }
        .sidebar ul li { padding:0.5rem; color:#ddd; }
        .sidebar ul li label { cursor:pointer; }
        .sidebar button { background:#444; color:#f0f0f0; border:none; padding:0.5rem 1rem; border-radius:6px; cursor:pointer; margin-top:0.5rem; width:100%; text-align:left; }
        .sidebar button:hover { background:#555; }
        main.chat-area { flex:1; display:flex; flex-direction:column; padding:1rem 2rem; overflow-y:auto; }
        header.top-bar { background:linear-gradient(90deg,#6f2da8,#ff6f61); padding:1rem; text-align:center; font-size:1.5rem; font-weight:bold; color:white; border-radius:8px; margin-bottom:1rem; }
        .prompt { font-size:1rem; margin-bottom:1rem; color:#bbb; }
        #final-counter { font-weight:bold; margin-bottom:1rem; color:#ccc; }
        #results-box { flex:1; overflow-x:auto; background:#2a2a2a; padding:1rem; border-radius:8px; margin-bottom:1rem; }
        form.input-form { display:flex; flex-direction:column; gap:0.5rem; background:#2c2c2c; padding:1rem; border-top:1px solid #444; }
        .tool-list { display:flex; flex-wrap:wrap; gap:0.5rem; }
        .tool-item { background:#1e1e1e; padding:0.5rem; border-radius:6px; }
        form.input-form textarea { background:#1e1e1e; color:#f0f0f0; border:1px solid #444; border-radius:8px; padding:0.75rem; resize:none; }
        form.input-form button { background:#6f2da8; color:white; border:none; padding:0.75rem 1rem; border-radius:8px; cursor:pointer; }
        .spinner { border:4px solid #f3f3f3; border-top:4px solid #6f2da8; border-radius:50%; width:24px; height:24px; animation:spin 1s linear infinite; display:inline-block; vertical-align:middle; }
        @keyframes spin { 0%{transform:rotate(0deg);} 100%{transform:rotate(360deg);} }
        .counter { margin-left:10px; font-weight:bold; color:#6f2da8; vertical-align:middle; }
    </style>
</head>
<body>
    <aside class="sidebar">
        <button class="new-chat" onclick="newChat()">➕ New Chat</button>
        <div>
            <div class="section"><button onclick="alert('Settings clicked')">⚙️ Settings</button></div>
            <div class="section"><button onclick="alert('About clicked')">ℹ️ About</button></div>
            <div class="section"><button onclick="alert('Help clicked')">❓ Help</button></div>
        </div>
    </aside>

    <main class="chat-area">
        <header class="top-bar">🧠 GenDox AI 🧠</header>
        <div class="prompt">Please start typing some prompt...</div>
        <div id="final-counter">{% if exec_time %}⏱ Execution time: {{ exec_time }}s{% endif %}</div>
        <div id="results-box">{{ result|safe }}</div>

        <form method="post" onsubmit="return showLoading()" class="input-form">
            <div class="tool-list">
                {% for name, desc in tools %}
                    <label class="tool-item" title="{{ desc }}">
                        <input type="checkbox" name="tool" value="{{ name }}">
                        {{ name }}
                    </label>
                {% endfor %}
            </div>

            <textarea name="input" placeholder="Please insert your search, select the tool in check box and press Send?">{{ input_text }}</textarea>
            <button type="submit">Send</button>
        </form>
        <p id="loading-message"></p>
    </main>

    <script>
        let counterInterval;
        let startTime;

        function showLoading() {
            if (!Array.from(document.querySelectorAll("input[name='tool']")).some(el => el.checked)) {
                alert("Selecciona al menos una herramienta antes de enviar.");
                return false;
            }

            document.getElementById("loading-message").innerHTML =
                '<span class="spinner"></span><span class="counter" id="counter">0s</span>';

            document.getElementById("results-box").innerHTML = "";
            document.getElementById("final-counter").innerText = "";

            startTime = Date.now();
            if (counterInterval) clearInterval(counterInterval);
            counterInterval = setInterval(() => {
                const elapsed = Math.floor((Date.now() - startTime) / 1000);
                document.getElementById("counter").innerText = elapsed + "s";
            }, 1000);

            return true;
        }

        function newChat() {
            document.querySelector("textarea[name='input']").value = "";
            document.getElementById("results-box").innerHTML = "";
            document.getElementById("final-counter").innerText = "";
            document.getElementById("loading-message").innerText = "";
            document.querySelectorAll("input[type='checkbox'][name='tool']").forEach(cb => cb.checked = false);
            if (counterInterval) clearInterval(counterInterval);
        }
    </script>

    {% if result %}
    <script>
        if (counterInterval) clearInterval(counterInterval);
        document.getElementById("loading-message").innerHTML = "";
    </script>
    {% endif %}
</body>
</html>
"""

@flask_app.route("/", methods=["GET","POST"])
def index():
    result = None
    input_text = ""
    exec_time = None

    if request.method == "POST":
        selected_tools = request.form.getlist("tool")  # ✅ obtiene lista de herramientas seleccionadas
        input_text = request.form.get("input", "")

        # fallback si no se selecciona ninguna herramienta
        if not selected_tools:
            selected_tools = ["How_to_fix"]

        results = []
        start = time.time()
        for tool_name in selected_tools:
            func = TOOLS.get(tool_name, {}).get("function")
            if not func:
                results.append(f"<pre>No tool found for {tool_name}</pre>")
                continue
            try:
                res = func(input_text)
                results.append(f"<div class='llama-response'><h3>{tool_name}</h3>{res}</div>")
            except Exception as e:
                results.append(f"<pre>Error executing tool '{tool_name}': {e}</pre>")
        exec_time = round(time.time() - start, 2)
        result = "<br>".join(results)

    return render_template_string(
        HTML_TEMPLATE,
        tools=registered_tools,
        result=result,
        input_text=input_text,
        exec_time=exec_time
    )

# 🚀 Run Servers
if __name__ == "__main__":
    import threading
    def run_mcp():
        print("🧠 MCP Server running in background")
        mcp.run(transport="sse")
    def run_flask():
        print("🌐 Flask interface available at http://127.0.0.1:5000")
        flask_app.run(host="0.0.0.0", port=5000)
    threading.Thread(target=run_mcp).start()
    run_flask()
