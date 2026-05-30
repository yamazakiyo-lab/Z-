import sys
import traceback
from pathlib import Path
import glob

print('--- スクリプト開始 ---')
try:
    # PDFファイル1件だけ取得
    pdfs = glob.glob(r'Z:/takachiho/2to9_業務別フォルダ/30_メーカー資料/あ行(あいうえお)/アイダエンジニアリング/調達/**/*.pdf', recursive=True)
    if not pdfs:
        print('PDFが見つかりません')
        sys.exit(1)
    target = pdfs[0]
    print(f'テスト対象: {target}')
    from extract_models_from_drawing_pdfs import extract_model_from_document
    import fitz
    with fitz.open(target) as doc:
        model = extract_model_from_document(doc)
    print(f'抽出結果: {model}')
    with open('extract_models_output.txt', 'w', encoding='utf-8') as f:
        f.write(f'{target}: {model}\n')
    print('ファイル出力完了')
except Exception as e:
    print('例外発生')
    with open('extract_models_error.txt', 'w', encoding='utf-8') as f:
        traceback.print_exc(file=f)
    print('エラー内容をextract_models_error.txtに出力')
