"""利用マニュアル(static/user_manual.md)をAI検索インデックスに取り込む。

AI Q&Aが「写真の投稿方法」「検索のやり方」などアプリの使い方の質問に、
利用マニュアルを根拠に章名付きで答えられるようにする。

方式: user_manual.md を章(##)単位でチャンク分割 → photo-index に
      media_type="manual" で全量入れ替えupsert(旧チャンクは先に削除)。
      マニュアルはアプリ更新のたびに変わるため、夜間ラン(run_mie_logged.ps1)
      から毎晩自動実行される。手動実行も可。

実行:
    python export_manual_index.py            # 取り込み
    python export_manual_index.py --dry-run  # 対象と分割数の確認のみ
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"), encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

MANUAL_PATH = Path(__file__).with_name("static") / "user_manual.md"
MEDIA_TYPE = "manual"
DOC_TITLE = "TSEG WORKS 利用マニュアル"
CHUNK_MAX = 1200
BATCH = 100


def _split_chapters(text: str) -> list[tuple[str, str]]:
    """(章タイトル, 本文) のリストに分割。## 見出し単位。"""
    chapters: list[tuple[str, str]] = []
    title = "はじめに"
    buf: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            if "".join(buf).strip():
                chapters.append((title, "\n".join(buf).strip()))
            title = m.group(1).strip()
            buf = []
        else:
            buf.append(line)
    if "".join(buf).strip():
        chapters.append((title, "\n".join(buf).strip()))
    return chapters


def _chunk(body: str) -> list[str]:
    """章が長い場合は段落境界で CHUNK_MAX を目安に分割。"""
    if len(body) <= CHUNK_MAX:
        return [body]
    out, cur = [], ""
    for para in body.split("\n\n"):
        if cur and len(cur) + len(para) > CHUNK_MAX:
            out.append(cur.strip())
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur.strip():
        out.append(cur.strip())
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="利用マニュアルのインデックス取り込み")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not MANUAL_PATH.exists():
        print(f"[ERROR] マニュアルが見つかりません: {MANUAL_PATH}", file=sys.stderr)
        return 1
    text = MANUAL_PATH.read_text(encoding="utf-8")
    chapters = _split_chapters(text)
    indexed_at = datetime.now(timezone.utc).isoformat()

    docs = []
    for ci, (title, body) in enumerate(chapters):
        for i, ch in enumerate(_chunk(body)):
            docs.append({
                "id": hashlib.sha256(f"manual::{ci}::{i}".encode("utf-8")).hexdigest(),
                "file_path": str(MANUAL_PATH),
                "file_name": "user_manual.md",
                "workno": "",
                "workno_name": title,          # 章タイトル(参照表示に使う)
                "phase": "",
                "media_type": MEDIA_TYPE,
                "capture_date": None,
                "capture_date_raw": "",
                "extension": ".md",
                "folder_path": str(MANUAL_PATH.parent),
                "indexed_at": indexed_at,
                "content_text": f"【{DOC_TITLE}/{title}】\n{ch}",
            })
    print(f"[SCAN] {len(chapters)}章 → {len(docs)}チャンク")
    for t, _ in chapters:
        print(f"  - {t}")
    if args.dry_run:
        return 0

    from azure.core.credentials import AzureKeyCredential
    from azure.search.documents import SearchClient
    from rag.config import SEARCH_INDEX_NAME, ensure_search_credentials

    endpoint, api_key = ensure_search_credentials()
    client = SearchClient(endpoint, SEARCH_INDEX_NAME, AzureKeyCredential(api_key))

    old_ids = [r["id"] for r in client.search(
        search_text="*", filter=f"media_type eq '{MEDIA_TYPE}'",
        select=["id"], top=100000)]
    if old_ids:
        for i in range(0, len(old_ids), BATCH):
            client.delete_documents([{"id": x} for x in old_ids[i:i + BATCH]])
        print(f"[DELETE] 旧マニュアルチャンク {len(old_ids)} 件を削除")

    for i in range(0, len(docs), BATCH):
        client.merge_or_upload_documents(docs[i:i + BATCH])
    print(f"[UPLOAD] {len(docs)} チャンクを登録完了")
    print("[DONE] AI Q&Aでアプリの使い方に答えられるようになりました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
