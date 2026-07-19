"""元マスタCSVの鮮度チェック＋LW通知（毎週土曜）。

背景:
    工事一覧表.csv / 発注者一覧表.csv は T-NEXUS から手動で出力して
    _GDExtraction に置いている。ここが古いままだと、工番マスタ・検索インデックス・
    顧客の正式名称まで、すべて古いデータで毎晩「正常に」更新され続けてしまう。
    （静かに古くなるので気づけない）
    置き忘れに気づけるよう、週1回だけ鮮度を見て担当者へLW通知する。

    ※ 業務管理ソフトが立ち上がって自動供給されるようになれば、このチェックは不要。

使い方（デスクトップ）:
    python check_master_csv.py            # 古ければ通知
    python check_master_csv.py --always   # 新しくても結果を通知
    python check_master_csv.py --dry-run  # LW送信せず表示のみ

環境変数（.env）:
    GD_EXTRACTION_DIR        CSVの置き場所（省略時は下記の既定パス）
    MASTER_CSV_MAX_AGE_DAYS  何日以上古ければ警告するか（既定 14）
    MASTER_CSV_NOTIFY_NAMES  通知先の氏名（カンマ区切り。既定 '山嵜喜隆,山嵜絵里'）
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"), encoding="utf-8")
except Exception:
    pass

DEFAULT_DIR = (
    r"Z:\takachiho\2to9_業務別フォルダ\91_工番別実績写真・動画\_GDExtraction"
)
TARGETS = [
    ("工事一覧表.csv", "工事一覧表"),
    ("発注者一覧表.csv", "発注者一覧表"),
]


def _notify(message: str, dry_run: bool) -> None:
    """LINE WORKS で担当者へ通知する（失敗しても処理は止めない）。"""
    try:
        import lw_annotation_bot as bot
    except Exception as e:
        print(f"[WARN] LW通知スキップ(bot読込失敗): {e}")
        return
    names = os.environ.get("MASTER_CSV_NOTIFY_NAMES", "山嵜喜隆,山嵜絵里")
    targets = [n.strip() for n in names.split(",") if n.strip()]
    try:
        umap = bot._load_user_names()  # {userId: 氏名}
    except Exception as e:
        print(f"[WARN] LW通知スキップ(氏名一覧取得失敗): {e}")
        return

    def _norm(s: str) -> str:
        return "".join((s or "").split())

    n2u = {_norm(v): k for k, v in umap.items()}
    if dry_run:
        bot.DRY_RUN = True
    for t in targets:
        uid = n2u.get(_norm(t))
        if not uid:
            print(f"[WARN] 通知先が見つかりません(氏名不一致): {t}")
            continue
        ok = bot._send_text(uid, message)
        print(f"[LW] {t} へ通知{'(DRY-RUN)' if dry_run else ''}: {'OK' if ok else 'NG'}")


def main() -> None:
    ap = argparse.ArgumentParser(description="元マスタCSVの鮮度チェック")
    ap.add_argument("--always", action="store_true", help="新しくても通知する")
    ap.add_argument("--dry-run", action="store_true", help="LW送信せず表示のみ")
    args = ap.parse_args()

    base = Path(os.environ.get("GD_EXTRACTION_DIR", DEFAULT_DIR))
    max_age = int(os.environ.get("MASTER_CSV_MAX_AGE_DAYS", "14"))

    problems: list[str] = []
    fresh: list[str] = []

    for fname, label in TARGETS:
        p = base / fname
        if not p.exists():
            problems.append(f"❌ {label}.csv が見つかりません")
            continue
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime)
        except Exception as e:
            problems.append(f"❌ {label}.csv の日付を取得できません: {e}")
            continue
        days = (datetime.now() - mtime).days
        stamp = mtime.strftime("%Y-%m-%d")
        if days >= max_age:
            problems.append(f"⚠️ {label}.csv が {days} 日前（{stamp}）のままです")
        else:
            fresh.append(f"{label}（{stamp} / {days}日前）")

    now = datetime.now().strftime("%Y/%m/%d")
    if problems:
        msg = (
            f"📋 元マスタCSVの鮮度チェック（{now}）\n\n"
            + "\n".join(problems)
            + "\n\nT-NEXUSから出力し直して、下記に置いてください:\n"
            + f"{base}\n\n"
            + "※このCSVが古いと、工番マスタ・検索インデックス・顧客の正式名称が"
            "古いまま更新され続けます。"
        )
        if fresh:
            msg += "\n\n✅ 最新: " + "、".join(fresh)
    else:
        msg = (
            f"📋 元マスタCSVの鮮度チェック（{now}）\n"
            f"✅ どちらも {max_age} 日以内です: " + "、".join(fresh)
        )

    print(msg)

    if problems or args.always:
        _notify(msg, args.dry_run)
    else:
        print(f"[LW] {max_age}日以内のため通知は送りません（--always で毎回送信）")


if __name__ == "__main__":
    main()
