"""学習協力Botのやり取りを Blob の callback 記録から時系列復元する診断ツール。

「継続すると3回目で写真が出なくなる」等の報告を検証するため、
lw-raw/<日付>/callback_*.json (受信側が全コールバックを保存している)を読み、
ユーザーごとの発言タイムラインを表示する。Botの送信は記録されないが、
「y」の後にコメントが続けば写真は届いており、「y」の後に無反応・
「？」・「写真が来ない」等が続けば送信失敗とわかる。

使い方(デスクトップ):
    python tools\\lw_diag_annotation.py                 # 今日(JST)
    python tools\\lw_diag_annotation.py --date 20260730 # 日付指定
    python tools\\lw_diag_annotation.py --user 802dd1b1 # ユーザー絞り込み(前方一致)

環境変数: AZURE_BLOB_CONNECTION_STRING (.env から自動読込)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

JST = timezone(timedelta(hours=9))
CONTAINER = os.environ.get("LW_BLOB_CONTAINER", "lw-raw")


def main() -> int:
    ap = argparse.ArgumentParser(description="学習協力Bot やり取りタイムライン")
    ap.add_argument("--date", default=datetime.now(JST).strftime("%Y%m%d"),
                    help="対象日 YYYYMMDD (既定: 今日JST)")
    ap.add_argument("--user", default="", help="ユーザーID前方一致で絞り込み")
    args = ap.parse_args()

    conn = os.environ.get("AZURE_BLOB_CONNECTION_STRING", "")
    if not conn:
        print("ERROR: AZURE_BLOB_CONNECTION_STRING が未設定です", file=sys.stderr)
        return 1
    from azure.storage.blob import BlobServiceClient
    container = BlobServiceClient.from_connection_string(conn) \
        .get_container_client(CONTAINER)

    # ユーザー名(あれば)
    names: dict = {}
    try:
        names = json.loads(container.download_blob("lw_user_names.json").readall())
        if isinstance(names, dict) and "names" in names:
            names = names["names"]
    except Exception:
        pass

    prefix = f"{args.date}/callback_"
    timeline: dict[str, list[tuple[str, str]]] = {}
    count = 0
    for blob in container.list_blobs(name_starts_with=prefix):
        try:
            payload = json.loads(container.download_blob(blob.name).readall())
        except Exception:
            continue
        count += 1
        uid = (payload.get("source") or {}).get("userId", "?")
        if args.user and not uid.startswith(args.user):
            continue
        content = payload.get("content") or {}
        mtype = content.get("type", "?")
        if mtype == "text":
            desc = content.get("text", "")
        else:
            desc = f"<{mtype}> {content.get('fileName', '')}"
        # callback_HHMMSS_xxxx.json から時刻
        t = blob.name.split("callback_")[-1][:6]
        hhmmss = f"{t[:2]}:{t[2:4]}:{t[4:6]}" if len(t) >= 6 and t.isdigit() else "?"
        timeline.setdefault(uid, []).append((hhmmss, desc))

    if not timeline:
        print(f"[INFO] {prefix} に該当なし (callback {count}件走査)")
        return 0

    print(f"[SCAN] {args.date}: callback {count}件")
    for uid, events in sorted(timeline.items(), key=lambda kv: kv[1][0][0]):
        name = names.get(uid, "") if isinstance(names, dict) else ""
        print(f"\n── {name or uid[:13]} ({len(events)}件) " + "─" * 30)
        for hhmmss, desc in events:
            print(f"  {hhmmss}  {desc[:70]}")
    print("\n読み方: 「y」の直後にコメントが続いていれば次の写真は届いている。")
    print("「y」の後に長い沈黙・「？」・再度「y」などが続く箇所が送信失敗の疑い。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
