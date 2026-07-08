"""
migrate_annotations.py
B1/B2/B3/B4 フォルダ内の .json サイドカーを _annotations/<工番>/ に移行する

使い方:
  python migrate_annotations.py --dry-run   # 件数確認のみ
  python migrate_annotations.py             # 実際に移行

移行ルール:
  <工番>/B*/some_file.json  →  _annotations/<工番>/some_file.json
"""

import sys
import shutil
import logging
from pathlib import Path

ROOT = Path(r"Z:\takachiho\2to9_業務別フォルダ\91_工番別実績写真・動画")
ANNOTATIONS_ROOT = ROOT / "_annotations"

B_FOLDERS = {"B1", "B2", "B3", "B4"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def migrate(dry_run: bool = False) -> None:
    if not ROOT.exists():
        logger.error(f"91フォルダが見つかりません: {ROOT}")
        return

    found = []

    for koban_dir in sorted(ROOT.iterdir()):
        if not koban_dir.is_dir():
            continue
        if koban_dir.name.startswith("_"):
            continue  # _annotations 自身などをスキップ

        for b_dir in koban_dir.iterdir():
            if not b_dir.is_dir():
                continue
            # フォルダ名に B1/B2/B3/B4 が含まれるか判定
            b_key = None
            for key in B_FOLDERS:
                if key in b_dir.name:
                    b_key = key
                    break
            if not b_key:
                continue

            for json_file in b_dir.rglob("*.json"):
                found.append((json_file, koban_dir.name))

    logger.info(f"対象 JSON ファイル数: {len(found)}")
    for jf, koban in found:
        logger.info(f"  {jf.relative_to(ROOT)}")

    if not found:
        logger.info("移行対象なし。終了します。")
        return

    if dry_run:
        logger.info("（dry-run モード：実際の移動は行いません）")
        return

    moved = 0
    errors = 0
    for json_file, koban in found:
        dest_dir = ANNOTATIONS_ROOT / koban
        dest_path = dest_dir / json_file.name

        # 同名ファイルが既にある場合は連番
        if dest_path.exists():
            stem = dest_path.stem
            for i in range(1, 1000):
                candidate = dest_dir / f"{stem}_{i}.json"
                if not candidate.exists():
                    dest_path = candidate
                    break

        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(json_file), str(dest_path))
            logger.info(f"移動: {json_file.relative_to(ROOT)} → _annotations/{koban}/{dest_path.name}")
            moved += 1
        except Exception as e:
            logger.error(f"エラー ({json_file.name}): {e}")
            errors += 1

    logger.info(f"完了: 移動 {moved} 件 / エラー {errors} 件")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    migrate(dry_run=dry_run)
