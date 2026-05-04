"""Workspace cleanup helper.

Usage:
  python run_cleanup.py           # dry-run (default)
  python run_cleanup.py --apply   # actually move files

The script scans the two project folders and reports (or moves) common junk files
and problematic directories into an archive folder (`.archive`) under each project.
"""
from __future__ import annotations

import argparse
import os
import shutil
import re
from pathlib import Path
from typing import List

PROJECTS = [
    Path("91GDX・252WORKNO-program"),
    Path("91OTHER-program"),
]

# directories to skip entirely (common virtualenv / vcs folders)
EXCLUDE_DIRS = {"venv", ".git", ".archive"}

JUNK_FILENAMES = {"Thumbs.db", "Thumbs_2.db", ".DS_Store", "desktop.ini"}
JUNK_DIRNAMES = {"__pycache__", ".ipynb_checkpoints"}


def safe_move(src: Path, dest_dir: Path, apply: bool) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if apply:
        try:
            shutil.move(str(src), str(dest))
            print(f"MOVED: {src} -> {dest}")
        except Exception as e:
            print(f"ERROR moving {src}: {e}")
    else:
        print(f"[DRY] MOVE: {src} -> {dest}")


def normalize_name(name: str) -> str:
    # trim, convert multiple spaces to single underscore, remove illegal chars for Windows
    s = name.strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r'[<>:"/\\|?*]', "_", s)
    return s


def run_project_cleanup(proj: Path, apply: bool) -> None:
    if not proj.exists():
        print(f"Skip (not found): {proj}")
        return
    archive = proj / ".archive"
    print(f"Scanning: {proj}")

    for root, dirs, files in os.walk(proj):
        p_root = Path(root)
        # skip archive itself
        if p_root == archive or archive in p_root.parents:
            continue
        # remove excluded dirs from traversal
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]

        # directories to archive
        for d in list(dirs):
            if d in JUNK_DIRNAMES:
                target = archive / "dirs"
                src = p_root / d
                safe_move(src, target, apply)
                dirs.remove(d)

        # files to archive or rename
        for fname in list(files):
            fp = p_root / fname
            if fname in JUNK_FILENAMES:
                target = archive / "files"
                safe_move(fp, target, apply)
                continue

            # filenames with trailing/leading spaces or multiple spaces
            normalized = normalize_name(fname)
            if normalized != fname:
                new_path = p_root / normalized
                if apply:
                    try:
                        fp.rename(new_path)
                        print(f"RENAMED: {fp} -> {new_path}")
                    except Exception as e:
                        print(f"ERROR rename {fp}: {e}")
                else:
                    print(f"[DRY] RENAME: {fp} -> {new_path}")


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Actually perform moves/renames")
    ap.add_argument("--projects", nargs="*", help="List of project folders to process (optional)")
    args = ap.parse_args(argv)

    targets = PROJECTS
    if args.projects:
        targets = [Path(p) for p in args.projects]

    for proj in targets:
        run_project_cleanup(proj, args.apply)

    print("Done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
