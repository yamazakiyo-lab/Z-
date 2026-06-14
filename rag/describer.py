"""Azure OpenAI GPT-4o Vision による画像説明文生成。"""
from __future__ import annotations

import base64
import json
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

DESCRIBE_PROMPT = (
    "この写真は機械・設備の工事または整備に関するものです。"
    "次の4点を含む説明を日本語・80文字以内で答えてください。"
    "(1)対象物（機械の種類・型番・メーカー名があれば記載）"
    "(2)駆動方式（油圧・空圧・電動・機械式など、判別できる場合のみ記載）"
    "(3)作業状態（着手前・施工中・完了・搬入・解体など）"
    "(4)場所・状況（屋外・屋内・ピット・制御盤など）"
    "説明文のみ出力し、番号や見出しは付けないこと。"
)


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


def describe_image(image_path: Path, *, retries: int = 2) -> str:
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
            response = client.chat.completions.create(
                model=OPENAI_GPT4O_DEPLOYMENT,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/{mime};base64,{b64}",
                                    "detail": "low",
                                },
                            },
                            {"type": "text", "text": DESCRIBE_PROMPT},
                        ],
                    }
                ],
                max_tokens=120,
                temperature=0,
            )
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
