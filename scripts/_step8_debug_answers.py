import httpx, json, sys
sys.stdout.reconfigure(encoding="utf-8")
API="http://127.0.0.1:5055"

def ask(q, role, domain=None):
    headers={"Authorization":"Bearer gfi","X-User-Role":role}
    payload={"question":q,"role":role,"offline_mode":True,"offline_model":"qwen2.5:3b","show_rag_trace":False}
    if domain: payload["domain"]=domain
    r=httpx.post(API+"/api/search/ask",json=payload,headers=headers,timeout=180.0)
    print("STATUS", r.status_code, "CT", r.headers.get("content-type"))
    events=[]
    for line in r.text.split("\n"):
        if line.startswith("data:"):
            try:
                events.append(json.loads(line[5:].strip()))
            except Exception:
                events.append({"raw":line[:200]})
    types=[e.get("type") for e in events if isinstance(e, dict)]
    print("EVENT_TYPES", types)
    # print final/complete
    for e in events:
        if not isinstance(e, dict):
            continue
        if e.get("type") in ("final_answer","complete","domain_mismatch","error","insufficient_evidence"):
            print("EVENT", e.get("type"))
            if e.get("type")=="final_answer":
                print((e.get("content") or "")[:500])
            elif e.get("type")=="complete":
                print("keys", list(e.keys()))
                print((e.get("final_answer") or e.get("content") or "")[:500])
            else:
                print(json.dumps(e, ensure_ascii=False)[:500])
    return events

print("=== G1 inheritance citizen ===")
ask("Ông nội tôi mất năm 2022 không để lại di chúc, để lại thửa đất tại quận Lê Chân, Hải Phòng. Tôi là cháu nội, em gái tôi đang ở Đức không về được. Muốn sang tên cho em gái thì làm thế nào?", "citizen", "dat_dai_xay_dung")
print("\n=== G5 officer mismatch ===")
ask("Tôi muốn đăng ký kết hôn, cần giấy tờ gì?", "officer", "dat_dai_xay_dung")
