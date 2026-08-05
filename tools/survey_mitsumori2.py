"""見積書Excelのフォーマット調査(第2弾)。

サンプル抽出したExcelを開き、宛先(様)・件名(御見積)・日付・金額らしき
セルの位置と値を推定して、テンプレートの統一度を確認する。
インデックス設計(何を機械抽出できるか)の判断材料にする。

使い方(デスクトップ):
    python tools/survey_mitsumori2.py              # 50件サンプル
    python tools/survey_mitsumori2.py --n 100
"""
from __future__ import annotations

import argparse
import os
import random
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(os.environ.get(
    "MITSUMORI_DIR", r"Z:\takachiho\2to9_業務別フォルダ\23_顧客・納入先別\231_見積書"))

RX_MONEY = re.compile(r"^\d{4,}$")


def _scan_file(p: Path) -> dict:
    """先頭シートの左上40行×12列から見積要素を推定。"""
    import openpyxl
    out = {"file": "", "atesaki": "", "kenmei": "", "date": "", "gokei": "",
           "cells": ""}
    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    found_cells = []
    max_num = 0
    for row in ws.iter_rows(min_row=1, max_row=40, max_col=12):
        for c in row:
            v = c.value
            if v is None:
                continue
            s = str(v).strip()
            if not s:
                continue
            if (s.endswith("様") or s.endswith("御中")) and not out["atesaki"]:
                out["atesaki"] = s[:30]
                found_cells.append(f"宛先={c.coordinate}")
            if ("見積" in s and len(s) <= 20) and not out["kenmei"]:
                found_cells.append(f"表題={c.coordinate}")
            if ("件名" in s or "工事名" in s or "品名" in s) and len(s) <= 8:
                found_cells.append(f"件名ラベル={c.coordinate}")
            if hasattr(v, "year"):  # datetime
                if not out["date"]:
                    out["date"] = str(v)[:10]
                    found_cells.append(f"日付={c.coordinate}")
            if isinstance(v, (int, float)) and v > max_num:
                max_num = v
                out["gokei"] = f"{v:,.0f}"
                gokei_cell = c.coordinate
    if out["gokei"]:
        found_cells.append(f"最大数値={gokei_cell}")
    out["cells"] = " ".join(found_cells[:8])
    wb.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="見積書Excelフォーマット調査")
    ap.add_argument("--n", type=int, default=50)
    args = ap.parse_args()

    if not ROOT.is_dir():
        print(f"[ERROR] {ROOT}", file=sys.stderr)
        return 1

    xlsx_all = []
    xls_count = 0
    for dirpath, _, filenames in os.walk(ROOT):
        for fn in filenames:
            if fn.startswith("~$"):
                continue
            if fn.lower().endswith(".xlsx"):
                xlsx_all.append(Path(dirpath) / fn)
            elif fn.lower().endswith(".xls"):
                xls_count += 1
    print(f"[SCAN] xlsx {len(xlsx_all):,} 件 / 旧xls {xls_count:,} 件"
          f"(旧xlsは今回未解析。件数だけ把握)")

    random.seed(0)
    sample = random.sample(xlsx_all, min(args.n, len(xlsx_all)))
    ok = err = 0
    cell_patterns: Counter = Counter()
    for p in sample:
        rel = p.relative_to(ROOT)
        try:
            r = _scan_file(p)
            ok += 1
            cell_patterns[r["cells"].split(" 最大数値")[0]] += 1
            print(f"--- {rel}")
            print(f"    宛先:{r['atesaki'] or '?'} / 日付:{r['date'] or '?'}"
                  f" / 最大数値:{r['gokei'] or '?'}")
            print(f"    検出セル: {r['cells']}")
        except Exception as e:
            err += 1
            print(f"--- {rel}\n    [読込失敗] {type(e).__name__}: {e}")

    print(f"\n[RESULT] 解析成功 {ok} / 失敗 {err}")
    print("[セル配置パターン上位] (同じ配置が多い=テンプレ統一度が高い)")
    for pat, c in cell_patterns.most_common(5):
        print(f"  {c:3}件: {pat}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
