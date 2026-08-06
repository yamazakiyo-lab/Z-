"""Z全体の全角（）→半角() 一括リネーム(フォルダ名・ファイル名)。

対象: Z:\takachiho 配下のすべてのフォルダ・ファイル名(種類を問わない)。
除外: 先頭が「_」のシステムフォルダ(_masters/_MIExtraction/_annotations等)と
      その配下、$RECYCLE.BIN 等。
安全策: dry-run既定・改名先が存在する場合はスキップ・使用中はスキップ。
注意: Excelの外部リンクやショートカットは改名でリンク切れになる可能性がある。
      再発防止はマスタ読込(normalize_master_name)・ENSPACE・231ツールに組込済み。

実行(デスクトップ):
    python tools/rename_zenkaku_paren.py > paren_preview.txt   # dry-run
    python tools/rename_zenkaku_paren.py --apply               # 実行
    python tools/rename_zenkaku_paren.py --root "Z:\\takachiho\\2to9_業務別フォルダ"
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT_ROOT = os.environ.get("ZENKAKU_ROOT", r"Z:\takachiho")
SKIP_DIRS = {"$RECYCLE.BIN", "System Volume Information", ".runtime"}


def _fix(name: str) -> str:
    return name.replace("（", "(").replace("）", ")")


def main() -> int:
    ap = argparse.ArgumentParser(description="全角（）→半角() 一括リネーム")
    ap.add_argument("--apply", action="store_true", help="実際に改名する(無指定はdry-run)")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    args = ap.parse_args()
    root = Path(args.root)
    if not root.is_dir():
        print(f"[ERROR] {root}", file=sys.stderr)
        return 1

    per_top: dict[str, int] = defaultdict(int)
    renamed = skipped = planned = 0
    # topdown=False: 中身(ファイル・子フォルダ)を先に処理してから親フォルダを改名
    for dirpath, dirs, files in os.walk(root, topdown=False):
        rel_parts = Path(dirpath).relative_to(root).parts if dirpath != str(root) else ()
        if any(p.startswith("_") or p in SKIP_DIRS for p in rel_parts):
            continue
        top = rel_parts[0] if rel_parts else "(直下)"
        for name in files + [d for d in dirs
                             if not d.startswith("_") and d not in SKIP_DIRS]:
            new = _fix(name)
            if new == name:
                continue
            src = Path(dirpath) / name
            dst = Path(dirpath) / new
            planned += 1
            per_top[top] += 1
            if dst.exists():
                print(f"[SKIP] 改名先が存在: {src}")
                skipped += 1
                continue
            print(f"  {src} -> {new}")
            if args.apply:
                try:
                    src.rename(dst)
                    renamed += 1
                except OSError as e:
                    print(f"[SKIP] 失敗(使用中?): {src}: {e}")
                    skipped += 1

    print(f"\n[集計] 対象 {planned:,} 件")
    for top, n in sorted(per_top.items(), key=lambda x: -x[1]):
        print(f"  {top}: {n:,} 件")
    if args.apply:
        print(f"[DONE] 改名 {renamed:,} 件 / スキップ {skipped:,} 件")
    else:
        print("[DRY-RUN] 実行は --apply を付けてください。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
