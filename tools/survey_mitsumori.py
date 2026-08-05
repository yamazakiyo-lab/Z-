"""見積書フォルダ(231_見積書)の構造調査。

TSEG WORKSへの見積検索・AI Q&A連携を設計するための下見。
ファイルは読み込まず、フォルダ構成・ファイル名・種類・日付だけを集計する。

使い方(デスクトップ):
    python tools/survey_mitsumori.py
    python tools/survey_mitsumori.py --samples 30   # ファイル名サンプル数を変更
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get(
    "MITSUMORI_DIR", r"Z:\takachiho\2to9_業務別フォルダ\23_顧客・納入先別\231_見積書"))


def main() -> int:
    ap = argparse.ArgumentParser(description="見積書フォルダの構造調査")
    ap.add_argument("--samples", type=int, default=20)
    args = ap.parse_args()

    if not ROOT.is_dir():
        print(f"[ERROR] フォルダが見つかりません: {ROOT}", file=sys.stderr)
        return 1

    ext_counter: Counter = Counter()
    year_counter: Counter = Counter()
    top_dirs: Counter = Counter()
    depth_counter: Counter = Counter()
    total = 0
    total_bytes = 0
    samples: list[str] = []
    workno_hits = 0
    rx_workno = re.compile(r"\b([A-Z]{0,4}\d{3,6}-\d{2})\b", re.IGNORECASE)

    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel_dir = Path(dirpath).relative_to(ROOT)
        for fn in filenames:
            if fn.startswith("~$"):
                continue
            total += 1
            p = Path(dirpath) / fn
            ext_counter[p.suffix.lower() or "(なし)"] += 1
            try:
                st = p.stat()
                total_bytes += st.st_size
                year_counter[datetime.fromtimestamp(st.st_mtime).year] += 1
            except OSError:
                pass
            parts = rel_dir.parts
            top_dirs[parts[0] if parts else "(直下)"] += 1
            depth_counter[len(parts)] += 1
            if rx_workno.search(fn):
                workno_hits += 1
            if len(samples) < args.samples and total % 97 == 1:  # 適当に間引いて採取
                samples.append(str(rel_dir / fn))

    print(f"[ROOT] {ROOT}")
    print(f"[TOTAL] {total:,} ファイル / {total_bytes/1024/1024:,.0f} MB")
    print(f"[工番らしき文字列を含むファイル名] {workno_hits:,} 件"
          f" ({workno_hits*100//max(total,1)}%)")
    print("\n[拡張子]")
    for ext, c in ext_counter.most_common(12):
        print(f"  {ext:8} {c:6,}")
    print("\n[更新年分布]")
    for y in sorted(year_counter):
        print(f"  {y}: {year_counter[y]:,}")
    print("\n[直下フォルダ別ファイル数(上位20)]")
    for d, c in top_dirs.most_common(20):
        print(f"  {c:6,}  {d}")
    print("\n[階層の深さ分布]")
    for d in sorted(depth_counter):
        print(f"  深さ{d}: {depth_counter[d]:,}")
    print(f"\n[ファイル名サンプル({len(samples)}件)]")
    for s in samples:
        print(f"  {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
