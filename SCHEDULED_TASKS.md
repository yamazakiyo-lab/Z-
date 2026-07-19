# スケジュールタスク運用メモ（KEIRI-PC）

TSEG のデスクトップ(KEIRI-PC)で動くスケジュールタスクの正式定義と、取り扱いの注意。
**タスクは全て設定済み。以下のルールを守れば壊れません。**

---

## ⚠️ 厳守ルール（過去に2回、全タスクが壊れた原因）

1. **「全タスクを一括で作り直す」処理は絶対に実行しない。**
   タスクは完成済み。再作成は不要。過去、VS Code(AIアシスタント)が生成した
   「全タスク再作成スクリプト」を流したことで、全タスクの実行パスが壊れた。

2. **PowerShell では `%CD%` を絶対に使わない。**
   `%CD%` は cmd 専用。PowerShell では展開されず、そのまま文字列
   `%CD%\xxx.bat` として登録され、実行時にファイルが見つからず失敗する。
   タスク登録は必ず**フルパス**で書く（例:
   `C:\Users\user\tseg_vscode\Zフォルダ整理\xxx.bat`）。

3. **確認だけのときは `/Query` のみ。`/Create` を混ぜない。**
   複数行を貼り付けると、意図せず `/Create` 行が実行されて上書きされる。

4. 壊れたかどうかは `Task To Run` を見る。`%CD%\...` や
   `C:\windows\system32\...` になっていたら壊れている（＝下の正式定義で作り直す）。

---

## 正式定義（フルパス）

作業フォルダ: `C:\Users\user\tseg_vscode\Zフォルダ整理`（以下 `<DIR>` と表記）

| タスク名 | 実行内容(Task To Run) | スケジュール |
|---|---|---|
| GDX_DailyRun | `<DIR>\run_gdx_wrapper.bat` | 毎日 0:00 |
| LW_Morning_Greeting | `<DIR>\run_lw_morning_greeting_wrapper.bat` | 毎日 8:05 |
| LW_Blob_Sync | `<DIR>\lw_blob_sync_wrapper.bat` | 毎日 8:15 |
| LW_Send_Morning | `<DIR>\run_lw_send_wrapper.bat` | 毎日 10:00 |
| LW_Ranking_Weekly | `<DIR>\run_lw_ranking_wrapper.bat` | 毎日 10:15 |
| TSEG_タスク点検通知 | `<DIR>\run_task_check.bat` | 毎日 12:00 |
| LW_Send_Afternoon | `<DIR>\run_lw_send_wrapper.bat` | 毎日 15:00 |
| LW_Evening_Reminder | `<DIR>\run_lw_evening_reminder_wrapper.bat` | 毎日 16:55 |
| LW_Cleanup_Reminder | `<DIR>\run_lw_cleanup_wrapper.bat` | 毎週月曜 14:00 |
| TSEG_検索アプリ未利用通知 | `<DIR>\run_app_usage_reminder.bat` | 毎週月曜 8:00 |
| TSEG_週次利用レポート | `<DIR>\run_usage_report.bat` | 毎週月曜 9:00 |
| TSEG_ログ掃除 | `<DIR>\run_log_cleanup.bat` | 毎日 12:30（ノート・デスクトップ両方に設定） |
| TSEG_マスタCSV鮮度チェック | `<DIR>\run_master_csv_check.bat` | 毎週土曜 9:00（古ければ山嵜喜隆・山嵜絵里へLW通知） |
| LW_Bot_Receiver | `<DIR>\lw_venv\Scripts\python.exe -m uvicorn lineworks_bot_receiver:app --host 0.0.0.0 --port 8000`（Start In: `<DIR>`） | 起動時 |

---

## 復旧コマンド（壊れたタスクだけ、PowerShellで実行）

`` `"…`" `` はPowerShellでの引用符エスケープ。`/F` で上書き。

```powershell
# 毎日系
schtasks /Create /TN "GDX_DailyRun" /TR "`"C:\Users\user\tseg_vscode\Zフォルダ整理\run_gdx_wrapper.bat`"" /SC DAILY /ST 00:00 /F
schtasks /Create /TN "LW_Blob_Sync" /TR "`"C:\Users\user\tseg_vscode\Zフォルダ整理\lw_blob_sync_wrapper.bat`"" /SC DAILY /ST 08:15 /F
schtasks /Create /TN "LW_Send_Morning" /TR "`"C:\Users\user\tseg_vscode\Zフォルダ整理\run_lw_send_wrapper.bat`"" /SC DAILY /ST 10:00 /F
schtasks /Create /TN "LW_Send_Afternoon" /TR "`"C:\Users\user\tseg_vscode\Zフォルダ整理\run_lw_send_wrapper.bat`"" /SC DAILY /ST 15:00 /F
schtasks /Create /TN "LW_Evening_Reminder" /TR "`"C:\Users\user\tseg_vscode\Zフォルダ整理\run_lw_evening_reminder_wrapper.bat`"" /SC DAILY /ST 16:55 /F
schtasks /Create /TN "TSEG_タスク点検通知" /TR "`"C:\Users\user\tseg_vscode\Zフォルダ整理\run_task_check.bat`"" /SC DAILY /ST 12:00 /F

# 毎週月曜系
schtasks /Create /TN "TSEG_検索アプリ未利用通知" /TR "`"C:\Users\user\tseg_vscode\Zフォルダ整理\run_app_usage_reminder.bat`"" /SC WEEKLY /D MON /ST 08:00 /F
schtasks /Create /TN "TSEG_週次利用レポート" /TR "`"C:\Users\user\tseg_vscode\Zフォルダ整理\run_usage_report.bat`"" /SC WEEKLY /D MON /ST 09:00 /F
schtasks /Create /TN "TSEG_マスタCSV鮮度チェック" /TR "`"C:\Users\user\tseg_vscode\Zフォルダ整理\run_master_csv_check.bat`"" /SC WEEKLY /D SAT /ST 09:00 /F
```

ログ掃除（ノート・デスクトップ **両方** に設定する。ローカルリポジトリ + Y を保持14日で削除）:

```powershell
# デスクトップ(KEIRI-PC)で
schtasks /Create /TN "TSEG_ログ掃除" /TR "`"C:\Users\user\tseg_vscode\Zフォルダ整理\run_log_cleanup.bat`"" /SC DAILY /ST 12:30 /F
# ノートで
schtasks /Create /TN "TSEG_ログ掃除" /TR "`"C:\Users\Yamazakiyo\tseg_vscode\Zフォルダ整理\run_log_cleanup.bat`"" /SC DAILY /ST 12:30 /F
```

確認（Task To Run がフルパスかを見るだけ。/Create は混ぜない）:

```powershell
foreach($t in 'GDX_DailyRun','LW_Blob_Sync','LW_Send_Morning','LW_Send_Afternoon','LW_Evening_Reminder','TSEG_検索アプリ未利用通知','TSEG_週次利用レポート','TSEG_タスク点検通知'){
  "===== $t ====="; schtasks /Query /TN $t /V /FO LIST | findstr /I "Task To Run:  Next Run Time:"
}
```

---

## 自動監視

`TSEG_タスク点検通知`（毎日12:00 / `check_tasks_notify.py`）が、重要タスクの前回結果を
点検し、失敗・無効化があれば **LINE WORKS で山嵜喜隆へ通知**する。
`%CD%` 破損や実行失敗もここで気づける。
