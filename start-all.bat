@echo off
chcp 65001 >nul
echo =======================================
echo   ChatBotLegal - Start All Services
echo =======================================
echo.

echo [1/4] Starting SurrealDB on port 8000...
start "SurrealDB" cmd /k "cd /d J:\ChatBotLegal && surreal.exe start surrealkv:surreal_data/mydatabase.db --user root --pass root --bind 0.0.0.0:8000"
timeout /t 5 /nobreak >nul

echo [2/4] Starting Backend API on port 5055...
start "Backend API" cmd /k "cd /d J:\ChatBotLegal && set PYTHONIOENCODING=utf-8 && set PYTHONUTF8=1 && set LEGAL_CRAWLER_ENABLED=true && set CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000 && python -m api.main"
timeout /t 10 /nobreak >nul

echo [3/4] Starting Legal Search Service on port 8765...
start "Legal Search" cmd /k "cd /d J:\ChatBotLegal && set LEGAL_EMBED_DEVICE=auto && set LEGAL_DATA_ROOT=J:\ChatBotLegal\release-data\legal && J:\ChatBotLegal\.venv-retrieval-cu126\Scripts\python.exe scripts\legal_search_server.py"
timeout /t 20 /nobreak >nul

echo [4/4] Starting Frontend on port 3000...
start "Frontend" cmd /k "cd /d J:\ChatBotLegal\frontend && npm run dev"
timeout /t 3 /nobreak >nul

echo.
echo =======================================
echo All services starting in new windows:
echo   - SurrealDB:       http://localhost:8000
echo   - Backend API:     http://localhost:5055
echo   - Legal Search:    http://localhost:8765
echo   - Frontend:        http://localhost:3000
echo =======================================
echo.
echo Press any key to close this window...
pause >nul

