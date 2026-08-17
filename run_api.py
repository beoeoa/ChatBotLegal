"""Simple API runner with better process handling."""
import os
import sys
from pathlib import Path

# Load .env file first so environment variables like DISABLE_AUTH, LEGAL_SEARCH_URL are available
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
except ImportError:
    pass

# Disable multiprocessing issues on Windows
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ['LEGAL_CRAWLER_ENABLED'] = 'true'
os.environ['CORS_ORIGINS'] = '*'
os.environ['SURREAL_USER'] = 'root'
os.environ['SURREAL_PASSWORD'] = 'root'
os.environ['SURREAL_NAMESPACE'] = 'open_notebook'
os.environ['SURREAL_DATABASE'] = 'open_notebook'
os.environ['OPEN_NOTEBOOK_ENCRYPTION_KEY'] = 'change-me-to-a-secret-string'

# Run uvicorn directly
import uvicorn
from api.main import app

if __name__ == "__main__":
    print("Starting ChatBotLegal API on http://localhost:5055")
    print("CORS: http://localhost:3000,http://127.0.0.1:3000")
    uvicorn.run(app, host="127.0.0.1", port=5055, log_level="info")
