import os
import requests
import html

def service_owners_search(query):
    print("🔎 Searching Service Owners table:", query)

    email = os.getenv("ATLASSIAN_EMAIL")
    token = os.getenv("CONFLUENCE_TOKEN")
    if not email or not token:
        return "<p>Error: ATLASSIAN_EMAIL and CONFLUENCE_TOKEN must be set in the environment.</p>"

    auth = (email, token)

    page_id = "55156845"
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

    # Add source URL at the bottom
    confluence_url = f"https://arlo.atlassian.net/wiki/spaces/ET/pages/{page_id}"
    table_html += f"""
    <div style='background: #f3f4f6; padding: 12px; border-radius: 6px; margin: 16px 0 0 0; border: 1px solid #d1d5db;'>
        <p style='margin: 0 0 8px 0; color: #374151; font-size: 13px; font-weight: bold;'>
            📚 Source Information:
        </p>
        <a href='{confluence_url}' target='_blank' 
           style='display: inline-block; background: #0052CC; color: white; padding: 8px 16px; 
                  border-radius: 4px; text-decoration: none; font-size: 13px; font-weight: bold;'>
            🔗 View in Confluence (Service Owners)
        </a>
        <p style='margin: 8px 0 0 0; color: #6b7280; font-size: 11px;'>
            Page ID: {page_id}
        </p>
    </div>
    """

    return table_html
