@echo off
cd /d C:\XAUUSD_AI_Bridge
echo Анализирам decisions.jsonl...
C:\Python314\python.exe analyze_decisions.py
echo.
echo Отварям HTML отчета...
start "" "C:\XAUUSD_AI_Bridge\analysis_report.html"
