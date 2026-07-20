import httpx, json, sys
sys.stdout.reconfigure(encoding="utf-8")
API="http://127.0.0.1:5055"

def ask(q, role, domain=None):
    headers={"Authorization":"Bearer gfi","X-User-Role":role}
    payload={"question":q,"role":role,"offline_mode":True,"offline_model":"qwen2.5:3b","show_rag_trace":False}
    if domain: payload["domain"]=domain
    r=httpx.post(API+"/api/search/ask",json=payload,headers=headers,timeout=180)
    ans=""
    for line in r.text.split("\n"):
        if line.startswith("data:"):
            try:
                e=json.loads(line[5:].strip())
            except Exception:
                continue
            if e.get("type") in ("final_answer","complete"):
                ans=e.get("content") or e.get("final_answer") or ans
    return ans

# G1 inheritance citizen
print("=== G1 inheritance citizen ===")
ans=ask("Ông nội tôi mất năm 2022 không để lại di chúc, để lại thửa đất tại quận Lê Chân, Hải Phòng. Tôi là cháu nội, em gái tôi đang ở Đức không về được. Muốn sang tên cho em gái thì làm thế nào?", "citizen", "dat_dai_xay_dung")
print(ans[:1500])

# G5 officer mismatch
print("\n=== G5 officer mismatch ===")
ans=ask("Tôi muốn đăng ký kết hôn, cần giấy tờ gì?", "officer", "dat_dai_xay_dung")
print(ans[:1500])
