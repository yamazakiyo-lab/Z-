

VS Code の実行タスク／デバッグ構成は `tasks.json` と `launch.json` に追加済みです。

注意: 実行時に Google Drive を利用する処理があるため、実運用では Drive 認証情報ファイル `credentials.json` を
プロジェクトルート（`91GDX・252WORKNO-program` フォルダ内）に配置してください。配置後に初回実行するとブラウザ認証が始まり、
生成された `token.json` が保存されます。

本番実行を既定とします。Google Drive を使う場合は `91GDX・252WORKNO-program` フォルダ直下に `credentials.json` を置いてから実行してください。

テスト目的で Drive を無効化して実行したい場合は環境変数 `GDX_SKIP_DRIVE=1` を設定します（ダミー応答）。PowerShell の例:

```powershell
$env:GDX_SKIP_DRIVE = '1'
& "${PWD}\91GDX・252WORKNO-program\venv\Scripts\python.exe" .\run_gdx.py --dry-run
```

毎日自動でフル実行したい場合は、同梱のスクリプト `schedule_gdx.ps1` を実行して Windows タスクスケジューラへ登録できます（デフォルトは毎日03:00）。

実行結果を朝に確認したい場合は、`schedule_daily_status_check.ps1` を実行すると、毎日 06:00 に GDX と OTHER の実行状況を確認してポップアップ表示し、同時に `daily_runs_summary_*.txt` を作成します。

例（手動で今すぐ実行）:

```powershell
& "${PWD}\91GDX・252WORKNO-program\venv\Scripts\python.exe" .\run_gdx.py
```

共有フォルダで両PC運用する場合の注意:

- 自動実行ホストは 1 台に固定するのが基本です。
- GDX と 91OTHER のラッパーは共有フォルダ上の `.runtime\gdx.lock` と `.runtime\other.lock` を使って二重実行を防ぎます。
- GDX の `token.json` は `LOCALAPPDATA\tseg_vscode_runtime\gdx` を優先して使うため、各PCでローカルに分離されます。
- タスクスケジューラではネットワークドライブ文字より UNC パスを使う方が安全です。
- `schedule_gdx.ps1`、`schedule_91other.ps1`、`schedule_daily_status_check.ps1` は、登録時にネットワークドライブを UNC に解決します。
- ノートPC側で定期実行を止めるときは `disable_local_tasks.ps1` を実行します。

開発用の C ドライブから Y ドライブのバックアップへ同期したい場合は、`sync_to_y_backup.ps1` を実行します。
まずは `-DryRun` で差分確認し、問題なければそのまま実行してください。

2 週間ごとの自動同期タスクは、`schedule_sync_to_y_backup.ps1` で登録します。既定では土曜 12:00 に実行されます。

```powershell
& .\sync_to_y_backup.ps1 -DryRun
& .\sync_to_y_backup.ps1
& .\schedule_sync_to_y_backup.ps1
```
