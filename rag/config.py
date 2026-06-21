"""Azure AI Search + Azure OpenAI 接続設定。

使い方:
    プロジェクトルートに .env ファイルを作成し、以下の変数を設定してください。
    テンプレートは .env.example を参照。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# プロジェクトルート（rag/ の一つ上）
_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env", override=True)


def _require(key: str) -> str:
    """必須環境変数を取得する。未設定なら分かりやすいエラーを出す。"""
    val = os.environ.get(key, "").strip()
    if not val:
        raise EnvironmentError(
            f"\n[設定エラー] 環境変数 '{key}' が設定されていません。\n"
            f".env ファイルを確認してください（テンプレート: .env.example）"
        )
    return val


# ── Azure AI Search ──────────────────────────────────────────────────────────
SEARCH_ENDPOINT: str = os.getenv("AZURE_SEARCH_ENDPOINT", "").strip()
# 例: https://my-search.search.windows.net

SEARCH_API_KEY: str = os.getenv("AZURE_SEARCH_API_KEY", "").strip()

SEARCH_INDEX_NAME: str = os.getenv("AZURE_SEARCH_INDEX_NAME", "photo-index")

# ── スキャン対象ルート ─────────────────────────────────────────────────────────
TARGET_91_ROOT: Path = Path(
    os.getenv(
        "TARGET_91_ROOT",
        r"Z:\takachiho\2to9_業務別フォルダ\91_工番別実績写真・動画",
    )
)

# ── マニフェスト（削除検知用） ─────────────────────────────────────────────────
# 前回インデックス登録済みファイルの id → file_path マッピングを保存する JSON
MANIFEST_PATH: Path = _ROOT / "rag" / "manifest.json"

# ── アップロードバッチサイズ ───────────────────────────────────────────────────
# Azure AI Search は 1 回のバッチで最大 1000 件。500 が安定。
UPLOAD_BATCH_SIZE: int = int(os.getenv("UPLOAD_BATCH_SIZE", "500"))

# ── Azure OpenAI ──────────────────────────────────────────────────────────────
OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
# 例: https://my-openai.openai.azure.com/

OPENAI_API_KEY: str = os.getenv("AZURE_OPENAI_API_KEY", "").strip()

OPENAI_GPT4O_DEPLOYMENT: str = os.getenv("AZURE_OPENAI_GPT4O_DEPLOYMENT", "gpt-4o")
# Azure OpenAI Studio でデプロイしたモデルのデプロイ名を指定

OPENAI_API_VERSION: str = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")

# 説明文キャッシュ（descriptions.json）
DESCRIPTIONS_PATH: Path = _ROOT / "rag" / "descriptions.json"

# 人間コメントキャッシュ（comments.json）
# {doc_id: {comment, user_id, annotated_at, borrowed_from}}
COMMENTS_PATH: Path = _ROOT / "rag" / "comments.json"


def ensure_search_credentials() -> tuple[str, str]:
    """Azure AI Search 接続に必要な資格情報を返す。未設定時は例外。"""
    endpoint = SEARCH_ENDPOINT or _require("AZURE_SEARCH_ENDPOINT")
    api_key = SEARCH_API_KEY or _require("AZURE_SEARCH_API_KEY")
    return endpoint, api_key


def ensure_openai_credentials() -> tuple[str, str]:
    """Azure OpenAI 接続に必要な資格情報を返す。未設定時は例外。"""
    endpoint = OPENAI_ENDPOINT or _require("AZURE_OPENAI_ENDPOINT")
    api_key = OPENAI_API_KEY or _require("AZURE_OPENAI_API_KEY")
    return endpoint, api_key
