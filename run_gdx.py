import os
import subprocess
import sys
import types
import importlib

HERE = os.path.dirname(__file__)
PKG_DIR = os.path.join(HERE, "91GDX・252WORKNO-program")

def _ensure_pkg(name: str, path: str):
    if name in sys.modules:
        return
    mod = types.ModuleType(name)
    mod.__path__ = [path]
    sys.modules[name] = mod

def main():
    _ensure_pkg("gdxpkg", PKG_DIR)
    # pass-through CLI args
    importlib.invalidate_caches()
    cli = importlib.import_module("gdxpkg.cli")
    cli.main()

    # RAG インデックスを最新化（manifest.json 更新）
    rag_script = os.path.join(HERE, "run_rag_index.py")
    if os.path.exists(rag_script):
        print("[run_gdx] run_rag_index.py を実行します...")
        subprocess.run([sys.executable, rag_script], check=False)

if __name__ == "__main__":
    main()
