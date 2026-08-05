@echo off
setlocal EnableExtensions
cd /d "%~dp0"

call :kill_port 8765
call :kill_port 5173

if not exist "orcalistupi-main\orcalistupi-main\START_API.bat" goto :missing_api
if not exist "frontend\package.json" goto :missing_frontend

start "ORACLIS API :8765" cmd /k "cd /d ""%~dp0orcalistupi-main\orcalistupi-main"" && call START_API.bat"
start "ORACLIS Frontend :5173" cmd /k "cd /d ""%~dp0frontend"" && if exist node_modules (npm run dev -- --host 127.0.0.1 --port 5173) else (call npm install && npm run dev -- --host 127.0.0.1 --port 5173)"

powershell -NoProfile -Command "Start-Sleep -Seconds 3; Start-Process 'http://127.0.0.1:5173'"
exit /b 0

:kill_port
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%~1 .*LISTENING"') do (
    echo Stopping PID %%P on port %~1...
    taskkill /F /PID %%P >nul 2>&1
)
exit /b 0

:missing_api
echo Missing backend launcher: orcalistupi-main\orcalistupi-main\START_API.bat
pause
exit /b 1

:missing_frontend
echo Missing frontend\package.json
pause
exit /b 1
