"""231_見積書のファイル名パターン調査(リネーム方針決定用・読み取りのみ)。

末尾の日付表記を分類して件数とサンプルを表示する。

実行(デスクトップ): python tools/survey_231_names.py
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get(
    "MITSUMORI_DIR", r"Z:\takachiho\2to9_業務別フォルダ\23_顧客・納入先別\231_見積書"))

RX = {
    "① _YYMMDD(+枝番) 区切りあり=準拠": re.compile(r".+_\d{6}\d?$"),
    "② YYMMDD(+枝番) 直結=改名候補": re.compile(r".+?[^\d_\-]\d{6}\d?$"),
    "③ -YYMMDD(+枝番) ハイフン区切り": re.compile(r".+-\d{6}\d?$"),
    "④ _YYYYMMDD 8桁日付": re.compile(r".+[_\-]?20\d{6}$"),
}


def _date_ok(s: str) -> bool:
    yy, mm, dd = int(s[:2]), int(s[2:4]), int(s[4:6])
    return 1 <= mm <= 12 and 1 <= dd <= 31 and yy <= (datetime.now().year % 100) + 1


def main() -> int:
    cats: dict[str, list[str]] = defaultdict(list)
    ext_n: dict[str, int] = defaultdict(int)
    total = 0
    for dirpath, _, filenames in os.walk(ROOT):
        for fn in filenames:
            if fn.startswith("~$"):
                continue
            p = Path(dirpath) / fn
            if p.suffix.lower() not in (".xlsx", ".xls", ".pdf"):
                continue
            total += 1
            ext_n[p.suffix.lower()] += 1
            stem = p.stem
            rel = str(p.relative_to(ROOT))
            for name, rx in RX.items():
                if rx.match(stem):
                    # 日付妥当性(末尾6桁がMM/DD範囲内か)
                    m6 = re.search(r"(\d{6})\d?$", stem)
                    if name.startswith("④") or (m6 and _date_ok(m6.group(1))):
                        cats[name].append(rel)
                        break
            else:
                if re.search(r"\d{6}", stem):
                    cats["⑤ 日付らしき6桁が中間にある"].append(rel)
                elif re.search(r"\d{4,}", stem):
                    cats["⑥ 4桁以上の数字あり(日付でない)"].append(rel)
                else:
                    cats["⑦ 日付なし"].append(rel)

    print(f"[TOTAL] {total:,} 件 (xlsx {ext_n['.xlsx']:,} / xls {ext_n['.xls']:,} / "
          f"pdf {ext_n['.pdf']:,})\n")
    for name in sorted(cats):
        files = cats[name]
        print(f"{name}: {len(files):,} 件")
        for s in files[:6]:
            print(f"    {s}")
        if len(files) > 6:
            print(f"    …ほか{len(files) - 6:,}件")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
