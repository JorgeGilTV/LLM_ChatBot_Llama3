from mcp.server.fastmcp import FastMCP
from flask import Flask, request, render_template_string
from flask_cors import CORS

import requests
import subprocess
import logging
import time
from config import (
    OPENWEATHER_API_KEY,
    SERPAPI_KEY,
    FIRECRAWL_KEY,
    SOURCEGRAPH_TOKEN
)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("agent_tool_logs.log"),
        logging.StreamHandler()
    ]
)

# Herramientas
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

def search_web(query: str) -> str:
    try:
        url = "https://serpapi.com/search"
        params = {"q": query, "api_key": SERPAPI_KEY}
        response = requests.get(url, params=params)
        results = response.json()
        if "organic_results" in results:
            return results["organic_results"][0].get("snippet", "No snippet available.")
        return "No results found."
    except Exception as e:
        return f"Search error: {e}"

def crawl_url(url: str) -> str:
    try:
        headers = {"Authorization": f"Bearer {FIRECRAWL_KEY}"}
        response = requests.post("https://api.firecrawl.dev/scrape", json={"url": url}, headers=headers)
        data = response.json()
        return data.get("text", "Could not extract content.")
    except Exception as e:
        return f"Scraping error: {e}"

def search_code(query: str) -> str:
    try:
        headers = {"Authorization": f"token {SOURCEGRAPH_TOKEN}"}
        url = "https://sourcegraph.com/.api/graphql"
        payload = {
            "query": f"""
            {{
                search(query: "{query}") {{
                    results {{
                        results {{
                            ... on FileMatch {{
                                file {{ path }}
                                repository {{ name }}
                            }}
                        }}
                    }}
                }}
            }}
            """
        }
        response = requests.post(url, json=payload, headers=headers)
        return str(response.json())
    except Exception as e:
        return f"Code search error: {e}"

def ping(_: str) -> str:
    return "✅ MCP Server is active & functional"

# MCP Server
mcp = FastMCP("MCP Server and agent tools")
registered_tools = []

TOOLS = {
    "search": {"description": "Search the web using SerpAPI", "function": search_web},
    "scrape": {"description": "Extract content from a URL using Firecrawl", "function": crawl_url},
    "code": {"description": "Search public code repositories using Sourcegraph", "function": search_code},
    "llama": {"description": "Ask LLaMA 3 via Ollama", "function": ask_llama},
    "ping": {"description": "Verify if MCP Server is active", "function": ping}
}

def register_tool(name, tool_def):
    @mcp.tool(name=name, description=tool_def["description"])
    def tool_wrapper(input: str):
        start_time = time.perf_counter()
        logging.info(f"Executed tool: {name}")
        logging.info(f"Input received: {input}")
        try:
            result = tool_def["function"](input)
            duration = time.perf_counter() - start_time
            logging.info(f"Result: {result[:500]}")
            logging.info(f"Execution time: {duration:.2f} secs")
            return result
        except Exception as e:
            logging.error(f"Error executing '{name}': {e}")
            return f"Error ejecutando herramienta '{name}': {e}"

    registered_tools.append((name, tool_def["description"]))

for name, tool_def in TOOLS.items():
    register_tool(name, tool_def)

# Interfaz Flask
flask_app = Flask(__name__)
CORS(flask_app)  # ✅ CORS habilitado después de definir flask_app

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>MCP Herramientas</title>
</head>
<body>
    <h1>Herramientas MCP</h1>
    <form method="post">
        <label for="tool">Selecciona herramienta:</label>
        <select name="tool">
            {% for name, desc in tools %}
                <option value="{{ name }}">{{ name }} — {{ desc }}</option>
            {% endfor %}
        </select>
        <label for="input">Input:</label>
        <textarea name="input" rows="5"></textarea>
        <input type="submit" value="Ejecutar">
    </form>
    {% if result %}
        <h2>Resultado:</h2>
        <pre>{{ result }}</pre>
    {% endif %}
</body>
</html>
"""

@flask_app.route("/", methods=["GET", "POST"])
def index():
    result = None
    if request.method == "POST":
        tool_name = request.form["tool"]
        input_text = request.form["input"]
        func = TOOLS[tool_name]["function"]
        result = func(input_text)
    return render_template_string(HTML_TEMPLATE, tools=registered_tools, result=result)

# Ejecutar servidores
if __name__ == "__main__":
    import threading

    def run_mcp():
        print("🧠 MCP Server corriendo en segundo plano")
        mcp.run(transport="sse")

    def run_flask():
        print("🌐 Interfaz Flask disponible en http://127.0.0.1:5000")
        flask_app.run(port=5000)

    threading.Thread(target=run_mcp).start()
    run_flask()