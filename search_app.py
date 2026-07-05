"""Zフォルダ 写真・動画検索アプリ（Streamlit）。

起動方法（Desktop PCのターミナルで実行）:
    streamlit run search_app.py

タブレットからのアクセス:
    http://{Desktop PCのIPアドレス}:8501
    ※ Desktop PC の IP は ipconfig で確認。VPN接続が前提。
"""
from __future__ import annotations

import base64
import html as _html
import os
import sys
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import List, Optional

import streamlit as st

# ── Azure Blob Storage 設定 ───────────────────────────────────────────────────
# App Service の環境変数に設定する。未設定時はローカルパスを直接使う（開発用）。
_BLOB_BASE_URL: str = os.getenv("AZURE_BLOB_BASE_URL", "").rstrip("/")
_BLOB_SAS_TOKEN: str = os.getenv("AZURE_BLOB_SAS_TOKEN", "")
_TARGET_91_ROOT_WIN: str = os.getenv(
    "TARGET_91_ROOT",
    r"Z:\takachiho\2to9_業務別フォルダ\91_工番別実績写真・動画",
)
_BLOB_271_BASE_URL: str = os.getenv("AZURE_BLOB_271_BASE_URL", "").rstrip("/")
# 271専用SASトークン。未設定時は共通トークンにフォールバック。
_BLOB_271_SAS_TOKEN: str = os.getenv("AZURE_BLOB_271_SAS_TOKEN", "") or _BLOB_SAS_TOKEN
_TARGET_271_ROOT_WIN: str = os.getenv(
    "TARGET_271_ROOT",
    r"Z:\takachiho\2to9_業務別フォルダ\27_サービス・出張工事\271_修理工事指令書",
)


def _to_blob_url(file_path: str) -> str | None:
    if not _BLOB_BASE_URL or not file_path:
        return None
    try:
        win_path = PureWindowsPath(file_path)
        root_path = PureWindowsPath(_TARGET_91_ROOT_WIN)
        rel = win_path.relative_to(root_path)
        blob_path = "/".join(rel.parts)
        url = f"{_BLOB_BASE_URL}/{blob_path}"
        if _BLOB_SAS_TOKEN:
            url += f"?{_BLOB_SAS_TOKEN}"
        return url
    except (ValueError, Exception):
        return None


def _to_blob_url_271(file_path: str) -> str | None:
    if not _BLOB_271_BASE_URL or not file_path:
        return None
    try:
        win_path = PureWindowsPath(file_path)
        root_path = PureWindowsPath(_TARGET_271_ROOT_WIN)
        rel = win_path.relative_to(root_path)
        blob_path = "/".join(rel.parts)
        url = f"{_BLOB_271_BASE_URL}/{blob_path}"
        if _BLOB_271_SAS_TOKEN:
            url += f"?{_BLOB_271_SAS_TOKEN}"
        return url
    except (ValueError, Exception):
        return None


# ページ設定（必ず最初に呼ぶ）
_LOGO_PATH = Path(__file__).parent / "tseg_favicon.png"
try:
    from PIL import Image as _PIL_Image
    _page_icon = _PIL_Image.open(_LOGO_PATH) if _LOGO_PATH.exists() else "🔍"
    if hasattr(_page_icon, "load"):
        _page_icon.load()
except Exception:
    _page_icon = "🔍"

st.set_page_config(
    page_title="TSEG FMP SEARCH APP",
    page_icon=_page_icon,
    layout="wide",
)

# ホーム画面アイコン（apple-touch-icon）注入
import streamlit.components.v1 as _components
_TOUCH_ICON_B64 = "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"
_components.html(
    f'''<script>
(function(){{
  var d=parent.document;
  var l=d.querySelector("link[rel='apple-touch-icon']");
  if(!l){{l=d.createElement("link");l.rel="apple-touch-icon";d.head.appendChild(l);}}
  l.href="data:image/png;base64,{_TOUCH_ICON_B64}";
}})();
</script>''',
    height=0,
)

# タイトル用ロゴをbase64で埋め込む
_LOGO_B64 = ""
if _LOGO_PATH.exists():
    try:
        _LOGO_B64 = base64.b64encode(_LOGO_PATH.read_bytes()).decode()
    except Exception:
        pass

# ── 接続チェック ───────────────────────────────────────────────────────────────
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


def do_search(
    client,
    query: str,
    phase: Optional[str],
    media_types: List[str],
    year: Optional[int],
    top: int = 50,
) -> List[dict]:
    """AI Search にクエリを投げて結果を返す。"""
    filters: List[str] = []
    if phase:
        filters.append(f"phase eq '{phase}'")
    if media_types:
        types_str = ",".join(media_types)
        filters.append(f"search.in(media_type, '{types_str}', ',')")
    if year:
        yy = str(year % 100).zfill(2)
        filters.append(f"capture_date_raw ge '{yy}0101' and capture_date_raw le '{yy}1231'")

    filter_expr = " and ".join(filters) if filters else None
    search_text = query.strip() if query.strip() else "*"

    try:
        results = client.search(
            search_text=search_text,
            filter=filter_expr,
            select=[
                "id", "file_path", "file_name", "workno", "workno_name",
                "phase", "media_type", "capture_date", "capture_date_raw",
                "folder_path", "content_text",
            ],
            order_by=["capture_date desc"],
            top=top,
        )
        return list(results)
    except Exception as e:
        st.error(f"検索エラー: {e}")
        return []


# ── UI ────────────────────────────────────────────────────────────────────────
def main() -> None:
    client = _get_search_client()

    # ヘッダー
    if _LOGO_B64:
        st.markdown(
            '<h1 style="display:flex;align-items:flex-end;gap:12px;line-height:1;padding-bottom:0">'
            '<img src="data:image/png;base64,' + _LOGO_B64 + '" width="64">'
            'TSEG FMP SEARCH APP'
            '</h1>',
            unsafe_allow_html=True,
        )
    else:
        st.title("🔍 TSEG FMP SEARCH APP")
    st.caption("写真・動画・過去の指令書を検索できます。")
    st.divider()

    # ── 検索フォーム ──────────────────────────────────────────────────────────
    col_q, col_btn = st.columns([5, 1])
    with col_q:
        query = st.text_input(
            label="検索キーワード",
            placeholder="例: 1234-01　/ 高知プラント　/ B2　/ 250611",
            label_visibility="collapsed",
        )
    with col_btn:
        search_clicked = st.button("検索", use_container_width=True, type="primary")

    # ── サイドバー フィルタ ────────────────────────────────────────────────────
    with st.sidebar:
        st.header("🔧 フィルタ")

        phase_options = ["（指定なし）", "B1", "B2", "B3", "B4"]
        phase_labels = {
            "B1": "B1 着手前",
            "B2": "B2 着手中",
            "B3": "B3 出荷以降",
            "B4": "B4 整理前",
        }
        phase_sel = st.selectbox(
            "フェーズ",
            phase_options,
            format_func=lambda x: phase_labels.get(x, x),
        )
        phase_val = phase_sel if phase_sel != "（指定なし）" else None

        media_options = ["photo", "video", "shirei"]
        media_labels = {"photo": "📷 写真", "video": "🎬 動画", "shirei": "📄 指令書(PDF)"}
        media_sel = st.multiselect(
            "種別（複数選択可）",
            media_options,
            format_func=lambda x: media_labels.get(x, x),
        )
        media_val = media_sel  # List[str]、空リスト = 絞り込みなし

        current_year = datetime.now().year
        year_options = ["（指定なし）"] + list(range(current_year, current_year - 6, -1))
        year_sel = st.selectbox("撮影年", year_options)
        year_val = int(year_sel) if year_sel != "（指定なし）" else None

        st.divider()
        top_n = st.slider("最大表示件数", 10, 200, 50, 10)

        st.divider()
        st.caption("ファイルパスをコピーして\nエクスプローラーで開けます。")

    # ── 検索実行 ──────────────────────────────────────────────────────────────
    if query or search_clicked:
        with st.spinner("検索中..."):
            results = do_search(
                client, query, phase_val, media_val or [], year_val, top=top_n
            )

        if not results:
            st.info("該当するファイルが見つかりませんでした。")
            return

        st.markdown(f"**{len(results)} 件** 見つかりました。")

        if st.session_state.get("preview_path"):
            _preview_dialog()

        st.divider()

        for doc in results:
            _render_result(doc)
    else:
        st.info("キーワードを入力するか、フィルタを選択して「検索」を押してください。")


@st.dialog("📷 フルサイズ表示", width="large")
def _preview_dialog() -> None:
    st.caption(st.session_state.get("preview_name", ""))
    st.image(st.session_state["preview_path"], use_container_width=True)


def _render_result(doc: dict) -> None:
    """検索結果1件を表示する。"""
    phase = doc.get("phase", "")
    media_type = doc.get("media_type", "")
    icon = "📷" if media_type == "photo" else "🎬" if media_type == "video" else "📄"
    phase_badge = {"B1": "🟦", "B2": "🟩", "B3": "🟨", "B4": "🟥"}.get(phase, "⬜")

    workno = doc.get("workno", "")
    workno_name = doc.get("workno_name", "")
    file_name = doc.get("file_name", "")
    file_path = doc.get("file_path", "")
    capture_raw = doc.get("capture_date_raw", "")

    display_date = ""
    if len(capture_raw) == 6:
        yy, mm, dd = capture_raw[:2], capture_raw[2:4], capture_raw[4:6]
        year = int(f"20{yy}") if int(yy) < 70 else int(f"19{yy}")
        display_date = f"{year}-{mm}-{dd}"

    content_text = doc.get("content_text", "")
    image_src = _to_blob_url(file_path) or file_path

    with st.container():
        col_thumb, col_info, col_path = st.columns([2, 3, 4])

        with col_thumb:
            if media_type == "photo" and file_path:
                try:
                    st.image(image_src, width=160)
                    if st.button("🔍 拡大", key=f"zoom_{doc.get('id','')}"):
                        st.session_state["preview_path"] = image_src
                        st.session_state["preview_name"] = file_name
                        st.rerun()
                except Exception:
                    st.markdown(f"## {icon}")
            elif media_type == "video" and file_path:
                video_url = _to_blob_url(file_path)
                if video_url:
                    st.video(video_url)
                else:
                    st.markdown(f"## {icon}")
            elif media_type == "shirei" and file_path:
                pdf_url = _to_blob_url_271(file_path)
                if pdf_url:
                    safe_url = _html.escape(pdf_url)
                    st.markdown(
                        f'<a href="{safe_url}" target="_blank">📄 PDFを開く</a>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(f"## {icon}")
            else:
                st.markdown(f"## {icon}")

        with col_info:
            st.markdown(f"**{file_name}**")
            st.markdown(
                f"{phase_badge} {phase} ｜ 工番: `{workno}` ｜ {workno_name}"
            )
            if display_date:
                st.caption(f"撮影日: {display_date}")
            if content_text:
                st.caption(f"[memo] {content_text}")

        with col_path:
            st.code(file_path, language=None)

        st.divider()


if __name__ == "__main__":
    main()
