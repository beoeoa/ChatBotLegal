import sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

wp = Path("api/routers/ward_procedures.py").read_text(encoding="utf-8")
# Extract download_official_form_source and download_seed and soft errors
for name in ["download_official_form_source", "download_seed_procedure_form", "download_form_template", "_copy_candidate_to_priority", "upload"]:
    idx = wp.find(f"async def {name}" if not name.startswith("_") else f"def {name}")
    if idx < 0:
        idx = wp.find(name)
    print("====", name, "at", idx)
    if idx >= 0:
        snippet = wp[idx:idx+1800]
        Path(f"scripts/_snip_{name}.txt").write_text(snippet, encoding="utf-8")
        print(snippet[:1200])
        print("...\n")

sr = Path("frontend/src/components/search/StreamingResponse.tsx").read_text(encoding="utf-8")
idx = sr.find("const handleDownloadForm")
if idx < 0:
    idx = sr.find("handleDownloadForm =")
print("==== handleDownloadForm ====")
print(sr[idx:idx+2200])
