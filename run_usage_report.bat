@echo off
rem 検索アプリ + Teams の週次利用レポートを生成（毎週月曜 9:00 に実行）
rem %~dp0 = このbatのあるフォルダ（＝リポジトリ）。マシンが変わっても動くよう相対で動作。
cd /d "%~dp0"

rem 最新のコードを取得してから実行
git pull origin master >> usage_report.log 2>&1

rem 利用レポート生成（usage_report_YYYYMMDD.csv がこのフォルダに出力される）
python report_usage.py >> usage_report.log 2>&1
