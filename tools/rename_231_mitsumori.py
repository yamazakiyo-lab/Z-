"""231_見積書のファイル名正規化(2026-08-06ルール)。

正規形: 「名前_YYMMDD(枝番)」または台帳の「名前_YYMM～」。_は日付の前だけ。

ルール:
  R1 期間台帳: 「ISS東京25.6~」「XX2506~」→「ISS東京_2506～」(半角~→全角～)
  R2 日付直結: 「SMX-II-S2-3000(2)_中古機130108」→「SMX-II-S2-3000(2)中古機_130108」
     (型式(英数)_説明(和文) の間の _ は除去。日本語同士の _ は残す)
  R3 日付+接尾: 「江藤製作所_中古機_140530_E」→「江藤製作所_中古機_140530」
     (英字1-2文字の接尾は除去。数字接尾は枝番として日付に連結)
  R4 日付なし: ファイル更新日(mtime)で「_YYMMDD」を付与
  ※ 8桁日付(20YYMMDD)は手動対応(--list-8で一覧表示、改名しない)

実行(デスクトップ):
    python tools/rename_231_mitsumori.py                  # dry-run(全件表示)
    python tools/rename_231_mitsumori.py > preview.txt    # 一覧をファイルに保存
    python tools/rename_231_mitsumori.py --list-8         # 8桁日付の一覧のみ
    python tools/rename_231_mitsumori.py --apply          # 実際に改名
インデックスは夜間ラン(または差分実行)が自動追従する。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get(
    "MITSUMORI_DIR", r"Z:\takachiho\2to9_業務別フォルダ\23_顧客・納入先別\231_見積書"))

RX_ASCII_MODEL = re.compile(r"[A-Za-z0-9()\-.×/ⅡⅢ+ ]+")
RX_CJK_START = re.compile(r"^[぀-ヿ㐀-䶿一-鿿]")
RX_8DIGIT = re.compile(r"20\d{6}")
# 準拠済みの期間表記: 「_2506～」「_2506～12」「_2506～2612」(範囲の終わり付き)
RX_PERIOD_OK = re.compile(r".+_\d{4}～(\d{2}|\d{4})?$")
RX_PERIOD = re.compile(r"^(?P<h>.+?)[_ ]?(?:(?P<yy>\d{2})\.(?P<mm>\d{1,2})|(?P<yymm>\d{4}))\s*[~～]$")
RX_TAIL = re.compile(r"^(?P<h>.+?[^\d_\- ])[_\- ]?(?P<d>\d{6})(?P<e>\d?)$")
RX_MIDSUF = re.compile(r"^(?P<h>.+?[^\d_\- ])[_\- ]?(?P<d>\d{6})(?P<e>\d?)_(?P<suf>[A-Za-z0-9]{1,2})$")


def _date_ok(d: str) -> bool:
    yy, mm, dd = int(d[:2]), int(d[2:4]), int(d[4:6])
    return 1 <= mm <= 12 and 1 <= dd <= 31 and yy <= (datetime.now().year % 100) + 1


def _norm_head(h: str) -> str:
    """型式(英数)_和文説明 の間の _ を除去。和文_和文はそのまま。末尾の区切りも掃除。"""
    parts = [x for x in h.split("_") if x != ""]
    if not parts:
        return h.strip("_- ")
    out = parts[0]
    for nxt in parts[1:]:
        if RX_ASCII_MODEL.fullmatch(out.split("_")[-1]) and RX_CJK_START.match(nxt):
            out += nxt          # 型式_説明 → 型式説明
        else:
            out += "_" + nxt    # それ以外は _ を維持
    return out.rstrip("_- ")


def normalize(stem: str, mtime: datetime) -> tuple[str | None, str]:
    """(新しいstem or None, ルール名) を返す。Noneは対象外(そのまま)。"""
    s = stem.replace("~", "～").strip()
    if RX_8DIGIT.search(s):
        return None, "8桁(手動)"
    if RX_PERIOD_OK.match(s):
        return (s if s != stem else None), "準拠(期間表記)"  # ~→～の置換のみ
    m = RX_PERIOD.match(s)
    if m:
        if m.group("yy"):
            yymm = f"{m.group('yy')}{int(m.group('mm')):02d}"
        else:
            yymm = m.group("yymm")
        if 1 <= int(yymm[2:]) <= 12:
            new = f"{_norm_head(m.group('h'))}_{yymm}～"
            return (new if new != stem else None), "R1期間台帳"
    m = RX_MIDSUF.match(s)
    if m and _date_ok(m.group("d")):
        suf = m.group("suf")
        if suf.isdigit() and not m.group("e"):
            new = f"{_norm_head(m.group('h'))}_{m.group('d')}{suf}"   # 数字接尾→枝番
        elif suf.isalpha():
            new = f"{_norm_head(m.group('h'))}_{m.group('d')}{m.group('e')}"  # 英字接尾→除去
        else:
            return None, "接尾混在(手動)"
        return (new if new != stem else None), "R3日付+接尾"
    m = RX_TAIL.match(s)
    if m and _date_ok(m.group("d")):
        new = f"{_norm_head(m.group('h'))}_{m.group('d')}{m.group('e')}"
        return (new if new != stem else None), "R2日付あり"
    # 日付なし → mtimeを付与
    new = f"{_norm_head(s)}_{mtime.strftime('%y%m%d')}"
    return (new if new != stem else None), "R4日付なし(mtime付与)"


def main() -> int:
    ap = argparse.ArgumentParser(description="231見積ファイル名の正規化")
    ap.add_argument("--apply", action="store_true", help="実際に改名する(無指定はdry-run)")
    ap.add_argument("--list-8", action="store_true", help="8桁日付ファイルの一覧のみ表示")
    args = ap.parse_args()

    if not ROOT.is_dir():
        print(f"[ERROR] {ROOT}", file=sys.stderr)
        return 1

    plans: dict[str, list[tuple[Path, str]]] = defaultdict(list)
    eight: list[str] = []
    planned_targets: set[str] = set()
    for dirpath, _, filenames in os.walk(ROOT):
        for fn in sorted(filenames):
            if fn.startswith("~$"):
                continue
            p = Path(dirpath) / fn
            if p.suffix.lower() not in (".xlsx", ".xls", ".pdf"):
                continue
            if RX_8DIGIT.search(p.stem):
                eight.append(str(p.relative_to(ROOT)))
                continue
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime)
            except OSError:
                continue
            new_stem, rule = normalize(p.stem, mtime)
            if not new_stem:
                continue
            new_name = new_stem + p.suffix
            key = str(Path(dirpath) / new_name).lower()
            if (Path(dirpath) / new_name).exists() or key in planned_targets:
                plans["衝突(スキップ・手動確認)"].append((p, new_name))
                continue
            planned_targets.add(key)
            plans[rule].append((p, new_name))

    if args.list_8:
        print(f"[8桁日付(手動対応)] {len(eight)} 件:")
        for s in eight:
            print(f"  {s}")
        return 0

    total = sum(len(v) for v in plans.values())
    print(f"[SCAN] 改名対象: {total:,} 件 / 8桁(手動): {len(eight)} 件\n")
    renamed = skipped = 0
    for rule in sorted(plans):
        items = plans[rule]
        print(f"── {rule}: {len(items):,} 件 ──")
        for p, new_name in items:
            rel = p.relative_to(ROOT)
            print(f"  {rel} -> {new_name}")
            if args.apply and not rule.startswith("衝突"):
                try:
                    p.rename(p.with_name(new_name))
                    renamed += 1
                except OSError as e:
                    print(f"  [SKIP] 改名失敗(使用中?): {rel}: {e}")
                    skipped += 1
        print()
    if args.apply:
        print(f"[DONE] 改名 {renamed:,} 件 / スキップ {skipped:,} 件。"
              f"インデックスは次の夜間ラン(または差分実行)で自動追従します。")
    else:
        print("[DRY-RUN] 実際の改名は --apply を付けて実行してください。"
              "一覧が長い場合は「 > preview.txt」でファイルに保存して確認を。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
