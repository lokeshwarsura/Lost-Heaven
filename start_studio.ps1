# ClipShip AI Studio - PowerShell Launcher
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "     Launching ClipShip AI Studio (Hybrid Pro)     " -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan

# 1. Start Backend in separate process
$backendPy = Join-Path $root "backend\venv\Scripts\python.exe"
if (-not (Test-Path $backendPy)) {
    Write-Error "Virtual environment not found at $backendPy"
}

Write-Host "[1/3] Starting FastAPI Backend on http://localhost:8000 ..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root'; & '$backendPy' -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir backend"

# 2. Start Frontend in separate process
Write-Host "[2/3] Starting Vite Frontend on http://localhost:5173 ..." -ForegroundColor Green
$frontendDir = Join-Path $root "frontend"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$frontendDir'; npm run dev -- --host 0.0.0.0 --port 5173"

# 3. Wait and open default browser
Start-Sleep -Seconds 3
Write-Host "[3/3] Opening Studio in default browser..." -ForegroundColor Yellow
Start-Process "http://localhost:5173"

Write-Host "`nStudio is now running at http://localhost:5173!" -ForegroundColor Cyan
