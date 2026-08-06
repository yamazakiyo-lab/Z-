"""全角（）と リネーム残骸(「_-」等)の調査(読み取りのみ)。

目的: ENSPACE処理に全角→半角変換を全体適用する前の安全確認。
  1. 工番マスタ(工事一覧表.csv / 発注者一覧表.csv)の全角（）・全角英数・全角スペース
     を洗い出す(マスタが全角のままだと改名往復が起きるため、先にマスタを直す)。
  2. 2to9配下のフォルダ名・ファイル名から「_-」「-_」残骸と全角（）を検出する。

実行(デスクトップ):
    python tools/survey_zenkaku_and_debris.py            # マスタ+ファイル名の両方
    python tools/survey_zenkaku_and_debris.py --masters  # マスタのみ(速い)
    python tools/survey_zenkaku_and_debris.py > zenkaku_report.txt
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

MASTERS_DIR = Path(os.environ.get(
    "MASTERS_DIR",
    r"Z:\takachiho\2to9_業務別フォルダ\91_工番別実績写真・動画\_masters"))
SCAN_ROOT = Path(os.environ.get("SCAN_ROOT", r"Z:\takachiho\2to9_業務別フォルダ"))

RX_ZK_PAREN = re.compile(r"[（）]")
RX_ZK_ALNUM = re.compile(r"[Ａ-Ｚａ-ｚ０-９]")
RX_ZK_SPACE = re.compile(r"　")
RX_DEBRIS = re.compile(r"_-|-_|\(_|_\)|__")


def _read_csv(p: Path) -> list[list[str]]:
    for enc in ("utf-8-sig", "cp932", "utf-8"):
        try:
            with open(p, encoding=enc, newline="") as f:
                return list(csv.reader(f))
        except (UnicodeDecodeError, UnicodeError):
            continue
    return []


def survey_masters() -> None:
    print("=" * 60)
    print("【1】工番マスタの全角チェック")
    print("=" * 60)
    for name in ("工事一覧表.csv", "発注者一覧表.csv"):
        p = MASTERS_DIR / name
        if not p.exists():
            print(f"[WARN] 見つかりません: {p}")
            continue
        rows = _read_csv(p)
        hits: dict[str, list[str]] = defaultdict(list)
        for row in rows[1:]:
            line = ",".join(c for c in row if c)
            if RX_ZK_PAREN.search(line):
                hits["全角（）"].append(line[:90])
            if RX_ZK_ALNUM.search(line):
                hits["全角英数字"].append(line[:90])
            if RX_ZK_SPACE.search(line):
                hits["全角スペース"].append(line[:90])
        print(f"\n--- {name} (全{len(rows) - 1:,}行) ---")
        if not hits:
            print("  全角（）・全角英数・全角スペースなし ✅")
        for kind, lines in hits.items():
            print(f"  {kind}: {len(lines):,} 行")
            for s in lines[:10]:
                print(f"    {s}")
            if len(lines) > 10:
                print(f"    …ほか{len(lines) - 10:,}行")


def survey_names() -> None:
    print()
    print("=" * 60)
    print(f"【2】ファイル名・フォルダ名の残骸/全角（） ({SCAN_ROOT})")
    print("=" * 60)
    debris: dict[str, list[str]] = defaultdict(list)
    zparen: dict[str, list[str]] = defaultdict(list)
    n = 0
    for dirpath, dirs, files in os.walk(SCAN_ROOT):
        # システムフォルダ(_MIExtraction等)は対象外
        dirs[:] = [d for d in dirs if not d.startswith("_")]
        try:
            top = Path(dirpath).relative_to(SCAN_ROOT).parts
            top = top[0] if top else "(直下)"
        except ValueError:
            top = "(不明)"
        for name in dirs + files:
            n += 1
            if RX_DEBRIS.search(name):
                debris[top].append(str(Path(dirpath) / name))
            if RX_ZK_PAREN.search(name):
                zparen[top].append(str(Path(dirpath) / name))

    print(f"\n走査対象: {n:,} 件\n")
    for title, d in (("「_-」「-_」「__」等の残骸", debris), ("全角（）を含む名前", zparen)):
        total = sum(len(v) for v in d.values())
        print(f"--- {title}: {total:,} 件 ---")
        for top in sorted(d, key=lambda k: -len(d[k])):
            print(f"  [{top}] {len(d[top]):,} 件")
            for s in d[top][:5]:
                print(f"    {s}")
            if len(d[top]) > 5:
                print(f"    …ほか{len(d[top]) - 5:,}件")
        print()


def main() -> int:
    ap = argparse.ArgumentParser(description="全角（）とリネーム残骸の調査")
    ap.add_argument("--masters", action="store_true", help="マスタCSVのみ調査")
    args = ap.parse_args()
    survey_masters()
    if not args.masters:
        survey_names()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
