"""231_見積書のファイル名正規化: 日付直結を「_YYMMDD」区切りに統一する。

「手塚工業NC1-1500(D)060509.xls」のように日付(6桁+任意の枝番1桁)がファイル名に
直結しているものを「手塚工業NC1-1500(D)_060509.xls」に改名する。
型式の数字を日付と誤認しないよう、末尾6〜7桁が日付として妥当(YY=00〜現在年+1,
MM=01-12, DD=01-31)な場合のみ対象。既に _ や - で区切られているものは触らない。

実行(デスクトップ):
    python tools/rename_231_mitsumori.py            # dry-run(一覧表示のみ)
    python tools/rename_231_mitsumori.py --apply    # 実際に改名
インデックスへの追従は不要(改名=旧名削除+新名再取込を夜間ランが自動処理)。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get(
    "MITSUMORI_DIR", r"Z:\takachiho\2to9_業務別フォルダ\23_顧客・納入先別\231_見積書"))

# 末尾: (日付6桁)(枝番0-1桁) + 拡張子。直前が英字・かな・漢字・閉じ括弧など
# (区切り記号 _ - でない)場合のみ対象。
RX_TAIL = re.compile(r"^(?P<head>.*?)(?P<date>\d{6})(?P<eda>\d?)$")


def _date_ok(yymmdd: str) -> bool:
    yy, mm, dd = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    if not (1 <= mm <= 12 and 1 <= dd <= 31):
        return False
    return yy <= (datetime.now().year % 100) + 1  # 00〜来年まで(それ以外は型式とみなす)


def main() -> int:
    ap = argparse.ArgumentParser(description="231見積ファイル名の日付区切り統一")
    ap.add_argument("--apply", action="store_true", help="実際に改名する(無指定はdry-run)")
    args = ap.parse_args()

    if not ROOT.is_dir():
        print(f"[ERROR] {ROOT}", file=sys.stderr)
        return 1

    targets: list[tuple[Path, str]] = []
    for dirpath, _, filenames in os.walk(ROOT):
        for fn in filenames:
            if fn.startswith("~$"):
                continue
            p = Path(dirpath) / fn
            if p.suffix.lower() not in (".xlsx", ".xls", ".pdf"):
                continue
            m = RX_TAIL.match(p.stem)
            if not m:
                continue
            head = m.group("head")
            if not head or head[-1] in "_-":
                continue  # 既に区切りあり(または名前全体が日付)
            if head[-1].isdigit():
                continue  # 数字が8桁以上連続(型式等の可能性)は触らない
            if not _date_ok(m.group("date")):
                continue
            new_name = f"{head}_{m.group('date')}{m.group('eda')}{p.suffix}"
            targets.append((p, new_name))

    print(f"[SCAN] 改名対象: {len(targets):,} 件")
    renamed = skipped = 0
    for p, new_name in targets:
        dst = p.with_name(new_name)
        rel = p.relative_to(ROOT)
        if dst.exists():
            print(f"  [SKIP] 改名先が既に存在: {rel} -> {new_name}")
            skipped += 1
            continue
        print(f"  {rel} -> {new_name}")
        if args.apply:
            try:
                p.rename(dst)
                renamed += 1
            except OSError as e:
                print(f"  [SKIP] 改名失敗(使用中?): {rel}: {e}")
                skipped += 1
    if args.apply:
        print(f"[DONE] 改名 {renamed:,} 件 / スキップ {skipped:,} 件。"
              f"インデックスは次の夜間ラン(または差分実行)で自動追従します。")
    else:
        print("[DRY-RUN] 実際の改名は --apply を付けて実行してください。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
