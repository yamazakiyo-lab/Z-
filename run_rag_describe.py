"""GPT-4o Vision による画像説明文バッチ生成スクリプト。

使い方:
    # まず 50 件だけ試す（コスト確認）
    python run_rag_describe.py --limit 50

    # 全件処理（時間がかかる・コスト注意）
    python run_rag_describe.py

    # 処理済み件数を確認するだけ
    python run_rag_describe.py --status

動作の流れ:
    1. manifest.json から全ファイル ID を取得
    2. descriptions.json と比較し、未処理のファイルを抽出
    3. 各画像を GPT-4o Vision で解析し説明文を生成
    4. descriptions.json に保存（途中終了しても再実行で続きから再開）
    5. Azure AI Search の当該ドキュメントを content_text フィールドのみ更新（merge）

コスト目安（2025年時点）:
    GPT-4o 画像解析: 約 $0.001〜0.003 / 枚（low detail モード）
    10,000枚 ≒ $10〜30 程度
    まず --limit 10 で1件あたりの時間とコストを確認することを推奨。

注意:
    - 動画ファイルは Vision 非対応のため自動スキップ
    - HEIC/HEIF は organizer.py で JPG 変換済みが前提
    - Azure OpenAI の.env 設定が必要:
        AZURE_OPENAI_ENDPOINT=
        AZURE_OPENAI_API_KEY=
        AZURE_OPENAI_GPT4O_DEPLOYMENT=gpt-4o  （デプロイ名）
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Dict


def main() -> None:
    parser = argparse.ArgumentParser(description="GPT-4o Vision による画像説明文バッチ生成")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="処理件数の上限（省略時は未処理を全件処理）",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="処理状況を表示して終了（生成処理は行わない）",
    )
    args = parser.parse_args()

    try:
        from rag.indexer import load_manifest
        from rag.describer import load_descriptions, save_descriptions, describe_image, VISION_SUPPORTED_EXT
        from rag.config import ensure_search_credentials, SEARCH_INDEX_NAME
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient
    except ImportError as e:
        print(
            f"[ERROR] インポート失敗: {e}\n"
            "  pip install azure-search-documents openai python-dotenv",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── 状態確認 ──────────────────────────────────────────────────────────────
    manifest = load_manifest()
    descriptions = load_descriptions()

    total = len(manifest)
    described = sum(1 for v in descriptions.values() if v)
    skipped = sum(1 for v in descriptions.values() if v == "")  # 試みたが失敗
    pending = sum(
        1 for doc_id, fp in manifest.items()
        if doc_id not in descriptions
        and Path(fp).suffix.lower() in VISION_SUPPORTED_EXT
    )

    print(f"[STATUS] 総ファイル数: {total}")
    print(f"[STATUS] 説明文あり:   {described}")
    print(f"[STATUS] スキップ済み: {skipped}（Vision非対応 or 失敗）")
    print(f"[STATUS] 未処理:       {pending}")

    if args.status:
        return

    if pending == 0:
        print("[INFO] 未処理ファイルなし。完了。")
        return

    # ── 処理対象を抽出 ────────────────────────────────────────────────────────
    targets = [
        (doc_id, fp)
        for doc_id, fp in manifest.items()
        if doc_id not in descriptions
        and Path(fp).suffix.lower() in VISION_SUPPORTED_EXT
    ]

    if args.limit:
        targets = targets[: args.limit]

    print(f"[START] {len(targets)} 件を処理します")
    if args.limit:
        print(f"        (--limit {args.limit} が指定されています)")

    # ── Azure AI Search クライアント（merge 用） ───────────────────────────────
    try:
        endpoint, api_key = ensure_search_credentials()
        search_client = SearchClient(endpoint, SEARCH_INDEX_NAME, AzureKeyCredential(api_key))
    except EnvironmentError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    # ── メイン処理ループ ──────────────────────────────────────────────────────
    processed = 0
    failed = 0
    merge_batch: list = []

    for i, (doc_id, fp) in enumerate(targets, 1):
        image_path = Path(fp)

        if not image_path.exists():
            print(f"[SKIP] ファイルが存在しません: {fp}")
            descriptions[doc_id] = ""
            continue

        print(f"[{i}/{len(targets)}] {image_path.name} ...", end=" ", flush=True)
        start = time.time()

        content = describe_image(image_path)
        elapsed = time.time() - start

        if content:
            print(f"OK ({elapsed:.1f}s): {content[:40]}...")
            descriptions[doc_id] = content
            merge_batch.append({"id": doc_id, "content_text": content})
            processed += 1
        else:
            print(f"SKIP")
            descriptions[doc_id] = ""
            failed += 1

        # ── 10件ごとに descriptions.json 保存 + AI Search merge ─────────────
        if len(merge_batch) >= 10 or i == len(targets):
            if merge_batch:
                try:
                    results = search_client.merge_documents(merge_batch)
                    ok = sum(1 for r in results if r.succeeded)
                    print(f"[MERGE] AI Search 更新: {ok}/{len(merge_batch)} 件")
                except Exception as e:
                    print(f"[WARN] AI Search merge 失敗: {e}", file=sys.stderr)
                merge_batch = []
            save_descriptions(descriptions)
            print(f"[SAVE] descriptions.json 保存 ({len(descriptions)} 件)")

        # 過負荷を避けるため少し待機
        time.sleep(0.5)

    print(f"\n[DONE] 処理完了: 成功={processed}, スキップ/失敗={failed}")
    print(
        f"[NEXT] 次回 run_rag_index.py を実行すると\n"
        f"       説明文が AI Search の content_text フィールドに反映されます。"
    )


if __name__ == "__main__":
    main()
