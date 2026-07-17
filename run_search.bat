@echo off
rem ============================================================
rem  検索アプリ起動ランチャー(自動で最新を取得してから起動)
rem  ダブルクリックで: git pull -> streamlit run search_app.py
rem  どのPCでも動くよう、このbatが置かれたフォルダを基準にする。
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

echo === 最新コードを取得します (git pull) ===
git pull
if errorlevel 1 (
    echo.
    echo [!] git pull に失敗しました。ネットワーク/競合を確認してください。
    echo     このまま現在のコードで起動する場合は何かキーを押してください。
    pause >nul
)

echo.
echo === 検索アプリを起動します (streamlit) ===
streamlit run search_app.py

echo.
echo アプリを終了しました。ウィンドウを閉じてください。
pause
