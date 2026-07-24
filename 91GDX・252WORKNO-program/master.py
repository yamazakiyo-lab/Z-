"""工番マスタ・GDX -> 91 への移動ロジック。"""

import csv
import io
import os
import re
import shutil
import stat
from pathlib import Path
from typing import Dict, List, Optional

from .utils import (
    normalize_existing_path_name,
    normalize_master_name,
    p,
    sanitize_name,
)
from .drive_sync import drive_delete_named_child_folders, sync_gdx_tree_checkpoint


PHOTO_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
VIDEO_EXT = {".mp4", ".mov", ".avi", ".mts", ".m2ts"}
JUNK_FILES = {"Thumbs.db", "desktop.ini", ".DS_Store"}
PO_LIST_EXT = {".xlsx", ".xls", ".xlsm", ".xlsb"}
MASTER_CSV_FILES = {"工事一覧表.csv", "CSV工番マスタ.csv", "master.csv"}


def normalize_workno(code: str) -> Optional[str]:
    if not code:
        return None
    s = str(code).strip().lstrip("#")
    m = re.match(r"^([A-Za-z]*)(\d+)[-_](\d{2})", s)
    if not m:
        return None
    prefix = m.group(1).upper()
    digits = m.group(2)
    right = m.group(3)
    if prefix:
        return f"{prefix}{digits}-{right}"
    left = digits.lstrip("0") or "0"
    return f"{left}-{right}"


def get_workno_from_name(name: str) -> Optional[str]:
    n = str(name).strip().lstrip("#")
    m = re.match(r"^([A-Za-z]*\d+[-_]\d{2})", n)
    if not m:
        return None
    return normalize_workno(m.group(1))


def is_media_file(p: Path) -> bool:
    ext = p.suffix.lower()
    return ext in PHOTO_EXT or ext in VIDEO_EXT


def clear_readonly(p: Path):
    try:
        os.chmod(str(p), stat.S_IWRITE)
    except Exception:
        pass


def ensure_unique_path_or_skip(dst: Path) -> Optional[Path]:
    """91 側に同名があればスキップ。"""
    try:
        if dst.exists():
            return None
    except Exception:
        pass
    return dst


def count_media_recursive(src_dir: Path) -> int:
    n = 0
    for root, _dirs, files in os.walk(src_dir):
        rp = Path(root)
        for fn in files:
            if fn in JUNK_FILES or fn.startswith("~$"):
                continue
            pth = rp / fn
            try:
                if pth.is_file() and is_media_file(pth):
                    n += 1
            except Exception:
                pass
    return n


def move_media_recursive_skip_dupe(src_dir: Path, dst_dir: Path, *, delete_empty_src: bool = False):
    dst_dir.mkdir(parents=True, exist_ok=True)

    moved_count = 0
    skip_count = 0
    fail_count = 0

    for root, _dirs, files in os.walk(src_dir):
        rootp = Path(root)
        for fn in files:
            if fn in JUNK_FILES or fn.startswith("~$"):
                continue
            sp = rootp / fn
            try:
                if not sp.is_file() or not is_media_file(sp):
                    continue
            except Exception:
                continue

            dp = ensure_unique_path_or_skip(dst_dir / sp.name)
            if dp is None:
                p(f"  SKIP(同名あり): {sp.name}")
                skip_count += 1
                continue

            try:
                clear_readonly(sp)
                shutil.move(str(sp), str(dp))
                moved_count += 1
                p(f"  MOVE: {sp} -> {dp}")
            except Exception as e:
                fail_count += 1
                p(f"  [WARN] 移動失敗: {sp} ({e})")

    if delete_empty_src:
        for root, dirs, files in os.walk(src_dir, topdown=False):
            if dirs or files:
                continue
            try:
                Path(root).rmdir()
            except Exception:
                pass

    p(f"[MOVE SUMMARY] moved={moved_count}, skipped={skip_count}, failed={fail_count}")


def _read_text_autoenc(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    for enc in ("utf-8-sig", "cp932", "utf-8"):
        try:
            return path.read_text(encoding=enc, errors="strict")
        except Exception:
            continue
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _read_csv_master(master_path: Path) -> Dict[str, str]:
    text = _read_text_autoenc(master_path)
    if not text:
        return {}

    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return {}

    header = [h.strip() for h in rows[0]]

    def find_col_exact(candidates: List[str]) -> Optional[int]:
        for cand in candidates:
            for i, h in enumerate(header):
                if h == cand:
                    return i
        return None

    def find_col_contains(candidates: List[str]) -> Optional[int]:
        for cand in candidates:
            for i, h in enumerate(header):
                if cand in h:
                    return i
        return None

    # 「工事番号＋枝番」を最優先で見る。
    # 工事一覧表.csv は「工事番号」(親のみ)と「工事番号＋枝番」の両方を持つ。
    # 親だけを読むと枝番が同一キーに潰れ、(a)親の名称が最後の枝番の名称で
    # 上書きされる (b)枝番フォルダがマスタ未登録扱いで黙って飛ばされる、
    # という2つの不具合が起きる。ld_sort.py と同じ列を見て件数を一致させる。
    code_i = find_col_exact(["工事番号＋枝番", "工事番号+枝番", "工事番号", "プロジェクトコード", "工番", "コード"])
    if code_i is None:
        code_i = find_col_contains(["工事番号＋枝番", "工事番号+枝番", "工事番号", "プロジェクトコード", "工番", "コード"])

    name_i = find_col_exact(["工事名称", "工事件名", "プロジェクト名", "工事名", "案件名", "名称", "名"])
    if name_i is None:
        name_i = find_col_contains(["工事名称", "工事件名", "プロジェクト名", "工事名", "案件名", "名称"])

    if code_i is None or name_i is None:
        code_i, name_i = 0, 1

    out: Dict[str, str] = {}
    for r in rows[1:]:
        if len(r) <= max(code_i, name_i):
            continue
        code_raw = (r[code_i] or "").strip()
        name = (r[name_i] or "").strip()
        key = normalize_workno(code_raw)
        if not key:
            # サフィックスなし形式 (例: "00000001") → "-00" を補完
            m2 = re.match(r'^([A-Za-z]*)(\d+)$', code_raw)
            if m2:
                prefix = m2.group(1).upper()
                digits = m2.group(2)
                left = digits.lstrip("0") or "0"
                key = f"{prefix}{left}-00" if prefix else f"{left}-00"
        if not key or not name:
            continue
        out[key] = name
    return out


def _pick_master_file(gd_root: Path) -> Optional[Path]:
    # GDX卒業(2026-07-24): 恒久置き場 <91ルート>/_masters を優先し、
    # 旧置き場(_GDExtraction = gd_root)にフォールバックする。
    for base in (gd_root.parent / "_masters", gd_root):
        for fn in ("工事一覧表.csv", "CSV工番マスタ.csv", "master.csv"):
            pth = base / fn
            if pth.exists():
                return pth
    return None


def _extract_workno_suffix(name: str) -> tuple[Optional[str], str]:
    raw = str(name).strip().lstrip("#")
    m = re.match(r"^([A-Za-z]*\d+[-_]\d{2})(.*)$", raw)
    if not m:
        return None, ""
    workno = normalize_workno(m.group(1))
    suffix = (m.group(2) or "").strip()
    suffix = suffix.lstrip(" _-")
    return workno, suffix


def _build_master_named_stem(workno: str, master_name: str, suffix: str = "") -> str:
    stem = f"{workno}_{normalize_master_name(master_name)}"
    cleaned_suffix = str(suffix).strip().lstrip(" _-")
    if cleaned_suffix:
        stem = sanitize_name(f"{stem}_{cleaned_suffix}")
    else:
        stem = sanitize_name(stem)
    return stem


def rename_gdextraction_folders_to_master(gd_root: Path, master: Dict[str, str]) -> List[tuple[str, str]]:
    """GDExtraction 直下の工番フォルダを工番マスタ名へリネームする。"""
    if not gd_root.is_dir():
        p(f"[WARN] GDExtraction root not found: {gd_root}")
        return []

    renamed_pairs: List[tuple[str, str]] = []
    skipped = 0

    folders = [pth for pth in gd_root.iterdir() if pth.is_dir()]
    for src in sorted(folders, key=lambda pth: pth.name.lower()):
        workno = get_workno_from_name(src.name)
        if not workno:
            continue

        master_name = master.get(workno)
        if not master_name:
            continue

        desired_name = sanitize_name(f"{workno}_{normalize_master_name(master_name)}")
        if src.name == desired_name:
            continue

        desired_path = src.parent / desired_name
        if desired_path.exists():
            skipped += 1
            p(f"[WARN] rename skip (GDX) (target exists): {src.name} -> {desired_name}")
            continue

        try:
            old_name = src.name
            src.rename(desired_path)
            renamed_pairs.append((old_name, desired_name))
            p(f"[RENAME:GDX] {old_name} -> {desired_name}")
        except Exception as e:
            skipped += 1
            p(f"[WARN] rename failed (GDX): {src} ({e})")

    p(f"[RENAME SUMMARY:GDX] renamed={len(renamed_pairs)}, skipped={skipped}")
    return renamed_pairs


def _find_workno_from_ancestors(path: Path, root: Path) -> Optional[str]:
    current = path.parent
    root_resolved = root.resolve()
    while True:
        try:
            current_resolved = current.resolve()
        except Exception:
            current_resolved = current
        if current_resolved == root_resolved:
            return None
        workno = get_workno_from_name(current.name)
        if workno:
            return workno
        if current.parent == current:
            return None
        current = current.parent


def rename_gdextraction_files_to_master(gd_root: Path, master: Dict[str, str]):
    """GDExtraction 配下の工番付きファイル名を工番マスタ名へ寄せる。"""
    if not gd_root.is_dir():
        p(f"[WARN] GDExtraction root not found for file rename: {gd_root}")
        return

    renamed = 0
    skipped = 0

    for root, _dirs, files in os.walk(gd_root):
        root_path = Path(root)
        for fn in files:
            if fn in MASTER_CSV_FILES or fn in JUNK_FILES or fn.startswith("~$"):
                continue

            src = root_path / fn
            workno, suffix = _extract_workno_suffix(src.stem)
            if not workno:
                workno = _find_workno_from_ancestors(src, gd_root)
                suffix = src.stem
            if not workno:
                continue

            master_name = master.get(workno)
            if not master_name:
                continue

            desired_stem = _build_master_named_stem(workno, master_name, suffix)
            desired_name = sanitize_name(f"{desired_stem}{src.suffix}")
            if src.name == desired_name:
                continue

            desired_path = src.with_name(desired_name)
            if desired_path.exists():
                skipped += 1
                p(f"[WARN] rename skip (GDX FILE) (target exists): {src.name} -> {desired_name}")
                continue

            try:
                src.rename(desired_path)
                renamed += 1
                p(f"[RENAME:GDX FILE] {src.name} -> {desired_name}")
            except Exception as e:
                skipped += 1
                p(f"[WARN] rename failed (GDX FILE): {src} ({e})")

    p(f"[RENAME SUMMARY:GDX FILE] renamed={renamed}, skipped={skipped}")


def cleanup_drive_gdx_names_after_local_rename(service, drive_parent_id: Optional[str], renamed_pairs: List[tuple[str, str]]):
    if not service or not drive_parent_id or not renamed_pairs:
        return

    deleted = 0
    for old_name, _new_name in renamed_pairs:
        try:
            deleted += drive_delete_named_child_folders(service, drive_parent_id, old_name)
        except Exception as e:
            p(f"[WARN] Drive old-name cleanup failed: {old_name} ({e})")
    p(f"[DRIVE CLEANUP:GDX OLD NAMES] deleted={deleted}")


def collect_gdextraction_folder_rename_plan(gd_root: Path, master: Dict[str, str]) -> List[Dict[str, str]]:
    plans: List[Dict[str, str]] = []
    if not gd_root.is_dir():
        return plans

    folders = [pth for pth in gd_root.iterdir() if pth.is_dir()]
    for src in sorted(folders, key=lambda pth: pth.name.lower()):
        workno = get_workno_from_name(src.name)
        if not workno:
            continue

        master_name = master.get(workno)
        if not master_name:
            continue

        desired_name = sanitize_name(f"{workno}_{normalize_master_name(master_name)}")
        if src.name == desired_name:
            continue

        desired_path = src.parent / desired_name
        status = "rename"
        reason = ""
        if desired_path.exists():
            status = "conflict"
            reason = "target exists"

        plans.append({
            "type": "folder",
            "status": status,
            "old": src.name,
            "new": desired_name,
            "workno": workno,
            "reason": reason,
        })
    return plans


def collect_gdextraction_file_rename_plan(gd_root: Path, master: Dict[str, str]) -> List[Dict[str, str]]:
    plans: List[Dict[str, str]] = []
    if not gd_root.is_dir():
        return plans

    for root, _dirs, files in os.walk(gd_root):
        root_path = Path(root)
        for fn in files:
            if fn in MASTER_CSV_FILES or fn in JUNK_FILES or fn.startswith("~$"):
                continue

            src = root_path / fn
            workno, suffix = _extract_workno_suffix(src.stem)
            if not workno:
                workno = _find_workno_from_ancestors(src, gd_root)
                suffix = src.stem
            if not workno:
                continue

            master_name = master.get(workno)
            if not master_name:
                continue

            desired_stem = _build_master_named_stem(workno, master_name, suffix)
            desired_name = sanitize_name(f"{desired_stem}{src.suffix}")
            if src.name == desired_name:
                continue

            desired_path = src.with_name(desired_name)
            status = "rename"
            reason = ""
            if desired_path.exists():
                status = "conflict"
                reason = "target exists"

            plans.append({
                "type": "file",
                "status": status,
                "old": str(src.relative_to(gd_root)),
                "new": str(desired_path.relative_to(gd_root)),
                "workno": workno,
                "reason": reason,
            })
    return plans


def write_gdextraction_master_preview_report(gd_root: Path, output_path: Path) -> Optional[Path]:
    master_file = _pick_master_file(gd_root)
    if not master_file:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            f"[ERROR] GDX直下にマスタCSVが見つかりません: {gd_root}\n",
            encoding="utf-8",
        )
        return output_path

    master = _read_csv_master(master_file)
    folder_plans = collect_gdextraction_folder_rename_plan(gd_root, master)
    file_plans = collect_gdextraction_file_rename_plan(gd_root, master)

    lines: List[str] = []
    lines.append(f"GDExtraction master rename preview")
    lines.append(f"gd_root: {gd_root}")
    lines.append(f"master_file: {master_file}")
    lines.append(f"master_count: {len(master)}")
    lines.append("")
    lines.append(f"folder_plan_count: {len(folder_plans)}")
    for item in folder_plans:
        suffix = f" | reason={item['reason']}" if item["reason"] else ""
        lines.append(f"[{item['status'].upper()}][FOLDER] {item['old']} -> {item['new']} | workno={item['workno']}{suffix}")

    lines.append("")
    lines.append(f"file_plan_count: {len(file_plans)}")
    for item in file_plans:
        suffix = f" | reason={item['reason']}" if item["reason"] else ""
        lines.append(f"[{item['status'].upper()}][FILE] {item['old']} -> {item['new']} | workno={item['workno']}{suffix}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def apply_gdextraction_master_renames(
    gd_root: Path,
    *,
    sync_service=None,
    sync_drive_parent_id: Optional[str] = None,
) -> Dict[str, int]:
    master_file = _pick_master_file(gd_root)
    if not master_file:
        p(f"[ERROR] GDX直下にマスタCSVが見つかりません: {gd_root}")
        return {"master_count": 0, "folder_renamed": 0, "file_renamed": 0}

    master = _read_csv_master(master_file)
    p(f"[MASTER] 使用: {master_file} (件数={len(master)})")

    renamed_pairs = rename_gdextraction_folders_to_master(gd_root, master)
    rename_gdextraction_files_to_master(gd_root, master)
    cleanup_drive_gdx_names_after_local_rename(sync_service, sync_drive_parent_id, renamed_pairs)
    if sync_service and sync_drive_parent_id:
        sync_gdx_tree_checkpoint(sync_service, str(gd_root), sync_drive_parent_id, "GDExtractionマスタ名整合後")

    file_plans_after = collect_gdextraction_file_rename_plan(gd_root, master)
    return {
        "master_count": len(master),
        "folder_renamed": len(renamed_pairs),
        "file_remaining_plan": len(file_plans_after),
    }


def rename_A_folders_to_master(target_root: Path, master: Dict[str, str], *, label: str = ""):
    """target_root 直下の A フォルダ名をマスタ名に揃えてリネーム。"""
    if not target_root.is_dir():
        p(f"[WARN] target root not found{f' ({label})' if label else ''}: {target_root}")
        return

    renamed = 0
    skipped = 0

    folders = [p for p in target_root.iterdir() if p.is_dir()]
    for a in sorted(folders, key=lambda pth: pth.name.lower()):
        workno = get_workno_from_name(a.name)
        if not workno:
            continue
        new_name_part = master.get(workno)
        if not new_name_part:
            continue

        desired_name = sanitize_name(f"{workno}_{normalize_master_name(new_name_part)}")
        desired_path = a.parent / desired_name

        if a.name == desired_name:
            continue

        if desired_path.exists():
            skipped += 1
            p(f"[WARN] rename skip{f' ({label})' if label else ''} (target exists): {a.name} -> {desired_name}")
            continue

        try:
            old_name = a.name
            a.rename(desired_path)
            renamed += 1
            p(f"[RENAME{':' + label if label else ''}] {old_name} -> {desired_name}")
        except Exception as e:
            skipped += 1
            p(f"[WARN] rename failed{f' ({label})' if label else ''}: {a} ({e})")

    p(f"[RENAME SUMMARY{':' + label if label else ''}] renamed={renamed}, skipped={skipped}")


def rename_92_files_to_master(target_root: Path, master: Dict[str, str]):
    """92_PO_LIST 直下の Excel ファイル名を工番マスタ名に統一する。"""
    if not target_root.is_dir():
        p(f"[WARN] target root not found (92): {target_root}")
        return

    renamed = 0
    skipped = 0

    files = [p for p in target_root.iterdir() if p.is_file() and p.suffix.lower() in PO_LIST_EXT]
    for src in sorted(files, key=lambda pth: pth.name.lower()):
        workno = get_workno_from_name(src.stem)
        if not workno:
            continue

        new_name_part = master.get(workno)
        if not new_name_part:
            continue

        desired_name = sanitize_name(f"{workno}_{normalize_master_name(new_name_part)}_PO_LIST{src.suffix}")
        desired_path = src.parent / desired_name

        if src.name == desired_name:
            continue

        if desired_path.exists():
            skipped += 1
            p(f"[WARN] rename skip (92) (target exists): {src.name} -> {desired_name}")
            continue

        try:
            old_name = src.name
            src.rename(desired_path)
            renamed += 1
            p(f"[RENAME:92] {old_name} -> {desired_name}")
        except Exception as e:
            skipped += 1
            p(f"[WARN] rename failed (92): {src} ({e})")

    p(f"[RENAME SUMMARY:92] renamed={renamed}, skipped={skipped}")


def rename_271_shirei_files_to_master(target_271_root: Path, master: Dict[str, str]):
    """271_修理工事指令書 直下のファイルを「工番_工事名_指令書」形式にリネームする。

    対応する入力形式:
      - 工番のみ          : 4605-00.pdf
      - 工番_任意文字列   : 4605-00_第一金属工業.pdf
      - 指令書_工番_任意  : 指令書_4605-00_第一金属工業.pdf
    複数ファイル同一工番 : _1, _2 のサフィックスを追加
    いずれも → {workno}_{工事名}_指令書(_N).ext
    """
    if not target_271_root.is_dir():
        p(f"[WARN] 271 root not found: {target_271_root}")
        return

    # ステップ1: 複数の _指令書_ を削除（クリーンアップ）
    _cleanup_duplicate_shirei_suffix_271(target_271_root)
    
    # ステップ2: 正規リネーム（工番グループ化とサフィックス処理対応）
    _rename_271_with_suffix(target_271_root, master)


def _cleanup_duplicate_shirei_suffix_271(target_271_root: Path):
    """_指令書_1_指令書_1_指令書_1 のような重複を削除。"""
    cleaned = 0
    
    for src in sorted(target_271_root.iterdir(), key=lambda pth: pth.name.lower()):
        if not src.is_file():
            continue
        if src.name.startswith("~$"):
            continue
        
        stem = src.stem
        
        # 複数の _指令書_ が含まれるか
        shirei_count = stem.count("_指令書_")
        if shirei_count >= 2:
            # すべての _指令書_N パターンを削除
            cleaned_stem = re.sub(r"(_指令書_\d+)+$", "", stem)
            cleaned_name = f"{cleaned_stem}_指令書{src.suffix}"
            cleaned_path = src.parent / cleaned_name
            
            if src.name != cleaned_name and not cleaned_path.exists():
                try:
                    src.rename(cleaned_path)
                    cleaned += 1
                    p(f"[CLEANUP:271] {src.name} -> {cleaned_name}")
                except Exception as e:
                    p(f"[WARN] cleanup failed (271): {src.name}: {e}")
    
    if cleaned > 0:
        p(f"[CLEANUP SUMMARY:271] cleaned={cleaned}")


def _rename_271_with_suffix(target_271_root: Path, master: Dict[str, str]):
    """271ファイルを工番でグループ化し、同一工番の複数ファイルに _1, _2 のサフィックスを付ける。"""
    
    _SHIREI_PREFIX = re.compile(r"^指令書[_\s]+")
    
    # 第1パス: 工番を抽出してグループ化
    file_info = []  # (src_path, workno, base_name)
    workno_groups = {}
    
    for src in sorted(target_271_root.iterdir(), key=lambda pth: pth.name.lower()):
        if not src.is_file():
            continue
        if src.name.startswith("~$"):
            continue
        
        stem = src.stem
        
        # プレフィックス除去
        if stem.startswith("指令書"):
            stem = _SHIREI_PREFIX.sub("", stem)
        
        # 工番抽出: \d+-\d+ パターン（2桁-2桁も対応）
        workno = None
        
        # パターン1: 既に「_指令書」で終わっている
        if stem.endswith("_指令書"):
            m = re.match(r"^(\d+\-\d+)_(.+)_指令書$", stem)
            if m:
                workno = m.group(1)
                desc = m.group(2)
                base_name = sanitize_name(f"{workno}_{desc}_指令書")
                file_info.append((src, workno, base_name))
            continue
        
        # パターン2: 工番_説明 形式
        m = re.match(r"^(\d+\-\d+)_(.+)$", stem)
        if m:
            workno = m.group(1)
            desc = m.group(2)
            base_name = sanitize_name(f"{workno}_{desc}_指令書")
            file_info.append((src, workno, base_name))
            continue
        
        # パターン3: 工番のみ
        m = re.match(r"^(\d+\-\d+)$", stem)
        if m:
            workno = m.group(1)
            # マスタから工事名を取得
            if workno in master:
                desc = master[workno]
                base_name = sanitize_name(f"{workno}_{desc}_指令書")
                file_info.append((src, workno, base_name))
            else:
                # マスタにない場合は工番のみで指令書を追加
                base_name = sanitize_name(f"{workno}_指令書")
                file_info.append((src, workno, base_name))
            continue
    
    # グループ化
    for src, workno, base_name in file_info:
        if workno not in workno_groups:
            workno_groups[workno] = []
        workno_groups[workno].append((src, base_name))
    
    # 第2パス: リネーム（複数ファイル時はサフィックス付け）
    renamed = 0
    skipped = 0
    
    for workno, files_in_group in sorted(workno_groups.items()):
        has_suffix = len(files_in_group) > 1
        
        if has_suffix:
            # 最初のファイルから説明部分を抽出して共有
            first_src, first_base_name = files_in_group[0]
            m = re.match(r"^\d+\-\d+_(.+)_指令書$", first_base_name)
            if m:
                common_desc = m.group(1)
                for idx, (src, base_name) in enumerate(files_in_group, 1):
                    desired_name = f"{workno}_{common_desc}_指令書_{idx}{src.suffix}"
                    desired_path = src.parent / desired_name
                    
                    if src.name == desired_name:
                        continue
                    
                    if desired_path.exists():
                        skipped += 1
                        p(f"[WARN] rename skip (271) (target exists): {src.name} -> {desired_name}")
                        continue
                    
                    try:
                        src.rename(desired_path)
                        renamed += 1
                        p(f"[RENAME:271] {src.name} -> {desired_name}")
                    except Exception as e:
                        skipped += 1
                        p(f"[WARN] rename failed (271): {src.name}: {e}")
        else:
            # 単一ファイル
            for src, base_name in files_in_group:
                desired_name = f"{base_name}{src.suffix}"
                desired_path = src.parent / desired_name
                
                if src.name == desired_name:
                    continue
                
                if desired_path.exists():
                    skipped += 1
                    p(f"[WARN] rename skip (271) (target exists): {src.name} -> {desired_name}")
                    continue
                
                try:
                    src.rename(desired_path)
                    renamed += 1
                    p(f"[RENAME:271] {src.name} -> {desired_name}")
                except Exception as e:
                    skipped += 1
                    p(f"[WARN] rename failed (271): {src.name}: {e}")
    
    p(f"[RENAME SUMMARY:271] renamed={renamed}, skipped={skipped}")



def rename_existing_paths_english_spaces(target_root: Path, *, label: str = ""):
    """既存のフォルダ名・ファイル名に対して英単語間 `_` を半角スペースへ戻す。"""
    if not target_root.is_dir():
        p(f"[WARN] target root not found for english-space normalization{f' ({label})' if label else ''}: {target_root}")
        return

    renamed = 0
    skipped = 0

    for root, dirs, files in os.walk(target_root, topdown=False):
        root_path = Path(root)

        for fn in files:
            src = root_path / fn
            desired_name = normalize_existing_path_name(src.name, is_dir=False)
            if desired_name == src.name:
                continue

            desired_path = src.with_name(desired_name)
            if desired_path.exists():
                skipped += 1
                p(f"[WARN] english-space skip{f' ({label})' if label else ''} (target exists): {src.name} -> {desired_name}")
                continue

            try:
                src.rename(desired_path)
                renamed += 1
                p(f"[ENSPACE{':' + label if label else ''}] {src.name} -> {desired_name}")
            except Exception as e:
                skipped += 1
                p(f"[WARN] english-space failed{f' ({label})' if label else ''}: {src} ({e})")

        for dn in dirs:
            src = root_path / dn
            desired_name = normalize_existing_path_name(src.name, is_dir=True)
            if desired_name == src.name:
                continue

            desired_path = src.with_name(desired_name)
            if desired_path.exists():
                skipped += 1
                p(f"[WARN] english-space skip{f' ({label})' if label else ''} (target exists): {src.name} -> {desired_name}")
                continue

            try:
                src.rename(desired_path)
                renamed += 1
                p(f"[ENSPACE{':' + label if label else ''}] {src.name} -> {desired_name}")
            except Exception as e:
                skipped += 1
                p(f"[WARN] english-space failed{f' ({label})' if label else ''}: {src} ({e})")

    p(f"[ENSPACE SUMMARY{':' + label if label else ''}] renamed={renamed}, skipped={skipped}")


def find_existing_A_folder(target_root: Path, workno: str) -> Optional[Path]:
    if not target_root.is_dir():
        return None
    candidates: List[Path] = []
    try:
        for a in target_root.iterdir():
            if not a.is_dir():
                continue
            w = get_workno_from_name(a.name)
            if w == workno:
                candidates.append(a)
    except Exception:
        return None
    if not candidates:
        return None
    return sorted(candidates, key=lambda pth: pth.name.lower())[0]


def create_A_folder_from_master(target_root: Path, workno: str, master: Dict[str, str]) -> Optional[Path]:
    name = master.get(workno)
    if not name:
        return None
    a_folder_name = sanitize_name(f"{workno}_{normalize_master_name(name)}")
    a_path = target_root / a_folder_name
    try:
        a_path.mkdir(parents=True, exist_ok=True)
        p(f"[CREATE A] {a_path}")
        return a_path
    except Exception as e:
        p(f"[WARN] Aフォルダ作成失敗: {a_path} ({e})")
        return None


def ensure_B4_under_A(a_folder: Path, workno: str) -> Path:
    b4 = a_folder / f"{workno}_B4整理前写真・動画"
    b4.mkdir(parents=True, exist_ok=True)
    return b4


def move_gdextraction_to_91_B4_with_master(
    gd_root: Path,
    target_91_root: Path,
    target_252_root: Optional[Path] = None,
    target_92_root: Optional[Path] = None,
    target_9781_root: Optional[Path] = None,
    *,
    delete_empty_src: bool = False,
    sync_service=None,
    sync_drive_parent_id: Optional[str] = None,
    sync_during_process: bool = True,
):
    if not gd_root.is_dir():
        p(f"[ERROR] GDExtraction が見つかりません: {gd_root}")
        return
    if not target_91_root.is_dir():
        p(f"[ERROR] 91 root が見つかりません: {target_91_root}")
        return

    master_file = _pick_master_file(gd_root)
    master: Dict[str, str] = {}
    if master_file:
        master = _read_csv_master(master_file)
        p(f"[MASTER] 使用: {master_file} (件数={len(master)})")
    else:
        p("[MASTER] GDX直下にマスタCSVが見つかりません（工事一覧表.csv 等）。")

    if master:
        p("[STEP] GDExtraction フォルダ名のマスタ整合開始")
        renamed_gdx_pairs = rename_gdextraction_folders_to_master(gd_root, master)
        p("[STEP] GDExtraction ファイル名のマスタ整合開始")
        rename_gdextraction_files_to_master(gd_root, master)
        cleanup_drive_gdx_names_after_local_rename(sync_service, sync_drive_parent_id, renamed_gdx_pairs)
        if target_252_root is not None:
            p("[STEP] 252 Aフォルダ名のマスタ整合開始")
            rename_A_folders_to_master(target_252_root, master, label="252")
            rename_existing_paths_english_spaces(target_252_root, label="252")
        if target_92_root is not None:
            p("[STEP] 92 PO_LISTファイル名のマスタ整合開始")
            rename_92_files_to_master(target_92_root, master)
            rename_existing_paths_english_spaces(target_92_root, label="92")
        if target_9781_root is not None:
            p("[STEP] 9781 Aフォルダ名のマスタ整合開始")
            rename_A_folders_to_master(target_9781_root, master, label="9781")
            rename_existing_paths_english_spaces(target_9781_root, label="9781")
        p("[STEP] 91 Aフォルダ名のマスタ整合開始")
        rename_A_folders_to_master(target_91_root, master, label="91")
        rename_existing_paths_english_spaces(target_91_root, label="91")

    src_folders = [p for p in gd_root.iterdir() if p.is_dir()]
    if not src_folders:
        p("[INFO] GDExtraction に対象フォルダがありません。")
        return

    p(f"[STEP] GDX対象フォルダ数: {len(src_folders)}")

    processed = 0
    skipped = 0

    for idx, src in enumerate(sorted(src_folders, key=lambda pth: pth.name.lower()), 1):
        p(f"[GDX {idx}/{len(src_folders)}] 処理開始: {src.name}")
        workno = get_workno_from_name(src.name)
        if not workno:
            skipped += 1
            p(f"[GDX {idx}/{len(src_folders)}] 工番取得不可のためスキップ: {src.name}")
            continue

        media_count = count_media_recursive(src)
        p(f"[GDX {idx}/{len(src_folders)}] media_count={media_count} / workno={workno}")

        if media_count == 0:
            skipped += 1
            if sync_during_process and sync_service and sync_drive_parent_id:
                sync_gdx_tree_checkpoint(sync_service, str(gd_root), sync_drive_parent_id, f"GDX空フォルダ確認後 {src.name}")
            continue

        a_folder = find_existing_A_folder(target_91_root, workno)
        created = False
        if a_folder is None:
            if not master:
                skipped += 1
                p(f"[WARN] 91側にA無し＆マスタ無しなのでスキップ: 工番={workno} | src={src.name}")
                if sync_during_process and sync_service and sync_drive_parent_id:
                    sync_gdx_tree_checkpoint(sync_service, str(gd_root), sync_drive_parent_id, f"91A未作成スキップ後 {src.name}")
                continue
            a_folder = create_A_folder_from_master(target_91_root, workno, master)
            if a_folder is None:
                skipped += 1
                p(f"[WARN] 91側にA無し＆マスタに無いのでスキップ: 工番={workno} | src={src.name}")
                if sync_during_process and sync_service and sync_drive_parent_id:
                    sync_gdx_tree_checkpoint(sync_service, str(gd_root), sync_drive_parent_id, f"マスタ未作成スキップ後 {src.name}")
                continue
            created = True

        b4 = ensure_B4_under_A(a_folder, workno)

        p("=== GDX -> 91(B4) ===")
        p(f"src    : {src} (media={media_count})")
        p(f"A      : {a_folder} {'[NEW]' if created else '[EXIST]'}")
        p(f"dst(B4): {b4}")

        move_media_recursive_skip_dupe(src, b4, delete_empty_src=delete_empty_src)
        processed += 1

        if sync_during_process and sync_service and sync_drive_parent_id:
            sync_gdx_tree_checkpoint(sync_service, str(gd_root), sync_drive_parent_id, f"GDX->91処理後 {src.name}")

    p(f"[STEP SUMMARY] GDX processed={processed}, skipped={skipped}")
