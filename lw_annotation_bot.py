"""
lw_annotation_bot.py - LINE WORKS 学習協力 Bot（デスクトップPC実行）

機能:
  1. 未アノテーション写真をランダムにユーザーへ送信しコメントを募集
  2. 週間・月間・年間ランキングを全ユーザーに配信
  3. 称号・バッジ付与
  4. GDX アノテーション Blob をローカル .json サイドカーに変換

使い方:
  python lw_annotation_bot.py --send                  # 写真を送信
  python lw_annotation_bot.py --ranking week          # 週間ランキング配信
  python lw_annotation_bot.py --ranking month         # 月間ランキング配信
  python lw_annotation_bot.py --ranking year          # 年間ランキング配信
  python lw_annotation_bot.py --sync-annotations      # Blob アノテーションをローカル .json に変換
  python lw_annotation_bot.py --add-user <user_id>   # ユーザー手動登録

環境変数 (.env):
  LINEWORKS_CLIENT_ID
  LINEWORKS_CLIENT_SECRET
  LINEWORKS_SERVICE_ACCOUNT
  LINEWORKS_PRIVATE_KEY / LINEWORKS_PRIVATE_KEY_PATH
  LINEWORKS_BOT_ID
  AZURE_BLOB_CONNECTION_STRING
  LW_BLOB_CONTAINER              (省略時: lw-raw)
  AZURE_BLOB_SAS_TOKEN           写真 Blob の SAS トークン
  AZURE_PHOTOS_BLOB_ENDPOINT     写真 Blob のエンドポイント
                                 (例: https://tsegphotos.blob.core.windows.net/photos)
  TARGET_91_ROOT                 91フォルダのローカルパス
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import requests
import jwt as pyjwt
from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv

load_dotenv()

# ── 環境変数 ─────────────────────────────────────────────────────────────────
CLIENT_ID: str        = os.environ.get("LINEWORKS_CLIENT_ID", "")
CLIENT_SECRET: str    = os.environ.get("LINEWORKS_CLIENT_SECRET", "")
SERVICE_ACCOUNT: str  = os.environ.get("LINEWORKS_SERVICE_ACCOUNT", "")
PRIVATE_KEY_CONTENT: str = os.environ.get("LINEWORKS_PRIVATE_KEY", "")
PRIVATE_KEY_PATH: str = os.environ.get("LINEWORKS_PRIVATE_KEY_PATH", "")
BOT_ID: str           = os.environ.get("LINEWORKS_BOT_ID", "")
BLOB_CONN_STR: str    = os.environ.get("AZURE_BLOB_CONNECTION_STRING", "")
BLOB_CONTAINER: str   = os.environ.get("LW_BLOB_CONTAINER", "lw-raw")
BLOB_SAS_TOKEN: str   = os.environ.get("AZURE_BLOB_SAS_TOKEN", "")
PHOTOS_BLOB_ENDPOINT: str = os.environ.get(
    "AZURE_PHOTOS_BLOB_ENDPOINT",
    "https://tsegphotos.blob.core.windows.net/photos",
).rstrip("/")
TARGET_91_ROOT: Path = Path(
    os.environ.get(
        "TARGET_91_ROOT",
        r"Z:\takachiho\2to9_業務別フォルダ\91_工番別実績写真・動画",
    )
)

LW_TOKEN_URL     = "https://auth.worksmobile.com/oauth2/v2.0/token"
LW_SEND_USER_URL = "https://www.worksapis.com/v1.0/bots/{bot_id}/users/{user_id}/messages"

# ── パス定義 ─────────────────────────────────────────────────────────────────
_ROOT             = Path(__file__).resolve().parent
MANIFEST_PATH     = _ROOT / "rag" / "manifest.json"
COMMENTS_PATH     = _ROOT / "rag" / "comments.json"
ANNOTATION_STATE_BLOB  = "annotation_state.json"
GDX_ANNOTATION_PREFIX  = "gdx_annotations/"

VISION_SUPPORTED_EXT = {".jpg", ".jpeg", ".png"}

# 休暇設定ファイル
HOLIDAY_JSON_PATH = _ROOT / "lw_holiday.json"

# 月次リマインダー送信先（管理者 user_id）
ADMIN_USER_ID = os.environ.get("LW_ADMIN_USER_ID", "")

# ── 称号定義 ─────────────────────────────────────────────────────────────────
BADGES = [
    (500, "👑 写真博士"),
    (100, "🏆 記録の達人"),
    (50,  "🥇 現場の目"),
    (10,  "🌟 協力者"),
]

STREAK_BADGES = [
    (30, "🔥 30日連続"),
    (7,  "⚡ 7日連続"),
    (3,  "✨ 3日連続"),
]

# ── ロガー ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ── LINE WORKS アクセストークン ───────────────────────────────────────────────
_access_token: str    = ""
_token_expires_at: float = 0.0


def _load_private_key() -> str:
    if PRIVATE_KEY_CONTENT:
        return PRIVATE_KEY_CONTENT.replace("\\n", "\n")
    if PRIVATE_KEY_PATH:
        return Path(PRIVATE_KEY_PATH).read_text(encoding="utf-8")
    raise EnvironmentError("LINEWORKS_PRIVATE_KEY または LINEWORKS_PRIVATE_KEY_PATH が未設定です。")


def _get_access_token() -> str:
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
    data = resp.json()
    _access_token = data["access_token"]
    _token_expires_at = now + int(data.get("expires_in", 3600))
    return _access_token


# ── メッセージ送信 ────────────────────────────────────────────────────────────
def _send_text(user_id: str, text: str) -> bool:
    try:
        token = _get_access_token()
        url = LW_SEND_USER_URL.format(bot_id=BOT_ID, user_id=user_id)
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"content": {"type": "text", "text": text}},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"テキスト送信失敗 ({user_id}): {e}")
        return False


def _send_image(user_id: str, image_url: str) -> bool:
    try:
        token = _get_access_token()
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
        return True
    except Exception as e:
        logger.error(f"画像送信失敗 ({user_id}): {e}")
        return False


# ── Blob: annotation_state.json ───────────────────────────────────────────────
def _get_blob_container():
    if not BLOB_CONN_STR:
        return None
    return BlobServiceClient.from_connection_string(BLOB_CONN_STR).get_container_client(BLOB_CONTAINER)


def _load_annotation_state() -> dict:
    container = _get_blob_container()
    if container is None:
        return {"pending": {}, "want_next": [], "users": []}
    try:
        data = container.download_blob(ANNOTATION_STATE_BLOB).readall()
        return json.loads(data.decode("utf-8"))
    except Exception:
        return {"pending": {}, "want_next": [], "users": []}


def _save_annotation_state(state: dict) -> None:
    container = _get_blob_container()
    if container is None:
        return
    try:
        container.upload_blob(
            ANNOTATION_STATE_BLOB,
            json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8"),
            overwrite=True,
        )
    except Exception as e:
        logger.error(f"annotation_state.json 保存失敗: {e}")


# ── 休暇・土日チェック ────────────────────────────────────────────────────────
def _is_holiday(target: date | None = None) -> bool:
    """土日または lw_holiday.json に定義された休暇期間なら True を返す。"""
    today = target or date.today()
    # 土日
    if today.weekday() >= 5:
        return True
    # 会社独自休暇
    if HOLIDAY_JSON_PATH.exists():
        try:
            data = json.loads(HOLIDAY_JSON_PATH.read_text(encoding="utf-8"))
            for h in data.get("holidays", []):
                start = date.fromisoformat(h["start"])
                end   = date.fromisoformat(h["end"])
                if start <= today <= end:
                    logger.info(f"休暇期間のためスキップ: {h.get('label', '')} ({start}〜{end})")
                    return True
        except Exception as e:
            logger.warning(f"lw_holiday.json 読み込みエラー: {e}")
    return False


# ── ローカルデータ ────────────────────────────────────────────────────────────
def _load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {}


def _load_comments() -> dict:
    if COMMENTS_PATH.exists():
        return json.loads(COMMENTS_PATH.read_text(encoding="utf-8"))
    return {}


def _save_comments(comments: dict) -> None:
    COMMENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    COMMENTS_PATH.write_text(
        json.dumps(comments, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── 未アノテーション写真検索 ──────────────────────────────────────────────────
def _find_unannotated_docs(state: dict) -> list[tuple[str, str]]:
    """comments.json にも pending にも記録がない画像ファイルのリストを返す。"""
    manifest   = _load_manifest()
    comments   = _load_comments()
    pending    = state.get("pending", {})
    pending_ids = {v["doc_id"] for v in pending.values()}
    result = []
    for doc_id, fp in manifest.items():
        if doc_id in comments or doc_id in pending_ids:
            continue
        if Path(fp).suffix.lower() not in VISION_SUPPORTED_EXT:
            continue
        result.append((doc_id, fp))
    return result


# ── Blob URL 生成 ─────────────────────────────────────────────────────────────
def _to_blob_url(file_path: str) -> str:
    try:
        rel = Path(file_path).relative_to(TARGET_91_ROOT)
        rel_str = str(rel).replace("\\", "/")
        sas = f"?{BLOB_SAS_TOKEN}" if BLOB_SAS_TOKEN else ""
        return f"{PHOTOS_BLOB_ENDPOINT}/{rel_str}{sas}"
    except ValueError:
        return ""


# ── 称号判定 ─────────────────────────────────────────────────────────────────
def _get_badge(count: int) -> str:
    for threshold, badge in BADGES:
        if count >= threshold:
            return badge
    return ""


def _get_streak(user_id: str, comments: dict) -> int:
    dates = set()
    for entry in comments.values():
        if entry.get("user_id") == user_id and entry.get("annotated_at"):
            try:
                d = datetime.fromisoformat(entry["annotated_at"]).date()
                dates.add(d)
            except Exception:
                pass
    if not dates:
        return 0
    sorted_dates = sorted(dates, reverse=True)
    today = datetime.now().date()
    streak = 0
    current = today
    for d in sorted_dates:
        if d == current or d == current - timedelta(days=1):
            streak += 1
            current = d
        else:
            break
    return streak


def _get_streak_badge(user_id: str, comments: dict) -> str:
    streak = _get_streak(user_id, comments)
    for threshold, badge in STREAK_BADGES:
        if streak >= threshold:
            return badge
    return ""


# ── ユーザー表示名 ────────────────────────────────────────────────────────────
def _display_name(user_id: str) -> str:
    """user_id から表示名を生成（@ 前の部分を使用）。"""
    return user_id.split("@")[0] if "@" in user_id else user_id


# ── ランキング計算 ────────────────────────────────────────────────────────────
def _calculate_ranking(period: str, comments: dict) -> tuple[list[dict], str]:
    now = datetime.now(timezone.utc)
    if period == "week":
        since = now - timedelta(days=7)
        label = "週間"
    elif period == "month":
        since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        label = "月間"
    else:  # year
        since = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        label = "年間"

    counts: dict[str, int] = {}
    for entry in comments.values():
        uid = entry.get("user_id", "")
        if not uid or entry.get("borrowed_from"):
            continue
        try:
            annotated = datetime.fromisoformat(entry["annotated_at"])
            if annotated.tzinfo is None:
                annotated = annotated.replace(tzinfo=timezone.utc)
            if annotated >= since:
                counts[uid] = counts.get(uid, 0) + 1
        except Exception:
            pass

    ranking = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    return [
        {"rank": i + 1, "user_id": uid, "count": cnt, "label": label}
        for i, (uid, cnt) in enumerate(ranking)
    ], label


def _format_ranking_message(
    ranking: list[dict], label: str, comments: dict, unannotated_count: int
) -> str:
    lines = [f"【学習協力ランキング {label}】"]
    if not ranking:
        lines.append("まだデータがありません。")
    else:
        for r in ranking[:10]:
            uid  = r["user_id"]
            cnt  = r["count"]
            total = sum(
                1 for e in comments.values()
                if e.get("user_id") == uid and not e.get("borrowed_from")
            )
            badge  = _get_badge(total)
            streak = _get_streak_badge(uid, comments)
            name   = _display_name(uid)
            line   = f"{r['rank']}位 {name}  {cnt}件"
            if badge:
                line += f"  {badge}"
            if streak:
                line += f" {streak}"
            lines.append(line)

    total_annotated = sum(
        1 for e in comments.values() if not e.get("borrowed_from")
    )
    total_files = total_annotated + unannotated_count
    pct = int(total_annotated / total_files * 100) if total_files else 0

    lines.append("")
    lines.append(f"【残り未アノテーション】{unannotated_count:,}件")
    lines.append(f"【全体達成率】{pct}%（{total_annotated:,}/{total_files:,}件）")
    lines.append("")
    lines.append("ご協力ありがとうございます！")
    return "\n".join(lines)


def _format_personal_message(user_id: str, comments: dict, period: str) -> str:
    """個人向けメッセージ（前週比・称号・連続日数）。"""
    now = datetime.now(timezone.utc)
    if period == "week":
        since      = now - timedelta(days=7)
        prev_since = now - timedelta(days=14)
        prev_until = since
        label = "今週"
        prev_label = "先週"
    elif period == "month":
        since      = now.replace(day=1, hour=0, minute=0, second=0)
        prev_since = (since - timedelta(days=1)).replace(day=1)
        prev_until = since
        label = "今月"
        prev_label = "先月"
    else:
        since      = now.replace(month=1, day=1, hour=0, minute=0, second=0)
        prev_since = since.replace(year=since.year - 1)
        prev_until = since
        label = "今年"
        prev_label = "昨年"

    def _count_in(uid, s, e):
        return sum(
            1 for v in comments.values()
            if v.get("user_id") == uid and not v.get("borrowed_from")
            and _in_range(v.get("annotated_at", ""), s, e)
        )

    def _in_range(ts, s, e):
        try:
            t = datetime.fromisoformat(ts)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            return s <= t < e
        except Exception:
            return False

    curr  = _count_in(user_id, since, now)
    prev  = _count_in(user_id, prev_since, prev_until)
    total = sum(1 for v in comments.values()
                if v.get("user_id") == user_id and not v.get("borrowed_from"))
    badge  = _get_badge(total)
    streak = _get_streak(user_id, comments)
    name   = _display_name(user_id)

    lines = [f"{name}さんの{label}のまとめ 📊"]
    lines.append(f"{label}: {curr}件")
    diff = curr - prev
    if diff > 0:
        lines.append(f"{prev_label}より +{diff}件 🎉")
    elif diff < 0:
        lines.append(f"{prev_label}より {diff}件")
    lines.append(f"累計: {total}件")
    if badge:
        lines.append(f"称号: {badge}")
    if streak >= 3:
        lines.append(f"連続日数: {streak}日 🔥")
    return "\n".join(lines)


# ── 写真送信 ─────────────────────────────────────────────────────────────────
def _send_annotation_request(
    user_id: str, doc_id: str, file_path: str, state: dict
) -> bool:
    job_number = ""
    try:
        rel = Path(file_path).relative_to(TARGET_91_ROOT)
        job_number = rel.parts[0] if rel.parts else ""
    except ValueError:
        pass

    image_url = _to_blob_url(file_path)
    file_name = Path(file_path).name

    # 画像送信（失敗時はファイル名テキストで代替）
    if image_url:
        ok = _send_image(user_id, image_url)
        if not ok:
            _send_text(user_id, f"📸 {file_name}")
    else:
        _send_text(user_id, f"📸 {file_name}")

    # コメント依頼
    msg = "この写真にコメントをお願いします！\n"
    if job_number:
        msg += f"工番: {job_number}\n"
    msg += "（作業内容・状態・部品名・気になった点など）\n"
    msg += "わからない場合は「？」を入力してください。"
    _send_text(user_id, msg)

    # pending 更新
    state.setdefault("pending", {})[user_id] = {
        "doc_id": doc_id,
        "file_path": file_path,
        "job_number": job_number,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }
    # want_next から除去
    state["want_next"] = [u for u in state.get("want_next", []) if u != user_id]
    return True


# ── GDX アノテーション Blob → ローカル .json サイドカー変換 ─────────────────
def cmd_sync_annotations() -> None:
    """Blob の gdx_annotations/ を読み、ローカルに .json サイドカーを作成する。"""
    container = _get_blob_container()
    if container is None:
        logger.error("AZURE_BLOB_CONNECTION_STRING が未設定です。")
        return

    blobs = [b for b in container.list_blobs() if b.name.startswith(GDX_ANNOTATION_PREFIX)]
    logger.info(f"GDX アノテーション Blob: {len(blobs)} 件")

    comments = _load_comments()
    new_count = 0

    for blob_item in blobs:
        try:
            raw = container.download_blob(blob_item.name).readall()
            ann = json.loads(raw.decode("utf-8"))

            doc_id    = ann.get("doc_id", "")
            file_path = ann.get("file_path", "")
            comment   = ann.get("comment", "")
            user_id   = ann.get("user_id", "")
            annotated_at = ann.get("annotated_at", "")

            if not file_path or not comment:
                logger.warning(f"スキップ（必須フィールド不足）: {blob_item.name}")
                continue

            # .json サイドカー作成
            sidecar_path = Path(file_path).with_suffix(".json")
            sidecar_data = {
                "doc_id": doc_id,
                "comment": comment,
                "user_id": user_id,
                "annotated_at": annotated_at,
                "source": "annotation_bot",
            }
            sidecar_path.write_text(
                json.dumps(sidecar_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info(f"サイドカー作成: {sidecar_path.name}")

            # Blob 削除（処理済み）
            container.delete_blob(blob_item.name)
            logger.info(f"Blob 削除: {blob_item.name}")
            new_count += 1

        except Exception as e:
            logger.error(f"エラー ({blob_item.name}): {e}")

    logger.info(f"GDX アノテーション同期完了: {new_count} 件")


# ── コマンド: 写真送信 ────────────────────────────────────────────────────────
def cmd_send() -> None:
    if _is_holiday():
        logger.info(f"本日（{date.today()}）は休日のため送信をスキップします。")
        return
    state = _load_annotation_state()
    users     = state.get("users", [])
    want_next = state.get("want_next", [])
    pending   = state.get("pending", {})

    if not users:
        logger.info("登録ユーザーがいません。lw_annotation_bot.py --add-user <user_id> で追加してください。")
        return

    unannotated = _find_unannotated_docs(state)
    if not unannotated:
        logger.info("未アノテーション写真がありません。")
        for user_id in users:
            _send_text(user_id, "🎉 すべての写真にアノテーションが完了しました！ありがとうございます！")
        _save_annotation_state(state)
        return

    sent_count = 0
    for user_id in users:
        # pending 中で want_next でもないユーザーはスキップ
        if user_id in pending and user_id not in want_next:
            logger.info(f"スキップ（pending中）: {user_id}")
            continue

        doc_id, file_path = random.choice(unannotated)
        ok = _send_annotation_request(user_id, doc_id, file_path, state)
        if ok:
            sent_count += 1
            logger.info(f"送信: {user_id} → {Path(file_path).name}")
        time.sleep(0.5)

    _save_annotation_state(state)
    logger.info(f"送信完了: {sent_count} 件")


# ── コマンド: ランキング配信 ──────────────────────────────────────────────────
def cmd_ranking(period: str) -> None:
    state     = _load_annotation_state()
    users     = state.get("users", [])
    comments  = _load_comments()
    unannotated = _find_unannotated_docs(state)

    ranking, label = _calculate_ranking(period, comments)
    team_msg  = _format_ranking_message(ranking, label, comments, len(unannotated))

    for user_id in users:
        # チーム全体メッセージ
        _send_text(user_id, team_msg)
        time.sleep(0.3)
        # 個人メッセージ
        personal = _format_personal_message(user_id, comments, period)
        _send_text(user_id, personal)
        time.sleep(0.5)

    logger.info(f"ランキング配信完了: {len(users)} 名")


# ── コマンド: ユーザー追加 ────────────────────────────────────────────────────
def cmd_add_user(user_id: str) -> None:
    state = _load_annotation_state()
    users = state.setdefault("users", [])
    if user_id in users:
        logger.info(f"すでに登録済み: {user_id}")
        return
    users.append(user_id)
    _save_annotation_state(state)
    logger.info(f"ユーザー登録完了: {user_id}")
    _send_text(user_id,
        "こんにちは！写真・動画の学習協力 Bot です 📸\n"
        "施工写真にコメントを付けることでAI検索精度が上がります。\n"
        "ご協力よろしくお願いします！\n"
        "（しばらくすると最初の写真が届きます）"
    )


# ── コマンド: 一斉送信 ───────────────────────────────────────────────────────
def cmd_broadcast(message: str) -> None:
    """登録済み全ユーザーにメッセージを一斉送信する。"""
    state = _load_annotation_state()
    users = state.get("users", [])
    if not users:
        logger.info("登録ユーザーがいません。")
        return
    success = 0
    for user_id in users:
        if _send_text(user_id, message):
            success += 1
        time.sleep(0.3)
    logger.info(f"一斉送信完了: {success}/{len(users)} 名")


# ── コマンド: 月次チャット削除リマインダー ────────────────────────────────────
def cmd_cleanup_reminder() -> None:
    """全ユーザーに「不要なチャットを削除してください」を送信する（月1回）。"""
    state = _load_annotation_state()
    users = state.get("users", [])
    if not users:
        logger.info("登録ユーザーがいません。")
        return
    msg = (
        "🗑️ Botチャットのお掃除のお願い\n"
        "このBotとのトーク履歴が溜まってきました。\n"
        "不要になったメッセージはトークを長押し → 削除で消せます。\n"
        "ご協力よろしくお願いします！"
    )
    for user_id in users:
        _send_text(user_id, msg)
        time.sleep(0.3)
    logger.info(f"削除リマインダー送信完了: {len(users)} 名")


# ── コマンド: 休暇設定更新リマインダー（GW明け年次） ─────────────────────────
def cmd_holiday_reminder() -> None:
    """管理者に lw_holiday.json の更新を促すメッセージを送信する。"""
    admin = ADMIN_USER_ID
    if not admin:
        logger.warning("LW_ADMIN_USER_ID が未設定です。.env に追加してください。")
        return
    msg = (
        "📅 年次休暇設定の更新をお願いします\n"
        "GWが終わりました。来年分の休暇期間を lw_holiday.json に追記してください。\n"
        "・夏季休暇（8月）\n"
        "・年末年始（12月〜1月）\n"
        "・GW（翌年4月〜5月）"
    )
    _send_text(admin, msg)
    logger.info(f"休暇設定更新リマインダー送信: {admin}")


# ── メイン ────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="LINE WORKS 学習協力 Bot")
    parser.add_argument("--send", action="store_true", help="未アノテーション写真を送信（土日・休暇日はスキップ）")
    parser.add_argument("--ranking", choices=["week", "month", "year"], help="ランキング配信")
    parser.add_argument("--sync-annotations", action="store_true",
                        help="Blob の GDX アノテーションをローカル .json サイドカーに変換")
    parser.add_argument("--add-user", metavar="USER_ID", help="ユーザーを手動登録")
    parser.add_argument("--broadcast", metavar="MESSAGE", help="登録済み全ユーザーにメッセージを一斉送信")
    parser.add_argument("--cleanup-reminder", action="store_true",
                        help="全ユーザーに Botチャット削除リマインダーを送信（月1回）")
    parser.add_argument("--holiday-reminder", action="store_true",
                        help="管理者に lw_holiday.json 更新リマインダーを送信（GW明け年次）")
    args = parser.parse_args()

    if args.send:
        cmd_send()
    elif args.ranking:
        cmd_ranking(args.ranking)
    elif args.sync_annotations:
        cmd_sync_annotations()
    elif args.add_user:
        cmd_add_user(args.add_user)
    elif args.broadcast:
        cmd_broadcast(args.broadcast)
    elif args.cleanup_reminder:
        cmd_cleanup_reminder()
    elif args.holiday_reminder:
        cmd_holiday_reminder()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
