import httpx, sys
sys.stdout.reconfigure(encoding="utf-8")
try:
    r = httpx.get("http://127.0.0.1:5055/api/search/health", timeout=5.0)
    print(f"status={r.status_code}")
    print(r.text[:200])
except Exception as e:
    print(f"ERR: {e}")
