import os, requests, datetime, html, urllib.parse
from dotenv import load_dotenv
from tools.gemini_tool import ask_gemini

load_dotenv()

def confluence_search(query: str) -> str:
    print("🔎 Searching Confluence:", query)

    email = os.getenv("ATLASSIAN_EMAIL")
    token = os.getenv("CONFLUENCE_TOKEN")
    if not email or not token:
        return "<p>Error: ATLASSIAN_EMAIL and CONFLUENCE_TOKEN must be set in the environment.</p>"

    auth = (email, token)

    trimmed_query = query
    # CQL: search pages (exclude titles containing .jpg)
    cql = f'text ~ "{trimmed_query}" AND type = "page" AND title !~ ".jpg"'
    search_url = (
        "https://arlo.atlassian.net/wiki/rest/api/search"
        f"?cql={urllib.parse.quote(cql)}&expand=content,space"
    )

    try:
        response = requests.get(search_url, auth=auth)
    except Exception as e:
        return f"<p>Error connecting to Confluence API: {html.escape(str(e))}</p>"

    if response.status_code != 200:
        return f"<p>Error {response.status_code}: {response.reason}</p>"

    print("Status:", response.status_code)
    print("Body preview:", response.text[:300])

    try:
        data = response.json()
    except Exception as e:
        return f"<p>Error parsing JSON: {html.escape(str(e))}</p>"

    results = data.get("results", [])
    if not results:
        return (
            f"<p>No documents found matching: "
            f"<strong>{html.escape(trimmed_query)}</strong></p>"
        ) + ask_gemini(query, ["Ask_Gemini"])

    keywords = ["troubleshooting", "debug", "issue", "error", "fix", "failure", "incident", "how-to"]

    def relevance_score(item):
        title = item.get("title", "").lower()
        labels = item.get("metadata", {}).get("labels", [])
        score = sum(1 for kw in keywords if kw in title)
        score += sum(1 for kw in keywords if kw in labels)
        score += 2 if trimmed_query.lower() in title else 0
        last_modified = item.get("version", {}).get("when")
        if last_modified:
            try:
                dt = datetime.datetime.strptime(last_modified[:10], "%Y-%m-%d")
                days_ago = (datetime.datetime.now() - dt).days
                score += max(0, 30 - days_ago) // 10
            except:
                pass
        return score

    scored_results = sorted(results, key=relevance_score, reverse=True)[:20]

    if not scored_results:
        return f"<p>No relevant documents found for: <strong>{html.escape(trimmed_query)}</strong></p>"

    output = "<h2>📚 Confluence search results</h2>"
    output += """
    <table border="1" cellpadding="5" cellspacing="0">
        <tr>
            <th>Title</th>
            <th>Link</th>
        </tr>
    """
    for item in scored_results:
        title = item.get("title", "Untitled")
        page_id = (
            item.get("content", {}).get("id")
            or item.get("id")
        )
        space_key = item.get("space", {}).get("key", "AWT")

        if not page_id or not space_key:
            print("⚠️ Item missing page_id or space_key:", item.get("title"))
            continue

        slug = urllib.parse.quote_plus(title)

        url = f"https://arlo.atlassian.net/wiki/spaces/{space_key}/pages/{page_id}/{slug}"
        output += f"""
        <tr>
            <td>{html.escape(title)}</td>
            <td><a href="{url}" target="_blank">Open</a></td>
        </tr>
        """
    output += "</table>"
    return output
