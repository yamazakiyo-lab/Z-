"""工事一覧表.csv から工番マスタ(workno_master.json)を作り、lw-raw Blob にアップロードする。

目的:
    受信Bot(Azure App Service)は社内NAS(Z:)を直接見られないため、
    工番→工事名・納入先のマスタを Blob 経由で渡す。Bot は写真投稿時の
    工番入力チェック(打ち間違い検知/新規工番確認)にこれを使う。

実行:
    KEIRI-PC(Z: が見える環境)で、デイリーランの最後に日次実行する。
    python export_workno_master.py

出力 Blob:
    lw-raw/workno_master.json
    形式: {"generated_at": ISO8601, "count": N,
           "worknos": {"3967-00": {"name": "...", "client": "大成"}, ...}}

環境変数(.env):
    AZURE_BLOB_CONNECTION_STRING  Blob 接続文字列
    LW_BLOB_CONTAINER             コンテナ名(省略時: lw-raw)
    TARGET_91_ROOT                91ルート(工事一覧表.csv の親 _GDExtraction を含む)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"), encoding="utf-8")
except Exception:
    pass

try:
    from azure.storage.blob import BlobServiceClient
except ImportError:
    print("[ERROR] pip install azure-storage-blob python-dotenv", file=sys.stderr)
    sys.exit(1)

# indexer の CSV 読み込み(枝番対応済み)を再利用する
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rag.indexer import _load_workno_csv  # noqa: E402
from rag.config import TARGET_91_ROOT      # noqa: E402

BLOB_CONN_STR = os.environ.get("AZURE_BLOB_CONNECTION_STRING", "")
BLOB_CONTAINER = os.environ.get("LW_BLOB_CONTAINER", "lw-raw")
MASTER_BLOB = "workno_master.json"


def main() -> None:
    if not BLOB_CONN_STR:
        print("[ERROR] AZURE_BLOB_CONNECTION_STRING が未設定です", file=sys.stderr)
        sys.exit(1)

    csv_path = Path(TARGET_91_ROOT) / "_GDExtraction" / "工事一覧表.csv"
    if not csv_path.exists():
        print(f"[ERROR] 工事一覧表.csv が見つかりません: {csv_path}", file=sys.stderr)
        print("        Z: 未接続の可能性。中止します(既存のBlobマスタを保護)。", file=sys.stderr)
        sys.exit(2)

    # {workno: {"client_name": ..., "billing_name": ...}}
    workno_csv = _load_workno_csv(csv_path)
    if not workno_csv:
        print("[ERROR] マスタが空。CSV列名または内容を確認してください。中止します。", file=sys.stderr)
        sys.exit(3)

    worknos = {
        wn: {
            "client": info.get("client_name", ""),
            "billing": info.get("billing_name", ""),
        }
        for wn, info in workno_csv.items()
    }
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(worknos),
        "worknos": worknos,
    }

    svc = BlobServiceClient.from_connection_string(BLOB_CONN_STR)
    container = svc.get_container_client(BLOB_CONTAINER)
    container.upload_blob(
        MASTER_BLOB,
        json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        overwrite=True,
        content_type="application/json",
    )
    print(f"[DONE] 工番マスタをアップロード: {BLOB_CONTAINER}/{MASTER_BLOB} ({len(worknos)} 件)")


if __name__ == "__main__":
    main()
