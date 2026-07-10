#!/usr/bin/env python3
"""
seam_fix_unknown.py — move repos from _unknown/ to correct language folder.

Usage:
  python seam_fix_unknown.py           # dry-run (show what would move)
  python seam_fix_unknown.py --yes     # actually move
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

from seam.core.config import load_config, find_profile
from seam.harvest.layout import read_meta, write_meta, meta_path

# extension → GitHub language name (must match lang_map in profile.yaml)
_EXT_TO_LANG: dict[str, str] = {
    ".rs":    "Rust",
    ".py":    "Python",
    ".ts":    "TypeScript",
    ".tsx":   "TypeScript",
    ".js":    "JavaScript",
    ".jsx":   "JavaScript",
    ".go":    "Go",
    ".swift": "Swift",
    ".cpp":   "C++",
    ".cc":    "C++",
    ".cxx":   "C++",
    ".c":     "C",
    ".h":     "C",
}

_SKIP_DIRS = {".git", "node_modules", "target", "__pycache__", ".venv"}


def detect_language(repo_dir: Path, max_files: int = 2000) -> str:
    """Count source files by extension, return dominant GitHub language name."""
    counts: Counter = Counter()
    scanned = 0
    for p in repo_dir.rglob("*"):
        if scanned >= max_files:
            break
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        if p.is_file():
            lang = _EXT_TO_LANG.get(p.suffix.lower())
            if lang:
                counts[lang] += 1
            scanned += 1
    return counts.most_common(1)[0][0] if counts else ""


def fix_unknown(target_dir: Path, cfg, dry_run: bool) -> None:
    unknown_dir = target_dir / "_unknown"
    if not unknown_dir.exists():
        print("_unknown/ not found — nothing to do")
        return

    repos = [d for d in unknown_dir.iterdir() if d.is_dir() and not d.name.startswith("seam_clone_")]
    if not repos:
        print("_unknown/ is empty")
        return

    for repo_dir in sorted(repos):
        meta = read_meta(repo_dir)
        slug = meta.get("candidate_id", "") if meta else ""
        if not slug:
            # fall back to dir name
            slug = repo_dir.name.replace("__", "/", 1)

        # detect language from files
        github_lang = detect_language(repo_dir)
        lang_folder = cfg.harvest.map_language(github_lang) if github_lang else "_unknown"

        if lang_folder == "_unknown":
            print(f"  ⚠ {slug}: could not detect language, keeping in _unknown")
            continue

        dest = target_dir / lang_folder / repo_dir.name
        print(f"  {slug}: {github_lang} → {lang_folder}/")
        print(f"    {repo_dir} → {dest}")

        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(repo_dir), str(dest))
            # update .seam-meta.json language field
            if meta:
                write_meta(
                    dest,
                    slug=meta.get("candidate_id", slug),
                    commit=meta.get("commit", ""),
                    language=lang_folder,
                    stars=meta.get("stars", 0),
                    clone_url=meta.get("clone_url", f"https://github.com/{slug}.git"),
                    size_kb=meta.get("size_kb", 0),
                )
            print(f"    moved ✓")

    # remove _unknown dir if now empty
    if not dry_run:
        try:
            unknown_dir.rmdir()
            print("_unknown/ removed (empty)")
        except OSError:
            remaining = list(unknown_dir.iterdir())
            print(f"_unknown/ kept ({len(remaining)} item(s) remain)")


def main() -> None:
    dry_run = "--yes" not in sys.argv
    if dry_run:
        print("=== DRY RUN (add --yes to apply) ===\n")
    else:
        print("=== APPLYING ===\n")

    cfg = load_config(find_profile())
    target_dir = cfg.harvest_target_dir()
    fix_unknown(target_dir, cfg, dry_run)


if __name__ == "__main__":
    main()
