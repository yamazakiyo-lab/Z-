"""91フォルダ整理（メディア振り分け／リネーム／空フォルダ削除）。"""

import os
import re
import shutil
import stat
import subprocess
import time
import uuid
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except Exception:
    register_heif_opener = None

from PIL import Image, ImageOps

from .master import get_workno_from_name
from .utils import is_windows, normalize_existing_path_name, to_long_path


TMP_RENAME_PATTERN = re.compile(r"^__TMP__\d+__\d{6}__(.+)$")


@dataclass(frozen=True)
class Config91:
    base_dir: Path = Path(r"Z:\takachiho")
    target_91_root: Path = Path(r"Z:\takachiho\2to9_業務別フォルダ\91_工番別実績写真・動画")

    photo_ext: Set[str] = field(default_factory=lambda: {".jpg", ".jpeg", ".png", ".heic", ".heif"})
    video_ext: Set[str] = field(default_factory=lambda: {".mp4", ".mov", ".avi", ".mts", ".m2ts"})
    max_kb: int = 1000
    junk_files: Set[str] = field(default_factory=lambda: {"Thumbs.db", "desktop.ini", ".DS_Store"})

    use_long_path_ops: bool = True
    enable_copy_fallback: bool = True
    delete_empty_phase_folders: bool = True

    progress_interval_sec: float = 0.4
    show_progress_bar: bool = True
    dry_run: bool = False


def get_media_capture_datetime(path: Path, cfg: Config91, log=None) -> datetime:
    ext = path.suffix.lower()

    if ext in {".jpg", ".jpeg", ".png", ".heic", ".heif"}:
        try:
            with Image.open(path) as img:
                exif = None
                try:
                    exif = img.getexif()
                except Exception:
                    exif = None
                if exif:
                    for tag in (36867, 36868, 306):
                        v = exif.get(tag)
                        if v:
                            s = str(v).strip().replace("-", ":")
                            if len(s) >= 19:
                                s = s[:19]
                            try:
                                return datetime.strptime(s, "%Y:%m:%d %H:%M:%S")
                            except Exception:
                                pass
        except Exception:
            pass

    if ext in cfg.video_ext:
        try:
            cmd = [
                "ffprobe", "-v", "error",
                "-show_entries", "format_tags=creation_time",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ]
            p_run = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if p_run.returncode == 0:
                s = (p_run.stdout or "").strip().replace("Z", "")
                if "." in s:
                    s = s.split(".", 1)[0]
                if "T" in s:
                    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
        except FileNotFoundError:
            if log:
                log.warn("ffprobe が見つからないため、動画日時は mtime を使用します。")
        except Exception:
            pass

    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except Exception:
        return datetime.now()


class Logger:
    def __init__(self, log_path: Path):
        self.log_path = log_path
        self._fp = None
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._fp = open(log_path, "a", encoding="utf-8", errors="replace", newline="\n")
        except Exception:
            self._fp = None

    def _ts(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def info(self, msg: str):
        ts = self._ts()
        print(f"[{ts}][INFO] {msg}", flush=True)
        if self._fp:
            try:
                self._fp.write(f"[{ts}][INFO] {msg}\n")
                self._fp.flush()
            except Exception:
                pass

    def warn(self, msg: str):
        ts = self._ts()
        print(f"[{ts}][WARN] {msg}", flush=True)
        if self._fp:
            try:
                self._fp.write(f"[{ts}][WARN] {msg}\n")
                self._fp.flush()
            except Exception:
                pass

    def close(self):
        try:
            if self._fp:
                self._fp.close()
        except Exception:
            pass


class FileOps:
    def __init__(self, cfg: Config91, log: Logger):
        self.cfg = cfg
        self.log = log

    def is_media(self, path: Path) -> bool:
        ext = path.suffix.lower()
        return ext in self.cfg.photo_ext or ext in self.cfg.video_ext

    def is_junk(self, path: Path) -> bool:
        """
        fix_b4_scan.py準拠: Thumbsを含む/拡張子.tmp/定義済みJUNK_FILES
        """
        name = path.name
        if name in self.cfg.junk_files:
            return True
        if "Thumbs" in name:
            return True
        if name.lower().endswith(".tmp"):
            return True
        return False

    def _exists(self, pth: Path) -> bool:
        try:
            if self.cfg.use_long_path_ops and is_windows():
                return os.path.exists(to_long_path(pth))
            return pth.exists()
        except Exception:
            return True

    def ensure_unique_path(self, dst: Path) -> Path:
        if not self._exists(dst):
            return dst
        stem, ext = dst.stem, dst.suffix
        for n in range(2, 10000):
            cand = dst.parent / f"{stem}_{n}{ext}"
            if not self._exists(cand):
                return cand
        return dst

    def safe_move(self, src: Path, dst_dir: Path) -> Optional[Path]:
        try:
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = self.ensure_unique_path(dst_dir / src.name)
            if self.cfg.dry_run:
                self.log.info(f"[DRY] MOVE {src} -> {dst}")
                return dst
            try:
                if src.is_file():
                    os.chmod(str(src), stat.S_IWRITE)
            except Exception:
                pass
            shutil.move(str(src), str(dst))
            return dst
        except Exception as e:
            self.log.warn(f"move失敗: {src} ({e})")
            return None

    def safe_rename(self, src: Path, dst: Path) -> Optional[Path]:
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst = self.ensure_unique_path(dst)
            if self.cfg.dry_run:
                self.log.info(f"[DRY] RENAME {src} -> {dst}")
                return dst
            src.rename(dst)
            return dst
        except Exception as e:
            self.log.warn(f"rename失敗: {src} ({e})")
            return None


class ImageCompressor:
    def __init__(self, cfg: Config91, ops: FileOps, log: Logger):
        self.cfg = cfg
        self.ops = ops
        self.log = log

    def compress_or_convert(self, path: Path) -> Path:
        if self.cfg.dry_run:
            return path

        ext = path.suffix.lower()
        if ext not in self.cfg.photo_ext:
            return path

        if ext in {".heic", ".heif"} and register_heif_opener is None:
            self.log.warn(f"[SKIP] HEIC/HEIF未対応: {path}")
            return path

        dst_path = path
        if ext in {".png", ".heic", ".heif"}:
            dst_path = self.ops.ensure_unique_path(path.with_suffix(".jpg"))

        try:
            if ext in {".jpg", ".jpeg"} and path.stat().st_size / 1024 <= self.cfg.max_kb:
                return path
        except Exception:
            pass

        tmp = dst_path.parent / f"~cmp_{uuid.uuid4().hex}.jpg"
        try:
            with Image.open(path) as img:
                img = ImageOps.exif_transpose(img)
                exif_bytes = img.info.get("exif")

                if img.mode in ("RGBA", "LA"):
                    bg = Image.new("RGB", img.size, (255, 255, 255))
                    bg.paste(img, mask=img.split()[-1])
                    img = bg
                else:
                    img = img.convert("RGB")

                quality = 95
                while quality >= 30:
                    kw = {"quality": quality, "optimize": True}
                    if exif_bytes:
                        kw["exif"] = exif_bytes
                    img.save(tmp, "JPEG", **kw)
                    try:
                        if tmp.stat().st_size / 1024 <= self.cfg.max_kb:
                            break
                    except Exception:
                        break
                    quality -= 5

            os.replace(str(tmp), str(dst_path))
            if dst_path != path:
                try:
                    path.unlink()
                except Exception:
                    pass
            return dst_path

        except Exception as e:
            self.log.warn(f"圧縮/変換失敗: {path} ({e})")
            return path
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass


class Organizer91:
    """91直下の A フォルダを処理し、A直下の B1..B4 に振り分ける。"""

    def __init__(self, cfg: Config91, log: Logger):
        self.cfg = cfg
        self.log = log
        self.ops = FileOps(cfg, log)
        self.compressor = ImageCompressor(cfg, self.ops, log)

    def _classify_folder_name_to_B(self, name: str) -> Optional[str]:
        n = unicodedata.normalize("NFKC", name)
        if any(k in n for k in ("引取", "入庫", "入荷", "受入", "入庫時", "着手前")):
            return "B1"
        if any(k in n for k in ("整備", "加工", "切削", "完成", "整備中", "着手中")):
            return "B2"
        if any(k in n for k in ("据付", "納入", "出荷", "引渡", "搬入", "出荷時")):
            return "B3"
        if any(k in n for k in ("整理前", "整理中", "整理")):
            return "B4"
        return None

    def _ensure_B_folders(self, a_folder: Path, workno: str) -> Dict[str, Path]:
        b1 = a_folder / f"{workno}_B1着手前写真・動画"
        b2 = a_folder / f"{workno}_B2着手中写真・動画"
        b3 = a_folder / f"{workno}_B3出荷以降写真・動画"
        b4 = a_folder / f"{workno}_B4整理前写真・動画"
        for pth in (b1, b2, b3, b4):
            pth.mkdir(parents=True, exist_ok=True)
        return {"B1": b1, "B2": b2, "B3": b3, "B4": b4}

    def _collect_dirs_under(self, root: Path) -> List[Path]:
        out: List[Path] = []
        for cur, dirs, _files in os.walk(root, topdown=False):
            curp = Path(cur)
            for dn in dirs:
                out.append(curp / dn)
        return out

    def _move_contents(self, src_folder: Path, dst_folder: Path):
        if str(src_folder).lower() == str(dst_folder).lower():
            return
        try:
            dst_folder.mkdir(parents=True, exist_ok=True)
            moved = 0
            for item in src_folder.iterdir():
                res = self.ops.safe_move(item, dst_folder)
                if res:
                    moved += 1
            self.log.info(f"move_contents: {src_folder.name} -> {dst_folder.name} / moved={moved}")
        except Exception as e:
            self.log.warn(f"move_contents失敗: {src_folder} -> {dst_folder} ({e})")

    def _contains_media_recursive(self, folder: Path) -> bool:
        try:
            for cur, _dirs, files in os.walk(folder):
                curp = Path(cur)
                for fn in files:
                    pth = curp / fn
                    if self.ops.is_media(pth):
                        return True
        except Exception:
            return False
        return False

    def _normalize_existing_names_recursive(self, root: Path):
        renamed = 0
        for cur, dirs, files in os.walk(root, topdown=False):
            curp = Path(cur)

            for fn in files:
                src = curp / fn
                desired_name = normalize_existing_path_name(src.name, is_dir=False)
                if desired_name == src.name:
                    continue
                moved = self.ops.safe_rename(src, src.with_name(desired_name))
                if moved:
                    renamed += 1
                    self.log.info(f"[91] 英単語アンダーバー補正(file): {src.name} -> {moved.name}")

            for dn in dirs:
                src = curp / dn
                desired_name = normalize_existing_path_name(src.name, is_dir=True)
                if desired_name == src.name:
                    continue
                moved = self.ops.safe_rename(src, src.with_name(desired_name))
                if moved:
                    renamed += 1
                    self.log.info(f"[91] 英単語アンダーバー補正(dir): {src.name} -> {moved.name}")

        if renamed:
            self.log.info(f"[91] 英単語アンダーバー補正件数: {renamed}")

    def _cleanup_stale_tmp_files(self, root: Path):
        recovered = 0
        skipped = 0
        for cur, _dirs, files in os.walk(root):
            curp = Path(cur)
            for fn in files:
                match = TMP_RENAME_PATTERN.match(fn)
                if not match:
                    continue

                original_name = match.group(1).strip()
                src = curp / fn
                if not original_name:
                    skipped += 1
                    self.log.warn(f"stale TMP回収失敗: 元名が読めないため残置 {src}")
                    continue

                moved = self.ops.safe_rename(src, curp / original_name)
                if moved:
                    recovered += 1
                    self.log.warn(f"stale TMP回収: {src.name} -> {moved.name}")
                else:
                    skipped += 1

        if recovered or skipped:
            self.log.info(f"[91] stale TMP回収 summary: recovered={recovered}, skipped={skipped}")

    _CONFORM_SEQ_RE = re.compile(r"^(\d{3})_(\d{6})$")

    def _is_folder_conformant(self, files: List[Path], prefix: Optional[str]) -> bool:
        """フォルダが整理済み（prefix_連番_日付、圧縮済み、未変換なし）なら True。

        stat とファイル名判定のみで画像は開かない（処理済みフォルダの高速スキップ用）。
        条件を1つでも満たさなければ False（従来のフル処理に回す）。
        """
        seqs = []
        pfx = f"{prefix}_" if prefix else ""
        for p in files:
            stem = p.stem
            if pfx:
                if not stem.startswith(pfx):
                    return False
                stem = stem[len(pfx):]
            m = self._CONFORM_SEQ_RE.match(stem)
            if not m:
                return False
            ext = p.suffix.lower()
            # 未変換の png/heic/heif が残っていれば要処理
            if ext in self.cfg.photo_ext and ext not in {".jpg", ".jpeg"}:
                return False
            # jpg は圧縮上限以内であること（stat のみ）
            if ext in {".jpg", ".jpeg"}:
                try:
                    if p.stat().st_size / 1024 > self.cfg.max_kb:
                        return False
                except OSError:
                    return False
            seqs.append(int(m.group(1)))
        # 連番が 1..N で欠番・重複なし
        return sorted(seqs) == list(range(1, len(files) + 1))

    def _rename_media_to_seq_date(self, folder: Path, prefix: Optional[str]):
        try:
            files = [p for p in folder.iterdir() if p.is_file() and self.ops.is_media(p)]
        except Exception:
            return
        if not files:
            return

        # ── 整理済みフォルダはスキップ（TMPリネーム・EXIF読みを行わない） ──
        if self._is_folder_conformant(files, prefix):
            self.log.info(f"rename不要（整理済み）: {folder} / media={len(files)}")
            return

        self.log.info(f"rename開始: {folder} / media={len(files)}")

        tmp_prefix = f"__TMP__{int(time.time() * 1000)}__"
        tmp_paths: List[Path] = []
        for i, pth in enumerate(sorted(files, key=lambda x: x.name.lower()), 1):
            tmp_name = f"{tmp_prefix}{i:06d}__{pth.name}"
            moved = self.ops.safe_rename(pth, folder / tmp_name)
            if moved:
                tmp_paths.append(moved)

        parsed = []
        for i, tp in enumerate(tmp_paths, 1):
            dt = get_media_capture_datetime(tp, self.cfg, self.log)
            parsed.append((tp, dt, dt.strftime("%y%m%d")))
            if i % 20 == 0 or i == len(tmp_paths):
                self.log.info(f"日時取得進捗: {folder.name} {i}/{len(tmp_paths)}")

        parsed.sort(key=lambda x: (x[1], x[0].name.lower()))

        for idx, (tp, _dt, yymmdd) in enumerate(parsed, 1):
            ext = tp.suffix.lower()
            base = f"{prefix}_{idx:03d}_{yymmdd}" if prefix else f"{idx:03d}_{yymmdd}"
            dst = folder / f"{base}{ext}"
            moved = self.ops.safe_rename(tp, dst)
            if moved and moved.suffix.lower() in self.cfg.photo_ext:
                final_path = self.compressor.compress_or_convert(moved)
                desired_final = folder / f"{base}.jpg" if final_path.suffix.lower() == ".jpg" and ext in {".png", ".heic", ".heif"} else moved
                if final_path != desired_final:
                    self.ops.safe_rename(final_path, desired_final)

            if idx % 20 == 0 or idx == len(parsed):
                self.log.info(f"rename進捗: {folder.name} {idx}/{len(parsed)}")

    def _remove_dir_if_empty(self, d: Path):
        """
        fix_b4_scan.py準拠: ジャンクファイル（Thumbs部分一致/.tmp/定義済み）除去後、空なら削除
        """
        if not d.is_dir():
            return  # ファイルパスが渡された場合は何もしない
        try:
            items = list(d.iterdir())
        except Exception as e:
            self.log.warn(f"空判定失敗: {d} ({e})")
            return

        # ジャンクファイルだけなら先に消す（Thumbs部分一致/.tmp/定義済み）
        for x in items:
            try:
                if x.is_file() and self.ops.is_junk(x):
                    self.log.info(f"[DEBUG] remove junk file: {x}")
                    if not self.cfg.dry_run:
                        try:
                            os.chmod(str(x), stat.S_IWRITE)
                        except Exception:
                            pass
                        x.unlink()
                        self.log.info(f"[OK] removed: {x}")
            except Exception as e:
                self.log.warn(f"[WARN] junk remove failed: {x} ({e})")

        try:
            items = list(d.iterdir())
        except Exception as e:
            self.log.warn(f"再空判定失敗: {d} ({e})")
            return

        if items:
            self.log.info(f"[DEBUG] 空ではないため残置: {d} / children={[x.name for x in items]}")
            return

        if self.cfg.dry_run:
            self.log.info(f"[DRY] 空フォルダ削除: {d}")
            return

        last_err = None
        for i in range(3):
            try:
                if self.cfg.use_long_path_ops and is_windows():
                    os.rmdir(to_long_path(d))
                else:
                    d.rmdir()
                self.log.info(f"空フォルダ削除: {d}")
                return
            except Exception as e:
                last_err = e
                self.log.warn(f"空フォルダ削除リトライ {i + 1}/3: {d} ({e})")
                time.sleep(0.5)

        self.log.warn(f"空フォルダ削除失敗: {d} ({last_err})")


    def _remove_B4_and_empty_subdirs(self, b4: Path):
        """B4配下の空サブフォルダを削除し、B4自体も空なら削除する。"""
        try:
            for sub in list(b4.iterdir()):
                if sub.is_dir():
                    items = list(sub.iterdir())
                    if not items:
                        self._remove_dir_if_empty(sub)
        except Exception as e:
            self.log.warn(f"B4配下サブフォルダ空判定失敗: {b4} ({e})")
        self._remove_dir_if_empty(b4)

    def _extract_non_media_from_phase_to_b4(self, phase: Path, b4: Path):
        """B1/B2/B3 内の非メディアを B4 ルートへ移動する。
        - phase: B1/B2/B3 の Path
        - b4: B4 の Path（存在する前提; ensure_B_folders で作成済み）
        注意: .json ファイル（RAGサイドカー）はメディアファイルに隣接させるため移動しない。
        """
        try:
            for item in list(phase.iterdir()):
                # ファイルが非メディアなら B4 へ（.json はサイドカーなので除外）
                if item.is_file():
                    if not self.ops.is_media(item) and item.suffix.lower() != ".json":
                        res = self.ops.safe_move(item, b4)
                        if res:
                            self.log.info(f"phase->B4 非メディア移動: {item.name} -> {b4.name}")
                # サブフォルダ内のファイルもチェックして非メディアを B4 へ
                elif item.is_dir():
                    try:
                        for sub in list(item.iterdir()):
                            if sub.is_file() and not self.ops.is_media(sub) and sub.suffix.lower() != ".json":
                                res = self.ops.safe_move(sub, b4)
                                if res:
                                    self.log.info(f"phase->B4 非メディア移動: {sub.name} -> {b4.name}")
                    except Exception:
                        # サブフォルダの読み取り失敗はログに残すが続行
                        self.log.warn(f"サブフォルダ走査失敗（phase->B4）: {item}")
                # サブフォルダが空なら削除
                self._remove_dir_if_empty(item)
        except Exception as e:
            self.log.warn(f"_extract_non_media_from_phase_to_b4 エラー: {phase} ({e})")

    def _flatten_non_media_in_b4(self, b4: Path):
        """B4 内のサブフォルダにある非メディアを B4 ルートへ移す（ばらす）。
        - サブフォルダが空になったら削除する。
        - 必要なら再帰化も可能（現状はサブフォルダ直下のみ）。
        """
        try:
            subs = [p for p in b4.iterdir() if p.is_dir()]
        except Exception:
            return
        for sd in sorted(subs, key=lambda pth: pth.name.lower()):
            moved = 0
            try:
                for child in list(sd.iterdir()):
                    if child.is_file() and not self.ops.is_media(child):
                        res = self.ops.safe_move(child, b4)
                        if res:
                            moved += 1
            except Exception:
                self.log.warn(f"B4 サブフォルダ走査失敗: {sd}")
            if moved:
                self.log.info(f"B4 サブフォルダ内 非メディア展開: {sd.name} -> {b4.name} moved={moved}")
            self._remove_dir_if_empty(sd)

    def _rehome_misfiled_phase_subdirs(self, bmap: Dict[str, Path]):
        moved = 0
        for current_key, current_b in bmap.items():
            try:
                subdirs = [p for p in current_b.iterdir() if p.is_dir()]
            except Exception:
                continue

            for subdir in sorted(subdirs, key=lambda pth: pth.name.lower()):
                target_key = self._classify_folder_name_to_B(subdir.name)
                if not target_key or target_key == current_key:
                    continue

                target_b = bmap[target_key]
                self.log.info(
                    f"位相サブフォルダ再配置: {subdir} -> {target_b} "
                    f"(from {current_key} to {target_key})"
                )
                self._move_contents(subdir, target_b)
                self._remove_dir_if_empty(subdir)
                moved += 1

        if moved:
            self.log.info(f"位相サブフォルダ再配置数: {moved}")

    def process_A(self, a_folder: Path, idx: int = 0, total: int = 0):
        workno = get_workno_from_name(a_folder.name)
        if not workno:
            return

        self.log.info(f"[91:{idx}/{total}] A処理開始: {a_folder}")
        self._normalize_existing_names_recursive(a_folder)
        bmap = self._ensure_B_folders(a_folder, workno)
        b1, b2, b3, b4 = bmap["B1"], bmap["B2"], bmap["B3"], bmap["B4"]

        subdirs = self._collect_dirs_under(a_folder)
        subdirs.sort(key=lambda pth: len(str(pth).split(os.sep)), reverse=True)
        self.log.info(f"[91:{workno}] 配下サブフォルダ数: {len(subdirs)}")

        classified = 0
        for d in subdirs:
            if str(d).lower() in {str(b1).lower(), str(b2).lower(), str(b3).lower(), str(b4).lower()}:
                continue

            k = self._classify_folder_name_to_B(d.name)
            if k:
                dst = bmap[k]
                self._move_contents(d, dst)
                self._remove_dir_if_empty(d)
                classified += 1
                continue

            # B4配下のサブフォルダ: フォルダ名でB1/B2/B3に振り分け、条件なしはB4直下に展開
            if d.parent == b4:
                target_key = self._classify_folder_name_to_B(d.name)
                if target_key in ("B1", "B2", "B3"):
                    target_dir = bmap[target_key]
                    for item in list(d.iterdir()):
                        self.ops.safe_move(item, target_dir)
                    self.log.info(f"B4サブフォルダ->{target_key}: {d.name}")
                else:
                    # 条件なし（入庫など）はB4直下に展開
                    for item in list(d.iterdir()):
                        self.ops.safe_move(item, b4)
                self._remove_dir_if_empty(d)
                classified += 1
                continue

            # 未分類サブフォルダでメディアがあればB4へ退避
            if d.parent == a_folder and self._contains_media_recursive(d):
                self.log.info(f"未分類サブフォルダをB4へ退避: {d}")
                self._move_contents(d, b4)
                self._remove_dir_if_empty(d)
                classified += 1


        self.log.info(f"[91:{workno}] 分類済みフォルダ数: {classified}")
        self._rehome_misfiled_phase_subdirs(bmap)

        # B1/B2/B3 に混入している非メディアを B4 へ移動（B4 は ensure_B_folders によって作成済み）
        for phase in (b1, b2, b3):
            self._extract_non_media_from_phase_to_b4(phase, b4)

        # B4 内サブフォルダにある非メディアを B4 ルートへ展開（ばらす）
        self._flatten_non_media_in_b4(b4)

        # A直下のメディアはB4へ
        moved_root_media = 0
        try:
            for pth in list(a_folder.iterdir()):
                if pth.is_file() and self.ops.is_media(pth):
                    res = self.ops.safe_move(pth, b4)
                    if res:
                        moved_root_media += 1
        except Exception as e:
            self.log.warn(f"A直下メディア移動失敗: {a_folder} -> {b4} ({e})")
        self.log.info(f"[91:{workno}] A直下->B4 移動数: {moved_root_media}")

        # B1/B2/B3内の非メディアファイルはB4へ退避
        for bx in (b1, b2, b3):
            try:
                for item in list(bx.iterdir()):
                    if item.is_file() and not self.ops.is_media(item) and item.name not in self.cfg.junk_files and item.suffix.lower() != ".json":
                        self.ops.safe_move(item, b4)
            except Exception as e:
                self.log.warn(f"B内非メディア退避失敗: {bx} -> {b4} ({e})")


        # メディアリネーム・圧縮
        for b in (b1, b2, b3, b4):
            self._rename_media_to_seq_date(b, prefix=workno)
            try:
                subs = [p for p in b.iterdir() if p.is_dir()]
            except Exception:
                subs = []
            for sd in sorted(subs, key=lambda pth: pth.name.lower()):
                self._rename_media_to_seq_date(sd, prefix=sd.name)

        # B4配下の空サブフォルダも削除し、B4自体も再度空判定して削除
        self._remove_B4_and_empty_subdirs(b4)
        # 他Bも空なら削除
        for bx in (b1, b2, b3):
            self._remove_dir_if_empty(bx)
        self.log.info(f"[91:{idx}/{total}] A処理完了: {a_folder}")

    def run(self):
        root = self.cfg.target_91_root
        if not root.is_dir():
            self.log.warn(f"91 root not found: {root}")
            return
        self._cleanup_stale_tmp_files(root)
        a_list = [p for p in root.iterdir() if p.is_dir() and get_workno_from_name(p.name)]
        a_list.sort(key=lambda pth: pth.name.lower())
        self.log.info(f"[91] 対象Aフォルダ数: {len(a_list)}")
        for idx, a in enumerate(a_list, 1):
            self.process_A(a, idx=idx, total=len(a_list))
