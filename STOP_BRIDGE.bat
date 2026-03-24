@echo off
echo Спирам XAUUSD AI Bridge...

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000" ^| findstr "LISTENING"') do (
    echo Спирам PID: %%a
    taskkill /PID %%a /F >nul 2>&1
)

timeout /t 1 /nobreak >nul

netstat -ano | findstr ":5000" | findstr "LISTENING" >nul 2>&1
if %errorlevel%==1 (
    echo [OK] Bridge е спрян.
) else (
    echo [!] Bridge все още работи - провери Task Manager.
)
pause
