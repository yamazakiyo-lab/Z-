@echo off
rem 元マスタCSV(工事一覧表・発注者一覧表)の鮮度チェック。毎週土曜に実行。
rem 古ければ 山嵜喜隆・山嵜絵里 へ LINE WORKS で通知する。
cd /d "%~dp0"

rem 最新のコード・設定を取得してから実行
git pull origin master >> master_csv_check.log 2>&1

python check_master_csv.py >> master_csv_check.log 2>&1
