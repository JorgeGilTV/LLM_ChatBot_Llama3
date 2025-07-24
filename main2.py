import requests
import subprocess
from config import (
    OPENWEATHER_API_KEY,
    SERPAPI_KEY,
    FIRECRAWL_KEY,
    SOURCEGRAPH_TOKEN
)

# 🧠 Tool: LLaMA 3
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

# 🌤️ Tool: Weather
def get_weather(city):
    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={OPENWEATHER_API_KEY}&units=metric"
        response = requests.get(url)
        data = response.json()
        if data.get("main"):
            temp = data["main"]["temp"]
            desc = data["weather"][0]["description"]
            return f"The weather in {city} is {desc} with {temp}°C."
        return "Could not retrieve weather data."
    except Exception as e:
        return f"Weather error: {e}"

# 🔍 Tool: Web Search
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

# 🕸️ Tool: Web Scraping
def crawl_url(url):
    try:
        headers = {"Authorization": f"Bearer {FIRECRAWL_KEY}"}
        response = requests.post("https://api.firecrawl.dev/scrape", json={"url": url}, headers=headers)
        data = response.json()
        return data.get("text", "Could not extract content.")
    except Exception as e:
        return f"Scraping error: {e}"

# 🧠 Tool: Code Search
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

# 🧰 Tool registry
TOOLS = {
    "weather": {
        "description": "Get current weather for a city",
        "function": get_weather
    },
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

# 🧠 Query router
def route_query(query):
    query_lower = query.lower()
    for key, tool in TOOLS.items():
        if key in query_lower:
            if key == "weather":
                city = query.split("in")[-1].strip()
                return tool["function"](city)
            elif key == "scrape":
                url = query.split("from")[-1].strip()
                return tool["function"](url)
            else:
                return tool["function"](query)
    return ask_llama(query)

# 🚀 Main loop
if __name__ == "__main__":
    print("🤖 LLaMA 3 Agent with Active Tools")
    while True:
        user_input = input("🧠 Question: ")
        if user_input.lower() in ["exit", "quit"]:
            break
        response = route_query(user_input)
        print(f"🔧 Answer: {response}")