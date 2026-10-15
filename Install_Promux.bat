@echo off
title Promux Windows Installer
cd /d "%~dp0"
echo ========================================================
echo Installing Promux Windows...
echo ========================================================
python installer.py
if %ERRORLEVEL% NEQ 0 (
    echo [!] Installation failed.
) else (
    echo [+] Installation completed! You can now launch Promux from your Desktop.
)
pause
