# Zフォルダ写真管理 RAG統合設計書

## 1. 既存構成の整理

### デイリーラン2本立て

| バッチ | 起動経路 | 対象範囲 |
|---|---|---|
| GDX_DailyRun | run_gdx_wrapper.bat → run_gdx_logged.ps1 → run_gdx.py → cli.py | GDExtraction → 91_工番別実績写真・動画（＋252/92/9781） |
| OTHER_DailyRun | run_91other_wrapper.bat → run_91other_logged.ps1 → run_91other.py → cleanup.py | 2to9_業務別フォルダ配下（91除外） |

### GDXバッチの処理フロー

```
GDExtraction/
  ├── {workno}_{工事名}/   ← マスタCSVでリネーム
  ...
      ↓ B4へ移動
91_工番別実績写真・動画/
  └── {workno}_{工事名}/   ← Aフォルダ
      ├── {workno}_B1着手前写真・動画/
      ├── {workno}_B2着手中写真・動画/
      ├── {workno}_B3出荷以降写真・動画/
      └── {workno}_B4整理前写真・動画/
          ↓ organizer.pyが振り分け・リネーム・圧縮
          {workno}_001_YYMMDD.jpg, {workno}_002_YYMMDD.jpg ...
```

### リネーム規則

- **ファイル名**: `{workno}_{連番3桁}_{YYMMDD}.jpg`（例: `1234-01_001_250611.jpg`）
- **工番形式**: `[prefix][digits]-[2digits]`（例: `1234-01`, `GDX1234-01`）
- **Bフォルダ振り分けキーワード**:
  - B1（着手前）: 引取/入庫/入荷/受入/入庫時/着手前
  - B2（着手中）: 整備/加工/切削/完成/整備中/着手中
  - B3（出荷以降）: 据付/納入/出荷/引渡/搬入/出荷時
  - B4（整理前）: 整理前/整理中/整理（一時置き場）

---

## 2. 追加する構成

### 新規ファイル（追加するだけ、既存は無変更）

```
プロジェクトルート/
├── 91GDX・252WORKNO-program/   ← 既存（変更なし）
├── 91OTHER-program/            ← 既存（変更なし）
├── run_gdx.py                  ← 既存（変更なし）
├── run_91other.py              ← 既存（変更なし）
│
├── rag/                        ← 新規追加
│   ├── __init__.py
│   ├── config.py               ← Azure接続設定
│   ├── indexer.py              ← AI Searchへの登録・更新
│   └── search.py              ← 検索ロジック（Streamlit用）
│
├── search_app.py               ← Streamlit検索UI（タブレット向け）
├── run_rag_index.py            ← バッチ後に呼ぶインデックス更新スクリプト
└── .env                        ← Azure APIキー（gitignore対象）
```

### 既存バッチへの追記箇所（最小限）

`run_gdx.py` の `main()` 末尾に1行追加するだけ：

```python
def main():
    _ensure_pkg("gdxpkg", PKG_DIR)
    importlib.invalidate_caches()
    cli = importlib.import_module("gdxpkg.cli")
    cli.main()

    # ★ここだけ追加
    import subprocess
    subprocess.run([sys.executable, "run_rag_index.py"], check=False)
```

---

## 3. Azure AI Search インデックス設計

### インデックス名: `photo-index`

| フィールド名 | 型 | 検索 | フィルタ | 備考 |
|---|---|---|---|---|
| `id` | String (Key) | - | - | ファイルパスのSHA256ハッシュ |
| `file_path` | String | ○ | - | Zドライブの絶対パス |
| `file_name` | String | ○ | - | ファイル名のみ |
| `workno` | String | ○ | ○ | 工番（例: `1234-01`） |
| `workno_name` | String | ○ | - | 工事名（マスタCSVより） |
| `phase` | String | - | ○ | B1/B2/B3/B4 |
| `media_type` | String | - | ○ | photo / video |
| `capture_date` | String | - | ○ | YYMMDD（ファイル名から） |
| `extension` | String | - | ○ | .jpg/.mp4 等 |
| `folder_path` | String | ○ | - | 親フォルダパス |
| `indexed_at` | DateTimeOffset | - | ○ | インデックス登録日時 |

### 検索クエリ例

```python
# 工番で検索
results = search_client.search("1234-01", filter="phase eq 'B1'")

# 工事名で検索
results = search_client.search("高知 油圧", filter="media_type eq 'photo'")

# 期間絞り込み
results = search_client.search("*", filter="capture_date ge '250101' and capture_date le '251231'")
```

---

## 4. Streamlit検索UI（search_app.py）

### 画面構成

```
┌─────────────────────────────────────┐
│  Zフォルダ 写真・動画検索            │
├─────────────────────────────────────┤
│  🔍 [工番・工事名・フォルダ名で検索 ] │
│                                     │
│  フィルタ: [B1▼] [写真▼] [年月▼]    │
├─────────────────────────────────────┤
│  検索結果: 32件                      │
│                                     │
│  📁 1234-01_001_250611.jpg          │
│     工番: 1234-01 | 高知プラント工事  │
│     B2着手中 | 2025-06-11           │
│     Z:\takachiho\...\1234-01_B2\... │
│                                     │
│  📁 1234-01_002_250611.jpg          │
│  ...                                │
└─────────────────────────────────────┘
```

---

## 5. 実装ステップ

### Phase 1: Azure環境構築
1. Azure AI Search リソース作成（Free or Basic tier）
2. Azure OpenAI リソース作成（text-embedding-3-small デプロイ）
3. `.env` にAPIキー設定

### Phase 2: インデクサー実装
1. `rag/config.py` - 接続設定
2. `rag/indexer.py` - インデックス作成 + ファイル登録
3. `run_rag_index.py` - 91フォルダ全体をスキャンして一括登録

### Phase 3: 初回インデックス構築
1. デスクトップPCで `run_rag_index.py` を実行（既存ファイルを全件登録）

### Phase 4: バッチへの組み込み
1. `run_gdx.py` 末尾に `run_rag_index.py` 呼び出しを追加
2. 深夜バッチ後に自動でインデックス更新される状態にする

### Phase 5: 検索UI
1. `search_app.py`（Streamlit）を作成
2. デスクトップPCで `streamlit run search_app.py` を常駐
3. タブレットからブラウザでアクセス確認

### Phase 6: 動作確認
1. Notebook PCのブラウザで検索テスト
2. 工場・事業所タブレットから接続テスト

---

## 6. コスト試算（月額目安）

| リソース | Tier | 月額 |
|---|---|---|
| Azure AI Search | Free (50MBまで) | $0 |
| Azure AI Search | Basic（本番移行後） | 約$75/月 |
| Azure OpenAI Embedding | text-embedding-3-small | ファイル数次第・安価 |
| Azure App Service | 不要（Desktop PCで直接ホスト） | $0 |

写真数が数万件以内ならFree tierで十分試せます。

---

## 7. 注意事項

- `.env` はGitHub管理外（`.gitignore` に追加必須）
- インデックスは差分更新（毎回全件再登録ではない）
- ファイル削除・リネームが発生した場合は `id`（パスハッシュ）で追跡
- タブレットからZドライブへのアクセスはVPN接続前提
