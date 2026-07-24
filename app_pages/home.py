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
        '<div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin:0 0 6px 0">'
        '<img src="data:image/png;base64,' + _LOGO_B64 + '" width="76" style="flex:0 0 auto">'
        '<span style="font-size:2.4rem;font-weight:800;line-height:1.2;letter-spacing:0.01em">'
        'TSEG WORKS</span>'
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
    /* Streamlitのボタンは内部がflex中央寄せのため、左寄せを明示する */
    display: flex;
    justify-content: flex-start;
    align-items: flex-start;
    text-align: left;
    font-size: 1.18rem;
    font-weight: 700;
    line-height: 1.65;
    padding: 16px 20px;
    margin-bottom: 10px;
    border: 2px solid #21A159;
    border-radius: 12px;
}
/* ラベル内側(p/div/span)まで左寄せを徹底する。説明文は少し小さめ・細字。 */
div.stButton > button p,
div.stButton > button div,
div.stButton > button span {
    text-align: left !important;
    width: 100%;
    margin: 0;
    font-size: 1.0rem;
    font-weight: 400;
}
/* 1行目のタイトル(**太字**)を大きく目立たせる */
div.stButton > button strong {
    font-size: 1.3rem;
    font-weight: 800;
    letter-spacing: 0.01em;
}
/* ローマ字は同じ文字サイズでも日本語より小さく見える(x-heightが低い)ため、
   ローマ字表記の「FMP SEARCH」だけ少し大きくして見た目を揃える。 */
div.st-key-menu_0 button strong {
    font-size: 1.5rem;
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
    ("🏢 顧客検索", "顧客(会社名)からその取引先の工番・写真を探す", "app_pages/nyunyusaki_search.py"),
    ("📦 部品在庫検索", "貯蔵品(寄居・綾瀬)を型式・品名・メーカー等から探す", "app_pages/zaiko_search.py"),
    ("🛠️ 動治工具・測定具・消耗品検索", "寄居・綾瀬の工具/測定具/消耗品を品名・型式から探す", "app_pages/tools_search.py"),
    ("💬 AI Q&A", "技術・業務の質問にAIが回答(社内の工番実績も参照)", "app_pages/ai_qa.py"),
]
for _i, (_title, _desc, _page) in enumerate(_MENUS):
    if st.button(f"**{_title}**\n{_desc}", key=f"menu_{_i}", use_container_width=True):
        st.switch_page(_page)

st.divider()
st.subheader("使い方")

if st.button(
    "**📖 マニュアル**\nTSEG WORKS の使い方と、裏の仕組みの資料を読む",
    key="menu_manual",
    use_container_width=True,
):
    st.switch_page("app_pages/manual.py")

st.divider()
st.caption("※ 左上の「≫」からサイドバーを開くと、どの画面からでもメニューに移動できます。")

# ログイン中アカウントの表示(管理者メニュー判定の確認にも使う)
try:
    _hdrs = st.context.headers or {}
    _upn = (_hdrs.get("X-MS-CLIENT-PRINCIPAL-NAME")
            or _hdrs.get("X-Ms-Client-Principal-Name") or "")
except Exception:
    _upn = ""
if _upn:
    st.caption(f"ログイン中: {_upn}")
