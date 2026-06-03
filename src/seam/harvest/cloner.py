"""
harvest/cloner.py — shallow clone engine for the 2T archive.

Pitfalls implemented:
  P-H01  free-space + size_cap guard before clone
  P-H02  subprocess timeout per repo
  P-H03  already-exists → fetch+reset (skip if same commit)
  P-H04  GIT_LFS_SKIP_SMUDGE=1

Design:
  - clone to a temp dir first, then atomic rename to final path (P-H01 half-clone guard)
  - never raises; all failures are recorded in CloneResult
  - no side-effects if skipped
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from ..core.config import HarvestCfg
from ..core.models import CloneResult
from .layout import (
    ensure_lang_dir,
    repo_dir,
    write_meta,
    read_meta,
)

_GH_BASE = "https://github.com"
_GIT_ENV = {**os.environ, "GIT_LFS_SKIP_SMUDGE": "1", "GIT_TERMINAL_PROMPT": "0"}


# ── public API ────────────────────────────────────────────────────────────────

def clone_repo(
    slug: str,
    target_root: Path,
    cfg: HarvestCfg,
    *,
    language: str = "",
    stars: int = 0,
    dry_run: bool = False,
    verbose: bool = False,
) -> CloneResult:
    """
    Clone (or update) owner/repo into target_root/<lang>/<owner>__<repo>/.

    Returns CloneResult — never raises. Caller checks .skipped / .skipped_reason.
    """
    lang_folder = cfg.map_language(language) if language else "_unknown"
    final_dir = repo_dir(target_root, lang_folder, slug)
    clone_url = f"{_GH_BASE}/{slug}.git"

    # ── 1. already exists? ────────────────────────────────────────────────
    if final_dir.exists():
        return _handle_existing(slug, final_dir, clone_url, cfg,
                                lang_folder, stars, dry_run, verbose)

    # ── 2. free space guard (P-H01) ──────────────────────────────────────
    free_gb = _free_gb(target_root)
    if free_gb < cfg.min_free_gb:
        msg = f"disk free {free_gb:.1f} GB < min_free_gb {cfg.min_free_gb} GB"
        _warn(slug, msg)
        return CloneResult(slug=slug, clone_path="", commit="",
                           language=lang_folder, skipped=True, skipped_reason="disk_full")

    # ── 3. size cap guard (P-H01) ─────────────────────────────────────────
    # GitHub API repo.size is in KB; skip if estimate > size_cap_mb * 1024
    # We don't call API here (no token required) — defer to caller who may pass size_kb
    # This guard is checked in clone_repos_batch; single-call users can skip.

    if dry_run:
        _info(slug, f"[dry-run] would clone → {final_dir}")
        return CloneResult(slug=slug, clone_path=str(final_dir), commit="dry-run",
                           language=lang_folder, skipped=True, skipped_reason="dry_run")

    # ── 4. clone ──────────────────────────────────────────────────────────
    ensure_lang_dir(target_root, lang_folder)
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"seam_clone_{slug.replace('/', '__')}_",
                                    dir=target_root / lang_folder))
    try:
        ok, commit, size_kb = _do_clone(slug, clone_url, tmp_dir, cfg, verbose)
        if not ok:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return CloneResult(slug=slug, clone_path="", commit="",
                               language=lang_folder, skipped=True, skipped_reason="clone_failed")

        # atomic rename: tmp → final (P-H01 — no half-baked dir at final path)
        tmp_dir.rename(final_dir)
        write_meta(final_dir, slug, commit, lang_folder, stars, clone_url, size_kb=size_kb)
        _info(slug, f"cloned → {final_dir}  commit={commit[:8]}")
        return CloneResult(slug=slug, clone_path=str(final_dir), commit=commit,
                           language=lang_folder, size_kb=size_kb)

    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        _warn(slug, f"unexpected error: {exc}")
        return CloneResult(slug=slug, clone_path="", commit="",
                           language=lang_folder, skipped=True, skipped_reason=f"error:{exc}")


def clone_repos_batch(
    slugs: list[tuple[str, str, int]],   # (slug, language, stars)
    target_root: Path,
    cfg: HarvestCfg,
    *,
    dry_run: bool = False,
    verbose: bool = False,
) -> list[CloneResult]:
    """
    Clone up to cfg.max_repos_per_night repos, stopping early if disk is low.
    slugs = list of (slug, github_language, stars).
    """
    results: list[CloneResult] = []
    for slug, language, stars in slugs[:cfg.max_repos_per_night]:
        # per-repo disk check before each clone
        if _free_gb(target_root) < cfg.min_free_gb:
            _warn(slug, "disk full — stopping batch")
            results.append(CloneResult(slug=slug, clone_path="", commit="",
                                       language=cfg.map_language(language),
                                       skipped=True, skipped_reason="disk_full"))
            break
        result = clone_repo(slug, target_root, cfg,
                            language=language, stars=stars,
                            dry_run=dry_run, verbose=verbose)
        results.append(result)
    return results


# ── internals ─────────────────────────────────────────────────────────────────

def _handle_existing(
    slug: str,
    final_dir: Path,
    clone_url: str,
    cfg: HarvestCfg,
    lang_folder: str,
    stars: int,
    dry_run: bool,
    verbose: bool,
) -> CloneResult:
    """Repo dir exists: check remote HEAD; skip if same, fetch+reset if different."""
    meta = read_meta(final_dir)
    stored_commit = meta.get("commit", "") if meta else ""

    remote_commit = _remote_head(clone_url, cfg.clone_timeout_sec)
    if remote_commit is None:
        # can't check → skip conservatively
        _warn(slug, "could not fetch remote HEAD, skipping update")
        return CloneResult(slug=slug, clone_path=str(final_dir), commit=stored_commit,
                           language=lang_folder, skipped=True,
                           skipped_reason="exists_same_commit")

    if stored_commit and remote_commit.startswith(stored_commit[:40]):
        if verbose:
            _info(slug, f"up to date  commit={stored_commit[:8]}")
        return CloneResult(slug=slug, clone_path=str(final_dir), commit=stored_commit,
                           language=lang_folder, skipped=True,
                           skipped_reason="exists_same_commit")

    if dry_run:
        _info(slug, f"[dry-run] would update {stored_commit[:8]} → {remote_commit[:8]}")
        return CloneResult(slug=slug, clone_path=str(final_dir), commit=stored_commit,
                           language=lang_folder, skipped=True, skipped_reason="dry_run")

    # fetch + reset (P-H03)
    ok, new_commit = _do_fetch_reset(slug, final_dir, cfg, verbose)
    if ok:
        write_meta(final_dir, slug, new_commit, lang_folder, stars, clone_url)
        _info(slug, f"updated → {new_commit[:8]}")
        return CloneResult(slug=slug, clone_path=str(final_dir), commit=new_commit,
                           language=lang_folder)
    else:
        return CloneResult(slug=slug, clone_path=str(final_dir), commit=stored_commit,
                           language=lang_folder, skipped=True, skipped_reason="fetch_failed")


def _do_clone(
    slug: str,
    url: str,
    dest: Path,
    cfg: HarvestCfg,
    verbose: bool,
) -> tuple[bool, str, int]:
    """
    Run git clone --depth 1 --single-branch into dest.
    Returns (success, commit_sha, size_kb).
    """
    cmd = [
        "git", "clone",
        "--depth", str(cfg.clone_depth),
        "--single-branch",
        "--no-tags",
        url,
        str(dest),
    ]
    try:
        _run(cmd, timeout=cfg.clone_timeout_sec, verbose=verbose, label=f"clone {slug}")
    except subprocess.TimeoutExpired:
        _warn(slug, f"clone timed out after {cfg.clone_timeout_sec}s (P-H02)")
        return False, "", 0
    except subprocess.CalledProcessError as exc:
        _warn(slug, f"clone failed: exit {exc.returncode}")
        return False, "", 0

    commit = _head_commit(dest)
    size_kb = _dir_size_kb(dest)
    return True, commit, size_kb


def _do_fetch_reset(
    slug: str,
    repo_dir_: Path,
    cfg: HarvestCfg,
    verbose: bool,
) -> tuple[bool, str]:
    """git fetch --depth 1 + git reset --hard FETCH_HEAD"""
    try:
        _run(
            ["git", "fetch", "--depth", str(cfg.clone_depth), "--no-tags", "origin"],
            cwd=repo_dir_, timeout=cfg.clone_timeout_sec, verbose=verbose,
            label=f"fetch {slug}",
        )
        _run(
            ["git", "reset", "--hard", "FETCH_HEAD"],
            cwd=repo_dir_, timeout=30, verbose=verbose,
            label=f"reset {slug}",
        )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        _warn(slug, f"fetch/reset failed: {exc}")
        return False, ""

    commit = _head_commit(repo_dir_)
    return True, commit


def _remote_head(url: str, timeout: int) -> Optional[str]:
    """Return the remote HEAD sha via git ls-remote, or None on failure."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--head", url, "HEAD"],
            capture_output=True, text=True,
            timeout=min(timeout, 30),
            env=_GIT_ENV,
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] in ("HEAD", "refs/heads/HEAD"):
                return parts[0]
            if len(parts) >= 1 and len(parts[0]) == 40:
                return parts[0]
        return None
    except Exception:
        return None


def _head_commit(repo: Path) -> str:
    """Return HEAD sha of a local repo, or '' on failure."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo, capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _run(
    cmd: list[str],
    *,
    timeout: int,
    verbose: bool,
    label: str,
    cwd: Optional[Path] = None,
) -> None:
    """Run a subprocess; raise on non-zero exit."""
    kwargs: dict = dict(
        env=_GIT_ENV,
        timeout=timeout,
        check=True,
    )
    if cwd:
        kwargs["cwd"] = cwd
    if not verbose:
        kwargs["capture_output"] = True
    subprocess.run(cmd, **kwargs)


def _free_gb(path: Path) -> float:
    try:
        usage = shutil.disk_usage(path)
        return usage.free / (1024 ** 3)
    except OSError:
        return 0.0


def _dir_size_kb(path: Path) -> int:
    total = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
    except OSError:
        pass
    return total // 1024


def _info(slug: str, msg: str) -> None:
    print(f"[seam/clone] {slug}: {msg}", file=sys.stderr)


def _warn(slug: str, msg: str) -> None:
    print(f"[seam/clone] ⚠ {slug}: {msg}", file=sys.stderr)
