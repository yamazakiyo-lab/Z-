@echo off
rem 全ログ掃除（ローカルリポジトリ + Y）。ノート・デスクトップ両方で毎日実行する。
rem デスクトップ実行 → K(=ローカル) + Y、ノート実行 → ノート(=ローカル) + Y を掃除。
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0cleanup_logs_all.ps1" -RetainDays 7 >> log_cleanup.log 2>&1
