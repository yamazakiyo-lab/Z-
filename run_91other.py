import os
import sys
import types
import importlib
import importlib.util
import threading
import time

HERE = os.path.dirname(__file__)
PKG_DIR = os.path.join(HERE, "91OTHER-program")

def _ensure_pkg(name: str, path: str):
    if name in sys.modules:
        return
    mod = types.ModuleType(name)
    mod.__path__ = [path]
    sys.modules[name] = mod

class TimeoutException(Exception):
    pass

_timeout_flag = False

def timeout_monitor(timeout_sec: int):
    """タイムアウト監視スレッド"""
    global _timeout_flag
    time.sleep(timeout_sec)
    _timeout_flag = True
    print(f"[TIMEOUT] OTHER処理が{timeout_sec}秒を超過しました。終了します。", file=sys.stderr)
    os._exit(1)  # 強制終了

def main():
    _ensure_pkg("otherpkg", PKG_DIR)
    importlib.invalidate_caches()
    
    # バックグラウンド監視スレッド開始（30分 = 1800秒）
    timeout_sec = 1800
    monitor_thread = threading.Thread(target=timeout_monitor, args=(timeout_sec,), daemon=True)
    monitor_thread.start()
    
    try:
        import otherpkg.cleanup as cleanup
        if hasattr(cleanup, "main"):
            print(f"[OTHER] 処理開始（タイムアウト: {timeout_sec}秒）", flush=True)
            cleanup.main()
            print(f"[OTHER] 処理完了", flush=True)
        else:
            print("otherpkg.cleanup has no main()")
            sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Error running otherpkg.cleanup: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
