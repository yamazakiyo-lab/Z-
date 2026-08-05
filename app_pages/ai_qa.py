"""AI Q&A — GPT-4o に質問できるチャットページ。

総合検索APPの1メニュー。search_app.py の st.navigation から呼ばれる。

仕組み:
  1. 質問を受けると Azure AI Search(photo-index)で社内データを検索
  2. ヒットした工番実績・コメントを文脈として GPT-4o に渡して回答生成
     (社内データに無い一般的な技術質問にも普通に答える)
  3. やり取りは全件 Blob(lw-raw/qa_log_YYYYMM.jsonl)に記録
     — 誰が(Entra UPN)・いつ・何を聞き・何と答えたか。管理者はログページで閲覧可。

必要な環境変数: AZURE_OPENAI_*(既存), AZURE_SEARCH_*(既存),
                AZURE_BLOB_CONNECTION_STRING(既存)
"""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

JST = timezone(timedelta(hours=9))
QA_LOG_CONTAINER = os.getenv("LW_BLOB_CONTAINER", "lw-raw")
MAX_HISTORY = 12          # GPTに渡す直近の往復数(コスト抑制)
RAG_TOP = 5               # 社内データ検索の件数
MAX_ANSWER_TOKENS = 1200

SYSTEM_PROMPT = """あなたは株式会社TSEGの社内AIアシスタントです。
製造業(産業機械・省力化装置)の現場からの質問に、日本語で簡潔・正確に答えてください。

- 「社内データ」として渡された工番実績・コメントに関連情報があれば、それを優先して回答に使い、どの工番の情報かを明示すること。
- 「社内データ」に【規程名】付きの社内規程の条文が含まれる場合は、それを根拠として回答し、規程名と条番号を必ず明示すること(例: 「就業規則 第23条によると…」)。人事・総務に関する質問(休暇・手当・勤務時間・慶弔など)はこの規程が最優先の根拠。
- 規程の条文が見つからない人事・総務の質問には、推測で答えず「規程に該当箇所が見つからないため、人事課に確認してください」と案内すること。
- 手当・休暇などの「一覧」「種類」を問われたら、[規程用語カタログ]の種類をすべて挙げて網羅的に答えること(条文が手元に無い種類は名称のみ挙げ、詳細は該当規程の参照を案内)。
- TSEG WORKS(このアプリ)や写真投稿botの使い方の質問には、【利用マニュアル】のチャンクを根拠に、章名を示して答えること(例: 「利用マニュアル『写真の投稿』によると…」)。
- 会社の経営方針・企業理念・重点施策・業務方針に関する質問には、[経営計画]のチャンクを根拠に、章名を示して答えること(例: 「中期経営計画書2026『重点施策』によると…」)。
- 社員の予定・出張・休暇の質問に[社内予定]が渡された場合は、それを根拠に答え、スナップショット時点の情報であることを添えること。該当が無ければ「カレンダーに予定が見当たらない」と答え、推測しないこと。
- [質問者]が渡された場合、質問文の「俺」「私」「自分」は質問者本人を指す。予定などの質問では質問者本人の情報を答えること。
- 誕生日・入社日の質問には[メンバー情報]を根拠に答えること。誕生日は月日のみのデータであり、生年・年齢は答えられない(推測もしない)。[メンバー情報]に「開示できない」とある場合は、人事情報のため答えられない旨を丁寧に伝えること。
- 過去の見積の質問に[過去見積]が渡された場合は、件名・見積日・合計金額・ファイルの場所を根拠に答えること。金額は抽出値のため「詳細はファイルで確認」を添えること。[過去見積]が無い場合、見積の金額に関する質問には「見積情報の閲覧権限がないか、該当が見つからない」旨を答え、推測しないこと。
- 在庫の質問に[在庫データ]が渡された場合は、その数量・棚番を根拠に直接答えること。ただし「昨晩時点のデータ」であることを添え、最新・詳細は「部品在庫検索」「動治工具・測定具・消耗品検索」メニューでの確認を必ず案内すること。[在庫データ]に該当が無い品は、数を推測せず在庫は不明としてメニューを案内すること。
- 社内データに無い一般的な技術・業務の質問には、あなたの知識で普通に答えてよい。
- わからないことは推測で断言せず、わからないと言うこと。
- 回答は現場の人が読みやすいよう、簡潔にすること。"""


# ── ユーザー特定(Entra Easy Auth) ─────────────────────────────────────────────
def _current_upn() -> str:
    """Easy Authヘッダーからログインユーザーを取得。
    値はURLエンコードされた氏名(例: %E5%B1%B1... = 山嵜喜隆)の場合があるためデコードする。
    """
    try:
        from urllib.parse import unquote
        hdrs = st.context.headers or {}
        raw = (hdrs.get("X-MS-CLIENT-PRINCIPAL-NAME")
               or hdrs.get("X-Ms-Client-Principal-Name") or "").strip()
        return unquote(raw).strip().lower()
    except Exception:
        return ""


# ── クライアント ──────────────────────────────────────────────────────────────
@st.cache_resource(show_spinner="AIに接続中...")
def _get_openai_client():
    from openai import AzureOpenAI
    from rag.config import OPENAI_API_VERSION, ensure_openai_credentials

    endpoint, api_key = ensure_openai_credentials()
    return AzureOpenAI(azure_endpoint=endpoint, api_key=api_key,
                       api_version=OPENAI_API_VERSION)


@st.cache_resource(show_spinner=False)
def _get_search_client():
    try:
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient
        from rag.config import SEARCH_INDEX_NAME, ensure_search_credentials

        endpoint, api_key = ensure_search_credentials()
        return SearchClient(endpoint, SEARCH_INDEX_NAME, AzureKeyCredential(api_key))
    except Exception:
        return None  # 検索が使えなくてもQ&A自体は動かす


# ── 社内データ検索(RAG) ───────────────────────────────────────────────────────
SYNONYMS_PATH = Path(__file__).resolve().parent.parent / "rag" / "qa_synonyms.json"


@st.cache_data(ttl=600)
def _load_synonyms() -> dict:
    """レビュー(tools/qa_review.py)で採用した用語対応表を読む。無ければ空。

    形式: {"話し言葉の語": "規程・実績で使われる言い回し", ...}
    """
    try:
        d = json.loads(SYNONYMS_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _synonyms_hint() -> str:
    syn = _load_synonyms()
    if not syn:
        return ""
    lines = [f"{k} → {v}" for k, v in list(syn.items())[:40]]
    return ("\n\n社内で確認済みの用語対応表(質問に該当する語があれば必ず変換に使うこと):\n"
            + "\n".join(lines))


@st.cache_data(ttl=21600)
def _load_kitei_terms() -> dict:
    """規程用語カタログ(Blob: kitei_terms.json)を読む。無ければ空。

    tools/export_kitei_terms.py が規程本文から自動抽出した
    手当・休暇等の実在用語一覧。規程再取り込みのたびに更新される。
    """
    try:
        conn = os.getenv("AZURE_BLOB_CONNECTION_STRING", "")
        if not conn:
            return {}
        from azure.storage.blob import BlobServiceClient
        raw = BlobServiceClient.from_connection_string(conn) \
            .get_blob_client(QA_LOG_CONTAINER, "kitei_terms.json") \
            .download_blob().readall()
        d = json.loads(raw.decode("utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


_TERM_CATS = ("手当", "休暇", "休業", "休職")


def _kitei_terms_hint() -> str:
    """キーワード変換用: 一覧系質問で実在の全種類を検索語に含めさせる。"""
    terms = _load_kitei_terms()
    lines = [f"{c}: {' '.join(terms[c][:20])}" for c in _TERM_CATS if terms.get(c)]
    if not lines:
        return ""
    return ("\n\n規程に実在する用語一覧(『一覧』『種類』『どんな○○がある』型の質問では、"
            "該当カテゴリの語をすべて検索語に含めること):\n" + "\n".join(lines))


@st.cache_data(ttl=21600)
def _load_genba_terms() -> dict:
    """現場用語カタログ(genba_terms.json)をBlobから読む。無ければ空。

    写真コメントから夜間ランが自動抽出する現場の呼び名(部品・型式・作業語)。
    コメントが溜まるほど語彙が増え、キーワード変換が現場語に強くなる。
    """
    try:
        conn = os.getenv("AZURE_BLOB_CONNECTION_STRING", "")
        if not conn:
            return {}
        from azure.storage.blob import BlobServiceClient
        svc = BlobServiceClient.from_connection_string(conn)
        raw = svc.get_blob_client(QA_LOG_CONTAINER, "genba_terms.json") \
            .download_blob().readall()
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _genba_terms_hint() -> str:
    """キーワード変換用: 現場写真コメントで実際に使われる語を検索語に使わせる。"""
    terms = _load_genba_terms()
    lines = [f"{c}: {' '.join(terms[c][:20])}"
             for c in ("カタカナ", "型式", "作業") if terms.get(c)]
    if not lines:
        return ""
    return ("\n\n現場の写真コメントで実際に使われる語(社内の呼び名。質問に関連する語が"
            "あればこの表記のまま検索語に含めること):\n" + "\n".join(lines))


@st.cache_data(ttl=900)
def _load_calendar() -> dict:
    """社内予定スナップショット(calendar_events.json)をBlobから読む。無ければ空。

    LINE WORKSカレンダーから朝8:30と夜間ランで自動エクスポートされる7日分。
    """
    try:
        conn = os.getenv("AZURE_BLOB_CONNECTION_STRING", "")
        if not conn:
            return {}
        from azure.storage.blob import BlobServiceClient
        svc = BlobServiceClient.from_connection_string(conn)
        raw = svc.get_blob_client(QA_LOG_CONTAINER, "calendar_events.json") \
            .download_blob().readall()
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


_CAL_INTENT = re.compile(
    r"予定|出張|カレンダー|スケジュール|休み|休暇|不在|在社|出社|出勤|どこに|来てる|いますか|いる？")


def _calendar_context(question: str, asker: str = "") -> str:
    """予定の質問なら、期間・人物で絞ったスナップショットの予定一覧を文脈として返す。

    スナップショットは今日から31日分。質問の「今月/来週/明日」等で期間を絞り、
    質問中の人名(または「俺/私」なら質問者)に該当する人の予定を優先する。
    """
    if not _CAL_INTENT.search(question):
        return ""
    cal = _load_calendar()
    events = cal.get("events") or []
    if not events:
        return ""
    import datetime as _dt
    today = datetime.now(JST).date()

    def _ev_range(e):
        """予定の(開始日, 実質終了日)。終日予定のendは翌日日付(排他的)なので1日引く。"""
        try:
            ds = _dt.date.fromisoformat((e.get("start") or "")[:10])
        except Exception:
            return None
        try:
            de = _dt.date.fromisoformat((e.get("end") or "")[:10])
            if e.get("all_day") and de > ds:
                de -= timedelta(days=1)
        except Exception:
            de = ds
        return ds, max(ds, de)

    # 期間の解釈: 今月/来月/N月/来週/今週/明日 → 該当範囲。指定なしは1か月先まで
    qn = question.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    start_d, end = today, None
    month = year = None
    if "今月" in qn:
        month, year = today.month, today.year
    elif "来月" in qn:
        month = today.month % 12 + 1
        year = today.year + (1 if today.month == 12 else 0)
    else:
        m = re.search(r"(\d{1,2})月", qn)
        if m and 1 <= int(m.group(1)) <= 12:
            month = int(m.group(1))
            year = today.year if month >= today.month else today.year + 1
    if month:
        first = _dt.date(year, month, 1)
        start_d = max(today, first)
        end = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    elif "来週" in qn:
        end = today + timedelta(days=14)
    elif "今週" in qn:
        end = today + timedelta(days=7)
    elif "明日" in qn or "明後日" in qn or "あす" in qn:
        end = today + timedelta(days=3)
    else:
        end = today + timedelta(days=31)
    # 期間に「重なる」予定を採用(開始日が過去でも継続中なら拾う)
    sel = [e for e in events
           if (r := _ev_range(e)) and r[0] <= end and r[1] >= start_d]

    # 人物の絞り込み: 質問中の実在人名 > 「俺/私/自分」=質問者
    names = {e.get("user_name", "") for e in sel if e.get("user_name")}
    hit = [n for n in names if n in question or (len(n) >= 2 and n[:2] in question)]
    if not hit and asker and re.search(r"俺|私|自分|わたし|僕", question):
        a = asker.replace(" ", "")
        hit = [n for n in names if n.replace(" ", "") in a or a in n.replace(" ", "")]
    if hit:
        pri = [e for e in sel if e.get("user_name") in hit]
        if pri:
            sel = pri
    if not sel:
        return ""
    lines = []
    for e in sel[:40]:
        r = _ev_range(e)
        ds, de = r
        t = (e.get("start") or "")[11:16]
        when = ds.strftime("%m/%d")
        if t and not e.get("all_day"):
            when += f" {t}"
        if de > ds:
            when += f"〜{de.strftime('%m/%d')}"  # 複数日は期間で明示
        loc = f"({e['location']})" if e.get("location") else ""
        lines.append(f"{when} {e.get('user_name', '?')}: {e.get('summary', '')}{loc}")
    gen = (cal.get("generated_at") or "")[:16].replace("T", " ")
    span = cal.get("days") or 31
    return (f"[社内予定(LINE WORKSカレンダー、{gen}時点・今日から{span}日分より抜粋)] "
            + " ｜ ".join(lines))


@st.cache_data(ttl=21600)
def _load_members_prof() -> dict:
    """メンバーの誕生日(月日のみ)・入社日(members_prof.json)をBlobから読む。

    毎朝8:00の記念日通知タスクが更新する。誕生日は年齢が出ないよう月日のみ。
    """
    try:
        conn = os.getenv("AZURE_BLOB_CONNECTION_STRING", "")
        if not conn:
            return {}
        from azure.storage.blob import BlobServiceClient
        svc = BlobServiceClient.from_connection_string(conn)
        raw = svc.get_blob_client(QA_LOG_CONTAINER, "members_prof.json") \
            .download_blob().readall()
        return json.loads(raw.decode("utf-8")).get("members", {})
    except Exception:
        return {}


# 見積の質問に答えられるのは見積作成メンバー+管理者のみ(金額は営業機密)
_MITSUMORI_ALLOWED = {n.strip() for n in os.getenv(
    "QA_MITSUMORI_ALLOWED_NAMES",
    "山嵜喜隆,山嵜絵里,昆哲郎,松尾崇,松﨑誠一,滝沢雄一").split(",") if n.strip()}
_MITSUMORI_INTENT = re.compile(r"見積|いくらで|金額|値段|単価|価格|出してる|出した")
# Easy Auth表示名の別名(実測: 専務=山嵜絵里、matsuo=松尾崇 等)
_NAME_ALIAS = {"専務": "山嵜絵里", "matsuo": "松尾崇", "ayase2": "松﨑誠一",
               "yamazakiyo@tseg.co.jp": "山嵜喜隆", "yamazakiyo": "山嵜喜隆",
               "t_user03": "昆哲郎", "s_user01": "山嵜絵里", "s_user02": "滝沢雄一"}


def _norm_name(s: str) -> str:
    return (s or "").replace(" ", "").replace("　", "").replace("﨑", "崎").lower()


def _mitsumori_allowed(asker: str) -> bool:
    a = _norm_name(asker)
    for k, v in _NAME_ALIAS.items():
        if _norm_name(k) == a:
            a = _norm_name(v)
            break
    if not a:
        return False
    return any(_norm_name(n) in a or a in _norm_name(n)
               for n in _MITSUMORI_ALLOWED if n)


_PROF_INTENT = re.compile(r"誕生日|バースデー|入社日|勤続|何年目|入社した")
# 誕生日・入社日は人事情報のため、AI Q&Aで答える相手を管理者に限定する
_PROF_ALLOWED = {n.strip() for n in
                 os.getenv("QA_PROF_ALLOWED_NAMES", "山嵜喜隆,山嵜絵里").split(",")}


def _members_prof_context(question: str, asker: str = "") -> str:
    """誕生日・入社日の質問なら、メンバープロフィールを文脈として返す(管理者のみ)。"""
    if not _PROF_INTENT.search(question):
        return ""
    a = (asker or "").replace(" ", "")
    if not any(n.replace(" ", "") in a or a == n.replace(" ", "")
               for n in _PROF_ALLOWED if n):
        # 管理者以外には答えない(データ自体を渡さない)
        return ("[メンバー情報] 誕生日・入社日は人事情報のため、"
                "この質問者には開示できない。丁寧にその旨を伝えること。")
    prof = _load_members_prof()
    if not prof:
        return ""
    lines = []
    for name, p in prof.items():
        parts = []
        if p.get("birthday"):
            parts.append(f"誕生日{p['birthday'].replace('-', '/')}")
        if p.get("hired"):
            parts.append(f"入社{p['hired']}")
        if parts:
            lines.append(f"{name}: {'、'.join(parts)}")
    if not lines:
        return ""
    return "[メンバー情報(誕生日は月日のみ)] " + " ｜ ".join(lines)


@st.cache_data(ttl=3600)
def _load_inventories() -> dict:
    """部品在庫・工具リスト(Blobの夜間エクスポート)を読む。無ければ空。"""
    out: dict = {}
    try:
        conn = os.getenv("AZURE_BLOB_CONNECTION_STRING", "")
        if not conn:
            return {}
        from azure.storage.blob import BlobServiceClient
        svc = BlobServiceClient.from_connection_string(conn)
        for key, blob_name in (("parts", "parts_inventory.json"),
                               ("tools", "tools_inventory.json")):
            try:
                raw = svc.get_blob_client(QA_LOG_CONTAINER, blob_name) \
                    .download_blob().readall()
                out[key] = json.loads(raw.decode("utf-8"))
            except Exception:
                pass
    except Exception:
        return {}
    return out


_INV_INTENT = re.compile(
    r"在庫|何個|何本|何枚|個数|数量|残数|残って|持って|払い出|ストック|部品|工具|測定具|消耗品")
_INV_STOP = {"在庫", "個数", "数量", "何個", "部品", "工具", "ある", "あり",
             "ありますか", "教えて", "ください", "どれ", "くらい"}


def _inv_norm(s) -> str:
    """在庫照合用の正規化(全半角統一・空白/ハイフン/括弧を除去・小文字化)。"""
    s = unicodedata.normalize("NFKC", str(s or ""))
    return re.sub(r"[\s\-‐－ー_/()（）,、.。]", "", s).lower()


def _search_inventory(question: str, keywords: str) -> list[dict]:
    """在庫の質問なら、品名・型式のキーワード一致で在庫データを引く。"""
    if not _INV_INTENT.search(question):
        return []
    inv = _load_inventories()
    if not inv:
        return []
    terms = []
    for t in re.split(r"[\s、。,？?]+", f"{keywords} {question}"):
        n = _inv_norm(t)
        if len(n) >= 2 and t not in _INV_STOP and n not in {_inv_norm(x) for x in _INV_STOP}:
            terms.append(n)
    if not terms:
        return []
    scored = []
    for src, label in (("parts", "部品在庫"), ("tools", "動治工具・測定具・消耗品")):
        data = inv.get(src) or {}
        for it in data.get("items", []):
            hay = _inv_norm(" ".join(str(it.get(k, "")) for k in
                                     ("name", "model", "spec", "cat", "maker", "tana")))
            score = sum(1 for t in terms if t in hay)
            if score:
                scored.append((score, len(hay), label, it))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [{"label": lb, **it} for _, _, lb, it in scored[:8]]


def _inventory_context(inv_hits: list[dict]) -> str:
    if not inv_hits:
        return ""
    lines = []
    for h in inv_hits:
        if h["label"] == "部品在庫":
            lines.append(f"{h.get('cat','')}/型式:{h.get('model','')}/仕様:{h.get('spec','')}"
                         f"/メーカー:{h.get('maker','')}/棚番:{h.get('tana','')}/数量:{h.get('qty','')}")
        else:
            lines.append(f"工具({h.get('site','')}){h.get('cat','')}/品名:{h.get('name','')}"
                         f"/型式:{h.get('model','')}/数量:{h.get('qty','')}")
    return ("[在庫データ(毎晩自動エクスポート=昨晩時点)] " + " ｜ ".join(lines))


def _kitei_terms_context() -> str:
    """回答用の文脈に足すカタログ。一覧系の回答の網羅性を担保する。"""
    terms = _load_kitei_terms()
    lines = [f"{c}: {'、'.join(terms[c][:20])}" for c in _TERM_CATS if terms.get(c)]
    if not lines:
        return ""
    return ("[規程用語カタログ(規程本文から自動抽出。一覧を問われたらこの種類で網羅を確認)] "
            + " ／ ".join(lines))


def _extract_keywords(openai_client, question: str) -> str:
    """話し言葉の質問を、規程・実績検索用のキーワードに変換する。

    例: 「有給休暇は当日申請でもいい？」→「年次有給休暇 請求 届出 期限 手続」
    話し言葉(当日申請)と条文の書き言葉(前日までに請求)の語のすれ違いを埋める。
    失敗時は元の質問文をそのまま返す。
    """
    try:
        from rag.config import OPENAI_GPT4O_DEPLOYMENT
        resp = openai_client.chat.completions.create(
            model=OPENAI_GPT4O_DEPLOYMENT,
            messages=[{
                "role": "user",
                "content": (
                    "次の質問を、社内規程・作業記録の全文検索に使うキーワードに変換してください。"
                    "名詞・専門用語を中心に3〜8語、スペース区切りで出力。"
                    "規程で使われる正式な言い回し(例: 有給→年次有給休暇、申請→請求 届出)も"
                    "含めること。キーワードのみを出力。"
                    + _synonyms_hint()
                    + _kitei_terms_hint()
                    + _genba_terms_hint()
                    + "\n\n質問: " + question
                ),
            }],
            max_tokens=60,
            temperature=0.0,
        )
        kw = (resp.choices[0].message.content or "").strip()
        return kw if kw else question
    except Exception:
        return question


def _search_internal(query: str, keywords: str = "",
                     include_mitsumori: bool = False) -> list[dict]:
    """規程と工番データを別枠で検索して統合する。

    1回の検索だと語の出現頻度で特定文書(例: 「休暇」「申請」が頻出する
    育児・介護休業規程)に枠を占有され、本命の条文(就業規則の年休など)が
    落ちることがあるため、規程枠(kitei)と実績枠を分けて取得する。
    検索語はキーワード変換済みのもの(keywords)を優先して使う。
    """
    client = _get_search_client()
    if client is None:
        return []

    q = keywords or query
    _select = ["workno", "workno_name", "phase", "file_name",
               "media_type", "content_text"]

    def _run(filter_expr: str, top: int) -> list:
        try:
            return list(client.search(
                search_text=q, top=top, filter=filter_expr, select=_select))
        except Exception:
            return []

    results = (_run("media_type eq 'kitei'", 4)
               + _run("media_type eq 'manual'", 2)
               + _run("media_type ne 'kitei' and media_type ne 'manual'"
                      " and media_type ne 'mitsumori'", 3))
    # 経営計画は上記3枠目(その他)に含まれて返るが、専用の底上げは行わない
    # (経営方針系の質問ならキーワード一致で自然に上位に来るため)
    # 見積は営業機密のため通常枠から除外し、許可された質問者のときだけ専用枠で検索
    if include_mitsumori:
        results += _run("media_type eq 'mitsumori'", 4)

    hits = []
    for r in results:
        txt = (r.get("content_text") or "").strip()
        if not txt:
            continue
        mt = r.get("media_type") or ""
        is_kitei = mt == "kitei"
        is_manual = mt == "manual"
        is_keikaku = mt == "keikaku"
        is_mitsumori = mt == "mitsumori"
        hits.append({
            "workno": r.get("workno") or "",
            "workno_name": r.get("workno_name") or "",
            "phase": r.get("phase") or "",
            "file_name": r.get("file_name") or "",
            "is_kitei": is_kitei,
            "is_manual": is_manual,
            "is_keikaku": is_keikaku,
            "is_mitsumori": is_mitsumori,
            # 規程条文・マニュアル・経営計画は長めに渡す
            "text": txt[:1000 if (is_kitei or is_manual or is_keikaku) else 500],
        })
    return hits


def _build_context(hits: list[dict]) -> str:
    if not hits:
        return "(該当する社内データなし)"
    lines = []
    for h in hits:
        if h.get("is_kitei"):
            head = f"[社内規程: {h['workno_name']}]"
        elif h.get("is_manual"):
            head = f"[利用マニュアル: {h['workno_name']}]"
        elif h.get("is_keikaku"):
            head = f"[経営計画: {h['workno_name']}]"
        elif h.get("is_mitsumori"):
            head = f"[過去見積: {h['workno_name']}]"
        else:
            head = f"[工番 {h['workno']} {h['workno_name']}".strip() + (
                f" / {h['phase']}]" if h["phase"] else "]")
        lines.append(f"{head} {h['text']}")
    return "\n".join(lines)


# ── ログ記録(Blob追記) ────────────────────────────────────────────────────────
def _log_qa(upn: str, question: str, answer: str, hits: list[dict]) -> None:
    """1往復を qa_log_YYYYMM.jsonl (Append Blob) に追記。失敗しても本体に影響させない。"""
    try:
        conn = os.getenv("AZURE_BLOB_CONNECTION_STRING", "")
        if not conn:
            return
        from azure.storage.blob import BlobServiceClient

        now = datetime.now(JST)
        blob_name = f"qa_log_{now:%Y%m}.jsonl"
        svc = BlobServiceClient.from_connection_string(conn)
        blob = svc.get_blob_client(QA_LOG_CONTAINER, blob_name)
        rec = {
            "ts": now.isoformat(timespec="seconds"),
            "user": upn or "(不明)",
            "q": question,
            "a": answer,
            "sources": [h["workno"] for h in hits if h.get("workno")],
        }
        line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            blob.append_block(line)
        except Exception:
            blob.create_append_blob()
            blob.append_block(line)
    except Exception:
        pass


# ── UI ────────────────────────────────────────────────────────────────────────
def main() -> None:
    st.page_link("app_pages/home.py", label="ホームに戻る", icon="🏠")
    st.title("💬 AI Q&A")
    st.caption("技術・業務・人事総務の質問にAIが答えます。"
               "社内の工番実績や社内規程(就業規則・給与規程・出張旅費規程など)、"
               "このアプリの利用マニュアルも参照して回答します。")
    st.divider()

    if "qa_messages" not in st.session_state:
        st.session_state.qa_messages = []

    # 履歴表示
    for m in st.session_state.qa_messages:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    question = st.chat_input("質問を入力してください")
    if not question:
        return

    upn = _current_upn()
    st.session_state.qa_messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    # 社内データ検索 → GPT-4o
    with st.chat_message("assistant"):
        with st.spinner("回答を作成中..."):
            # 話し言葉→検索キーワード変換をしてから検索(規程の取りこぼし対策)
            try:
                _oai = _get_openai_client()
                keywords = _extract_keywords(_oai, question)
            except Exception:
                keywords = ""
            _incl_mitsumori = (_MITSUMORI_INTENT.search(question) is not None
                               and _mitsumori_allowed(_current_upn()))
            hits = _search_internal(question, keywords,
                                    include_mitsumori=_incl_mitsumori)
            context = _build_context(hits)
            _terms_ctx = _kitei_terms_context()
            if _terms_ctx:
                context = f"{context}\n{_terms_ctx}"
            try:
                inv_hits = _search_inventory(question, keywords)
            except Exception:
                inv_hits = []
            if inv_hits:
                context = f"{context}\n{_inventory_context(inv_hits)}"
            try:
                _cal_ctx = _calendar_context(question, _current_upn())
            except Exception:
                _cal_ctx = ""
            if _cal_ctx:
                context = f"{context}\n{_cal_ctx}"
            try:
                _prof_ctx = _members_prof_context(question, _current_upn())
            except Exception:
                _prof_ctx = ""
            if _prof_ctx:
                context = f"{context}\n{_prof_ctx}"
            _asker = _current_upn()
            if _asker:
                context = f"{context}\n[質問者: {_asker}]"

            history = st.session_state.qa_messages[-(MAX_HISTORY * 2):]
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            # 直近履歴(今回の質問は文脈付きで別途足すので除く)
            for m in history[:-1]:
                messages.append({"role": m["role"], "content": m["content"]})
            messages.append({
                "role": "user",
                "content": f"### 社内データ(参考)\n{context}\n\n### 質問\n{question}",
            })

            try:
                from rag.config import OPENAI_GPT4O_DEPLOYMENT
                client = _get_openai_client()
                resp = client.chat.completions.create(
                    model=OPENAI_GPT4O_DEPLOYMENT,
                    messages=messages,
                    max_tokens=MAX_ANSWER_TOKENS,
                    temperature=0.2,
                )
                answer = (resp.choices[0].message.content or "").strip()
            except Exception as e:
                answer = f"⚠️ AI呼び出しに失敗しました: {e}"

            st.markdown(answer)
            if hits:
                with st.expander(f"参照した社内データ({len(hits)}件)"):
                    for h in hits:
                        if h.get("is_kitei"):
                            st.markdown(
                                f"- 📖 **{h['workno_name']}**(社内規程) — {h['text'][:120]}…")
                        elif h.get("is_manual"):
                            st.markdown(
                                f"- 📘 **{h['workno_name']}**(利用マニュアル) — {h['text'][:120]}…")
                        elif h.get("is_keikaku"):
                            st.markdown(
                                f"- 📋 **{h['workno_name']}**(経営計画) — {h['text'][:120]}…")
                        elif h.get("is_mitsumori"):
                            st.markdown(
                                f"- 💰 **{h['workno_name']}**(過去見積) — {h['text'][:120]}…")
                        else:
                            st.markdown(
                                f"- **工番 {h['workno']}** {h['workno_name']} "
                                f"{('/ ' + h['phase']) if h['phase'] else ''} — {h['text'][:120]}…")
            if inv_hits:
                with st.expander(f"参照した在庫データ({len(inv_hits)}件・昨晩時点)"):
                    for h in inv_hits:
                        nm = h.get("name") or h.get("model") or "?"
                        st.markdown(
                            f"- 📦 **{nm}**({h['label']}/{h.get('cat','')}) — "
                            f"数量 {h.get('qty') or '?'}")

    st.session_state.qa_messages.append({"role": "assistant", "content": answer})
    _log_qa(upn, question, answer, hits)


main()
