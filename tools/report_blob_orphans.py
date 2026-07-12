"""Blob 孤児レポート — photos コンテナと Z:\\ の 91 フォルダを突き合わせる。

Blob 上に存在するがローカル(Z:)に存在しないファイル(=リネーム・削除で取り残された旧Blob)を
CSV に列挙する。**削除は一切行わない**(レポート専用)。

背景: azcopy sync は --delete-destination を付けていない片方向差分コピーのため、
ローカルでリネーム・削除されたファイルの旧Blobが蓄積し続ける(ストレージコスト増)。
掃除の実施判断はこのレポートを確認してから行う。

使い方:
  python tools/report_blob_orphans.py            # レポート作成
  python tools/report_blob_orphans.py --limit 100  # 最初の100件だけ(動作確認用)

必要な環境変数(.env):
  AZURE_BLOB_CONNECTION_STRING  Blob 接続文字列
  PHOTOS_BLOB_CONTAINER         コンテナ名(省略時: photos)
  TARGET_91_ROOT                ローカル91ルート(省略時: Z:\\takachiho\\2to9_業務別フォルダ\\91_工番別実績写真・動画)
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime
from pathlib import Path, PureWindowsPath

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

try:
    from azure.storage.blob import BlobServiceClient
except ImportError:
    print("[ERROR] pip install azure-storage-blob python-dotenv", file=sys.stderr)
    sys.exit(1)

CONN_STR = os.environ.get("AZURE_BLOB_CONNECTION_STRING", "")
CONTAINER = os.environ.get("PHOTOS_BLOB_CONTAINER", "photos")
LOCAL_ROOT = Path(os.environ.get(
    "TARGET_91_ROOT",
    r"Z:\takachiho\2to9_業務別フォルダ\91_工番別実績写真・動画",
))


def main() -> None:
    parser = argparse.ArgumentParser(description="Blob孤児レポート(削除はしない)")
    parser.add_argument("--limit", type=int, default=None, help="走査するBlob数の上限(動作確認用)")
    args = parser.parse_args()

    if not CONN_STR:
        print("[ERROR] AZURE_BLOB_CONNECTION_STRING が未設定です", file=sys.stderr)
        sys.exit(1)

    # Z: が見えない状態で走ると全Blobが孤児扱いになるため必ず中止する
    if not LOCAL_ROOT.is_dir():
        print(f"[ERROR] ローカルルートにアクセスできません: {LOCAL_ROOT}", file=sys.stderr)
        print("        Z: 未接続の可能性。誤検知防止のため中止します。", file=sys.stderr)
        sys.exit(2)

    svc = BlobServiceClient.from_connection_string(CONN_STR)
    container = svc.get_container_client(CONTAINER)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(__file__).resolve().parent.parent / f"blob_orphans_{ts}.csv"

    scanned = 0
    orphans = 0
    orphan_bytes = 0

    with open(out_path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["blob_name", "size_bytes", "last_modified", "local_path"])
        for blob in container.list_blobs():
            scanned += 1
            local = LOCAL_ROOT / PureWindowsPath(blob.name.replace("/", "\\"))
            if not local.exists():
                orphans += 1
                orphan_bytes += blob.size or 0
                writer.writerow([
                    blob.name,
                    blob.size or 0,
                    blob.last_modified.isoformat() if blob.last_modified else "",
                    str(local),
                ])
            if scanned % 2000 == 0:
                print(f"[SCAN] {scanned} 件走査, 孤児 {orphans} 件 ({orphan_bytes/1024/1024/1024:.2f} GB)")
            if args.limit and scanned >= args.limit:
                print(f"[SCAN] --limit {args.limit} に到達したため打ち切り")
                break

    print("=" * 60)
    print(f"[DONE] 走査: {scanned} 件 / 孤児: {orphans} 件 / 回収可能: {orphan_bytes/1024/1024/1024:.2f} GB")
    print(f"[DONE] レポート: {out_path}")
    print("[NOTE] このスクリプトは削除を行いません。掃除はレポート確認後に別途判断してください。")


if __name__ == "__main__":
    main()
