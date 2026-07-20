from pathlib import Path
import re

# Search frontend for other RAG process UI
root = Path("frontend/src")
hits = []
for p in root.rglob("*"):
    if p.suffix not in {".ts", ".tsx", ".js", ".jsx"}:
        continue
    try:
        text = p.read_text(encoding="utf-8")
    except Exception:
        continue
    for i, line in enumerate(text.splitlines(), 1):
        if any(k in line for k in ["Quy trình RAG", "ragTrace", "rag_trace", "showRagTrace"]):
            hits.append(f"{p.as_posix()}:{i}: {line.strip()}")

Path("scripts/_step5_rag_hits.txt").write_text("\n".join(hits), encoding="utf-8")
print(f"hits={len(hits)}")
for h in hits[:80]:
    # ascii-safe
    print(h.encode("ascii", "backslashreplace").decode("ascii"))

# Backend still returns rag_trace?
backend_hits = []
for p in Path("api").rglob("*.py"):
    try:
        text = p.read_text(encoding="utf-8")
    except Exception:
        continue
    if "rag_trace" in text or "ragTrace" in text:
        for i, line in enumerate(text.splitlines(), 1):
            if "rag_trace" in line or "ragTrace" in line:
                backend_hits.append(f"{p.as_posix()}:{i}: {line.strip()}")
print("backend_hits", len(backend_hits))
for h in backend_hits[:40]:
    print(h.encode("ascii", "backslashreplace").decode("ascii"))
