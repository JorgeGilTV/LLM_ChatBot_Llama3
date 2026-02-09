import os
import requests
import html

def noc_kt_search(query):
    print("🔎 Buscando en tabla Service Owners:", query)

    email = os.getenv("ATLASSIAN_EMAIL")
    token = os.getenv("CONFLUENCE_TOKEN")
    if not email or not token:
        return "<p>Error: ATLASSIAN_EMAIL o CONFLUENCE_TOKEN no están definidos en las variables de entorno.</p>"

    auth = (email, token)

    page_id = "55187717"
    url = f"https://arlo.atlassian.net/wiki/rest/api/content/{page_id}?expand=body.atlas_doc_format"

    response = requests.get(url, auth=auth)
    if response.status_code != 200:
        return f"<p>Error {response.status_code}: {response.reason}</p>"

    data = response.json()
    adf = data.get("body", {}).get("atlas_doc_format", {}).get("value", "")
    if not adf:
        return "<p>No se encontró contenido en formato ADF.</p>"

    import json
    doc = json.loads(adf)

    # Buscar la tabla dentro del JSON
    tables = [node for node in doc.get("content", []) if node.get("type") == "table"]
    if not tables:
        return "<p>No se encontró ninguna tabla en la página.</p>"

    table = tables[0]
    rows = table.get("content", [])

    headers = []
    filtered_rows = []

    for row in rows:
        cells = []
        for cell in row.get("content", []):
            # Cada celda puede tener párrafos con texto o menciones
            cell_text_parts = []
            for paragraph in cell.get("content", []):
                for item in paragraph.get("content", []):
                    if item["type"] == "text":
                        cell_text_parts.append(item["text"])
                    elif item["type"] == "mention":
                        cell_text_parts.append(item["attrs"]["text"])  # aquí aparece el @usuario
            cells.append(" ".join(cell_text_parts).strip())
        # Si es la primera fila, son los headers
        if row["content"][0]["type"] == "tableHeader":
            headers = cells
        else:
            row_text = " ".join(cells)
            if query.lower() in row_text.lower():
                filtered_rows.append(cells)

    if not filtered_rows:
        return f"<p>No se encontraron coincidencias en la tabla para: <strong>{html.escape(query)}</strong></p>"

    # Construir tabla HTML
    table_html = "<table border='1' style='border-collapse:collapse; width:100%;'>"
    if headers:
        table_html += "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
    for cells in filtered_rows:
        row_html = "<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in cells) + "</tr>"
        table_html += row_html
    table_html += "</table>"

    return table_html
