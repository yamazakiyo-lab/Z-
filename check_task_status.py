"""デスクトップタスクのステータスをBlobから読んで表示する。
ネットワーク不問（Azure Blob に接続できれば OK）。

使い方:
  py check_task_status.py
"""
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

BLOB_CONTAINER = "lw-raw"
STATUS_PREFIX  = "task_status/"

TASKS = [
    ("gdx",     "GDX・RAG・AzCopy  "),
    ("other",   "91フォルダ以外整理"),
    ("lw",      "LW Blob同期       "),
    ("lw_send", "LW送信（朝/昼）   "),
]

STATUS_ICON = {
    "PASS":    "✅",
    "FAIL":    "❌",
    "SKIP":    "⏭",
    "TIMEOUT": "⏱",
    "ERROR":   "💥",
    "UNKNOWN": "❓",
}

JST = timezone(timedelta(hours=9))


def _get_container():
    conn_str = os.environ.get("AZURE_BLOB_CONNECTION_STRING")
    if not conn_str:
        print("ERROR: AZURE_BLOB_CONNECTION_STRING が未設定です")
        return None
    from azure.storage.blob import BlobServiceClient
    client = BlobServiceClient.from_connection_string(conn_str)
    return client.get_container_client(BLOB_CONTAINER)


def _fmt_time(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str).astimezone(JST)
        return dt.strftime("%m/%d %H:%M")
    except Exception:
        return iso_str


def main():
    container = _get_container()
    if container is None:
        return

    print()
    print("═" * 52)
    print("  デスクトップ タスク ステータス")
    print("═" * 52)

    for task_id, label in TASKS:
        blob_name = f"{STATUS_PREFIX}{task_id}.json"
        try:
            raw = container.download_blob(blob_name).readall()
            data = json.loads(raw.decode("utf-8"))
            status = data.get("status", "UNKNOWN")
            icon = STATUS_ICON.get(status, "❓")
            updated = _fmt_time(data.get("updated_at", ""))
            host = data.get("host", "")
            msg = data.get("message", "")
            line = f"  {icon} {label}  {updated}  [{host}]"
            if msg and status != "PASS":
                line += f"\n       {msg}"
            print(line)
        except Exception:
            print(f"  ❓ {label}  （データなし）")

    print("═" * 52)
    print(f"  確認時刻: {datetime.now(JST).strftime('%m/%d %H:%M:%S')} JST")
    print()


if __name__ == "__main__":
    main()
