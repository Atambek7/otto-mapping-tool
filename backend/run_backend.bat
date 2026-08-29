@echo off
cd %~dp0
call venv\Scripts\activate
uvicorn main:app --reload --port 8001
