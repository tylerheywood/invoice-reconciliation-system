@echo off

:: Activate venv if present
if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
)

echo Installing requirements...
pip install -r docs\requirements.txt --quiet
if not exist input mkdir input

echo Starting Invoice Reconciliation System on http://localhost:5000

:: Start Flask in the background
start /b python app.py

:: Wait for server to be ready before opening browser
:wait_loop
timeout /t 1 /nobreak >nul
curl -s -o nul http://localhost:5000 2>nul && goto :server_ready
goto :wait_loop

:server_ready
start http://localhost:5000

:: Keep window open so Flask stays running
echo Server running. Close this window to stop.
pause >nul
