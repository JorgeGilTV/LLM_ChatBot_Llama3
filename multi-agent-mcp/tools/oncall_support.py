import os, requests, datetime, html
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

def confluence_oncall_today(query: str = None) -> str:
    print("🔎 Who is oncall this month?:", query if query else "All teams")
    """
    Lee la página fija de Confluence (On Call Support During Holidays)
    y devuelve quién está oncall durante todo el mes actual.
    Si query está vacío, muestra todos los equipos.
    """
    email = os.getenv("ATLASSIAN_EMAIL")
    token = os.getenv("CONFLUENCE_TOKEN")
    if not email or not token:
        return "<p>Error: ATLASSIAN_EMAIL o CONFLUENCE_TOKEN no están definidos en las variables de entorno.</p>"

    auth = (email, token)
    base_url = "https://arlo.atlassian.net/wiki"
    page_id = "754581728"

    # Traer el contenido en formato storage (HTML)
    url = f"{base_url}/rest/api/content/{page_id}?expand=body.storage"
    response = requests.get(url, auth=auth)
    if response.status_code != 200:
        return f"<p>Error {response.status_code}: {response.reason}</p>"

    data = response.json()
    html_content = data["body"]["storage"]["value"]

    # Parsear el HTML con BeautifulSoup
    soup = BeautifulSoup(html_content, "html.parser")

    # Calcular todas las fechas del mes actual
    today = datetime.date.today()
    start_of_month = today.replace(day=1)
    if today.month == 12:
        next_month = today.replace(year=today.year+1, month=1, day=1)
    else:
        next_month = today.replace(month=today.month+1, day=1)
    end_of_month = next_month - datetime.timedelta(days=1)

    month_dates = [start_of_month + datetime.timedelta(days=i)
                   for i in range((end_of_month - start_of_month).days + 1)]
    month_headers = [d.strftime("%d-%b") for d in month_dates]

    # Buscar encabezados de tabla
    headers = [th.get_text(strip=True) for th in soup.find_all("th")]

    # Renderizar HTML
    output = f"<h2>👩‍💻 Oncall Schedule for {today.strftime('%B %Y')} ({query if query else 'All teams'})</h2>"

    for col_header in month_headers:
        if col_header not in headers:
            continue

        col_index = headers.index(col_header)
        oncall_day = []

        # Recorrer filas de recursos
        for row in soup.find_all("tr"):
            cells = [c.get_text(strip=True) for c in row.find_all("td")]
            if len(cells) > col_index:
                status = cells[col_index]
                if status == "✅":
                    name = cells[0]
                    team = cells[1] if len(cells) > 1 else ""
                    contact = cells[2] if len(cells) > 2 else ""
                    # 🔑 Filtrar solo si query está definido
                    if not query or query.lower() in team.lower():
                        oncall_day.append({"name": name, "team": team, "contact": contact})

        # Tabla por cada día
        output += f"<h3>{col_header}</h3>"
        if not oncall_day:
            output += f"<p>No hay recursos oncall para {col_header} en {query if query else 'All teams'}</p>"
        else:
            output += """
            <table border="1" cellpadding="5" cellspacing="0">
              <tr><th>Name</th><th>Team</th><th>Contact</th></tr>
            """
            for row in oncall_day:
                output += (
                    f"<tr>"
                    f"<td style='color:green;font-weight:bold'>{html.escape(row['name'])}</td>"
                    f"<td>{html.escape(row['team'])}</td>"
                    f"<td>{html.escape(row['contact'])}</td>"
                    f"</tr>"
                )
            output += "</table>"

    return output
