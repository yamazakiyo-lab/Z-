"""Blob上のLW写真ステージング(lw-raw/YYYYMMDD/)を保持期間で掃除する。

LINE WORKSで投稿された写真は lw-raw の日付フォルダに一旦保存され、
lw_blob_sync が Z(_LWExtraction経由) へ取り込む。取り込み後もBlob原本が
残り続けて容量が育つため、次の二条件を両方満たすものだけ削除する。

  1. 日付フォルダ(YYYYMMDD/)が保持日数(既定30日)より古い
  2. lw_blob_sync_state.json の "synced" に記録がある(=Zへ取り込み済み)

未同期のまま古くなったものは削除せず [WARN] で知らせる(データ保護)。
写真本体を消すとき、対になる *_meta.json も一緒に消す。

使い方:
    python cleanup_blob_photos.py             # 30日より古い同期済み分を削除
    python cleanup_blob_photos.py --days 60   # 保持日数を指定
    python cleanup_blob_photos.py --dry-run   # 削除せず対象を表示

環境変数: AZURE_BLOB_CONNECTION_STRING, LW_BLOB_CONTAINER(既定 lw-raw),
          LW_SYNC_STATE(既定 リポジトリ直下の lw_blob_sync_state.json)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

JST = timezone(timedelta(hours=9))
CONTAINER = os.environ.get("LW_BLOB_CONTAINER", "lw-raw")
_default_state = Path(__file__).with_name("lw_blob_sync_state.json")
STATE_FILE = Path(os.environ.get("LW_SYNC_STATE", str(_default_state)))
DATE_PREFIX = re.compile(r"^(\d{8})/")


def _load_synced() -> set[str]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return set(data.get("synced", []))
    except Exception as e:
        print(f"[WARN] 同期記録が読めません({STATE_FILE}): {e}")
        return set()


def main() -> int:
    ap = argparse.ArgumentParser(description="LW写真ステージングのBlob掃除")
    ap.add_argument("--days", type=int, default=30, help="保持日数(既定30)")
    ap.add_argument("--dry-run", action="store_true", help="削除せず対象を表示")
    args = ap.parse_args()

    conn = os.environ.get("AZURE_BLOB_CONNECTION_STRING", "")
    if not conn:
        print("ERROR: AZURE_BLOB_CONNECTION_STRING が未設定です", file=sys.stderr)
        return 1
    from azure.storage.blob import BlobServiceClient

    synced = _load_synced()
    if not synced:
        print("[SKIP] 同期記録が空のため何も削除しません(安全側)")
        return 0

    cutoff = (datetime.now(JST) - timedelta(days=args.days)).strftime("%Y%m%d")
    container = BlobServiceClient.from_connection_string(conn) \
        .get_container_client(CONTAINER)

    # 拡張子ぬきの照合用(メタ *_meta.json は対の写真の同期記録で判定する)
    synced_stems = {n.rsplit(".", 1)[0] for n in synced}

    deleted = kept_recent = kept_unsynced = 0
    for blob in container.list_blobs():
        m = DATE_PREFIX.match(blob.name)
        if not m:
            continue  # 日付フォルダ以外(logs/, task_status/等)は対象外
        if m.group(1) >= cutoff:
            kept_recent += 1
            continue
        # callbackダンプ(受信生データのログ)は同期対象外なので期限だけで削除
        if re.match(r"^\d{8}/callback_", blob.name):
            if args.dry_run:
                print(f"[DRY-RUN] 削除対象(callback): {blob.name}")
            else:
                try:
                    container.delete_blob(blob.name)
                except Exception as e:
                    print(f"[WARN] 削除失敗: {blob.name}: {e}")
                    continue
            deleted += 1
            continue
        if blob.name.endswith("_meta.json"):
            ok = blob.name[:-len("_meta.json")] in synced_stems
        else:
            ok = blob.name in synced
        if not ok:
            # 未同期 → 本体もメタも残す(データ保護)
            kept_unsynced += 1
            continue
        if args.dry_run:
            print(f"[DRY-RUN] 削除対象: {blob.name}")
        else:
            try:
                container.delete_blob(blob.name)
                print(f"[DEL] {blob.name}")
            except Exception as e:
                print(f"[WARN] 削除失敗: {blob.name}: {e}")
                continue
        deleted += 1

    if kept_unsynced:
        print(f"[WARN] 未同期のため残した古いblob: {kept_unsynced}件"
              "(lw_blob_syncの取り込み漏れの可能性。確認推奨)")
    print(f"[DONE] 写真ステージング掃除: 削除{deleted}件 / "
          f"保持期間内{kept_recent}件 / 未同期保護{kept_unsynced}件 (保持{args.days}日)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
