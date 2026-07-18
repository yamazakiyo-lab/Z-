"""マニュアルページ — アプリ内でそのまま読めるマニュアル。

PDFビューアに頼らず、本文を Markdown でアプリ内に描画する（スマホでも確実に読め、
読み終わったら「ホームに戻る」で戻れる）。PDFは印刷・保存したい人向けに残す。

  static/user_manual.md / user_manual.pdf … アプリの使い方（利用者向け）
  static/ops_manual.md  / ops_manual.pdf  … 裏で動く自動処理の仕組み（管理者向け）

.docx を更新したら `python build_manual_md.py` で .md を作り直し、PDFも差し替えて push する。

TSEG WORKS の1メニュー。search_app.py の st.navigation から呼ばれる。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

_STATIC = Path(__file__).resolve().parent.parent / "static"

st.page_link("app_pages/home.py", label="ホームに戻る", icon="🏠")

st.title("📖 マニュアル")
st.caption("章をタップすると開きます。アプリの中でそのまま読めます。")
st.divider()


def _updated(path: Path) -> str:
    try:
        jst = timezone(timedelta(hours=9))
        return datetime.fromtimestamp(path.stat().st_mtime, tz=jst).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _render(md_path: Path, pdf_path: Path, dl_name: str, description: str) -> None:
    """マニュアル1件分：説明・章ごとの折りたたみ本文・PDFダウンロードを描画。"""
    st.write(description)

    if not md_path.exists():
        st.warning(
            f"本文ファイルが見つかりません: `static/{md_path.name}`\n\n"
            "`python build_manual_md.py` を実行して生成し、push してください。"
        )
        return

    upd = _updated(md_path)
    if upd:
        st.caption(f"更新日: {upd}")

    md = md_path.read_text(encoding="utf-8")

    # 「## 」＝章。章ごとに折りたたむ（畳んだ状態が目次代わりになる）
    parts = re.split(r"^## ", md, flags=re.M)
    intro = parts[0].strip()
    if intro:
        st.markdown(intro)

    for chunk in parts[1:]:
        head, _, body = chunk.partition("\n")
        with st.expander(head.strip(), expanded=False):
            st.markdown(body.strip() or "（内容なし）")

    # PDFは印刷・保存したい人向けの補助
    if pdf_path.exists():
        st.divider()
        st.download_button(
            "📥 PDFで保存・印刷する",
            data=pdf_path.read_bytes(),
            file_name=dl_name,
            mime="application/pdf",
            use_container_width=True,
            key=f"dl_{pdf_path.stem}",
        )


tab_user, tab_ops = st.tabs(["📖 利用者マニュアル", "⚙️ 運用マニュアル（仕組み）"])

with tab_user:
    _render(
        _STATIC / "user_manual.md",
        _STATIC / "user_manual.pdf",
        "TSEG_WORKS_利用者マニュアル.pdf",
        "TSEG WORKS の使い方です。各検索メニューの操作方法、絞り込み、写真・動画の見かたを説明しています。",
    )

with tab_ops:
    _render(
        _STATIC / "ops_manual.md",
        _STATIC / "ops_manual.pdf",
        "共有フォルダ整理プログラム_運用マニュアル.pdf",
        "毎晩自動で動いている「共有フォルダ整理プログラム」の仕組みです。"
        "写真・動画がどう取り込まれ、工番ごとに整理・命名・圧縮されて検索できるようになるか、"
        "通知・監視の自動化（未利用通知／利用レポート／タスク点検／ログ掃除）まで記載しています。",
    )

st.divider()
st.page_link("app_pages/home.py", label="ホームに戻る", icon="🏠")
