"""AI Q&A ログレビュー — 答えられなかった質問を洗い出してスクリプト改善に使う。

Blob(lw-raw/qa_log_YYYYMM.jsonl)を読み、回答文の決まり文句から
「未回答(ミス)」を分類して月次レポート(logs/qa_review_YYYYMM.md)と
類義語候補ドラフト(rag/qa_synonyms_draft.json)を出力する。

分類:
    MISS_KITEI   規程系の空振り(「規程に該当箇所が見つからない」「人事課に確認」)
    MISS_UNKNOWN その他の「わかりません/見つかりません」回答
    ERROR        AI呼び出し失敗(⚠️)
    NO_SOURCE    社内情報っぽい質問なのに参照ソースゼロ(参考フラグ)

運用の流れ:
    1. 月初にこのスクリプトを実行(スケジュールタスク可):
         python tools/qa_review.py                # 先月分をBlobから取得
         python tools/qa_review.py --month 202607 # 月指定
         python tools/qa_review.py --local path\to\qa_log.jsonl  # ローカルファイルで試す
    2. logs/qa_review_YYYYMM.md を確認。繰り返し聞かれているミスが優先度高。
    3. 原因が「語のすれ違い」なら rag/qa_synonyms_draft.json の候補を精査し、
       採用するものを rag/qa_synonyms.json に移す(ai_qa.py が次回から参照)。
       原因が「規程が索引に無い」なら indexer.py で該当規程を取り込む。

必要な環境変数: AZURE_BLOB_CONNECTION_STRING(Blob取得時)
オプション: AZURE_OPENAI_*(--suggest で類義語候補をGPTに出させる場合)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", encoding="utf-8")
except ImportError:
    pass

JST = timezone(timedelta(hours=9))
QA_LOG_CONTAINER = os.getenv("LW_BLOB_CONTAINER", "lw-raw")

# ── ミス判定パターン(回答文に含まれていたら未回答扱い) ───────────────────────
MISS_KITEI_PAT = re.compile(
    r"(規程に該当箇所が見つからない|人事課に確認|規程.{0,10}(見つかりません|確認できません))")
MISS_UNKNOWN_PAT = re.compile(
    r"(わかりません|分かりません|判断できません|見つかりませんでした|"
    r"該当する(情報|データ|記録)?(は|が)?(見つかりません|ありません)|"
    r"該当が見当たりません|お答えできません)")
ERROR_PAT = re.compile(r"⚠️|AI呼び出しに失敗")
# 社内情報っぽさ(NO_SOURCE判定用): 人事総務・工番・社内実績を指す語
INTERNAL_PAT = re.compile(
    r"(有給|休暇|手当|残業|勤務|給与|賞与|慶弔|出張|旅費|規程|規定|就業|退職|育児|介護|"
    r"工番|実績|納期|見積|客先|社内|当社|うち(の|では))")


def classify(rec: dict) -> str | None:
    """1レコードを分類。ミスでなければ None。"""
    a = rec.get("a") or ""
    q = rec.get("q") or ""
    if ERROR_PAT.search(a):
        return "ERROR"
    if MISS_KITEI_PAT.search(a):
        return "MISS_KITEI"
    if MISS_UNKNOWN_PAT.search(a):
        return "MISS_UNKNOWN"
    if not rec.get("sources") and INTERNAL_PAT.search(q):
        return "NO_SOURCE"
    return None


# ── ログ取得 ──────────────────────────────────────────────────────────────────
def load_records(month: str, local: str | None) -> list[dict]:
    if local:
        raw = Path(local).read_text(encoding="utf-8")
    else:
        conn = os.getenv("AZURE_BLOB_CONNECTION_STRING", "")
        if not conn:
            sys.exit("AZURE_BLOB_CONNECTION_STRING が未設定です(--local でローカルファイルも指定可)")
        from azure.storage.blob import BlobServiceClient
        svc = BlobServiceClient.from_connection_string(conn)
        blob = svc.get_blob_client(QA_LOG_CONTAINER, f"qa_log_{month}.jsonl")
        raw = blob.download_blob().readall().decode("utf-8")
    recs = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except Exception:
            continue
    return recs


# ── 索引突合(任意): ミスの原因が「索引に無い」のか「検索で落ちた」のか ─────────
def probe_index(question: str) -> str:
    """規程枠で再検索して原因を推定。検索クライアントが無ければ '-'。"""
    try:
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient
        from rag.config import SEARCH_INDEX_NAME, ensure_search_credentials
        endpoint, api_key = ensure_search_credentials()
        client = SearchClient(endpoint, SEARCH_INDEX_NAME, AzureKeyCredential(api_key))
        n = len(list(client.search(search_text=question, top=3,
                                   filter="media_type eq 'kitei'")))
        return "検索ヒットあり(語のすれ違い?)" if n else "規程枠ヒット0(未収録の可能性)"
    except Exception:
        return "-"


# ── 類義語候補(任意): GPTに「話し言葉→規程の言い回し」候補を出させる ───────────
def suggest_synonyms(questions: list[str]) -> dict[str, str]:
    """ミスした質問ごとに検索キーワード候補を返す。失敗時は空。"""
    out: dict[str, str] = {}
    try:
        from openai import AzureOpenAI
        from rag.config import (OPENAI_API_VERSION, OPENAI_GPT4O_DEPLOYMENT,
                                ensure_openai_credentials)
        endpoint, api_key = ensure_openai_credentials()
        client = AzureOpenAI(azure_endpoint=endpoint, api_key=api_key,
                             api_version=OPENAI_API_VERSION)
        for q in questions:
            resp = client.chat.completions.create(
                model=OPENAI_GPT4O_DEPLOYMENT,
                messages=[{"role": "user", "content": (
                    "次の質問が社内規程の全文検索で空振りしました。"
                    "規程の条文で使われていそうな正式な言い回し・同義語を"
                    "3〜8語、スペース区切りで出力してください。語のみ出力。\n\n質問: " + q)}],
                max_tokens=60, temperature=0.0)
            kw = (resp.choices[0].message.content or "").strip()
            if kw:
                out[q] = kw
    except Exception:
        pass
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="AI Q&A ログレビュー")
    # 週次運用(毎週月曜)を既定とし、当月のここまでの分を見る。
    # 月初(1〜7日)は先月分がまだ総括されていないため先月を対象にする。
    _now = datetime.now(JST)
    default_month = (f"{(_now.replace(day=1) - timedelta(days=1)):%Y%m}"
                     if _now.day <= 7 else f"{_now:%Y%m}")
    ap.add_argument("--month", default=default_month,
                    help="対象月 YYYYMM(既定: 当月。毎月1〜7日は先月)")
    ap.add_argument("--local", help="Blobの代わりに読むローカルjsonl")
    ap.add_argument("--out", default=str(ROOT / "logs"), help="レポート出力先")
    ap.add_argument("--check-index", action="store_true", help="ミス質問を索引に再照会して原因を推定")
    ap.add_argument("--suggest", action="store_true", help="GPTで類義語候補を生成しドラフトに出力")
    ap.add_argument("--notify", action="store_true",
                    help="結果の要約をLINE WORKSで管理者へ通知(週次タスク用)")
    args = ap.parse_args()

    recs = load_records(args.month, args.local)
    misses = []
    for r in recs:
        kind = classify(r)
        if kind:
            misses.append((kind, r))

    # 同じ質問の繰り返し(正規化: 全半角統一・空白と文末記号除去)= 優先度シグナル
    import unicodedata
    norm = lambda s: re.sub(r"[\s?？!！。.]+", "",
                            unicodedata.normalize("NFKC", s or ""))
    freq = Counter(norm(r["q"]) for _, r in misses)

    counts = Counter(kind for kind, _ in misses)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / f"qa_review_{args.month}.md"

    lines = [
        f"# AI Q&A レビュー {args.month}",
        "",
        f"- 総やり取り: {len(recs)}件 / うち未回答・ミス: {len(misses)}件"
        f"({(len(misses) / len(recs) * 100):.1f}%)" if recs else "- ログなし",
        f"- 内訳: " + ", ".join(f"{k}: {v}件" for k, v in counts.most_common()) if misses else "",
        "",
        "| 日時 | 質問者 | 質問 | 分類 | 回数 | 索引照会 |",
        "|---|---|---|---|---|---|",
    ]
    probed: dict[str, str] = {}
    for kind, r in misses:
        key = norm(r["q"])
        probe = "-"
        if args.check_index and kind in ("MISS_KITEI", "MISS_UNKNOWN"):
            if key not in probed:
                probed[key] = probe_index(r["q"])
            probe = probed[key]
        q_disp = (r["q"] or "").replace("|", "/").replace("\n", " ")[:60]
        lines.append(f"| {r.get('ts','')} | {r.get('user','')} | {q_disp} "
                     f"| {kind} | {freq[key]} | {probe} |")
    lines += [
        "",
        "## 次のアクション",
        "- 「検索ヒットあり」→ 語のすれ違い。rag/qa_synonyms_draft.json の候補を精査して",
        "  rag/qa_synonyms.json へ採用(ai_qa.py が次回から検索語に反映)。",
        "- 「規程枠ヒット0」→ 規程が索引に未収録の可能性。indexer.py で取り込みを確認。",
        "- 回数が多い質問から優先的に対応。",
    ]
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"レポート: {report}({len(misses)}/{len(recs)}件がミス)")

    # ── LW通知(週次タスク用): 要約1通を管理者へ ──────────────────────────────
    if args.notify:
        try:
            from check_tasks_notify import _notify
            # 通知先: 山嵜喜隆・山嵜絵里(このプロセス内でのみ上書き。点検通知には影響しない)
            os.environ["CHECK_NOTIFY_NAMES"] = os.getenv(
                "QA_REVIEW_NOTIFY_NAMES", "山嵜喜隆,山嵜絵里")
            if not recs:
                msg = f"📊AI Q&A週次レビュー({args.month}): ログなし"
            elif not misses:
                msg = (f"📊AI Q&A週次レビュー({args.month}): "
                       f"やり取り{len(recs)}件、未回答ミスなし🎉")
            else:
                # 正規化キー→元の質問文(最初に出たもの)
                orig = {}
                for _, r in misses:
                    orig.setdefault(norm(r["q"]), (r["q"] or "").strip())
                tops = "、".join(
                    f"「{orig.get(k, k)[:25]}」×{v}"
                    for k, v in freq.most_common(3))
                msg = (f"📊AI Q&A週次レビュー({args.month}): "
                       f"やり取り{len(recs)}件中ミス{len(misses)}件。"
                       f"上位: {tops}。詳細はデスクトップの {report.name} を確認。")
            _notify(msg, dry_run=False)
        except Exception as e:
            print(f"[WARN] LW通知に失敗: {e}")

    if args.suggest and misses:
        uniq = list({norm(r["q"]): r["q"] for k, r in misses
                     if k in ("MISS_KITEI", "MISS_UNKNOWN")}.values())
        sugg = suggest_synonyms(uniq)
        if sugg:
            draft_path = ROOT / "rag" / "qa_synonyms_draft.json"
            draft = {}
            if draft_path.exists():
                try:
                    draft = json.loads(draft_path.read_text(encoding="utf-8"))
                except Exception:
                    draft = {}
            draft.update(sugg)
            draft_path.write_text(
                json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"類義語候補ドラフト: {draft_path}({len(sugg)}件追記)")


if __name__ == "__main__":
    main()
