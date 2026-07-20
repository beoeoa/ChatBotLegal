import httpx, json, sys, time
sys.stdout.reconfigure(encoding="utf-8")
API="http://127.0.0.1:5055"
H={"Authorization":"Bearer gfi","X-User-Role":"citizen"}

# health
try:
    r=httpx.get(API+"/api/search/health", headers=H, timeout=10)
    print("health", r.status_code, r.text[:200])
except Exception as e:
    print("health ERR", e)

# G5 mismatch quick
payload={"question":"Tôi muốn đăng ký kết hôn, cần giấy tờ gì?","role":"officer","offline_mode":True,"offline_model":"qwen2.5:3b","show_rag_trace":False,"domain":"dat_dai_xay_dung"}
r=httpx.post(API+"/api/search/ask", json=payload, headers={"Authorization":"Bearer gfi","X-User-Role":"officer"}, timeout=120)
print("G5 status", r.status_code, r.headers.get("content-type"))
ans=""
for line in r.text.split("\n"):
    if line.startswith("data:"):
        try:
            e=json.loads(line[5:].strip())
        except Exception:
            continue
        if e.get("type") in ("final_answer","complete"):
            ans=e.get("content") or e.get("final_answer") or ans
            print("type", e.get("type"), "keys", list(e.keys()))
print("G5 ans:", ans[:400])

# G1 inheritance
q="Ông nội tôi mất năm 2022 không để lại di chúc, để lại thửa đất tại quận Lê Chân, Hải Phòng. Tôi là cháu nội, em gái tôi đang ở Đức không về được. Muốn sang tên cho em gái thì làm thế nào?"
payload={"question":q,"role":"citizen","offline_mode":True,"offline_model":"qwen2.5:3b","show_rag_trace":False,"domain":"dat_dai_xay_dung"}
r=httpx.post(API+"/api/search/ask", json=payload, headers=H, timeout=180)
print("\nG1 status", r.status_code, r.headers.get("content-type"))
if r.status_code>=400:
    print(r.text[:500])
else:
    ans=""
    for line in r.text.split("\n"):
        if line.startswith("data:"):
            try:
                e=json.loads(line[5:].strip())
            except Exception:
                continue
            if e.get("type") in ("final_answer","complete"):
                ans=e.get("content") or e.get("final_answer") or ans
    print("G1 ans len", len(ans))
    print(ans[:700])
