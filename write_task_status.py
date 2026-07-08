"""タスク実行結果を Azure Blob に書くヘルパースクリプト。
各 PowerShell スクリプトの末尾から呼び出す。

使い方:
  py write_task_status.py --task gdx --status PASS
  py write_task_status.py --task lw_send --status FAIL --message "exit code 1"
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# .env 読み込み
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

BLOB_CONTAINER = "lw-raw"
STATUS_PREFIX   = "task_status/"

TASK_LABELS = {
    "gdx":     "GDX・RAG・AzCopy",
    "other":   "91フォルダ以外整理",
    "lw":      "LW Blob同期",
    "lw_send": "LW送信（朝/昼）",
}


def _get_container():
    conn_str = os.environ.get("AZURE_BLOB_CONNECTION_STRING")
    if not conn_str:
        print("ERROR: AZURE_BLOB_CONNECTION_STRING が未設定です", file=sys.stderr)
        sys.exit(1)
    from azure.storage.blob import BlobServiceClient
    client = BlobServiceClient.from_connection_string(conn_str)
    return client.get_container_client(BLOB_CONTAINER)


def main():
    parser = argparse.ArgumentParser(description="タスクステータスを Blob に書く")
    parser.add_argument("--task",    required=True, choices=list(TASK_LABELS.keys()),
                        help="タスク名 (gdx / other / lw / lw_send)")
    parser.add_argument("--status",  required=True,
                        choices=["PASS", "FAIL", "SKIP", "TIMEOUT", "ERROR", "UNKNOWN"],
                        help="実行結果")
    parser.add_argument("--message", default="", help="追加メッセージ（エラー内容など）")
    parser.add_argument("--host",    default=os.environ.get("COMPUTERNAME", "unknown"),
                        help="実行ホスト名")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    payload = {
        "task":       args.task,
        "label":      TASK_LABELS[args.task],
        "status":     args.status,
        "message":    args.message,
        "host":       args.host,
        "updated_at": now.isoformat(),
    }

    blob_name = f"{STATUS_PREFIX}{args.task}.json"
    try:
        container = _get_container()
        container.upload_blob(
            blob_name,
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            overwrite=True,
        )
        print(f"[task_status] {args.task}: {args.status} → Blob 保存完了 ({blob_name})")
    except Exception as e:
        # ステータス書き込み失敗はタスク自体の失敗ではないので警告のみ
        print(f"[task_status] WARNING: Blob 書き込み失敗 ({e})", file=sys.stderr)


if __name__ == "__main__":
    main()
