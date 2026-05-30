from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from typing import Iterable, Optional

try:
    import fitz  # PyMuPDF
except ImportError as exc:  # pragma: no cover - import-time guard
    raise SystemExit(
        "PyMuPDF is required. Install it with: pip install pymupdf"
    ) from exc

try:
    import pytesseract
    from PIL import Image
except ImportError:
    pytesseract = None
    Image = None


DEFAULT_ROOT = Path(
    r"Z:\takachiho\2to9_業務別フォルダ\30_メーカー資料\あ行(あいうえお)\アイダエンジニアリング\調達"
)

SKIP_DIR_NAMES = {".git", ".archive", "venv", "__pycache__", ".idea"}
PREFERRED_PDF_HINTS = ("工程表",)
NOISE_PATTERNS = (
    r"^page\s*\d+",
    r"^\d+\s*/\s*\d+$",
    r"^作成日",
    r"^改訂",
    r"^rev\.?",
    r"^ver\.?",
    r"^http[s]?://",
)

TESSERACT_PATH = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
if pytesseract is not None and TESSERACT_PATH.exists():
    pytesseract.pytesseract.tesseract_cmd = str(TESSERACT_PATH)


def sanitize_folder_name(name: str) -> str:
    cleaned = re.sub(r"[<>:\"/\\|?*]", "_", str(name))
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(".")
    return cleaned or "untitled"


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_machine_token(text: str) -> str:
    cleaned = normalize_text(text)
    cleaned = cleaned.replace("（", "(").replace("）", ")")
    cleaned = cleaned.replace("［", "[").replace("］", "]")
    cleaned = re.sub(r"\s*([()\[\]_\-])\s*", r"\1", cleaned)
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned.strip("_")


def is_noise_line(line: str) -> bool:
    text = normalize_text(line)
    if not text:
        return True
    lowered = text.lower()
    if any(re.search(pattern, lowered) for pattern in NOISE_PATTERNS):
        return True
    if len(text) <= 1:
        return True
    return False


def machine_like_score(text: str) -> int:
    normalized = normalize_machine_token(text)
    if not normalized:
        return -10_000
    score = len(normalized)
    if re.search(r"[A-Z]", normalized):
        score += 8
    if re.search(r"\d", normalized):
        score += 6
    if re.search(r"[-_()]", normalized):
        score += 4
    if re.fullmatch(r"[A-Z0-9][A-Z0-9\-_.()]*", normalized):
        score += 6
    if any(keyword in normalized for keyword in ("工程表", "作成日", "改訂", "ページ", "PAGE", "ver", "REV")):
        score -= 20
    return score


def extract_machine_token(text: str) -> Optional[str]:
    normalized = normalize_text(text).replace("（", "(").replace("）", ")")
    normalized = normalized.replace("［", "[").replace("］", "]")
    patterns = (
        r"(?<![A-Z0-9])[A-Z]\d{2,5}(?![A-Z0-9])",
        r"(?<![A-Z0-9])[A-Z]\d-\d{3,5}[A-Z]?(?![A-Z0-9])",
        r"(?<![A-Z0-9])[A-Z][A-Z0-9]{0,4}-\d{3,5}[A-Z]?(?![A-Z0-9])",
        r"(?<![A-Z0-9])[A-Z][A-Z0-9]{0,4}(?:-[A-Z0-9]{2,8})+(?![A-Z0-9])",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            return normalize_machine_token(match.group(0))
    return None


def extract_machine_prefix_from_name(text: str) -> Optional[str]:
    parts = [part for part in re.split(r"[_\s]+", normalize_text(text)) if part]
    if len(parts) >= 2:
        first = extract_machine_token(parts[0])
        second = extract_machine_token(parts[1])
        if first and second:
            return f"{first}_{second}"
    return extract_machine_token(text)


def extract_machine_name_from_lines(lines: Iterable[str]) -> Optional[str]:
    normalized = [normalize_text(line) for line in lines if normalize_text(line)]
    if not normalized:
        return None

    table_row_pattern = re.compile(r"^\d{6,}\s+([A-Z][A-Z0-9\-]{1,20})\b")

    def is_machine_like(value: str) -> bool:
        return bool(re.search(r"\d|[\-]", value))

    for line in normalized:
        if any(keyword in line for keyword in ("工程表", "指図番号", "機械名", "部品番号", "工事番号")):
            continue

        match = table_row_pattern.search(line)
        if match:
            candidate = match.group(1)
            if is_noise_line(candidate) or not is_machine_like(candidate):
                continue
            return normalize_machine_token(candidate)

        if "_" in line:
            before, _after = line.split("_", 1)
            before_tokens = re.findall(r"[A-Z][A-Z0-9\-]{1,20}", before)
            if before_tokens:
                candidate = before_tokens[-1]
                if candidate not in {"MAT", "SL", "SCMn3", "B", "A"} and is_machine_like(candidate):
                    return normalize_machine_token(candidate)

        if "2020/" in line or "2021/" in line or "2022/" in line or "2023/" in line or "2024/" in line or "2025/" in line or "2026/" in line:
            tokens = re.findall(r"[A-Z][A-Z0-9\-]{1,20}", line)
            if len(tokens) >= 2:
                candidate = tokens[1]
                if not is_noise_line(candidate) and is_machine_like(candidate):
                    return normalize_machine_token(candidate)

        token = extract_machine_token(line)
        if token and not is_noise_line(token) and is_machine_like(token):
            return normalize_machine_token(token)

    return None


def iter_pdf_candidates(folder: Path) -> list[Path]:
    pdfs = sorted(folder.glob("*.pdf"), key=lambda p: p.name.lower())
    preferred = [p for p in pdfs if any(hint in p.name for hint in PREFERRED_PDF_HINTS)]
    return preferred


def extract_top_text(pdf_path: Path, top_ratio: float = 0.25) -> list[str]:
    with fitz.open(pdf_path) as doc:
        if len(doc) == 0:
            return []
        page = doc[0]
        clip = fitz.Rect(0, 0, page.rect.width, page.rect.height * top_ratio)
        text = page.get_text("text", clip=clip)
        lines = [normalize_text(line) for line in text.splitlines()]
        return [line for line in lines if line]


def extract_top_ocr_text(pdf_path: Path, top_ratio: float = 0.35) -> list[str]:
    if pytesseract is None or Image is None:
        return []

    with fitz.open(pdf_path) as doc:
        if len(doc) == 0:
            return []
        page = doc[0]
        clip = fitz.Rect(0, 0, page.rect.width, page.rect.height * top_ratio)
        pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), clip=clip, alpha=False)
        image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        text = pytesseract.image_to_string(image, lang="eng", config="--psm 6")
        lines = [normalize_text(line) for line in text.splitlines()]
        return [line for line in lines if line]


def candidate_from_label_lines(lines: Iterable[str]) -> Optional[str]:
    label_pattern = re.compile(
        r"(?:機械名|機械名称|設備名|機種名|機番|マシン名)\s*[:：]?\s*(.+)"
    )
    label_only_pattern = re.compile(r"^(?:機械名|機械名称|設備名|機種名|機番|マシン名)\s*[:：]?$")

    normalized = [normalize_text(line) for line in lines if normalize_text(line)]
    for idx, line in enumerate(normalized):
        match = label_pattern.search(line)
        if match:
            candidate = normalize_text(match.group(1))
            if not is_noise_line(candidate):
                return candidate
        if label_only_pattern.fullmatch(line) and idx + 1 < len(normalized):
            candidate = normalize_text(normalized[idx + 1])
            if not is_noise_line(candidate):
                return candidate
    return None


def candidate_from_lines(lines: Iterable[str]) -> Optional[str]:
    normalized = [normalize_text(line) for line in lines if normalize_text(line)]
    if not normalized:
        return None

    token_candidates: list[str] = []
    for line in normalized:
        if is_noise_line(line):
            continue
        if any(keyword in line for keyword in ("工程表", "作成日", "改訂", "page", "ver")):
            continue
        token = extract_machine_token(line)
        if token:
            token_candidates.append(token)

    if token_candidates:
        return max(token_candidates, key=machine_like_score)

    best_line = max(normalized, key=machine_like_score)
    if machine_like_score(best_line) < 8:
        return None
    if sum(ch.isascii() for ch in best_line) / max(len(best_line), 1) < 0.6:
        return None
    token = extract_machine_token(best_line)
    return token or normalize_machine_token(best_line)


def derive_machine_name(pdf_path: Path) -> Optional[str]:
    for lines in (extract_top_ocr_text(pdf_path), extract_top_text(pdf_path)):
        if not lines:
            continue

        machine_candidate = extract_machine_name_from_lines(lines)
        if machine_candidate:
            return machine_candidate

        label_candidate = candidate_from_label_lines(lines)
        if label_candidate:
            return normalize_machine_token(label_candidate)

        line_candidate = candidate_from_lines(lines)
        if line_candidate:
            return normalize_machine_token(line_candidate)

    folder_token = extract_machine_prefix_from_name(pdf_path.parent.name)
    if folder_token:
        return folder_token

    return None


def resolve_target_name(folder: Path, machine_name: str) -> str:
    machine_prefix = sanitize_folder_name(machine_name)
    current_name = folder.name

    if current_name == machine_prefix or current_name.startswith(machine_prefix):
        return current_name

    return sanitize_folder_name(f"{machine_prefix}_{current_name}")


def unique_target_path(folder: Path, target_name: str) -> Path:
    parent = folder.parent
    candidate = parent / target_name
    if candidate == folder:
        return candidate
    if not candidate.exists():
        return candidate

    suffix = 2
    while True:
        numbered = parent / f"{target_name}_{suffix}"
        if numbered == folder or not numbered.exists():
            return numbered
        suffix += 1


def build_rename_plan(folder: Path) -> tuple[Path, str, Path] | None:
    pdf_candidates = iter_pdf_candidates(folder)
    if not pdf_candidates:
        return None

    pdf_path = pdf_candidates[0]
    machine_name = derive_machine_name(pdf_path)
    if not machine_name:
        return None

    target_name = resolve_target_name(folder, machine_name)
    target_path = unique_target_path(folder, target_name)
    return pdf_path, machine_name, target_path


def rename_folder(folder: Path, apply: bool) -> tuple[bool, str | None]:
    plan = build_rename_plan(folder)
    if not plan:
        return False, None

    pdf_path, machine_name, target_path = plan
    if target_path == folder:
        return False, machine_name

    if apply:
        folder.rename(target_path)
        print(f"RENAMED: {folder} -> {target_path}")
    else:
        print(f"[DRY] RENAME: {folder} -> {target_path}")
        print(f"       source pdf: {pdf_path.name}")
        print(f"       machine: {machine_name}")

    return True, machine_name


def run(root: Path, apply: bool, recursive: bool) -> int:
    if not root.exists():
        print(f"Root not found: {root}")
        return 1

    picked_machines: dict[str, list[Path]] = {}
    target_folders: list[Path] = []

    if recursive:
        for dirpath, dirnames, filenames in os.walk(root, topdown=False):
            current = Path(dirpath)
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
            if current == root:
                continue
            target_folders.append(current)
    else:
        for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if child.is_dir() and child.name not in SKIP_DIR_NAMES:
                target_folders.append(child)

    for folder in target_folders:
        try:
            plan = build_rename_plan(folder)
            if plan:
                _pdf_path, machine_name, _target_path = plan
                picked_machines.setdefault(machine_name, []).append(folder)
        except Exception as exc:
            print(f"[ERROR] {folder}: {exc}")

    if picked_machines:
        print("Picked machine names:")
        for machine_name in sorted(picked_machines):
            label = f"{machine_name}_" if machine_name == "K1-6300" else machine_name
            folder_paths = ", ".join(sorted(str(folder) for folder in picked_machines[machine_name]))
            print(f"  - {label}: {folder_paths}")
        print("---")

    if recursive:
        for current in target_folders:
            try:
                renamed, machine_name = rename_folder(current, apply=apply)
                if not renamed:
                    print(f"[SKIP] {current}")
            except Exception as exc:
                print(f"[ERROR] {current}: {exc}")
    else:
        for child in target_folders:
            try:
                renamed, machine_name = rename_folder(child, apply=apply)
                if not renamed:
                    print(f"[SKIP] {child}")
            except Exception as exc:
                print(f"[ERROR] {child}: {exc}")

    print("Done")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="工程表PDFの上部から機械名を拾って、フォルダ名の先頭に付ける",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="対象の親フォルダ",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="実際にリネームする",
    )
    parser.add_argument(
        "--non-recursive",
        action="store_true",
        help="直下のフォルダだけ処理する",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run(args.root, apply=args.apply, recursive=not args.non_recursive)


if __name__ == "__main__":
    raise SystemExit(main())