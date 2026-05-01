@echo off
cd /d "%~dp0"
chcp 65001 >nul
echo.
echo   Synaqmaker Resolver
echo.
python run.py
pause
