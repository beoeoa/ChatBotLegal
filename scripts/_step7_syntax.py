from pathlib import Path
import ast
# syntax check
for p in ["api/routers/ward_procedures.py", "scripts/crawl_forms_from_dvc.py"]:
    try:
        ast.parse(Path(p).read_text(encoding="utf-8"))
        print("SYNTAX OK", p)
    except SyntaxError as e:
        print("SYNTAX ERR", p, e)

# ensure frontend soft download already no-throw
sr = Path("frontend/src/components/search/StreamingResponse.tsx").read_text(encoding="utf-8")
print("toast.error soft:", "toast.error" in sr)
print("canDownload guard:", "if (!canDownload)" in sr)
print("throw Error download:", "throw new Error" in sr and "Download failed" in sr)
