@echo off
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\start_source_test.ps1" -Console
if errorlevel 1 pause
