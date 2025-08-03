from mcp.server.fastmcp import FastMCP
from flask import Flask, request, render_template_string

# 🔧 Herramientas
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

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("agent_tool_logs.log"),
        logging.StreamHandler()
    ]
)

# LLaMA 3
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

# Web Search
def search_web(query: str) -> str:
    try:
        url = "https://serpapi.com/search"
        params = {
            "q": query,
            "api_key": SERPAPI_KEY
        }
        response = requests.get(url, params=params)
        results = response.json()
        if "organic_results" in results:
            return results["organic_results"][0].get("snippet", "No snippet available.")
        return "No results found."
    except Exception as e:
        return f"Search error: {e}"

# Web Scraping
def crawl_url(url: str) -> str:
    try:
        headers = {"Authorization": f"Bearer {FIRECRAWL_KEY}"}
        response = requests.post("https://api.firecrawl.dev/scrape", json={"url": url}, headers=headers)
        data = response.json()
        return data.get("text", "Could not extract content.")
    except Exception as e:
        return f"Scraping error: {e}"

# Code Search
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

# Ping
def ping(_: str) -> str:
    return "✅ Servidor MCP activo y funcional"

# MCP Server
mcp = FastMCP("Servidor MCP con herramientas para agentes")
registered_tools = []

# Diccionario de herramientas
TOOLS = {
    "search": {
        "description": "Search the web using SerpAPI",
        "function": search_web
    },
    "scrape": {
        "description": "Extract content from a URL using Firecrawl",
        "function": crawl_url
    },
    "code": {
        "description": "Search public code repositories using Sourcegraph",
        "function": search_code
    },
    "llama": {
        "description": "Ask LLaMA 3 via Ollama",
        "function": ask_llama
    },
    "ping": {
        "description": "Verifica si el servidor MCP está activo",
        "function": ping
    }
}

# 🔧 Registrar herramientas con logging
def register_tool(name, tool_def):
    @mcp.tool(name=name, description=tool_def["description"])
    def tool_wrapper(input: str):
        start_time = time.perf_counter()
        logging.info(f"Herramienta ejecutada: {name}")
        logging.info(f"Input recibido: {input}")
        try:
            result = tool_def["function"](input)
            duration = time.perf_counter() - start_time
            logging.info(f"Resultado: {result[:500]}")
            logging.info(f"Tiempo de ejecución: {duration:.2f} segundos")
            return result
        except Exception as e:
            logging.error(f"Error ejecutando '{name}': {e}")
            return f"Error ejecutando herramienta '{name}': {e}"

    registered_tools.append((name, tool_def["description"]))

for name, tool_def in TOOLS.items():
    register_tool(name, tool_def)

# 🌐 Interfaz Flask
flask_app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>MCP Herramientas</title>
    <style>
        body {
            font-family: "Segoe UI", Roboto, sans-serif;
            background-color: #f9f9fb;
            color: #333;
            margin: 0;
            padding: 0;
        }
        header {
            background-color: #4f46e5;
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
        select, textarea, input[type="submit"] {
            width: 100%;
            padding: 0.75rem;
            margin-top: 0.5rem;
            border: 1px solid #ccc;
            border-radius: 8px;
            font-size: 1rem;
        }
        input[type="submit"] {
            background-color: #4f46e5;
            color: white;
            border: none;
            cursor: pointer;
            transition: background-color 0.2s ease;
        }
        input[type="submit"]:hover {
            background-color: #4338ca;
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
        <h1> Herramientas y aplicaciones del Model Context Platform</h1>
    </header>
    <main>
        <form method="post">
            <label for="tool">Selecciona una herramienta que quieres utilizar:</label>
            <select name="tool">
                {% for name, desc in tools %}
                    <option value="{{ name }}">{{ name }} — {{ desc }}</option>
                {% endfor %}
            </select>

            <label for="input">Escribe tu input:</label>
            <textarea name="input" rows="5" placeholder="Ejemplo: ¿Cuantos empleados tiene amdocs en mexico?"></textarea>

            <input type="submit" value="Ejecutar herramienta">
        </form>

        {% if result %}
            <h2>Respuesta:</h2>
            <pre>{{ result }}</pre>
        {% endif %}
    </main>
    <footer>
        Servidor MCP corriendo ........... Claude Style✨
    </footer>
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

# 🚀 Ejecutar ambos servidores
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