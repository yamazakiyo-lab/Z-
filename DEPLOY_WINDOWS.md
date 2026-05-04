# Deploy to Y server (Windows) — 手順メモ

このリポジトリを Y サーバー（Windows）に配置して毎日 00:00 に自動実行するための手順です。

前提
- Y サーバーに管理者またはデプロイ用サービスアカウントでアクセスできること
- Python 3.x がインストールされていること（`py` コマンドが使えると楽）
- サーバーにファイルを配置できる方法（SMB/robocopy / WinRM / RDP / scp for Win）

概要手順
1. ワークスペースをサーバーへコピー（例: `robocopy` または SMB）。
2. サーバーで venv を作成し依存をインストール。
3. `credentials.json` / `token.json` を安全な場所に配置（アクセス権を限定）。
4. スケジュールタスク（`schtasks`）を作成して `run_gdx_wrapper.bat` / `run_91other_wrapper.bat` を 00:00 に実行する。要件に応じて「ユーザーがログオンしているかどうかに関係なく実行」設定にする。
5. 日次ログ集約（`check_and_cleanup_logs.ps1`）を 00:01 に実行するタスクを作成。
6. 手動テスト実行 → `logs/` と `daily_runs_summary_*.txt` を確認。

重要な設定例（コマンド）

ファイルコピー（SMB の例）
```powershell
robocopy C:\path\to\local\workspace \\Y-SERVER\Deploy\gdx_workspace /MIR
```

サーバー上でのセットアップ
```powershell
cd C:\Deploy\gdx_workspace
py -3 -m venv venv
.\venv\Scripts\pip install -r requirements.txt
mkdir logs
```

スケジュールタスク作成（現在ログオン中のユーザーで登録する例）
```powershell
schtasks /Create /TN "GDX_DailyRun" /SC DAILY /ST 00:00 /TR "C:\Deploy\gdx_workspace\run_gdx_wrapper.bat" /F
schtasks /Create /TN "OTHER_DailyRun" /SC DAILY /ST 00:00 /TR "C:\Deploy\gdx_workspace\run_91other_wrapper.bat" /F
schtasks /Create /TN "CHECK_DAILYRUNS" /SC DAILY /ST 00:01 /TR "powershell -NoProfile -ExecutionPolicy Bypass -File 'C:\Deploy\gdx_workspace\check_and_cleanup_logs.ps1'" /F
```

「ユーザーがログオンしていなくても実行」させたい場合
- ドメインサービスアカウントを作成し、そのアカウントで `/RU "DOMAIN\svc-account" /RP "<password>"` を指定してタスクを作成する。
- あるいは SYSTEM アカウントで実行する（権限と副作用に注意）。

認証の注意点
- Google Drive 等で OAuth のインタラクティブ認証が必要な場合は、最初に手動で `token.json` を作成してサーバーに置く（サービスアカウントの導入が可能ならそちらを推奨）。

ログと保守
- `logs/` 配下にすべての実行ログを蓄積。`check_and_cleanup_logs.ps1` による90日クリーンアップを有効にする。
- 障害通知が欲しければ `check_and_cleanup_logs.ps1` にメール/HTTP 通知を追加する。

次のアクション
- 私が Y サーバーへファイルを転送してセットアップ（WinRM/SMB の接続情報が必要）してよいですか？
- または、この手順に従ってご自身で実行しますか？
