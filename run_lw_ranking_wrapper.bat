@echo off
cd /d "%~dp0"
setlocal enabledelayedexpansion
set "dryrun_flag="
if "%~1"=="--dry-run" set "dryrun_flag=--dry-run"
py -3 "%~dp0lw_annotation_bot.py" --ranking-weekly !dryrun_flag!
