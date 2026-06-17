# --- 図面枠（タイトルブロック）自動検出＆回転補正 ---
from __future__ import annotations

def detect_title_block_and_correct_angle(image: Image.Image, debug_out: str = None) -> Image.Image:
    """
    OpenCVで最大の長方形輪郭（図面枠）を検出し、
    その角度で画像を回転補正して返す。
    debug_out: 補正後画像の保存パス（任意）
    """
    import cv2
    import numpy as np
    arr = np.array(image.convert("L"))
    # 2値化
    _, th = cv2.threshold(arr, 180, 255, cv2.THRESH_BINARY_INV)
    # 輪郭抽出
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    max_area = 0
    best_rect = None
    for cnt in contours:
        rect = cv2.minAreaRect(cnt)
        (cx, cy), (w, h), angle = rect
        area = w * h
        if area > max_area and w > 100 and h > 100:
            max_area = area
            best_rect = rect
    if best_rect is None:
        # 検出失敗時はそのまま返す
        return image
    (cx, cy), (w, h), angle = best_rect
    # OpenCVのminAreaRectのangleは-90〜0度
    if w < h:
        angle = angle + 90
    # 回転補正
    rot_mat = cv2.getRotationMatrix2D((cx, cy), angle, 1.0)
    arr_color = np.array(image.convert("RGB"))
    rotated = cv2.warpAffine(arr_color, rot_mat, (arr_color.shape[1], arr_color.shape[0]), flags=cv2.INTER_CUBIC, borderValue=(255,255,255))
    # 補正後画像をPILに戻す
    pil_rot = Image.fromarray(rotated)
    if debug_out:
        pil_rot.save(debug_out)
    return pil_rot


#
#
import os
import re
import argparse
import csv
from pathlib import Path

# --- モデルらしき部分を行中から抽出する正規表現群 ---
_MODEL_INNER_REGEXES = [
    re.compile(r"[A-Z]{1,5}-\d{2,6}(?:[A-Z0-9xX%./]*)?", re.IGNORECASE),   # PMX-12000, ABC-1234
    re.compile(r"[A-Z]{1,4}\d{2,5}(?:-[A-Z0-9]{1,3})?", re.IGNORECASE),    # A1234, AB12-3
    re.compile(r"[A-Z]{2,5}\([^)]+\)", re.IGNORECASE),                     # ABC(12)
    re.compile(r"[A-Z]{2,5}", re.IGNORECASE),                              # ABC
]

def extract_inner_model_token(line: str) -> str | None:
    """行の中から最も"モデルらしき"部分を取り出す（最長一致・優先順位あり）"""
    if not line:
        return None
    for rx in _MODEL_INNER_REGEXES:
        m = rx.search(line)
        if m:
            return m.group(0)
    m2 = re.search(r"[A-Za-z0-9][A-Za-z0-9\-\./()]{1,20}", line)
    if m2:
        return m2.group(0)
    return None

def normalize_model_token(token: str) -> str:
    """モデルトークンを正規化：余分なサフィックスを削り、大文字化など"""
    if not token:
        return token
    t = token.strip()
    t = re.sub(r"^[0-9]{1,2}[a-z]\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"[\s\._,-]*(?:[sS]{1,2})\.?$", "", t)
    t = re.sub(r"[a-z]{1,2}$", "", t)
    t = t.replace("×", "x").replace("%", "x").replace("#", "特")
    t = re.sub(r"\s+", "", t)
    return t.upper()

try:
    import fitz  # PyMuPDF
except ImportError as exc:  # pragma: no cover - import-time guard
    raise SystemExit("PyMuPDF is required. Install it with: pip install pymupdf") from exc


try:
    import pytesseract
    from PIL import Image, ImageFilter, ImageOps
except ImportError as exc:
    raise SystemExit("pytesseract and Pillow are required. Install them in the workspace venv.") from exc

# OpenCV/numpy for advanced preprocessing
try:
    import cv2
    import numpy as np
except ImportError as exc:
    raise SystemExit("opencv-python and numpy are required for advanced OCR preprocessing. Install them in the workspace venv.") from exc


TESSERACT_PATH = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
if TESSERACT_PATH.exists():
    pytesseract.pytesseract.tesseract_cmd = str(TESSERACT_PATH)

DEFAULT_ROOT = Path(r"Z:\takachiho\2to9_業務別フォルダ\30_メーカー資料\あ行(あいうえお)\アイダエンジニアリング\調達")
print(f"[DEBUG] DEFAULT_ROOT={DEFAULT_ROOT}")
print(f"[DEBUG] DEFAULT_ROOT exists={DEFAULT_ROOT.exists()}")
print(f"[DEBUG] DEFAULT_ROOT is_dir={DEFAULT_ROOT.is_dir()}")
print(f"[DEBUG] DEFAULT_ROOT={DEFAULT_ROOT}")
print(f"[DEBUG] DEFAULT_ROOT exists={DEFAULT_ROOT.exists()}")
print(f"[DEBUG] DEFAULT_ROOT is_dir={DEFAULT_ROOT.is_dir()}")
DWG_RE = re.compile(r"^(\d{3}-\d{4,5}(?:-[a-z])?)", re.IGNORECASE)


def find_drawing_number(path: Path) -> str | None:
    m = DWG_RE.match(path.name)
    if m:
        return m.group(1)
    return None


def model_from_folder_name(folder_name: str) -> str | None:
    parts = folder_name.split("_")
    if len(parts) >= 2:
        return parts[1]
    return None


def ocr_lower_right(page) -> str:
    full_pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
    full_image = Image.frombytes("RGB", [full_pix.width, full_pix.height], full_pix.samples)

    texts = []
    for angle in (0, 90, 180, 270):
        texts.append(pytesseract.image_to_string(full_image.rotate(angle, expand=True), lang="eng", config="--psm 11"))
    return "\n".join(texts)


def ocr_text(image: Image.Image, psm: int = 6) -> str:
    return pytesseract.image_to_string(image, lang="eng", config=f"--psm {psm}")



# OpenCVベースの強力な前処理
def prepare_for_ocr(image: Image.Image) -> Image.Image:
    arr = np.array(image.convert("RGB"))[:, :, ::-1]  # PIL->BGR
    gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
    # CLAHE（局所コントラスト強調）
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    cl = clahe.apply(gray)
    # ノイズ除去
    den = cv2.fastNlMeansDenoising(cl, None, h=10, templateWindowSize=7, searchWindowSize=21)
    # 適応的二値化
    th = cv2.adaptiveThreshold(den, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY, 31, 10)
    # モルフォロジー: クロージングで線をつなげ、オープニングで小ノイズ除去
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    morphed = cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel)
    morphed = cv2.morphologyEx(morphed, cv2.MORPH_OPEN, kernel)
    return Image.fromarray(morphed)


MODEL_FUZZY_RE = re.compile(r"^M[O0][D][E3][L1I]$", re.IGNORECASE)
_MODEL_SHORT_ALLOWLIST = {"TMX"}
_MODEL_SHORT_NOISE_RE = re.compile(r"^[A-Z]{2,5}$")
_MODEL_SINGLE_PREFIX_CODE_RE = re.compile(r"^[A-Z]-\d{2,6}(?:/\d{2,6})?$")


def normalize_drawing_number(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", value).upper()


def title_block_strips(image: Image.Image) -> list[tuple[tuple[int, int, int, int], Image.Image]]:
    """Return likely title-block regions from the image.

    The crop list is ordered from narrowest to broadest so OCR first sees the
    most magnified title-block slice, then wider fallbacks, then the full image.
    """
    width, height = image.size
    # 右下横長、右端縦長、下端横長、全体
    strips = [
        ((int(width * 0.80), int(height * 0.75), width, height), image.crop((int(width * 0.80), int(height * 0.75), width, height))),
        ((int(width * 0.85), 0, width, height), image.crop((int(width * 0.85), 0, width, height))),
        ((0, int(height * 0.80), width, height), image.crop((0, int(height * 0.80), width, height))),
        ((0, 0, width, height), image),
    ]
    # 右上コーナーの縦長領域（MODELラベルが縦書きで入るパターン対応）
    strips.append(((int(width * 0.80), 0, width, int(height * 0.25)), image.crop((int(width * 0.80), 0, width, int(height * 0.25)))))
    # 右上コーナーのさらに狭い縦長領域（よりピンポイント）
    strips.append(((int(width * 0.90), 0, width, int(height * 0.20)), image.crop((int(width * 0.90), 0, width, int(height * 0.20)))))
    return strips


def _data_to_lines(data: dict) -> list[dict]:
    """Group Tesseract word-level data into logical lines."""
    from collections import defaultdict

    groups: dict[tuple[int, int, int], list[int]] = defaultdict(list)
    for index, word in enumerate(data["text"]):
        if not str(word).strip():
            continue
        key = (data["block_num"][index], data["par_num"][index], data["line_num"][index])
        groups[key].append(index)

    lines: list[dict] = []
    for key in sorted(groups):
        indexes = groups[key]
        text = " ".join(str(data["text"][i]) for i in indexes)
        left = min(data["left"][i] for i in indexes)
        top = min(data["top"][i] for i in indexes)
        right = max(data["left"][i] + data["width"][i] for i in indexes)
        bottom = max(data["top"][i] + data["height"][i] for i in indexes)
        lines.append({"text": text, "left": left, "top": top, "right": right, "bottom": bottom})
    return lines


def find_model_boxes(image: Image.Image) -> list[tuple[int, int, int, int]]:
    boxes: list[tuple[int, int, int, int]] = []
    for box, strip in title_block_strips(image):
        proc = prepare_for_ocr(strip)
        data = pytesseract.image_to_data(proc, lang="eng", config="--psm 11", output_type=pytesseract.Output.DICT)
        lines = _data_to_lines(data)
        for line in lines:
            if MODEL_FUZZY_RE.search(line["text"]):
                left = line["left"] + box[0]
                top = line["top"] + box[1]
                width = line["right"] - line["left"]
                height = line["bottom"] - line["top"]
                boxes.append((left, top, width, height))

        if boxes:
            return boxes

        text = ocr_text(strip, psm=6)
        if re.search(r"\bM[O0]D[E3][LI1]\b", text, re.IGNORECASE):
            boxes.append(box)
            return boxes
    return boxes


def find_drawing_number_boxes(image: Image.Image, drawing_number: str) -> list[tuple[int, int, int, int]]:
    """Locate the drawing number on the page using line-level search."""
    parts = drawing_number.split("-", 1)
    suffix = parts[-1] if len(parts) > 1 else drawing_number
    prefix = parts[0] if len(parts) > 1 else ""

    suffix_pattern = re.compile(r"[^0-9A-Za-z]*".join(re.escape(c) for c in suffix), re.IGNORECASE)
    full_pattern = (
        re.compile(re.escape(prefix) + r"[\s\-_./]*" + re.escape(suffix), re.IGNORECASE)
        if prefix
        else suffix_pattern
    )

    seen: set[tuple[int, int]] = set()
    boxes: list[tuple[int, int, int, int]] = []
    for box, strip in title_block_strips(image):
        proc = prepare_for_ocr(strip)
        data = pytesseract.image_to_data(proc, lang="eng", config="--psm 11", output_type=pytesseract.Output.DICT)
        lines = _data_to_lines(data)

        for line in lines:
            text = line["text"]
            if full_pattern.search(text) or suffix_pattern.search(text):
                left = line["left"] + box[0]
                top = line["top"] + box[1]
                width = line["right"] - line["left"]
                height = line["bottom"] - line["top"]
                key = (left, top)
                if key not in seen:
                    seen.add(key)
                    boxes.append((left, top, width, height))

        if boxes:
            return boxes

        text = ocr_text(strip, psm=6)
        if full_pattern.search(text) or suffix_pattern.search(text):
            boxes.append(box)
            return boxes
    return boxes


def crop_model_region(image: Image.Image, box: tuple[int, int, int, int]) -> list[Image.Image]:
    left, top, width, height = box
    image_width, image_height = image.size
    pad_x = max(80, width * 3)
    pad_y = max(60, height * 3)

    crops: list[Image.Image] = []

    regions = [
        (left - pad_x, top - pad_y, left + width + pad_x * 3, top + height + pad_y * 2),
        (left - pad_x, top - pad_y, left + width + pad_x * 2, top + height + pad_y * 3),
        (left - pad_x, top - pad_y, left + width + pad_x * 2, top + height + pad_y),
    ]

    for region_left, region_top, region_right, region_bottom in regions:
        clipped = (
            max(0, int(region_left)),
            max(0, int(region_top)),
            min(image_width, int(region_right)),
            min(image_height, int(region_bottom)),
        )
        if clipped[2] > clipped[0] and clipped[3] > clipped[1]:
            crops.append(image.crop(clipped))

    return crops


TIER_MODEL_ANCHOR = 0   # "MODEL" label found on page
TIER_DWG_ANCHOR = 1     # Drawing number found on page
TIER_NONE = 2           # No anchor found (not used as result)


def extract_model_from_page_with_score(
    page, drawing_number: str | None = None
) -> tuple[str | None, int, int]:
    """Return (candidate, score, tier).

    Tier 0 = extracted near a MODEL anchor (most trusted).
    Tier 1 = extracted near a drawing-number anchor.
    Tier 2 = no reliable anchor found (caller should treat as failure).

    Full-page OCR is intentionally NOT used as a candidate source to avoid
    picking up unrelated model-like strings from notes and parts lists.
    """

    full_pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
    full_image = Image.frombytes("RGB", [full_pix.width, full_pix.height], full_pix.samples)
    # --- タイトルブロック検出＆回転補正 ---
    try:
        corrected_image = detect_title_block_and_correct_angle(full_image)
        print("[DEBUG] detect_title_block_and_correct_angle 適用済み")
    except Exception as exc:
        print(f"[WARN] detect_title_block_and_correct_angle 失敗: {exc}")
        corrected_image = full_image

    # 以降は corrected_image を使う

    def normalize_candidate(candidate: str) -> str:
        candidate = candidate.strip().strip("-_/ ")
        candidate = candidate.replace("×", "x")
        candidate = candidate.replace("%", "x")
        candidate = candidate.replace("#", "特")
        candidate = re.sub(r"\s+", "", candidate)
        candidate = re.sub(r"(\d)[A-Z][a-z]+$", r"\1", candidate); candidate = re.sub(r"(\d)[a-z]{2,}$", r"\1", candidate)
        return candidate

    def is_likely_model(candidate: str) -> bool:
        if not candidate:
            return False
        if not candidate[0].isalpha() or not candidate[0].isupper():
            return False
        upper_candidate = normalize_candidate(candidate).upper()
        if re.search(r"[^A-Z0-9()./\-X特]", upper_candidate):
            return False
        if any(token in upper_candidate for token in ("PART", "DWG", "DATE", "SCALE", "MAT", "QTY", "REVISION", "SIGN", "NO", "NAME", "MODEL")):
            return False
        if upper_candidate in _MODEL_SHORT_ALLOWLIST:
            return True
        if _MODEL_SHORT_NOISE_RE.fullmatch(upper_candidate):
            return False
        if _MODEL_SINGLE_PREFIX_CODE_RE.fullmatch(upper_candidate):
            return False
        if upper_candidate.count("(") != upper_candidate.count(")"):
            return False
        if " " in candidate:
            return False
        if len(upper_candidate) < 2:
            return False
        if not re.search(r"[A-Z0-9]", upper_candidate):
            return False
        if len(upper_candidate) > 12 and not re.search(r"[-/()]", upper_candidate):
            return False
        if re.search(r"\d", upper_candidate) and len(upper_candidate) > 6 and not re.search(r"[-/()]", upper_candidate):
            return False
        if re.fullmatch(r"[A-Z0-9.\-_/]+\([A-Z0-9]+\)", upper_candidate):
            return False
        if re.fullmatch(r"[A-Z]{1,5}\([^)]+\)", candidate):
            return True
        if re.fullmatch(r"[A-Z]{1,4}\d{2,5}", upper_candidate):
            return True
        if re.fullmatch(r"[A-Z]{1,4}\d{2,5}-[A-Z0-9]{1,3}", upper_candidate):
            return True
        if re.fullmatch(r"[A-Z]{1,5}-\d{2,6}(?:[A-Z0-9xX%./]*)?", upper_candidate):
            return True
        if re.fullmatch(r"[A-Z]{1,5}-[A-Z0-9]{1,4}", upper_candidate):
            return True
        if re.fullmatch(r"[A-Z]\.[A-Z]-\d+/\d+[A-Z0-9xX%./]*", upper_candidate):
            return True
        return False

    def score_candidate(candidate: str, near_model: bool = False) -> int:
        normalized = normalize_candidate(candidate)
        upper_candidate = normalized.upper()
        score = 0
        if near_model:
            score += 12
        if re.search(r"\d", upper_candidate):
            score += 10
        if re.search(r"[()特]", normalized):
            score += 6
        if re.search(r"[\-./]", upper_candidate):
            score += 4
        if re.fullmatch(r"[A-Z]{2,5}", upper_candidate):
            score += 3
        if re.fullmatch(r"[A-Z]{1,5}\([^)]+\)", normalized):
            score += 8
        if normalized.islower():
            score -= 6
        if re.search(r"\b(PART|DWG|DATE|SCALE|MAT|QTY|REVISION|SIGN|NO\.?|NAME|MODEL)\b", upper_candidate):
            score -= 20
        if " " in normalized:
            score -= 6
        return score

    def extract_from_text(text_block: str, require_model_anchor: bool) -> tuple[str | None, int]:

        normalized_lines = [line for line in (ln.strip() for ln in text_block.splitlines()) if line]

        best_candidate: str | None = None
        best_score = -10_000

        model_indexes = [idx for idx, line in enumerate(normalized_lines) if re.search(r"\bMODEL\b", line, re.IGNORECASE)]

        if require_model_anchor and model_indexes:
            for idx in model_indexes:
                line = normalized_lines[idx]
                tail = re.split(r"\bMODEL\b", line, maxsplit=1, flags=re.IGNORECASE)[-1]
                inner = extract_inner_model_token(tail)
                candidate_raw = inner if inner else tail
                candidate = normalize_model_token(candidate_raw)
                if candidate and is_likely_model(candidate):
                    score = score_candidate(candidate, near_model=True)
                    if score > best_score:
                        best_candidate, best_score = candidate, score

                for look_ahead in normalized_lines[idx + 1 : idx + 4]:
                    inner = extract_inner_model_token(look_ahead)
                    candidate_raw = inner if inner else look_ahead
                    candidate = normalize_model_token(candidate_raw)
                    if candidate and is_likely_model(candidate):
                        score = score_candidate(candidate, near_model=True)
                        if score > best_score:
                            best_candidate, best_score = candidate, score

            if best_candidate:
                return best_candidate, best_score

        if not require_model_anchor:
            for idx, raw_line in enumerate(normalized_lines):
                inner = extract_inner_model_token(raw_line)
                candidate_raw = inner if inner else raw_line
                candidate = normalize_model_token(candidate_raw)
                if candidate and is_likely_model(candidate):
                    score = score_candidate(candidate, near_model=False)
                    if score > best_score:
                        best_candidate, best_score = candidate, score

            if best_candidate:
                return best_candidate, best_score

        return best_candidate, best_score

    # --- Per-tier accumulators ---
    tier0_best: str | None = None
    tier0_score = -10_000
    tier1_best: str | None = None
    tier1_score = -10_000

    for angle in (0, 90, 180, 270):
        print(f"[DEBUG]  angle={angle} 回転開始")
        rotated_image = corrected_image.rotate(angle, expand=True)
        print(f"[DEBUG]  angle={angle} title_block_strips 開始")
        for _box, strip in title_block_strips(rotated_image):
            print(f"[DEBUG]   strip取得OK")
            # --- Tier 0: MODEL anchor ---
            print(f"[DEBUG]    find_model_boxes 開始")
            for box in find_model_boxes(strip):
                print(f"[DEBUG]     model_box取得OK")
                for crop in crop_model_region(strip, box):
                    print(f"[DEBUG]      crop_model_region OK, OCR前")
                    try:
                        ocr_result = ocr_text(crop, psm=6)
                        print(f"[DEBUG]      OCR OK, extract_from_text前")
                        candidate, score = extract_from_text(ocr_result, require_model_anchor=True)
                        print(f"[DEBUG]      extract_from_text OK: candidate={candidate}, score={score}")
                    except Exception as ocr_exc:
                        print(f"[ERROR]      OCR/抽出失敗: {ocr_exc}")
                        candidate, score = None, -99999
                    if candidate:
                        score += 15
                        if score > tier0_score:
                            tier0_best, tier0_score = candidate, score

            # --- Tier 1: Drawing-number anchor ---
            if drawing_number:
                print(f"[DEBUG]    find_drawing_number_boxes 開始")
                for box in find_drawing_number_boxes(strip, drawing_number):
                    print(f"[DEBUG]     drawing_number_box取得OK")
                    for crop in crop_model_region(strip, box):
                        print(f"[DEBUG]      crop_model_region OK, OCR前 (dwg)")
                        try:
                            ocr_result = ocr_text(crop, psm=6)
                            print(f"[DEBUG]      OCR OK, extract_from_text前 (dwg)")
                            candidate, score = extract_from_text(ocr_result, require_model_anchor=False)
                            print(f"[DEBUG]      extract_from_text OK (dwg): candidate={candidate}, score={score}")
                        except Exception as ocr_exc:
                            print(f"[ERROR]      OCR/抽出失敗 (dwg): {ocr_exc}")
                            candidate, score = None, -99999
                        if candidate:
                            score += 25
                            if score > tier1_score:
                                tier1_best, tier1_score = candidate, score
        print(f"[DEBUG]  angle={angle} 終了")
        # Full-page OCR is intentionally omitted to reduce false positives.

    if tier0_best is not None:
        return tier0_best, tier0_score, TIER_MODEL_ANCHOR
    if tier1_best is not None:
        return tier1_best, tier1_score, TIER_DWG_ANCHOR
    return None, -10_000, TIER_NONE


def extract_model_from_page(page) -> str | None:
    candidate, _score, _tier = extract_model_from_page_with_score(page)
    return candidate


def extract_model_from_document(doc, drawing_number: str | None = None) -> str | None:
    """Return best model across all pages, preferring Tier 0 pages over Tier 1."""
    tier0_results: list[tuple[str, int]] = []
    tier1_results: list[tuple[str, int]] = []

    for page in doc:
        candidate, score, tier = extract_model_from_page_with_score(page, drawing_number=drawing_number)
        if candidate is None:
            continue
        if tier == TIER_MODEL_ANCHOR:
            tier0_results.append((candidate, score))
        elif tier == TIER_DWG_ANCHOR:
            tier1_results.append((candidate, score))

    # Prefer pages where MODEL label was visible.
    if tier0_results:
        return max(tier0_results, key=lambda x: x[1])[0]
    if tier1_results:
        return max(tier1_results, key=lambda x: x[1])[0]
    return None


def iter_pdf_files(root: Path):
    print(f"[DEBUG] os.walk root={root}")
    for dirpath, _dirnames, filenames in os.walk(root):
        print(f"[DEBUG] dirpath={dirpath}, files={filenames}")
        for filename in filenames:
            if not filename.lower().endswith(".pdf"):
                continue
            path = Path(dirpath) / filename
            print(f"[DEBUG] found PDF: {path}")
            dwg = find_drawing_number(path)
            print(f"[DEBUG] find_drawing_number({path.name}) -> {dwg}")
            yield path, dwg


def extract_models(root: Path):
    results = []
    for pdf_path, dwg in iter_pdf_files(root):
        try:
            with fitz.open(pdf_path) as doc:
                if len(doc) == 0:
                    continue
                model = extract_model_from_document(doc, drawing_number=dwg) or "共通"
                results.append((dwg, model, pdf_path))
        except Exception as exc:
            results.append((dwg, f"[ERROR] {exc}", pdf_path))
    return results


def extract_models_to_csv(root: Path, out_csv: Path, limit: int = 0):
    """Stream processing: write CSV incrementally and print debug info.

    This avoids losing all results if processing aborts mid-run.
    """
    out_dir = os.path.abspath(str(out_csv))
    out_parent = os.path.dirname(out_dir)
    if out_parent and not os.path.exists(out_parent):
        try:
            os.makedirs(out_parent, exist_ok=True)
            print(f"[DEBUG] 出力先ディレクトリを作成しました: {out_parent}")
        except Exception as exc:
            print(f"[ERROR] 出力先ディレクトリの作成に失敗しました: {out_parent}: {exc}")
            raise

    results: list[tuple] = []
    count = 0

    # Open the CSV and write rows as we process each PDF
    try:
        fh = open(out_csv, "w", newline="", encoding="utf-8")
    except Exception as exc:
        print(f"[ERROR] CSVファイルを開けません: {out_csv}: {exc}")
        raise

    with fh:
        writer = csv.writer(fh)
        writer.writerow(["drawing", "model", "tier", "score", "page_idx", "pdf_path"])
        fh.flush()

        print(f"[DEBUG] iter_pdf_files({root}) START")
        for pdf_path, dwg in iter_pdf_files(root):
            if limit and count >= limit:
                break
            count += 1
            print(f"[DEBUG] ({count}) processing: {pdf_path} ({dwg})")
            try:
                with fitz.open(pdf_path) as doc:
                    page_results = []
                    for page_idx, page in enumerate(doc, start=1):
                        try:
                            candidate, score, tier = extract_model_from_page_with_score(page, drawing_number=dwg)
                        except Exception as ocr_exc:
                            print(f"[ERROR] page OCR failed for {pdf_path} page {page_idx}: {ocr_exc}")
                            candidate, score, tier = None, -99999, TIER_NONE
                        page_results.append((page_idx, candidate, score, tier))

                    tier0 = [(p,c,s,t) for (p,c,s,t) in page_results if c and t == TIER_MODEL_ANCHOR]
                    tier1 = [(p,c,s,t) for (p,c,s,t) in page_results if c and t == TIER_DWG_ANCHOR]
                    if tier0:
                        best = max(tier0, key=lambda x: x[2])
                    elif tier1:
                        best = max(tier1, key=lambda x: x[2])
                    else:
                        best = (None, None, -10000, TIER_NONE)

                    page_idx_sel, model_sel, score_sel, tier_sel = best
                    model_out = model_sel or "共通"

                    writer.writerow([dwg, model_out, tier_sel, score_sel, page_idx_sel or "", str(pdf_path)])
                    fh.flush()
                    results.append((dwg, model_out, tier_sel, score_sel, page_idx_sel, pdf_path))
            except Exception as exc:
                print(f"[ERROR] processing failed for {pdf_path}: {exc}")
                writer.writerow([dwg, f"[ERROR] {exc}", TIER_NONE, -100000, "", str(pdf_path)])
                fh.flush()
                results.append((dwg, f"[ERROR] {exc}", TIER_NONE, -100000, None, pdf_path))

    print(f"[DEBUG] CSV write finished: {out_csv} (written_rows={len(results)})")
    return results





def parse_args():
    parser = argparse.ArgumentParser(description="Extract MODEL values from drawing-number PDFs.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Search root folder")
    parser.add_argument("--limit", type=int, default=0, help="Limit output rows (0 = no limit)")
    parser.add_argument("--out-csv", type=Path, default=None, help="Write CSV results to this path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print(f"[DEBUG] START: root={args.root}, out_csv={args.out_csv}, limit={args.limit}")
    if args.out_csv:
        print("[DEBUG] extract_models_to_csv() called")
        extract_models_to_csv(args.root, args.out_csv, limit=args.limit)
        print("[DEBUG] extract_models_to_csv() finished")
        return 0
    print("[DEBUG] extract_models() called")
    results = extract_models(args.root)
    results.sort(key=lambda item: item[0])

    printed = 0
    for dwg, model, _pdf_path in results:
        print(f"{dwg}: {model}")
        printed += 1
        if args.limit and printed >= args.limit:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())