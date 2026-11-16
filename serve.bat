@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul || (echo [!] python not on PATH & pause & exit /b 1)
python run.py %*
if errorlevel 1 pause
endlocal
