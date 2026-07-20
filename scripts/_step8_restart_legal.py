import subprocess, sys, time, os
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

# Kill legal search server PID 8564
subprocess.run(["taskkill", "/F", "/PID", "8564"], capture_output=True)
print("Killed legal search server PID 8564")

time.sleep(3)

# Restart it
proc = subprocess.Popen(
    ["python", "scripts/legal_search_server.py"],
    cwd=str(Path(".").resolve()),
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    env={**dict(os.environ), "OLLAMA_HOST": "http://127.0.0.1:11434"},
)
print(f"Restarted legal search server PID={proc.pid}")

# Wait for startup
for i in range(30):
    time.sleep(2)
    try:
        import httpx
        r = httpx.get("http://127.0.0.1:8765/health", timeout=5.0)
        if r.status_code == 200:
            print(f"Legal search ready after {i+1} attempts")
            break
    except Exception:
        pass
else:
    print("Legal search failed to start")
    proc.kill()
