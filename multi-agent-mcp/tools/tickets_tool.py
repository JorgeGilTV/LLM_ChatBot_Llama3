import json, html, requests, os

def read_tickets(query: str) -> str:
    session = requests.Session()
    session.auth = (os.getenv("ITRACK_USER"), os.getenv("ITRACK_PASSWORD"))
    if "accepted_tickets" not in globals():
        globals()["accepted_tickets"] = {}
    # ✅ Caso directo: query es un ticket ID tipo CDEX-xxxxx
    if query.startswith("CDEX-") and query[5:].isdigit():
        ticket_url = f"https://itrack.web.att.com/rest/api/2/issue/{query}?expand=comments"
        response = session.get(ticket_url)
        if response.status_code != 200:
            return f"<p>Error {response.status_code}: {response.reason}</p>"
        issue = response.json()
        key = issue["key"]
        status = issue["fields"]["status"]["name"]
        summary = issue["fields"]["summary"]
        description = issue["fields"].get("description", "No description available")
        comments = issue["fields"].get("comment", {}).get("comments", [])
        last_two_comments = [c.get("body", "") for c in comments[-2:]] if comments else ["No comments available"]
        url = f"https://itrack.web.att.com/projects/CDEX/issues/{key}"
        if status.lower() == "accepted":
            globals()["accepted_tickets"][key] = {
                "status": status,
                "summary": summary,
                "description": description,
                "comments": last_two_comments,
                "url": url
            }
            return f"<p>Ticket {key} almacenado en Accepted (no mostrado en tabla).</p>"
        # ✅ Mostrar solo si empieza con CDEX
        if key.startswith("CDEX-"):
            return _render_table([{
                "key": key,
                "status": status,
                "summary": summary,
                "description": description,
                "last_comments": last_two_comments,
                "url": url
            }])
        else:
            return f"<p>Ticket {key} no mostrado (no es CDEX).</p>"
    # ✅ Caso normal: búsqueda por texto en summary/description
    jql = f'(description ~ "{query}")'
    search_url = f'https://itrack.web.att.com/rest/api/2/search?jql={jql}&maxResults=50'
    response = session.get(search_url)
    if response.status_code != 200:
        return f"<p>Error {response.status_code}: {response.reason}</p>"
    data = response.json()
    issues = data.get("issues", [])
    if not issues:
        return f"<p>No se encontró información para: '{html.escape(query)}'</p>"
    tabla = []
    for issue in issues:
        key = issue["key"]
        # 🔎 Segunda llamada para traer comentarios completos
        issue_url = f'https://itrack.web.att.com/rest/api/2/issue/{key}?expand=comments'
        issue_resp = session.get(issue_url)
        if issue_resp.status_code != 200:
            continue
        full_issue = issue_resp.json()
        status = full_issue["fields"]["status"]["name"]
        summary = full_issue["fields"]["summary"]
        description = full_issue["fields"].get("description", "No description available")
        comments = full_issue["fields"].get("comment", {}).get("comments", [])
        last_two_comments = [c.get("body", "") for c in comments[-2:]] if comments else ["No comments available"]
        url = f"https://itrack.web.att.com/projects/CDEX/issues/{key}"
        if status.lower() == "accepted" or status.lower() == "closed" or status.lower() == "test complete" or status.lower() == "dev complete" or status.lower() == "cancelled":
            globals()["accepted_tickets"][key] = {
                "status": status,
                "summary": summary,
                "description": description,
                "comments": last_two_comments,
                "url": url
            }
            continue
        # ✅ Mostrar solo si empieza con CDEX
        if key.startswith("CDEX-"):
            tabla.append({
                "key": key,
                "status": status,
                "summary": summary,
                "description": description,
                "last_comments": last_two_comments,
                "url": url
            })
    if not tabla:
        return f"<p>We didnt found any ticket open for: '{html.escape(query)}'</p>"
    return _render_table(tabla)
def _render_table(tabla):
    output = f"""
        <style>
        .response-area {{
            padding: 10px;
            border-radius: 6px;
        }}

        /*  Modo claro */
        @media (prefers-color-scheme: light) {{
            .response-area {{
                background-color: #f5f5f5; /* gris claro */
                color: #000000;
            }}
            .ticket-table {{
                background-color: #ffffff;
                color: #000000;
            }}
        }}

        /*  Modo oscuro */
        @media (prefers-color-scheme: dark) {{
            .response-area {{
                background-color: #2b2b2b; /* gris oscuro */
                color: #f0f0f0;
            }}
            .ticket-table {{
                background-color: #1e1e1e;
                color: #f0f0f0;
            }}
            .ticket-table th {{
                background-color: #333333;
            }}
            .ticket-table tr:nth-child(even) {{
                background-color: #2a2a2a;
            }}
        }}

        .ticket-table {{
            border-collapse: collapse;
            width: 100%;
            font-family: Arial, sans-serif;
            font-size: 14px;
        }}
        .ticket-table th, .ticket-table td {{
            border: 1px solid #ccc;
            padding: 8px;
            text-align: left;
            vertical-align: top;
        }}
        .ticket-table th {{
            background-color: #f2f2f2;
        }}
        .ticket-table tr:nth-child(even) {{
            background-color: #fafafa;
        }}
        .status-In\\ Progress {{ background-color: #fff3cd; }}
        .status-To\\ Do {{ background-color: #e0f7fa; }}
        .status-Open {{ background-color: #e0f7fa; }}
        </style>
        <div class="response-area">
            <p><strong>Tickets Found:</strong> {len(tabla)}</p>
            <table class="ticket-table">
                <tr>
                    <th>TICKET</th>
                    <th>STATUS</th>
                    <th>SUMMARY</th>
                    <th>DESCRIPTION</th>
                    <th>LAST 2 COMMENTS</th>
                    <th>LINK</th>
                    <th>URL</th>
                </tr>
    """
    for row in tabla:
        status_value = row.get('status') or "Unknown"
        status_class = f"status-{status_value.replace(' ', '\\ ')}"
        output += f"<tr class='{status_class}'>"
        output += f"<td>{html.escape(row['key'])}</td>"
        output += f"<td>{html.escape(status_value)}</td>"
        output += f"<td>{html.escape(row['summary'])}</td>"
        output += f"<td>{html.escape(row['description'])}</td>"
        output += f"<td>{"<br>".join([html.escape(c) for c in row['last_comments']])}</td>"
        output += f"<td><a href='{row['url']}' target='_blank'>Open</a></td>"
        output += f"<td>{html.escape(row['url'])}</td>"
        output += "</tr>"
    output += "</table></div>"
    return output
