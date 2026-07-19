"""_annotations の重複フォルダを「正規化した工番」に統合するスクリプト。

背景:
    ld_sort.py が以前、アノテーションの入れ先にLW側の生フォルダ名を使っていたため、
    同じ工番でも表記が違うと _annotations に別フォルダが乱立した。
    （例: 4618-00 / 4618-00 エア漏れ / 04618-00 → 本来はすべて 4618-00）
    ld_sort.py は修正済みなので今後は発生しないが、過去分をここで統合する。

使い方:
    python consolidate_annotations.py --dry-run   # 何がどこへ統合されるか確認
    python consolidate_annotations.py             # 実際に統合

動作:
    _annotations/<フォルダ名> の先頭から工番を取り出して正規化し、
    フォルダ名が正規化工番と違うものを _annotations/<正規化工番>/ へ移動する。
    - 同名ファイルが移動先にある場合は上書きせず "_dup1" 等を付けて退避
    - 工番が読み取れないフォルダ（コメント文など）は触らない
    - 空になった元フォルダは削除
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from ld_sort import ANNOTATIONS_ROOT, _get_workno, logger


def _unique_dest(dest: Path) -> Path:
    """移動先に同名ファイルがある場合、上書きせず別名を返す。"""
    if not dest.exists():
        return dest
    stem, suf = dest.stem, dest.suffix
    for i in range(1, 1000):
        cand = dest.with_name(f"{stem}_dup{i}{suf}")
        if not cand.exists():
            return cand
    return dest.with_name(f"{stem}_dup_last{suf}")


def consolidate(dry_run: bool = False) -> None:
    if not ANNOTATIONS_ROOT.exists():
        logger.error(f"_annotations が見つかりません: {ANNOTATIONS_ROOT}")
        return

    moved_files = 0
    merged_dirs = 0
    untouched = 0
    no_workno = []

    for d in sorted(ANNOTATIONS_ROOT.iterdir()):
        if not d.is_dir():
            continue
        workno = _get_workno(d.name)
        if not workno:
            no_workno.append(d.name)
            continue
        if d.name == workno:
            untouched += 1
            continue  # 既に正規化済み

        dest_dir = ANNOTATIONS_ROOT / workno
        files = [f for f in d.rglob("*") if f.is_file()]
        logger.info(
            f"{'[dry-run] ' if dry_run else ''}統合: {d.name}/ → {workno}/ "
            f"({len(files)} ファイル)"
        )
        merged_dirs += 1

        if dry_run:
            moved_files += len(files)
            continue

        dest_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            target = _unique_dest(dest_dir / f.name)
            try:
                shutil.move(str(f), str(target))
                moved_files += 1
                if target.name != f.name:
                    logger.warning(f"  同名のため改名: {f.name} → {target.name}")
            except Exception as e:
                logger.error(f"  移動失敗 {f.name}: {e}")

        # 空になった元フォルダを削除
        try:
            for sub in sorted(d.rglob("*"), key=lambda p: -len(p.parts)):
                if sub.is_dir() and not any(sub.iterdir()):
                    sub.rmdir()
            if not any(d.iterdir()):
                d.rmdir()
                logger.info(f"  空フォルダ削除: {d.name}/")
        except Exception as e:
            logger.warning(f"  元フォルダの削除に失敗 {d.name}: {e}")

    label = "（dry-run）" if dry_run else ""
    logger.info(
        f"完了{label}: 統合フォルダ {merged_dirs} 件 / 移動ファイル {moved_files} 件 "
        f"/ 既に正規化済み {untouched} 件"
    )
    if no_workno:
        logger.info(
            f"工番が読み取れず触らなかったフォルダ {len(no_workno)} 件: "
            + "、".join(no_workno[:10])
        )


if __name__ == "__main__":
    consolidate(dry_run="--dry-run" in sys.argv)
