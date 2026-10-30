@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch-local.ps1" -FirstBattle
exit /b %ERRORLEVEL%
