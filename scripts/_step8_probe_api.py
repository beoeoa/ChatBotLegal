import httpx
import sys
sys.stdout.reconfigure(encoding="utf-8")

API = "http://127.0.0.1:5055"
headers = {"Authorization": "Bearer gfi", "X-User-Role": "citizen"}

# Probe various endpoints
endpoints = [
    "/api/search/ask",
    "/api/search/ask/simple",
    "/search/ask",
    "/search/ask/simple",
    "/api/config",
    "/config",
    "/api/search/health",
    "/search/health",
    "/docs",
    "/openapi.json",
]
with httpx.Client(timeout=10.0) as client:
    for ep in endpoints:
        try:
            if "ask" in ep:
                r = client.post(API + ep, json={"question": "test", "role": "citizen", "offline_mode": True}, headers=headers)
            else:
                r = client.get(API + ep, headers=headers)
            print(f"{r.status_code} {ep} {r.text[:100]}")
        except Exception as e:
            print(f"ERR {ep}: {e}")
