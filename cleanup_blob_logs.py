"""Blob上のデイリーランログ(lw-raw/logs/)を保持期間で掃除する。

run_gdx_logged.ps1 がログを logs/dailyrun_*.txt としてBlobにアップロードする
(2026-07-28追加)ため、その置き場を定期掃除する。dailyrun_latest.txt は常に残す。

使い方:
    python cleanup_blob_logs.py             # 7日より古いものを削除
    python cleanup_blob_logs.py --days 14   # 保持日数を指定
    python cleanup_blob_logs.py --dry-run   # 削除せず対象を表示

環境変数: AZURE_BLOB_CONNECTION_STRING, LW_BLOB_CONTAINER(既定 lw-raw)
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"), encoding="utf-8")
except Exception:
    pass

CONTAINER = os.environ.get("LW_BLOB_CONTAINER", "lw-raw")
PREFIX = "logs/"
KEEP_ALWAYS = {"logs/dailyrun_latest.txt"}


def main() -> None:
    ap = argparse.ArgumentParser(description="Blobログ掃除")
    ap.add_argument("--days", type=int, default=7, help="保持日数(既定7)")
    ap.add_argument("--dry-run", action="store_true", help="削除せず表示のみ")
    args = ap.parse_args()

    conn = os.environ.get("AZURE_BLOB_CONNECTION_STRING", "")
    if not conn:
        print("[ERROR] AZURE_BLOB_CONNECTION_STRING が未設定", file=sys.stderr)
        sys.exit(1)

    from azure.storage.blob import BlobServiceClient

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    cont = BlobServiceClient.from_connection_string(conn).get_container_client(CONTAINER)

    deleted = kept = 0
    for blob in cont.list_blobs(name_starts_with=PREFIX):
        if blob.name in KEEP_ALWAYS:
            kept += 1
            continue
        mtime = blob.last_modified
        if mtime and mtime < cutoff:
            if args.dry_run:
                print(f"[DRY-RUN] 削除対象: {blob.name} ({mtime:%Y-%m-%d})")
            else:
                cont.delete_blob(blob.name)
                print(f"[DELETE] {blob.name} ({mtime:%Y-%m-%d})")
            deleted += 1
        else:
            kept += 1
    print(f"[DONE] 削除 {deleted} 件 / 保持 {kept} 件 (保持{args.days}日)")


if __name__ == "__main__":
    main()
