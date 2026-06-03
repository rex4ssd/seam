"""
runtime/history.py — append-only CSV harvest history.

Schema (harvest_history.csv):
  timestamp, repo, commit, stage, result, scheduled_for

  stage  ∈ clone | signals | analyze | report | veinout
  result ∈ running | pass | fail | skipped | killed

Idempotency key = (repo, commit, stage).
"finalized"  = key has at least one terminal result (pass/fail/killed/skipped).
"in_progress" = key has only 'running' (process was interrupted mid-stage).

Non-idempotent stages (analyze, veinout): conservatively skip if in_progress,
to avoid re-burning expensive ollama calls.
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


_TERMINAL = frozenset({"pass", "fail", "killed", "skipped"})
_NON_IDEMPOTENT = frozenset({"analyze", "veinout"})
_FIELDS = ["timestamp", "repo", "commit", "stage", "result", "scheduled_for"]


def default_history_path() -> Path:
    return Path.home() / ".seam" / "harvest_history.csv"


# ── read ───────────────────────────────────────────────────────────────────────

def load_done_keys(hist: Path) -> tuple[set[tuple], set[tuple]]:
    """
    Return (finalized, in_progress).

    finalized    = {(repo, commit, stage)} — has at least one terminal result.
    in_progress  = {(repo, commit, stage)} — only 'running', no terminal result.
    """
    if not hist.exists():
        return set(), set()

    stage_results: dict[tuple, set[str]] = {}
    for row in _read_rows(hist):
        key = (row["repo"], row["commit"], row["stage"])
        stage_results.setdefault(key, set()).add(row["result"])

    finalized: set[tuple] = set()
    in_progress: set[tuple] = set()
    for key, results in stage_results.items():
        if results & _TERMINAL:
            finalized.add(key)
        elif "running" in results:
            in_progress.add(key)

    return finalized, in_progress


def should_skip(
    repo: str,
    commit: str,
    stage: str,
    finalized: set[tuple],
    in_progress: set[tuple],
) -> bool:
    """
    True → caller should skip this (repo, commit, stage).

    - finalized:                          already has a terminal result → skip
    - in_progress + non-idempotent stage: interrupted mid-way → conservatively skip
      (avoids re-burning expensive ollama calls on uncertain state)
    """
    key = (repo, commit, stage)
    if key in finalized:
        return True
    if key in in_progress and stage in _NON_IDEMPOTENT:
        return True
    return False


def retry_failures_24h(
    hist: Path,
    active_repos: set[str],
) -> list[tuple[str, str, str]]:
    """
    Return list of (repo, commit, stage) that:
      - have a 'fail' result within the last 24 hours
      - repo is in active_repos (not stale)
      - have NOT since succeeded (no 'pass' for same key)

    Deduplicates: each (repo, commit, stage) appears at most once.
    """
    if not hist.exists():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    recent_fails: dict[tuple, datetime] = {}   # key → latest fail time
    passes: set[tuple] = set()

    for row in _read_rows(hist):
        key = (row["repo"], row["commit"], row["stage"])
        try:
            ts = _parse_ts(row["timestamp"])
        except ValueError:
            continue

        if row["result"] == "fail" and ts >= cutoff:
            if key not in recent_fails or ts > recent_fails[key]:
                recent_fails[key] = ts
        elif row["result"] == "pass":
            passes.add(key)

    return [
        (repo, commit, stage)
        for (repo, commit, stage), _ts in recent_fails.items()
        if (repo, commit, stage) not in passes
        and repo in active_repos
    ]


# ── write ──────────────────────────────────────────────────────────────────────

def mark(
    hist: Path,
    repo: str,
    commit: str,
    stage: str,
    result: str,
    scheduled_for: str,
) -> None:
    """
    Append one row to the CSV history.
    flush + fsync on every write (P-S02 pattern).
    """
    hist.parent.mkdir(parents=True, exist_ok=True)
    write_header = not hist.exists() or hist.stat().st_size == 0

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    row = {
        "timestamp":     ts,
        "repo":          repo,
        "commit":        commit,
        "stage":         stage,
        "result":        result,
        "scheduled_for": scheduled_for,
    }
    with open(hist, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)
        f.flush()
        os.fsync(f.fileno())


def now_slot() -> str:
    """Current UTC time as 'YYYY-MM-DD HH:MM' (used for scheduled_for field)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


# ── helpers ────────────────────────────────────────────────────────────────────

def _read_rows(hist: Path) -> list[dict]:
    """Read all valid rows; silently skip malformed lines."""
    rows = []
    try:
        with open(hist, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if all(k in row for k in ("repo", "commit", "stage", "result", "timestamp")):
                    rows.append(row)
    except (OSError, csv.Error):
        pass
    return rows


def _parse_ts(ts_str: str) -> datetime:
    return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
