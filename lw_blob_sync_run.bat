@echo off
cd /d C:\Users\Yamazakiyo\tseg_vscode\Zフォルダ整理
echo [1/2] 依存パッケージを確認中...
pip install azure-storage-blob python-dotenv --quiet
echo [2/2] 同期スクリプトを実行中...
python lw_blob_sync.py > lw_sync_last.log 2>&1
type lw_sync_last.log
echo.
echo 終了。キーを押して閉じてください。
pause
