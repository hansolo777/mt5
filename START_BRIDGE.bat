@echo off
echo ============================================================
echo   XAUUSD AI Bridge v1.0 — Claude Powered
echo ============================================================
echo.
echo Python: C:\Python314\python.exe
echo.
echo ВАЖНО: Провери дали ANTHROPIC_API_KEY е зададен в bridge.py
echo        Отвори bridge.py и постави ключа на ред 20:
echo        ANTHROPIC_API_KEY = "sk-ant-..."
echo.
echo Стартирам на http://localhost:5000
echo.
cd /d C:\XAUUSD_AI_Bridge
C:\Python314\python.exe bridge.py
#start "" /B C:\Python314\pythonw.exe bridge.py
pause
