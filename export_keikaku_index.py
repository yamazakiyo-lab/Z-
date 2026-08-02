"""中期経営計画書をAI検索インデックスに取り込む。

AI Q&Aが「会社の経営方針は？」「重点施策は？」「MF-TOKYOって何を目指してる？」
などの質問に、経営計画書を根拠に章名付きで答えられるようにする。

対象: KEIKAKU_DIR (既定: Y:\\経営企画\\中期経営計画\\中期経営計画26-29) 配下のうち
      ファイル名が「中期経営計画書*.docx」のものだけ(KEIKAKU_GLOBで変更可)。
      同じフォルダにある個人宛・人事関連などの文書を誤って公開しないための
      ホワイトリスト方式(2026-08-02)。複数年版は全て取り込む。
方式: 【…】見出しで章分割し、長い章は番号見出し(1）/1．)でさらに分割 →
      photo-index に media_type="keikaku" で全量入れ替えupsert。
      改定時に再実行するだけでよい(run_keikaku_index.bat)。

実行:
    python export_keikaku_index.py            # 取り込み
    python export_keikaku_index.py --dry-run  # 分割の確認のみ
    python export_keikaku_index.py --file "C:\\path\\to\\計画書.docx"  # 単発指定
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

KEIKAKU_DIR = Path(os.environ.get(
    "KEIKAKU_DIR", r"Y:\経営企画\中期経営計画\中期経営計画26-29"))
# 取り込むファイル名のパターン(これ以外は読まない。個人宛文書等の誤公開防止)
KEIKAKU_GLOB = os.environ.get("KEIKAKU_GLOB", "中期経営計画書*.docx")
MEDIA_TYPE = "keikaku"
CHUNK_MAX = 1000
BATCH = 100

RX_CHAPTER = re.compile(r"^【.+】$")
RX_NUMBERED = re.compile(r"^\d+[）\)\.．]")


def _read_docx(path: Path) -> list[str]:
    import docx
    d = docx.Document(str(path))
    return [p.text.strip() for p in d.paragraphs if p.text.strip()]


def _split_chapters(paras: list[str]) -> list[tuple[str, str]]:
    """【…】見出しで (章タイトル, 本文) に分割。冒頭は「はじめに」。"""
    chapters: list[tuple[str, str]] = []
    title = "はじめに"
    buf: list[str] = []
    for line in paras:
        if RX_CHAPTER.match(line):
            if buf:
                chapters.append((title, "\n".join(buf)))
            title = line.strip("【】")
            buf = []
        else:
            buf.append(line)
    if buf:
        chapters.append((title, "\n".join(buf)))
    return chapters


def _chunk(body: str) -> list[str]:
    """長い章は番号見出し(1）…/1．…)を境に CHUNK_MAX を目安に分割。"""
    if len(body) <= CHUNK_MAX:
        return [body]
    out, cur = [], ""
    for line in body.split("\n"):
        if cur and RX_NUMBERED.match(line) and len(cur) > 300:
            out.append(cur.strip())
            cur = line
        elif cur and len(cur) + len(line) > CHUNK_MAX:
            out.append(cur.strip())
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur.strip():
        out.append(cur.strip())
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="経営計画書のインデックス取り込み")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--file", help="単発のdocxパス(既定はKEIKAKU_DIR配下を全走査)")
    args = ap.parse_args()

    if args.file:
        files = [Path(args.file)]
    else:
        if not KEIKAKU_DIR.is_dir():
            print(f"[ERROR] 経営計画フォルダが見つかりません: {KEIKAKU_DIR}\n"
                  f"        フォルダを作成してdocxを置くか、--file で指定してください。",
                  file=sys.stderr)
            return 1
        files = sorted(p for p in KEIKAKU_DIR.rglob(KEIKAKU_GLOB)
                       if not p.name.startswith("~$"))
        skipped = sorted(p.name for p in KEIKAKU_DIR.rglob("*.docx")
                         if not p.name.startswith("~$") and p not in files)
        if skipped:
            print(f"[SKIP] パターン外のため対象外: {', '.join(skipped)}")
    if not files:
        print("[ERROR] 対象docxがありません", file=sys.stderr)
        return 1

    indexed_at = datetime.now(timezone.utc).isoformat()
    docs = []
    for f in files:
        doc_title = f.stem
        chapters = _split_chapters(_read_docx(f))
        print(f"[SCAN] {f.name}: {len(chapters)}章")
        for ci, (title, body) in enumerate(chapters):
            for i, ch in enumerate(_chunk(body)):
                docs.append({
                    "id": hashlib.sha256(
                        f"keikaku::{doc_title}::{ci}::{i}".encode()).hexdigest(),
                    "file_path": str(f),
                    "file_name": f.name,
                    "workno": "",
                    "workno_name": f"{doc_title}/{title}",  # 参照表示用
                    "phase": "",
                    "media_type": MEDIA_TYPE,
                    "capture_date": None,
                    "capture_date_raw": "",
                    "extension": ".docx",
                    "folder_path": str(f.parent),
                    "indexed_at": indexed_at,
                    "content_text": f"【{doc_title}/{title}】\n{ch}",
                })
            print(f"  - {title}")
    print(f"[TOTAL] {len(docs)} チャンク")
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
        print(f"[DELETE] 旧経営計画チャンク {len(old_ids)} 件を削除")

    for i in range(0, len(docs), BATCH):
        client.merge_or_upload_documents(docs[i:i + BATCH])
    print(f"[UPLOAD] {len(docs)} チャンクを登録完了")
    print("[DONE] AI Q&Aが経営計画を根拠に答えられるようになりました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
