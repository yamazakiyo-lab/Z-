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
    if hasattr(_mod, "json_main"):
        return _mod.json_main(*args, **kwargs)
    if hasattr(_mod, "main"):
        return _mod.main(*args, **kwargs)
    # If original script has no main(), try calling module-level execution
    return None

# Re-export common names if present
for name in ("Config", "GeneralOrganizer", "Logger", "Progress"):
    if hasattr(_mod, name):
        globals()[name] = getattr(_mod, name)
