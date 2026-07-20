import httpx, json, sys
sys.stdout.reconfigure(encoding="utf-8")
API="http://127.0.0.1:5055"
headers={"Authorization":"Bearer gfi","X-User-Role":"citizen"}
q="Ông nội tôi mất năm 2022 không để lại di chúc, để lại thửa đất tại quận Lê Chân, Hải Phòng. Tôi là cháu nội, em gái tôi đang ở Đức không về được. Muốn sang tên cho em gái thì làm thế nào?"
payload={"question":q,"role":"citizen","offline_mode":True,"offline_model":"qwen2.5:3b","show_rag_trace":False,"domain":"dat_dai_xay_dung"}
r=httpx.post(API+"/api/search/ask",json=payload,headers=headers,timeout=180.0)
print("status", r.status_code)
print("ct", r.headers.get("content-type"))
print(r.text[:1500])
