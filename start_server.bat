@echo off
title DeepSearcher Backend (8500)
cd /d D:\code\deep-searcher-study
call .venv\Scripts\activate.bat
uvicorn main:app --host 127.0.0.1 --port 8500
pause
