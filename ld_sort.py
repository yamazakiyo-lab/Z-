"""
ld_sort.py
LDExtraction/<工番>/ の写真・動画を <工番>/B1|B2|B3|B4/ に、
メタ JSON を _annotations/<工番>/ に振り分けるスクリプト

使用方法:
  python ld_sort.py [--dry-run]

動作:
  1. _LDExtraction/<工番>/ の画像・動画ファイルを探す
  2. JSON の phase に従って 91/<工番フルネーム>/B1|B2|B3 に move
     phase が空の場合は B4 に move
  3. 同名の _meta.json があれば _annotations/<工番>/ に move（B フォルダには入れない）
  4. 91フォルダ内の既存工番フォルダ（フルネーム）を前方一致で検索して使用する
     （例: 4031-01 → 4031-01_PMX-L2-200(1)-155-83 リビルト機 1995年3月製）

注意:
  - --dry-run では実際の移動は行わず結果のみ表示
"""

import re
import sys
import json
import shutil
import logging
from pathlib import Path
from typing import Optional

MAGIC_MAP = [
    (b"\xff\xd8\xff",          ".jpg"),
    (b"\x89PNG\r\n\x1a\n",     ".png"),
]


def detect_ext(path: Path) -> str:
    """マジックバイトからファイルの拡張子を推定する。"""
    try:
        header = path.read_bytes()[:12]
    except Exception:
        return ""
    for magic, ext in MAGIC_MAP:
        if header[:len(magic)] == magic:
            return ext
    if len(header) >= 12 and header[4:8] == b"ftyp":
        brand = header[8:12]
        return ".mov" if brand in (b"qt  ", b"moov") else ".mp4"
    if header[:4] == b"RIFF" and header[8:12] == b"AVI ":
        return ".avi"
    return ""


# ── パス定数 ──────────────────────────────────────────────────────────────────
LD_EXTRACTION = Path(
    r"Z:\takachiho\2to9_業務別フォルダ\91_工番別実績写真・動画\_LDExtraction"
)
BASE_DEST = Path(
    r"Z:\takachiho\2to9_業務別フォルダ\91_工番別実績写真・動画"
)
ANNOTATIONS_ROOT = BASE_DEST / "_annotations"

MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".mp4", ".mov", ".avi"}

# ── ログ設定 ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _get_workno(name: str) -> Optional[str]:
    """フォルダ名の先頭から工番コード（例: 4031-01）を抽出して正規化する。"""
    s = str(name).strip().lstrip("#")
    m = re.match(r"^([A-Za-z]*\d+[-_]\d{2})", s)
    if not m:
        return None
    raw = m.group(1).replace("_", "-")
    m2 = re.match(r"^([A-Za-z]*)(\d+)-(\d{2})$", raw)
    if not m2:
        return None
    prefix = m2.group(1).upper()
    left   = m2.group(2)
    right  = m2.group(3)
    if prefix:
        return f"{prefix}{left}-{right}"
    return f"{left.lstrip('0') or '0'}-{right}"


def _find_existing_a_folder(workno: str) -> Optional[Path]:
    """91フォルダ内で工番コードが一致する既存フォルダを前方一致で返す。"""
    if not BASE_DEST.is_dir():
        return None
    candidates = []
    try:
        for d in BASE_DEST.iterdir():
            if not d.is_dir():
                continue
            if d.name.startswith("_"):  # _annotations, _LDExtraction 等を除外
                continue
            if _get_workno(d.name) == workno:
                candidates.append(d)
    except Exception:
        return None
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: p.name.lower())[0]


def sort_ld_extraction(dry_run: bool = False) -> None:
    if not LD_EXTRACTION.exists():
        logger.error(f"LDExtraction が見つかりません: {LD_EXTRACTION}")
        return

    moved = 0
    skipped = 0
    errors = 0

    for koban_dir in sorted(LD_EXTRACTION.iterdir()):
        if not koban_dir.is_dir():
            continue
        koban = koban_dir.name

        for media_file in sorted(koban_dir.iterdir()):
            ext = media_file.suffix.lower()

            # 拡張子なし → マジックバイトで判定してリネーム
            if not ext:
                if not media_file.with_suffix(".json").exists():
                    continue  # JSON もなければスキップ
                detected = detect_ext(media_file)
                if not detected:
                    logger.warning(f"拡張子判定不能、スキップ: {koban}/{media_file.name}")
                    continue
                new_path = media_file.with_name(media_file.name + detected)
                if dry_run:
                    logger.info(f"[dry-run] リネーム予定: {media_file.name} → {new_path.name}")
                else:
                    media_file.rename(new_path)
                    logger.info(f"リネーム: {media_file.name} → {new_path.name}")
                media_file = new_path
                ext = detected

            if ext not in MEDIA_EXTENSIONS:
                continue

            json_file = media_file.with_suffix(".json")

            # phase を JSON から読んで B1/B2/B3 に直接振り分け（なければ B4）
            phase = ""
            if json_file.exists():
                try:
                    phase = json.loads(json_file.read_text(encoding="utf-8")).get("phase", "")
                except Exception:
                    pass
            b_folder = phase if phase in ("B1", "B2", "B3") else "B4"

            # 既存の工番フォルダ（フルネーム）を前方一致で検索
            workno = _get_workno(koban)
            a_folder = _find_existing_a_folder(workno) if workno else None
            if a_folder is None:
                # 既存フォルダが見つからない場合は工番コードだけのフォルダに入れる
                a_folder = BASE_DEST / koban
                logger.warning(f"既存工番フォルダ未発見、新規作成: {koban}/")

            dest_dir   = a_folder / b_folder
            dest_media = dest_dir / media_file.name
            ann_dir    = ANNOTATIONS_ROOT / koban
            dest_json  = ann_dir / json_file.name

            # 既に移動済みならスキップ
            if dest_media.exists():
                logger.info(f"スキップ（既存）: {koban}/{media_file.name}")
                skipped += 1
                continue

            if dry_run:
                logger.info(f"[dry-run] 写真移動予定: {koban}/{media_file.name} → {a_folder.name}/{b_folder}/")
                if json_file.exists():
                    logger.info(f"[dry-run] JSON移動予定:  {koban}/{json_file.name} → _annotations/{koban}/")
                moved += 1
                continue

            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(media_file), str(dest_media))
                logger.info(f"写真移動: {koban}/{media_file.name} → {a_folder.name}/{b_folder}/")

                if json_file.exists():
                    ann_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(json_file), str(dest_json))
                    logger.info(f"JSON移動: {koban}/{json_file.name} → _annotations/{koban}/")

                moved += 1

            except Exception as e:
                logger.error(f"エラー ({koban}/{media_file.name}): {e}")
                errors += 1

    # ── 空になった工番フォルダを削除 ─────────────────────────────────────────
    removed_dirs = 0
    for koban_dir in sorted(LD_EXTRACTION.iterdir()):
        if not koban_dir.is_dir():
            continue
        remaining = list(koban_dir.iterdir())
        if not remaining:
            if dry_run:
                logger.info(f"[dry-run] 空フォルダ削除予定: {koban_dir.name}/")
            else:
                try:
                    koban_dir.rmdir()
                    logger.info(f"空フォルダ削除: {koban_dir.name}/")
                    removed_dirs += 1
                except Exception as e:
                    logger.error(f"フォルダ削除エラー ({koban_dir.name}): {e}")

    label = "（dry-run）" if dry_run else ""
    logger.info(
        f"完了{label}: 移動 {moved} 件 / スキップ {skipped} 件 / エラー {errors} 件 / 空フォルダ削除 {removed_dirs} 件"
    )


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    sort_ld_extraction(dry_run=dry_run)
