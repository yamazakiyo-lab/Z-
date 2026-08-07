"""過去リネームの残骸「)_-」→「)-」修復(フォルダ名・ファイル名)。

例: SMX-II-S2-4000(2)_-250-130仕様書.doc → SMX-II-S2-4000(2)-250-130仕様書.doc
    (型式の仕様数字が旧リネームで「_」区切りされた残骸を戻す)

パターンは「)の直後の _-」に限定。「Takachiho_-_...」「山本NS2-3000_2_-2020...」の
ように ) 以外の後の「_-」は元々の名前とみなして触らない。

実行(デスクトップ):
    python tools/rename_paren_debris.py            # dry-run
    python tools/rename_paren_debris.py --apply    # 実行
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT_ROOT = os.environ.get("ZENKAKU_ROOT", r"Z:\takachiho")
SKIP_DIRS = {"$RECYCLE.BIN", "System Volume Information", ".runtime"}


import re

RX_E_DIGIT = re.compile(r"\)_E(?=\d)")      # )_E12345 → )E_12345 (旧番号は_を残す)
RX_E_US = re.compile(r"\)_E_(?=\d)")        # )_E_130108 → )E_130108
RX_E_CJK = re.compile(r"\)_E(?=[぀-ヿ㐀-䶿一-鿿])")  # )_E電気… → )E電気…


def _fix(name: str) -> str:
    name = name.replace(")_-", ")-")
    name = name.replace(")_(E)", ")E")   # (1)_(E)中古機 → (1)E中古機
    name = name.replace(")_用", ")用")    # (2)_用TCV → (2)用TCV
    name = RX_E_US.sub(")E_", name)
    name = RX_E_DIGIT.sub(")E_", name)
    name = RX_E_CJK.sub(")E", name)
    return name


def main() -> int:
    ap = argparse.ArgumentParser(description="「)_-」残骸の修復")
    ap.add_argument("--apply", action="store_true", help="実際に改名する(無指定はdry-run)")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    args = ap.parse_args()
    root = Path(args.root)
    if not root.is_dir():
        print(f"[ERROR] {root}", file=sys.stderr)
        return 1

    per_top: dict[str, int] = defaultdict(int)
    renamed = skipped = planned = 0
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
