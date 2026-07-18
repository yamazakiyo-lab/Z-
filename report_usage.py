"""検索アプリ + Teams の利用/未利用 一覧レポート。

サインインログ(要 Entra P1/P2)ではなく、Microsoft 365 の「利用状況レポート」
(getTeamsUserActivityUserDetail / Reports.Read.All)を使うので、無料テナントでも動く。
これに検索アプリの利用ログ(app_usage.json / Blob)と Entra ディレクトリ
(Directory.Read.All)を突き合わせ、次を一覧化する:

  - Entra 有効メンバーごとの「検索アプリ利用」「Teams利用」の ○/×
  - どちらも未利用の人(＝ほぼ使えていない/使っていない人)

前提:
  1) tseg-signin-reader に「Reports.Read.All」(アプリケーションの許可)を付与し
     "管理者の同意を与える" を実行済み(既存の Directory.Read.All に追加)。
  2) Microsoft 365 管理センターでレポートの匿名化を解除しておく:
     設定 > 組織設定 > レポート > 「レポートで、ユーザー名/グループ名/サイト名を
     非表示にする」の チェックを外す。
     (匿名化が有効だと UPN が伏字になり氏名突合できない)
  3) .env(スクリプトと同じフォルダ) に:
       GRAPH_TENANT_ID / GRAPH_CLIENT_ID / GRAPH_CLIENT_SECRET
       AZURE_BLOB_CONNECTION_STRING   (検索アプリ利用ログ用。無くてもTeams分は出る)
       LW_BLOB_CONTAINER              (省略時 lw-raw)
       USAGE_DAYS                     (集計日数。省略時 7)

実行(デスクトップ):
  python report_usage.py
出力:
  usage_report_YYYYMMDD.csv  (氏名 / UPN / 検索アプリ / Teams / どちらも未利用)
  画面にサマリー
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"), encoding="utf-8")
except Exception:
    pass

import requests

TENANT_ID = os.environ.get("GRAPH_TENANT_ID", "")
CLIENT_ID = os.environ.get("GRAPH_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GRAPH_CLIENT_SECRET", "")
BLOB_CONN = os.environ.get("AZURE_BLOB_CONNECTION_STRING", "")
BLOB_CONTAINER = os.environ.get("LW_BLOB_CONTAINER", "lw-raw")
DAYS = int(os.environ.get("USAGE_DAYS", "7"))

GRAPH = "https://graph.microsoft.com/v1.0"


def _fail(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def get_token() -> str:
    if not (TENANT_ID and CLIENT_ID and CLIENT_SECRET):
        _fail("GRAPH_TENANT_ID / GRAPH_CLIENT_ID / GRAPH_CLIENT_SECRET が未設定です(.env を確認)")
    r = requests.post(
        f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
        data={
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }, timeout=30,
    )
    if r.status_code != 200:
        _fail(f"トークン取得失敗: {r.status_code} {r.text[:300]}")
    return r.json()["access_token"]


def graph_get_all(token: str, url: str) -> list[dict]:
    out: list[dict] = []
    headers = {"Authorization": f"Bearer {token}"}
    while url:
        r = requests.get(url, headers=headers, timeout=60)
        if r.status_code != 200:
            _fail(f"Graph取得失敗 ({url[:80]}...): {r.status_code} {r.text[:300]}")
        body = r.json()
        out.extend(body.get("value", []))
        url = body.get("@odata.nextLink")
    return out


def get_teams_active(token: str) -> tuple[set[str], bool]:
    """Teams利用状況レポートから、直近DAYS日に活動のあった UPN(小文字) 集合を返す。

    戻り値: (active_upns, anonymized)
      anonymized=True の場合、UPNが伏字化されていて突合不可(管理センターで解除が必要)。
    """
    period = "D7" if DAYS <= 7 else ("D30" if DAYS <= 30 else "D90")
    url = f"{GRAPH}/reports/getTeamsUserActivityUserDetail(period='{period}')"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=90)
    if r.status_code != 200:
        _fail(
            f"Teamsレポート取得失敗: {r.status_code} {r.text[:300]}\n"
            "  → tseg-signin-reader に Reports.Read.All(アプリ許可)＋管理者同意が必要です。"
        )
    text = r.content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    active: set[str] = set()
    anonymized = False
    total = 0
    for row in reader:
        total += 1
        upn = (row.get("User Principal Name") or "").strip()
        last = (row.get("Last Activity Date") or "").strip()
        if upn and "@" not in upn:
            anonymized = True
        if last:  # 期間内に何らかの活動があった
            active.add(upn.lower())
    print(f"Teamsレポート: {total} 行 / 活動あり {len(active)} 名 (period={period})")
    return active, anonymized


def get_search_used() -> set[str]:
    """検索アプリ利用ログ(app_usage.json)から直近DAYS日に利用した UPN(小文字)集合。"""
    if not BLOB_CONN:
        print("[WARN] AZURE_BLOB_CONNECTION_STRING 未設定 → 検索アプリ利用は全員×で出力します")
        return set()
    try:
        from azure.storage.blob import BlobServiceClient
        svc = BlobServiceClient.from_connection_string(BLOB_CONN)
        cont = svc.get_container_client(BLOB_CONTAINER)
        data = json.loads(cont.download_blob("app_usage.json").readall())
    except Exception as e:
        print(f"[WARN] app_usage.json 読込失敗({e}) → 検索アプリ利用は全員×")
        return set()
    cutoff = (date.today() - timedelta(days=DAYS)).isoformat()
    return {u.lower() for u, d in data.items() if isinstance(d, str) and d >= cutoff}


def _norm(s: str) -> str:
    return "".join((s or "").split())


# レポート要約の既定の送信先（氏名）。環境変数 LW_REPORT_RECIPIENT_NAMES
# （カンマ区切りの氏名）で上書き可能。
DEFAULT_REPORT_RECIPIENTS = ["山嵜喜隆", "小山智樹", "昆哲郎", "松尾崇", "松﨑誠一", "山嵜絵里"]


def notify_lw(summary: str, dry_run: bool) -> None:
    """レポート要約を LW BOT で複数の受信者へ送信する。

    受信者は氏名で指定し、lw_user_names.json を逆引きして userId を特定する。
    既定は DEFAULT_REPORT_RECIPIENTS。環境変数で上書き可:
      LW_REPORT_RECIPIENT_NAMES … カンマ区切りの氏名
    """
    try:
        import lw_annotation_bot as bot
    except Exception as e:
        print(f"[WARN] LW通知スキップ(botモジュール読込失敗): {e}")
        return
    env_names = os.environ.get("LW_REPORT_RECIPIENT_NAMES", "").strip()
    targets = [n.strip() for n in env_names.split(",") if n.strip()] or DEFAULT_REPORT_RECIPIENTS
    try:
        names = bot._load_user_names()  # {userId: 氏名}
    except Exception as e:
        print(f"[WARN] LW通知スキップ(氏名一覧の取得失敗): {e}")
        return
    name_to_uid = {_norm(nm): uid for uid, nm in names.items()}
    if dry_run:
        bot.DRY_RUN = True
    sent = 0
    for target in targets:
        uid = name_to_uid.get(_norm(target))
        if not uid:
            print(f"[WARN] LW通知先が見つかりません(氏名不一致): {target}")
            continue
        ok = bot._send_text(uid, summary)
        if ok:
            sent += 1
        print(f"[LW] {target} へ送信{'(DRY-RUN)' if dry_run else ''}: {'OK' if ok else 'NG'} → {uid}")
    print(f"[LW] レポート要約 送信 {sent}/{len(targets)} 名")


def main() -> None:
    ap = argparse.ArgumentParser(description="検索アプリ+Teams 利用レポート")
    ap.add_argument("--no-lw", action="store_true", help="LW通知を送らない(CSVのみ)")
    ap.add_argument("--dry-run", action="store_true", help="LW通知をドライラン(実送信しない)")
    args = ap.parse_args()

    token = get_token()
    print(f"集計期間: 直近 {DAYS} 日\n")

    teams_active, anonymized = get_teams_active(token)
    if anonymized:
        print(
            "\n[!!] Teamsレポートのユーザー名が匿名化されています。\n"
            "     Microsoft 365 管理センター > 設定 > 組織設定 > レポート で\n"
            "     「...ユーザー名/グループ名/サイト名を非表示にする」のチェックを外してから\n"
            "     再実行してください（そうしないと Teams列は正しく突合できません）。\n"
        )
    search_used = get_search_used()

    users = graph_get_all(
        token,
        f"{GRAPH}/users?$select=displayName,userPrincipalName,accountEnabled,userType&$top=999",
    )
    members = [
        u for u in users
        if u.get("accountEnabled") and (u.get("userType") or "Member") == "Member"
    ]
    print(f"対象ユーザー(有効メンバー): {len(members)} 名\n")

    today = datetime.now().strftime("%Y%m%d")
    out_path = Path(__file__).with_name(f"usage_report_{today}.csv")

    def _kind(upn: str) -> str:
        """UPNのローカル部から種別を判定。共有端末(スマホ/タブレット)か個人か。"""
        local = upn.split("@", 1)[0]
        for pat in ("-sp", "-tab", "sp0", "tab0"):
            if pat in local:
                return "共有端末"
        return "個人"

    # カテゴリ別・種別別の未利用「氏名」を集める（誰が使っていないか分かるように）
    teams_no_ind: list[str] = []
    teams_no_sh: list[str] = []
    search_no_ind: list[str] = []
    search_no_sh: list[str] = []
    both_no: list[str] = []

    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["種別", "氏名", "UPN(メール)", "検索アプリ", "Teams", "どちらも未利用"])
        for u in sorted(members, key=lambda x: (_kind((x.get("userPrincipalName") or "")),
                                                x.get("displayName") or "")):
            upn = (u.get("userPrincipalName") or "").lower()
            name = u.get("displayName") or ""
            kind = _kind(upn)
            s_ok = upn in search_used
            t_ok = upn in teams_active
            both_x = (not s_ok) and (not t_ok)
            if not t_ok:
                (teams_no_ind if kind == "個人" else teams_no_sh).append(name)
            if not s_ok:
                (search_no_ind if kind == "個人" else search_no_sh).append(name)
            if both_x:
                both_no.append(f"[{kind}] {name}")
            w.writerow([kind, name, upn, "○" if s_ok else "×", "○" if t_ok else "×",
                        "★" if both_x else ""])

    def _join(names: list[str], cap: int = 40) -> str:
        if not names:
            return "なし"
        if len(names) <= cap:
            return "、".join(names)
        return "、".join(names[:cap]) + f" …他{len(names) - cap}名"

    print("===== サマリー =====")
    print(f"Teams 未活動: 個人 {len(teams_no_ind)} 名 / 共有端末 {len(teams_no_sh)} 台")
    print(f"検索アプリ 未利用: 個人 {len(search_no_ind)} 名 / 共有端末 {len(search_no_sh)} 台")
    print(f"どちらも未利用: {len(both_no)} 件")
    print(f"[CSV] {out_path.name}  （種別列で 個人/共有端末 を区別）")

    # ── LW通知用の要約（氏名入り） ─────────────────────────────────────
    d = f"{today[:4]}/{today[4:6]}/{today[6:]}"
    summary = (
        f"📊検索アプリ＋Teams 利用状況（直近{DAYS}日 / {d}時点）\n\n"
        f"【Teams 未活動】個人{len(teams_no_ind)}名・共有端末{len(teams_no_sh)}台\n"
        f"　個人: {_join(teams_no_ind)}\n"
        f"　共有端末: {_join(teams_no_sh)}\n\n"
        f"【検索アプリ 未利用】個人{len(search_no_ind)}名・共有端末{len(search_no_sh)}台\n"
        f"　個人: {_join(search_no_ind)}\n"
        f"　共有端末: {_join(search_no_sh)}\n\n"
        f"※検索アプリは記録開始直後はデータが少なく全員が未利用に見えます。数週で正確になります。\n"
        f"詳細CSV: {out_path.name}"
    )
    print("\n----- LW送信要約プレビュー -----\n" + summary + "\n")

    if args.no_lw:
        print("[LW] --no-lw 指定のため通知は送信しません。")
    else:
        notify_lw(summary, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
