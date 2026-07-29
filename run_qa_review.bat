@echo off
rem AI Q&A 週次レビュー(毎週月曜9:15)。当月ログのミス回答を分類し、
rem 類義語候補ドラフトを生成、要約を山嵜さんへLW通知する。
cd /d "%~dp0"

rem 最新コードを取得してから実行
git pull origin master >> qa_review.log 2>&1

set PYTHONIOENCODING=utf-8
python tools\qa_review.py --check-index --suggest --notify >> qa_review.log 2>&1
