"""RAG インデックス更新スクリプト（手動実行 / バッチ自動実行兼用）。

使い方:
    # 手動実行（初回 or 動作確認時）
    python run_rag_index.py

    # 対象フォルダを指定して実行
    python run_rag_index.py --root "Z:\\takachiho\\2to9_業務別フォルダ\\91_工番別実績写真・動画"

    # ドライラン（スキャンのみ、AI Search への書き込みなし）
    python run_rag_index.py --dry-run

自動実行への組み込み方:
    動作確認が完了したら run_gdx.py の main() 末尾に以下を追加する:

        import subprocess
        subprocess.run([sys.executable, "run_rag_index.py"], check=False)

    ※ 初回は時間がかかるため、必ず手動実行で完了を確認してから組み込むこと。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Zフォルダ写真インデックス更新")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="スキャン対象フォルダ（省略時は config の TARGET_91_ROOT）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="スキャンのみ実行し、AI Search への書き込みを行わない",
    )
    args = parser.parse_args()

    if args.dry_run:
        # ドライラン: スキャンだけして件数を表示
        _dry_run(args.root)
        return

    # 本番実行
    try:
        from rag.indexer import PhotoIndexer
    except ImportError as e:
        print(
            f"[ERROR] rag パッケージのインポートに失敗しました。\n"
            f"  依存ライブラリをインストールしてください:\n"
            f"    pip install azure-search-documents python-dotenv\n"
            f"  詳細: {e}",
            file=sys.stderr,
        )
        sys.exit(1)

    start = time.time()
    try:
        indexer = PhotoIndexer()
        indexer.run(root=args.root)
    except EnvironmentError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] インデックス更新中にエラーが発生しました: {e}", file=sys.stderr)
        raise

    elapsed = time.time() - start
    print(f"[TIME] 実行時間: {elapsed:.1f} 秒")


def _dry_run(root: Path | None) -> None:
    """スキャンのみ実行し、対象ファイル一覧と件数を表示する。"""
    from rag.config import TARGET_91_ROOT
    from rag.indexer import scan_media_files

    target = root or TARGET_91_ROOT
    print(f"[DRY RUN] スキャン対象: {target}")

    count = 0
    phase_count: dict[str, int] = {}
    media_count: dict[str, int] = {}

    for doc in scan_media_files(target):
        count += 1
        phase_count[doc["phase"]] = phase_count.get(doc["phase"], 0) + 1
        media_count[doc["media_type"]] = media_count.get(doc["media_type"], 0) + 1
        if count <= 5:
            print(f"  {doc['file_path']}")
        elif count == 6:
            print("  ...")

    print(f"\n[DRY RUN] スキャン結果:")
    print(f"  総ファイル数: {count}")
    print(f"  フェーズ別: {phase_count}")
    print(f"  種別: {media_count}")
    print("[DRY RUN] AI Search への書き込みはスキップしました")


if __name__ == "__main__":
    main()
