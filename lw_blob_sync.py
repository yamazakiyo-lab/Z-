"""
lw_blob_sync.py  -  LINE WORKS 受信ファイルをローカルへ同期

  Azure Blob Storage の lw-raw コンテナにある *_meta.json を読み、
  対応する写真・動画ファイルを以下へコピーする:
    <LW_SYNC_DEST>\\<工番>\\<YYYYMMDD_HHMMSS_部分_コメント>.<拡張子>

使い方:
  python lw_blob_sync.py            # 通常実行
  python lw_blob_sync.py --dry-run  # ダウンロードせず確認だけ

環境変数 (.env または OS 環境変数):
  AZURE_BLOB_CONNECTION_STRING  Azure Blob Storage 接続文字列
  LW_BLOB_CONTAINER             コンテナ名（省略時: lw-raw）
  LW_SYNC_DEST                  同期先ルート（省略時: Z:\\takachiho\\...\\LWExtraction）
  LW_SYNC_STATE                 同期済み記録ファイル（省略時: スクリプトと同階層）
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv

# ── 環境変数 ─────────────────────────────────────────────────────────────────
load_dotenv()

BLOB_CONN_STR: str = os.environ.get("AZURE_BLOB_CONNECTION_STRING", "")
BLOB_CONTAINER: str = os.environ.get("LW_BLOB_CONTAINER", "lw-raw")
SYNC_DEST = Path(
    os.environ.get(
        "LW_SYNC_DEST",
        r"Z:\takachiho\2to9_業務別フォルダ\91_工番別実績写真・動画\_LWExtraction",
    )
)
_default_state = Path(__file__).with_name("lw_blob_sync_state.json")
STATE_FILE = Path(os.environ.get("LW_SYNC_STATE", str(_default_state)))

# Content-Type → 拡張子マッピング（Blob に拡張子が無い場合のフォールバック）
CONTENT_TYPE_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-msvideo": ".avi",
    "video/webm": ".webm",
    "video/3gpp": ".3gp",
    "application/pdf": ".pdf",
}

# ── ロガー ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ── 同期状態ファイル ──────────────────────────────────────────────────────────

def load_state() -> set[str]:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return set(data.get("synced", []))
        except Exception as e:
            logger.warning(f"状態ファイル読み込みエラー（リセット）: {e}")
    return set()


def save_state(synced: set[str]) -> None:
    STATE_FILE.write_text(
        json.dumps({"synced": sorted(synced)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"状態ファイルを保存: {STATE_FILE}")


# ── ユーティリティ ────────────────────────────────────────────────────────────

def safe_name(s: str) -> str:
    """Windows ファイル名に使えない文字・制御文字（改行・タブ等）を除去する。

    260713: LINE WORKSのコメントに改行が含まれるとファイル保存が
    [Errno 22] Invalid argument で失敗するため、制御文字は空白に置換して
    連続空白を1つにまとめる。
    """
    invalid = r'\/:*?"<>|'
    s = "".join((" " if ord(c) < 32 else c) for c in s if c not in invalid)
    return " ".join(s.split()).strip()


def resolve_ext(blob_name: str, content_type: str) -> str:
    """Blob 名の拡張子を返す。なければ Content-Type から推定する。"""
    ext = Path(blob_name).suffix.lower()
    if ext:
        return ext
    return CONTENT_TYPE_EXT.get(content_type.split(";")[0].strip(), "")


def detect_ext_from_bytes(data: bytes) -> str:
    """マジックバイトからファイルの拡張子を推定する。"""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"AVI ":
        return ".avi"
    # MP4 / MOV: ftyp ボックス（オフセット4〜8）
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in (b"qt  ", b"moov"):
            return ".mov"
        return ".mp4"
    # 3GP
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:11] == b"3gp":
        return ".3gp"
    return ""


# ── メイン同期処理 ─────────────────────────────────────────────────────────────

def main(dry_run: bool = False) -> None:
    if not BLOB_CONN_STR:
        logger.error("AZURE_BLOB_CONNECTION_STRING が未設定です。.env を確認してください。")
        sys.exit(1)

    logger.info(f"同期先: {SYNC_DEST}")
    logger.info(f"コンテナ: {BLOB_CONTAINER}")
    if dry_run:
        logger.info("[DRY RUN モード] ファイルはダウンロードしません。")

    client = BlobServiceClient.from_connection_string(BLOB_CONN_STR)
    container = client.get_container_client(BLOB_CONTAINER)

    synced = load_state()

    # *_meta.json を全件収集
    meta_blobs = [b for b in container.list_blobs() if b.name.endswith("_meta.json")]
    logger.info(f"メタファイル数: {len(meta_blobs)}")

    new_count = 0
    skip_count = 0
    error_count = 0

    for meta_blob_item in sorted(meta_blobs, key=lambda b: b.name):
        try:
            # ── メタ読み込み ──────────────────────────────────────────────
            raw = container.download_blob(meta_blob_item.name).readall()
            meta = json.loads(raw.decode("utf-8"))

            file_blob: str   = meta.get("file_blob", "").strip()
            koban: str       = meta.get("koban", "").strip()
            buhin: str       = meta.get("buhin", "").strip()
            comment: str     = meta.get("comment", "").strip()
            phase: str       = meta.get("phase", "").strip()
            recorded_at: str = meta.get("recorded_at", "")

            # 必須フィールドチェック
            if not file_blob:
                logger.warning(f"スキップ（file_blob なし）: {meta_blob_item.name}")
                skip_count += 1
                continue
            if not koban:
                logger.warning(f"スキップ（工番なし）: {meta_blob_item.name}")
                skip_count += 1
                continue

            # 同期済みチェック
            if file_blob in synced:
                logger.debug(f"スキップ（同期済み）: {file_blob}")
                skip_count += 1
                continue

            # ── Blob 存在確認 ─────────────────────────────────────────────
            blob_client = container.get_blob_client(file_blob)
            try:
                props = blob_client.get_blob_properties()
                content_type: str = props.content_settings.content_type or ""
            except Exception:
                logger.warning(f"Blob が見つかりません（スキップ）: {file_blob}")
                skip_count += 1
                continue

            # ── ファイル名生成 ─────────────────────────────────────────────
            ext = resolve_ext(file_blob, content_type)

            try:
                dt = datetime.fromisoformat(recorded_at)
                # ローカル時刻に変換
                ts = dt.astimezone().strftime("%Y%m%d_%H%M%S")
            except Exception:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")

            name_parts = [ts]
            if buhin:
                name_parts.append(safe_name(buhin))
            if comment and comment not in ("なし", ""):
                name_parts.append(safe_name(comment))

            dest_filename = "_".join(name_parts) + ext
            dest_dir = SYNC_DEST / safe_name(koban)
            dest_path = dest_dir / dest_filename

            logger.info(f"同期: {file_blob}")
            logger.info(f"  → {dest_path}")

            if dry_run:
                new_count += 1
                synced.add(file_blob)
                continue

            # ── ダウンロード & 保存 ───────────────────────────────────────
            dest_dir.mkdir(parents=True, exist_ok=True)

            # 同名ファイルが既にある場合は連番付加
            if dest_path.exists():
                stem = dest_path.stem
                for i in range(1, 1000):
                    candidate = dest_dir / f"{stem}_{i}{ext}"
                    if not candidate.exists():
                        dest_path = candidate
                        break

            file_bytes = blob_client.download_blob().readall()

            # 拡張子が不明な場合はマジックバイトで補完
            if not ext:
                detected = detect_ext_from_bytes(file_bytes)
                if detected:
                    dest_path = dest_path.with_name(dest_path.name + detected)
                    logger.info(f"  拡張子を自動検出: {detected}")
                else:
                    logger.warning(f"  拡張子を判定できませんでした: {dest_path.name}")

            dest_path.write_bytes(file_bytes)
            logger.info(f"  保存完了: {len(file_bytes):,} bytes")

            # ── meta.json をメディアファイルと同じ場所に保存 ──────────────
            meta_local = dest_path.with_suffix(".json")
            meta_out = {
                "koban": koban,
                "buhin": buhin,
                "comment": comment,
                "phase": phase,
                "recorded_at": recorded_at,
                "file_blob": file_blob,
                "source_meta_blob": meta_blob_item.name,
            }
            meta_local.write_text(
                json.dumps(meta_out, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info(f"  メタ保存: {meta_local.name}")

            synced.add(file_blob)
            new_count += 1

        except Exception as e:
            logger.error(f"エラー ({meta_blob_item.name}): {e}")
            error_count += 1

    # ── 状態保存 ──────────────────────────────────────────────────────────────
    if not dry_run and new_count > 0:
        save_state(synced)

    logger.info(
        f"完了: 新規 {new_count} 件 / スキップ {skip_count} 件 / エラー {error_count} 件"
    )


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    main(dry_run=dry_run)
