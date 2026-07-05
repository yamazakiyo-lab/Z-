# -*- coding: utf-8 -*-
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import sys

# This wrapper loads the original script file (which may have a non-standard filename)
# and exposes a `main()` function so the package can be imported normally.
_orig = Path(__file__).parent / "91フォルダ以外整理"
if not _orig.exists():
    raise FileNotFoundError(f"Original script not found: {_orig}")

print(f"[cleanup] Loading script: {_orig}", file=sys.stderr, flush=True)
spec = importlib.util.spec_from_file_location("otherpkg.orig_script", str(_orig))
if spec is None or spec.loader is None:
    print("[cleanup] spec is None or spec.loader is None, using SourceFileLoader", file=sys.stderr, flush=True)
    loader = SourceFileLoader("otherpkg.orig_script", str(_orig))
    spec = importlib.util.spec_from_loader("otherpkg.orig_script", loader)

try:
    _mod = importlib.util.module_from_spec(spec)
    print("[cleanup] module_from_spec created", file=sys.stderr, flush=True)
    spec.loader.exec_module(_mod)
    print("[cleanup] module loaded successfully", file=sys.stderr, flush=True)
except Exception as e:
    print(f"[cleanup] Failed to load module: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
    import traceback
    traceback.print_exc(file=sys.stderr)
    raise

def main(*args, **kwargs):
    try:
        if hasattr(_mod, "json_main"):
            print("[cleanup] calling json_main()", file=sys.stderr, flush=True)
            return _mod.json_main(*args, **kwargs)
        if hasattr(_mod, "main"):
            print("[cleanup] calling main()", file=sys.stderr, flush=True)
            return _mod.main(*args, **kwargs)
        # If original script has no main(), try calling module-level execution
        print("[cleanup] No main() or json_main() found", file=sys.stderr, flush=True)
        return None
    except Exception as e:
        print(f"[cleanup] Error in main(): {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        import traceback
        traceback.print_exc(file=sys.stderr)
        raise

# Re-export common names if present
for name in ("Config", "GeneralOrganizer", "Logger", "Progress"):
    if hasattr(_mod, name):
        globals()[name] = getattr(_mod, name)
