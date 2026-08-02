"""誕生日・入社記念日の当日通知。

LINE WORKSのメンバー情報(誕生日・入社日)を毎朝APIで読み、当日が
誕生日・入社記念日(入社当日含む)のメンバーがいれば、管理者
(既定: 山嵜喜隆・山嵜絵里)へだけBotでLW通知する。該当なしの日は何も送らない。

メンバーの誕生日・入社日は LW Admin「メンバー情報の一括修正」のExcelで
登録・更新する(スクリプト側のマスタ管理は不要)。

使い方(デスクトップ):
    python tools/lw_anniversary_notify.py --check   # 登録状況の一覧表示のみ
    python tools/lw_anniversary_notify.py           # 当日判定して通知(タスク用)
    python tools/lw_anniversary_notify.py --dry-run # 通知せず判定結果を表示

環境変数: ANNIV_NOTIFY_NAMES で通知先を変更可(既定「山嵜喜隆,山嵜絵里」)
タスク: TSEG_記念日通知(毎日8:00)
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lw_annotation_bot import _get_access_token, _send_text, logger  # noqa: E402

JST = timezone(timedelta(hours=9))
NOTIFY_NAMES = [n.strip() for n in
                os.getenv("ANNIV_NOTIFY_NAMES", "山嵜喜隆,山嵜絵里").split(",") if n.strip()]


def _parse_date(v) -> tuple[int | None, int, int] | None:
    """'YYYY-MM-DD'/'MM-DD'/'YYYYMMDD' 等を (年 or None, 月, 日) に。不明はNone。"""
    if not v:
        return None
    s = re.sub(r"[^0-9]", "", str(v))
    if len(s) == 8:      # YYYYMMDD
        return int(s[:4]), int(s[4:6]), int(s[6:8])
    if len(s) == 4:      # MMDD
        return None, int(s[:2]), int(s[2:4])
    return None


def _fetch_users_full() -> list[dict]:
    """全ユーザーの生オブジェクトを返す(誕生日・入社日込み)。"""
    token = _get_access_token()
    users: list[dict] = []
    url = "https://www.worksapis.com/v1.0/users?count=100"
    while url:
        r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
        r.raise_for_status()
        data = r.json()
        users.extend(data.get("users", []))
        cur = data.get("responseMetaData", {}).get("nextCursor")
        url = f"https://www.worksapis.com/v1.0/users?count=100&cursor={cur}" if cur else None
    return users


def _display_name(u: dict) -> str:
    n = u.get("userName", {}) or {}
    return f"{n.get('lastName', '')}{n.get('firstName', '')}".strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="誕生日・入社記念日の当日通知")
    ap.add_argument("--check", action="store_true", help="登録状況の一覧表示のみ")
    ap.add_argument("--dry-run", action="store_true", help="通知せず判定結果を表示")
    args = ap.parse_args()

    users = _fetch_users_full()
    today = datetime.now(JST).date()

    if args.check:
        print(f"{'氏名':<12} {'誕生日':<12} 入社日")
        for u in users:
            name = _display_name(u)
            if not name:
                continue
            print(f"{name:<12} {str(u.get('birthday') or '-'):<12} "
                  f"{u.get('hiredDate') or u.get('hireDate') or '-'}")
        return 0

    lines: list[str] = []
    for u in users:
        name = _display_name(u)
        if not name:
            continue
        # 誕生日
        b = _parse_date(u.get("birthday"))
        if b and (b[1], b[2]) == (today.month, today.day):
            lines.append(f"🎂 今日は {name} さんの誕生日です")
        # 入社日・入社記念日
        h = _parse_date(u.get("hiredDate") or u.get("hireDate"))
        if h and (h[1], h[2]) == (today.month, today.day):
            if h[0] and h[0] < today.year:
                lines.append(f"🎉 {name} さんは本日で入社{today.year - h[0]}周年です"
                             f"({h[0]}年入社)")
            elif h[0] == today.year:
                lines.append(f"🌸 本日 {name} さんが入社しました")

    if not lines:
        logger.info("本日の誕生日・入社記念日は該当なし")
        return 0

    msg = f"📅 {today.month}/{today.day} の記念日\n" + "\n".join(lines)
    print(msg)
    if args.dry_run:
        print("[DRY-RUN] 通知は送りません")
        return 0

    # 通知先(氏名→userId)
    name_to_id = { _display_name(u).replace(" ", ""): (u.get("userId") or u.get("id"))
                   for u in users }
    sent = 0
    for nm in NOTIFY_NAMES:
        uid = name_to_id.get(nm.replace(" ", ""))
        if not uid:
            logger.warning(f"通知先が見つかりません: {nm}")
            continue
        if _send_text(uid, msg):
            sent += 1
    logger.info(f"記念日通知送信: {sent}/{len(NOTIFY_NAMES)} 名")
    return 0 if sent else 1


if __name__ == "__main__":
    raise SystemExit(main())
