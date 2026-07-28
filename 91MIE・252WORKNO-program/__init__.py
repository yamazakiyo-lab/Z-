"""gdx91: 91フォルダ整理＆GDX同期スクリプトパッケージ

このパッケージは以下の機能を提供します。

- Google Drive から GDExtraction へダウンロード
- GDExtraction から 91 へのメディア移動
- 91 フォルダのメディア振り分け・リネーム・空フォルダ削除

このパッケージは `python -m gdx91` で実行できます。
"""

from .cli import main  # noqa: F401

__all__ = ["main"]
