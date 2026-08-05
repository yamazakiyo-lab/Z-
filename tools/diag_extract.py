"""見積1ファイルの抽出診断: 指定語がブックのどこにあり、インデックスに載ったかを調べる。

QUOTATION SEARCHで語がヒットしない原因(旧xls/シート上限/文字数上限/表記違い)の
切り分け用。ブック全シートを上限なしで走査した結果と、インデクサ(_extract)が
実際に取り込んだ本文の両方で語を探し、差があれば「上限で切れた」と分かる。

実行(デスクトップ):
    python tools/diag_extract.py "Z:\\...\\見積.xlsx" QD1
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def main() -> int:
    if len(sys.argv) < 3:
        print("使い方: python tools/diag_extract.py <xlsxパス> <探す語>")
        return 1
    p = Path(sys.argv[1])
    term = sys.argv[2]
    if not p.exists():
        print(f"[ERROR] ファイルがありません: {p}")
        return 1
    if p.suffix.lower() == ".xls":
        print("[判定] 旧xls形式です。インデックス未対応(第2期対応待ち)のため検索に出ません。")
        return 0

    term_l = term.lower()

    # ── 1. ブック全体を上限なしで走査(語の実在位置を確認) ──────────────────
    import openpyxl
    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    print(f"[BOOK] シート数: {len(wb.worksheets)}")
    found = []
    for si, ws in enumerate(wb.worksheets):
        for ri, row in enumerate(ws.iter_rows(values_only=True), start=1):
            for ci, v in enumerate(row, start=1):
                if v is not None and term_l in str(v).lower():
                    found.append((si + 1, ws.title, ri, ci, str(v).strip()[:80]))
    wb.close()
    if found:
        print(f"[RAW] 「{term}」はブック内に {len(found)} 箇所:")
        for si, name, ri, ci, s in found[:10]:
            over = []
            if si > 30:
                over.append("シート31枚目以降")
            if ri > 300:
                over.append("301行目以降")
            if ci > 20:
                over.append("21列目以降")
            mark = f" ⚠{'・'.join(over)}" if over else ""
            print(f"  シート{si}「{name}」 {ri}行{ci}列: {s}{mark}")
    else:
        print(f"[RAW] 「{term}」はブック内に見つかりません(表記違いの可能性)。")

    # ── 2. インデクサが実際に取り込む本文で確認 ────────────────────────────
    from export_mitsumori_index import _extract
    info = _extract(p)
    body = info.get("body", "")
    print(f"\n[IDX] インデクサ取込本文: {len(body)}字")
    if term_l in body.lower():
        pos = body.lower().find(term_l)
        print(f"[IDX] 「{term}」は取込済み(位置{pos})。検索に出ないなら型式の"
              f"トークン分割の問題 → diag_search_hits.py のトークン分割を確認。")
        print(f"  前後: …{body[max(0, pos - 60):pos + 60]}…")
    elif found:
        print(f"[IDX] 「{term}」は取込本文に無し ⚠ 上限(シート30枚/行300/列20/"
              f"シート2000字/全体9000字)で切れています。上限拡大が必要。")
    else:
        print(f"[IDX] 取込本文にも無し(ブックにも無いため表記違いを疑ってください)。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
