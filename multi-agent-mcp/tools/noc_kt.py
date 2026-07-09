import os
import requests
import html

NOC_KT_KEYWORDS = (
    "noc kt",
    "noc knowledge",
    "knowledge transfer",
    "kt table",
    "noc runbook",
    "noc playbook",
)


def is_noc_kt_question(question: str) -> bool:
    if not (question or "").strip():
        return False
    ql = question.lower()
    return any(kw in ql for kw in NOC_KT_KEYWORDS)


def noc_kt_search(query):
    print("🔎 Searching NOC KT table:", query)

    email = os.getenv("ATLASSIAN_EMAIL")
    token = os.getenv("CONFLUENCE_TOKEN")
    if not email or not token:
        return "<p>Error: ATLASSIAN_EMAIL and CONFLUENCE_TOKEN must be set in the environment.</p>"

    auth = (email, token)

    page_id = "55187717"
    url = f"https://arlo.atlassian.net/wiki/rest/api/content/{page_id}?expand=body.atlas_doc_format"

    response = requests.get(url, auth=auth)
    if response.status_code != 200:
        return f"<p>Error {response.status_code}: {response.reason}</p>"

    data = response.json()
    adf = data.get("body", {}).get("atlas_doc_format", {}).get("value", "")
    if not adf:
        return "<p>No ADF content found on the page.</p>"

    import json
    doc = json.loads(adf)

    tables = [node for node in doc.get("content", []) if node.get("type") == "table"]
    if not tables:
        return "<p>No table found on the page.</p>"

    table = tables[0]
    rows = table.get("content", [])

    headers = []
    filtered_rows = []

    for row in rows:
        cells = []
        for cell in row.get("content", []):
            cell_text_parts = []
            for paragraph in cell.get("content", []):
                for item in paragraph.get("content", []):
                    if item["type"] == "text":
                        cell_text_parts.append(item["text"])
                    elif item["type"] == "mention":
                        cell_text_parts.append(item["attrs"]["text"])
            cells.append(" ".join(cell_text_parts).strip())
        if row["content"][0]["type"] == "tableHeader":
            headers = cells
        else:
            row_text = " ".join(cells)
            if query.lower() in row_text.lower():
                filtered_rows.append(cells)

    if not filtered_rows:
        return f"<p>No matches in the table for: <strong>{html.escape(query)}</strong></p>"
    table_html = "<table border='1' style='border-collapse:collapse; width:100%;'>"
    if headers:
        table_html += "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
    for cells in filtered_rows:
        row_html = "<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in cells) + "</tr>"
        table_html += row_html
    table_html += "</table>"

    return table_html


def extract_noc_kt_query(question: str) -> str:
    """Pull search term from natural-language NOC KT questions."""
    q = (question or "").strip()
    if not q:
        return ""
    ql = q.lower()
    for marker in (
        "search noc kt for",
        "noc kt for",
        "noc kt about",
        "noc knowledge for",
        "kt table for",
        "noc kt ",
        "kt table ",
    ):
        if marker in ql:
            idx = ql.index(marker) + len(marker)
            return q[idx:].strip(" :?.")
    return q


def noc_kt_search_mcp(query: str = "", question: str = "") -> str:
    """MCP entry: search the NOC KT Confluence table."""
    search = (query or "").strip()
    if not search and (question or "").strip():
        search = extract_noc_kt_query(question)
    if not search:
        return (
            "<p style='color:#b45309;'>Provide a <code>query</code> or <code>question</code> "
            "to search the NOC KT table (e.g. service name, escalation path).</p>"
        )
    return noc_kt_search(search)
