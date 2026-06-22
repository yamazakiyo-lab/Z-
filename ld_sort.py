"""
ld_sort.py
_LDExtraction/<工番>/ の写真・動画を 91/<工番フルネーム>/B1|B2|B3|B4/ に、
メタ JSON を _annotations/<工番>/ に振り分けるスクリプト

使用方法:
  python ld_sort.py [--dry-run]

動作:
  1. _LDExtraction/<工番>/ の画像・動画ファイルを探す
  2. _GDExtraction/工事一覧表.csv でマスタを読み込む
  3. 91フォルダ内の既存工番フォルダ（フルネーム）を前方一致で検索
     見つからない場合はマスタからフルネームで新規作成
     マスタにもない場合は警告してスキップ
  4. JSON の phase に従って B1|B2|B3 に振り分け（なければ B4）
     B-フォルダも既存（GDXの {workno}_B4整理前写真・動画 等）を前方一致で検索
  5. _meta.json を _annotations/<工番>/ に移動
  6. 空になった _LDExtraction/<工番>/ を削除

注意:
  - --dry-run では実際の移動は行わず結果のみ表示
"""

import csv
import io
import re
import sys
import json
import shutil
import logging
from pathlib import Path
from typing import Dict, Optional

# ── パス定数 ──────────────────────────────────────────────────────────────────
BASE_DEST = Path(
    r"Z:\takachiho\2to9_業務別フォルダ\91_工番別実績写真・動画"
)
LD_EXTRACTION  = BASE_DEST / "_LDExtraction"
GD_EXTRACTION  = BASE_DEST / "_GDExtraction"
ANNOTATIONS_ROOT = BASE_DEST / "_annotations"

MASTER_CSV_NAMES = ("工事一覧表.csv", "CSV工番マスタ.csv", "master.csv")
MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".mp4", ".mov", ".avi"}

MAGIC_MAP = [
    (b"\xff\xd8\xff",      ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
]

# ── ログ設定 ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── ユーティリティ ────────────────────────────────────────────────────────────

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


def _sanitize(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", str(name))
    return name.strip().rstrip(".") or "untitled"


def _normalize_master_name(name: str) -> str:
    """マスタ名称を Windows ファイル名として安全化する（GDXと同じ処理）。"""
    # 英字トークン間の "_" を半角スペースに戻す
    parts = str(name).strip().split("_")
    out = []
    i = 0
    while i < len(parts):
        if parts[i].isascii() and parts[i].isalpha():
            phrase = [parts[i]]
            j = i + 1
            while j < len(parts) and parts[j].isascii() and parts[j].isalpha():
                phrase.append(parts[j])
                j += 1
            out.append(" ".join(phrase))
            i = j
        else:
            out.append(parts[i])
            i += 1
    return _sanitize("_".join(out))


def _get_workno(name: str) -> Optional[str]:
    """フォルダ/ファイル名の先頭から工番コードを抽出して正規化する。"""
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


# ── マスタ CSV 読み込み ────────────────────────────────────────────────────────

def _read_master() -> Dict[str, str]:
    """_GDExtraction/工事一覧表.csv 等を読んで {workno: 工事名} を返す。"""
    for fname in MASTER_CSV_NAMES:
        path = GD_EXTRACTION / fname
        if not path.exists():
            continue
        for enc in ("utf-8-sig", "cp932", "utf-8"):
            try:
                text = path.read_text(encoding=enc, errors="strict")
                break
            except Exception:
                text = None
        if not text:
            continue

        rows = list(csv.reader(io.StringIO(text)))
        if not rows:
            continue
        header = [h.strip() for h in rows[0]]

        def find_col(keys):
            for k in keys:
                for i, h in enumerate(header):
                    if h == k:
                        return i
            for k in keys:
                for i, h in enumerate(header):
                    if k in h:
                        return i
            return None

        code_i = find_col(["プロジェクトコード", "工番", "コード"])
        name_i = find_col(["プロジェクト名", "工事名", "案件名", "名称", "名"])
        if code_i is None or name_i is None:
            code_i, name_i = 0, 1

        master: Dict[str, str] = {}
        for r in rows[1:]:
            if len(r) <= max(code_i, name_i):
                continue
            wn = _get_workno((r[code_i] or "").strip())
            nm = (r[name_i] or "").strip()
            if wn and nm:
                master[wn] = nm
        logger.info(f"マスタ読込: {path.name} ({len(master)} 件)")
        return master

    logger.warning(f"工事一覧表.csv が見つかりません: {GD_EXTRACTION}")
    return {}


# ── フォルダ検索 ──────────────────────────────────────────────────────────────

def _find_existing_a_folder(workno: str) -> Optional[Path]:
    """91フォルダ内で工番コードが一致する既存Aフォルダを前方一致で返す。"""
    if not BASE_DEST.is_dir():
        return None
    candidates = []
    try:
        for d in BASE_DEST.iterdir():
            if not d.is_dir() or d.name.startswith("_"):
                continue
            if _get_workno(d.name) == workno:
                candidates.append(d)
    except Exception:
        return None
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: p.name.lower())[0]


def _get_or_create_a_folder(
    workno: str, koban: str, master: Dict[str, str], dry_run: bool
) -> Optional[Path]:
    """既存Aフォルダを探し、なければマスタからフルネームで作成する。
    マスタにもない場合は None を返す。"""
    a = _find_existing_a_folder(workno)
    if a:
        return a

    master_name = master.get(workno)
    if not master_name:
        logger.warning(f"91フォルダ未発見＆マスタ未登録のためスキップ: {koban}")
        return None

    folder_name = _sanitize(f"{workno}_{_normalize_master_name(master_name)}")
    a = BASE_DEST / folder_name
    if not dry_run:
        a.mkdir(parents=True, exist_ok=True)
        logger.info(f"Aフォルダ新規作成: {folder_name}")
    else:
        logger.info(f"[dry-run] Aフォルダ作成予定: {folder_name}")
    return a


def _find_b_folder(a_folder: Path, b_label: str) -> Path:
    """Aフォルダ内で b_label を含む既存サブフォルダを探して返す。
    見つからない場合は b_label 名のパスを返す（作成はしない）。"""
    try:
        for d in a_folder.iterdir():
            if d.is_dir() and b_label in d.name:
                return d
    except Exception:
        pass
    return a_folder / b_label


# ── メイン処理 ────────────────────────────────────────────────────────────────

def sort_ld_extraction(dry_run: bool = False) -> None:
    if not LD_EXTRACTION.exists():
        logger.error(f"_LDExtraction が見つかりません: {LD_EXTRACTION}")
        return

    master = _read_master()

    moved = 0
    skipped = 0
    errors = 0

    for koban_dir in sorted(LD_EXTRACTION.iterdir()):
        if not koban_dir.is_dir():
            continue
        koban = koban_dir.name
        workno = _get_workno(koban)

        for media_file in sorted(koban_dir.iterdir()):
            ext = media_file.suffix.lower()

            # 拡張子なし → マジックバイトで判定してリネーム
            if not ext:
                json_candidate = media_file.with_suffix(".json")
                if not json_candidate.exists():
                    continue
                detected = detect_ext(media_file)
                if not detected:
                    logger.warning(f"拡張子判定不能、スキップ: {koban}/{media_file.name}")
                    continue
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

            # phase を JSON から読んで B1/B2/B3/B4 を決定
            phase = ""
            if json_file.exists():
                try:
                    phase = json.loads(
                        json_file.read_text(encoding="utf-8")
                    ).get("phase", "")
                except Exception:
                    pass
            b_label = phase if phase in ("B1", "B2", "B3") else "B4"

            # Aフォルダを取得/作成
            a_folder = _get_or_create_a_folder(workno or koban, koban, master, dry_run)
            if a_folder is None:
                skipped += 1
                continue

            # Bフォルダを既存から検索（GDXの {workno}_B4整理前写真・動画 形式も対応）
            b_dir      = _find_b_folder(a_folder, b_label)
            dest_media = b_dir / media_file.name
            ann_dir    = ANNOTATIONS_ROOT / koban
            dest_json  = ann_dir / json_file.name

            # 既に移動済みならスキップ
            if dest_media.exists():
                logger.info(f"スキップ（既存）: {koban}/{media_file.name}")
                skipped += 1
                continue

            if dry_run:
                logger.info(
                    f"[dry-run] 写真移動予定: {koban}/{media_file.name}"
                    f" → {a_folder.name}/{b_dir.name}/"
                )
                if json_file.exists():
                    logger.info(
                        f"[dry-run] JSON移動予定: {koban}/{json_file.name}"
                        f" → _annotations/{koban}/"
                    )
                moved += 1
                continue

            try:
                b_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(media_file), str(dest_media))
                logger.info(
                    f"写真移動: {koban}/{media_file.name}"
                    f" → {a_folder.name}/{b_dir.name}/"
                )

                if json_file.exists():
                    ann_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(json_file), str(dest_json))
                    logger.info(
                        f"JSON移動: {koban}/{json_file.name}"
                        f" → _annotations/{koban}/"
                    )

                moved += 1

            except Exception as e:
                logger.error(f"エラー ({koban}/{media_file.name}): {e}")
                errors += 1

    # ── 空になった工番フォルダを削除 ─────────────────────────────────────────
    removed_dirs = 0
    for koban_dir in sorted(LD_EXTRACTION.iterdir()):
        if not koban_dir.is_dir():
            continue
        if not list(koban_dir.iterdir()):
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
        f"完了{label}: 移動 {moved} 件 / スキップ {skipped} 件"
        f" / エラー {errors} 件 / 空フォルダ削除 {removed_dirs} 件"
    )


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    sort_ld_extraction(dry_run=dry_run)
