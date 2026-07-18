"""利用者マニュアルページ — TSEG WORKS の使い方(PDF)を閲覧・ダウンロードする。

PDFの実体は static/TSEG_WORKS_利用者マニュアル.pdf。
マニュアル(.docx)を更新したら PDF を作り直して static/ に置き、push すれば
自動デプロイでアプリ側も最新になる。

TSEG WORKS の1メニュー。search_app.py の st.navigation から呼ばれる。
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone, timedelta
from pathlib import Path

import streamlit as st

PDF_PATH = Path(__file__).resolve().parent.parent / "static" / "TSEG_WORKS_利用者マニュアル.pdf"

st.page_link("app_pages/home.py", label="ホームに戻る", icon="🏠")

st.title("📖 利用者マニュアル")
st.caption("TSEG WORKS の使い方をまとめたマニュアルです。スマホ・タブレットでも読めます。")
st.divider()

if not PDF_PATH.exists():
    st.warning(
        "マニュアルファイルが見つかりません。\n\n"
        "`static/TSEG_WORKS_利用者マニュアル.pdf` を配置して再デプロイしてください。"
    )
    st.stop()

data = PDF_PATH.read_bytes()

# 更新日（JST）を表示して、最新版かどうか分かるようにする
try:
    jst = timezone(timedelta(hours=9))
    updated = datetime.fromtimestamp(PDF_PATH.stat().st_mtime, tz=jst).strftime("%Y-%m-%d")
    st.info(f"マニュアル更新日: {updated}　／　サイズ: {len(data)/1024:.0f} KB")
except Exception:
    pass

st.download_button(
    "📥 マニュアルをダウンロード（PDF）",
    data=data,
    file_name="TSEG_WORKS_利用者マニュアル.pdf",
    mime="application/pdf",
    use_container_width=True,
)

st.caption(
    "スマートフォンでは、ダウンロード後にPDFビューアで開くと読みやすいです。"
    "ブラウザの「ホーム画面に追加」をしておくと、TSEG WORKS をアプリのように起動できます。"
)

# ── 画面内プレビュー（PCブラウザ向け。スマホでは表示されないことがある） ──────────
with st.expander("この画面でプレビューする（PC向け）", expanded=False):
    b64 = base64.b64encode(data).decode()
    st.markdown(
        f'<iframe src="data:application/pdf;base64,{b64}" '
        'width="100%" height="800" style="border:1px solid #ddd;border-radius:8px"></iframe>',
        unsafe_allow_html=True,
    )
    st.caption("表示されない場合は、上の「ダウンロード」からPDFを開いてください。")

st.divider()
st.page_link("app_pages/home.py", label="ホームに戻る", icon="🏠")
