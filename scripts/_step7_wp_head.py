import sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
wp = Path("api/routers/ward_procedures.py").read_text(encoding="utf-8")
# print constants and review endpoint
for i, line in enumerate(wp.splitlines(), 1):
    if i <= 120 or any(k in line for k in ["PRIORITY", "OFFICIAL", "CLASSIFIED", "FORMS_", "PROJECT_ROOT", "review", "approved", "File(", "UploadFile"]):
        if i <= 120 or any(k in line for k in ["PRIORITY", "OFFICIAL", "CLASSIFIED", "FORMS_", "PROJECT_ROOT", "review_status", "approved", "UploadFile", "File(", "prefix="]):
            print(f"{i}: {line[:180]}")
