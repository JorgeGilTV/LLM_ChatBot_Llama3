import requests, html
from bs4 import BeautifulSoup

def read_arlo_status(query: str) -> str:
    print("🔎 Leyendo el status de Arlo:", query)
    """
    Lee https://status.arlo.com y devuelve un bloque HTML con:
    - Resumen general
    - Tabla de servicios principales
    - Tabla de incidentes pasados
    """
    url = "https://status.arlo.com"
    try:
        resp = requests.get(url, timeout=15)
    except Exception as e:
        return f"<p>Error al obtener Arlo Status: {html.escape(str(e))}</p>"
    if resp.status_code != 200:
        return f"<p>Error {resp.status_code}: {html.escape(resp.reason)}</p>"

    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text("\n", strip=True)
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    # Extraer resumen
    summary = next((l for l in lines if "operational" in l.lower()), "No summary found")

    # Extraer servicios (ejemplo: "Log In All Good")
    core_services = []
    for i, l in enumerate(lines):
        if l in ["Log In","Notifications","Library","Live Streaming","Video Recording","Arlo Store","Community"]:
            if i+1 < len(lines):
                core_services.append({"service": l, "status": lines[i+1]})

    # Extraer incidentes pasados
    past_incidents = []
    for i, l in enumerate(lines):
        if any(day in l.lower() for day in ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]):
            if i+1 < len(lines):
                past_incidents.append({"date": l, "detail": lines[i+1]})

    return _render_arlo_html(summary, core_services, past_incidents, url)


def _render_arlo_html(summary, core_services, past_incidents, source_url):
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
      }}
      .ticket-table th {{
        background-color: #f2f2f2;
      }}
      .ticket-table tr:nth-child(even) {{
        background-color: #fafafa;
      }}
      .status-good {{
        color: green;
        font-weight: bold;
      }}
      .status-bad {{
        color: red;
        font-weight: bold;
      }}
    </style>
    <div class="response-area">
      <p><strong>Arlo Status:</strong> {html.escape(summary)}</p>
      <p>Fuente: <a href="{html.escape(source_url)}" target="_blank">{html.escape(source_url)}</a></p>

      <h3>Core Services</h3>
      <table class="ticket-table">
        <tr><th>Service</th><th>Status</th></tr>
    """
    for row in core_services:
        status_text = row['status']
        # Si el status es exactamente "All Good" → verde, si no → rojo
        css_class = "status-good" if status_text.strip().lower() == "all good".lower() else "status-bad"
        output += f"<tr><td>{html.escape(row['service'])}</td><td class='{css_class}'>{html.escape(status_text)}</td></tr>"
    output += "</table>"

    output += """
      <h3>Past Incidents</h3>
      <table class="ticket-table">
        <tr><th>Date</th><th>Detail</th></tr>
    """
    for inc in past_incidents:
        output += f"<tr><td>{html.escape(inc['date'])}</td><td>{html.escape(inc['detail'])}</td></tr>"
    output += "</table></div>"
    return output
