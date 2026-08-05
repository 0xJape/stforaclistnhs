@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHON_EXE=%~dp0..\..\.venv-1\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%LOCALAPPDATA%\ORACLIS\py314\Scripts\python.exe"
if not exist "%PYTHON_EXE%" goto :failed
"%PYTHON_EXE%" src\spatiotemporal_runtime_api.py --host 127.0.0.1 --port 8765
if errorlevel 1 goto :failed
exit /b 0
:failed
pause
exit /b 1
