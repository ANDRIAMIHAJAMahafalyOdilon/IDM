@echo off
title FlashDL
cd /d "%~dp0flashdl"
call .venv\Scripts\activate.bat
python main.py
pause
