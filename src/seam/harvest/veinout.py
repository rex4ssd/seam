"""
harvest/veinout.py — Seam → Vein handoff (architecture §3).

Two methods:
  A. to_vein_lines()       → 'owner/repo --tag lang:x --tag strength:y --tag seam-score:n'
                             pipe to: while read l; do vein fetch $l; done
  B. write_vein_watchlist() → write collection seam-<day> into .vein/watchlist.yaml
                             consumed by: vein night-harvest

No imports from Vein internals — file-contract only (D-002).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from ..core.models import StrengthReport
from ..core.store import atomic_write_text


class WatchlistError(RuntimeError):
    """Raised when the existing watchlist.yaml cannot be parsed — we refuse
    to overwrite it (the file may contain collections from other tools)."""


# ── public API ─────────────────────────────────────────────────────────────────

def to_vein_lines(reps: list[StrengthReport]) -> list[str]:
    """
    Method A: one line per repo, suitable for:
      seam harvest --pipe-vein | while read l; do vein fetch $l; done

    Format: 'owner/repo --tag lang:x --tag strength:y --tag seam-score:n --tag seam-pick:date'
    """
    return [_vein_line(rep) for rep in reps]


def write_vein_watchlist(
    reps: list[StrengthReport],
    vein_dir: Path,
    day: str,        # "YYYY-MM-DD"
) -> Path:
    """
    Method B: write (or update) a collection in .vein/watchlist.yaml.

    Collection key: seam-<day>
    Structure:
      seam-2026-06-02:
        repos:
          - astral-sh/ruff
          - simonw/llm
        compare: true

    Idempotent: re-running for same day overwrites (repos list replaced, not appended).
    Returns the path to the watchlist file.
    """
    wl_path = vein_dir / "watchlist.yaml"
    vein_dir.mkdir(parents=True, exist_ok=True)

    # Load existing watchlist (may not exist yet).
    # A file that exists but cannot be parsed as a YAML mapping is NOT
    # silently replaced — that would wipe every other collection in it.
    existing: dict = {}
    if wl_path.exists():
        try:
            data = yaml.safe_load(wl_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise WatchlistError(
                f"cannot parse existing watchlist ({wl_path}): {exc} — "
                "refusing to overwrite; fix or move the file and retry"
            ) from exc
        if data is None:
            existing = {}
        elif isinstance(data, dict):
            existing = data
        else:
            raise WatchlistError(
                f"existing watchlist is not a YAML mapping ({wl_path}) — "
                "refusing to overwrite; fix or move the file and retry"
            )

    collection_key = f"seam-{day}"
    existing[collection_key] = {
        "repos": [rep.candidate_id for rep in reps],
        "compare": True,
    }

    # temp + fsync + atomic replace: a crash mid-write never truncates
    # or corrupts the shared watchlist file.
    atomic_write_text(
        wl_path,
        yaml.dump(existing, default_flow_style=False, allow_unicode=True),
    )
    return wl_path


# ── internals ──────────────────────────────────────────────────────────────────

def _vein_line(rep: StrengthReport) -> str:
    """Build one vein-fetch-compatible line for a StrengthReport."""
    tags: list[str] = []

    # lang:<primary>
    tags.append(f"lang:{_safe_tag(rep.language)}")

    # strength:<tag> for each strength tag
    for t in rep.strength_tags:
        tags.append(f"strength:{_safe_tag(t)}")

    # seam-score:<overall> — use average of dimensions as overall proxy
    if rep.dimensions:
        avg = int(sum(rep.dimensions.values()) / len(rep.dimensions))
        tags.append(f"seam-score:{avg}")

    # seam-pick:<date> — derived from analyzed_at (YYYY-MM-DD prefix)
    if rep.analyzed_at:
        day = rep.analyzed_at[:10]
        tags.append(f"seam-pick:{day}")

    tag_args = " ".join(f"--tag {t}" for t in tags)
    return f"{rep.candidate_id} {tag_args}".strip()


def _safe_tag(value: str) -> str:
    """Replace characters not safe in shell tag values with hyphens."""
    return re.sub(r"[^a-zA-Z0-9:._-]", "-", value)
