"""
LINE WORKS Bot Callback 受信サーバー

機能:
  1. POST /lineworks/callback で Webhook 受信
  2. X-WORKS-Signature を Bot Secret で検証
  3. Callback JSON を Azure Blob Storage に保存
  4. content type が image / video / file の場合:
       a. Service Account JWT で LINE WORKS アクセストークン取得（1時間キャッシュ）
       b. LINE WORKS API からファイルをダウンロード
       c. Azure Blob Storage (lw-raw コンテナ) に保存
       d. ユーザーに工番・部分・コメントを順番に質問
  5. テキスト受信時は会話状態に応じて次の質問または保存
  6. 200 OK を返す

Blob 保存パス:
  lw-raw/YYYYMMDD/callback_HHMMSS_<uuid>.json   ← 全 Callback の JSON
  lw-raw/YYYYMMDD/HHMMSS_<uuid>_<元ファイル名>  ← 画像・動画・ファイル
  lw-raw/YYYYMMDD/HHMMSS_<uuid>_meta.json       ← 工番・部分・コメント

起動:
  pip install -r requirements.txt
  uvicorn lineworks_bot_receiver:app --host 0.0.0.0 --port 8000

環境変数 (.env または OS 環境変数):
  LINEWORKS_BOT_SECRET            Bot Secret（署名検証）
  LINEWORKS_CLIENT_ID             Client ID
  LINEWORKS_CLIENT_SECRET         Client Secret
  LINEWORKS_SERVICE_ACCOUNT       Service Account ID（メールアドレス形式）
  LINEWORKS_PRIVATE_KEY_PATH      Private Key .pem ファイルのパス
  LINEWORKS_PRIVATE_KEY           PEM 内容を直接指定（ファイル不要）
  LINEWORKS_BOT_ID                Bot ID（数字）
  AZURE_BLOB_CONNECTION_STRING    Blob Storage 接続文字列
  LW_BLOB_CONTAINER               Blob コンテナ名（省略時: lw-raw）
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sys
import time
import uuid
from base64 import b64encode
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests
import jwt as pyjwt
from azure.storage.blob import BlobServiceClient, ContentSettings, generate_blob_sas, BlobSasPermissions
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

# ── 環境変数読み込み ──────────────────────────────────────────────────────────
load_dotenv()

BOT_SECRET: str = os.environ.get("LINEWORKS_BOT_SECRET", "")
CLIENT_ID: str = os.environ.get("LINEWORKS_CLIENT_ID", "")
CLIENT_SECRET: str = os.environ.get("LINEWORKS_CLIENT_SECRET", "")
SERVICE_ACCOUNT: str = os.environ.get("LINEWORKS_SERVICE_ACCOUNT", "")
PRIVATE_KEY_PATH: str = os.environ.get("LINEWORKS_PRIVATE_KEY_PATH", "")
PRIVATE_KEY_CONTENT: str = os.environ.get("LINEWORKS_PRIVATE_KEY", "")
BOT_ID: str = os.environ.get("LINEWORKS_BOT_ID", "")
BLOB_CONN_STR: str = os.environ.get("AZURE_BLOB_CONNECTION_STRING", "")
BLOB_CONTAINER: str = os.environ.get("LW_BLOB_CONTAINER", "lw-raw")
PHOTOS_BLOB_ENDPOINT: str = os.environ.get(
    "AZURE_PHOTOS_BLOB_ENDPOINT",
    "https://tsegphotos.blob.core.windows.net/photos",
).rstrip("/")
BLOB_SAS_TOKEN: str = os.environ.get("AZURE_BLOB_SAS_TOKEN", "")
PHOTOS_CONTAINER: str = os.environ.get("AZURE_PHOTOS_CONTAINER", "photos")

LW_TOKEN_URL = "https://auth.worksmobile.com/oauth2/v2.0/token"
LW_FILE_URL = "https://www.worksapis.com/v1.0/bots/{bot_id}/attachments/{file_id}"
LW_SEND_CHANNEL_URL = "https://www.worksapis.com/v1.0/bots/{bot_id}/channels/{channel_id}/messages"
LW_SEND_USER_URL    = "https://www.worksapis.com/v1.0/bots/{bot_id}/users/{user_id}/messages"
LW_USER_PROFILE_URL = "https://www.worksapis.com/v1.0/users/{user_id}"
USER_NAMES_BLOB     = "lw_user_names.json"

# ファイルダウンロード対象の content type
DOWNLOADABLE_TYPES = {"image", "video", "file"}

# 会話ステート定数
STATE_WAITING_KOBAN        = "waiting_koban"
STATE_WAITING_BRANCH_CHOICE = "waiting_branch_choice"  # 枝番が複数ある工番の選択待ち
STATE_WAITING_KOBAN_CONFIRM = "waiting_koban_confirm"  # マスタ未登録工番の新規確認 Y/修正 待ち
STATE_WAITING_BUHIN        = "waiting_buhin"
STATE_WAITING_COMMENT      = "waiting_comment"
STATE_WAITING_PHASE        = "waiting_phase"         # フェーズ（B1/B2/B3）待ち
STATE_WAITING_BATCH        = "waiting_batch"          # まとめ保存 Y/N 待ち
# 学習協力 Bot 用ステート
STATE_WAITING_ANNOTATION   = "waiting_annotation"   # 写真コメント待ち
STATE_WAITING_NEXT         = "waiting_next"          # 次の写真送るか Y/N 待ち

PHASE_MAP = {
    "1": "B1", "着手前": "B1", "b1": "B1",
    "2": "B2", "着手中": "B2", "b2": "B2",
    "3": "B3", "出荷以降": "B3", "b3": "B3",
}

# Blob 上の annotation_state.json パス
ANNOTATION_STATE_BLOB = "annotation_state.json"
GDX_ANNOTATION_PREFIX = "gdx_annotations/"

# 工番マスタ(export_workno_master.py が日次で lw-raw に出力)
WORKNO_MASTER_BLOB = "workno_master.json"
_workno_master_cache: dict = {}
_workno_master_loaded = False
_KOBAN_RE = __import__("re").compile(r"^([A-Za-z]{0,4})(\d+)(?:[-_](\d{1,2}))?$")


def _normalize_koban(text: str):
    """投稿された工番文字列を正規化してマスタキー候補を返す。

    戻り値: (正規化キー or None, 工番の形をしているか, 枝番が明示されていたか)
    例:
      '4026-02'  -> ('4026-02', True, True)
      'IS080064' -> ('IS080064-00', True, False)  # 枝番省略は -00 補完(未明示)
      'ty080221' -> ('TY080221-00', True, False)  # 小文字は大文字化
      '角ネジ...\n...' -> (None, False, False)     # 工番の形をしていない

    枝番が未明示のときは、呼び出し側で同じ工番の枝番候補を探して
    複数あればユーザーに選ばせる（勝手に -00 と決めない）。
    """
    if not text:
        return None, False, False
    # 先頭行のみ・全角空白/空白除去・全角英数を半角化
    first = text.strip().splitlines()[0].strip() if text.strip().splitlines() else ""
    first = first.translate(str.maketrans(
        "０１２３４５６７８９－ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ",
        "0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    )).replace(" ", "").replace("　", "").upper()
    m = _KOBAN_RE.match(first)
    if not m:
        return None, False, False
    prefix, digits, suffix = m.group(1), m.group(2), m.group(3)
    # プレフィックスありは数字部のゼロを保持(IS080064-00)、
    # プレフィックスなしは先頭ゼロを削る(00003967->3967)。マスタキーの形式に合わせる。
    num = digits if prefix else (digits.lstrip("0") or "0")
    suf = suffix.zfill(2) if suffix else "00"
    key = f"{prefix}{num}-{suf}"
    return key, True, bool(suffix)


def _find_branch_candidates(key: str, master: dict) -> list:
    """枝番違いの同一工番をマスタから探して昇順で返す。

    例: key='4618-00' → ['4618-00', '4618-01', '4618-02'] （マスタに在るものだけ）
    枝番が未明示で投稿されたとき、候補が複数あれば本人に選ばせるために使う。
    """
    base = key.rsplit("-", 1)[0]
    return sorted(k for k in master if k.rsplit("-", 1)[0] == base)


def _format_branch_choices(cands: list, master: dict) -> str:
    """枝番候補を「1) 工番 工事名」の形に整形する。"""
    lines = []
    for i, wn in enumerate(cands, 1):
        info = master.get(wn) or {}
        name = (info.get("name") or "").strip()
        client = (info.get("client") or "").strip()
        detail = name or client or ""
        lines.append(f"{i}) {wn}" + (f"　{detail[:34]}" if detail else ""))
    return "\n".join(lines)


def _load_workno_master() -> dict:
    """工番マスタを Blob から読み込み(キャッシュ)。{workno: {client, billing}} の worknos 部分を返す。"""
    global _workno_master_cache, _workno_master_loaded
    if _workno_master_loaded:
        return _workno_master_cache
    client = _get_blob_client()
    if client is None:
        _workno_master_loaded = True
        return {}
    try:
        container = client.get_container_client(BLOB_CONTAINER)
        data = container.download_blob(WORKNO_MASTER_BLOB).readall()
        payload = json.loads(data.decode("utf-8"))
        _workno_master_cache = payload.get("worknos", {})
        logger.info(f"工番マスタ読み込み: {len(_workno_master_cache)} 件")
    except Exception as e:
        logger.warning(f"工番マスタ読み込み失敗(照合なしで続行): {e}")
        _workno_master_cache = {}
    _workno_master_loaded = True
    return _workno_master_cache

# ── ロガー設定 ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ── 起動時チェック ────────────────────────────────────────────────────────────
def _warn_if_missing(name: str, value: str) -> None:
    if not value:
        logger.warning(f"環境変数 {name} が未設定です。")

_warn_if_missing("LINEWORKS_BOT_SECRET", BOT_SECRET)
_warn_if_missing("LINEWORKS_CLIENT_ID", CLIENT_ID)
_warn_if_missing("LINEWORKS_SERVICE_ACCOUNT", SERVICE_ACCOUNT)
_warn_if_missing("LINEWORKS_PRIVATE_KEY_PATH", PRIVATE_KEY_PATH)
_warn_if_missing("LINEWORKS_BOT_ID", BOT_ID)
_warn_if_missing("AZURE_BLOB_CONNECTION_STRING", BLOB_CONN_STR)

# ── Blob クライアント ─────────────────────────────────────────────────────────
_blob_client: Optional[BlobServiceClient] = None

def _get_blob_client() -> Optional[BlobServiceClient]:
    global _blob_client
    if _blob_client is None and BLOB_CONN_STR:
        _blob_client = BlobServiceClient.from_connection_string(BLOB_CONN_STR)
    return _blob_client


def _upload_to_blob(blob_name: str, data: bytes, content_type: str = "application/octet-stream") -> bool:
    """data を Blob Storage にアップロードする。失敗時は False を返す。"""
    client = _get_blob_client()
    if client is None:
        logger.warning("Blob Storage 未設定のためアップロードをスキップします。")
        return False
    try:
        container = client.get_container_client(BLOB_CONTAINER)
        container.upload_blob(
            name=blob_name,
            data=data,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )
        logger.info(f"Blob アップロード完了: {BLOB_CONTAINER}/{blob_name}")
        return True
    except Exception as e:
        logger.error(f"Blob アップロード失敗: {e}")
        return False


# ── LINE WORKS アクセストークン（キャッシュ付き） ─────────────────────────────
_access_token: str = ""
_token_expires_at: float = 0.0


def _load_private_key() -> str:
    """Private Key を環境変数または .pem ファイルから読み込む。"""
    if PRIVATE_KEY_CONTENT:
        key = PRIVATE_KEY_CONTENT.replace("\\n", "\n")
        lines = key.splitlines()
        logger.info(f"[KEY-DIAG] source=env lines={len(lines)} first={lines[0][:30] if lines else '(empty)'} last={lines[-1][:30] if lines else '(empty)'}")
        return key
    if not PRIVATE_KEY_PATH:
        raise EnvironmentError("LINEWORKS_PRIVATE_KEY または LINEWORKS_PRIVATE_KEY_PATH が未設定です。")
    path = Path(PRIVATE_KEY_PATH)
    if not path.exists():
        raise FileNotFoundError(f"Private Key ファイルが見つかりません: {path}")
    key = path.read_text(encoding="utf-8")
    lines = key.splitlines()
    logger.info(f"[KEY-DIAG] source=file path={path} lines={len(lines)} first={lines[0][:30] if lines else '(empty)'} last={lines[-1][:30] if lines else '(empty)'}")
    return key


def _get_access_token() -> str:
    """Service Account JWT フローでアクセストークンを取得する。"""
    global _access_token, _token_expires_at

    now = time.time()
    if _access_token and now < _token_expires_at - 60:
        return _access_token

    private_key = _load_private_key()
    jwt_payload = {
        "iss": CLIENT_ID,
        "sub": SERVICE_ACCOUNT,
        "iat": int(now),
        "exp": int(now) + 3600,
    }
    logger.info(f"[JWT-DIAG] iss={CLIENT_ID} sub={SERVICE_ACCOUNT}")
    assertion = pyjwt.encode(jwt_payload, private_key, algorithm="RS256")

    resp = requests.post(
        LW_TOKEN_URL,
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope": "bot.message",
        },
        timeout=10,
    )
    if not resp.ok:
        logger.error(f"[TOKEN-ERROR] status={resp.status_code} body={resp.text[:500]}")
    resp.raise_for_status()
    token_data = resp.json()

    _access_token = token_data["access_token"]
    _token_expires_at = now + int(token_data.get("expires_in", 3600))
    logger.info("LINE WORKS アクセストークンを更新しました。")
    return _access_token


# ── LINE WORKS メッセージ送信 ─────────────────────────────────────────────────
def _send_text(channel_id: str, user_id: str, text: str) -> None:
    """Bot からテキストメッセージを送信する。channel_id があればチャンネル宛、なければ user_id 宛。"""
    try:
        token = _get_access_token()
        if channel_id:
            url = LW_SEND_CHANNEL_URL.format(bot_id=BOT_ID, channel_id=channel_id)
        else:
            url = LW_SEND_USER_URL.format(bot_id=BOT_ID, user_id=user_id)
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"content": {"type": "text", "text": text}},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info(f"メッセージ送信完了: channel={channel_id}, user={user_id}")
    except Exception as e:
        logger.error(f"メッセージ送信失敗: {e}")


# ── LINE WORKS 画像送信 ────────────────────────────────────────────────────────
def _send_image(channel_id: str, user_id: str, image_url: str) -> None:
    try:
        token = _get_access_token()
        if channel_id:
            url = LW_SEND_CHANNEL_URL.format(bot_id=BOT_ID, channel_id=channel_id)
        else:
            url = LW_SEND_USER_URL.format(bot_id=BOT_ID, user_id=user_id)
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"content": {
                "type": "image",
                "originalContentUrl": image_url,
                "previewImageUrl": image_url,
            }},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"画像送信失敗: {e}")


# ── LINE WORKS ファイルダウンロード ───────────────────────────────────────────
def _download_lw_file(file_id: str) -> tuple[bytes, str]:
    """LINE WORKS API からファイルをダウンロードして (bytes, content_type) を返す。
    リダイレクト先にも Authorization ヘッダーを引き継ぐ。
    """
    token = _get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    url = LW_FILE_URL.format(bot_id=BOT_ID, file_id=file_id)

    # まずリダイレクトを追わずに取得
    resp = requests.get(url, headers=headers, timeout=60, allow_redirects=False)

    # リダイレクトの場合はヘッダーを引き継いで再リクエスト
    while resp.status_code in (301, 302, 303, 307, 308):
        redirect_url = resp.headers.get("Location")
        if not redirect_url:
            break
        logger.info(f"ファイルダウンロード リダイレクト: {redirect_url}")
        resp = requests.get(redirect_url, headers=headers, timeout=60, allow_redirects=False)

    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "application/octet-stream").split(";")[0].strip()
    return resp.content, content_type


# ── 署名検証 ─────────────────────────────────────────────────────────────────
def _verify_signature(body: bytes, signature_header: str) -> bool:
    if not BOT_SECRET:
        logger.warning("BOT_SECRET 未設定のため署名検証をスキップします（開発用）。")
        return True
    expected = b64encode(
        hmac.new(BOT_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("utf-8")
    return hmac.compare_digest(expected, signature_header)


# ── Blob パス生成 ─────────────────────────────────────────────────────────────
def _make_blob_prefix() -> tuple[str, str]:
    """(date_folder, time_uuid) を返す。"""
    now = datetime.now(timezone.utc).astimezone()
    date_folder = now.strftime("%Y%m%d")
    time_str = now.strftime("%H%M%S")
    short_id = uuid.uuid4().hex[:8]
    return date_folder, f"{time_str}_{short_id}"


# ── annotation_state.json（Blob 共有） ───────────────────────────────────────
def _load_annotation_state() -> dict:
    client = _get_blob_client()
    if client is None:
        return {"pending": {}, "want_next": [], "users": []}
    try:
        container = client.get_container_client(BLOB_CONTAINER)
        data = container.download_blob(ANNOTATION_STATE_BLOB).readall()
        return json.loads(data.decode("utf-8"))
    except Exception:
        return {"pending": {}, "want_next": [], "users": []}


def _save_annotation_state(state: dict) -> None:
    client = _get_blob_client()
    if client is None:
        return
    try:
        container = client.get_container_client(BLOB_CONTAINER)
        container.upload_blob(
            ANNOTATION_STATE_BLOB,
            json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8"),
            overwrite=True,
        )
    except Exception as e:
        logger.error(f"annotation_state.json 保存失敗: {e}")


def _save_gdx_annotation(doc_id: str, file_path: str, comment: str, user_id: str, quality: str = "ok") -> None:
    """GDX 写真のアノテーションを Blob に保存する（デスクトップが .json サイドカーに変換）。"""
    client = _get_blob_client()
    if client is None:
        return
    try:
        blob_name = f"{GDX_ANNOTATION_PREFIX}{doc_id}.json"
        data = json.dumps({
            "doc_id": doc_id,
            "file_path": file_path,
            "comment": comment,
            "user_id": user_id,
            "quality": quality,
            "annotated_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False, indent=2).encode("utf-8")
        container = client.get_container_client(BLOB_CONTAINER)
        container.upload_blob(blob_name, data, overwrite=True)
        logger.info(f"GDX アノテーション保存: {blob_name}")
    except Exception as e:
        logger.error(f"GDX アノテーション保存失敗: {e}")


# ── ユーザー名キャッシュ（Blob: lw_user_names.json） ─────────────────────────
_user_names_cache: dict = {}
_user_names_cache_loaded: bool = False
_user_name_fetch_attempted: set = set()  # 取得試行済み（失敗含む）→ 再試行防止

def _load_user_names() -> dict:
    global _user_names_cache, _user_names_cache_loaded
    if _user_names_cache_loaded:
        return _user_names_cache
    client = _get_blob_client()
    if client is None:
        return {}
    try:
        container = client.get_container_client(BLOB_CONTAINER)
        data = container.download_blob(USER_NAMES_BLOB).readall()
        _user_names_cache = json.loads(data.decode("utf-8"))
    except Exception:
        _user_names_cache = {}
    _user_names_cache_loaded = True
    return _user_names_cache


def _save_user_names(names: dict) -> None:
    global _user_names_cache, _user_names_cache_loaded
    _user_names_cache = names
    _user_names_cache_loaded = True
    client = _get_blob_client()
    if client is None:
        return
    try:
        container = client.get_container_client(BLOB_CONTAINER)
        container.upload_blob(
            USER_NAMES_BLOB,
            json.dumps(names, ensure_ascii=False, indent=2).encode("utf-8"),
            overwrite=True,
        )
    except Exception as e:
        logger.warning(f"lw_user_names.json 保存失敗: {e}")


def _fetch_and_cache_user_name(user_id: str) -> None:
    """LINE WORKS API からユーザー名を取得してキャッシュする（失敗は無視）。"""
    # 取得試行済みとしてマーク（失敗しても再試行しない）
    _user_name_fetch_attempted.add(user_id)
    try:
        private_key = _load_private_key()
        now = time.time()
        jwt_payload = {
            "iss": CLIENT_ID,
            "sub": SERVICE_ACCOUNT,
            "iat": int(now),
            "exp": int(now) + 3600,
        }
        assertion = pyjwt.encode(jwt_payload, private_key, algorithm="RS256")
        token_resp = requests.post(
            LW_TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "scope": "bot.message user.read",
            },
            timeout=10,
        )
        if not token_resp.ok:
            logger.warning(f"ユーザー名取得: トークン取得失敗 ({user_id}) status={token_resp.status_code}")
            return
        token = token_resp.json().get("access_token", "")
        if not token:
            return
        resp = requests.get(
            LW_USER_PROFILE_URL.format(user_id=user_id),
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            name = data.get("displayName") or data.get("userName", "")
            if name:
                names = _load_user_names()
                names[user_id] = name
                _save_user_names(names)
                logger.info(f"ユーザー名キャッシュ: {user_id} → {name}")
        else:
            logger.warning(f"ユーザー名取得: プロフィールAPI失敗 ({user_id}) status={resp.status_code}")
    except Exception as e:
        logger.warning(f"ユーザー名取得失敗 ({user_id}): {e}")


# ── 会話状態管理（in-memory、channel_id をキーとする） ───────────────────────
# 構造: { user_id: { "state": str, "channel_id": str, "file_blob": str, "koban": str, "buhin": str } }
_conv: dict[str, dict] = {}


def _start_inquiry(user_id: str, channel_id: str, file_blob: str) -> None:
    """ファイル受信後、工番質問を開始する。会話中なら次のファイルをキューに積む。"""
    if user_id in _conv:
        # すでに会話中 → キューに追加
        _conv[user_id].setdefault("queued_files", []).append(file_blob)
        q = len(_conv[user_id]["queued_files"])
        logger.info(f"キューに追加: {user_id} / queue={q}")
        return

    _conv[user_id] = {
        "state": STATE_WAITING_KOBAN,
        "channel_id": channel_id,
        "file_blob": file_blob,
        "koban": "",
        "buhin": "",
        "queued_files": [],
    }
    _send_text(channel_id, user_id, "ファイルを受け取りました！\nどの工番ですか？（中止する場合は「X」）")


def _upload_meta(file_blob: str, koban: str, buhin: str, comment: str, phase: str,
                 user_id: str = "") -> None:
    """1ファイル分のメタを Blob に保存する。"""
    meta = {
        "file_blob": file_blob,
        "koban": koban,
        "buhin": buhin,
        "comment": comment,
        "phase": phase,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,  # 写真投稿ランキング集計用
    }
    if file_blob:
        meta_blob = file_blob.rsplit(".", 1)[0] + "_meta.json"
    else:
        date_folder, uid = _make_blob_prefix()
        meta_blob = f"{date_folder}/{uid}_meta.json"
    _upload_to_blob(
        meta_blob,
        json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"),
        "application/json",
    )
    logger.info(f"メタ保存完了: {meta}")


def _save_meta(user_id: str, phase: str) -> None:
    """メタ情報を Blob に保存し、キューがあれば一括確認へ。"""
    state = _conv.get(user_id, {})
    channel_id  = state.get("channel_id", "")
    file_blob   = state.get("file_blob", "")
    koban       = state.get("koban", "")
    buhin       = state.get("buhin", "")
    comment     = state.get("comment", "")
    queued      = state.get("queued_files", [])

    _upload_meta(file_blob, koban, buhin, comment, phase, user_id=user_id)

    if queued:
        # キューあり → まとめ保存を確認
        _conv[user_id] = {
            "state": STATE_WAITING_BATCH,
            "channel_id": channel_id,
            "koban": koban,
            "buhin": buhin,
            "comment": comment,
            "phase": phase,
            "queued_files": queued,
        }
        phase_label = {"B1": "着手前", "B2": "着手中", "B3": "出荷以降"}.get(phase, phase)
        _send_text(channel_id, user_id,
            f"ありがとうございます！保存しました。\n\n"
            f"他に {len(queued)} 件のファイルが届いています。\n"
            f"同じ設定（工番: {koban} / 部品: {buhin} / フェーズ: {phase_label}）で保存しますか？\n"
            f"Y → まとめて保存　N → 1件ずつ入力\n\n"
            f"💡 ヒント: 工番・部品・コメントが同じ写真はまとめて選択して送ると入力が1回で済みます。"
        )
    else:
        _conv.pop(user_id, None)
        _send_text(channel_id, user_id, "ありがとうございます！保存しました。")


# ── FastAPI アプリ ────────────────────────────────────────────────────────────
app = FastAPI(title="LINE WORKS Bot Receiver", version="0.3.0")


@app.post("/lineworks/callback")
async def lineworks_callback(request: Request) -> Response:
    body = await request.body()

    # 署名検証
    signature = request.headers.get("X-WORKS-Signature", "")
    if not _verify_signature(body, signature):
        logger.warning("署名検証失敗。不正なリクエストを拒否しました。")
        raise HTTPException(status_code=401, detail="Invalid signature")

    # JSON パース
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        logger.error(f"JSON パース失敗: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    source = payload.get("source", {})
    channel_id = source.get("channelId", "")
    user_id = source.get("userId", "")
    content = payload.get("content", {})
    msg_type = content.get("type", "unknown")

    logger.info(f"Callback受信: type={msg_type}, channel={channel_id}, user={user_id}")

    # ── Callback JSON を Blob に保存 ──────────────────────────────────────
    date_folder, uid = _make_blob_prefix()
    callback_blob = f"{date_folder}/callback_{uid}.json"
    _upload_to_blob(
        callback_blob,
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        "application/json",
    )

    # ── 画像・動画・ファイル受信 ──────────────────────────────────────────
    if msg_type in DOWNLOADABLE_TYPES:
        file_id = content.get("fileId", "")
        file_name = content.get("fileName", f"file_{uid}")
        file_blob = ""

        if file_id:
            try:
                file_bytes, ct = _download_lw_file(file_id)
                # 拡張子がなければ Content-Type から補完
                if not os.path.splitext(file_name)[1]:
                    ext_map = {
                        "video/mp4": ".mp4", "video/quicktime": ".mov",
                        "video/x-msvideo": ".avi", "video/x-matroska": ".mkv",
                        "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
                        "application/pdf": ".pdf",
                    }
                    ext = ext_map.get(ct, "")
                    if ext:
                        file_name = file_name + ext
                        logger.info(f"拡張子補完: {ct} → {ext}")
                file_blob = f"{date_folder}/{uid}_{file_name}"
                _upload_to_blob(file_blob, file_bytes, ct)
                logger.info(f"ファイル保存完了: {file_blob} ({len(file_bytes):,} bytes)")
            except Exception as e:
                logger.error(f"ファイルダウンロード失敗: {e}")

        if user_id:
            # 学習協力中に写真を送ってきた場合は先に学習協力を終わらせるよう案内
            current_state = _conv.get(user_id, {}).get("state", "")
            if current_state in {STATE_WAITING_ANNOTATION, STATE_WAITING_NEXT}:
                _send_text(channel_id, user_id,
                    "学習協力の回答が終わっていません 📸\n"
                    "先にコメントを入力してから写真を送ってください。"
                )
            else:
                _start_inquiry(user_id, channel_id, file_blob)

    # ── テキスト受信（会話ステート処理） ─────────────────────────────────
    elif msg_type == "text":
        text = content.get("text", "").strip()

        # _conv にない場合: annotation_state.json から pending を復元
        _skip_state = False  # T トリガーで即開始した場合は後続ステート処理をスキップ
        if user_id not in _conv:
            ann_state = _load_annotation_state()
            # 新規ユーザー自動登録
            if user_id and user_id not in ann_state.get("users", []):
                ann_state.setdefault("users", []).append(user_id)
                _save_annotation_state(ann_state)
                logger.info(f"学習協力ユーザー登録: {user_id}")
            # ユーザー名キャッシュ（未取得かつ未試行の場合のみ）
            if user_id and user_id not in _load_user_names() and user_id not in _user_name_fetch_attempted:
                _fetch_and_cache_user_name(user_id)
            # pending があれば会話状態を復元
            pending = ann_state.get("pending", {})
            if user_id in pending:
                _conv[user_id] = {
                    "state": STATE_WAITING_ANNOTATION,
                    "channel_id": channel_id,
                    "doc_id": pending[user_id]["doc_id"],
                    "file_path": pending[user_id]["file_path"],
                    "job_number": pending[user_id].get("job_number", ""),
                }

        if user_id not in _conv:
            # トリガーワード「T」→ 学習協力をオンデマンド開始
            if text.strip().lower() in {"t", "ｔ"}:
                import random as _random
                pool = ann_state.get("unannotated_pool", [])
                if pool:
                    item = _random.choice(pool)
                    pool.remove(item)
                    next_doc_id = item["doc_id"]
                    next_fp     = item.get("file_path", "")
                    next_url    = item.get("thumb_url", "") or item.get("blob_url", "")
                    job_number  = item.get("job_number", "")
                    if next_url:
                        _send_image(channel_id, user_id, next_url)
                    else:
                        _send_text(channel_id, user_id, f"📸 {Path(next_fp).name}")
                    ann_msg = "この写真・動画について教えてください！\n"
                    if job_number:
                        ann_msg += f"工番: {job_number}\n"
                    ann_msg += "（部品名・シーン・何をしているかなど、5文字以上）\n"
                    ann_msg += "わからない場合はわかりそうな人に聞いてみましょう。\n"
                    ann_msg += "それでもわからない場合は「？」を入力してください。"
                    _send_text(channel_id, user_id, ann_msg)
                    ann_state.setdefault("pending", {})[user_id] = {
                        "doc_id": next_doc_id,
                        "file_path": next_fp,
                        "job_number": job_number,
                        "sent_at": datetime.now(timezone.utc).isoformat(),
                    }
                    ann_state["unannotated_pool"] = pool
                    _conv[user_id] = {
                        "state": STATE_WAITING_ANNOTATION,
                        "channel_id": channel_id,
                        "doc_id": next_doc_id,
                        "file_path": next_fp,
                        "job_number": job_number,
                    }
                    _save_annotation_state(ann_state)
                    _skip_state = True  # 写真送信済み → テキスト"T"をコメントとして処理しない
                else:
                    _send_text(channel_id, user_id, "現在送信できる写真がありません。次回の配信時にお届けします 📸")
            else:
                # 状態なし（想定外のメッセージ）→ 用途を案内
                _send_text(channel_id, user_id,
                    "このBotは施工写真の記録・学習協力専用です 📸\n"
                    "写真を送ると工番・コメントを記録できます。\n"
                    "学習協力写真が届いたらコメントをお願いします！\n"
                    "「T」と送ると今すぐ学習協力写真が届きます。\n"
                    "途中で中止したい場合は「X」でキャンセルできます。"
                )

        if not _skip_state and user_id in _conv:
            state_data = _conv[user_id]
            state = state_data["state"]
            ch = state_data.get("channel_id", channel_id)

            # キャンセルコマンド（写真アップロードフロー中のみ）
            _upload_states = {STATE_WAITING_KOBAN, STATE_WAITING_BRANCH_CHOICE, STATE_WAITING_KOBAN_CONFIRM, STATE_WAITING_BUHIN, STATE_WAITING_COMMENT, STATE_WAITING_PHASE, STATE_WAITING_BATCH}
            if state in _upload_states and text.strip().lower() in {"x", "ｘ", "キャンセル", "中止", "cancel"}:
                _conv.pop(user_id, None)
                _send_text(ch, user_id, "入力を中止しました。")
            elif state == STATE_WAITING_KOBAN:
                key, looks_like, has_branch = _normalize_koban(text)
                master = _load_workno_master()

                # 枝番が未明示 → 同じ工番の枝番候補を探す。
                # 複数あるなら勝手に -00 と決めず、どれか選んでもらう。
                asked_branch = False
                if key and looks_like and not has_branch and master:
                    cands = _find_branch_candidates(key, master)
                    if len(cands) > 1:
                        state_data["branch_cands"] = cands
                        state_data["state"] = STATE_WAITING_BRANCH_CHOICE
                        _send_text(ch, user_id,
                            f"工番 {key.rsplit('-', 1)[0]} には枝番が複数あります。\n"
                            f"どれですか？番号で答えてください。\n\n"
                            f"{_format_branch_choices(cands, master)}\n\n"
                            f"中止する場合は「X」。"
                        )
                        asked_branch = True
                    elif len(cands) == 1:
                        key = cands[0]   # 候補が1つだけならそれで確定

                if asked_branch:
                    pass  # 枝番の選択待ち。この先の判定は行わない
                elif key and key in master:
                    # マスタ一致: 工事名・納入先を見せて確認(打ち間違いなら本人が気づく)
                    info = master[key]
                    client_name = info.get("client", "")
                    state_data["koban"] = key
                    state_data["state"] = STATE_WAITING_BUHIN
                    detail = f"（納入先: {client_name}）" if client_name else ""
                    _send_text(ch, user_id, f"工番 {key} {detail}ですね。\nどの部分ですか？")
                elif looks_like:
                    # 工番の形はあるがマスタ未登録 → 新規工番として確認(取り立て工番対応)
                    state_data["koban"] = key
                    state_data["state"] = STATE_WAITING_KOBAN_CONFIRM
                    _send_text(ch, user_id,
                        f"工番 {key} はマスタに未登録です。\n"
                        f"新規工番として進めますか？\n"
                        f"Y → このまま進む　修正 → 工番を入力し直す"
                    )
                else:
                    # 工番の形をしていない(説明文など) → 必ず有効な工番の再入力を求める(C-2方針)
                    # ステートは STATE_WAITING_KOBAN のまま維持 → 工番が入るまで先に進めない
                    _send_text(ch, user_id,
                        "工番が読み取れませんでした。\n"
                        "工番を入力してください（例: 4031-00、IS080064）。\n"
                        "中止する場合は「X」。"
                    )

            elif state == STATE_WAITING_BRANCH_CHOICE:
                # 「1」「2」… の番号、または工番そのもの（4618-01 等）で選択を受け付ける
                cands = state_data.get("branch_cands") or []
                master = _load_workno_master()
                ans = text.strip().translate(str.maketrans("０１２３４５６７８９－", "0123456789-"))
                chosen = None
                if ans.isdigit() and 1 <= int(ans) <= len(cands):
                    chosen = cands[int(ans) - 1]
                else:
                    k2, looks2, _ = _normalize_koban(ans)
                    if k2 and k2 in cands:
                        chosen = k2
                if chosen:
                    info = master.get(chosen) or {}
                    name = (info.get("name") or "").strip()
                    client_name = (info.get("client") or "").strip()
                    detail = name or (f"納入先: {client_name}" if client_name else "")
                    state_data["koban"] = chosen
                    state_data["state"] = STATE_WAITING_BUHIN
                    state_data.pop("branch_cands", None)
                    _send_text(ch, user_id,
                        f"工番 {chosen}" + (f"（{detail[:34]}）" if detail else "") +
                        "ですね。\nどの部分ですか？"
                    )
                else:
                    _send_text(ch, user_id,
                        "番号で選んでください。\n\n"
                        f"{_format_branch_choices(cands, master)}\n\n"
                        "中止する場合は「X」。"
                    )

            elif state == STATE_WAITING_KOBAN_CONFIRM:
                ans = text.strip().upper()
                if ans in {"Y", "ＹＥＳ", "YES", "はい"}:
                    state_data["state"] = STATE_WAITING_BUHIN
                    _send_text(ch, user_id, f"新規工番 {state_data.get('koban','')} で進めます。\nどの部分ですか？")
                elif text.strip() in {"修正", "しゅうせい", "N", "n"}:
                    state_data["state"] = STATE_WAITING_KOBAN
                    _send_text(ch, user_id, "工番を入力し直してください。（中止する場合は「X」）")
                else:
                    _send_text(ch, user_id, "「Y」（このまま進む）または「修正」（入力し直す）で答えてください。")

            elif state == STATE_WAITING_BUHIN:
                state_data["buhin"] = text
                state_data["state"] = STATE_WAITING_COMMENT
                _send_text(ch, user_id, "コメントはありますか？（なければ「なし」と入力）\nコメントはAI検索精度を上げるため、作業内容・状態・気になった点など、なるべく詳細に入力してもらえると助かります。")

            elif state == STATE_WAITING_COMMENT:
                state_data["comment"] = text
                state_data["state"] = STATE_WAITING_PHASE
                _send_text(ch, user_id,
                    "どのフェーズですか？\n"
                    "1 → 着手前（入庫・受入時）\n"
                    "2 → 着手中（整備・加工中）\n"
                    "3 → 出荷以降（納入・引渡後）"
                )

            elif state == STATE_WAITING_PHASE:
                phase = PHASE_MAP.get(text.strip().lower())
                if not phase:
                    _send_text(ch, user_id, "1・2・3 のいずれかを入力してください。\n1 → 着手前　2 → 着手中　3 → 出荷以降")
                else:
                    _save_meta(user_id, phase)

            elif state == STATE_WAITING_BATCH:
                ans = text.strip().upper()
                queued  = state_data.get("queued_files", [])
                koban   = state_data.get("koban", "")
                buhin   = state_data.get("buhin", "")
                comment = state_data.get("comment", "")
                phase   = state_data.get("phase", "")

                if ans == "Y":
                    for qf in queued:
                        _upload_meta(qf, koban, buhin, comment, phase)
                    _conv.pop(user_id, None)
                    _send_text(ch, user_id, f"まとめて {len(queued)} 件保存しました！ありがとうございます。")

                elif ans == "N":
                    next_blob = queued.pop(0)
                    _conv[user_id] = {
                        "state": STATE_WAITING_KOBAN,
                        "channel_id": ch,
                        "file_blob": next_blob,
                        "koban": "",
                        "buhin": "",
                        "queued_files": queued,
                    }
                    _send_text(ch, user_id, "では1件ずつ入力します。\nどの工番ですか？")

                else:
                    _send_text(ch, user_id, "Y（まとめて保存）または N（1件ずつ入力）で答えてください。")

            # ── 学習協力 Bot ステート ─────────────────────────────────
            elif state == STATE_WAITING_ANNOTATION:
                doc_id = state_data.get("doc_id", "")
                file_path = state_data.get("file_path", "")

                if text.strip().lower() in {"x", "ｘ"}:
                    # X → キャンセル（スキップ扱い）
                    ann_state = _load_annotation_state()
                    ann_state.get("pending", {}).pop(user_id, None)
                    _save_annotation_state(ann_state)
                    _conv.pop(user_id, None)
                    _send_text(ch, user_id, "キャンセルしました。また定時に写真をお送りします 🙏\n「T」を押すと再開できます。")
                elif text == "？":
                    # スキップ：skipped リストに移動し pending から除去
                    ann_state = _load_annotation_state()
                    ann_state.get("pending", {}).pop(user_id, None)
                    ann_state.setdefault("skipped", [])
                    if doc_id and doc_id not in ann_state["skipped"]:
                        ann_state["skipped"].append(doc_id)
                    # 「？」スキップ回数を記録
                    skipped_by = ann_state.setdefault("skipped_by", {})
                    skipped_by[user_id] = skipped_by.get(user_id, 0) + 1
                    _save_annotation_state(ann_state)
                    _conv.pop(user_id, None)
                    _send_text(ch, user_id, "スキップしました。別の方に回します！\nまた写真が届いたらよろしくお願いします 🙏")
                elif text.strip().lower() in {"y", "ｙ", "n", "ｎ"}:
                    # Y/N はコメント保存後に聞く質問。この時点では操作の取り違えなので
                    # コメント扱いにしない。（"n" を2回送ると quality=low で "n" が
                    # そのまま保存されてしまうため、ここで確実に止める）
                    _send_text(ch, user_id,
                        "いまは写真のコメントを待っています 📸\n"
                        "Y／N は、コメントを保存したあとに聞きます。\n"
                        "・分かる方 → 部品名や作業内容を5文字以上で入力\n"
                        "・分からない → 「？」でスキップ\n"
                        "・やめる → 「X」でキャンセル"
                    )
                else:
                    # 文字数チェック（5文字未満はリトライ促す）
                    MIN_COMMENT_LEN = 5
                    is_short = len(text.strip()) < MIN_COMMENT_LEN
                    is_retry = state_data.get("comment_retry", False)

                    if is_short and not is_retry:
                        # 1回だけ再入力を促す
                        state_data["comment_retry"] = True
                        _send_text(ch, user_id,
                            "コメントが短すぎます 🙏\n"
                            "作業内容・状態・部品名など、もう少し詳しく入力してください。\n"
                            "（どうしても分からない場合は「？」を入力）"
                        )
                    else:
                        # 保存（リトライ後も短い場合は quality=low）
                        quality = "low" if is_short else "ok"
                        _save_gdx_annotation(doc_id, file_path, text, user_id, quality)
                        # annotation_state の pending から除去
                        ann_state = _load_annotation_state()
                        ann_state.get("pending", {}).pop(user_id, None)
                        _save_annotation_state(ann_state)
                        # Y/N を聞く
                        state_data["state"] = STATE_WAITING_NEXT
                        state_data.pop("comment_retry", None)
                        _send_text(ch, user_id,
                            "ありがとうございます！コメントを保存しました 🎉\n"
                            "次の写真も協力しますか？\nY → 続ける　N → 今はここまで（定時または「T」で再開）"
                        )

            elif state == STATE_WAITING_NEXT:
                ch = state_data.get("channel_id", channel_id)
                yes_words = {"y", "yes", "はい", "お願い", "続ける", "1"}
                no_words  = {"n", "no", "いいえ", "ここまで", "やめる", "stop", "2", "x", "ｘ"}
                t = text.lower().strip()
                if t in yes_words:
                    _conv.pop(user_id, None)
                    ann_state = _load_annotation_state()
                    pool = ann_state.get("unannotated_pool", [])
                    if pool:
                        import random as _random
                        item = _random.choice(pool)
                        pool.remove(item)
                        next_doc_id  = item["doc_id"]
                        next_fp      = item.get("file_path", "")
                        next_url     = item.get("thumb_url", "") or item.get("blob_url", "")
                        job_number   = item.get("job_number", "")
                        if next_url:
                            _send_image(ch, user_id, next_url)
                        else:
                            _send_text(ch, user_id, f"📸 {Path(next_fp).name}")
                        ann_msg = "この写真・動画について教えてください！\n"
                        if job_number:
                            ann_msg += f"工番: {job_number}\n"
                        ann_msg += "（部品名・シーン・何をしているかなど）\n"
                        ann_msg += "わからない場合はわかりそうな人に聞いてみましょう。\n"
                        ann_msg += "それでもわからない場合は「？」を入力してください。"
                        _send_text(ch, user_id, ann_msg)
                        ann_state.setdefault("pending", {})[user_id] = {
                            "doc_id": next_doc_id,
                            "file_path": next_fp,
                            "job_number": job_number,
                            "sent_at": datetime.now(timezone.utc).isoformat(),
                        }
                        ann_state["unannotated_pool"] = pool
                        _conv[user_id] = {
                            "state": STATE_WAITING_ANNOTATION,
                            "channel_id": ch,
                            "doc_id": next_doc_id,
                            "file_path": next_fp,
                            "job_number": job_number,
                        }
                    else:
                        _send_text(ch, user_id, "現在送信できる写真がありません。次回の配信時にお届けします 📸")
                    _save_annotation_state(ann_state)
                elif t in no_words:
                    _conv.pop(user_id, None)
                    _send_text(ch, user_id, "ありがとうございました！また定時に写真をお送りします 🙏\n「T」を押すと再開できます。")
                else:
                    _send_text(ch, user_id,
                        "Y か N で答えてください 🙏\nY → 続ける　N → 今はここまで（定時または「T」で再開）"
                    )

    return Response(content="OK", status_code=200)


@app.get("/video/{blob_path:path}")
async def video_redirect(blob_path: str) -> RedirectResponse:
    """動画BlobのSAS URLへリダイレクト（LINE WORKSがクエリ文字列を切り捨てる対策）。"""
    try:
        client = _get_blob_client()
        if client is None:
            raise HTTPException(status_code=500, detail="Storage not configured")
        account_name = client.account_name
        account_key = client.credential.account_key
        sas = generate_blob_sas(
            account_name=account_name,
            container_name=PHOTOS_CONTAINER,
            blob_name=blob_path,
            account_key=account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        url = f"https://{account_name}.blob.core.windows.net/{PHOTOS_CONTAINER}/{blob_path}?{sas}"
        logger.info(f"動画リダイレクト: {blob_path}")
        return RedirectResponse(url=url, status_code=302)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"動画リダイレクト失敗: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "blob_container": BLOB_CONTAINER,
        "bot_id": BOT_ID,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("lineworks_bot_receiver:app", host="0.0.0.0", port=8000, reload=False)
