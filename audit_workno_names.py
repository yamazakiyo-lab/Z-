r"""工番フォルダ名が工事一覧表と一致しているかを全社フォルダで監査する(読み取り専用)。

背景:
    旧コードは工番マスタを「工事番号」(枝番なし)列で読んでいたため、
    枝番が同一キーに潰れ、親(-00)フォルダに「最後の枝番の名称」を書き込んでいた。
    そのため -00 と -01 が同じ工事名で並ぶ、といった破損が各所に残っている。
    例) 4031-00 と 4031-01 が両方「PMX-L2-200(1)-155-83 リビルト機 1995年3月製」
        正しくは 4031-00 = PMX-L2-300(1)-185-88 リビルト機 1996年10月製

    どこにどれだけ残っているかを、リネームせずに一覧化する。

やること:
    - 工事一覧表.csv を「工事番号＋枝番」列で読む(修正後の正しい読み方)
    - 対象フォルダ直下の工番フォルダ/ファイルを走査し、現在名と正しい名を突き合わせる
    - 結果を audit_workno_names.txt に出力する

    ※ 一切リネームしない。判断材料を出すだけ。

使い方(PowerShell):
    cd C:\Users\Yamazakiyo\tseg_vscode\Zフォルダ整理
    & ".\91GDX・252WORKNO-program\venv\Scripts\python.exe" audit_workno_names.py
"""
from __future__ import annotations

import importlib
import sys
import types
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_DIR = HERE / "91GDX・252WORKNO-program"
OUT = HERE / "audit_workno_names.txt"

BASE = Path(r"Z:\takachiho\2to9_業務別フォルダ")
# GDX卒業(2026-07-24): _masters優先、旧_GDExtractionフォールバック
MASTER_CSV = BASE / "91_工番別実績写真・動画" / "_masters" / "工事一覧表.csv"
if not MASTER_CSV.exists():
    MASTER_CSV = BASE / "91_工番別実績写真・動画" / "_GDExtraction" / "工事一覧表.csv"

# 監査対象(工番名で並んでいる場所)
TARGETS = [
    ("91_実績写真・動画", BASE / "91_工番別実績写真・動画", "dir"),
    ("92_PO LIST",        BASE / "92_PO LIST", "file"),
    ("252_整備資料",      BASE / "25_リビルト・中古機" / "252_整備資料", "dir"),
    ("9781_工事工番",     BASE / "97_技術資料" / "978_CADデータ図庫" / "9781_工事工番", "dir"),
]


def _ensure_pkg(name: str, path: Path) -> None:
    if name in sys.modules:
        return
    mod = types.ModuleType(name)
    mod.__path__ = [str(path)]
    sys.modules[name] = mod


def main() -> int:
    if not MASTER_CSV.exists():
        print(f"[ERROR] 工事一覧表.csv が見つかりません: {MASTER_CSV}")
        return 1

    _ensure_pkg("gdxpkg", PKG_DIR)
    importlib.invalidate_caches()
    try:
        m = importlib.import_module("gdxpkg.master")
        u = importlib.import_module("gdxpkg.utils")
    except ImportError as e:
        print(f"[ERROR] 読み込み失敗: {e}")
        print(r'  venv で実行してください: & ".\91GDX・252WORKNO-program\venv\Scripts\python.exe" audit_workno_names.py')
        return 1

    master = m._read_csv_master(MASTER_CSV)
    print(f"[AUDIT] マスタ件数: {len(master)}")

    lines: list[str] = []
    lines.append("工番フォルダ名 監査レポート(読み取り専用)")
    lines.append(f"master: {MASTER_CSV}")
    lines.append(f"master_count: {len(master)}")
    lines.append("")

    total = {"ok": 0, "mismatch": 0, "no_master": 0, "dup": 0}

    for label, root, kind in TARGETS:
        lines.append("=" * 70)
        lines.append(f"[{label}] {root}")
        if not root.is_dir():
            lines.append("  ⚠️ フォルダが見つかりません(未接続?)")
            lines.append("")
            continue

        entries = [e for e in root.iterdir() if (e.is_dir() if kind == "dir" else e.is_file())]
        by_name: dict[str, list[str]] = defaultdict(list)
        mism: list[str] = []
        nomas: list[str] = []
        ok = 0

        for e in sorted(entries, key=lambda x: x.name.lower()):
            stem = e.stem if kind == "file" else e.name
            workno = m.get_workno_from_name(stem)
            if not workno:
                continue
            master_name = master.get(workno)
            if not master_name:
                nomas.append(f"    {e.name}  (工番={workno})")
                continue

            correct = u.sanitize_name(f"{workno}_{u.normalize_master_name(master_name)}")
            # 92_PO LIST は "_PO LIST" のような接尾辞が付くので前方一致で判定
            actual = stem
            if actual == correct or actual.startswith(correct + "_"):
                ok += 1
            else:
                mism.append(f"    現在: {e.name}\n      正: {correct}")

            # 工番を除いた「名前部分」で重複を検出(-00 と -01 が同名など)
            namepart = stem.split("_", 1)[1] if "_" in stem else stem
            by_name[namepart].append(stem)

        dups = {k: v for k, v in by_name.items() if len(v) > 1}

        lines.append(f"  一致: {ok} 件 / 不一致: {len(mism)} 件 / マスタ未登録: {len(nomas)} 件 / 同名重複: {len(dups)} 組")
        total["ok"] += ok
        total["mismatch"] += len(mism)
        total["no_master"] += len(nomas)
        total["dup"] += len(dups)

        if mism:
            lines.append("  --- 名称不一致(マスタと違う名前が付いている) ---")
            lines.extend(mism)
        if dups:
            lines.append("  --- 同名重複(別工番なのに同じ工事名) ---")
            for k, v in sorted(dups.items()):
                lines.append(f"    「{k}」")
                for s in sorted(v):
                    lines.append(f"      - {s}")
        if nomas:
            lines.append("  --- マスタ未登録(CSVに無い工番) ---")
            lines.extend(nomas[:50])
            if len(nomas) > 50:
                lines.append(f"    ...ほか {len(nomas) - 50} 件")
        lines.append("")

    lines.append("=" * 70)
    lines.append(f"合計: 一致 {total['ok']} / 不一致 {total['mismatch']} / マスタ未登録 {total['no_master']} / 同名重複 {total['dup']} 組")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[AUDIT] 一致 {total['ok']} / 不一致 {total['mismatch']} / マスタ未登録 {total['no_master']} / 同名重複 {total['dup']} 組")
    print(f"[AUDIT] 出力: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
