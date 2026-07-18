"""重要スケジュールタスクの健康診断＋LW通知（見張り役）。

各タスクの「前回の実行結果(LastTaskResult)」を Get-ScheduledTaskInfo で読み、
失敗（0=成功 / 267009=実行中 / 267011=未実行 以外）や無効化を検知したら、
LINE WORKS BOT で担当者（既定:山嵜喜隆）へ通知する。

サインインログ等と違い管理者権限も不要。毎日1回スケジュール実行する想定。

使い方（デスクトップ）:
  python check_tasks_notify.py            # 異常があるときだけ通知
  python check_tasks_notify.py --always   # 正常でも結果を通知
  python check_tasks_notify.py --dry-run  # LW送信せず画面表示のみ

環境変数（.env）:
  CHECK_NOTIFY_NAMES  通知先の氏名（カンマ区切り。既定 '山嵜喜隆'）
  ＋ LW BOT が使う既存の資格情報（lw_annotation_bot が参照）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"), encoding="utf-8")
except Exception:
    pass

# 監視対象タスク（重要なものだけ。必要に応じて増減可）
CRITICAL_TASKS = [
    "GDX_DailyRun",
    "LW_Blob_Sync",
    "TSEG_検索アプリ未利用通知",
    "TSEG_週次利用レポート",
    "LW_Morning_Greeting",
    "LW_Evening_Reminder",
]
# 正常とみなす LastTaskResult: 0=成功 / 267009=実行中 / 267011=まだ実行なし
OK_RESULTS = {0, 267009, 267011}


def _query_task(task: str) -> dict:
    """Get-ScheduledTaskInfo でタスクの結果・状態を取得（ロケール非依存）。"""
    ps = (
        "$ErrorActionPreference='Stop';"
        f"$t=Get-ScheduledTask -TaskName '{task}';"
        "$i=$t|Get-ScheduledTaskInfo;"
        "[pscustomobject]@{"
        "result=[int]$i.LastTaskResult;"
        "state=[string]$t.State;"
        "last=if($i.LastRunTime){$i.LastRunTime.ToString('yyyy-MM-dd HH:mm')}else{''};"
        "next=if($i.NextRunTime){$i.NextRunTime.ToString('yyyy-MM-dd HH:mm')}else{''}"
        "} | ConvertTo-Json -Compress"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        out = (r.stdout or "").strip()
        if not out:
            return {"found": False, "err": (r.stderr or "").strip()[:200]}
        d = json.loads(out)
        d["found"] = True
        return d
    except Exception as e:
        return {"found": False, "err": str(e)}


def _notify(msg: str, dry_run: bool) -> None:
    try:
        import lw_annotation_bot as bot
    except Exception as e:
        print(f"[WARN] LW通知スキップ(bot読込失敗): {e}")
        return
    names = os.environ.get("CHECK_NOTIFY_NAMES", "山嵜喜隆")
    targets = [n.strip() for n in names.split(",") if n.strip()]
    try:
        umap = bot._load_user_names()  # {userId: 氏名}
    except Exception as e:
        print(f"[WARN] LW通知スキップ(氏名一覧取得失敗): {e}")
        return

    def _norm(s: str) -> str:
        return "".join((s or "").split())

    n2u = {_norm(v): k for k, v in umap.items()}
    if dry_run:
        bot.DRY_RUN = True
    for t in targets:
        uid = n2u.get(_norm(t))
        if not uid:
            print(f"[WARN] 通知先が見つかりません(氏名不一致): {t}")
            continue
        ok = bot._send_text(uid, msg)
        print(f"[LW] {t} へ通知{'(DRY-RUN)' if dry_run else ''}: {'OK' if ok else 'NG'}")


def main() -> None:
    ap = argparse.ArgumentParser(description="重要タスクの健康診断＋LW通知")
    ap.add_argument("--always", action="store_true", help="正常でも通知する")
    ap.add_argument("--dry-run", action="store_true", help="LW送信せず表示のみ")
    args = ap.parse_args()

    problems: list[str] = []
    ok: list[str] = []
    for t in CRITICAL_TASKS:
        info = _query_task(t)
        if not info.get("found"):
            problems.append(f"❌ {t}: 未登録/取得失敗 {info.get('err', '')}")
            continue
        res = info.get("result")
        state = info.get("state", "")
        if state == "Disabled":
            problems.append(f"⚠️ {t}: 無効化されています")
        elif res not in OK_RESULTS:
            problems.append(
                f"⚠️ {t}: 前回結果 {res}（前回 {info.get('last', '')} / 次回 {info.get('next', '')}）"
            )
        else:
            ok.append(t)

    now = datetime.now().strftime("%Y/%m/%d %H:%M")
    if problems:
        msg = (
            f"🩺デイリータスク点検（{now}）\n"
            f"⚠️要対応 {len(problems)}件:\n" + "\n".join(problems) +
            f"\n\n✅正常 {len(ok)}件: " + "、".join(ok)
        )
    else:
        msg = f"🩺デイリータスク点検（{now}）\n✅すべて正常（{len(ok)}件）: " + "、".join(ok)

    print(msg)

    if problems or args.always:
        _notify(msg, args.dry_run)
    else:
        print("[LW] 異常なしのため通知は送りません（--always で毎回送信）")


if __name__ == "__main__":
    main()
