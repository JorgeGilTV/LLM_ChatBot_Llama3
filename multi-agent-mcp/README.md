🧠 GenDox AI
GenDox AI is a web-based platform that integrates technical ticket analysis, internal documentation search, and AI-powered recommendations. Designed for DevOps, SRE, and support teams, it streamlines troubleshooting workflows by combining multiple tools into a single intelligent interface.

🚀 Key Features
🔍 Read_Itrack: Queries AT&T Itrack tickets, displaying status, summary, description, and latest comments.
📚 Read_Wiki: Searches AT&T Wiki using CQL filters, prioritizing troubleshooting documents.
🤖 How_to_fix: Uses LLaMA 3 to generate structured recommendations based on ticket context.

✅ MCP_Connect: Verifies MCP server connectivity.

🧰 Technologies Used
Python + Flask: Web backend and tool orchestration
FastMCP: Modular execution engine for AI tools
LLaMA 3 (via Ollama): AI model for generating recommendations
JIRA API + Wiki API: Integration with internal systems
HTML + CSS (Dark UI): Modern chatbot-style interface
BeautifulSoup: HTML parsing for ticket analysis

🖥️ Web Interface
Dark theme with gradient header
Sidebar with selectable tools (checkboxes)
Input box and live execution timer
Results displayed in styled cards per tool
“New Chat” button resets input and selections

📦 Installation
Clone the repository:

git clone https://github.com/your-username/gendox-ai.git
cd gendox-ai

Install dependencies:
pip install -r requirements.txt
Set environment variables:
export ITRACK_USER=your_username
export ITRACK_PASSWORD=your_password
export WIKI_TOKEN=your_wiki_token

Start the server:
python app.py

🧪 How to Use
Open http://localhost:5000 in your browser.
Select one or more tools from the sidebar.
Enter a query (ticket ID, keyword, etc.).
Click Send to execute.
View results in separate cards per tool.
Click New Chat to reset the session.

🛠️ Example Queries
CDEX-123456 → Select Read_Itrack to fetch ticket details.
SSL error → Select How_to_fix to get AI-generated suggestions.
Grafana dashboard → Select Read_Wiki to find related documentation.

📁 Project Structure
Code
gendox-ai/
├── app.py               # Flask + MCP server
├── config.py            # API keys and credentials
├── templates/           # HTML templates
├── static/              # CSS and JS assets
├── tools/               # Tool functions (Itrack, Wiki, LLaMA)
├── logs/                # Execution logs
└── README.md            # This file

👤 Author
Developed by Jorge Gil, 
Software Engineering Manager, with expertise in DevOps, operational resilience, and AI-driven tooling for technical teams.