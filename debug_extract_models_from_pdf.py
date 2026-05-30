from __future__ import annotations

import argparse
import re
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

from extract_models_from_drawing_pdfs import (
    crop_model_region,
    find_drawing_number,
    find_drawing_number_boxes,
    find_model_boxes,
    ocr_text,
)


def normalize_candidate(candidate: str) -> str:
    candidate = candidate.strip().strip("-_/ ")
    candidate = candidate.replace("×", "x")
    candidate = candidate.replace("%", "x")
    candidate = candidate.replace("#", "特")
    candidate = re.sub(r"\s+", "", candidate)
    return candidate


def rejection_reason(candidate: str) -> str | None:
    if not candidate:
        return "empty"
    if not candidate[0].isalpha() or not candidate[0].isupper():
        return "not uppercase alpha start"

    upper_candidate = normalize_candidate(candidate).upper()
    if re.search(r"[^A-Z0-9()./\-X特]", upper_candidate):
        return "contains unsupported characters"
    if any(token in upper_candidate for token in ("PART", "DWG", "DATE", "SCALE", "MAT", "QTY", "REVISION", "SIGN", "NO", "NAME", "MODEL")):
        return "contains blocked keyword"
    if upper_candidate.count("(") != upper_candidate.count(")"):
        return "unbalanced parentheses"
    if " " in candidate:
        return "contains spaces"
    if len(upper_candidate) < 2:
        return "too short"
    if not re.search(r"[A-Z0-9]", upper_candidate):
        return "no alnum"
    if len(upper_candidate) > 12 and not re.search(r"[-/()]", upper_candidate):
        return "too long without separator"
    if re.search(r"\d", upper_candidate) and len(upper_candidate) > 6 and not re.search(r"[-/()]", upper_candidate):
        return "digits but no separator"
    if re.fullmatch(r"[A-Z0-9.\-_/]+\([A-Z0-9]+\)", upper_candidate):
        return "looks like annotation"
    if re.fullmatch(r"[A-Z]{2,5}", upper_candidate):
        return None
    if re.fullmatch(r"[A-Z]{1,5}\([^)]+\)", candidate):
        return None
    if re.fullmatch(r"[A-Z]{1,4}\d{2,5}", upper_candidate):
        return None
    if re.fullmatch(r"[A-Z]{1,4}\d{2,5}-[A-Z0-9]{1,3}", upper_candidate):
        return None
    if re.fullmatch(r"[A-Z]{1,5}-\d{2,6}(?:[A-Z0-9xX%./]*)?", upper_candidate):
        return None
    if re.fullmatch(r"[A-Z]{1,5}-[A-Z0-9]{1,4}", upper_candidate):
        return None
    if re.fullmatch(r"[A-Z]\.[A-Z]-\d+/\d+[A-Z0-9xX%./]*", upper_candidate):
        return None
    return "does not match allowed model patterns"


def candidate_reasons(text_block: str) -> list[tuple[str, str | None]]:
    lines = [normalize_candidate(line) for line in text_block.splitlines()]
    lines = [line for line in lines if line]
    return [(line, rejection_reason(line)) for line in lines]


def save_image(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def print_ocr_preview(text: str, prefix: str = "    ", max_lines: int = 24) -> None:
    lines = [line for line in text.splitlines() if line.strip()]
    preview = lines[:max_lines]
    for line in preview:
        print(f"{prefix}{line}")
    if len(lines) > max_lines:
        print(f"{prefix}... ({len(lines) - max_lines} more lines)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Debug model extraction from one PDF.")
    parser.add_argument("pdf", type=Path, help="PDF file to inspect")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("debug_extract_models_output"),
        help="Directory where crops and OCR text are written",
    )
    args = parser.parse_args()

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    with fitz.open(args.pdf) as doc:
        print(f"PDF: {args.pdf}")
        print(f"Pages: {len(doc)}")
        drawing_number = find_drawing_number(args.pdf) or ""
        if drawing_number:
            print(f"Drawing number: {drawing_number}")

        for page_index, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
            base_image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            print()
            print(f"=== Page {page_index} ===")

            for angle in (0, 90, 180, 270):
                rotated = base_image.rotate(angle, expand=True)
                page_prefix = output_dir / f"page{page_index:03d}_rot{angle:03d}"

                full_text = ocr_text(rotated, psm=11)
                (page_prefix.with_suffix(".txt")).write_text(full_text, encoding="utf-8")

                drawing_number_boxes = find_drawing_number_boxes(rotated, drawing_number) if drawing_number else []
                model_boxes = find_model_boxes(rotated)

                print(f"Rotation {angle}: drawing-number boxes={len(drawing_number_boxes)} model boxes={len(model_boxes)}")

                for box_index, box in enumerate(drawing_number_boxes, start=1):
                    crops = crop_model_region(rotated, box)
                    for crop_index, crop in enumerate(crops, start=1):
                        crop_path = page_prefix.with_name(f"{page_prefix.name}_drawingbox{box_index}_crop{crop_index}.png")
                        save_image(crop, crop_path)
                        crop_text = ocr_text(crop, psm=6)
                        print(f"  drawing box {box_index} crop {crop_index}: {crop_path.name}")
                        print("  OCR:")
                        print_ocr_preview(crop_text)

                for box_index, box in enumerate(model_boxes, start=1):
                    crops = crop_model_region(rotated, box)
                    for crop_index, crop in enumerate(crops, start=1):
                        crop_path = page_prefix.with_name(f"{page_prefix.name}_modelbox{box_index}_crop{crop_index}.png")
                        save_image(crop, crop_path)
                        crop_text = ocr_text(crop, psm=6)
                        print(f"  model box {box_index} crop {crop_index}: {crop_path.name}")
                        print("  OCR:")
                        print_ocr_preview(crop_text)
                        for line, reason in candidate_reasons(crop_text):
                            status = "KEEP" if reason is None else f"DROP ({reason})"
                            print(f"    {status}: {line}")

                print("  Full page OCR candidates:")
                for line, reason in candidate_reasons(full_text):
                    status = "KEEP" if reason is None else f"DROP ({reason})"
                    print(f"    {status}: {line}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())