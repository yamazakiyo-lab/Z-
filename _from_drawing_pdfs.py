[1mdiff --git a/extract_models_from_drawing_pdfs.py b/extract_models_from_drawing_pdfs.py[m
[1mindex c2565a1..ed7b849 100644[m
[1m--- a/extract_models_from_drawing_pdfs.py[m
[1m+++ b/extract_models_from_drawing_pdfs.py[m
[36m@@ -1,8 +1,48 @@[m
[32m+[m[32m# --- 図面枠（タイトルブロック）自動検出＆回転補正 ---[m
[32m+[m[32mfrom __future__ import annotations[m
[32m+[m
[32m+[m[32mdef detect_title_block_and_correct_angle(image: Image.Image, debug_out: str = None) -> Image.Image:[m
[32m+[m[32m    """[m
[32m+[m[32m    OpenCVで最大の長方形輪郭（図面枠）を検出し、[m
[32m+[m[32m    その角度で画像を回転補正して返す。[m
[32m+[m[32m    debug_out: 補正後画像の保存パス（任意）[m
[32m+[m[32m    """[m
[32m+[m[32m    import cv2[m
[32m+[m[32m    import numpy as np[m
[32m+[m[32m    arr = np.array(image.convert("L"))[m
[32m+[m[32m    # 2値化[m
[32m+[m[32m    _, th = cv2.threshold(arr, 180, 255, cv2.THRESH_BINARY_INV)[m
[32m+[m[32m    # 輪郭抽出[m
[32m+[m[32m    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[m
[32m+[m[32m    max_area = 0[m
[32m+[m[32m    best_rect = None[m
[32m+[m[32m    for cnt in contours:[m
[32m+[m[32m        rect = cv2.minAreaRect(cnt)[m
[32m+[m[32m        (cx, cy), (w, h), angle = rect[m
[32m+[m[32m        area = w * h[m
[32m+[m[32m        if area > max_area and w > 100 and h > 100:[m
[32m+[m[32m            max_area = area[m
[32m+[m[32m            best_rect = rect[m
[32m+[m[32m    if best_rect is None:[m
[32m+[m[32m        # 検出失敗時はそのまま返す[m
[32m+[m[32m        return image[m
[32m+[m[32m    (cx, cy), (w, h), angle = best_rect[m
[32m+[m[32m    # OpenCVのminAreaRectのangleは-90〜0度[m
[32m+[m[32m    if w < h:[m
[32m+[m[32m        angle = angle + 90[m
[32m+[m[32m    # 回転補正[m
[32m+[m[32m    rot_mat = cv2.getRotationMatrix2D((cx, cy), angle, 1.0)[m
[32m+[m[32m    arr_color = np.array(image.convert("RGB"))[m
[32m+[m[32m    rotated = cv2.warpAffine(arr_color, rot_mat, (arr_color.shape[1], arr_color.shape[0]), flags=cv2.INTER_CUBIC, borderValue=(255,255,255))[m
[32m+[m[32m    # 補正後画像をPILに戻す[m
[32m+[m[32m    pil_rot = Image.fromarray(rotated)[m
[32m+[m[32m    if debug_out:[m
[32m+[m[32m        pil_rot.save(debug_out)[m
[32m+[m[32m    return pil_rot[m
 [m
 [m
 #[m
 #[m
[31m-from __future__ import annotations[m
 import os[m
 import re[m
 import argparse[m
[36m@@ -66,7 +106,13 @@[m [mTESSERACT_PATH = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")[m
 if TESSERACT_PATH.exists():[m
     pytesseract.pytesseract.tesseract_cmd = str(TESSERACT_PATH)[m
 [m
[31m-DEFAULT_ROOT = Path(r"Z:\takachiho\2to9_業務別フォルダ\30_メーカー資料\あ行(あいうえお)\アイダエンジニアリング\新品仕込機")[m
[32m+[m[32mDEFAULT_ROOT = Path(r"Z:\takachiho\2to9_業務別フォルダ\30_メーカー資料\あ行(あいうえお)\アイダエンジニアリング\調達")[m
[32m+[m[32mprint(f"[DEBUG] DEFAULT_ROOT={DEFAULT_ROOT}")[m
[32m+[m[32mprint(f"[DEBUG] DEFAULT_ROOT exists={DEFAULT_ROOT.exists()}")[m
[32m+[m[32mprint(f"[DEBUG] DEFAULT_ROOT is_dir={DEFAULT_ROOT.is_dir()}")[m
[32m+[m[32mprint(f"[DEBUG] DEFAULT_ROOT={DEFAULT_ROOT}")[m
[32m+[m[32mprint(f"[DEBUG] DEFAULT_ROOT exists={DEFAULT_ROOT.exists()}")[m
[32m+[m[32mprint(f"[DEBUG] DEFAULT_ROOT is_dir={DEFAULT_ROOT.is_dir()}")[m
 DWG_RE = re.compile(r"^(\d{3}-\d{4,5}(?:-[a-z])?)", re.IGNORECASE)[m
 [m
 [m
[36m@@ -119,6 +165,9 @@[m [mdef prepare_for_ocr(image: Image.Image) -> Image.Image:[m
 [m
 [m
 MODEL_FUZZY_RE = re.compile(r"^M[O0][D][E3][L1I]$", re.IGNORECASE)[m
[32m+[m[32m_MODEL_SHORT_ALLOWLIST = {"TMX"}[m
[32m+[m[32m_MODEL_SHORT_NOISE_RE = re.compile(r"^[A-Z]{2,5}$")[m
[32m+[m[32m_MODEL_SINGLE_PREFIX_CODE_RE = re.compile(r"^[A-Z]-\d{2,6}(?:/\d{2,6})?$")[m
 [m
 [m
 def normalize_drawing_number(value: str) -> str:[m
[36m@@ -132,12 +181,18 @@[m [mdef title_block_strips(image: Image.Image) -> list[tuple[tuple[int, int, int, in[m
     most magnified title-block slice, then wider fallbacks, then the full image.[m
     """[m
     width, height = image.size[m
[31m-    return [[m
[32m+[m[32m    # 右下横長、右端縦長、下端横長、全体[m
[32m+[m[32m    strips = [[m
         ((int(width * 0.80), int(height * 0.75), width, height), image.crop((int(width * 0.80), int(height * 0.75), width, height))),[m
         ((int(width * 0.85), 0, width, height), image.crop((int(width * 0.85), 0, width, height))),[m
         ((0, int(height * 0.80), width, height), image.crop((0, int(height * 0.80), width, height))),[m
         ((0, 0, width, height), image),[m
     ][m
[32m+[m[32m    # 右上コーナーの縦長領域（MODELラベルが縦書きで入るパターン対応）[m
[32m+[m[32m    strips.append(((int(width * 0.80), 0, width, int(height * 0.25)), image.crop((int(width * 0.80), 0, width, int(height * 0.25)))))[m
[32m+[m[32m    # 右上コーナーのさらに狭い縦長領域（よりピンポイント）[m
[32m+[m[32m    strips.append(((int(width * 0.90), 0, width, int(height * 0.20)), image.crop((int(width * 0.90), 0, width, int(height * 0.20)))))[m
[32m+[m[32m    return strips[m
 [m
 [m
 def _data_to_lines(data: dict) -> list[dict]:[m
[36m@@ -273,8 +328,18 @@[m [mdef extract_model_from_page_with_score([m
     Full-page OCR is intentionally NOT used as a candidate source to avoid[m
     picking up unrelated model-like strings from notes and parts lists.[m
     """[m
[32m+[m
     full_pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)[m
     full_image = Image.frombytes("RGB", [full_pix.width, full_pix.height], full_pix.samples)[m
[32m+[m[32m    # --- タイトルブロック検出＆回転補正 ---[m
[32m+[m[32m    try:[m
[32m+[m[32m        corrected_image = detect_title_block_and_correct_angle(full_image)[m
[32m+[m[32m        print("[DEBUG] detect_title_block_and_correct_angle 適用済み")[m
[32m+[m[32m    except Exception as exc:[m
[32m+[m[32m        print(f"[WARN] detect_title_block_and_correct_angle 失敗: {exc}")[m
[32m+[m[32m        corrected_image = full_image[m
[32m+[m
[32m+[m[32m    # 以降は corrected_image を使う[m
 [m
     def normalize_candidate(candidate: str) -> str:[m
         candidate = candidate.strip().strip("-_/ ")[m
[36m@@ -295,6 +360,12 @@[m [mdef extract_model_from_page_with_score([m
             return False[m
         if any(token in upper_candidate for token in ("PART", "DWG", "DATE", "SCALE", "MAT", "QTY", "REVISION", "SIGN", "NO", "NAME", "MODEL")):[m
             return False[m
[32m+[m[32m        if upper_candidate in _MODEL_SHORT_ALLOWLIST:[m
[32m+[m[32m            return True[m
[32m+[m[32m        if _MODEL_SHORT_NOISE_RE.fullmatch(upper_candidate):[m
[32m+[m[32m            return False[m
[32m+[m[32m        if _MODEL_SINGLE_PREFIX_CODE_RE.fullmatch(upper_candidate):[m
[32m+[m[32m            return False[m
         if upper_candidate.count("(") != upper_candidate.count(")"):[m
             return False[m
         if " " in candidate:[m
[36m@@ -309,8 +380,6 @@[m [mdef extract_model_from_page_with_score([m
             return False[m
         if re.fullmatch(r"[A-Z0-9.\-_/]+\([A-Z0-9]+\)", upper_candidate):[m
             return False[m
[31m-        if re.fullmatch(r"[A-Z]{2,5}", upper_candidate):[m
[31m-            return True[m
         if re.fullmatch(r"[A-Z]{1,5}\([^)]+\)", candidate):[m
             return True[m
         if re.fullmatch(r"[A-Z]{1,4}\d{2,5}", upper_candidate):[m
[36m@@ -404,13 +473,25 @@[m [mdef extract_model_from_page_with_score([m
     tier1_score = -10_000[m
 [m
     for angle in (0, 90, 180, 270):[m
[31m-        rotated_image = full_image.rotate(angle, expand=True)[m
[31m-[m
[32m+[m[32m        print(f"[DEBUG]  angle={angle} 回転開始")[m
[32m+[m[32m        rotated_image = corrected_image.rotate(angle, expand=True)[m
[32m+[m[32m        print(f"[DEBUG]  angle={angle} title_block_strips 開始")[m
         for _box, strip in title_block_strips(rotated_image):[m
[32m+[m[32m            print(f"[DEBUG]   strip取得OK")[m
             # --- Tier 0: MODEL anchor ---[m
[32m+[m[32m            print(f"[DEBUG]    find_model_boxes 開始")[m
             for box in find_model_boxes(strip):[m
[32m+[m[32m                print(f"[DEBUG]     model_box取得OK")[m
                 for crop in crop_model_region(strip, box):[m
[31m-                    candidate, score = extract_from_text(ocr_text(crop, psm=6), require_model_anchor=True)[m
[32m+[m[32m                    print(f"[DEBUG]      crop_model_region OK, OCR前")[m
[32m+[m[32m                    try:[m
[32m+[m[32m                        ocr_result = ocr_text(crop, psm=6)[m
[32m+[m[32m                        print(f"[DEBUG]      OCR OK, extract_from_text前")[m
[32m+[m[32m                        candidate, score = extract_from_text(ocr_result, require_model_anchor=True)[m
[32m+[m[32m                        print(f"[DEBUG]      extract_from_text OK: candidate={candidate}, score={score}")[m
[32m+[m[32m                    except Exception as ocr_exc:[m
[32m+[m[32m                        print(f"[ERROR]      OCR/抽出失敗: {ocr_exc}")[m
[32m+[m[32m                        candidate, score = None, -99999[m
                     if candidate:[m
                         score += 15[m
                         if score > tier0_score:[m
[36m@@ -418,14 +499,24 @@[m [mdef extract_model_from_page_with_score([m
 [m
             # --- Tier 1: Drawing-number anchor ---[m
             if drawing_number:[m
[32m+[m[32m                print(f"[DEBUG]    find_drawing_number_boxes 開始")[m
                 for box in find_drawing_number_boxes(strip, drawing_number):[m
[32m+[m[32m                    print(f"[DEBUG]     drawing_number_box取得OK")[m
                     for crop in crop_model_region(strip, box):[m
[31m-                        candidate, score = extract_from_text(ocr_text(crop, psm=6), require_model_anchor=False)[m
[32m+[m[32m                        print(f"[DEBUG]      crop_model_region OK, OCR前 (dwg)")[m
[32m+[m[32m                        try:[m
[32m+[m[32m                            ocr_result = ocr_text(crop, psm=6)[m
[32m+[m[32m                            print(f"[DEBUG]      OCR OK, extract_from_text前 (dwg)")[m
[32m+[m[32m                            candidate, score = extract_from_text(ocr_result, require_model_anchor=False)[m
[32m+[m[32m                            print(f"[DEBUG]      extract_from_text OK (dwg): candidate={candidate}, score={score}")[m
[32m+[m[32m                        except Exception as ocr_exc:[m
[32m+[m[32m                            print(f"[ERROR]      OCR/抽出失敗 (dwg): {ocr_exc}")[m
[32m+[m[32m                            candidate, score = None, -99999[m
                         if candidate:[m
                             score += 25[m
                             if score > tier1_score:[m
                                 tier1_best, tier1_score = candidate, score[m
[31m-[m
[32m+[m[32m        print(f"[DEBUG]  angle={angle} 終了")[m
         # Full-page OCR is intentionally omitted to reduce false positives.[m
 [m
     if tier0_best is not None:[m
[36m@@ -463,14 +554,17 @@[m [mdef extract_model_from_document(doc, drawing_number: str | None = None) -> str |[m
 [m
 [m
 def iter_pdf_files(root: Path):[m
[32m+[m[32m    print(f"[DEBUG] os.walk root={root}")[m
     for dirpath, _dirnames, filenames in os.walk(root):[m
[32m+[m[32m        print(f"[DEBUG] dirpath={dirpath}, files={filenames}")[m
         for filename in filenames:[m
             if not filename.lower().endswith(".pdf"):[m
                 continue[m
             path = Path(dirpath) / filename[m
[32m+[m[32m            print(f"[DEBUG] found PDF: {path}")[m
             dwg = find_drawing_number(path)[m
[31m-            if dwg:[m
[31m-                yield path, dwg[m
[32m+[m[32m            print(f"[DEBUG] find_drawing_number({path.name}) -> {dwg}")[m
[32m+[m[32m            yield path, dwg[m
 [m
 [m
 def extract_models(root: Path):[m
[36m@@ -488,39 +582,80 @@[m [mdef extract_models(root: Path):[m
 [m
 [m
 def extract_models_to_csv(root: Path, out_csv: Path, limit: int = 0):[m
[31m-    results = [][m
[31m-    count = 0[m
[31m-    for pdf_path, dwg in iter_pdf_files(root):[m
[31m-        if limit and count >= limit:[m
[31m-            break[m
[31m-        count += 1[m
[32m+[m[32m    """Stream processing: write CSV incrementally and print debug info.[m
[32m+[m
[32m+[m[32m    This avoids losing all results if processing aborts mid-run.[m
[32m+[m[32m    """[m
[32m+[m[32m    out_dir = os.path.abspath(str(out_csv))[m
[32m+[m[32m    out_parent = os.path.dirname(out_dir)[m
[32m+[m[32m    if out_parent and not os.path.exists(out_parent):[m
         try:[m
[31m-            with fitz.open(pdf_path) as doc:[m
[31m-                page_results = [][m
[31m-                for page_idx, page in enumerate(doc, start=1):[m
[31m-                    candidate, score, tier = extract_model_from_page_with_score(page, drawing_number=dwg)[m
[31m-                    page_results.append((page_idx, candidate, score, tier))[m
[31m-                tier0 = [(p,c,s,t) for (p,c,s,t) in page_results if c and t == TIER_MODEL_ANCHOR][m
[31m-                tier1 = [(p,c,s,t) for (p,c,s,t) in page_results if c and t == TIER_DWG_ANCHOR][m
[31m-                if tier0:[m
[31m-                    best = max(tier0, key=lambda x: x[2])[m
[31m-                elif tier1:[m
[31m-                    best = max(tier1, key=lambda x: x[2])[m
[31m-                else:[m
[31m-                    best = (None, None, -10000, TIER_NONE)[m
[31m-                page_idx_sel, model_sel, score_sel, tier_sel = best[m
[31m-                model_out = model_sel or "共通"[m
[31m-                results.append((dwg, model_out, tier_sel, score_sel, page_idx_sel, pdf_path))[m
[32m+[m[32m            os.makedirs(out_parent, exist_ok=True)[m
[32m+[m[32m            print(f"[DEBUG] 出力先ディレクトリを作成しました: {out_parent}")[m
         except Exception as exc:[m
[31m-            results.append((dwg, f"[ERROR] {exc}", TIER_NONE, -100000, None, pdf_path))[m
[31m-    with open(out_csv, "w", newline="", encoding="utf-8") as fh:[m
[32m+[m[32m            print(f"[ERROR] 出力先ディレクトリの作成に失敗しました: {out_parent}: {exc}")[m
[32m+[m[32m            raise[m
[32m+[m
[32m+[m[32m    results: list[tuple] = [][m
[32m+[m[32m    count = 0[m
[32m+[m
[32m+[m[32m    # Open the CSV and write rows as we process each PDF[m
[32m+[m[32m    try:[m
[32m+[m[32m        fh = open(out_csv, "w", newline="", encoding="utf-8")[m
[32m+[m[32m    except Exception as exc:[m
[32m+[m[32m        print(f"[ERROR] CSVファイルを開けません: {out_csv}: {exc}")[m
[32m+[m[32m        raise[m
[32m+[m
[32m+[m[32m    with fh:[m
         writer = csv.writer(fh)[m
         writer.writerow(["drawing", "model", "tier", "score", "page_idx", "pdf_path"])[m
[31m-        for row in results:[m
[31m-            writer.writerow([row[0], row[1], row[2], row[3], row[4] or "", str(row[5])])[m
[32m+[m[32m        fh.flush()[m
[32m+[m
[32m+[m[32m        print(f"[DEBUG] iter_pdf_files({root}) START")[m
[32m+[m[32m        for pdf_path, dwg in iter_pdf_files(root):[m
[32m+[m[32m            if limit and count >= limit:[m
[32m+[m[32m                break[m
[32m+[m[32m            count += 1[m
[32m+[m[32m            print(f"[DEBUG] ({count}) processing: {pdf_path} ({dwg})")[m
[32m+[m[32m            try:[m
[32m+[m[32m                with fitz.open(pdf_path) as doc:[m
[32m+[m[32m                    page_results = [][m
[32m+[m[32m                    for page_idx, page in enumerate(doc, start=1):[m
[32m+[m[32m                        try:[m
[32m+[m[32m                            candidate, score, tier = extract_model_from_page_with_score(page, drawing_number=dwg)[m
[32m+[m[32m                        except Exception as ocr_exc:[m
[32m+[m[32m                            print(f"[ERROR] page OCR failed for {pdf_path} page {page_idx}: {ocr_exc}")[m
[32m+[m[32m                            candidate, score, tier = None, -99999, TIER_NONE[m
[32m+[m[32m                        page_results.append((page_idx, candidate, score, tier))[m
[32m+[m
[32m+[m[32m                    tier0 = [(p,c,s,t) for (p,c,s,t) in page_results if c and t == TIER_MODEL_ANCHOR][m
[32m+[m[32m                    tier1 = [(p,c,s,t) for (p,c,s,t) in page_results if c and t == TIER_DWG_ANCHOR][m
[32m+[m[32m                    if tier0:[m
[32m+[m[32m                        best = max(tier0, key=lambda x: x[2])[m
[32m+[m[32m                    elif tier1:[m
[32m+[m[32m                        best = max(tier1, key=lambda x: x[2])[m
[32m+[m[32m                    else:[m
[32m+[m[32m                        best = (None, None, -10000, TIER_NONE)[m
[32m+[m
[32m+[m[32m                    page_idx_sel, model_sel, score_sel, tier_sel = best[m
[32m+[m[32m                    model_out = model_sel or "共通"[m
[32m+[m
[32m+[m[32m                    writer.writerow([dwg, model_out, tier_sel, score_sel, page_idx_sel or "", str(pdf_path)])[m
[32m+[m[32m                    fh.flush()[m
[32m+[m[32m                    results.append((dwg, model_out, tier_sel, score_sel, page_idx_sel, pdf_path))[m
[32m+[m[32m            except Exception as exc:[m
[32m+[m[32m                print(f"[ERROR] processing failed for {pdf_path}: {exc}")[m
[32m+[m[32m                writer.writerow([dwg, f"[ERROR] {exc}", TIER_NONE, -100000, "", str(pdf_path)])[m
[32m+[m[32m                fh.flush()[m
[32m+[m[32m                results.append((dwg, f"[ERROR] {exc}", TIER_NONE, -100000, None, pdf_path))[m
[32m+[m
[32m+[m[32m    print(f"[DEBUG] CSV write finished: {out_csv} (written_rows={len(results)})")[m
     return results[m
 [m
 [m
[32m+[m
[32m+[m
[32m+[m
 def parse_args():[m
     parser = argparse.ArgumentParser(description="Extract MODEL values from drawing-number PDFs.")[m
     parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Search root folder")[m
[36m@@ -531,9 +666,13 @@[m [mdef parse_args():[m
 [m
 def main() -> int:[m
     args = parse_args()[m
[32m+[m[32m    print(f"[DEBUG] START: root={args.root}, out_csv={args.out_csv}, limit={args.limit}")[m
     if args.out_csv:[m
[32m+[m[32m        print("[DEBUG] extract_models_to_csv() called")[m
         extract_models_to_csv(args.root, args.out_csv, limit=args.limit)[m
[32m+[m[32m        print("[DEBUG] extract_models_to_csv() finished")[m
         return 0[m
[32m+[m[32m    print("[DEBUG] extract_models() called")[m
     results = extract_models(args.root)[m
     results.sort(key=lambda item: item[0])[m
 [m
