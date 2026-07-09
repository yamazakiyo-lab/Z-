"""アノテーション状態を診断するスクリプト。

使い方:
  py check_annotation_state.py
"""
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

BLOB_CONTAINER = "lw-raw"
JST = timezone(timedelta(hours=9))


def _get_container():
    conn_str = os.environ.get("AZURE_BLOB_CONNECTION_STRING")
    if not conn_str:
        print("ERROR: AZURE_BLOB_CONNECTION_STRING が未設定です")
        return None
    from azure.storage.blob import BlobServiceClient
    client = BlobServiceClient.from_connection_string(conn_str)
    return client.get_container_client(BLOB_CONTAINER)


def main():
    container = _get_container()
    if container is None:
        return

    print()
    print("═" * 56)
    print("  アノテーション状態診断")
    print("═" * 56)

    # 1. annotation_state.json（Blob）
    print("\n【Blob: annotation_state.json】")
    try:
        raw = container.download_blob("annotation_state.json").readall()
        state = json.loads(raw.decode("utf-8"))
        updated = state.get("updated_at", "")
        if updated:
            try:
                dt = datetime.fromisoformat(updated).astimezone(JST)
                updated = dt.strftime("%Y-%m-%d %H:%M JST")
            except Exception:
                pass
        print(f"  更新日時    : {updated or '（なし）'}")
        users     = state.get("users", [])
        want_next = state.get("want_next", [])
        pending   = state.get("pending", {})
        print(f"  users件数   : {len(users)}")
        print(f"  want_next   : {len(want_next)}")
        print(f"  pending件数 : {len(pending)}")
        if not users:
            print("  ⚠️  users が空 → --send は何もしません（--add-user が必要）")
        if pending:
            print("  pending 例 (先頭5件):")
            for i, (k, v) in enumerate(list(pending.items())[:5]):
                fname = v.get("file_name", k)
                print(f"    [{i+1}] {fname}")
        print(f"  (Blob サイズ: {len(raw):,} bytes)")
    except Exception as e:
        print(f"  取得エラー: {e}")

    # 2. ローカル manifest.json
    print("\n【ローカル: rag/manifest.json】")
    manifest_path = Path(__file__).parent / "rag" / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            print(f"  エントリ数 : {len(manifest)}")
            if manifest:
                first_key = next(iter(manifest))
                print(f"  先頭キー例 : {first_key}")
            else:
                print("  ⚠️  manifest.json は空です（全アノテーション済み or 未同期）")
        except Exception as e:
            print(f"  読み込みエラー: {e}")
    else:
        print(f"  ファイルなし: {manifest_path}")

    # 3. ローカル comments.json
    print("\n【ローカル: rag/comments.json】")
    comments_path = Path(__file__).parent / "rag" / "comments.json"
    if comments_path.exists():
        try:
            comments = json.loads(comments_path.read_text(encoding="utf-8"))
            print(f"  エントリ数: {len(comments)}")
        except Exception as e:
            print(f"  読み込みエラー: {e}")
    else:
        print(f"  ファイルなし: {comments_path}")

    # 4. Blob の gdx_annotations/ ファイル数
    print("\n【Blob: gdx_annotations/ サイドカー数】")
    try:
        count = 0
        for blob in container.list_blobs(name_starts_with="gdx_annotations/"):
            if blob.name.endswith("_annotations/") or blob.name.endswith(".json"):
                count += 1
        print(f"  .json ファイル数: {count}")
    except Exception as e:
        print(f"  取得エラー: {e}")

    print()
    print("═" * 56)
    print(f"  確認時刻: {datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')} JST")
    print()


if __name__ == "__main__":
    main()
