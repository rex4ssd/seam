from __future__ import annotations
import json
import os
from datetime import date, timedelta
from pathlib import Path

from .models import Pick


def _seam_dir(profile_dir: Path | None = None) -> Path:
    if profile_dir:
        return profile_dir
    # walk up to find .seam/
    d = Path.cwd().resolve()
    for parent in [d, *d.parents]:
        p = parent / ".seam"
        if p.is_dir():
            return p
    # fallback: cwd/.seam
    return Path.cwd() / ".seam"


def picks_log_path(profile_dir: Path | None = None) -> Path:
    return _seam_dir(profile_dir) / "picks.jsonl"


def append_picks(picks: list[Pick], profile_dir: Path | None = None) -> None:
    """Atomic per-line append with fsync. (P-S02)"""
    path = picks_log_path(profile_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        for pick in picks:
            line = json.dumps(pick.to_dict(), ensure_ascii=False)
            f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


def load_picks(
    profile_dir: Path | None = None,
    days: int = 7,
    since: date | None = None,
    until: date | None = None,
) -> list[dict]:
    path = picks_log_path(profile_dir)
    if not path.exists():
        return []

    cutoff = (since or (date.today() - timedelta(days=days))).isoformat()
    end = (until or date.today()).isoformat()

    results = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            d = row.get("date", "")
            if cutoff <= d <= end:
                results.append(row)
    return results


def seen_ids(profile_dir: Path | None = None, cooldown_days: int = 14) -> set[str]:
    """Return repo IDs seen within the cooldown window. (P-S04)"""
    cutoff = (date.today() - timedelta(days=cooldown_days)).isoformat()
    path = picks_log_path(profile_dir)
    if not path.exists():
        return set()
    ids: set[str] = set()
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("date", "") >= cutoff:
                ids.add(row.get("id", ""))
    return ids
