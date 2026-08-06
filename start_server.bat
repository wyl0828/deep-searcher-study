@echo off
title DeepSearcher Backend (8650)
cd /d "%~dp0"
call .venv\Scripts\activate.bat
uvicorn main:app --host 127.0.0.1 --port 8650
pause
