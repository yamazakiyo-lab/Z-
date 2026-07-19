"""顧客検索ページ — 納入先(注文者)会社名から、その取引先の工番・写真を探す。

データ源は写真検索と同じ Azure AI Search インデックス（client_name は索引済み）。
納入先ごとに工番を束ねて表示し、各工番から FMP SEARCH の写真へ飛べる。
そのため一覧に出る工番は必ず写真がある（＝「写真」で空振りしない）。

総合検索APPの1メニュー。search_app.py の st.navigation から呼ばれる。
"""
from __future__ import annotations

from typing import Dict

import streamlit as st


# ── AI Search クライアント（写真検索と同じ資格情報を利用） ───────────────────────
@st.cache_resource(show_spinner="Azure AI Search に接続中...")
def _get_search_client():
    try:
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient
        from rag.config import SEARCH_INDEX_NAME, ensure_search_credentials

        endpoint, api_key = ensure_search_credentials()
        return SearchClient(endpoint, SEARCH_INDEX_NAME, AzureKeyCredential(api_key))
    except EnvironmentError as e:
        st.error(str(e))
        st.stop()
    except ImportError:
        st.error(
            "依存ライブラリが不足しています。\n"
            "`pip install azure-search-documents python-dotenv streamlit` を実行してください。"
        )
        st.stop()


# ── 納入先ごとに工番を集約（重い処理なのでキャッシュ） ─────────────────────────
@st.cache_data(show_spinner="納入先一覧を読み込み中...", ttl=600)
def _load_client_index() -> Dict[str, dict]:
    """インデックス全件を走査し、納入先ごとに工番と件数を集約する。

    戻り値: {client: {"worknos": {workno: {"name": 工事名, "count": 件数}},
                      "total": 写真総数, "address": 住所, "tel": 電話}}
    ※ 納入先名は発注者一覧表由来の正式名称（株式会社等を含む）。
    """
    client = _get_search_client()
    clients: Dict[str, dict] = {}
    page = 1000
    skip = 0
    while True:
        rows = list(
            client.search(
                search_text="*",
                select=["workno", "workno_name", "client_name", "client_address", "client_tel"],
                top=page,
                skip=skip,
            )
        )
        if not rows:
            break
        for d in rows:
            cl = (d.get("client_name") or "").strip()
            w = (d.get("workno") or "").strip()
            if not cl or not w:
                continue
            c = clients.setdefault(
                cl, {"worknos": {}, "total": 0, "address": "", "tel": ""}
            )
            c["total"] += 1
            if not c["address"] and d.get("client_address"):
                c["address"] = d["client_address"]
            if not c["tel"] and d.get("client_tel"):
                c["tel"] = d["client_tel"]
            wk = c["worknos"].setdefault(w, {"name": "", "count": 0})
            wk["count"] += 1
            if not wk["name"] and d.get("workno_name"):
                wk["name"] = d["workno_name"]
        skip += page
        if len(rows) < page:
            break
        if skip >= 100000:  # AI Search の skip 上限ガード
            break
    return clients


@st.cache_data(show_spinner=False, ttl=600)
def _index_updated() -> str:
    """元マスタCSV(工事一覧表)の更新日を返す。取得できなければ空。"""
    import os
    from datetime import datetime
    from pathlib import Path as _P
    base = os.getenv(
        "GD_EXTRACTION_DIR",
        r"Z:\takachiho\2to9_業務別フォルダ\91_工番別実績写真・動画\_GDExtraction",
    )
    try:
        p = _P(base) / "工事一覧表.csv"
        return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
    except Exception:
        return "－"


# ── UI ────────────────────────────────────────────────────────────────────────
st.page_link("app_pages/home.py", label="ホームに戻る", icon="🏠")

st.title("🏢 顧客検索")
st.caption("顧客（注文者）の会社名から、その取引先の工番・写真を探せます。正式名称・住所つき。")
st.markdown("""
<style>
div[data-testid="stTextInput"] input {
    border: 2px solid #21A159 !important;
    border-radius: 8px !important;
    padding: 10px 14px !important;
}
div[data-testid="stTextInput"] input:focus {
    border-color: #44CC77 !important;
    box-shadow: 0 0 0 2px rgba(33,161,89,0.25) !important;
}
</style>
""", unsafe_allow_html=True)
st.divider()

kw_raw = st.text_input(
    label="納入先名（部分一致）",
    placeholder="例: 高千穂 / トータス / 須藤",
)
max_rows = st.number_input("最大表示件数（納入先）", min_value=10, max_value=200, value=50, step=10)

kw = kw_raw.strip().lower()

if not kw:
    st.info("納入先名の一部を入力してください。")
    st.stop()

clients = _load_client_index()
hits = [
    (cl, info) for cl, info in clients.items()
    if kw in cl.lower()
]
# 写真が多い納入先を上に
hits.sort(key=lambda x: -x[1]["total"])

if not hits:
    st.warning(f"「{kw_raw.strip()}」に一致する納入先は見つかりませんでした。")
    st.stop()

st.markdown(f"**{len(hits)} 件** の顧客が見つかりました（写真がある工番のみ集計）。")
st.caption(f"データ更新日: {_index_updated()}（元データ: 工事一覧表・発注者一覧表）")
if len(hits) > max_rows:
    st.caption(f"※ 先頭 {int(max_rows)} 件を表示しています。キーワードを絞ると件数が減ります。")
    hits = hits[: int(max_rows)]

st.divider()

for cl, info in hits:
    worknos = info["worknos"]
    with st.expander(f"🏢 {cl} 　（工番 {len(worknos)} 件 / 写真 {info['total']} 件）"):
        # 住所・電話（発注者一覧表より）
        _addr, _tel = info.get("address", ""), info.get("tel", "")
        if _addr or _tel:
            st.caption(
                "　".join(x for x in [_addr, (f"TEL {_tel}" if _tel else "")] if x)
            )
        for wno, wk in sorted(worknos.items()):
            col_info, col_btn = st.columns([5, 2])
            with col_info:
                st.markdown(f"**`{wno}`** 　{wk.get('name') or '－'}")
                st.caption(f"写真 {wk.get('count', 0)} 枚")
            with col_btn:
                if st.button("📷 写真を見る", key=f"nj_{cl}_{wno}", use_container_width=True):
                    # FMP SEARCH へ工番を渡して遷移 → あちらで自動検索
                    st.session_state["jump_workno"] = wno
                    st.switch_page("app_pages/fmp_search.py")
            st.divider()
