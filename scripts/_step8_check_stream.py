import httpx, json, sys
sys.stdout.reconfigure(encoding="utf-8")
API="http://127.0.0.1:5055"
headers={"Authorization":"Bearer gfi","X-User-Role":"citizen"}
payload={"question":"Tôi muốn đăng ký kết hôn cần giấy tờ gì?","role":"citizen","offline_mode":True,"offline_model":"qwen2.5:3b","show_rag_trace":False}
# Check content-type and raw bytes
r=httpx.post(API+"/api/search/ask",json=payload,headers=headers,timeout=120.0)
print(f"status={r.status_code}")
print(f"content-type={r.headers.get('content-type','?')}")
print(f"raw_len={len(r.content)}")
print(f"raw_first_500={r.content[:500]}")
print(f"text_first_500={r.text[:500]}")
# Check if it's SSE
if b'data:' in r.content or b': ' in r.content:
    print("Looks like SSE/streaming")
    # Parse SSE
    for line in r.text.split('\n'):
        if line.startswith('data:'):
            try:
                d=json.loads(line[5:].strip())
                print("SSE event:", json.dumps(d, ensure_ascii=False)[:500])
            except:
                print("SSE raw:", line[:200])
