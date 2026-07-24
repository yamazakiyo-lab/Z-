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
import sys
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
def _search_internal(query: str) -> list[dict]:
    client = _get_search_client()
    if client is None:
        return []
    try:
        results = client.search(
            search_text=query,
            top=RAG_TOP,
            select=["workno", "workno_name", "phase", "file_name", "content_text"],
        )
        hits = []
        for r in results:
            txt = (r.get("content_text") or "").strip()
            if not txt:
                continue
            hits.append({
                "workno": r.get("workno") or "",
                "workno_name": r.get("workno_name") or "",
                "phase": r.get("phase") or "",
                "file_name": r.get("file_name") or "",
                "text": txt[:500],
            })
        return hits
    except Exception:
        return []


def _build_context(hits: list[dict]) -> str:
    if not hits:
        return "(該当する社内データなし)"
    lines = []
    for h in hits:
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
    st.caption("技術・業務の質問にAIが答えます。社内の工番実績に関連情報があれば併せて参照します。"
               "※やり取りは記録されます。")
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
            hits = _search_internal(question)
            context = _build_context(hits)

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
                        st.markdown(
                            f"- **工番 {h['workno']}** {h['workno_name']} "
                            f"{('/ ' + h['phase']) if h['phase'] else ''} — {h['text'][:120]}…")

    st.session_state.qa_messages.append({"role": "assistant", "content": answer})
    _log_qa(upn, question, answer, hits)


main()
