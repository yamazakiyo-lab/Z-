"""文字化け・構文エラーの再発防止：スクリプトの文字コード点検＋自動修復。

背景:
    Windows PowerShell 5.1 は「BOMの無いUTF-8ファイル」を cp932(Shift-JIS)として
    読んでしまう。そのため日本語コメントやパスを含む .ps1 がBOM無しだと、
    文字化け・構文エラー（例: Unexpected token / 文字列の終端がない）で突然落ちる。
    実際に cleanup_logs_all.ps1 がこれで動かなくなった。

    → 日本語を含む .ps1 は必ず「UTF-8 (BOM付き)」で保存する。これが唯一の対策。

使い方:
    python check_encoding.py            # 点検のみ（問題があれば一覧表示・終了コード1）
    python check_encoding.py --fix      # BOMを付けて自動修復
    python check_encoding.py --notify   # 問題があればLINE WORKSで通知

デイリーランやタスク点検に組み込んでおけば、
新しく作った .ps1 がBOM無しでも、動かなくなる前に気づける。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"), encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
BOM = b"\xef\xbb\xbf"

# 点検対象: PowerShell はBOM必須。バッチは日本語をrem内に留める運用なので対象外。
TARGET_GLOBS = ("*.ps1",)
# 除外（外部由来・自動生成など）
SKIP_DIRS = {".git", "archive", "venv", "lw_venv", "rag_venv", "__pycache__", "logs", "lw_logs"}


def _has_non_ascii(data: bytes) -> bool:
    return any(b >= 0x80 for b in data)


def scan() -> tuple[list[Path], list[Path]]:
    """(BOM無しで日本語を含む=要修正, 判定不能=要確認) を返す。"""
    need_fix: list[Path] = []
    unknown: list[Path] = []
    # .ps1 はリポジトリ直下に置く運用。venv等の巨大フォルダを走査すると
    # 遅いだけで得るものが無いので、直下のみを対象にする。
    for pattern in TARGET_GLOBS:
        for p in sorted(BASE.glob(pattern)):
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            try:
                data = p.read_bytes()
            except Exception:
                continue
            if not _has_non_ascii(data):
                continue          # ASCIIのみなら文字コード問題は起きない
            if data.startswith(BOM):
                continue          # 正常
            try:
                data.decode("utf-8")
                need_fix.append(p)      # UTF-8だがBOM無し → BOMを付ければ直る
            except UnicodeDecodeError:
                unknown.append(p)       # cp932等 → 変換が必要
    return need_fix, unknown


def fix(paths: list[Path], unknown: list[Path]) -> int:
    fixed = 0
    for p in paths:
        try:
            text = p.read_bytes().decode("utf-8")
            p.write_bytes(BOM + text.encode("utf-8"))
            print(f"[FIX] BOMを付与: {p.relative_to(BASE)}")
            fixed += 1
        except Exception as e:
            print(f"[ERROR] 修復失敗 {p.name}: {e}", file=sys.stderr)
    for p in unknown:
        try:
            text = p.read_bytes().decode("cp932")
            p.write_bytes(BOM + text.encode("utf-8"))
            print(f"[FIX] cp932→UTF-8(BOM付き)に変換: {p.relative_to(BASE)}")
            fixed += 1
        except Exception as e:
            print(f"[ERROR] 変換失敗 {p.name}: {e}", file=sys.stderr)
    return fixed


def notify(message: str) -> None:
    try:
        import lw_annotation_bot as bot
    except Exception as e:
        print(f"[WARN] LW通知スキップ(bot読込失敗): {e}")
        return
    names = os.environ.get("ENCODING_NOTIFY_NAMES", "山嵜喜隆")
    try:
        umap = bot._load_user_names()
    except Exception as e:
        print(f"[WARN] LW通知スキップ: {e}")
        return
    norm = lambda s: "".join((s or "").split())
    n2u = {norm(v): k for k, v in umap.items()}
    for t in [n.strip() for n in names.split(",") if n.strip()]:
        uid = n2u.get(norm(t))
        if uid:
            bot._send_text(uid, message)
            print(f"[LW] {t} へ通知")


def main() -> None:
    ap = argparse.ArgumentParser(description="スクリプトの文字コード点検")
    ap.add_argument("--fix", action="store_true", help="BOMを付けて自動修復する")
    ap.add_argument("--notify", action="store_true", help="問題があればLW通知する")
    args = ap.parse_args()

    need_fix, unknown = scan()

    if not need_fix and not unknown:
        print("[OK] 日本語を含む .ps1 はすべて UTF-8(BOM付き) です。")
        return

    print(f"[NG] 文字コードに問題のあるファイル: {len(need_fix) + len(unknown)} 件")
    for p in need_fix:
        print(f"  BOM無し(UTF-8) : {p.relative_to(BASE)}")
    for p in unknown:
        print(f"  UTF-8でない     : {p.relative_to(BASE)}")

    if args.fix:
        n = fix(need_fix, unknown)
        print(f"[DONE] {n} 件を UTF-8(BOM付き) に修復しました。")
        return

    if args.notify:
        names = "、".join(p.name for p in (need_fix + unknown))
        notify(
            "⚠️ スクリプトの文字コード異常\n"
            f"BOM無しの .ps1 が {len(need_fix) + len(unknown)} 件あります: {names}\n"
            "PowerShellが文字化け・構文エラーで落ちる原因になります。\n"
            "`python check_encoding.py --fix` で修復してください。"
        )

    sys.exit(1)


if __name__ == "__main__":
    main()
