"""QUOTATION SEARCH — 過去見積検索(見積メンバー限定)。

231_見積書から取り込んだ過去見積(media_type="mitsumori")を、顧客名・件名・
型式・工番で検索する。金額は営業機密のため、メニュー表示(search_app.py)と
このページの両方で見積メンバーのみに制限する。
"""
from __future__ import annotations

import os
import re

import streamlit as st

# ── 権限(ai_qa.pyの見積権限と同じ既定。Easy Auth表示名の別名も吸収) ─────────
_ALLOWED = {n.strip() for n in os.getenv(
    "QA_MITSUMORI_ALLOWED_NAMES",
    "山嵜喜隆,山嵜絵里,昆哲郎,松尾崇,松﨑誠一,滝沢雄一").split(",") if n.strip()}
_ALIAS = {"専務": "山嵜絵里", "matsuo": "松尾崇", "ayase2": "松﨑誠一",
          "yamazakiyo@tseg.co.jp": "山嵜喜隆", "yamazakiyo": "山嵜喜隆",
          "t_user03": "昆哲郎", "s_user01": "山嵜絵里", "s_user02": "滝沢雄一"}


def _norm(s: str) -> str:
    return (s or "").replace(" ", "").replace("　", "").replace("﨑", "崎").lower()


def _current_name() -> str:
    try:
        from urllib.parse import unquote
        hdrs = st.context.headers or {}
        raw = (hdrs.get("X-MS-CLIENT-PRINCIPAL-NAME")
               or hdrs.get("X-Ms-Client-Principal-Name") or "").strip()
        return unquote(raw).strip()
    except Exception:
        return ""


def _is_allowed(name: str) -> bool:
    a = _norm(_ALIAS.get(_norm(name), name) if _norm(name) in
              {_norm(k) for k in _ALIAS} else name)
    # 別名変換(キーの正規化込み)
    for k, v in _ALIAS.items():
        if _norm(k) == _norm(name):
            a = _norm(v)
            break
    if not a:
        return False
    for n in _ALLOWED:
        nn = _norm(n)
        if nn and (nn in a or a in nn):
            return True
    return False


@st.cache_resource
def _get_search_client():
    from azure.core.credentials import AzureKeyCredential
    from azure.search.documents import SearchClient
    from rag.config import SEARCH_INDEX_NAME, ensure_search_credentials
    endpoint, api_key = ensure_search_credentials()
    return SearchClient(endpoint, SEARCH_INDEX_NAME, AzureKeyCredential(api_key))


RX_GOKEI = re.compile(r"合計金額:\s*([\d,]+)円")
RX_DATE = re.compile(r"見積日:\s*([0-9\-]+)")


def main() -> None:
    st.page_link("app_pages/home.py", label="ホームに戻る", icon="🏠")
    st.title("💰 QUOTATION SEARCH")

    name = _current_name()
    if not _is_allowed(name):
        st.error("このメニューは見積作成メンバー専用です。閲覧権限がありません。")
        st.stop()

    st.caption("過去見積(2013〜)を顧客名・件名・型式・工番で検索します。"
               "金額は自動抽出値のため、正確な内容は必ずファイル本体で確認してください。")
    st.divider()

    col1, col2 = st.columns([3, 1])
    with col1:
        q = st.text_input("検索キーワード(顧客名・件名・型式・工番など)",
                          placeholder="例: サトー精機 NC1-110 / 4031-00 / オーバーホール")
    with col2:
        top = st.selectbox("表示件数", [20, 50, 100], index=1)

    if not q:
        st.info("キーワードを入力してください。スペース区切りで複数語も可。")
        return

    client = _get_search_client()
    try:
        results = list(client.search(
            search_text=q, top=top,
            filter="media_type eq 'mitsumori'",
            select=["workno", "workno_name", "file_name", "file_path",
                    "capture_date_raw", "content_text"]))
    except Exception as e:
        st.error(f"検索に失敗しました: {e}")
        return

    if not results:
        st.warning("該当する見積が見つかりませんでした。キーワードを短くしてみてください。")
        return

    rows = []
    for r in results:
        text = r.get("content_text") or ""
        m_g = RX_GOKEI.search(text)
        rows.append({
            "見積日": r.get("capture_date_raw") or "",
            "顧客/件名": r.get("workno_name") or "",
            "金額": m_g.group(1) + "円" if m_g else "",
            "工番": r.get("workno") or "",
            "ファイル名": r.get("file_name") or "",
            "_path": r.get("file_path") or "",
        })
    rows.sort(key=lambda x: x["見積日"], reverse=True)

    st.write(f"{len(rows)} 件ヒット(関連度順に取得し、日付の新しい順に表示)")
    st.dataframe(
        [{k: v for k, v in row.items() if k != "_path"} for row in rows],
        use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("ファイルの場所")
    st.caption("開きたい見積のパスをコピーして、エクスプローラーのアドレス欄に貼り付けてください。")
    for row in rows[:20]:
        with st.expander(f"{row['見積日']} {row['顧客/件名']} {row['金額']}"):
            st.code(row["_path"], language=None)


main()
