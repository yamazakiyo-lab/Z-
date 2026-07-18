"""工番検索ページ。

工事名・納入先・工番の一部から工番を探し、見つけた工番で FMP SEARCH へ飛べる。
データ源は写真検索と同じ Azure AI Search インデックス。
そのため一覧に出る工番は必ず写真があり、「写真を見る」で空振りしない。
（※撮影がまだ無い新規工番は出ない。それは写真検索しても空なので実害なし）

総合検索APPの1メニュー。search_app.py の st.navigation から呼ばれる。
※ set_page_config はナビ入口(search_app.py)で一度だけ設定するため、ここでは呼ばない。
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


# ── 工番一覧をインデックスから集約（重い処理なのでキャッシュ） ───────────────────
@st.cache_data(show_spinner="工番一覧を読み込み中...", ttl=600)
def _load_workno_index() -> Dict[str, dict]:
    """インデックス全件を走査し、工番ごとに 工事名・納入先・写真件数 を集約して返す。

    戻り値: {workno: {"name": 工事名, "client": 納入先, "count": 件数}}
    """
    client = _get_search_client()
    worknos: Dict[str, dict] = {}
    page = 1000
    skip = 0
    while True:
        rows = list(
            client.search(
                search_text="*",
                select=["workno", "workno_name", "client_name"],
                top=page,
                skip=skip,
            )
        )
        if not rows:
            break
        for d in rows:
            w = (d.get("workno") or "").strip()
            if not w:
                continue
            e = worknos.setdefault(w, {"name": "", "client": "", "count": 0})
            e["count"] += 1
            if not e["name"] and d.get("workno_name"):
                e["name"] = d["workno_name"]
            if not e["client"] and d.get("client_name"):
                e["client"] = d["client_name"]
        skip += page
        if len(rows) < page:
            break
        if skip >= 100000:  # AI Search の skip 上限ガード
            break
    return worknos


def _match(row: dict, workno: str, kw: str) -> bool:
    """工番・工事名・納入先のいずれかに kw（小文字化済み）が部分一致すれば True。"""
    if kw in workno.lower():
        return True
    if kw in (row.get("name") or "").lower():
        return True
    if kw in (row.get("client") or "").lower():
        return True
    return False


# ── 完成/未成マスタ（Blobの workno_master.json）を読む ─────────────────────────
@st.cache_data(show_spinner=False, ttl=600)
def _load_kanryo_map() -> Dict[str, str]:
    """workno_master.json (Blob) から {workno: "完成"/"未成"} を読む。

    AZURE_BLOB_CONNECTION_STRING が未設定、または読み込み失敗・kanryo未収録の場合は
    空dictを返す（＝完成/未成フィルタは自動的に非表示になり、従来どおりの挙動）。
    """
    import json
    import os

    conn = os.getenv("AZURE_BLOB_CONNECTION_STRING", "")
    container_name = os.getenv("LW_BLOB_CONTAINER", "lw-raw")
    if not conn:
        return {}
    try:
        from azure.storage.blob import BlobServiceClient

        svc = BlobServiceClient.from_connection_string(conn)
        cont = svc.get_container_client(container_name)
        data = cont.download_blob("workno_master.json").readall()
        payload = json.loads(data)
        worknos = payload.get("worknos", {})
        return {
            w: (info.get("kanryo") or "")
            for w, info in worknos.items()
            if (info.get("kanryo") or "")
        }
    except Exception:
        return {}


# ── UI ────────────────────────────────────────────────────────────────────────
st.title("🔎 工番検索")
st.caption("工事名・納入先・工番の一部から工番を探せます。（機械名は工事名に含まれることが多いので工事名検索でヒットします）")
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
    label="キーワード（工事名・納入先・工番の一部）",
    placeholder="例: トータス / 高千穂 / 4031",
)
max_rows = st.number_input("最大表示件数", min_value=20, max_value=500, value=100, step=20)

# 完成/未成フィルタ（マスタに完成/未成データがある時だけ表示）
kanryo_map = _load_kanryo_map()
kanryo_choice = "すべて"
if kanryo_map:
    kanryo_choice = st.radio(
        "状態で絞り込み", ["すべて", "完成", "未成"], horizontal=True, index=0
    )

kw = kw_raw.strip().lower()

if not kw:
    st.info("工事名・納入先・工番の一部を入力してください。")
    st.stop()

index = _load_workno_index()
hits = [
    (w, row) for w, row in index.items()
    if _match(row, w, kw)
]
# 工番の昇順で安定表示
hits.sort(key=lambda x: x[0])

# 完成/未成フィルタ
if kanryo_map and kanryo_choice != "すべて":
    hits = [(w, row) for (w, row) in hits if kanryo_map.get(w) == kanryo_choice]

if not hits:
    st.warning(f"「{kw_raw.strip()}」に一致する工番は見つかりませんでした。")
    st.stop()

st.markdown(f"**{len(hits)} 件** の工番が見つかりました（写真がある工番のみ）。")
if len(hits) > max_rows:
    st.caption(f"※ 先頭 {int(max_rows)} 件を表示しています。キーワードを絞ると件数が減ります。")
    hits = hits[: int(max_rows)]

st.divider()

# ── 見出し行 ──
h1, h2, h3, h4 = st.columns([2, 4, 3, 2])
h1.markdown("**工番**")
h2.markdown("**工事名**")
h3.markdown("**納入先**")
h4.markdown("**写真へ**")

_KANRYO_BADGE = {"完成": "🟢 完成", "未成": "🔴 未成"}
for wno, row in hits:
    c1, c2, c3, c4 = st.columns([2, 4, 3, 2])
    c1.markdown(f"`{wno}`")
    _kn = kanryo_map.get(wno)
    if _kn:
        c1.caption(_KANRYO_BADGE.get(_kn, _kn))
    c2.write(row.get("name") or "－")
    c3.write(row.get("client") or "－")
    with c4:
        st.caption(f"{row.get('count', 0)} 件")
        if st.button("📷 写真を見る", key=f"jump_{wno}", use_container_width=True):
            # FMP SEARCH ページへ工番を渡して遷移 → あちらで自動検索
            st.session_state["jump_workno"] = wno
            st.switch_page("app_pages/fmp_search.py")
