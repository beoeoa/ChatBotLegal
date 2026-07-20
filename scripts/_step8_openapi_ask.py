import httpx, json, sys
sys.stdout.reconfigure(encoding="utf-8")
API="http://127.0.0.1:5055"
headers={"Authorization":"Bearer gfi","X-User-Role":"citizen"}
# Check required body from openapi
r=httpx.get(API+"/openapi.json", timeout=30.0)
schema=r.json()
# Find AskRequest
comps=schema.get("components",{}).get("schemas",{})
for name in comps:
    if "Ask" in name:
        print(name, json.dumps(comps[name], ensure_ascii=False)[:800])
        print("---")
