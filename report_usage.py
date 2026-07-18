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


def main() -> None:
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
    both_no: list[str] = []
    teams_no: list[str] = []
    search_no: list[str] = []

    def _kind(upn: str) -> str:
        """UPNのローカル部から種別を判定。共有端末(スマホ/タブレット)か個人か。"""
        local = upn.split("@", 1)[0]
        for pat in ("-sp", "-tab", "sp0", "tab0"):
            if pat in local:
                return "共有端末"
        return "個人"

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
            tag = f"[{kind}] {name} <{upn}>"
            if not s_ok:
                search_no.append(tag)
            if not t_ok:
                teams_no.append(tag)
            if both_x:
                both_no.append(tag)
            w.writerow([kind, name, upn, "○" if s_ok else "×", "○" if t_ok else "×",
                        "★" if both_x else ""])

    print("===== サマリー =====")
    print(f"検索アプリ 未利用: {len(search_no)} 名 / Teams 未利用: {len(teams_no)} 名")
    print(f"どちらも未利用: {len(both_no)} 件")
    print(f"[CSV] {out_path.name}  （種別列で 個人/共有端末 を区別）")
    if teams_no:
        print("\n--- Teams 未利用(直近{}日 活動なし) ---".format(DAYS))
        for line in teams_no:
            print("  ", line)


if __name__ == "__main__":
    main()
