import subprocess, sys, time, os
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

# Kill existing server
try:
    subprocess.run(["taskkill", "/F", "/IM", "python.exe", "/FI", "WINDOWTITLE eq *uvicorn*"], capture_output=True, timeout=10)
    print("Killed uvicorn")
except Exception as e:
    print(f"Kill error: {e}")

time.sleep(2)

# Start server in background
proc = subprocess.Popen(
    ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "5055", "--reload"],
    cwd=str(Path(".").resolve()),
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    env={**dict(os.environ), "OLLAMA_HOST": "http://127.0.0.1:11434"},
)
print(f"Started server PID={proc.pid}")

# Wait for startup
for i in range(30):
    time.sleep(2)
    try:
        import httpx
        r = httpx.get("http://127.0.0.1:5055/api/search/health", timeout=5.0)
        if r.status_code == 200:
            print(f"Server ready after {i+1} attempts")
            break
    except Exception:
        pass
else:
    print("Server failed to start")
    proc.kill()
