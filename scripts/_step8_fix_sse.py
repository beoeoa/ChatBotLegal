from pathlib import Path
import re

p = Path("scripts/e2e_step8_regression.py")
text = p.read_text(encoding="utf-8")

# Replace the ask() function with SSE-aware version
old_ask = '''def ask(client: httpx.Client, question: str, role: str, domain: str | None = None, offline: bool = True) -> dict[str, Any]:
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
        # Try common ask endpoints (search router mounted at /api prefix)
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
    except Exception as e:
        return {"error": str(e), "answer": "", "citations": [], "role": role}'''

new_ask = '''def parse_sse_response(raw_text: str) -> dict[str, Any]:
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
            # complete event may contain final_answer directly
            if not answer:
                answer = evt.get("final_answer", content)
            # May also contain other fields
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
            return {"error": f"Non-JSON response (status {r.status_code})", "answer": "", "citations": [], "role": role}
            
    except Exception as e:
        return {"error": str(e), "answer": "", "citations": [], "role": role}'''

if old_ask in text:
    text = text.replace(old_ask, new_ask, 1)
    print("OK: replaced ask() with SSE-aware version")
else:
    print("WARN: old ask() not found exactly")
    # Try finding it by key markers
    if "def ask(client:" in text:
        print("Found def ask(client:) but exact match failed")

p.write_text(text, encoding="utf-8", newline="\n")
import ast
ast.parse(p.read_text(encoding="utf-8"))
print("syntax OK")
