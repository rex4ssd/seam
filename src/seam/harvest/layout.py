"""
harvest/layout.py — directory layout helpers for the 2T archive.

Layout:
  <target_dir>/
    _index.jsonl
    <lang>/
      <owner>__<repo>/
        .seam-meta.json
        STRENGTH.md
        <repo files…>

Rules:
  - lang folder = map_language(github_language) — see HarvestCfg.lang_map
  - repo dir = slug with '/' → '__'
  - .seam-meta.json written at clone time, read at analyze time
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── path helpers ──────────────────────────────────────────────────────────────

def slug_to_dir_name(slug: str) -> str:
    """'owner/repo' → 'owner__repo'"""
    return slug.replace("/", "__")


def repo_dir(target_dir: Path, lang_folder: str, slug: str) -> Path:
    """Return the absolute clone path for a given repo."""
    return target_dir / lang_folder / slug_to_dir_name(slug)


def meta_path(repo_clone_dir: Path) -> Path:
    return repo_clone_dir / ".seam-meta.json"


def strength_report_path(repo_clone_dir: Path) -> Path:
    return repo_clone_dir / "STRENGTH.md"


# ── .seam-meta.json ───────────────────────────────────────────────────────────

def write_meta(
    repo_clone_dir: Path,
    slug: str,
    commit: str,
    language: str,
    stars: int,
    clone_url: str,
    size_kb: int = 0,
    extra: dict[str, Any] | None = None,
) -> None:
    """Write .seam-meta.json into the cloned repo dir."""
    data: dict[str, Any] = {
        "candidate_id": slug,
        "commit": commit,
        "language": language,
        "stars": stars,
        "clone_url": clone_url,
        "size_kb": size_kb,
        "cloned_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        data.update(extra)
    path = meta_path(repo_clone_dir)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())


def read_meta(repo_clone_dir: Path) -> dict[str, Any] | None:
    """Read .seam-meta.json; return None if missing or corrupt."""
    path = meta_path(repo_clone_dir)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# ── layout validation ─────────────────────────────────────────────────────────

def assert_target_dir(target_dir: Path) -> None:
    """
    Raise ValueError if target_dir is not configured or does not exist.
    (per harvest_plan §5 — not silently created)
    """
    if not target_dir or str(target_dir) in ("", "."):
        raise ValueError(
            "harvest.target_dir is not set in profile.yaml. "
            "Set it to the 2T mount path (e.g. /Volumes/2T/github_open_project)."
        )
    if not target_dir.exists():
        raise ValueError(
            f"harvest.target_dir does not exist: {target_dir}\n"
            "Mount the drive or create the directory before running seam harvest."
        )
    if not target_dir.is_dir():
        raise ValueError(f"harvest.target_dir is not a directory: {target_dir}")


def ensure_lang_dir(target_dir: Path, lang_folder: str) -> Path:
    """Create <target_dir>/<lang_folder>/ if needed and return it."""
    d = target_dir / lang_folder
    d.mkdir(parents=True, exist_ok=True)
    return d
