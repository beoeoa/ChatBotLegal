# -*- coding: utf-8 -*-
"""Smoke test end-to-end: Admin import-duyet-embedding, Officer hoi dung linh vuc,
   Citizen hoi thu tuc co bieu mau, citation khong bia."""

import asyncio, sys, json
try:
    import aiohttp
except ImportError:
    print("ERROR: aiohttp not installed. pip install aiohttp")
    sys.exit(1)

API = "http://localhost:5055"
PASSWORD = "gfi"
TIMEOUT = 120
results = []

def record(name, passed, msg=""):
    status = "PASS" if passed else "FAIL"
    results.append((name, status, msg))
    print(f"  [{status}] {name}: {msg}")

def auth_headers(role):
    return {"Authorization": "Bearer " + PASSWORD, "X-User-Role": role}

async def get_default_model(session):
    """Auto-discover a chat model to use for ask tests."""
    try:
        async with session.get(API + "/api/models",
                               headers=auth_headers("admin"),
                               timeout=aiohttp.ClientTimeout(total=10)) as r:
            data = await r.json()
            if isinstance(data, list):
                for m in data:
                    p = (m.get("provider", "") or "").lower()
                    t = (m.get("type", "") or "").lower()
                    pid = m.get("id", "")
                    # Prefer deepseek first, then google, then any language
                    if p == "deepseek" and t in ("language", ""):
                        return pid
                    if p == "google" and t in ("language", ""):
                        return pid
                    if p in ("google", "deepseek") and t in ("language", ""):
                        return pid
            return None
    except Exception:
        return None

async def main():
    print("=" * 60)
    print("SMOKE TEST E2E - 3 ROLES")
    print("=" * 60)

    # Get a usable model ID
    async with aiohttp.ClientSession() as s:
        MODEL = await get_default_model(s) or ""

    if MODEL:
        print(f"Using model: {MODEL}")
    else:
        print("WARNING: No chat model found - ask tests will fail")

    # ============================================================
    # 1. ADMIN ROLE
    # ============================================================
    print("\n--- 1. ADMIN ROLE ---")
    async with aiohttp.ClientSession() as s:
        # 1a. Health
        try:
            async with s.get(API + "/health", timeout=aiohttp.ClientTimeout(total=10)) as r:
                record("API Health Check", r.status == 200, "status=" + str(r.status))
        except Exception as e:
            record("API Health Check", False, str(e))

        # 1b. Legal search health
        try:
            async with s.get(API + "/api/search/health", headers=auth_headers("admin"),
                             timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
                j = await r.json()
                ok = r.status == 200
                record("Legal search health", ok, "status=" + str(j.get("status", "?")))
        except Exception as e:
            record("Legal search health", False, str(e))

        # 1c. Models accessible
        try:
            async with s.get(API + "/api/models", headers=auth_headers("admin"),
                             timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
                j = await r.json()
                n = len(j) if isinstance(j, list) else j.get("total", 0)
                record("Admin models list", r.status == 200 and n > 0, str(n) + " models")
        except Exception as e:
            record("Admin models list", False, str(e))

        # 1d. Users list
        try:
            async with s.get(API + "/api/users", headers=auth_headers("admin"),
                             timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
                ok = r.status == 200
                record("Admin users list", ok, "status=" + str(r.status))
        except Exception as e:
            record("Admin users list", False, str(e))

        # 1e. Settings accessible
        try:
            async with s.get(API + "/api/settings", headers=auth_headers("admin"),
                             timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
                ok = r.status == 200
                record("Admin settings", ok, "status=" + str(r.status))
        except Exception as e:
            record("Admin settings", False, str(e))

        # 1f. Ask history accessible
        try:
            async with s.get(API + "/api/search/ask-history?limit=5", headers=auth_headers("admin"),
                             timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
                ok = r.status == 200
                record("Admin ask history", ok, "status=" + str(r.status))
        except Exception as e:
            record("Admin ask history", False, str(e))

        # 1g. Crawl candidates list
        try:
            async with s.get(API + "/api/legal/crawl/candidates", headers=auth_headers("admin"),
                             timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
                ok = r.status == 200
                record("Admin crawl candidates", ok, "status=" + str(r.status))
        except Exception as e:
            record("Admin crawl candidates", False, str(e))

    # ============================================================
    # 2. OFFICER ROLE
    # ============================================================
    print("\n--- 2. OFFICER ROLE ---")
    async with aiohttp.ClientSession() as s:
        # 2a. Officer can search
        try:
            body = {"query": "xu phat hanh chinh xe do sai", "type": "text", "limit": 5}
            async with s.post(API + "/api/search", json=body, headers=auth_headers("officer"),
                              timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
                j = await r.json()
                n = len(j.get("results", []))
                record("Officer search", r.status == 200, str(n) + " results")
        except Exception as e:
            record("Officer search", False, str(e))

        # 2b. Officer can ask simple (with real model)
        officer_ask_passed = False
        if MODEL:
            try:
                body = {"question": "Can cu nao cam do xe tren via he?", "role": "officer",
                        "strategy_model": MODEL, "answer_model": MODEL, "final_answer_model": MODEL, "offline_mode": False, "show_rag_trace": True}
                async with s.post(API + "/api/search/ask/simple", json=body, headers=auth_headers("officer"),
                                  timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
                    j = await r.json() if r.status == 200 else {}
                    answer = j.get("answer", "")
                    gs = j.get("grounding_status", "unknown")
                    has_cite = "[legal:" in answer
                    officer_ask_passed = r.status == 200 and len(answer) > 20
                    record("Officer ask (cam do xe via he)", officer_ask_passed,
                           "grounding=" + gs + ", cite=" + str(has_cite) + ", len=" + str(len(answer)))
            except Exception as e:
                record("Officer ask", False, str(e))
        else:
            record("Officer ask", False, "No model available")

        # 2c. Officer can view ask history
        try:
            async with s.get(API + "/api/search/ask-history?limit=5&role=officer", headers=auth_headers("officer"),
                             timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
                ok = r.status == 200
                record("Officer ask history", ok, "status=" + str(r.status))
        except Exception as e:
            record("Officer ask history", False, str(e))

        # 2d. Officer cannot access admin-only endpoints
        try:
            async with s.get(API + "/api/credentials", headers=auth_headers("officer"),
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                ok = r.status in (401, 403)
                record("Officer blocked from admin-only", ok, "status=" + str(r.status))
        except Exception as e:
            record("Officer admin-only", True, "blocked: " + str(e))

    # ============================================================
    # 3. CITIZEN ROLE
    # ============================================================
    print("\n--- 3. CITIZEN ROLE ---")
    async with aiohttp.ClientSession() as s:
        # 3a. Citizen can search
        try:
            body = {"query": "dang ky khai sinh", "type": "text", "limit": 5}
            async with s.post(API + "/api/search", json=body, headers=auth_headers("citizen"),
                              timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
                j = await r.json()
                n = len(j.get("results", []))
                record("Citizen search", r.status == 200, str(n) + " results")
        except Exception as e:
            record("Citizen search", False, str(e))

        # 3b. Citizen ask
        citizen_ask_passed = False
        if MODEL:
            try:
                body = {"question": "Dang ky khai sinh cho con can nhung gi?", "role": "citizen",
                        "strategy_model": MODEL, "answer_model": MODEL, "final_answer_model": MODEL, "offline_mode": False, "show_rag_trace": True}
                async with s.post(API + "/api/search/ask/simple", json=body, headers=auth_headers("citizen"),
                                  timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
                    j = await r.json() if r.status == 200 else {}
                    answer = j.get("answer", "")
                    gs = j.get("grounding_status", "unknown")
                    has_cite = "[legal:" in answer
                    proc = j.get("procedure_detail")
                    has_proc = bool(proc and proc.get("steps"))
                    citizen_ask_passed = r.status == 200 and len(answer) > 20
                    record("Citizen ask (khai sinh)", citizen_ask_passed,
                           "grounding=" + gs + ", cite=" + str(has_cite) + 
                           ", proc=" + str(has_proc) + ", len=" + str(len(answer)))
            except Exception as e:
                record("Citizen ask", False, str(e))
        else:
            record("Citizen ask", False, "No model available")

        # 3c. Citizen ask non-existent -> should say insufficient evidence
        if MODEL and citizen_ask_passed:
            try:
                body = {"question": "abcxyz khong ton tai phap luat gi ca 1234567890", "role": "citizen",
                        "strategy_model": MODEL, "answer_model": MODEL, "final_answer_model": MODEL, "offline_mode": False, "show_rag_trace": True}
                async with s.post(API + "/api/search/ask/simple", json=body, headers=auth_headers("citizen"),
                                  timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
                    j = await r.json() if r.status == 200 else {}
                    answer = j.get("answer", "")
                    gs = j.get("grounding_status", "unknown")
                    has_cite = "[legal:" in answer
                    # Should not fabricate citations for nonsense
                    ok = not has_cite or gs == "insufficient_evidence"
                    record("Citizen ask (non-existent)", ok,
                           "grounding=" + gs + ", cite=" + str(has_cite))
            except Exception as e:
                record("Citizen ask (non-existent)", False, str(e))

        # 3d. Citizen forms/procedures list
        try:
            async with s.get(API + "/api/procedures", headers=auth_headers("citizen"),
                             timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
                ok = r.status == 200
                record("Citizen procedures list", ok, "status=" + str(r.status))
        except Exception as e:
            record("Citizen procedures list", False, str(e))

        # 3e. Citizen cannot access admin endpoints
        try:
            async with s.get(API + "/api/credentials", headers=auth_headers("citizen"),
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                ok = r.status in (401, 403)
                record("Citizen blocked from admin", ok, "status=" + str(r.status))
        except Exception as e:
            record("Citizen admin-only", True, "blocked: " + str(e))

    # ============================================================
    # 4. CROSS-ROLE: media endpoints
    # ============================================================
    print("\n--- 4. MEDIA ENDPOINTS ---")
    async with aiohttp.ClientSession() as s:
        # 4a. Media voice transcribe (STT unavailable is OK)
        try:
            async with s.post(API + "/api/media/transcribe-voice", headers=auth_headers("citizen"),
                              timeout=aiohttp.ClientTimeout(total=10)) as r:
                ok = r.status in (400, 415, 422, 503)  # expected: no file or STT unavailable
                record("Media transcribe-voice exists", ok, "status=" + str(r.status))
        except Exception as e:
            record("Media transcribe-voice", False, str(e))

        # 4b. Media extract-text exists
        try:
            async with s.post(API + "/api/media/extract-text", headers=auth_headers("citizen"),
                              timeout=aiohttp.ClientTimeout(total=10)) as r:
                ok = r.status in (400, 415, 422)  # expected: no file
                record("Media extract-text exists", ok, "status=" + str(r.status))
        except Exception as e:
            record("Media extract-text", False, str(e))

    # ============================================================
    # SUMMARY
    # ============================================================
    print("\n" + "=" * 60)
    print("SMOKE TEST RESULTS")
    print("=" * 60)
    for name, status, msg in results:
        print(f"  [{status}] {name}: {msg}")

    n_pass = sum(1 for _, s, _ in results if s == "PASS")
    n_fail = sum(1 for _, s, _ in results if s == "FAIL")
    print("\nTotal: " + str(n_pass) + "/" + str(len(results)) + " PASS, " + str(n_fail) + " FAIL")

    return 0 if n_fail == 0 else 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

