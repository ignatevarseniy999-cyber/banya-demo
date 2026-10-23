@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
    echo Первая установка, подождите...
    python -m venv .venv || (echo Установите Python 3.10+ с python.org и поставьте галочку "Add to PATH" & pause & exit /b 1)
    .venv\Scripts\python -m pip install -r requirements.txt
    .venv\Scripts\python -m playwright install chromium
)
if not exist .env (
    copy .env.example .env >nul
    echo Впишите токен бота в файл .env и запустите run.bat ещё раз.
    notepad .env
    pause
    exit /b 0
)
.venv\Scripts\python monitor.py
pause
