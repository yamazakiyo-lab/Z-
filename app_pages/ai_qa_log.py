"""AI Q&A ログ閲覧 — 管理者専用ページ。

Blob(lw-raw/qa_log_YYYYMM.jsonl)に記録された全社員のQ&Aやり取りを閲覧する。
管理者(QA_LOG_ADMINS。既定: yamazakiyo@tseg.co.jp)以外がアクセスした場合は
その旨を表示して何も見せない。

環境変数:
    QA_LOG_ADMINS   閲覧を許可するUPN(カンマ区切り・小文字)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta

import streamlit as st

JST = timezone(timedelta(hours=9))
QA_LOG_CONTAINER = os.getenv("LW_BLOB_CONTAINER", "lw-raw")
ADMINS = {
    u.strip().lower()
    for u in os.getenv("QA_LOG_ADMINS", "yamazakiyo@tseg.co.jp").split(",")
    if u.strip()
}


def _current_upn() -> str:
    """Easy Authヘッダーからログインユーザーを取得(URLエンコードされた氏名をデコード)。"""
    try:
        from urllib.parse import unquote
        hdrs = st.context.headers or {}
        raw = (hdrs.get("X-MS-CLIENT-PRINCIPAL-NAME")
               or hdrs.get("X-Ms-Client-Principal-Name") or "").strip()
        return unquote(raw).strip().lower()
    except Exception:
        return ""


@st.cache_data(ttl=300, show_spinner="ログを読み込み中...")
def _load_log(month: str) -> list[dict]:
    """qa_log_YYYYMM.jsonl を読み、新しい順のリストで返す。"""
    conn = os.getenv("AZURE_BLOB_CONNECTION_STRING", "")
    if not conn:
        return []
    try:
        from azure.storage.blob import BlobServiceClient

        svc = BlobServiceClient.from_connection_string(conn)
        blob = svc.get_blob_client(QA_LOG_CONTAINER, f"qa_log_{month}.jsonl")
        raw = blob.download_blob().readall().decode("utf-8")
        recs = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except Exception:
                continue
        recs.reverse()  # 新しい順
        return recs
    except Exception:
        return []


def main() -> None:
    st.page_link("app_pages/home.py", label="ホームに戻る", icon="🏠")
    st.title("📋 AI Q&A ログ")

    upn = _current_upn()
    if upn not in ADMINS:
        st.warning("このページは管理者専用です。")
        st.stop()

    # ── 対象月の選択(直近6ヶ月) ──────────────────────────────────────────────
    now = datetime.now(JST)
    months = []
    y, m = now.year, now.month
    for _ in range(6):
        months.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    month = st.selectbox("対象月", months,
                         format_func=lambda s: f"{s[:4]}年{int(s[4:]):d}月")

    recs = _load_log(month)
    if not recs:
        st.info("この月の記録はありません。")
        return

    # ── 絞り込み ─────────────────────────────────────────────────────────────
    users = sorted({r.get("user", "") for r in recs})
    col1, col2 = st.columns([1, 2])
    with col1:
        sel_user = st.selectbox("利用者", ["(全員)"] + users)
    with col2:
        kw = st.text_input("キーワード(質問・回答を検索)")

    shown = recs
    if sel_user != "(全員)":
        shown = [r for r in shown if r.get("user") == sel_user]
    if kw.strip():
        k = kw.strip().lower()
        shown = [r for r in shown
                 if k in (r.get("q", "") + r.get("a", "")).lower()]

    st.caption(f"{len(shown)} 件 / 全 {len(recs)} 件")

    # ── 一覧表示 ─────────────────────────────────────────────────────────────
    for r in shown[:200]:
        ts = r.get("ts", "")[:16].replace("T", " ")
        user = r.get("user", "")
        q = r.get("q", "")
        title = f"{ts}　{user}　—　{q[:40]}{'…' if len(q) > 40 else ''}"
        with st.expander(title):
            st.markdown(f"**Q:** {q}")
            st.markdown(f"**A:** {r.get('a', '')}")
            srcs = r.get("sources") or []
            if srcs:
                st.caption("参照工番: " + ", ".join(srcs))
    if len(shown) > 200:
        st.caption("※表示は200件まで。キーワードで絞り込んでください。")


main()
