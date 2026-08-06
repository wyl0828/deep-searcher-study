@echo off
title DeepSearcher Frontend (8600)
cd /d "%~dp0"
call .venv\Scripts\activate.bat
set DEEPSEARCHER_API_URL=http://127.0.0.1:8650
uvicorn frontend.server:app --host 127.0.0.1 --port 8600
pause
