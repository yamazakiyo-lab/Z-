r"""GDExtraction のリネーム内容を「実行せずに」確認するプレビュー。

（docstring 内に C:\... や Z:\... のパスを書くため raw 文字列にしている。
  通常の文字列だと \U が Unicode エスケープと解釈されて SyntaxError になる）

なぜ必要か:
    工番マスタの読込列を「工事番号」→「工事番号＋枝番」に修正したことで、
    これまで照合できていなかった工番(特に英字接頭辞付き)が一斉に対象になる。
    本番リネームは元に戻すのが大変なので、先に何がどう変わるかを一覧で見る。

やること:
    - Z:\...\_GDExtraction を読むだけ。ファイル名・フォルダ名は一切変更しない。
    - 結果を preview_master_rename.txt に書き出す。

使い方(PowerShell):
    cd C:\Users\Yamazakiyo\tseg_vscode\Zフォルダ整理
    & ".\91GDX・252WORKNO-program\venv\Scripts\python.exe" preview_master_rename.py

    ※ master.py が drive_sync(Google API)を読み込むため、
      システムPythonではなく上記 venv の python.exe で実行すること。

補足:
    フォルダ名に「・」やハイフンが含まれ Python の識別子にならないので、
    run_gdx.py と同じく gdxpkg という仮のパッケージ名を割り当てて読み込む。
"""
from __future__ import annotations

import importlib
import os
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_DIR = HERE / "91GDX・252WORKNO-program"

GD_ROOT = Path(
    os.environ.get(
        "GD_EXTRACTION_DIR",
        r"Z:\takachiho\2to9_業務別フォルダ\91_工番別実績写真・動画\_GDExtraction",
    )
)
OUT = HERE / "preview_master_rename.txt"


def _ensure_pkg(name: str, path: Path) -> None:
    """フォルダ名が識別子にならないので、仮のパッケージ名で読み込めるようにする。"""
    if name in sys.modules:
        return
    mod = types.ModuleType(name)
    mod.__path__ = [str(path)]
    sys.modules[name] = mod


def main() -> int:
    if not PKG_DIR.is_dir():
        print(f"[ERROR] プログラムフォルダが見つかりません: {PKG_DIR}")
        return 1
    if not GD_ROOT.is_dir():
        print(f"[ERROR] GDExtraction が見つかりません: {GD_ROOT}")
        print("       Zドライブが接続されているか確認してください。")
        return 1

    _ensure_pkg("gdxpkg", PKG_DIR)
    importlib.invalidate_caches()
    try:
        master = importlib.import_module("gdxpkg.master")
    except ImportError as e:
        print(f"[ERROR] master.py の読み込みに失敗: {e}")
        print("       venv の python.exe で実行しているか確認してください:")
        print(r'       & ".\91GDX・252WORKNO-program\venv\Scripts\python.exe" preview_master_rename.py')
        return 1

    print(f"[PREVIEW] 対象: {GD_ROOT}")
    print("[PREVIEW] 読み取りのみ。リネームは実行しません。")
    path = master.write_gdextraction_master_preview_report(GD_ROOT, OUT)
    if not path:
        print("[ERROR] プレビューを生成できませんでした。")
        return 1

    # 要約だけ画面にも出す(本文は長いのでファイルで確認)
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith(("master_file:", "master_count:", "folder_plan_count:", "file_plan_count:")):
            print(f"[PREVIEW] {line}")
    text = Path(path).read_text(encoding="utf-8")
    print(f"[PREVIEW] CONFLICT: {text.count('[CONFLICT]')} 件")
    print(f"[PREVIEW] 出力: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
