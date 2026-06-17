"""Zフォルダ 写真・動画検索アプリ（Streamlit）。

起動方法（Desktop PCのターミナルで実行）:
    streamlit run search_app.py

タブレットからのアクセス:
    http://{Desktop PCのIPアドレス}:8501
    ※ Desktop PC の IP は ipconfig で確認。VPN接続が前提。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import PureWindowsPath
from typing import List, Optional

import streamlit as st

# ── Azure Blob Storage 設定 ───────────────────────────────────────────────────
# App Service の環境変数に設定する。未設定時はローカルパスを直接使う（開発用）。
_BLOB_BASE_URL: str = os.getenv("AZURE_BLOB_BASE_URL", "").rstrip("/")
# 例: https://tsegphotos.blob.core.windows.net/photos
_BLOB_SAS_TOKEN: str = os.getenv("AZURE_BLOB_SAS_TOKEN", "")
# 例: sv=2026-02-06&ss=b&srt=co&sp=r...（先頭の ? は不要）
_TARGET_91_ROOT_WIN: str = os.getenv(
    "TARGET_91_ROOT",
    r"Z:\takachiho\2to9_業務別フォルダ\91_工番別実績写真・動画",
)


def _to_blob_url(file_path: str) -> str | None:
    """Z:ドライブのWindowsパスをAzure Blob Storage URLに変換する。

    AZURE_BLOB_BASE_URL が未設定の場合は None を返す（ローカルパスにフォールバック）。
    """
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


# ページ設定（必ず最初に呼ぶ）
st.set_page_config(
    page_title="写真・動画 検索",
    page_icon="🔍",
    layout="wide",
)

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
    media_type: Optional[str],
    year: Optional[int],
    top: int = 50,
) -> List[dict]:
    """AI Search にクエリを投げて結果を返す。"""
    filters: List[str] = []
    if phase:
        filters.append(f"phase eq '{phase}'")
    if media_type:
        filters.append(f"media_type eq '{media_type}'")
    if year:
        # capture_date_raw は YYMMDD なので下2桁で年を絞る
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
    st.title("🔍 Zフォルダ 写真・動画 検索")
    st.caption("工番・工事名・フォルダ名などで検索できます。")
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

        media_options = ["（指定なし）", "photo", "video"]
        media_labels = {"photo": "📷 写真", "video": "🎬 動画"}
        media_sel = st.selectbox(
            "種別",
            media_options,
            format_func=lambda x: media_labels.get(x, x),
        )
        media_val = media_sel if media_sel != "（指定なし）" else None

        current_year = datetime.now().year
        year_options = ["（指定なし）"] + list(range(current_year, current_year - 6, -1))
        year_sel = st.selectbox("撮影年", year_options)
        year_val = int(year_sel) if year_sel != "（指定なし）" else None

        st.divider()
        top_n = st.slider("最大表示件数", 10, 200, 50, 10)

        st.divider()
        st.caption("ファイルパスをコピーして\nエクスプローラーで開けます。")

    # ── 検索実行 ──────────────────────────────────────────────────────────────
    # 初回表示 or 検索ボタン or Enterキー
    if query or search_clicked:
        with st.spinner("検索中..."):
            results = do_search(
                client, query, phase_val, media_val, year_val, top=top_n
            )

        if not results:
            st.info("該当するファイルが見つかりませんでした。")
            return

        st.markdown(f"**{len(results)} 件** 見つかりました。")

        # フルサイズプレビュー
        if "preview_path" in st.session_state and st.session_state["preview_path"]:
            with st.expander(f"📷 {st.session_state.get('preview_name', '')} — フルサイズ", expanded=True):
                st.image(st.session_state["preview_path"], use_container_width=True)
                if st.button("✕ 閉じる"):
                    st.session_state["preview_path"] = None
                    st.rerun()

        st.divider()

        for doc in results:
            _render_result(doc)
    else:
        st.info("キーワードを入力するか、フィルタを選択して「検索」を押してください。")


def _render_result(doc: dict) -> None:
    """検索結果1件を表示する。"""
    phase = doc.get("phase", "")
    media_type = doc.get("media_type", "")
    icon = "📷" if media_type == "photo" else "🎬"
    phase_badge = {"B1": "🟦", "B2": "🟩", "B3": "🟨", "B4": "🟥"}.get(phase, "⬜")

    workno = doc.get("workno", "")
    workno_name = doc.get("workno_name", "")
    file_name = doc.get("file_name", "")
    file_path = doc.get("file_path", "")
    capture_raw = doc.get("capture_date_raw", "")

    # 日付を YYMMDD → YYYY-MM-DD に変換して表示
    display_date = ""
    if len(capture_raw) == 6:
        yy, mm, dd = capture_raw[:2], capture_raw[2:4], capture_raw[4:6]
        year = int(f"20{yy}") if int(yy) < 70 else int(f"19{yy}")
        display_date = f"{year}-{mm}-{dd}"

    content_text = doc.get("content_text", "")

    # Blob URL（Azureデプロイ時）またはローカルパス（開発時）
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
                except Exception:
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
                st.caption(f"📝 {content_text}")

        with col_path:
            st.code(file_path, language=None)

        st.divider()


if __name__ == "__main__":
    main()
