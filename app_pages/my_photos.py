"""📸 FM POST(マイフォト) — 自分の作業写真・動画の個人アルバム+格納。

コンセプト: ワーカーが自分のために撮った写真を工番別アルバムとして自分専用に
使い(閲覧・削除自由)、作業が終わったらコメントを付けて「格納」する。格納すると
LINE WORKS Bot投稿と同じ契約(*_meta.json)で夜間パイプラインがZの91フォルダへ
取り込み、検索・学習データになる。

- 個人領域: Blob lw-raw/myphoto/<氏名>/<工番>/... (メタ無し=同期対象外)
- 格納: 各ファイルの隣に *_meta.json を書くだけ(lw_blob_syncが拾う)
- 閲覧権限: 本人+管理者(山嵜喜隆・山嵜絵里)
- 動画は30秒まで(mp4/mov、mvhd解析)。格納済みは削除不可。
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import streamlit as st

CONTAINER = os.getenv("LW_BLOB_CONTAINER", "lw-raw")
PREFIX = "myphoto/"
ADMINS = {n.strip() for n in os.getenv(
    "MYPHOTO_ADMIN_NAMES", "山嵜喜隆,山嵜絵里").split(",") if n.strip()}
_ALIAS = {"専務": "山嵜絵里", "yamazakiyo@tseg.co.jp": "山嵜喜隆",
          "yamazakiyo": "山嵜喜隆", "s_user01": "山嵜絵里"}
IMG_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
VID_EXTS = {".mp4", ".mov", ".m4v"}
VIDEO_MAX_SEC = 30
JST = timezone(timedelta(hours=9))


def _norm(s: str) -> str:
    return (s or "").replace(" ", "").replace("　", "").replace("﨑", "崎").lower()


def _current_name() -> str:
    try:
        from urllib.parse import unquote
        hdrs = st.context.headers or {}
        raw = (hdrs.get("X-MS-CLIENT-PRINCIPAL-NAME")
               or hdrs.get("X-Ms-Client-Principal-Name") or "").strip()
        name = unquote(raw).strip()
        for k, v in _ALIAS.items():
            if _norm(k) == _norm(name):
                return v
        return name
    except Exception:
        return ""


def _is_admin(name: str) -> bool:
    return any(_norm(a) == _norm(name) for a in ADMINS)


@st.cache_resource
def _container():
    from azure.storage.blob import BlobServiceClient
    conn = os.getenv("AZURE_BLOB_CONNECTION_STRING", "")
    return BlobServiceClient.from_connection_string(conn).get_container_client(CONTAINER)


def _sas_url(blob_name: str) -> str:
    from azure.storage.blob import BlobSasPermissions, generate_blob_sas
    conn = os.getenv("AZURE_BLOB_CONNECTION_STRING", "")
    parts = dict(x.split("=", 1) for x in conn.split(";") if "=" in x)
    sas = generate_blob_sas(
        account_name=parts["AccountName"], container_name=CONTAINER,
        blob_name=blob_name, account_key=parts["AccountKey"],
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(hours=2))
    return (f"https://{parts['AccountName']}.blob.core.windows.net/"
            f"{CONTAINER}/{blob_name}?{sas}")


@st.cache_data(ttl=3600, show_spinner=False)
def _workno_master() -> dict:
    try:
        raw = _container().download_blob("workno_master.json").readall()
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


@st.cache_data(ttl=3600, show_spinner=False)
def _user_names() -> dict:
    try:
        raw = _container().download_blob("lw_user_names.json").readall()
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _lw_user_id(name: str) -> str:
    nk = _norm(name)
    for uid, n in _user_names().items():
        if nk and _norm(n) == nk:
            return uid
    return ""


def _normalize_koban(raw: str) -> str:
    s = (raw or "").strip().upper().replace("−", "-").translate(
        str.maketrans("０１２３４５６７８９", "0123456789"))
    if re.fullmatch(r"[A-Z]{0,4}\d{3,6}", s):
        s += "-00"
    return s if re.fullmatch(r"[A-Z]{0,4}\d{3,6}-\d{2}", s) else ""


def _video_duration(data: bytes) -> float | None:
    """mp4/movのmvhdから再生秒数を読む。読めなければNone。"""
    i = data.find(b"mvhd")
    if i < 0 or i + 24 > len(data):
        return None
    ver = data[i + 4]
    if ver == 1 and i + 36 > len(data):
        return None
    try:
        if ver == 0:
            # version/flags(4) + ctime(4) + mtime(4) → timescale @+16, duration @+20
            ts = int.from_bytes(data[i + 16:i + 20], "big")
            dur = int.from_bytes(data[i + 20:i + 24], "big")
        else:
            # version/flags(4) + ctime(8) + mtime(8) → timescale @+24, duration @+28(8B)
            ts = int.from_bytes(data[i + 24:i + 28], "big")
            dur = int.from_bytes(data[i + 28:i + 36], "big")
        return dur / ts if ts else None
    except Exception:
        return None


def _safe(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", s).strip() or "file"


def _list_albums(user: str) -> dict:
    """{工番: {"files": [(blob, size, mtime)], "committed": set(blob)}} を返す。"""
    albums: dict[str, dict] = {}
    metas: set = set()
    prefix = f"{PREFIX}{user}/"
    for b in _container().list_blobs(name_starts_with=prefix):
        rel = b.name[len(prefix):]
        parts = rel.split("/", 1)
        if len(parts) != 2:
            continue
        koban = parts[0]
        alb = albums.setdefault(koban, {"files": [], "committed": set()})
        if b.name.endswith("_meta.json"):
            metas.add(b.name)
        else:
            alb["files"].append((b.name, b.size, b.last_modified))
    for koban, alb in albums.items():
        for name, _, _ in alb["files"]:
            if name.rsplit(".", 1)[0] + "_meta.json" in metas:
                alb["committed"].add(name)
        alb["files"].sort(key=lambda x: x[0])
    return albums


def main() -> None:
    st.page_link("app_pages/home.py", label="ホームに戻る", icon="🏠")
    st.title("📸 FM POST（マイフォト）")

    me = _current_name()
    if not me:
        st.error("利用者を特定できませんでした。ログインし直してください。")
        st.stop()

    user = me
    if _is_admin(me):
        users = sorted({b.name[len(PREFIX):].split("/", 1)[0]
                        for b in _container().list_blobs(name_starts_with=PREFIX)
                        if "/" in b.name[len(PREFIX):]} | {me})
        user = st.selectbox("表示するメンバー(管理者のみ)", users,
                            index=users.index(me) if me in users else 0)

    st.caption("自分の作業写真・動画を工番別のアルバムとして貯めておく場所です。"
               "格納するまでは自分(と管理者)だけが見られ、削除も自由。作業が終わったら"
               "コメントを付けて「格納」すると、夜間の自動処理で会社の工番フォルダに"
               "取り込まれ、FMP SEARCHで検索できるようになります。")

    # ── アップロード ────────────────────────────────────────────────────────
    st.subheader("追加する")
    col1, col2 = st.columns([1, 2])
    with col1:
        koban_raw = st.text_input("工番", placeholder="例: 4671-00")
    koban = _normalize_koban(koban_raw) if koban_raw else ""
    master = _workno_master()
    job_name = ((master.get(koban) or {}).get("name", "") or "") if koban else ""
    with col2:
        if koban_raw and not koban:
            st.warning("工番の形式が読み取れません(例: 4671-00 / IS080064-00)")
        elif koban:
            st.info(f"工番 {koban}" + (f"「{job_name}」" if job_name
                                       else " (工番マスタに未登録。このまま投稿は可能)"))

    ups = st.file_uploader(
        "写真・動画を選択(複数可)。動画は30秒まで(mp4/mov)",
        type=[e[1:] for e in sorted(IMG_EXTS | VID_EXTS)],
        accept_multiple_files=True)
    if st.button("⬆️ マイフォトに追加", disabled=not (koban and ups and user == me)):
        cont = _container()
        ok = ng = 0
        for f in ups:
            ext = os.path.splitext(f.name)[1].lower()
            data = f.getvalue()
            if ext in VID_EXTS:
                sec = _video_duration(data)
                if sec is None:
                    st.error(f"{f.name}: 動画の長さを確認できませんでした(mp4/movで保存し直してください)")
                    ng += 1
                    continue
                if sec > VIDEO_MAX_SEC:
                    st.error(f"{f.name}: 動画が{sec:.0f}秒あります。30秒以内に切り出してください")
                    ng += 1
                    continue
            ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
            blob = f"{PREFIX}{user}/{koban}/{ts}_{uuid.uuid4().hex[:8]}_{_safe(f.name)}"
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            from azure.storage.blob import ContentSettings
            cont.upload_blob(blob, data, overwrite=True,
                             content_settings=ContentSettings(content_type=ctype))
            ok += 1
        if ok:
            st.success(f"{ok} 件を工番 {koban} のアルバムに追加しました")
            st.rerun()

    # ── アルバム一覧 ────────────────────────────────────────────────────────
    st.divider()
    st.subheader("アルバム")
    albums = _list_albums(user)
    if not albums:
        st.info("まだ写真がありません。上のフォームから追加してください。")
        return

    for koban in sorted(albums, reverse=True):
        alb = albums[koban]
        files = alb["files"]
        committed = alb["committed"]
        pending = [f for f in files if f[0] not in committed]
        jname = ((master.get(koban) or {}).get("name", "") or "")
        label = (f"📁 {koban}" + (f"「{jname[:25]}」" if jname else "")
                 + f" — {len(files)}件"
                 + (f"(未格納 {len(pending)})" if pending else "(格納済み)"))
        with st.expander(label, expanded=False):
            cols = st.columns(4)
            for i, (blob, size, _) in enumerate(files):
                ext = os.path.splitext(blob)[1].lower()
                with cols[i % 4]:
                    url = _sas_url(blob)
                    if ext in IMG_EXTS:
                        st.image(url, use_container_width=True)
                    else:
                        st.video(url)
                    done = blob in committed
                    if done:
                        st.caption("✅ 格納済み")
                    elif user == me and st.button(
                            "🗑️ 削除", key=f"del_{blob}"):
                        _container().delete_blob(blob)
                        st.rerun()
            if pending and user == me:
                st.markdown("---")
                st.markdown(f"**この工番の未格納 {len(pending)} 件を格納する**")
                comment = st.text_area(
                    "コメント(作業内容・気づいたこと。検索とAIの学習に使われます)",
                    key=f"cm_{koban}", placeholder="例: 後メタル交換。当たり調整に時間がかかった")
                phase = st.radio(
                    "フェーズ", ["B1 着手前", "B2 着手中", "B3 出荷以降", "F 完成時"],
                    index=1, horizontal=True, key=f"ph_{koban}")
                if st.button(f"📦 {koban} を格納する", key=f"cmt_{koban}",
                             disabled=not comment.strip()):
                    cont = _container()
                    uid = _lw_user_id(me)
                    now = datetime.now(timezone.utc).isoformat()
                    for blob, _, _ in pending:
                        meta = {"file_blob": blob, "koban": koban, "buhin": "",
                                "comment": comment.strip(),
                                "phase": phase.split()[0],
                                "recorded_at": now, "user_id": uid,
                                "source": "fmpost"}
                        cont.upload_blob(
                            blob.rsplit(".", 1)[0] + "_meta.json",
                            json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"),
                            overwrite=True)
                    st.success(f"{len(pending)} 件を格納しました。今夜の自動処理で"
                               f"工番フォルダに取り込まれ、明日には検索に出ます📦")
                    st.rerun()


main()
