"""規程インデックスから用語カタログを自動抽出してBlobへ置く。

「手当の一覧は？」のような一覧系質問は、検索上位の数チャンクに載っている
種類しか答えられない(役職手当が抜ける等)。対策として、規程本文から
「○○手当」「○○休暇」等のカテゴリ用語を機械的に総ざらいし、
lw-raw/kitei_terms.json に保存する。AI Q&A(ai_qa.py)がこれを読み、
一覧系質問では実在する全種類を検索語・回答の両方に使う。

抽出元は規程本文そのもの(GPTの推測ではない)ため人の精査は不要。
規程改定で run_kitei_index.bat を実行するたびに自動で更新される。

使い方:
    python tools/export_kitei_terms.py            # 抽出してBlobへアップロード
    python tools/export_kitei_terms.py --dry-run  # 抽出結果の表示のみ

環境変数: AZURE_SEARCH_*(rag.config経由), AZURE_BLOB_CONNECTION_STRING
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
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
BLOB_NAME = "kitei_terms.json"

# カテゴリ: 表示名 → 抽出正規表現(規程本文中の「○○＋語尾」を拾う)
CATEGORIES = {
    "手当": re.compile(r"[一-龠ァ-ヶーA-Za-z]{1,8}手当"),
    "休暇": re.compile(r"[一-龠ァ-ヶーA-Za-z]{1,8}休暇"),
    "休業": re.compile(r"[一-龠ァ-ヶーA-Za-z]{1,8}休業"),
    "休職": re.compile(r"[一-龠ァ-ヶーA-Za-z]{0,8}休職"),
}
# 抽出ノイズ(語として成立していない断片・指示語付き)の除外
STOPWORDS = {"該当", "当該", "本手当", "ブル休暇", "週間休業", "等休暇"}


def main() -> int:
    ap = argparse.ArgumentParser(description="規程用語カタログの抽出・登録")
    ap.add_argument("--dry-run", action="store_true", help="Blobへ書かず表示のみ")
    args = ap.parse_args()

    from azure.core.credentials import AzureKeyCredential
    from azure.search.documents import SearchClient
    from rag.config import SEARCH_INDEX_NAME, ensure_search_credentials

    ep, key = ensure_search_credentials()
    client = SearchClient(ep, SEARCH_INDEX_NAME, AzureKeyCredential(key))

    counters: dict[str, Counter] = {k: Counter() for k in CATEGORIES}
    kitei_names: set[str] = set()
    chunks = 0
    for r in client.search("*", filter="media_type eq 'kitei'",
                           select=["workno_name", "content_text"], top=1000):
        chunks += 1
        kitei_names.add((r.get("workno_name") or "").strip())
        text = r.get("content_text") or ""
        for cat, rx in CATEGORIES.items():
            for m in rx.findall(text):
                if m.startswith("当該"):
                    m = m[2:]  # 指示語を剥がす(当該育児休業→育児休業)
                if m in STOPWORDS or len(m) <= len(cat):
                    continue  # 「手当」単体などカテゴリ語そのものは除外
                counters[cat][m] += 1

    if chunks == 0:
        print("[ERROR] 規程チャンクが見つかりません(kitei未取り込み?)", file=sys.stderr)
        return 1

    catalog = {
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "chunks": chunks,
        "規程名": sorted(n for n in kitei_names if n),
        **{cat: [w for w, _ in counters[cat].most_common()] for cat in CATEGORIES},
    }

    print(f"[SCAN] 規程チャンク {chunks} 件から抽出:")
    for cat in CATEGORIES:
        print(f"  {cat}: {len(catalog[cat])}種 … {', '.join(catalog[cat][:10])}"
              + (" ほか" if len(catalog[cat]) > 10 else ""))

    if args.dry_run:
        print("[DRY-RUN] Blobへは書き込みません")
        return 0

    conn = os.environ.get("AZURE_BLOB_CONNECTION_STRING", "")
    if not conn:
        print("ERROR: AZURE_BLOB_CONNECTION_STRING が未設定です", file=sys.stderr)
        return 1
    from azure.storage.blob import BlobServiceClient
    blob = BlobServiceClient.from_connection_string(conn) \
        .get_blob_client(CONTAINER, BLOB_NAME)
    blob.upload_blob(
        json.dumps(catalog, ensure_ascii=False, indent=1).encode("utf-8"),
        overwrite=True)
    print(f"[DONE] 用語カタログを更新: {CONTAINER}/{BLOB_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
