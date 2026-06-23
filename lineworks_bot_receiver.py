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
PHOTOS_BLOB_ENDPOINT: str = os.environ.get(
    "AZURE_PHOTOS_BLOB_ENDPOINT",
    "https://tsegphotos.blob.core.windows.net/photos",
).rstrip("/")
BLOB_SAS_TOKEN: str = os.environ.get("AZURE_BLOB_SAS_TOKEN", "")

LW_TOKEN_URL = "https://auth.worksmobile.com/oauth2/v2.0/token"
LW_FILE_URL = "https://www.worksapis.com/v1.0/bots/{bot_id}/attachments/{file_id}"
LW_SEND_CHANNEL_URL = "https://www.worksapis.com/v1.0/bots/{bot_id}/channels/{channel_id}/messages"
LW_SEND_USER_URL    = "https://www.worksapis.com/v1.0/bots/{bot_id}/users/{user_id}/messages"

# ファイルダウンロード対象の content type
DOWNLOADABLE_TYPES = {"image", "video", "file"}

# 会話ステート定数
STATE_WAITING_KOBAN        = "waiting_koban"
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


def _save_gdx_annotation(doc_id: str, file_path: str, comment: str, user_id: str) -> None:
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
            "annotated_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False, indent=2).encode("utf-8")
        container = client.get_container_client(BLOB_CONTAINER)
        container.upload_blob(blob_name, data, overwrite=True)
        logger.info(f"GDX アノテーション保存: {blob_name}")
    except Exception as e:
        logger.error(f"GDX アノテーション保存失敗: {e}")


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
    _send_text(channel_id, user_id, "ファイルを受け取りました！\nどの工番ですか？")


def _upload_meta(file_blob: str, koban: str, buhin: str, comment: str, phase: str) -> None:
    """1ファイル分のメタを Blob に保存する。"""
    meta = {
        "file_blob": file_blob,
        "koban": koban,
        "buhin": buhin,
        "comment": comment,
        "phase": phase,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
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

    _upload_meta(file_blob, koban, buhin, comment, phase)

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
                    next_url    = item.get("blob_url", "")
                    job_number  = item.get("job_number", "")
                    if next_url:
                        _send_image(channel_id, user_id, next_url)
                    else:
                        _send_text(channel_id, user_id, f"📸 {Path(next_fp).name}")
                    ann_msg = "この写真にコメントをお願いします！\n"
                    if job_number:
                        ann_msg += f"工番: {job_number}\n"
                    ann_msg += "（作業内容・状態・部品名・気になった点など）\n"
                    ann_msg += "わからない場合は「？」を入力してください。"
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
                    "「T」と送ると今すぐ学習協力写真が届きます。"
                )

        if not _skip_state and user_id in _conv:
            state_data = _conv[user_id]
            state = state_data["state"]
            ch = state_data.get("channel_id", channel_id)

            if state == STATE_WAITING_KOBAN:
                state_data["koban"] = text
                state_data["state"] = STATE_WAITING_BUHIN
                _send_text(ch, user_id, "どの部分ですか？")

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

                if text == "？":
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
                else:
                    # Blob に GDX アノテーション保存
                    _save_gdx_annotation(doc_id, file_path, text, user_id)
                    # annotation_state の pending から除去・want_next はデスクトップ側で処理
                    ann_state = _load_annotation_state()
                    ann_state.get("pending", {}).pop(user_id, None)
                    _save_annotation_state(ann_state)
                    # Y/N を聞く
                    state_data["state"] = STATE_WAITING_NEXT
                    _send_text(ch, user_id,
                        "ありがとうございます！コメントを保存しました 🎉\n"
                        "次の写真も協力しますか？\nY → 続ける　N → 今日はここまで（明日また届きます）"
                    )

            elif state == STATE_WAITING_NEXT:
                ch = state_data.get("channel_id", channel_id)
                yes_words = {"y", "yes", "はい", "お願い", "続ける", "1"}
                no_words  = {"n", "no", "いいえ", "ここまで", "やめる", "stop", "2"}
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
                        next_url     = item.get("blob_url", "")
                        job_number   = item.get("job_number", "")
                        if next_url:
                            _send_image(ch, user_id, next_url)
                        else:
                            _send_text(ch, user_id, f"📸 {Path(next_fp).name}")
                        ann_msg = "この写真にコメントをお願いします！\n"
                        if job_number:
                            ann_msg += f"工番: {job_number}\n"
                        ann_msg += "（作業内容・状態・部品名・気になった点など）\n"
                        ann_msg += "わからない場合は「？」を入力してください。"
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
                        "Y か N で答えてください 🙏\nY → 続ける　N → 今日はここまで（明日また届きます）"
                    )

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
