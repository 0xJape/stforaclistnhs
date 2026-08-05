@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if exist "data\cache" rmdir /s /q "data\cache"
exit /b 0
