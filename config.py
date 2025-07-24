import os
from dotenv import load_dotenv

load_dotenv()

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")
FIRECRAWL_KEY = os.getenv("FIRECRAWL_KEY")
SOURCEGRAPH_TOKEN = os.getenv("SOURCEGRAPH_TOKEN")