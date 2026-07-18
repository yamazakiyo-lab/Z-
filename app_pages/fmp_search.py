"""FMP SEARCH — 写真・動画・過去の指令書PDF検索ページ。

総合検索APPの1メニュー。search_app.py の st.navigation から呼ばれる。
※ set_page_config / favicon 等はナビ入口(search_app.py)で一度だけ設定するため、
   このページでは呼ばない。
"""
from __future__ import annotations

import base64
import html as _html
import os
import re as _re
from pathlib import Path, PureWindowsPath
from typing import List, Optional

import streamlit as st

# 工番パターン: 数字1〜6桁 + ハイフン or アンダースコア + 2桁 (例: 3477-00, A1234-01)
_WORKNO_QUERY_RE = _re.compile(r'^[A-Za-z]*\d{1,6}[-_]\d{2}$')


def _normalize_workno_query(query: str) -> Optional[str]:
    """クエリが工番パターンなら正規化した工番文字列を返す。そうでなければ None。"""
    q = query.strip()
    m = _re.match(r'^([A-Za-z]*)(\d+)[-_](\d{2})$', q)
    if not m:
        return None
    prefix = m.group(1).upper()
    digits = m.group(2)
    right = m.group(3)
    if prefix:
        return f"{prefix}{digits}-{right}"
    left = digits.lstrip("0") or "0"
    return f"{left}-{right}"


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


# タイトル用ロゴをbase64で埋め込む（リポジトリ直下の tseg_favicon.png）
_LOGO_PATH = Path(__file__).resolve().parent.parent / "tseg_favicon.png"
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
    phases: List[str],
    media_types: List[str],
    top: int = 50,
    client_name_q: str = "",
    billing_name_q: str = "",
) -> List[dict]:
    """AI Search にクエリを投げて結果を返す。"""
    filters: List[str] = []
    if phases:
        phases_str = ",".join(phases)
        filters.append(f"search.in(phase, '{phases_str}', ',')")
    if media_types:
        types_str = ",".join(media_types)
        filters.append(f"search.in(media_type, '{types_str}', ',')")

    # 工番パターン（例: 3477-00）は workno フィールドで完全一致フィルタを追加
    # → 標準アナライザーが "-" を分割するため "4555-00" も "00" でマッチする問題を防ぐ
    _norm_wno = _normalize_workno_query(query) if query.strip() else None
    if _norm_wno:
        filters.append(f"workno eq '{_norm_wno}'")

    # 納入先・請求先フィルタ（フレーズ一致: full Lucene でダブルクォート指定）
    if client_name_q.strip():
        safe_cn = client_name_q.strip().replace("'", "''").replace('"', '')
        filters.append(f"""search.ismatch('"{safe_cn}"', 'client_name', 'full', 'any')""")
    if billing_name_q.strip():
        safe_bn = billing_name_q.strip().replace("'", "''").replace('"', '')
        filters.append(f"""search.ismatch('"{safe_bn}"', 'billing_name', 'full', 'any')""")

    filter_expr = " and ".join(filters) if filters else None
    # 工番パターン（例: 3970-00）の場合はフィルタのみで検索。
    # テキスト検索を併用するとアナライザーが "00" 等を分割して
    # 他の文書にも誤マッチするため search_text は * に固定する。
    if _norm_wno:
        search_text = "*"
    else:
        search_text = query.strip() if query.strip() else "*"

    try:
        results = client.search(
            search_text=search_text,
            filter=filter_expr,
            search_mode="all",   # スペース区切りはAND検索（全語一致）
            select=[
                "id", "file_path", "file_name", "workno", "workno_name",
                "client_name", "billing_name",
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


# ── 完成/未成マスタ（Blobの workno_master.json）を読む ─────────────────────────
@st.cache_data(show_spinner=False, ttl=600)
def _load_kanryo_map() -> dict:
    """workno_master.json (Blob) から {workno: "完成"/"未成"} を読む。

    AZURE_BLOB_CONNECTION_STRING 未設定・読み込み失敗・kanryo未収録なら空dict
    （＝完成/未成フィルタは非表示になり従来どおりの挙動）。
    """
    import json
    import os

    conn = os.getenv("AZURE_BLOB_CONNECTION_STRING", "")
    container_name = os.getenv("LW_BLOB_CONTAINER", "lw-raw")
    if not conn:
        return {}
    try:
        from azure.storage.blob import BlobServiceClient

        svc = BlobServiceClient.from_connection_string(conn)
        cont = svc.get_container_client(container_name)
        data = cont.download_blob("workno_master.json").readall()
        payload = json.loads(data)
        worknos = payload.get("worknos", {})
        return {
            w: (info.get("kanryo") or "")
            for w, info in worknos.items()
            if (info.get("kanryo") or "")
        }
    except Exception:
        return {}


# ── UI ────────────────────────────────────────────────────────────────────────
def main() -> None:
    client = _get_search_client()

    # 工番検索ページからの遷移: 指定工番をキーワード欄に入れて自動検索する。
    # （工番はAI Search由来の正規化済み文字列なので、既存の workno eq フィルタで完全一致する）
    _jumped_wno = st.session_state.pop("jump_workno", None)
    if _jumped_wno:
        st.session_state["main_query"] = _jumped_wno

    # ヘッダー
    if _LOGO_B64:
        st.markdown(
            '<div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin:0 0 6px 0">'
            '<img src="data:image/png;base64,' + _LOGO_B64 + '" width="58" style="flex:0 0 auto">'
            '<span style="font-size:2rem;font-weight:700;line-height:1.25">FMP SEARCH</span>'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
        st.title("🔍 FMP SEARCH")
    st.caption("写真・動画・過去の指令書PDFを検索できます。")
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
/* フィルタ行（子が2つのstHorizontalBlock）だけ段組みを維持 */
.stHorizontalBlock:has(> .stColumn:last-child:nth-child(2)) {
    flex-direction: row !important;
    flex-wrap: wrap !important;
    align-items: flex-start !important;
}
.stHorizontalBlock:has(> .stColumn:last-child:nth-child(2)) > .stColumn {
    flex: 1 1 0% !important;
    min-width: 0 !important;
    width: auto !important;
}
</style>
""", unsafe_allow_html=True)
    st.divider()

    # ── 検索フォーム ──────────────────────────────────────────────────────────
    query = st.text_input(
        label="キーワード（工番・作業・コメント等）",
        placeholder="スペース区切りでAND検索",
        key="main_query",
    )
    if _jumped_wno:
        st.success(f"工番 `{_jumped_wno}` の写真を表示しています。（工番検索から遷移）")
    client_name_q = st.text_input(
        label="納入先名（部分一致可）",
        placeholder="例: 高千穂工業",
    )
    billing_name_q = st.text_input(
        label="請求先名（部分一致可）",
        placeholder="例: 株式会社",
    )

    # ── フィルタ行（メイン画面） ─────────────────────────────────────────────
    col_media, col_phase = st.columns([3, 4])
    with col_media:
        st.caption("種別")
        show_photo  = st.checkbox("📷 写真")
        show_video  = st.checkbox("🎬 動画")
        show_shirei = st.checkbox("📄 指令書PDF")
    with col_phase:
        st.caption("フェーズ")
        show_b1 = st.checkbox("🟦 B1 着手前")
        show_b2 = st.checkbox("🟩 B2 着手中")
        show_b3 = st.checkbox("🟨 B3 出荷以降")
        show_b4 = st.checkbox("🟥 B4 整理前")

    # 状態(完成/未成)フィルタ（マスタに完成/未成データがある時だけ表示）
    kanryo_map = _load_kanryo_map()
    kanryo_choice = "すべて"
    if kanryo_map:
        st.caption("状態")
        kanryo_choice = st.radio(
            "状態で絞り込み", ["すべて", "完成", "未成"],
            horizontal=True, index=0, key="fmp_kanryo", label_visibility="collapsed",
        )

    top_n = st.number_input("表示件数", min_value=10, max_value=200, value=50, step=10)

    # 種別フィルタ組み立て（未選択 = すべて表示）
    media_val: List[str] = []
    if show_photo:  media_val.append("photo")
    if show_video:  media_val.append("video")
    if show_shirei: media_val.append("shirei")

    # フェーズフィルタ組み立て（未選択 = すべて表示）
    phases_val: List[str] = []
    if show_b1: phases_val.append("B1")
    if show_b2: phases_val.append("B2")
    if show_b3: phases_val.append("B3")
    if show_b4: phases_val.append("B4")

    # アクティブフィルタのサマリー表示
    active: List[str] = []
    if show_photo:   active.append("📷 写真")
    if show_video:   active.append("🎬 動画")
    if show_shirei:  active.append("📄 指令書PDF")
    if phases_val:   active.append("フェーズ: " + "/".join(phases_val))
    if client_name_q.strip():
        active.append(f"納入先: {client_name_q.strip()}")
    if billing_name_q.strip():
        active.append(f"請求先: {billing_name_q.strip()}")
    if active:
        st.info("🔍 絞り込み中: " + " ｜ ".join(active))

    # ── 検索ボタン ────────────────────────────────────────────────────────────
    search_clicked = st.button("🔍 検索", use_container_width=True, type="primary")

    # ── 検索実行 ──────────────────────────────────────────────────────────────
    if query or search_clicked or client_name_q.strip() or billing_name_q.strip():
        with st.spinner("検索中..."):
            results = do_search(
                client, query, phases_val, media_val or [], top=int(top_n),
                client_name_q=client_name_q,
                billing_name_q=billing_name_q,
            )

        # 状態(完成/未成)で絞り込み
        if kanryo_map and kanryo_choice != "すべて":
            results = [r for r in results if kanryo_map.get(r.get("workno", "")) == kanryo_choice]

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
        st.info("キーワードまたはフィルタを選択して検索してください。\n複数キーワードの場合はスペースで区切ってください。")


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
                # 写真と同様にローカルフォールバックを付与(ローカル起動でもZ:から再生可能)
                video_url = _to_blob_url(file_path) or file_path
                # ブラウザで再生できる形式はインライン表示を試みる
                _vext = file_name.lower().rsplit(".", 1)[-1] if "." in file_name else ""
                _web_playable = _vext in {"mp4", "webm", "mov", "m4v"}
                if _web_playable:
                    try:
                        st.video(video_url)
                    except Exception:
                        pass
                # 形式を問わず「開く/ダウンロード」リンクを出す。
                # (.avi/.mts/.m2ts やコーデック非対応の .mov でも、別タブ表示やDLで視聴可能)
                if str(video_url).startswith("http"):
                    safe_v = _html.escape(video_url)
                    st.markdown(
                        f'<a href="{safe_v}" target="_blank">🎬 動画を開く / ダウンロード</a>',
                        unsafe_allow_html=True,
                    )
                elif not _web_playable:
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
            client_name = doc.get("client_name", "")
            billing_name = doc.get("billing_name", "")
            if client_name or billing_name:
                st.caption(f"📌 納入先: {client_name or '－'} ｜ 請求先: {billing_name or '－'}")
            if display_date:
                st.caption(f"撮影日: {display_date}")
            if content_text:
                st.caption(f"[memo] {content_text}")

        with col_path:
            st.code(file_path, language=None)

        st.divider()


# st.navigation から呼ばれてこのスクリプトが実行される（__main__ ガードは付けない）
main()
