from pathlib import Path

files = {
    "streaming": Path("frontend/src/components/search/StreamingResponse.tsx"),
    "page": Path("frontend/src/app/(dashboard)/search/page.tsx"),
}
for name, p in files.items():
    text = p.read_text(encoding="utf-8")
    print(f"==== {name} ====")
    keys = [
        "role === 'admin'",
        "showRagTrace",
        "ragTrace && role === 'admin' && showRagTrace",
        "ragTrace && role === 'admin'",
        "ragTrace && (",
        "Quy trinh RAG",
        "Quy trình RAG",
        "role={role}",
        "showRagTrace={showRagTrace}",
        "role?: 'citizen' | 'officer' | 'admin'",
        "showRagTrace?: boolean",
        "role = 'citizen'",
        "showRagTrace = false",
    ]
    for k in keys:
        print(f"  {('OK' if k in text else 'MISS')}: {k}")

# Print exact relevant snippets with line numbers to file to avoid console encoding issues
out = []
for name, p in files.items():
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    out.append(f"==== {name} ====")
    for i, line in enumerate(lines, 1):
        if any(k in line for k in ["ragTrace", "showRagTrace", "role", "Quy tr", "StreamingResponse", "show-rag-trace"]):
            if any(k in line for k in ["ragTrace", "showRagTrace", "role", "Quy", "StreamingResponse", "show-rag-trace", "admin"]):
                out.append(f"{i}: {line}")
Path("scripts/_step5_verify_snippets.txt").write_text("\n".join(out), encoding="utf-8")
print("Wrote scripts/_step5_verify_snippets.txt")
