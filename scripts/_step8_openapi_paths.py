import httpx, json, sys
sys.stdout.reconfigure(encoding="utf-8")
API="http://127.0.0.1:5055"
headers={"Authorization":"Bearer gfi","X-User-Role":"citizen"}
# Get openapi paths related to ask/search
r=httpx.get(API+"/openapi.json", timeout=30.0)
paths=r.json().get("paths",{})
for p in sorted(paths):
    if "ask" in p or "search" in p:
        methods=",".join(paths[p].keys())
        print(methods, p)
