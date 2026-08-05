"""過去見積書(231_見積書)をAI検索インデックスに取り込む。

AI Q&Aが「○○社のNC1-110整備、過去いくらで出してる？」等の質問に、
過去見積の件名・日付・金額・ファイル所在を根拠に答えられるようにする。
回答できるのは見積作成メンバー(ai_qa.py側で質問者を制限)のみ。

方式: xlsx見積の先頭シートから宛先・件名・日付・合計金額らしき値を
      ヒューリスティックに抽出し、1見積=1ドキュメントで photo-index に
      media_type="mitsumori" でupsert。差分更新(状態ファイルで管理)。
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


def _customer_from_path(rel: Path) -> str:
    parts = rel.parts
    if parts and RX_GYO.match(parts[0]):
        return parts[1] if len(parts) > 2 else ""
    return parts[0] if len(parts) > 1 else ""


def _extract(p: Path) -> dict:
    """先頭シートから宛先・件名・日付・合計金額を推定(50行×14列)。"""
    import openpyxl
    atesaki = kenmei = date_s = ""
    max_num = 0.0
    label_next = False
    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        for row in ws.iter_rows(min_row=1, max_row=50, max_col=14):
            for c in row:
                v = c.value
                if v is None:
                    continue
                if label_next and isinstance(v, str) and v.strip():
                    kenmei = kenmei or v.strip()[:60]
                    label_next = False
                s = str(v).strip()
                if not s:
                    continue
                if (s.endswith("様") or s.endswith("御中")) and not atesaki and len(s) <= 40:
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
    finally:
        wb.close()
    return {"atesaki": atesaki, "kenmei": kenmei, "date": date_s,
            "gokei": int(max_num) if max_num >= 1000 else 0}


def _doc_for(p: Path, rel: Path, indexed_at: str) -> dict:
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
            + f"\nファイル: {p}")
    return {
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
                d = _doc_for(p, rel, indexed_at)
                print(f"--- {rel}\n    {d['content_text'].splitlines()[0]}")
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
    for p, rel, mt in targets:
        try:
            docs.append(_doc_for(p, rel, indexed_at))
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
    print(f"[DONE] 登録 {ok:,} 件 / 失敗 {err:,} 件。"
          f"AI Q&A(見積メンバー限定)で過去見積に答えられます。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
