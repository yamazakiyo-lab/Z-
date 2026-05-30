"""Diagnostic: print raw OCR text from title-block strips for one PDF.

Usage:
    python debug_strip_ocr.py <pdf_path> [drawing_number]

Example:
    python debug_strip_ocr.py "Z:\...\\207-35987_A191a_WEIGHT.pdf" 207-35987
"""
from __future__ import annotations

import sys
from pathlib import Path

import fitz
import pytesseract
from PIL import Image, ImageFilter, ImageOps


TESSERACT_PATH = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
if TESSERACT_PATH.exists():
    pytesseract.pytesseract.tesseract_cmd = str(TESSERACT_PATH)


def prepare_for_ocr(image: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(image)
    gray = gray.filter(ImageFilter.SHARPEN)
    lo, hi = gray.getextrema()
    threshold = lo + (hi - lo) // 2
    binary = gray.point(lambda p: 255 if p > threshold else 0)
    return binary.convert("RGB")


def strips(image: Image.Image) -> list[tuple[str, Image.Image]]:
    w, h = image.size
    return [
        ("quadrant_br_25pct", image.crop((int(w * 0.75), int(h * 0.75), w, h))),
        ("right_15pct", image.crop((int(w * 0.85), 0, w, h))),
        ("right_20pct", image.crop((int(w * 0.80), 0, w, h))),
        ("bottom_20pct", image.crop((0, int(h * 0.80), w, h))),
        ("full_page", image),
    ]


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python debug_strip_ocr.py <pdf_path> [drawing_number]")
        return 1

    pdf_path = Path(sys.argv[1])
    dwg = sys.argv[2] if len(sys.argv) > 2 else ""

    with fitz.open(pdf_path) as doc:
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
        full_image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

    for angle in (0, 90, 180, 270):
        rotated = full_image.rotate(angle, expand=True)
        print(f"\n{'=' * 60}")
        print(f"ANGLE {angle}")
        print(f"{'=' * 60}")

        for name, strip in strips(rotated):
            proc = prepare_for_ocr(strip)
            for psm in (6, 11):
                text = pytesseract.image_to_string(proc, lang="eng", config=f"--psm {psm}")
                text = text.strip()
                if not text:
                    continue

                has_model = "model" in text.lower()
                has_dwg = dwg and (dwg in text or dwg.split("-")[-1] in text)
                if has_model or has_dwg:
                    print(f"\n--- {name} / psm={psm} ---")
                    print(text[:800])
                    break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())