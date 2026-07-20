from pathlib import Path
import re

p = Path("scripts/e2e_step8_regression.py")
text = p.read_text(encoding="utf-8")

# Replace ask function using line-based approach
lines = text.split("\n")
ask_start = None
ask_end = None
for i, line in enumerate(lines):
    if line.startswith("def ask(client"):
        ask_start = i
    elif ask_start is not None and i > ask_start and (line.startswith("def ") or line.startswith("class ")):
        ask_end = i
        break

if ask_start is not None and ask_end is not None:
    # Build new ask function
    new_func = '''def parse_sse_response(raw_text: str) -> dict[str, Any]:
    """Parse SSE stream from /api/search/ask and extract final_answer + metadata."""
    answer = ""
    citations = []
    grounding_status = "unknown"
    domain_mismatch = False
    suggested_domain = None
    rag_trace = None
    procedure_detail = None
    recommended_forms = []
    faqs = []

    for line in raw_text.split("\\n"):
        if not line.startswith("data:"):
            continue
        try:
            evt = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue

        etype = evt.get("type", "")
        content = evt.get("content", "")

        if etype == "final_answer":
            answer = content
        elif etype == "complete":
            if not answer:
                answer = evt.get("final_answer", content)
            if "citations" in evt:
                citations = evt["citations"]
            if "grounding_status" in evt:
                grounding_status = evt["grounding_status"]
            if "domain_mismatch" in evt:
                domain_mismatch = evt["domain_mismatch"]
            if "suggested_domain" in evt:
                suggested_domain = evt["suggested_domain"]
            if "procedure_detail" in evt:
                procedure_detail = evt["procedure_detail"]
            if "recommended_forms" in evt:
                recommended_forms = evt["recommended_forms"]
            if "faqs" in evt:
                faqs = evt["faqs"]
        elif etype == "rag_trace":
            rag_trace = evt.get("trace")
        elif etype == "citations":
            citations = evt.get("citations", [])
        elif etype == "grounding_status":
            grounding_status = evt.get("status", grounding_status)

    return {
        "answer": answer,
        "citations": citations,
        "procedure_detail": procedure_detail,
        "recommended_forms": recommended_forms,
        "faqs": faqs,
        "grounding_status": grounding_status,
        "domain_mismatch": domain_mismatch,
        "suggested_domain": suggested_domain,
        "rag_trace": rag_trace,
    }


def ask(client: httpx.Client, question: str, role: str, domain: str | None = None, offline: bool = True) -> dict[str, Any]:
    payload = {
        "question": question,
        "role": role,
        "offline_mode": offline,
        "offline_model": "qwen2.5:3b",
        "show_rag_trace": False,
    }
    if domain:
        payload["domain"] = domain

    try:
        endpoints = [
            f"{API}/api/search/ask",
            f"{API}/api/search/ask/simple",
        ]
        r = None
        last_err = None
        for ep in endpoints:
            try:
                r = client.post(ep, json=payload, headers=auth(role), timeout=120.0)
                if r.status_code != 404:
                    break
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                r = None

        if r is None:
            return {"error": f"No ask endpoint reachable: {last_err}", "answer": "", "citations": [], "role": role}

        # SSE stream detection
        ct = r.headers.get("content-type", "")
        if "event-stream" in ct or "text/event-stream" in ct:
            parsed = parse_sse_response(r.text)
            parsed["role"] = role
            return parsed

        # Fallback: regular JSON
        try:
            data = r.json()
            return {
                "answer": data.get("answer") or data.get("final_answer") or "",
                "citations": data.get("citations") or [],
                "procedure_detail": data.get("procedure_detail"),
                "recommended_forms": data.get("recommended_forms") or [],
                "faqs": data.get("faqs") or [],
                "grounding_status": data.get("grounding_status"),
                "domain_mismatch": data.get("domain_mismatch"),
                "suggested_domain": data.get("suggested_domain"),
                "rag_trace": data.get("rag_trace"),
                "role": role,
                "raw": data,
            }
        except json.JSONDecodeError:
            return {"error": f"Non-JSON response (status {r.status_code})", "answer": "", "citations": [], "role": role"}

    except Exception as e:
        return {"error": str(e), "answer": "", "citations": [], "role": role}


'''
    lines[ask_start:ask_end] = [new_func]
    text = "\n".join(lines)
    p.write_text(text, encoding="utf-8", newline="\n")
    print(f"OK: replaced ask function (lines {ask_start+1}-{ask_end})")
else:
    print("ERROR: could not find ask function boundaries")

import ast
ast.parse(p.read_text(encoding="utf-8"))
print("syntax OK")
