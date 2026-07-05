import os
import sys
import types
import importlib
import importlib.util

HERE = os.path.dirname(__file__)
PKG_DIR = os.path.join(HERE, "91OTHER-program")

# ========================================================
# エンコーディング設定
# ========================================================
import io
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ('utf-8', 'utf8'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

def _ensure_pkg(name: str, path: str):
    if name in sys.modules:
        return
    mod = types.ModuleType(name)
    mod.__path__ = [path]
    sys.modules[name] = mod

def main():
    _ensure_pkg("otherpkg", PKG_DIR)
    importlib.invalidate_caches()
    # Import the packaged module `otherpkg.cleanup` and invoke main()
    try:
        print("[OTHER] パッケージ読み込み開始", flush=True)
        import otherpkg.cleanup as cleanup
        print("[OTHER] cleanup モジュール読み込み完了", flush=True)
        if hasattr(cleanup, "main"):
            print("[OTHER] cleanup.main() 実行開始", flush=True)
            cleanup.main()
            print("[OTHER] cleanup.main() 実行完了", flush=True)
        else:
            print("otherpkg.cleanup has no main()", file=sys.stderr, flush=True)
            sys.exit(1)
    except Exception as e:
        import traceback
        print(f"[ERROR] run_91other.py でエラー発生:", file=sys.stderr, flush=True)
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
