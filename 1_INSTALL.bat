@echo off
cd /d "%~dp0"
chcp 65001 >nul
echo.
echo   Synaqmaker Resolver - Install
echo.
pip install -r requirements.txt
echo.
echo   Done! Run 2_START.bat
pause
