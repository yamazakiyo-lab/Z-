"""サインインログ抽出レポート（フェーズA）。

Microsoft Graph のサインインログ(過去N日)を取得し、次を一覧化する:
  - 検索アプリ(TSEG-FM-SEARCH)にログインした人／していない人
  - Teams にログインした人／していない人
  - ログイン失敗(＝ログインできない人)＋失敗理由

出力:
  - 画面にサマリー
  - CSV: signin_report_YYYYMMDD.csv（表示名 / UPN / 検索アプリ / Teams / 失敗理由）
  - 失敗のみ: signin_failures_YYYYMMDD.csv

前提(.env or 環境変数):
  GRAPH_TENANT_ID       ディレクトリ(テナント)ID
  GRAPH_CLIENT_ID       アプリ(クライアント)ID  (tseg-signin-reader)
  GRAPH_CLIENT_SECRET   クライアントシークレットの「値」
  SEARCH_APP_ID         検索アプリのappId(省略時は既定値)
  SIGNIN_DAYS           集計日数(省略時7)

実行(Z:/Graphが使えるデスクトップで):
  python report_signin.py

注意: サインインログAPIの利用には Entra ID P1/P2 ライセンスが必要です。
"""
from __future__ import annotations

import csv
import os
import sys
from datetime import datetime, timedelta, timezone
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
# 検索アプリ(TSEG-FM-SEARCH)の appId（認証画面で確認済みの既定値）
SEARCH_APP_ID = os.environ.get("SEARCH_APP_ID", "31a26b25-cd60-4ff5-9ec3-3f7fd88275c5")
DAYS = int(os.environ.get("SIGNIN_DAYS", "7"))

GRAPH = "https://graph.microsoft.com/v1.0"


def _fail(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def get_token() -> str:
    if not (TENANT_ID and CLIENT_ID and CLIENT_SECRET):
        _fail("GRAPH_TENANT_ID / GRAPH_CLIENT_ID / GRAPH_CLIENT_SECRET が未設定です(.env を確認)")
    url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }
    r = requests.post(url, data=data, timeout=30)
    if r.status_code != 200:
        _fail(f"トークン取得失敗: {r.status_code} {r.text[:300]}")
    return r.json()["access_token"]


def graph_get_all(token: str, url: str) -> list[dict]:
    """@odata.nextLink をたどって全ページ取得。"""
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


def main() -> None:
    token = get_token()
    since = (datetime.now(timezone.utc) - timedelta(days=DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"集計期間: 過去 {DAYS} 日 ({since} 以降)")

    # ── サインインログ取得 ──────────────────────────────────────────────
    signin_url = (
        f"{GRAPH}/auditLogs/signIns"
        f"?$filter=createdDateTime ge {since}&$top=1000"
    )
    signins = graph_get_all(token, signin_url)
    print(f"サインインイベント数: {len(signins)}")

    search_ok: set[str] = set()   # 検索アプリに成功ログインした UPN
    teams_ok: set[str] = set()    # Teams に成功ログインした UPN
    failures: dict[str, list[tuple[str, int, str]]] = {}  # UPN -> [(app, code, reason)]

    for s in signins:
        upn = (s.get("userPrincipalName") or "").lower()
        if not upn:
            continue
        app_id = s.get("appId") or ""
        app_name = s.get("appDisplayName") or ""
        status = s.get("status") or {}
        code = status.get("errorCode", 0)
        if code == 0:  # 成功
            if app_id == SEARCH_APP_ID or "TSEG-FM-SEARCH" in app_name.upper():
                search_ok.add(upn)
            if "teams" in app_name.lower():
                teams_ok.add(upn)
        else:  # 失敗(＝ログインできない)
            reason = status.get("failureReason", "")
            failures.setdefault(upn, []).append((app_name, code, reason))

    # ── 対象ユーザー(有効なメンバー)取得 ────────────────────────────────
    users_url = (
        f"{GRAPH}/users?$select=displayName,userPrincipalName,accountEnabled,userType"
        f"&$top=999"
    )
    users = graph_get_all(token, users_url)
    members = [
        u for u in users
        if u.get("accountEnabled") and (u.get("userType") or "Member") == "Member"
    ]
    print(f"対象ユーザー(有効メンバー): {len(members)}")

    # ── レポート作成 ──────────────────────────────────────────────────
    today = datetime.now().strftime("%Y%m%d")
    rep_path = Path(__file__).with_name(f"signin_report_{today}.csv")
    fail_path = Path(__file__).with_name(f"signin_failures_{today}.csv")

    search_no: list[str] = []
    teams_no: list[str] = []
    with rep_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["表示名", "UPN(メール)", "検索アプリ", "Teams", "ログイン失敗有無"])
        for u in sorted(members, key=lambda x: x.get("displayName") or ""):
            upn = (u.get("userPrincipalName") or "").lower()
            name = u.get("displayName") or ""
            s_used = "○" if upn in search_ok else "×"
            t_used = "○" if upn in teams_ok else "×"
            has_fail = "あり" if upn in failures else ""
            if upn not in search_ok:
                search_no.append(f"{name} <{upn}>")
            if upn not in teams_ok:
                teams_no.append(f"{name} <{upn}>")
            w.writerow([name, upn, s_used, t_used, has_fail])

    # 失敗(できない人)の明細
    name_by_upn = {(u.get("userPrincipalName") or "").lower(): (u.get("displayName") or "") for u in members}
    with fail_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["表示名", "UPN(メール)", "アプリ", "エラーコード", "失敗理由"])
        for upn, items in sorted(failures.items()):
            for app_name, code, reason in items:
                w.writerow([name_by_upn.get(upn, ""), upn, app_name, code, reason])

    # ── サマリー表示 ──────────────────────────────────────────────────
    print("\n===== サマリー =====")
    print(f"検索アプリ 利用者: {len(search_ok)} 名 / 未利用: {len(search_no)} 名")
    print(f"Teams 利用者: {len(teams_ok)} 名 / 未利用: {len(teams_no)} 名")
    print(f"ログイン失敗(できない人の可能性): {len(failures)} 名")
    print(f"\n[CSV] 一覧: {rep_path.name}")
    print(f"[CSV] 失敗明細: {fail_path.name}")
    print("\n--- 検索アプリ 未利用者(先頭20) ---")
    for line in search_no[:20]:
        print("  ", line)


if __name__ == "__main__":
    main()
