"""動治工具・測定具・消耗品検索ページ — 寄居・綾瀬の工具リストを探す。

データ源は Blob の tools_inventory.json（export_tools_inventory.py がデスクトップで生成）。
本番Azureは Z: を読めないため、Blob経由でデータを受け取る。
AZURE_BLOB_CONNECTION_STRING が必要（工番マスタ・部品在庫と同じ倉庫 lw-raw）。

※仕入先・単価はノウハウのため、そもそもデータに含めていない。

TSEG WORKS の1メニュー。search_app.py の st.navigation から呼ばれる。
"""
from __future__ import annotations

from typing import List, Tuple

import streamlit as st


# ── Blob から工具リストを読む（重いのでキャッシュ） ─────────────────────────
@st.cache_data(show_spinner="工具リストを読み込み中...", ttl=600)
def _load_tools() -> Tuple[List[dict], str]:
    """tools_inventory.json (Blob) から (items, generated_at) を返す。

    未設定・読み込み失敗・未アップロード時は ([], "")。
    """
    import json
    import os

    conn = os.getenv("AZURE_BLOB_CONNECTION_STRING", "")
    container_name = os.getenv("LW_BLOB_CONTAINER", "lw-raw")
    if not conn:
        return [], ""
    try:
        from azure.storage.blob import BlobServiceClient

        svc = BlobServiceClient.from_connection_string(conn)
        cont = svc.get_container_client(container_name)
        data = cont.download_blob("tools_inventory.json").readall()
        payload = json.loads(data)
        return payload.get("items", []), payload.get("generated_at", "")
    except Exception:
        return [], ""


# ── UI ────────────────────────────────────────────────────────────────────────
st.page_link("app_pages/home.py", label="ホームに戻る", icon="🏠")

st.title("🛠️ 動治工具・測定具・消耗品検索")
st.caption("品名・型式・仕様・メーカーから、寄居・綾瀬の動治工具／測定具／消耗品を探せます。")
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

items, generated_at = _load_tools()

if not items:
    st.warning(
        "工具リストのデータがまだ準備されていません。\n\n"
        "デスクトップ（Z:が見える環境）で `python export_tools_inventory.py` を実行して、"
        "リストを Blob にアップロードしてください。"
    )
    st.stop()

st.divider()

sites = sorted({it.get("site", "") for it in items if it.get("site")})
cats = sorted({it.get("cat", "") for it in items if it.get("cat")})

col1, col2 = st.columns([3, 2])
with col1:
    kw_raw = st.text_input(
        "キーワード（品名・型式・仕様・メーカー）",
        placeholder="例: トルクレンチ / ミツトヨ / ノギス / T4MN300",
    )
with col2:
    site_sel = st.selectbox("拠点で絞り込み", ["すべて"] + sites)

cat_sel = st.selectbox("カテゴリで絞り込み", ["すべて"] + cats)

if generated_at:
    st.caption(f"リスト更新日: {generated_at[:10]}　｜　登録 {len(items)} 品目")

kw = kw_raw.strip().lower()

if not kw and cat_sel == "すべて" and site_sel == "すべて":
    st.info("キーワードを入力するか、拠点・カテゴリを選んでください。")
    st.stop()


def _match(it: dict) -> bool:
    if site_sel != "すべて" and it.get("site") != site_sel:
        return False
    if cat_sel != "すべて" and it.get("cat") != cat_sel:
        return False
    if kw:
        hay = " ".join(
            it.get(k, "") for k in ("name", "model", "spec", "maker", "cat", "site")
        ).lower()
        if kw not in hay:
            return False
    return True


hits = [it for it in items if _match(it)]

if not hits:
    st.warning("該当する工具・測定具・消耗品が見つかりませんでした。")
    st.stop()

st.markdown(f"**{len(hits)} 品目** ヒットしました。")

MAX = 500
shown = hits[:MAX]
rows = [
    {
        "拠点": it.get("site", ""),
        "カテゴリ": it.get("cat", ""),
        "品名": it.get("name", ""),
        "型式": it.get("model", ""),
        "仕様・用途": it.get("spec", ""),
        "メーカー": it.get("maker", ""),
        "個数": it.get("qty", ""),
        "単位": it.get("unit", ""),
    }
    for it in shown
]

try:
    import pandas as pd
    df = pd.DataFrame(rows, columns=[
        "拠点", "カテゴリ", "品名", "型式", "仕様・用途", "メーカー", "個数", "単位",
    ])
    st.dataframe(df, use_container_width=True, hide_index=True)
except Exception:
    st.table(rows)

if len(hits) > MAX:
    st.caption(f"※ 先頭 {MAX} 品目を表示しています。キーワードや拠点・カテゴリで絞ってください。")

st.divider()
st.page_link("app_pages/home.py", label="ホームに戻る", icon="🏠")
