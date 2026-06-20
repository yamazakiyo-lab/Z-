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
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
import jwt as pyjwt
from azure.storage.blob import BlobServiceClient, ContentSettings
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response

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

LW_TOKEN_URL = "https://auth.worksmobile.com/oauth2/v2.0/token"
LW_FILE_URL = "https://www.worksapis.com/v1.0/bots/{bot_id}/attachments/{file_id}"
LW_SEND_URL = "https://www.worksapis.com/v1.0/bots/{bot_id}/channels/{channel_id}/messages"

# ファイルダウンロード対象の content type
DOWNLOADABLE_TYPES = {"image", "video", "file"}

# 会話ステート定数
STATE_WAITING_KOBAN   = "waiting_koban"
STATE_WAITING_BUHIN   = "waiting_buhin"
STATE_WAITING_COMMENT = "waiting_comment"

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
        return PRIVATE_KEY_CONTENT.replace("\\n", "\n")
    if not PRIVATE_KEY_PATH:
        raise EnvironmentError("LINEWORKS_PRIVATE_KEY または LINEWORKS_PRIVATE_KEY_PATH が未設定です。")
    path = Path(PRIVATE_KEY_PATH)
    if not path.exists():
        raise FileNotFoundError(f"Private Key ファイルが見つかりません: {path}")
    return path.read_text(encoding="utf-8")


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
    resp.raise_for_status()
    token_data = resp.json()

    _access_token = token_data["access_token"]
    _token_expires_at = now + token_data.get("expires_in", 3600)
    logger.info("LINE WORKS アクセストークンを更新しました。")
    return _access_token


# ── LINE WORKS メッセージ送信 ─────────────────────────────────────────────────
def _send_text(channel_id: str, text: str) -> None:
    """Bot からチャンネルにテキストメッセージを送信する。"""
    try:
        token = _get_access_token()
        url = LW_SEND_URL.format(bot_id=BOT_ID, channel_id=channel_id)
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
        logger.info(f"メッセージ送信完了: channel={channel_id}")
    except Exception as e:
        logger.error(f"メッセージ送信失敗: {e}")


# ── LINE WORKS ファイルダウンロード ───────────────────────────────────────────
def _download_lw_file(file_id: str) -> bytes:
    """LINE WORKS API からファイルをダウンロードして bytes を返す。"""
    token = _get_access_token()
    url = LW_FILE_URL.format(bot_id=BOT_ID, file_id=file_id)
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.content


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


# ── 会話状態管理（in-memory、channel_id をキーとする） ───────────────────────
# 構造: { channel_id: { "state": str, "file_blob": str, "koban": str, "buhin": str } }
_conv: dict[str, dict] = {}


def _start_inquiry(channel_id: str, file_blob: str) -> None:
    """ファイル受信後、工番質問を開始する。"""
    _conv[channel_id] = {
        "state": STATE_WAITING_KOBAN,
        "file_blob": file_blob,
        "koban": "",
        "buhin": "",
    }
    _send_text(channel_id, "どの工番ですか？")


def _save_meta(channel_id: str, comment: str) -> None:
    """メタ情報を Blob に保存し、会話状態をクリアする。"""
    state = _conv.pop(channel_id, {})
    file_blob = state.get("file_blob", "")
    meta = {
        "file_blob": file_blob,
        "koban": state.get("koban", ""),
        "buhin": state.get("buhin", ""),
        "comment": comment,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    # meta ファイルのパスはファイルと同じプレフィックス（_meta.json を付ける）
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
    _send_text(channel_id, "ありがとうございます！保存しました。")


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
                file_bytes = _download_lw_file(file_id)
                file_blob = f"{date_folder}/{uid}_{file_name}"
                _upload_to_blob(file_blob, file_bytes)
                logger.info(f"ファイル保存完了: {file_blob} ({len(file_bytes):,} bytes)")
            except Exception as e:
                logger.error(f"ファイルダウンロード失敗: {e}")

        if channel_id:
            _start_inquiry(channel_id, file_blob)

    # ── テキスト受信（会話ステート処理） ─────────────────────────────────
    elif msg_type == "text" and channel_id in _conv:
        text = content.get("text", "").strip()
        state_data = _conv[channel_id]
        state = state_data["state"]

        if state == STATE_WAITING_KOBAN:
            state_data["koban"] = text
            state_data["state"] = STATE_WAITING_BUHIN
            _send_text(channel_id, "どの部分ですか？")

        elif state == STATE_WAITING_BUHIN:
            state_data["buhin"] = text
            state_data["state"] = STATE_WAITING_COMMENT
            _send_text(channel_id, "コメントはありますか？（なければ「なし」と入力）")

        elif state == STATE_WAITING_COMMENT:
            _save_meta(channel_id, text)

    return Response(content="OK", status_code=200)


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
