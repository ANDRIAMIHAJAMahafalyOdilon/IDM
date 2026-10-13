@echo off
title Roch Xd
cd /d "%~dp0flashdl"
call .venv\Scripts\activate.bat
python main.py
pause
