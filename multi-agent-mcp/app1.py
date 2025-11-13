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
from email.message import EmailMessage
from flask import Flask, request, render_template
from flask_cors import CORS
from jira import JIRA
import datetime

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
        logging.FileHandler("agent_tool_logs.log"),
        logging.StreamHandler()
    ]
)

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

    # 🔍 Usar solo las primeras 20 letras del query
    trimmed_query = query[:20].strip()

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
                score += max(0, 30 - days_ago) // 10  # más puntos si es reciente
            except:
                pass

        return score

    # 🔽 Ordenar por score descendente y limitar a 5
    scored_results = sorted(results, key=relevance_score, reverse=True)[:5]

    if not scored_results:
        return f"<p>No relevant troubleshooting documents found for: <strong>{html.escape(trimmed_query)}</strong></p>"

    # 🧾 Construir tabla HTML
    output = """
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


# ITRACK TICKETS
def read_tickets(query: str) -> str:
    session = requests.Session()
    session.auth = (os.getenv("ITRACK_USER"), os.getenv("ITRACK_PASSWORD"))

    ticket_url = 'https://itrack.web.att.com/projects/CDEX/issues/?filter=allopenissues'
    response = session.get(ticket_url)

    if response.status_code != 200:
        return f"<p>Error {response.status_code}: {response.reason}</p>"

    raw_html = response.text
    pattern = re.compile(r'WRM\._unparsedData\["[^"]*"\]\s*=\s*"(.+?)";', re.DOTALL)
    matches_raw = pattern.findall(raw_html)

    if not matches_raw:
        return "<p>We didnt found WRM._unparsedData block</p>"

    tabla = []
    procesados = set()

    for raw_json_str in matches_raw:
        try:
            decoded_json_str = raw_json_str.encode().decode('unicode_escape')
            ticket_pattern = re.compile(
                r'"key":"(CDEX-\d+)",\s*"status":"([^"]+)",\s*"summary":"([^"]*?)"',
                re.IGNORECASE
            )
            resultados = ticket_pattern.findall(decoded_json_str)

            for key, status, summary in resultados:
                if query.lower() in summary.lower():
                    if key in procesados:
                        continue
                    procesados.add(key)

                    url = f"url: https://itrack.web.att.com/projects/CDEX/issues/{key}"
                    tabla.append({
                        "key": key,
                        "status": status,
                        "summary": summary,
                        "url": url
                    })

        except Exception as e:
            return f"<p>Decode Error: {str(e)}</p>"

    if not tabla:
        return f"<p>We didnt found information for: '{query}'</p>"
    # 🔽 Ordenar por STATUS
    tabla.sort(key=lambda x: x['status'])
    
    # HTML con estilos y colores por estado
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
        .status-Resolved {{ background-color: #d4edda; }}
        .status-Closed {{ background-color: #f8d7da; }}
        .status-Open {{ background-color: #e0f7fa; }}
        </style>
        <p><strong>Tickets Found:</strong> {len(tabla)}</p>
        <table class="ticket-table">
            <tr>
                <th>TICKET</th>
                <th>STATUS</th>
                <th>SUMMARY</th>
                <th>LINK</th>
                <th>URL</th>
            </tr>
    """

    for row in tabla:
        status_class = f"status-{row['status'].replace(' ', '\\ ')}"
        output += f"<tr class='{status_class}'>"
        output += f"<td>{html.escape(row['key'])}</td>"
        output += f"<td>{html.escape(row['status'])}</td>"
        output += f"<td>{html.escape(row['summary'])}</td>"
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
        if len(cols) >= 4:
            resumen = f"{cols[0].text.strip()} | {cols[1].text.strip()} | {cols[2].text.strip()} | {cols[3].text.strip()}"
            resumen_tickets.append(resumen)

    texto_tickets = "\n".join(resumen_tickets)

    prompt = f"""
    You are a senior DevOps service engineer specializing in troubleshooting and ticket analysis. Based on the following list of open tickets and the query context provided, generate a structured set of recommendations to resolve each issue.

    Your output must follow this exact format, using HTML tags for styling. Each ticket must be wrapped in the following block:

    <div style="border:1px solid #ccc; padding:1rem; margin-bottom:1rem; border-radius:8px; background-color:#fdfdfd;">
        <h3>🎫 <strong>Ticket ID:</strong> CDEX-xxxxx</h3>
        <p><strong>Status:</strong> Open</p>
        <p><strong>Summary:</strong> Short description of the issue</p>
        <p><strong>Ticket URL:</strong> <a href="https://itrack.web.att.com/projects/CDEX/issues/CDEX-xxxxx" target="_blank">Open</a></p>
        <p><strong>Recommendations:</strong></p>
        <ul>
            <li>First recommendation</li>
            <li>Second recommendation</li>
            <li>Third recommendation</li>
        </ul>
    </div>

    Repeat this block for each ticket.

    Before the individual tickets, include a section for similar tickets grouped together:

    <h2>🔗 Similar Tickets (Grouped)</h2>
    <p><strong>Ticket Group:</strong> Short description</p>
    <ul>
        <li>CDEX-xxxxx: Summary</li>
        <li>CDEX-xxxxx: Summary</li>
        <li>CDEX-xxxxx: Summary</li>
    </ul>
    <p><strong>Recommendations:</strong></p>
    <ul>
        <li>Group recommendation 1</li>
        <li>Group recommendation 2</li>
        <li>Group recommendation 3</li>
    </ul>

    At the end, include a section for external documentation links:

    <h2>📚 External Documentation Links</h2>
    <ul>
        <li>Azure Queues: <a href="https://docs.microsoft.com/en-us/azure/queues" target="_blank">https://docs.microsoft.com/en-us/azure/queues</a></li>
        <li>API Management: <a href="https://docs.microsoft.com/en-us/azure/api-management" target="_blank">https://docs.microsoft.com/en-us/azure/api-management</a></li>
    </ul>

    Do not use Markdown. Do not collapse fields into single lines. Use only HTML tags as shown. Do not add extra commentary or change the structure.

    Query context: {query}

    Tickets:
    {texto_tickets}
    """

    raw_response = ask_llama(prompt)
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
CORS(flask_app)  # ✅ Enables CORS for all routes and origins

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>MCP Tools</title>
    <style>
        body {
            font-family: "Segoe UI", Roboto, sans-serif;
            background-color: #f9f9fb;
            color: #333;
            margin: 0;
            padding: 0;
        }
        header {
            background: linear-gradient(90deg, #6f2da8, #ff6f61); /* Amdocs-inspired gradient */
            color: white;
            padding: 1rem 2rem;
            text-align: center;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        main {
            max-width: 800px;
            margin: 2rem auto;
            background-color: white;
            padding: 2rem;
            border-radius: 12px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.05);
        }
        h1 {
            margin-top: 0;
            font-size: 1.8rem;
        }
        label {
            font-weight: 600;
            display: block;
            margin-top: 1rem;
        }
        textarea {
            width: 100%;
            padding: 0.75rem;
            margin-top: 0.5rem;
            border: 1px solid #ccc;
            border-radius: 8px;
            font-size: 1rem;
        }
        button {
            padding: 0.75rem 1rem;
            background: linear-gradient(90deg, #6f2da8, #ff6f61); /* Matches header */
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 1rem;
            cursor: pointer;
            transition: background 0.3s ease;
        }
        button:hover {
            background: linear-gradient(90deg, #5a1f8e, #e85c50); /* Darker hover */
        }
        .tool-buttons {
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem;
            margin-top: 0.5rem;
        }
        pre {
            background-color: #f4f4f6;
            padding: 1rem;
            border-radius: 8px;
            white-space: pre-wrap;
            word-wrap: break-word;
            margin-top: 1rem;
        }
        footer {
            text-align: center;
            font-size: 0.9rem;
            color: #777;
            margin-top: 2rem;
        }
    </style>
</head>
<body>
    <header>
        <h1>🧠 GenDox AI 🧠</h1>
    </header>
    <main>
        <form method="post">
            <label>Select a tool to use:</label>
            <div class="tool-buttons">
                {% for name, desc in tools %}
                    <button type="submit" name="tool" value="{{ name }}" title="{{ desc }}">{{ name }}</button>
                {% endfor %}
            </div>

            <label for="input">Enter your input:</label>
            <textarea name="input" rows="5" placeholder="Can be Application name or exact filter Example: DIGITAL or OMV or BSSE, etc"></textarea>
        </form>

        {% if result %}
            <h2>Response:</h2>
            <div>{{ result|safe }}</div>
        {% endif %}
    </main>
    <footer>
        MCP Server running ........... Claude Style✨
    </footer>
</body>
</html>"""

@flask_app.route("/", methods=["GET", "POST"])
def index():
    result = None
    if request.method == "POST":
        tool_name = request.form["tool"]
        input_text = request.form["input"]
        func = TOOLS[tool_name]["function"]
        try:
            result = func(input_text if input_text else "")
        except Exception as e:
            result = f"Error executing tool '{tool_name}': {e}"
    return render_template_string(HTML_TEMPLATE, tools=registered_tools, result=result)

# 💬 Teams Endpoint https://teams.microsoft.com/l/chat/19:0e649888e1324801a86352030fdc9b41@thread.v2/conversations?context=%7B%22contextType%22%3A%22chat%22%7D
@flask_app.route("/teams", methods=["POST"])
def teams_webhook():
    message = request.json.get("text", "")
    if "status" in message.lower():
        summary = llama_suggestions("")
        return jsonify({"text": summary})
    return jsonify({"text": "Unrecognized command"})

# 🚀 Run Servers
if __name__ == "__main__":
    import threading

    def run_mcp():
        print("🧠 MCP Server running in background")
        mcp.run(transport="sse")

    def run_flask():
        print("🌐 Flask interface available at http://127.0.0.1:5000")
        flask_app.run(port=5000)

    threading.Thread(target=run_mcp).start()
    run_flask()