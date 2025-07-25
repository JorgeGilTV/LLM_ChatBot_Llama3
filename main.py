import requests
import subprocess
import tkinter as tk
from tkinter import scrolledtext
from config import (OPENWEATHER_API_KEY,SERPAPI_KEY,FIRECRAWL_KEY,SOURCEGRAPH_TOKEN)

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

#Query router (word selection)
def route_query(query):
    query_lower = query.lower()
    for key, tool in TOOLS.items():
        if key in query_lower:
            if key == "scrape":
                url = query.split("from")[-1].strip()
                return tool["function"](url)
            else:
                return tool["function"](query)
    return ask_llama(query)

#@Main loop
def run_gui():
    window = tk.Tk()
    window.title("LLaMA 3 Agent with Tools ChatBot")

    tk.Label(window, text="Please ask something:").pack(pady=5)
    entry = tk.Entry(window, width=80)
    entry.pack(pady=5)
    output = scrolledtext.ScrolledText(window, wrap=tk.WORD, width=80, height=20)
    output.pack(pady=10)

    def on_submit():
        question = entry.get()
        response = route_query(question)
        output.delete(1.0, tk.END)
        output.insert(tk.END, response)
    
    button_frame = tk.Frame(window)
    button_frame.pack(pady=5)
    tk.Button(button_frame, text="Send", command=on_submit).pack(side=tk.LEFT, padx=10)
    tk.Button(button_frame, text="Exit", command=window.destroy).pack(side=tk.LEFT, padx=10)
    window.mainloop()

#Launch GUI
if __name__ == "__main__":
    #run_gui()
    print("🤖 🤖 🤖 LLaMA 3 Agent with Active Tools 🤖 🤖 🤖")
    while True:
        user_input = input("Please do the question: ")
        if user_input.lower() in ["exit", "quit"]:
            break
        response = route_query(user_input)
        print(f"Answering: {response}")
