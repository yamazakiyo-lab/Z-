import os
import sys
import types
import importlib
import importlib.util

HERE = os.path.dirname(__file__)
PKG_DIR = os.path.join(HERE, "91OTHER-program")

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
        import otherpkg.cleanup as cleanup
        if hasattr(cleanup, "main"):
            cleanup.main()
        else:
            print("otherpkg.cleanup has no main()")
    except Exception as e:
        print("Error running otherpkg.cleanup:", e)

if __name__ == "__main__":
    main()
