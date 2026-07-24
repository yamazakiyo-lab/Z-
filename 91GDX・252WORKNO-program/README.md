# 91フォルダ整理＆GDX同期スクリプト

> **GDX卒業 (2026-07-24)**: 写真取り込みは LINE WORKS bot に一本化した。
> デイリーランは `GDX_NO_DRIVE=1`（run_gdx_logged.ps1 で設定）により
> Google Drive 連携（下記の [1] 吸い取り / [4] 同期）をスキップして動く。
> 91整理・工番マスタ整合・B4投入などのローカル処理は従来どおり有効。
> マスタCSV（工事一覧表・発注者一覧表）の恒久置き場は `<91ルート>\_masters`
> （旧 `_GDExtraction` にもフォールバックする）。

このリポジトリは以下の機能を提供します。

- ~~Google Drive から GDExtraction へのダウンロード~~（GDX卒業により停止中）
- GDExtraction 直下フォルダ名と配下ファイル名の工番マスタ整合
- GDExtraction から 91 へのメディア移動（B4 への整理）
- 91 フォルダの B1/B2/B3/B4 への振り分け・リネーム・空フォルダ削除
- ~~GDExtraction のフォルダ構成を Drive へ戻す際の旧名フォルダ掃除~~（GDX卒業により停止中）

## 使い方

### 1) 依存パッケージをインストール

```powershell
cd "C:\Users\Yamazakiyo\tseg_vscode\Zフォルダ整理\91GDX・252WORKNO-program"
py -m pip install -r requirements.txt
```

### 2) 実行

```powershell
cd "C:\Users\Yamazakiyo\tseg_vscode\Zフォルダ整理"
py -m gdx91 --help
```

もしくは従来の呼び出し方法として：

```powershell
py "91フォルダ整理&GDX.py" --help
```

## 主要ファイル構成

- `gdx91/` - モジュール化されたパッケージ本体
  - `cli.py` - CLI 入口とワークフロー制御
  - `drive_sync.py` - Google Drive との同期/ダウンロード
  - `master.py` - マスタ CSV 読み込み／移動処理
  - `organizer.py` - 91 フォルダ整理（B1〜B4 への振り分け・リネーム）
  - `config.py` - 設定データクラス
  - `utils.py` - 共通ユーティリティ

- `91フォルダ整理&GDX.py` - 従来互換の実行用ラッパー
- `91フォルダ整理&GDX.legacy` - 元の（モノリシック）実装のバックアップ

---

## 注意

- Google Drive 連携には `google-api-python-client` などの依存が必要です。
- `requirements.txt` を使ってインストールしてください。
