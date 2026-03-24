@echo off
cd /d C:\XAUUSD_AI_Bridge

netstat -ano | findstr ":5000" | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo [!] Bridge вече работи. Спри го с STOP_BRIDGE.bat
    pause
    exit /b
)

echo Стартирам Bridge в background...
start "" /B C:\Python314\pythonw.exe bridge.py

timeout /t 4 /nobreak >nul

netstat -ano | findstr ":5000" | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo [OK] Bridge работи на http://127.0.0.1:5000
    echo За да спреш: STOP_BRIDGE.bat
) else (
    echo [ГРЕШКА] Bridge не стартира. Провери bridge.log
)
pause
