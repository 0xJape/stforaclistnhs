@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "ENV_DIR=%LOCALAPPDATA%\ORACLIS\py314"
set "PYTHON_EXE=%ENV_DIR%\Scripts\python.exe"
set "TEMP=%LOCALAPPDATA%\ORACLIS\tmp"
set "TMP=%TEMP%"
if not exist "%TEMP%" mkdir "%TEMP%"
if not exist "%PYTHON_EXE%" (
    py -3.14 -c "import sys,struct; assert sys.version_info[:2]==(3,14); assert struct.calcsize('P')*8==64" >nul 2>&1
    if errorlevel 1 goto :failed
    if not exist "%LOCALAPPDATA%\ORACLIS" mkdir "%LOCALAPPDATA%\ORACLIS"
    py -3.14 -m venv "%ENV_DIR%"
    if errorlevel 1 goto :failed
)
"%PYTHON_EXE%" -m pip install --disable-pip-version-check --no-cache-dir --upgrade pip setuptools wheel
if errorlevel 1 goto :failed
"%PYTHON_EXE%" -m pip install --disable-pip-version-check --no-cache-dir --only-binary=:all: -r requirements.txt
if errorlevel 1 goto :failed
"%PYTHON_EXE%" src\check_south_cotabato_boundary_setup.py
if errorlevel 1 goto :failed
"%PYTHON_EXE%" src\run_spatiotemporal_bayesian.py --config spatiotemporal_config.json --self-test
if errorlevel 1 goto :failed
"%PYTHON_EXE%" integration\send_make_alerts.py
if errorlevel 1 goto :failed
set "RUN_DIR="
for /f "usebackq delims=" %%I in ("outputs\latest_spatiotemporal_run.txt") do set "RUN_DIR=%%I"
if defined RUN_DIR (
    if exist "%RUN_DIR%\ORACLIS_SouthCotabato_SpatioTemporal_Bayesian_Report.html" start "" "%RUN_DIR%\ORACLIS_SouthCotabato_SpatioTemporal_Bayesian_Report.html"
    if exist "%RUN_DIR%\maps\ORACLIS_SouthCotabato_Interactive_Forecast_Map.html" start "" "%RUN_DIR%\maps\ORACLIS_SouthCotabato_Interactive_Forecast_Map.html"
    start "" "%RUN_DIR%"
)
exit /b 0
:failed
pause
exit /b 1
