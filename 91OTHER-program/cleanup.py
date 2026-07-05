import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import threading
import sys

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

# Patch count_media_files_excluding_special with timeout
_original_count = None

def _timeout_wrapper(org_instance, base):
    """count_media_files_excluding_special にタイムアウト機構を追加"""
    result = [0]  # mutable container to capture result from thread
    exception_holder = [None]
    
    def run_count():
        try:
            result[0] = _original_count(org_instance, base)
        except Exception as e:
            exception_holder[0] = e
    
    thread = threading.Thread(target=run_count, daemon=True)
    thread.start()
    thread.join(timeout=180)  # 3分タイムアウト
    
    if thread.is_alive():
        print(f"[TIMEOUT] count_media_files_excluding_special timeout after 180s, returning 0", flush=True)
        return 0
    
    if exception_holder[0]:
        raise exception_holder[0]
    
    return result[0]

def main(*args, **kwargs):
    global _original_count
    print(f"[CLEANUP] Starting cleanup.main()", flush=True)
    try:
        if hasattr(_mod, "GeneralOrganizer"):
            # Patch the method
            original_method = _mod.GeneralOrganizer.count_media_files_excluding_special
            _original_count = original_method
            
            def patched_count(self, base):
                return _timeout_wrapper(self, base)
            
            _mod.GeneralOrganizer.count_media_files_excluding_special = patched_count
            print(f"[CLEANUP] Patched count_media_files_excluding_special with timeout", flush=True)
        
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
