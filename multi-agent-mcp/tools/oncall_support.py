import os, requests, datetime, html
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

def confluence_oncall_today(query: str = None) -> str:
    print("🔎 Who is oncall this month?:", query if query else "All teams")
    """
    Reads the fixed Confluence page (On Call Support During Holidays) and returns
    who is on call for each day of the current month. If query is empty, shows all teams.
    """
    email = os.getenv("ATLASSIAN_EMAIL")
    token = os.getenv("CONFLUENCE_TOKEN")
    if not email or not token:
        return "<p>Error: ATLASSIAN_EMAIL and CONFLUENCE_TOKEN must be set in the environment.</p>"

    auth = (email, token)
    base_url = "https://arlo.atlassian.net/wiki"
    page_id = "754581728"

    # Fetch body as storage (HTML)
    url = f"{base_url}/rest/api/content/{page_id}?expand=body.storage"
    response = requests.get(url, auth=auth)
    if response.status_code != 200:
        return f"<p>Error {response.status_code}: {response.reason}</p>"

    data = response.json()
    html_content = data["body"]["storage"]["value"]

    # Parse HTML with BeautifulSoup
    soup = BeautifulSoup(html_content, "html.parser")

    # All dates in the current month
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

    # Table headers
    headers = [th.get_text(strip=True) for th in soup.find_all("th")]

    output = f"<h2>👩‍💻 Oncall schedule for {today.strftime('%B %Y')} ({query if query else 'All teams'})</h2>"

    for col_header in month_headers:
        if col_header not in headers:
            continue

        col_index = headers.index(col_header)
        oncall_day = []

        for row in soup.find_all("tr"):
            cells = [c.get_text(strip=True) for c in row.find_all("td")]
            if len(cells) > col_index:
                status = cells[col_index]
                if status == "✅":
                    name = cells[0]
                    team = cells[1] if len(cells) > 1 else ""
                    contact = cells[2] if len(cells) > 2 else ""
                    if not query or query.lower() in team.lower():
                        oncall_day.append({"name": name, "team": team, "contact": contact})

        output += f"<h3>{col_header}</h3>"
        if not oncall_day:
            output += f"<p>No on-call resources for {col_header} in {query if query else 'All teams'}</p>"
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
