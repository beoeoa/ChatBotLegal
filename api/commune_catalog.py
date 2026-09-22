"""User-approved commune UI catalog. Does not rewrite corpus metadata."""
from pathlib import Path
import json

def commune_catalog():
    path = Path(__file__).resolve().parents[1] / "frontend/src/lib/data/commune-catalog.json"
    return json.loads(path.read_text(encoding="utf-8"))

def scoped_import_fields(fields):
    allowed = {name for group in commune_catalog() for name in group["field_names"]}
    return [row for row in fields if row.get("name") in allowed]
