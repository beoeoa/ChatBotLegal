import httpx, json, sys
sys.stdout.reconfigure(encoding="utf-8")
API="http://127.0.0.1:5055"
headers={"Authorization":"Bearer gfi","X-User-Role":"citizen"}
# Quick test with a simple question
payload={"question":"Tôi muốn đăng ký kết hôn cần giấy tờ gì?","role":"citizen","offline_mode":True,"offline_model":"qwen2.5:3b","show_rag_trace":False}
try:
    r=httpx.post(API+"/api/search/ask",json=payload,headers=headers,timeout=120.0)
    print(f"status={r.status_code}")
    data=r.json()
    ans=data.get("answer","")
    print(f"answer_len={len(ans)}")
    print(f"first_200={ans[:200]}")
    print(f"citations={data.get('citations')}")
    print(f"grounding_status={data.get('grounding_status')}")
except Exception as e:
    print(f"ERR: {e}")
