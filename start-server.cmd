@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-service.ps1"
if errorlevel 1 (
  echo Local LJJC server failed to start. 1>&2
  exit /b 1
)
endlocal
