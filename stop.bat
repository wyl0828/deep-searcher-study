@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1" %*
if errorlevel 1 (
  echo.
  echo Stop failed. Review the message above.
  pause
)
