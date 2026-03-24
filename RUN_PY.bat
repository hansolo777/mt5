@echo off
REM Wrapper за стартиране на Python скриптове без stdin проблем
REM Употреба: RUN_PY.bat скрипт.py
C:\Python314\python.exe -u %1 %2 %3 %4 %5 < nul
