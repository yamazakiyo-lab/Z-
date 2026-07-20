"""学習協力の pending を「氏名つき」で確認する診断。

なぜ必要か:
    check_annotation_state.py は pending を userId のまま表示するため、
    誰がコメント待ちなのかが読み取れない。
    「写真は届いたのにコメントすると案内文が返る」場合、
    その人が pending に載っていないことが原因なので、氏名で突き合わせる。

使い方(デスクトップ):
    py diag_annotation_pending.py
    py diag_annotation_pending.py 山嵜喜隆     # 特定の人だけ詳しく見る

読み取り専用。状態は一切変更しない。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))


def _fmt(ts: str) -> str:
    if not ts:
        return "(なし)"
    try:
        return datetime.fromisoformat(ts).astimezone(JST).strftime("%m/%d %H:%M JST")
    except Exception:
        return ts


def main() -> int:
    try:
        import lw_annotation_bot as bot
    except Exception as e:
        print(f"[ERROR] lw_annotation_bot の読み込みに失敗: {e}")
        return 1

    state = bot._load_annotation_state()
    names = bot._load_user_names()          # {userId: 氏名}
    pending = state.get("pending", {})
    users = state.get("users", [])
    pool = state.get("unannotated_pool", [])

    print("=" * 60)
    print("  学習協力 pending 診断")
    print("=" * 60)
    print(f"  登録ユーザー   : {len(users)}人")
    print(f"  コメント待ち   : {len(pending)}人")
    print(f"  未コメント在庫 : {len(pool)}件")

    print("\n【コメント待ちの人】")
    if not pending:
        print("  (なし)")
    for uid, v in pending.items():
        nm = names.get(uid, "(氏名未取得)")
        print(f"  {nm:<12} 送信={_fmt(v.get('sent_at', ''))} 工番={v.get('job_number', '') or '-'}")

    target = sys.argv[1] if len(sys.argv) > 1 else "山嵜喜隆"
    norm = lambda s: "".join((s or "").split())
    hit = [u for u, n in names.items() if norm(n) == norm(target)]

    print(f"\n【{target} の状態】")
    if not hit:
        print("  ⚠️ LINE WORKS の氏名一覧に見つかりません(userId未解決)")
        return 0
    for uid in hit:
        print(f"  userId        : {uid}")
        print(f"  登録ユーザー  : {'はい' if uid in users else 'いいえ ← --add-user が必要'}")
        if uid in pending:
            v = pending[uid]
            print(f"  コメント待ち  : はい (送信={_fmt(v.get('sent_at', ''))})")
            print("  → 状態は正常。コメントすれば記録されるはず。")
        else:
            print("  コメント待ち  : いいえ ← 案内文が返る原因はこれ")
            print("  → 10:00の配信対象external外だったか、既にコメント済みで解除された可能性。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
