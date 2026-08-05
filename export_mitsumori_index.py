"""過去見積書(231_見積書)をAI検索インデックスに取り込む。

AI Q&Aが「○○社のNC1-110整備、過去いくらで出してる？」等の質問に、
過去見積の件名・日付・金額・ファイル所在を根拠に答えられるようにする。
回答できるのは見積作成メンバー(ai_qa.py側で質問者を制限)のみ。

方式: xlsx見積の全シート(最大30枚)から明細本文を、先頭シートから宛先・件名・
      日付・合計金額をヒューリスティックに抽出し、1見積=1ドキュメントで
      photo-index に media_type="mitsumori" でupsert。差分更新(状態ファイル)。
      旧xls・PDFは第2期(未対応。件数のみログ)。

実行(デスクトップ):
    python export_mitsumori_index.py --dry-run --limit 20  # 抽出結果の確認
    python export_mitsumori_index.py                       # 差分取り込み
    python export_mitsumori_index.py --full                # 全量作り直し
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"), encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(os.environ.get(
    "MITSUMORI_DIR", r"Z:\takachiho\2to9_業務別フォルダ\23_顧客・納入先別\231_見積書"))
MEDIA_TYPE = "mitsumori"
STATE_PATH = Path(__file__).with_name("mitsumori_index_state.json")
BATCH = 100

RX_GYO = re.compile(r"^[あかさたなはまやらわ]行$")
RX_WORKNO = re.compile(r"([A-Z]{0,4}\d{3,6}-\d{2})", re.IGNORECASE)

# ── 見積用語カタログ(明細から自動抽出→Blob mitsumori_terms.json) ─────────────
# 規程・現場カタログに続く4つ目の辞書。AI Q&Aのキーワード変換に注入される。
TERMS_COUNTS_PATH = Path(__file__).with_name("mitsumori_terms_counts.json")
TERMS_BLOB = "mitsumori_terms.json"
TERMS_MIN_COUNT = 3   # 明細は繰り返しが多いためノイズ除けに3回以上
RX_KATAKANA = re.compile(r"[ァ-ヴー]{3,}")
RX_MODEL = re.compile(r"[A-Za-z][A-Za-z\-]{0,8}[0-9][A-Za-z0-9\-]{0,14}")
RX_WORK = re.compile(
    r"[一-龠ァ-ヴーA-Za-z0-9]{1,8}"
    r"(?:交換|整備|修理|組立|組付|分解|洗浄|塗装|溶接|研磨|研削|調整|点検|測定|"
    r"芯出し|取付|取外し|加工|補修|清掃|据付|搬入|搬出|改造|更新)")
TERMS_STOP = {"ミツモリ", "ゴウケイ", "ショウヒゼイ"}


def _customer_from_path(rel: Path) -> str:
    parts = rel.parts
    if parts and RX_GYO.match(parts[0]):
        return parts[1] if len(parts) > 2 else ""
    return parts[0] if len(parts) > 1 else ""


def _extract(p: Path) -> dict:
    """全シートから明細本文を、先頭シートから宛先・件名・日付・合計金額を抽出。

    明細本文(body): 各シート(最大30枚)の文字列セルを行単位で連結。機械加工
    見積のようにシートが大量にあるブックにも対応(2026-08-05: 従来は先頭シート
    のみ・1800字で、2枚目以降の明細が検索に載っていなかった)。複数シート時は
    「◆シート名」の見出し付き。シートごと300行×20列・2000字、全体9000字まで。
    """
    import openpyxl
    atesaki = kenmei = date_s = ""
    max_num = 0.0
    label_next = False
    body_parts: list[str] = []
    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    try:
        multi = len(wb.worksheets) > 1
        for si, ws in enumerate(wb.worksheets[:30]):
            lines: list[str] = []
            for row in ws.iter_rows(min_row=1, max_row=300, max_col=20):
                row_texts: list[str] = []
                for c in row:
                    v = c.value
                    if v is None:
                        continue
                    if si == 0 and label_next and isinstance(v, str) and v.strip():
                        kenmei = kenmei or v.strip()[:60]
                        label_next = False
                    s = str(v).strip()
                    if not s:
                        continue
                    if isinstance(v, str):
                        row_texts.append(s)
                    if si == 0:
                        # ヘッダー情報は先頭シートからのみ抽出
                        if ((s.endswith("様") or s.endswith("御中"))
                                and not atesaki and len(s) <= 40):
                            atesaki = s
                        if isinstance(v, str) and re.fullmatch(r"件\s*名|工事名|品\s*名", s):
                            label_next = True  # 次の非空セルを件名とみなす
                        if hasattr(v, "year") and not date_s:
                            try:
                                date_s = v.strftime("%Y-%m-%d")
                            except Exception:
                                pass
                        if isinstance(v, (int, float)) and not isinstance(v, bool):
                            if v > max_num:
                                max_num = float(v)
                joined = " ".join(row_texts)
                if len(joined) >= 4:
                    lines.append(joined)
            if lines:
                sheet_text = "\n".join(lines)[:2000]
                if multi:
                    sheet_text = f"◆シート「{ws.title}」\n" + sheet_text
                body_parts.append(sheet_text)
            if sum(len(x) for x in body_parts) >= 9000:
                break
    finally:
        wb.close()
    body = "\n".join(body_parts)[:9000]
    return {"atesaki": atesaki, "kenmei": kenmei, "date": date_s,
            "gokei": int(max_num) if max_num >= 1000 else 0, "body": body}


def _doc_for(p: Path, rel: Path, indexed_at: str) -> tuple[dict, str]:
    """(検索ドキュメント, 明細テキスト) を返す。"""
    info = _extract(p)
    customer = _customer_from_path(rel)
    kenmei = info["kenmei"] or p.stem
    date_s = info["date"]
    if not date_s:
        try:
            date_s = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
        except OSError:
            date_s = ""
    m = RX_WORKNO.search(p.stem) or RX_WORKNO.search(kenmei)
    workno = m.group(1) if m else ""
    gokei = f"{info['gokei']:,}円" if info["gokei"] else "(金額抽出不可)"
    text = (f"【過去見積】顧客: {customer} / 宛先: {info['atesaki'] or '不明'} / "
            f"件名: {kenmei} / 見積日: {date_s or '不明'} / 合計金額: {gokei}"
            + (f" / 工番: {workno}" if workno else "")
            + f"\nファイル: {p}"
            + (f"\n明細:\n{info['body']}" if info.get("body") else ""))
    doc = {
        "id": hashlib.sha256(f"mitsumori::{rel}".encode("utf-8")).hexdigest(),
        "file_path": str(p),
        "file_name": p.name,
        "workno": workno,
        "workno_name": f"{customer}/{kenmei}"[:100],
        "phase": "",
        "media_type": MEDIA_TYPE,
        "capture_date": None,
        "capture_date_raw": date_s,
        "extension": p.suffix.lower(),
        "folder_path": str(p.parent),
        "indexed_at": indexed_at,
        "content_text": text,
    }
    return doc, info.get("body", "")


def main() -> int:
    ap = argparse.ArgumentParser(description="過去見積のインデックス取り込み")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="dry-run時の件数制限")
    ap.add_argument("--full", action="store_true", help="状態を無視して全量再取込")
    args = ap.parse_args()

    if not ROOT.is_dir():
        print(f"[ERROR] {ROOT}", file=sys.stderr)
        return 1

    state = {}
    if STATE_PATH.exists() and not args.full:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))

    targets = []
    skipped_old = pdf_n = xls_n = 0
    current_keys = set()
    for dirpath, _, filenames in os.walk(ROOT):
        for fn in filenames:
            if fn.startswith("~$"):
                continue
            low = fn.lower()
            p = Path(dirpath) / fn
            if low.endswith(".pdf"):
                pdf_n += 1
                continue
            if low.endswith(".xls"):
                xls_n += 1
                continue
            if not low.endswith(".xlsx"):
                continue
            rel = p.relative_to(ROOT)
            key = str(rel)
            current_keys.add(key)
            try:
                mt = p.stat().st_mtime
            except OSError:
                continue
            if state.get(key) == mt:
                skipped_old += 1
                continue
            targets.append((p, rel, mt))

    removed = [k for k in state if k not in current_keys]
    print(f"[SCAN] 対象xlsx 新規/更新 {len(targets):,} 件 / 変更なし {skipped_old:,} 件 / "
          f"削除 {len(removed):,} 件 (未対応: 旧xls {xls_n:,}・PDF {pdf_n:,})")

    if args.dry_run:
        n = args.limit or 20
        indexed_at = datetime.now(timezone.utc).isoformat()
        for p, rel, _ in targets[:n]:
            try:
                d, body = _doc_for(p, rel, indexed_at)
                print(f"--- {rel}\n    {d['content_text'].splitlines()[0]}")
                if body:
                    print(f"    明細冒頭: {body[:120]}")
            except Exception as e:
                print(f"--- {rel}\n    [失敗] {type(e).__name__}: {e}")
        return 0

    from azure.core.credentials import AzureKeyCredential
    from azure.search.documents import SearchClient
    from rag.config import SEARCH_INDEX_NAME, ensure_search_credentials

    endpoint, api_key = ensure_search_credentials()
    client = SearchClient(endpoint, SEARCH_INDEX_NAME, AzureKeyCredential(api_key))

    if args.full:
        old_ids = [r["id"] for r in client.search(
            search_text="*", filter=f"media_type eq '{MEDIA_TYPE}'",
            select=["id"], top=100000)]
        if old_ids:
            for i in range(0, len(old_ids), BATCH):
                client.delete_documents([{"id": x} for x in old_ids[i:i + BATCH]])
            print(f"[DELETE] 旧見積チャンク {len(old_ids):,} 件を削除")
        state = {}

    indexed_at = datetime.now(timezone.utc).isoformat()
    docs, ok, err = [], 0, 0
    processed_bodies: list[str] = []
    for p, rel, mt in targets:
        try:
            d, body = _doc_for(p, rel, indexed_at)
            docs.append(d)
            if body:
                processed_bodies.append(body)
            state[str(rel)] = mt
            ok += 1
        except Exception as e:
            err += 1
            if err <= 10:
                print(f"[WARN] 抽出失敗 {rel}: {type(e).__name__}: {e}")
        if len(docs) >= BATCH:
            client.merge_or_upload_documents(docs)
            docs = []
            if ok % 500 < BATCH:
                print(f"  ... {ok:,} 件登録")
    if docs:
        client.merge_or_upload_documents(docs)

    # 削除されたファイルをインデックスからも削除
    if removed:
        del_ids = [{"id": hashlib.sha256(f"mitsumori::{k}".encode()).hexdigest()}
                   for k in removed]
        for i in range(0, len(del_ids), BATCH):
            client.delete_documents(del_ids[i:i + BATCH])
        for k in removed:
            state.pop(k, None)
        print(f"[DELETE] 削除ファイル分 {len(removed):,} 件をインデックスから削除")

    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    # ── 見積用語カタログの更新(処理したファイルの明細から語彙を累積) ────────
    try:
        _update_terms_catalog(processed_bodies, full=args.full)
    except Exception as e:
        print(f"[WARN] 用語カタログ更新失敗: {e}")

    print(f"[DONE] 登録 {ok:,} 件 / 失敗 {err:,} 件。"
          f"AI Q&A(見積メンバー限定)で過去見積に答えられます。")
    return 0


def _update_terms_catalog(bodies: list[str], full: bool = False) -> None:
    """明細テキストから語彙を抽出し、累積カウントを更新してBlobへ出力する。

    カウントはローカル(mitsumori_terms_counts.json)に累積。--full時は作り直し。
    出現TERMS_MIN_COUNT回以上の語だけをBlobのカタログに載せる(金額は含まない)。
    """
    from collections import Counter
    counts = {"カタカナ": {}, "型式": {}, "作業": {}}
    if TERMS_COUNTS_PATH.exists() and not full:
        counts = json.loads(TERMS_COUNTS_PATH.read_text(encoding="utf-8"))
    kata, model, work = (Counter(counts.get("カタカナ", {})),
                         Counter(counts.get("型式", {})),
                         Counter(counts.get("作業", {})))
    for body in bodies:
        for m in RX_KATAKANA.findall(body):
            if m not in TERMS_STOP:
                kata[m] += 1
        for m in RX_MODEL.findall(body):
            m = m.upper().rstrip("-")
            if len(m) >= 3:
                model[m] += 1
        for m in RX_WORK.findall(body):
            if not m.startswith(("の", "を", "は", "と", "が")):
                work[m] += 1
    TERMS_COUNTS_PATH.write_text(json.dumps(
        {"カタカナ": dict(kata), "型式": dict(model), "作業": dict(work)},
        ensure_ascii=False), encoding="utf-8")

    catalog = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "カタカナ": [w for w, c in kata.most_common(300) if c >= TERMS_MIN_COUNT],
        "型式": [w for w, c in model.most_common(300) if c >= TERMS_MIN_COUNT],
        "作業": [w for w, c in work.most_common(300) if c >= TERMS_MIN_COUNT],
    }
    conn = os.environ.get("AZURE_BLOB_CONNECTION_STRING", "")
    if not conn:
        print("[WARN] AZURE_BLOB_CONNECTION_STRING未設定のため用語カタログ未出力")
        return
    from azure.storage.blob import BlobServiceClient
    BlobServiceClient.from_connection_string(conn) \
        .get_blob_client(os.environ.get("LW_BLOB_CONTAINER", "lw-raw"), TERMS_BLOB) \
        .upload_blob(json.dumps(catalog, ensure_ascii=False, indent=1).encode("utf-8"),
                     overwrite=True)
    print(f"[TERMS] 見積用語カタログ更新: カタカナ{len(catalog['カタカナ'])}種 / "
          f"型式{len(catalog['型式'])}種 / 作業{len(catalog['作業'])}種")


if __name__ == "__main__":
    raise SystemExit(main())
