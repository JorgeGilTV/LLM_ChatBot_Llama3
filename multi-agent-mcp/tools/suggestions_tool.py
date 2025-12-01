from bs4 import BeautifulSoup
from tools.tickets_tool import read_tickets
from tools.wiki_tool import wiki_search
from tools.gemini_tool import ask_gemini

def AI_suggestions(query: str) -> str:
    html_tickets = read_tickets(query)
    soup = BeautifulSoup(html_tickets, "html.parser")
    rows = soup.select("table.ticket-table tr")[1:]
    resumen_tickets = []
    for row in rows:
        cols = row.find_all("td")
        if len(cols) >= 5:
            ticket_id = cols[0].text.strip()
            status = cols[1].text.strip()
            summary = cols[2].text.strip()
            description = cols[3].text.strip()
            last_comment = cols[4].text.strip()
            # ✅ only tickets In Progress, To Do, Retest
            if status.lower() in ["in progress", "to do", "retest"]:
                resumen = f"{ticket_id}\n{status}\n{summary}\n{last_comment}"
                resumen_tickets.append(resumen)
            # ✅ for Accepted, include last 2 comments
            elif status.lower() == "accepted":
                comments = row.find_all("td")[4].text.strip().split("\n")
                last_two = comments[-2:] if len(comments) >= 2 else comments
                resumen = f"{ticket_id}\n{status}\n{summary}\nLast 2 Comments: {'\n'.join(last_two)}"
                resumen_tickets.append(resumen)
    texto_tickets = "\n".join(resumen_tickets)
    prompt = f"""
    You are a senior DevOps service engineer specializing in troubleshooting and ticket analysis.
    Your task is to generate structured recommendations to resolve each issue based on the provided tickets.
    ⚠️ Rules:
    - Only include tickets with status "In Progress", "To Do", or "Retest" for recommendations.
    - For grouped tickets include tickets in "Accepted" status , and list the last 2 comments available.
    - Always use HTML tags exactly as shown in the format below.
    - Do not use Markdown.
    - Do not collapse fields into single lines.
    - Do not add commentary, explanations, or text outside the required structure.
    - Include only the valid suggestions but always inside <ul><li>.
    - Group similar tickets together by technical context (e.g., SSL errors, ELK cleanup).
    Each ticket must follow this exact block:
    <div style="border:1px solid #ccc; padding:1rem; margin-bottom:1rem; border-radius:8px; background-color:#fdfdfd;">
    <h3>🎫 <strong>Ticket ID:</strong> CDEX-xxxxx</h3>
    <p><strong>Status:</strong> In Progress</p>
    <p><strong>Summary:</strong> Short description of the issue</p>
    <p><strong>Last comments:</strong> Last Comments</p>
    <p><strong>Ticket URL:</strong> <a href="https://itrtt.com/projects/CDEX/issues/CDEX-xxxxxOpen</a></p>
    <p><strong>Recommendations:</strong></p>
    <ul>
    <li>First recommendation</li>
    <li>Second recommendation</li>
    <li>Third recommendation</li>
    </ul>
    </div>
    Before listing individual tickets, include a section for grouped tickets:
    <h2>🔗 Similar Tickets (Grouped)</h2>
    <p><strong>Ticket Group:</strong> Short description</p>
    <ul>
    <li>CDEX-xxxxx: Summary</li>
    <p><strong>Last 2 Comments:</strong></p>
    <ul>
    <li>Comment 1</li>
    <li>Comment 2</li>
    </ul>
    </ul>
    <p><strong>Recommendations:</strong></p>
    <ul>
    <li>Group recommendation 1</li>
    <li>Group recommendation 2</li>
    <li>Group recommendation 3</li>
    </ul>
    At the end, include a section for external documentation links suggested by GenAI related to the applications or technologies mentioned in the tickets.
    Only use official documentation sources (Microsoft, Elastic, Kubernetes, etc.).
    Query context: {query}
    Tickets:
    {texto_tickets}
    """
    raw_response = ask_gemini(prompt, ["How_to_fix"])
    wiki_html = wiki_search(query[:20])
    raw_response += f"""
    <p><strong>Related Wiki Pages:</strong></p>
    {wiki_html}
    </div>
    """