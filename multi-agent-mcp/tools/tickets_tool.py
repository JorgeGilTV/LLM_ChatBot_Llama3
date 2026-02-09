import json, html, requests, os

def read_tickets(query: str) -> str:
    session = requests.Session()
    session.auth = (os.getenv("SNOW_USER"), os.getenv("SNOW_PASSWORD"))
    session.headers.update({"Accept": "application/json"})

    if "accepted_tickets" not in globals():
        globals()["accepted_tickets"] = {}

    tabla = []

    # ✅ Caso directo: query es un sys_id de ServiceNow (32 caracteres hex)
    if len(query) == 32:
        ticket_url = f"https://arlo.service-now.com/api/now/table/incident/{query}"
        response = session.get(ticket_url)
        if response.status_code != 200:
            return f"<p>Error {response.status_code}: {response.reason}</p>"
        issue = response.json().get("result", {})
        key = issue.get("number")
        status = issue.get("state")
        summary = issue.get("short_description")
        description = issue.get("description", "No description available")
        # ⚠️ Los comentarios en ServiceNow se obtienen de journal entries, aquí simplificado
        comments = issue.get("comments", [])
        last_two_comments = comments[-2:] if comments else ["No comments available"]
        url = f"https://arlo.service-now.com/now/nav/ui/classic/params/target/incident.do?sys_id={query}"
        if status in ["Accepted", "Closed", "Cancelled", "Resolved"]:
            globals()["accepted_tickets"][key] = {
                "status": status,
                "summary": summary,
                "description": description,
                "comments": last_two_comments,
                "url": url
            }
            return f"<p>Incident {key} almacenado en Accepted (no mostrado en tabla).</p>"
        tabla.append({
            "key": key,
            "status": status,
            "summary": summary,
            "description": description,
            "last_comments": last_two_comments,
            "url": url
        })
        return _render_table(tabla)

    # ✅ Caso normal: búsqueda por texto en short_description
    search_url = f'https://arlo.service-now.com/api/now/table/incident?sysparm_query=short_descriptionLIKE{query}&sysparm_limit=50'
    response = session.get(search_url)
    if response.status_code != 200:
        return f"<p>Error {response.status_code}: {response.reason}</p>"
    issues = response.json().get("result", [])
    if not issues:
        return f"<p>No se encontró información para: '{html.escape(query)}'</p>"

    for issue in issues:
        key = issue.get("number")
        status = issue.get("state")
        summary = issue.get("short_description")
        description = issue.get("description", "No description available")
        comments = issue.get("comments", [])
        last_two_comments = comments[-2:] if comments else ["No comments available"]
        url = f"https://arlo.service-now.com/now/nav/ui/classic/params/target/incident.do?sys_id={issue.get('sys_id')}"
        if status in ["Accepted", "Closed", "Cancelled", "Resolved"]:
            globals()["accepted_tickets"][key] = {
                "status": status,
                "summary": summary,
                "description": description,
                "comments": last_two_comments,
                "url": url
            }
            continue
        tabla.append({
            "key": key,
            "status": status,
            "summary": summary,
            "description": description,
            "last_comments": last_two_comments,
            "url": url
        })

    if not tabla:
        return f"<p>No se encontraron incidents abiertos para: '{html.escape(query)}'</p>"
    return _render_table(tabla)


def _render_table(tabla):
    output = f"""
        <style>
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
        </style>
        <div class="response-area">
            <p><strong>Incidents Found:</strong> {len(tabla)}</p>
            <table class="ticket-table">
                <tr>
                    <th>INCIDENT</th>
                    <th>STATUS</th>
                    <th>SUMMARY</th>
                    <th>DESCRIPTION</th>
                    <th>LAST 2 COMMENTS</th>
                    <th>LINK</th>
                </tr>
    """
    for row in tabla:
        output += f"<tr>"
        output += f"<td>{html.escape(row['key'])}</td>"
        output += f"<td>{html.escape(str(row['status']))}</td>"
        output += f"<td>{html.escape(row['summary'])}</td>"
        output += f"<td>{html.escape(row['description'])}</td>"
        comments_html = "<br>".join([html.escape(c) for c in row['last_comments']])
        output += f"<td>{comments_html}</td>"
        output += f"<td><a href='{row['url']}' target='_blank'>Open</a></td>"
        output += "</tr>"
    output += "</table></div>"
    return output
