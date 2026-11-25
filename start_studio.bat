@echo off
title ClipShip AI Studio Launcher
echo ===================================================
echo     Launching ClipShip AI Studio (Hybrid Pro)
echo ===================================================
echo.

cd /d "%~dp0"

:: 1. Check Python virtual environment
if not exist "backend\venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found in backend\venv.
    echo Please run: python -m venv backend\venv
    pause
    exit /b 1
)

:: 2. Check Node modules
if not exist "frontend\node_modules" (
    echo [INFO] Installing frontend dependencies...
    cd frontend && npm install && cd ..
)

:: 3. Launch Backend in new window
echo [INFO] Starting FastAPI Backend on http://localhost:8000 ...
start "ClipShip Backend (FastAPI)" cmd /k "cd /d "%~dp0" && backend\venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir backend"

:: 4. Launch Frontend in new window
echo [INFO] Starting Vite Frontend on http://localhost:5173 ...
start "ClipShip Frontend (Vite)" cmd /k "cd /d "%~dp0\frontend" && npm run dev -- --host 0.0.0.0 --port 5173"

:: 5. Open browser
timeout /t 3 /nobreak >nul
echo [INFO] Opening ClipShip Studio in browser: http://localhost:5173 ...
start http://localhost:5173

echo.
echo ===================================================
echo   Studio is running! Keep the windows open.
echo   Press any key to close this launcher window.
echo ===================================================
pause >nul
