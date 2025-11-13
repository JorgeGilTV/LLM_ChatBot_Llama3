import os
from dotenv import load_dotenv
 
load_dotenv()
 
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")
FIRECRAWL_KEY = os.getenv("FIRECRAWL_KEY")
SOURCEGRAPH_TOKEN = os.getenv("SOURCEGRAPH_TOKEN")
MCP_API_KEY = os.getenv("MCP_API_KEY")
 
# LLaMA y Ollama
OLLAMA_MODEL = "llama3"
 
# JIRA
JIRA_URL = "https://jira-instance.com"
JIRA_USERNAME = "usuario"
JIRA_PASSWORD = "token"
 
# Grafana
GRAFANA_API_KEY = "grafana_api_key"
GRAFANA_PANEL_UID = "abc123"
 
# Wiki AT&T
WIKI_USERNAME = "jg493a"
WIKI_PASSWORD = ""
 
# Email
SMTP_SERVER = "smtp.amdocs.com"
SMTP_PORT = 587
EMAIL_USER = "usuario"
EMAIL_PASS = "contraseña"
EMAIL_FROM = "monitorai@amdocs.com"
EMAIL_TO = "equipoAI_generativo@amdocs.com"
