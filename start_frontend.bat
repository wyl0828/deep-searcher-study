@echo off
title DeepSearcher Frontend (8600)
cd /d D:\code\deep-searcher-study
call .venv\Scripts\activate.bat
uvicorn frontend.server:app --host 127.0.0.1 --port 8600
pause
