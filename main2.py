import requests
import subprocess
from config import (
    OPENWEATHER_API_KEY,
    SERPAPI_KEY,
    FIRECRAWL_KEY,
    SOURCEGRAPH_TOKEN,
    CLAUDE_API_KEY
)
from flask import Flask, request, jsonify
from flask_cors import CORS

#@Tool: LLaMA 3
def ask_llama(prompt):
    try:
        result = subprocess.run(
            ["ollama", "run", "llama3"],
            input=prompt.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        return result.stdout.decode("utf-8")
    except Exception as e:
        return f"Error running LLaMA: {e}"

#@Tool: Claude
def ask_claude(prompt):
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
        return result["content"][0]["text"]
    except Exception as e:
        return f"Claude error: {e}"

#@Tool: MCP (Model Context Protocol)
def ask_mcp(prompt):
    try:
        # Detectar herramienta desde el prompt
        for key in TOOLS.keys():
            if key in prompt.lower():
                tool_name = key
                break
        else:
            return "No matching MCP tool found in prompt."

        # Enviar solicitud JSON-RPC al servidor MCP
        url = "http://localhost:3333/jsonrpc"
        payload = {
            "jsonrpc": "2.0",
            "method": "call_tool",
            "params": {
                "tool": tool_name,
                "input": prompt
            },
            "id": 1
        }
        response = requests.post(url, json=payload)
        result = response.json()
        return result.get("result", "No result from MCP.")
    except Exception as e:
        return f"MCP error: {e}"

#@Tool: Web Search
def search_web(query):
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

#@Tool: Web Scraping
def crawl_url(url):
    try:
        headers = {"Authorization": f"Bearer {FIRECRAWL_KEY}"}
        response = requests.post("https://api.firecrawl.dev/scrape", json={"url": url}, headers=headers)
        data = response.json()
        return data.get("text", "Could not extract content.")
    except Exception as e:
        return f"Scraping error: {e}"

#@Tool: Code Search
def search_code(query):
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
        return response.json()
    except Exception as e:
        return f"Code search error: {e}"

#@Tool registry
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
    }
}

#Query router
def route_query(query):
    query_lower = query.lower()
    for key, tool in TOOLS.items():
        if key in query_lower:
            if key == "scrape":
                url = query.split("from")[-1].strip()
                return tool["function"](url)
            else:
                return tool["function"](query)

    if "claude:" in query_lower:
        return ask_claude(query.replace("claude:", "").strip())
    elif "mcp:" in query_lower:
        return ask_mcp(query.replace("mcp:", "").strip())
    else:
        return ask_llama(query)

#@Flask API
app = Flask(__name__)
CORS(app)

@app.route("/ask", methods=["GET", "POST"])
def ask():
    if request.method == "POST":
        data = request.get_json()
        question = data.get("question", "")
    else:
        question = request.args.get("question", "")

    if not question:
        return jsonify({"error": "Missing 'question' parameter"}), 400

    response = route_query(question)
    return jsonify({"response": response})

@app.route("/")
def index():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Multi-Agent ChatBot</title>
        <style>
            body { font-family: Arial; padding: 20px; background-color: #f4f4f4; }
            #container { max-width: 700px; margin: auto; background: white; padding: 20px; border-radius: 8px; }
            textarea { width: 100%; height: 200px; margin-top: 10px; }
            input[type=text] { width: 100%; padding: 10px; margin-top: 10px; }
            button { padding: 10px 20px; margin-top: 10px; }
        </style>
    </head>
    <body>
        <div id="container">
            <h2>🧠 Multi-Agent ChatBot</h2>
            <input type="text" id="question" placeholder="Ask something...">
            <button onclick="sendQuestion()">Send</button>
            <textarea id="response" readonly placeholder="Response will appear here..."></textarea>
        </div>

        <script>
            async function sendQuestion() {
                const question = document.getElementById("question").value;
                const responseBox = document.getElementById("response");
                responseBox.value = "Thinking...";

                try {
                    const res = await fetch("/ask", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ question })
                    });
                    const data = await res.json();
                    responseBox.value = data.response || "No response.";
                } catch (err) {
                    responseBox.value = "Error: " + err;
                }
            }
        </script>
    </body>
    </html>
    """

#@Main launcher
if __name__ == "__main__":
    print("🚀 Starting Flask API on http://127.0.0.1:5000")
    app.run(host="0.0.0.0", port=5000)