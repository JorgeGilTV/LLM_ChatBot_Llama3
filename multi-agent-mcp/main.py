from mcp.server.fastmcp import FastMCP

# 🔧 Herramientas
import requests
import subprocess
from config import (
    OPENWEATHER_API_KEY,
    SERPAPI_KEY,
    FIRECRAWL_KEY,
    SOURCEGRAPH_TOKEN,
    CLAUDE_API_KEY
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
        # Limpia prefijos como "llama3 >"
        if ">" in output:
            output = output.split(">", 1)[-1].strip()
        return output
    except Exception as e:
        return f"Error running LLaMA: {e}"

# 🤖 Claude
def ask_claude(prompt: str) -> str:
    try:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        data = {
            "model": "claude-3-opus-20240229",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}]
        }
        response = requests.post(url, json=data, headers=headers)
        result = response.json()
        return result.get("content", [{}])[0].get("text", "No response from Claude.")
    except Exception as e:
        return f"Claude error: {e}"

# 🔍 Web Search
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

# 🕸️ Web Scraping
def crawl_url(url: str) -> str:
    try:
        headers = {"Authorization": f"Bearer {FIRECRAWL_KEY}"}
        response = requests.post("https://api.firecrawl.dev/scrape", json={"url": url}, headers=headers)
        data = response.json()
        return data.get("text", "Could not extract content.")
    except Exception as e:
        return f"Scraping error: {e}"

# 🧑‍💻 Code Search
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

# ✅ Herramienta de prueba
def ping(_: str) -> str:
    return "✅ Servidor MCP activo y funcional"

# 🧰 Diccionario de herramientas
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
    "claude": {
        "description": "Ask Claude 3 via Anthropic API",
        "function": ask_claude
    },
    "ping": {
        "description": "Verifica si el servidor MCP está activo",
        "function": ping
    }
}

# 🚀 Servidor MCP
mcp = FastMCP("Servidor MCP para Claude Desktop")
registered_tools = []

# ✅ Registrar herramientas correctamente
def register_tool(name, func, description):
    @mcp.tool(name=name, description=description)
    def tool_wrapper(input: str):
        print(f"🔧 Ejecutando herramienta '{name}' con input: {input}")
        return func(input)
    registered_tools.append((name, description))

for name, tool_def in TOOLS.items():
    register_tool(name, tool_def["function"], tool_def["description"])

# 🧠 Ejecutar servidor
if __name__ == "__main__":
    print("🧠 MCP Server corriendo para Claude Desktop")
    print("🔧 Herramientas registradas:")
    for name, description in registered_tools:
        print(f" - {name}: {description}")
    mcp.run(transport="sse")