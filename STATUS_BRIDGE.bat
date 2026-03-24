@echo off
echo === XAUUSD AI Bridge Status ===
echo.

netstat -ano | findstr ":5000" | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo [ONLINE]  Bridge raboti na http://127.0.0.1:5000
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000" ^| findstr "LISTENING"') do echo [PID]     %%a
) else (
    echo [OFFLINE] Bridge ne raboti - izpulni START_BRIDGE_BACKGROUND.bat
)

echo.
echo === Posledni 15 reshenia ===
if exist C:\XAUUSD_AI_Bridge\decisions.jsonl (
    powershell -Command "$lines = Get-Content C:\XAUUSD_AI_Bridge\decisions.jsonl -Tail 15; $lines | ForEach-Object { try { $r = $_ | ConvertFrom-Json; [PSCustomObject]@{ time = $r.timestamp.Substring(11,8); action = $r.action; conf = $r.confidence; bid = $r.bid; sl = $r.sl_price; tp = $r.tp_price } } catch {} } | Format-Table -AutoSize"
) else (
    echo Nyama reshenia oshte.
)

echo.
echo === Posledni 5 loga ===
if exist C:\XAUUSD_AI_Bridge\bridge.log (
    powershell -Command "Get-Content C:\XAUUSD_AI_Bridge\bridge.log -Tail 5"
)
pause
