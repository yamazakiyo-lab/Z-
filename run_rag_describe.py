"""GPT-4o Vision による画像説明文バッチ生成スクリプト。

使い方:
    # まず 50 件だけ試す（コスト確認）
    python run_rag_describe.py --limit 50

    # 全件処理（新規のみ）
    python run_rag_describe.py

    # 処理済み件数を確認するだけ
    python run_rag_describe.py --status

    # 失敗・スキップ済みを再処理
    python run_rag_describe.py --retry-failed

    # プロンプト改善後に全件再生成（旧バージョン分を再処理）
    python run_rag_describe.py --re-describe

バージョン管理:
    describer.py の PROMPT_VERSION を上げると、次回 --re-describe 実行時に
    旧バージョンで生成されたすべての説明文が再生成対象になる。
    通常実行（フラグなし）は新規ファイルのみ処理するため、デイリーランに影響しない。

コスト目安（2025年時点）:
    GPT-4o 画像解析: 約 $0.003〜0.01 / 枚（high detail モード）
    10,000枚 ≒ $30〜100 程度
    まず --limit 10 で1件あたりの時間とコストを確認することを推奨。

注意:
    - 動画ファイルは Vision 非対応のため自動スキップ
    - HEIC/HEIF は organizer.py で JPG 変換済みが前提
    - Azure OpenAI の.env 設定が必要
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Dict


def _extract_job_number(fp: str) -> str:
    """ファイルパスから工番（TARGET_91_ROOT 直下のフォルダ名）を抽出する。"""
    try:
        from rag.config import TARGET_91_ROOT
        rel = Path(fp).relative_to(TARGET_91_ROOT)
        return rel.parts[0] if rel.parts else ""
    except (ValueError, Exception):
        return ""


def _is_current_version(value: str, version: str) -> bool:
    """説明文が現在のプロンプトバージョンで生成されたか判定する。"""
    return value.startswith(f"{version}|")


def _strip_version(value: str, version: str) -> str:
    """説明文からバージョン接頭辞を除去して返す（AI Search 送信用）。"""
    prefix = f"{version}|"
    return value[len(prefix):] if value.startswith(prefix) else value


def main() -> None:
    parser = argparse.ArgumentParser(description="GPT-4o Vision による画像説明文バッチ生成")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="処理件数の上限（省略時は対象を全件処理）",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="処理状況を表示して終了（生成処理は行わない）",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="スキップ済み（失敗・空文字）のファイルを再処理対象に含める",
    )
    parser.add_argument(
        "--re-describe",
        action="store_true",
        help="旧バージョンのプロンプトで生成された説明文を全件再生成する（プロンプト改善後に使用）",
    )
    args = parser.parse_args()

    try:
        from rag.indexer import load_manifest
        from rag.describer import (
            load_descriptions, save_descriptions, describe_image,
            VISION_SUPPORTED_EXT, PROMPT_VERSION,
        )
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
    current_ver  = sum(1 for v in descriptions.values() if _is_current_version(v, PROMPT_VERSION))
    old_ver      = sum(1 for v in descriptions.values() if v and not _is_current_version(v, PROMPT_VERSION))
    failed       = sum(1 for v in descriptions.values() if v == "")
    pending      = sum(
        1 for doc_id, fp in manifest.items()
        if doc_id not in descriptions
        and Path(fp).suffix.lower() in VISION_SUPPORTED_EXT
    )

    print(f"[STATUS] 総ファイル数:          {total}")
    print(f"[STATUS] 現バージョン({PROMPT_VERSION})済み: {current_ver}")
    print(f"[STATUS] 旧バージョン済み:      {old_ver}（--re-describe で再生成対象）")
    print(f"[STATUS] 失敗・スキップ済み:    {failed}（--retry-failed で再試行可）")
    print(f"[STATUS] 未処理:               {pending}")

    if args.status:
        return

    # ── 処理対象を抽出 ────────────────────────────────────────────────────────
    if args.re_describe:
        # 旧バージョン + 失敗 + 未処理 を全部対象に
        targets = [
            (doc_id, fp)
            for doc_id, fp in manifest.items()
            if not _is_current_version(descriptions.get(doc_id, ""), PROMPT_VERSION)
            and Path(fp).suffix.lower() in VISION_SUPPORTED_EXT
        ]
        print(f"[INFO] --re-describe: 旧バージョン {old_ver} 件 + 失敗 {failed} 件 + 未処理 {pending} 件を対象にします")
    elif args.retry_failed:
        targets = [
            (doc_id, fp)
            for doc_id, fp in manifest.items()
            if (doc_id not in descriptions or descriptions[doc_id] == "")
            and Path(fp).suffix.lower() in VISION_SUPPORTED_EXT
        ]
        print(f"[INFO] --retry-failed: 未処理 {pending} 件 + 失敗済み {failed} 件を対象にします")
    else:
        # 通常実行: 新規ファイルのみ（デイリーラン用）
        targets = [
            (doc_id, fp)
            for doc_id, fp in manifest.items()
            if doc_id not in descriptions
            and Path(fp).suffix.lower() in VISION_SUPPORTED_EXT
        ]

    if not targets:
        print("[INFO] 処理対象ファイルなし。完了。")
        return

    if args.limit:
        targets = targets[: args.limit]

    print(f"[START] {len(targets)} 件を処理します（プロンプトバージョン: {PROMPT_VERSION}）")
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
    failed_count = 0
    merge_batch: list = []

    for i, (doc_id, fp) in enumerate(targets, 1):
        image_path = Path(fp)

        # ネットワーク切断リトライ
        file_ok = False
        for _attempt in range(5):
            try:
                file_ok = image_path.exists()
                break
            except OSError as e:
                print(f"\n[NETWORK] Z:ドライブ切断検知 ({e}). 30秒後リトライ...", file=sys.stderr)
                time.sleep(30)
        else:
            print(f"\n[ERROR] ネットワーク復旧せず。進捗を保存して終了します。")
            save_descriptions(descriptions)
            sys.exit(1)

        if not file_ok:
            print(f"[SKIP] ファイルが存在しません: {fp}")
            descriptions[doc_id] = ""
            continue

        job_number = _extract_job_number(fp)
        label = f"[{i}/{len(targets)}] {image_path.name}"
        if job_number:
            label += f" (工番:{job_number})"
        print(f"{label} ...", end=" ", flush=True)
        start = time.time()

        content = describe_image(image_path, job_number=job_number)
        elapsed = time.time() - start

        if content:
            print(f"OK ({elapsed:.1f}s): {content[:50]}...")
            # バージョン接頭辞付きで保存
            descriptions[doc_id] = f"{PROMPT_VERSION}|{content}"
            # AI Search には接頭辞なしで送信
            merge_batch.append({"id": doc_id, "content_text": content})
            processed += 1
        else:
            print(f"SKIP")
            descriptions[doc_id] = ""
            failed_count += 1

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

    print(f"\n[DONE] 処理完了: 成功={processed}, スキップ/失敗={failed_count}")
    print(
        f"[NEXT] 次回 run_rag_index.py を実行すると\n"
        f"       説明文が AI Search の content_text フィールドに反映されます。"
    )


if __name__ == "__main__":
    main()
