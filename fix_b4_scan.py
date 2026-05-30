def remove_junk_files_in_dir(path: Path):
    """
    指定ディレクトリ直下のThumbs系・.TMP・JUNK_FILESを削除
    """
    print(f"      [DEBUG] scanning: {path}")
    match = False
    for f in path.iterdir():
        print(f"      [DEBUG] found: {f.name}")
        if f.is_file():
            match = (
                f.name in JUNK_FILES
                or "Thumbs" in f.name
                or f.name.lower().endswith(".tmp")
            )
            print(f"      [DEBUG] match={match}, DRYRUN={DRYRUN}, file={f.name}")
            if match:
                print(f"      -> remove junk file: {f}")
                if not DRYRUN:
                    try:
                        f.unlink()
                        print(f"      [OK] removed: {f}")
                    except Exception as e:
                        print(f"      [WARN] junk remove failed: {f} ({e})")
    return match
import sys
from pathlib import Path

# ====== 設定 ======
ROOT = Path(r"Z:/takachiho/2to9_業務別フォルダ/91_工番別実績写真・動画")  # 必要に応じて変更
DRYRUN = False  # True の間は削除/移動は実行されません。確認後 False にしてください.

JUNK_FILES = {"desktop.ini", ".DS_Store"}
# ====== B4 フォルダ探索 ======
def find_b4_dirs(root: Path):
    out = []
    for a in root.iterdir():
        if not a.is_dir():
            continue
        for p in a.iterdir():
            if p.is_dir() and p.name.endswith("B4整理前写真・動画"):
                out.append(p)
    return out

def inspect_b4(b4: Path):
    items = list(b4.iterdir())
    subdirs = [x for x in items if x.is_dir()]
    files = [x for x in items if x.is_file()]
    return subdirs, files

def main():
    b4s = find_b4_dirs(ROOT)
    print(f"found B4 dirs: {len(b4s)}")
    for b4 in sorted(b4s):
        subdirs, files = inspect_b4(b4)
        print(f"\nB4: {b4}")
        print(f"  subdirs: {len(subdirs)}, files: {len(files)}")
        # サブディレクトリ内のジャンクファイルも削除
        if subdirs:
            for sd in subdirs:
                children = list(sd.iterdir())
                print(f"    SD: {sd.name} / children={len(children)}")
                remove_junk_files_in_dir(sd)
                # サブディレクトリが空になったら削除
                try:
                    if not list(sd.iterdir()):
                        print(f"      -> remove empty subdir: {sd}")
                        if not DRYRUN:
                            sd.rmdir()
                except Exception as e:
                    print(f"      [WARN] remove failed: {sd} ({e})")
        if files:
            for f in files:
                print(f"    FILE: {f.name}")
        # B4直下のジャンクファイル削除
        remove_junk_files_in_dir(b4)
        # オプション: 空のサブフォルダを削除する
        for sd in subdirs:
            try:
                if not list(sd.iterdir()):
                    print(f"      -> remove empty subdir: {sd}")
                    if not DRYRUN:
                        sd.rmdir()
            except Exception as e:
                print(f"      [WARN] remove failed: {sd} ({e})")

        # オプション: B4 が完全に空なら削除
        try:
            if not list(b4.iterdir()):
                print(f"  -> remove empty B4: {b4}")
                if not DRYRUN:
                    b4.rmdir()
        except Exception as e:
            print(f"  [WARN] remove B4 failed: {e}")

if __name__ == '__main__':
    main()
