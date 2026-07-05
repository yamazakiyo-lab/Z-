import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

# This wrapper loads the original script file (which may have a non-standard filename)
# and exposes a `main()` function so the package can be imported normally.
_orig = Path(__file__).parent / "91フォルダ以外整理"
if not _orig.exists():
    raise FileNotFoundError(f"Original script not found: {_orig}")

spec = importlib.util.spec_from_file_location("otherpkg.orig_script", str(_orig))
if spec is None or spec.loader is None:
    loader = SourceFileLoader("otherpkg.orig_script", str(_orig))
    spec = importlib.util.spec_from_loader("otherpkg.orig_script", loader)
_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_mod)

def main(*args, **kwargs):
    print(f"[CLEANUP] Starting cleanup.main()", flush=True)
    try:
        if hasattr(_mod, "json_main"):
            print(f"[CLEANUP] Calling json_main...", flush=True)
            return _mod.json_main(*args, **kwargs)
        if hasattr(_mod, "main"):
            print(f"[CLEANUP] Calling main...", flush=True)
            result = _mod.main(*args, **kwargs)
            print(f"[CLEANUP] main() completed", flush=True)
            return result
        # If original script has no main(), try calling module-level execution
        print(f"[CLEANUP] No json_main or main found", flush=True)
        return None
    except Exception as e:
        print(f"[CLEANUP ERROR] {e}", flush=True)
        import traceback
        traceback.print_exc()
        raise

# Re-export common names if present
for name in ("Config", "GeneralOrganizer", "Logger", "Progress"):
    if hasattr(_mod, name):
        globals()[name] = getattr(_mod, name)
