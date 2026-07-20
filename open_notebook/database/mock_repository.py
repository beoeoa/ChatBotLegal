"""Mock database for testing without SurrealDB."""
import json
from pathlib import Path
from typing import Any

DB_FILE = Path("J:/ChatBotLegal/test_data/mock_db.json")

def _load_db():
    if DB_FILE.exists():
        with open(DB_FILE) as f:
            return json.load(f)
    return {"sources": [], "candidates": [], "chat_sessions": []}

def _save_db(data):
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)

async def repo_query(query: str, params: dict) -> list:
    """Mock query - returns empty list or sample data."""
    db = _load_db()
    # Return sample empty results for common queries
    return []

async def repo_update(table: str, id: str, data: dict) -> list:
    """Mock update - saves to file."""
    db = _load_db()
    if table not in db:
        db[table] = []
    
    # Add or update
    found = False
    for i, item in enumerate(db[table]):
        if item.get("id") == id:
            db[table][i].update(data)
            found = True
            break
    
    if not found:
        new_item = {"id": id, **data}
        db[table].append(new_item)
    
    _save_db(db)
    return [data]

def ensure_record_id(id_str):
    return id_str
