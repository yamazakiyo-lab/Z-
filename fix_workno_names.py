r"""工番フォルダ名を工事一覧表に合わせて修復する(既定はドライラン)。

背景:
    旧コードが工番マスタを「工事番号」(枝番なし)列で読んでいたため、
    親(-00)フォルダに「最後の枝番の名称」が書き込まれていた。
    audit_workno_names.py の結果、不一致188件はすべて -00 側だった。
    名前だけが壊れており中身は移動していないので、正しい名前に戻す。

安全策:
    - 既定はドライラン。--apply を付けたときだけ実際にリネームする。
    - 実行時は必ず取り消し用CSV(undo_rename_YYYYmmdd_HHMMSS.csv)を残す。
    - 同名のフォルダ/ファイルが既にある場合(conflict)は触らずスキップする。
    - --undo <csv> でいつでも元に戻せる。

使い方(PowerShell):
    cd C:\Users\Yamazakiyo\tseg_vscode\Zフォルダ整理
    $py = ".\91GDX・252WORKNO-program\venv\Scripts\python.exe"

    & $py fix_workno_names.py                  # ドライラン(何も変更しない)
    & $py fix_workno_names.py --only 91        # 91だけ様子を見る
    & $py fix_workno_names.py --only 91 --apply # 91だけ実行
    & $py fix_workno_names.py --apply           # 全対象を実行
    & $py fix_workno_names.py --undo undo_rename_20260720_143000.csv
"""
from __future__ import annotations

import argparse
import csv
import importlib
import sys
import types
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_DIR = HERE / "91GDX・252WORKNO-program"

BASE = Path(r"Z:\takachiho\2to9_業務別フォルダ")
MASTER_CSV = BASE / "91_工番別実績写真・動画" / "_GDExtraction" / "工事一覧表.csv"

TARGETS = [
    ("91",   BASE / "91_工番別実績写真・動画", "dir"),
    ("92",   BASE / "92_PO LIST", "file"),
    ("252",  BASE / "25_リビルト・中古機" / "252_整備資料", "dir"),
    ("9781", BASE / "97_技術資料" / "978_CADデータ図庫" / "9781_工事工番", "dir"),
]


def _ensure_pkg(name: str, path: Path) -> None:
    if name in sys.modules:
        return
    mod = types.ModuleType(name)
    mod.__path__ = [str(path)]
    sys.modules[name] = mod


def _load_modules():
    _ensure_pkg("gdxpkg", PKG_DIR)
    importlib.invalidate_caches()
    return importlib.import_module("gdxpkg.master"), importlib.import_module("gdxpkg.utils")


def do_undo(csv_path: Path) -> int:
    if not csv_path.exists():
        print(f"[ERROR] 取り消しCSVが見つかりません: {csv_path}")
        return 1
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))
    done = fail = 0
    # 後から実行した分から戻す
    for r in reversed(rows):
        new = Path(r["new_path"])
        old = Path(r["old_path"])
        if not new.exists():
            print(f"[SKIP] 見つかりません: {new.name}")
            continue
        if old.exists():
            print(f"[SKIP] 戻し先が既に存在: {old.name}")
            fail += 1
            continue
        try:
            new.rename(old)
            done += 1
        except Exception as e:
            print(f"[ERROR] {new.name}: {e}")
            fail += 1
    print(f"[UNDO] 復元 {done} 件 / 失敗・スキップ {fail} 件")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="工番フォルダ名をマスタに合わせて修復")
    ap.add_argument("--apply", action="store_true", help="実際にリネームする(既定はドライラン)")
    ap.add_argument("--only", help="対象を限定 (91 / 92 / 252 / 9781)")
    ap.add_argument("--undo", help="取り消しCSVを指定して元に戻す")
    args = ap.parse_args()

    if args.undo:
        return do_undo(Path(args.undo))

    if not MASTER_CSV.exists():
        print(f"[ERROR] 工事一覧表.csv が見つかりません: {MASTER_CSV}")
        return 1

    try:
        m, u = _load_modules()
    except ImportError as e:
        print(f"[ERROR] 読み込み失敗: {e}")
        print(r'  venv で実行してください: & ".\91GDX・252WORKNO-program\venv\Scripts\python.exe" fix_workno_names.py')
        return 1

    master = m._read_csv_master(MASTER_CSV)
    print(f"[FIX] マスタ件数: {len(master)}")
    print(f"[FIX] モード: {'本番実行(--apply)' if args.apply else 'ドライラン(変更しません)'}")

    plans: list[tuple[Path, Path]] = []
    conflicts: list[str] = []

    for label, root, kind in TARGETS:
        if args.only and args.only != label:
            continue
        if not root.is_dir():
            print(f"[WARN] 見つかりません: {root}")
            continue

        entries = [e for e in root.iterdir() if (e.is_dir() if kind == "dir" else e.is_file())]
        for e in sorted(entries, key=lambda x: x.name.lower()):
            stem = e.stem if kind == "file" else e.name
            workno = m.get_workno_from_name(stem)
            if not workno:
                continue
            master_name = master.get(workno)
            if not master_name:
                continue

            correct = u.sanitize_name(f"{workno}_{u.normalize_master_name(master_name)}")
            if stem == correct or stem.startswith(correct + "_"):
                continue

            # 「工番_工事名」以降の接尾辞(例: _PO LIST)は保持する
            rest = ""
            if "_" in stem:
                head_name = stem.split("_", 1)[1]
                # 接尾辞らしきものを拾う(PO LIST など、最後の "_xxx")
                for suf in ("_PO LIST",):
                    if head_name.endswith(suf):
                        rest = suf
                        break
            new_stem = correct + rest
            new_path = e.with_name(new_stem + (e.suffix if kind == "file" else ""))

            if new_path.exists():
                conflicts.append(f"  [CONFLICT] {e.name}\n     -> {new_path.name} (同名が既に存在)")
                continue
            plans.append((e, new_path))

    print(f"[FIX] リネーム対象: {len(plans)} 件 / 衝突でスキップ: {len(conflicts)} 件")
    for src, dst in plans:
        print(f"  {src.name}\n    -> {dst.name}")
    if conflicts:
        print("\n--- 衝突(手動確認が必要) ---")
        print("\n".join(conflicts))

    if not args.apply:
        print("\n[FIX] ドライランのため何も変更していません。実行するには --apply を付けてください。")
        return 0

    if not plans:
        print("[FIX] 対象がありません。")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    undo_path = HERE / f"undo_rename_{stamp}.csv"
    done = fail = 0
    with undo_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["old_path", "new_path"])
        for src, dst in plans:
            try:
                src.rename(dst)
                w.writerow([str(src), str(dst)])
                f.flush()          # 途中で落ちても戻せるよう都度書き出す
                done += 1
            except Exception as e:
                print(f"[ERROR] {src.name}: {e}")
                fail += 1

    print(f"\n[FIX] 完了: {done} 件リネーム / {fail} 件失敗")
    print(f"[FIX] 取り消し用CSV: {undo_path}")
    print(f"[FIX] 元に戻す場合: fix_workno_names.py --undo {undo_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
