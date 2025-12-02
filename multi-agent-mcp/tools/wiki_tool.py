import os, requests, datetime, html
from tools.gemini_tool import ask_gemini

def wiki_search(query: str) -> str:
    token = os.getenv("WIKI_TOKEN")
    if not token:
        return "<p>Error: WIKI_TOKEN not defined for environment variables.</p>"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    summary_context = None
    # ✅ if query is a ticket like CDEX-xxxxx, get summary from Jira
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
    # 🧠 CQL with filters: search in text & title, exclude images
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
        print(f"<p>No documents found related to: <strong>{html.escape(trimmed_query)}</strong></p>")
        wiki=ask_gemini(query, ["Ask_Gemini"])
        return wiki
    # 🔍 key words
    keywords = ["troubleshooting", "debug", "issue", "error", "fix", "failure", "incident", "how-to"]
    def relevance_score(item):
        title = item.get("title", "").lower()
        labels = item.get("metadata", {}).get("labels", [])
        score = sum(1 for kw in keywords if kw in title)
        score += sum(1 for kw in keywords if kw in labels)
        score += 2 if trimmed_query.lower() in title else 0
        # 📅 Priorice by date
        last_modified = item.get("version", {}).get("when")
        if last_modified:
            try:
                dt = datetime.datetime.strptime(last_modified[:10], "%Y-%m-%d")
                days_ago = (datetime.datetime.now() - dt).days
                score += max(0, 30 - days_ago) // 10
            except:
                pass
        return score
    # 🔽 Order by score desc & limit to 10
    scored_results = sorted(results, key=relevance_score, reverse=True)[:10]
    if not scored_results:
        return f"<p>No relevant troubleshooting documents found for: <strong>{html.escape(trimmed_query)}</strong></p>"
    # 🧾 build HTML table with context header
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