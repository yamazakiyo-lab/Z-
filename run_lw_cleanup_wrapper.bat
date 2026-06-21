@echo off
cd /d "%~dp0"
py -3 "%~dp0lw_annotation_bot.py" --cleanup-reminder
