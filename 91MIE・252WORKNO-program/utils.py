"""共通ユーティリティ（ファイル名正規化やログ、パス操作など）。"""

import os
import re
import time
from datetime import datetime
from pathlib import Path

INVALID_WIN_CHARS = r"[\\/:*?\"<>|]"
ENGLISH_SPACE_EXACT_EXCLUSIONS = {
    "AUDIO_TS",
    "VIDEO_TS",
}


def now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def p(msg: str):
    """簡易ログ出力（標準出力）。"""
    print(f"[{now_ts()}] {msg}", flush=True)


def sanitize_name(name: str) -> str:
    name = re.sub(INVALID_WIN_CHARS, "_", str(name))
    name = name.strip().rstrip(".")
    return name or "untitled"


def restore_english_spaces(name_stem: str) -> str:
    """英字のみの連続トークン間の "_" を半角スペースに戻す。"""
    if name_stem in ENGLISH_SPACE_EXACT_EXCLUSIONS:
        return name_stem
    parts = name_stem.split("_")
    if len(parts) <= 1:
        return name_stem

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

    return "_".join(out)


def restore_machine_suffix_underscores(name_stem: str) -> str:
    """機種名末尾の E 記号を `)E_12345` 形式に戻す。"""
    return re.sub(r"\)_E(?=\d)", ")E_", name_stem)


def normalize_master_name(name: str) -> str:
    """工番マスタ由来の名称を nl2sp しつつ Windows ファイル名に安全化する。"""
    normalized = restore_english_spaces(str(name).strip())
    normalized = restore_machine_suffix_underscores(normalized)
    return sanitize_name(normalized)


def normalize_existing_path_name(name: str, *, is_dir: bool) -> str:
    """既存パス名の英単語間 `_` を半角スペースへ戻す。"""
    raw = str(name)
    if is_dir:
        return sanitize_name(restore_english_spaces(raw))

    pth = Path(raw)
    fixed_stem = restore_english_spaces(pth.stem)
    return sanitize_name(f"{fixed_stem}{pth.suffix}")


def escape_gdrive_query_value(s: str) -> str:
    # Drive クエリでのシングルクォートをエスケープ
    return str(s).replace("\\", "\\\\").replace("'", "\\'")


def is_windows() -> bool:
    return os.name == "nt"


def to_long_path(pth: Path) -> str:
    s = os.path.abspath(str(pth))
    if not is_windows():
        return s
    if s.startswith("\\\\?\\"):
        return s
    if s.startswith("\\\\"):
        return "\\\\?\\UNC\\" + s[2:]
    return "\\\\?\\" + s


def ensure_local_dir(path: str):
    os.makedirs(path, exist_ok=True)
