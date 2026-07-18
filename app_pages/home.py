"""ホーム（玄関）ページ — 総合検索APPの入口。

各検索メニューへのリンクを並べる。今後メニューが増えたらここに追記する。
search_app.py の st.navigation から呼ばれる。
"""
from __future__ import annotations

import base64
from pathlib import Path

import streamlit as st

# タイトル用ロゴ（リポジトリ直下の tseg_favicon.png）
_LOGO_PATH = Path(__file__).resolve().parent.parent / "tseg_favicon.png"
_LOGO_B64 = ""
if _LOGO_PATH.exists():
    try:
        _LOGO_B64 = base64.b64encode(_LOGO_PATH.read_bytes()).decode()
    except Exception:
        pass

if _LOGO_B64:
    st.markdown(
        '<h1 style="display:flex;align-items:flex-end;gap:12px;line-height:1;padding-bottom:0">'
        '<img src="data:image/png;base64,' + _LOGO_B64 + '" width="64">'
        'TSEG 総合検索'
        '</h1>',
        unsafe_allow_html=True,
    )
else:
    st.title("🔍 TSEG 総合検索")

st.caption("使いたい検索メニューを選んでください。")
st.divider()

st.subheader("検索メニュー")

st.page_link(
    "app_pages/fmp_search.py",
    label="FMP SEARCH ― 写真・動画・過去の指令書PDFを検索",
    icon="🔍",
)
st.page_link(
    "app_pages/koban_search.py",
    label="工番検索 ― 工事名・納入先・工番の一部から工番を探す",
    icon="🔎",
)

st.divider()
st.caption("※ メニューは順次追加予定（納入先検索・部品在庫検索 など）。左のサイドバーからも移動できます。")
