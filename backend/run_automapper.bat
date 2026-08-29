@echo off
set PYTHONIOENCODING=utf8
set PYTHONUNBUFFERED=1
cd %~dp0
call venv\Scripts\activate
python automapper.py
