"""現場写真コメントから「現場用語カタログ」を自動抽出してBlobへ置く。

規程用語カタログ(export_kitei_terms.py)の技術版。学習協力・写真投稿で
蓄積されたコメント(rag/comments.json)から、現場で実際に使われる
カタカナ語(部品・装置名)・型式(英数字)・作業語(○○交換など)を機械的に
総ざらいし、lw-raw/genba_terms.json に保存する。AI Q&A(ai_qa.py)が
これを読み、話し言葉の質問を現場の呼び名でも検索できるようにする。

コメントは毎晩増えるため、夜間ラン(run_mie_logged.ps1)から自動実行される。
溜まれば溜まるほどキーワード変換が現場語彙に強くなる。

使い方(デスクトップ):
    python tools/export_genba_terms.py            # 抽出してBlobへアップロード
    python tools/export_genba_terms.py --dry-run  # 抽出結果の表示のみ

環境変数: AZURE_BLOB_CONNECTION_STRING (.env から自動読込)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
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
BLOB_NAME = "genba_terms.json"
COMMENTS_PATH = ROOT / "rag" / "comments.json"

MIN_COUNT = 2  # 2回以上出た語だけ採用(打ち間違い・一回きりの語を除外)

# カタカナ語: 部品・装置名(3文字以上)
RX_KATAKANA = re.compile(r"[ァ-ヴー]{3,}")
# 型式: 英字始まりで数字を含む英数ハイフン列(NC1-110, VBOZN-BY5S など)
RX_MODEL = re.compile(r"[A-Za-z][A-Za-z\-]{0,8}[0-9][A-Za-z0-9\-]{0,14}")
# 作業語: ○○＋作業サフィックス
RX_WORK = re.compile(
    r"[一-龠ァ-ヴーA-Za-z0-9]{1,8}"
    r"(?:交換|整備|修理|組立|組付|分解|洗浄|塗装|溶接|研磨|研削|調整|点検|測定|"
    r"芯出し|払い出し|取付|取外し|加工|補修|清掃)")

STOPWORDS = {
    # 一般語・あいづち系(コメントに混ざるノイズ)
    "コメント", "ヨロシク", "オネガイ", "ナシ", "スミマセン", "アリガトウ",
    "ワカラナイ", "オツカレ", "チェック", "テスト",
    # 作業語の断片
    "の交換", "を交換", "は交換", "と交換", "の整備", "の修理", "の点検",
}


def main() -> int:
    ap = argparse.ArgumentParser(description="現場用語カタログの抽出・登録")
    ap.add_argument("--dry-run", action="store_true", help="Blobへ書かず表示のみ")
    args = ap.parse_args()

    if not COMMENTS_PATH.exists():
        print(f"[ERROR] comments.json が見つかりません: {COMMENTS_PATH}",
              file=sys.stderr)
        return 1
    comments = json.loads(COMMENTS_PATH.read_text(encoding="utf-8"))

    kata: Counter = Counter()
    model: Counter = Counter()
    work: Counter = Counter()
    n = 0
    for entry in comments.values():
        text = (entry.get("comment") or "").strip()
        if not text or text in ("なし", "？"):
            continue
        n += 1
        for m in RX_KATAKANA.findall(text):
            if m not in STOPWORDS:
                kata[m] += 1
        for m in RX_MODEL.findall(text):
            m = m.upper().rstrip("-")
            if len(m) >= 3:
                model[m] += 1
        for m in RX_WORK.findall(text):
            if m not in STOPWORDS and not m.startswith(("の", "を", "は", "と", "が")):
                work[m] += 1

    def _top(c: Counter) -> list[str]:
        return [w for w, cnt in c.most_common() if cnt >= MIN_COUNT]

    catalog = {
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "comments": n,
        "カタカナ": _top(kata),
        "型式": _top(model),
        "作業": _top(work),
    }

    print(f"[SCAN] コメント {n} 件から抽出(出現{MIN_COUNT}回以上):")
    for cat in ("カタカナ", "型式", "作業"):
        terms = catalog[cat]
        print(f"  {cat}: {len(terms)}種 … {', '.join(terms[:12])}"
              + (" ほか" if len(terms) > 12 else ""))

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
    print(f"[DONE] 現場用語カタログを更新: {CONTAINER}/{BLOB_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
