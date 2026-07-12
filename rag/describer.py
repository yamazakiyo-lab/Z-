"""Azure OpenAI GPT-4o Vision による画像説明文生成。"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, Optional

from .config import (
    DESCRIPTIONS_PATH,
    OPENAI_API_VERSION,
    OPENAI_GPT4O_DEPLOYMENT,
    ensure_openai_credentials,
)

VISION_SUPPORTED_EXT: frozenset = frozenset({".jpg", ".jpeg", ".png"})
MAX_IMAGE_BYTES: int = 4 * 1024 * 1024

# プロンプトバージョン。プロンプト変更時に上げる（例: v2→v3）。
# run_rag_describe.py --re-describe を実行すると旧バージョン分が全件再生成される。
PROMPT_VERSION: str = "v2"

# ── V3 設定（RAG_DESCRIBE_V3=1 で有効化。既定は従来動作＝V2） ─────────────────
V3_ENABLED: bool = os.getenv("RAG_DESCRIBE_V3", "0") == "1"
if V3_ENABLED:
    PROMPT_VERSION = "v3"
# モデル・画質・出力長は .env で切替可能（A/Bテスト・コスト調整用）
DESCRIBE_DEPLOYMENT: str = os.getenv("OPENAI_DESCRIBE_DEPLOYMENT", "")
DESCRIBE_IMAGE_DETAIL: str = os.getenv(
    "DESCRIBE_IMAGE_DETAIL", "low" if V3_ENABLED else "high"
)
DESCRIBE_MAX_TOKENS: int = int(os.getenv(
    "DESCRIBE_MAX_TOKENS", "150" if V3_ENABLED else "300"
))

# 新しいAPIバージョン(2024-10以降)は max_tokens ではなく max_completion_tokens を使う。
# gpt-5系デプロイは新バージョン+max_completion_tokens が必須。
_USE_NEW_TOKENS_PARAM: bool = OPENAI_API_VERSION >= "2024-10"

_DESCRIBE_PROMPT_BASE = (
    "この写真は機械・設備の工事または整備に関するものです。"
    "次の5点を含む説明を日本語・150文字以内で答えてください。"
    "(1)対象物（機械の種類・型番・メーカー名・銘板の文字があれば正確に記載）"
    "(2)駆動方式（油圧・空圧・電動・機械式など、判別できる場合のみ記載）"
    "(3)作業状態（着手前・施工中・完了・搬入・解体など）"
    "(4)損傷・劣化状況（錆・亀裂・摩耗・変形・油漏れなど、確認できる場合のみ記載）"
    "(5)場所・状況（屋外・屋内・ピット・制御盤など）"
    "説明文のみ出力し、番号や見出しは付けないこと。"
)


def _build_prompt(job_number: str = "", lw_comment: str = "", few_shot=None) -> str:
    parts = []
    if few_shot:
        examples = "".join(f"・{t}\n" for t in few_shot[:3])
        parts.append(
            "以下はTSEG現場担当者による同種写真の説明例です。"
            "専門用語・部品名の語彙を参考にしてください。\n" + examples
        )
    if job_number:
        parts.append(f"工番: {job_number}。")
    if lw_comment:
        parts.append(f"作業者コメント（参考）: 「{lw_comment}」。")
    return "".join(parts) + _DESCRIBE_PROMPT_BASE


def load_descriptions() -> Dict[str, str]:
    if not DESCRIPTIONS_PATH.exists():
        return {}
    try:
        with open(DESCRIPTIONS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] descriptions.json 読み込み失敗: {e}", file=sys.stderr)
        return {}


def save_descriptions(descriptions: Dict[str, str]) -> None:
    DESCRIPTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DESCRIPTIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(descriptions, f, ensure_ascii=False, indent=2)


def _image_to_base64(path: Path) -> Optional[str]:
    try:
        size = path.stat().st_size
        if size > MAX_IMAGE_BYTES:
            print(f"[SKIP] 画像サイズ超過 ({size // 1024}KB): {path.name}", file=sys.stderr)
            return None
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        print(f"[WARN] 画像読み込み失敗: {path.name} ({e})", file=sys.stderr)
        return None


def describe_image(image_path: Path, *, retries: int = 2, job_number: str = "", lw_comment: str = "", few_shot=None, deployment: str = "", detail: str = "", max_tokens: int = 0) -> str:
    ext = image_path.suffix.lower()
    if ext not in VISION_SUPPORTED_EXT:
        return ""

    b64 = _image_to_base64(image_path)
    if not b64:
        return ""

    mime = "jpeg" if ext in {".jpg", ".jpeg"} else "png"

    try:
        endpoint, api_key = ensure_openai_credentials()
    except EnvironmentError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return ""

    try:
        from openai import AzureOpenAI
    except ImportError:
        print("[ERROR] openai パッケージが見つかりません。pip install openai", file=sys.stderr)
        return ""

    client = AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        api_version=OPENAI_API_VERSION,
    )

    for attempt in range(retries + 1):
        try:
            dep = deployment or DESCRIBE_DEPLOYMENT or OPENAI_GPT4O_DEPLOYMENT
            kwargs = {
                "model": dep,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/{mime};base64,{b64}",
                                    "detail": detail or DESCRIBE_IMAGE_DETAIL,
                                },
                            },
                            {"type": "text", "text": _build_prompt(job_number, lw_comment, few_shot)},
                        ],
                    }
                ],
            }
            token_limit = max_tokens or DESCRIBE_MAX_TOKENS
            if _USE_NEW_TOKENS_PARAM:
                kwargs["max_completion_tokens"] = token_limit
            else:
                kwargs["max_tokens"] = token_limit
            # gpt-5系は temperature 指定不可(既定値のみ)
            if not dep.lower().startswith("gpt-5"):
                kwargs["temperature"] = 0
            response = client.chat.completions.create(**kwargs)
            return response.choices[0].message.content.strip()

        except Exception as e:
            err_str = str(e)
            if "RateLimitError" in type(e).__name__ or "429" in err_str:
                wait = 20 * (attempt + 1)
                print(f"[RATELIMIT] {wait}秒待機してリトライ ({attempt + 1}/{retries})", file=sys.stderr)
                time.sleep(wait)
            else:
                print(f"[WARN] GPT-4o Vision 失敗: {image_path.name} ({e})", file=sys.stderr)
                return ""

    print(f"[WARN] リトライ上限超過: {image_path.name}", file=sys.stderr)
    return ""
