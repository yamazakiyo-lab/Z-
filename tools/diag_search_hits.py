"""検索ヒット診断: クエリがインデックスのどこに当たっているかを可視化する。

QUOTATION SEARCH の精度調査用。ヒット箇所をハイライト(【】)で表示し、
フィールドのアナライザ設定と、クエリ/本文がどうトークン分割されるかも出す。

実行(デスクトップ):
    python tools/diag_search_hits.py ツメ
    python tools/diag_search_hits.py ツメ --top 10
    python tools/diag_search_hits.py "ISSリアライズ" --type mitsumori
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def main() -> int:
    ap = argparse.ArgumentParser(description="検索ヒット箇所の診断")
    ap.add_argument("query")
    ap.add_argument("--type", default="mitsumori", help="media_typeフィルタ(空文字で全体)")
    ap.add_argument("--top", type=int, default=5)
    args = ap.parse_args()

    from azure.core.credentials import AzureKeyCredential
    from azure.search.documents import SearchClient
    from azure.search.documents.indexes import SearchIndexClient
    from azure.search.documents.indexes.models import AnalyzeTextOptions
    from rag.config import SEARCH_INDEX_NAME, ensure_search_credentials

    endpoint, api_key = ensure_search_credentials()
    cred = AzureKeyCredential(api_key)

    # ── 1. フィールドのアナライザ設定 ─────────────────────────────────────
    idx_client = SearchIndexClient(endpoint, cred)
    index = idx_client.get_index(SEARCH_INDEX_NAME)
    print("=== フィールド設定(searchableのみ) ===")
    searchable = []
    for f in index.fields:
        if getattr(f, "searchable", False):
            searchable.append(f.name)
            print(f"  {f.name}: analyzer={f.analyzer_name or '(既定=standard.lucene)'}")

    # ── 2. クエリのトークン分割(各アナライザでどう切れるか) ──────────────
    print(f"\n=== クエリ「{args.query}」のトークン分割 ===")
    for an in ["standard.lucene", "ja.lucene", "ja.microsoft"]:
        try:
            toks = idx_client.analyze_text(
                SEARCH_INDEX_NAME, AnalyzeTextOptions(text=args.query, analyzer_name=an))
            print(f"  {an}: {[t.token for t in toks]}")
        except Exception as e:
            print(f"  {an}: [失敗] {e}")

    # ── 3. 実検索+ハイライト ─────────────────────────────────────────────
    client = SearchClient(endpoint, SEARCH_INDEX_NAME, cred)
    kw = {}
    if args.type:
        kw["filter"] = f"media_type eq '{args.type}'"
    results = client.search(
        search_text=args.query, top=args.top,
        highlight_fields=",".join(searchable),
        highlight_pre_tag="【", highlight_post_tag="】",
        select=["file_name", "workno_name", "capture_date_raw"], **kw)
    print(f"\n=== 検索結果(上位{args.top}件)とヒット箇所 ===")
    n = 0
    for r in results:
        n += 1
        print(f"\n--- #{n} score={r['@search.score']:.2f} "
              f"{r.get('capture_date_raw') or ''} {r.get('workno_name') or ''}")
        print(f"    file: {r.get('file_name')}")
        hl = r.get("@search.highlights") or {}
        if not hl:
            print("    (ハイライトなし=どこに当たったか不明)")
        for field, frags in hl.items():
            for frag in frags[:3]:
                frag = frag.replace("\n", " ")[:160]
                print(f"    [{field}] …{frag}…")
    if n == 0:
        print("  ヒットなし")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
