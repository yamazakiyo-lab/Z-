"""T-NEXUS修正依頼用: 工番マスタの全角（）を含む行の一覧を出力する。

工事一覧表.csv・発注者一覧表.csvから、全角（）を含む行を
「ID / 該当項目 / 修正後の案(半角化)」の形で出力する。
T-NEXUS側で名称を手修正するための作業リスト。

実行(デスクトップ):
    python tools/list_master_zenkaku.py > T-NEXUS修正リスト_全角括弧.txt
"""
from __future__ import annotations

import csv
import os
import re
import sys
from pathlib import Path

MASTERS_DIR = Path(os.environ.get(
    "MASTERS_DIR",
    r"Z:\takachiho\2to9_業務別フォルダ\91_工番別実績写真・動画\_masters"))
RX_ZK = re.compile(r"[（）]")


def _read_csv(p: Path) -> list[list[str]]:
    for enc in ("utf-8-sig", "cp932", "utf-8"):
        try:
            with open(p, encoding=enc, newline="") as f:
                return list(csv.reader(f))
        except (UnicodeDecodeError, UnicodeError):
            continue
    return []


def main() -> int:
    print("T-NEXUS 名称修正リスト: 全角（）→半角() に直す行")
    print("(該当項目の列に全角括弧が含まれています。修正後案のとおり半角に変更)")
    for name in ("工事一覧表.csv", "発注者一覧表.csv"):
        p = MASTERS_DIR / name
        if not p.exists():
            print(f"[WARN] 見つかりません: {p}", file=sys.stderr)
            continue
        rows = _read_csv(p)
        header = rows[0] if rows else []
        hits = 0
        print(f"\n{'=' * 70}\n■ {name}\n{'=' * 70}")
        for row in rows[1:]:
            bad = [(i, c) for i, c in enumerate(row) if c and RX_ZK.search(c)]
            if not bad:
                continue
            hits += 1
            rid = "/".join(row[:2])
            print(f"\n[{hits}] ID: {rid}")
            for i, c in bad:
                col = header[i] if i < len(header) and header[i] else f"{i + 1}列目"
                print(f"    {col}: {c}")
                print(f"    → 修正後: {c.replace('（', '(').replace('）', ')')}")
        print(f"\n--- {name}: 合計 {hits} 行 ---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
