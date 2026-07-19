"""部品在庫検索ページ — 貯蔵品(寄居・綾瀬)の在庫を型式・品名等から探す。

データ源は Blob の parts_inventory.json（export_parts_inventory.py がデスクトップで生成）。
本番Azureは Z: を読めないため、Blob経由でデータを受け取る。
AZURE_BLOB_CONNECTION_STRING が必要（工番マスタと同じ倉庫 lw-raw）。

総合検索APPの1メニュー。search_app.py の st.navigation から呼ばれる。
"""
from __future__ import annotations

from typing import List, Tuple

import streamlit as st


# ── Blob から在庫データを読む（重いのでキャッシュ） ───────────────────────────
@st.cache_data(show_spinner="部品在庫を読み込み中...", ttl=600)
def _load_parts() -> Tuple[List[dict], str]:
    """parts_inventory.json (Blob) から (items, generated_at) を返す。

    AZURE_BLOB_CONNECTION_STRING 未設定・読み込み失敗・未アップロード時は ([], "")。
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
        data = cont.download_blob("parts_inventory.json").readall()
        payload = json.loads(data)
        return payload.get("items", []), payload.get("generated_at", "")
    except Exception:
        return [], ""


# ── UI ────────────────────────────────────────────────────────────────────────
st.page_link("app_pages/home.py", label="ホームに戻る", icon="🏠")

st.title("📦 部品在庫検索")
st.caption("型式・品名・メーカー・棚番から、貯蔵品（寄居・綾瀬）の在庫を探せます。")
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

items, generated_at = _load_parts()

if not items:
    st.warning(
        "在庫データがまだ準備されていません。\n\n"
        "デスクトップ（Z:が見える環境）で `python export_parts_inventory.py` を実行して、"
        "在庫表を Blob にアップロードしてください。"
        "（検索アプリ側に環境変数 `AZURE_BLOB_CONNECTION_STRING` が必要）"
    )
    st.stop()

st.divider()

cats = sorted({it.get("cat", "") for it in items if it.get("cat")})

col1, col2 = st.columns([3, 2])
with col1:
    kw_raw = st.text_input(
        "キーワード（型式・品名・メーカー・棚番）",
        placeholder="例: ベアリング / SMC / 6203 / TN1",
    )
with col2:
    cat_sel = st.selectbox("カテゴリで絞り込み", ["すべて"] + cats)

if generated_at:
    st.caption(f"在庫データ更新日: {generated_at[:10]}　｜　登録 {len(items)} 品目")

kw = kw_raw.strip().lower()

if not kw and cat_sel == "すべて":
    st.info("キーワードを入力するか、カテゴリを選んでください。")
    st.stop()


def _match(it: dict) -> bool:
    if cat_sel != "すべて" and it.get("cat") != cat_sel:
        return False
    if kw:
        hay = " ".join(
            it.get(k, "") for k in ("model", "spec", "maker", "tana", "cat")
        ).lower()
        if kw not in hay:
            return False
    return True


hits = [it for it in items if _match(it)]

if not hits:
    st.warning("該当する部品が見つかりませんでした。")
    st.stop()

st.markdown(f"**{len(hits)} 品目** ヒットしました。")

MAX = 500
shown = hits[:MAX]
rows = [
    {
        "カテゴリ": it.get("cat", ""),
        "棚番": it.get("tana", ""),
        "型式/品名": it.get("model", ""),
        "用途・仕様": it.get("spec", ""),
        "メーカー": it.get("maker", ""),
        "数量": it.get("qty", ""),
        "見積単価": it.get("quote", ""),
    }
    for it in shown
]

try:
    import pandas as pd
    df = pd.DataFrame(rows, columns=[
        "カテゴリ", "棚番", "型式/品名", "用途・仕様", "メーカー", "数量", "見積単価",
    ])
    st.dataframe(df, use_container_width=True, hide_index=True)
except Exception:
    # pandas が無い場合のフォールバック
    st.table(rows)

if len(hits) > MAX:
    st.caption(f"※ 先頭 {MAX} 品目を表示しています。キーワードやカテゴリで絞ってください。")
