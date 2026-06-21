"""
ld_sort.py
LDExtraction/<工番>/ の写真・動画とメタ JSON を
91_工番別実績写真・動画/<工番>/B4/ に振り分けるスクリプト

使用方法:
  python ld_sort.py [--dry-run]

動作:
  1. LDExtraction/<工番>/ の画像・動画ファイルを探す
  2. 同名の .json があれば一緒に扱う
  3. <工番>/B4/ に move（LDExtraction からは消える）
  4. annotation_state.json には追加しない（LW学習ボット対象外）

注意:
  - --dry-run では実際の移動は行わず結果のみ表示
"""

import sys
import json
import shutil
import logging
from pathlib import Path

# ── パス定数 ───────────────────────────────────────────────────────────────────
LD_EXTRACTION = Path(
    r"Z:\takachiho\2to9_業務別フォルダ\91_工番別実績写真・動画\LDExtraction"
)
BASE_DEST = Path(
    r"Z:\takachiho\2to9_業務別フォルダ\91_工番別実績写真・動画"
)

MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".mp4", ".mov", ".avi"}

# ── ログ設定 ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


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
            if media_file.suffix.lower() not in MEDIA_EXTENSIONS:
                continue

            json_file = media_file.with_suffix(".json")
            dest_dir  = BASE_DEST / koban / "B4"
            dest_media = dest_dir / media_file.name
            dest_json  = dest_dir / json_file.name

            # 既に移動済みならスキップ
            if dest_media.exists():
                logger.info(f"スキップ（既存）: {koban}/{media_file.name}")
                skipped += 1
                continue

            if dry_run:
                logger.info(f"[dry-run] 移動予定: {koban}/{media_file.name} → B4/")
                if json_file.exists():
                    logger.info(f"[dry-run]           {koban}/{json_file.name} → B4/")
                moved += 1
                continue

            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(media_file), str(dest_media))
                logger.info(f"移動: {koban}/{media_file.name} → B4/")

                if json_file.exists():
                    shutil.move(str(json_file), str(dest_json))
                    logger.info(f"移動: {koban}/{json_file.name} → B4/")

                moved += 1

            except Exception as e:
                logger.error(f"エラー ({koban}/{media_file.name}): {e}")
                errors += 1

    label = "（dry-run）" if dry_run else ""
    logger.info(
        f"完了{label}: 移動 {moved} 件 / スキップ {skipped} 件 / エラー {errors} 件"
    )


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    sort_ld_extraction(dry_run=dry_run)
