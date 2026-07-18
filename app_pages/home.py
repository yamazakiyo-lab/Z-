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
        '<div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin:0 0 6px 0">'
        '<img src="data:image/png;base64,' + _LOGO_B64 + '" width="58" style="flex:0 0 auto">'
        '<span style="font-size:2rem;font-weight:700;line-height:1.25">TSEG WORKS</span>'
        '</div>',
        unsafe_allow_html=True,
    )
else:
    st.title("🔍 TSEG WORKS")

st.caption("下のメニューをタップして選んでください。")
st.divider()

# ── メニューをカード風ボタンで表示 ────────────────────────────────────────────
# ラベル内の改行を活かすため white-space: pre-line を指定（1行目=名称／2行目=説明）
st.markdown(
    """
<style>
div.stButton > button {
    white-space: pre-line;      /* ラベルの改行をそのまま表示 */
    text-align: left;
    font-size: 1.18rem;
    font-weight: 700;
    line-height: 1.65;
    padding: 16px 20px;
    margin-bottom: 10px;
    border: 2px solid #21A159;
    border-radius: 12px;
}
div.stButton > button:hover {
    background: rgba(33,161,89,0.14);
    border-color: #44CC77;
}
</style>
""",
    unsafe_allow_html=True,
)

st.subheader("検索メニュー")

_MENUS = [
    ("🔍 FMP SEARCH", "写真・動画・過去の指令書PDFを検索", "app_pages/fmp_search.py"),
    ("🔎 工番検索", "工事名・納入先・工番の一部から工番を探す", "app_pages/koban_search.py"),
    ("🏢 納入先検索", "納入先(会社名)からその取引先の工番・写真を探す", "app_pages/nyunyusaki_search.py"),
    ("📦 部品在庫検索", "貯蔵品(寄居・綾瀬)を型式・品名・メーカー等から探す", "app_pages/zaiko_search.py"),
]
for _i, (_title, _desc, _page) in enumerate(_MENUS):
    if st.button(f"{_title}\n{_desc}", key=f"menu_{_i}", use_container_width=True):
        st.switch_page(_page)

st.divider()
st.subheader("使い方")

if st.button(
    "📖 利用者マニュアル\nTSEG WORKS の使い方・仕組みの資料(PDF)を見る",
    key="menu_manual",
    use_container_width=True,
):
    st.switch_page("app_pages/manual.py")

st.divider()
st.caption("※ 左上の「≫」からサイドバーを開くと、どの画面からでもメニューに移動できます。")
