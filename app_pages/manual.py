"""マニュアルページ — 利用者マニュアル / 運用マニュアル(PDF)を閲覧・ダウンロードする。

PDFの実体:
  static/TSEG_WORKS_利用者マニュアル.pdf          … アプリの使い方（現場・事務の利用者向け）
  static/共有フォルダ整理プログラム_運用マニュアル.pdf … 裏で動く自動処理の仕様（管理者向け）

.docx を更新したら PDF を作り直して static/ に置き、push すれば自動デプロイで最新になる。

TSEG WORKS の1メニュー。search_app.py の st.navigation から呼ばれる。
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import streamlit as st

_STATIC = Path(__file__).resolve().parent.parent / "static"
USER_PDF = _STATIC / "TSEG_WORKS_利用者マニュアル.pdf"
OPS_PDF = _STATIC / "共有フォルダ整理プログラム_運用マニュアル.pdf"

st.page_link("app_pages/home.py", label="ホームに戻る", icon="🏠")

st.title("📖 マニュアル")
st.caption("アプリの使い方と、裏で動いている自動処理の仕組みをまとめています。")
st.divider()


def _render(pdf_path: Path, description: str, dl_name: str) -> None:
    """PDF1件分のダウンロード・プレビューを描画する。"""
    if not pdf_path.exists():
        st.warning(
            f"ファイルが見つかりません: `static/{pdf_path.name}`\n\n"
            "PDFを配置して再デプロイしてください。"
        )
        return

    data = pdf_path.read_bytes()
    st.write(description)

    try:
        jst = timezone(timedelta(hours=9))
        updated = datetime.fromtimestamp(pdf_path.stat().st_mtime, tz=jst).strftime("%Y-%m-%d")
        st.info(f"更新日: {updated}　／　サイズ: {len(data)/1024:.0f} KB")
    except Exception:
        pass

    # 新しいタブで開く（タブを閉じればアプリに戻れる＝アプリごと閉じなくてよい）
    url = "/app/static/" + quote(pdf_path.name)
    st.markdown(
        f'<a href="{url}" target="_blank" rel="noopener" '
        'style="display:block;text-align:center;padding:0.65rem 1rem;margin-bottom:10px;'
        'border:2px solid #21A159;border-radius:8px;color:#21A159;'
        'text-decoration:none;font-weight:700">'
        '🔎 新しいタブで開く（閉じればアプリに戻れます）</a>',
        unsafe_allow_html=True,
    )

    st.download_button(
        "📥 端末に保存（ダウンロード）",
        data=data,
        file_name=dl_name,
        mime="application/pdf",
        use_container_width=True,
        key=f"dl_{pdf_path.stem}",
    )

    with st.expander("この画面でプレビューする（PC向け）", expanded=False):
        b64 = base64.b64encode(data).decode()
        st.markdown(
            f'<iframe src="data:application/pdf;base64,{b64}" '
            'width="100%" height="800" style="border:1px solid #ddd;border-radius:8px"></iframe>',
            unsafe_allow_html=True,
        )
        st.caption("表示されない場合は、上の「ダウンロード」からPDFを開いてください。")


tab_user, tab_ops = st.tabs(["📖 利用者マニュアル", "⚙️ 運用マニュアル（仕組み）"])

with tab_user:
    _render(
        USER_PDF,
        "TSEG WORKS の使い方です。各検索メニューの操作方法、絞り込み、写真・動画の見かたなどを説明しています。",
        "TSEG_WORKS_利用者マニュアル.pdf",
    )

with tab_ops:
    _render(
        OPS_PDF,
        "毎晩自動で動いている「共有フォルダ整理プログラム」の仕組みです。"
        "写真・動画がどう取り込まれ、工番ごとにどう整理・命名・圧縮され、検索できるようになるかを説明しています。"
        "通知・監視の自動化（未利用通知／利用レポート／タスク点検／ログ掃除）も記載しています。",
        "共有フォルダ整理プログラム_運用マニュアル.pdf",
    )

st.divider()
st.caption(
    "スマートフォンでは、ダウンロード後にPDFビューアで開くと読みやすいです。"
    "ブラウザの「ホーム画面に追加」をしておくと、TSEG WORKS をアプリのように起動できます。"
)
st.page_link("app_pages/home.py", label="ホームに戻る", icon="🏠")
