@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0status.ps1" %*
if errorlevel 1 (
  echo.
  echo Some services are not ready.
)
pause
