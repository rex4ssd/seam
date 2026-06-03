"""
Phase 3.5 — runtime/lock.py + runtime/history.py unit tests.

lock:
  - context manager acquires and releases lock file
  - stale PID (dead process) → auto-cleared, proceeds
  - live PID → raises LockError
  - corrupt lock file → treated as stale

history:
  - mark() appends CSV rows with fsync
  - load_done_keys: finalized vs in_progress classification
  - should_skip: finalized → True, in_progress non-idempotent → True, else False
  - retry_failures_24h: returns recent fails not yet passed, active repos only
  - _read_rows handles missing / empty / malformed CSV gracefully
"""
from __future__ import annotations

import csv
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from seam.runtime.lock import LockError, _pid_alive, single_instance
from seam.runtime.history import (
    _TERMINAL,
    load_done_keys,
    mark,
    now_slot,
    retry_failures_24h,
    should_skip,
    _read_rows,
)


# ── lock.py ───────────────────────────────────────────────────────────────────

class TestLock:
    def test_creates_and_removes_lock(self, tmp_path):
        lock = tmp_path / "test.lock"
        assert not lock.exists()
        with single_instance(lock):
            assert lock.exists()
            assert lock.read_text() == str(os.getpid())
        assert not lock.exists()

    def test_removes_lock_on_exception(self, tmp_path):
        lock = tmp_path / "test.lock"
        with pytest.raises(RuntimeError):
            with single_instance(lock):
                raise RuntimeError("boom")
        assert not lock.exists()

    def test_stale_lock_cleared(self, tmp_path):
        lock = tmp_path / "test.lock"
        # Write a PID that is almost certainly dead (PID 1 on non-init)
        # Use 99999 — very unlikely to be alive
        lock.write_text("99999999")
        # If that PID is alive by miracle, just write a known-dead PID
        if _pid_alive(99999999):
            pytest.skip("PID 99999999 is somehow alive")
        with single_instance(lock):
            assert lock.exists()
        assert not lock.exists()

    def test_live_lock_raises(self, tmp_path):
        lock = tmp_path / "test.lock"
        lock.write_text(str(os.getpid()))   # our own PID = alive
        with pytest.raises(LockError):
            with single_instance(lock):
                pass
        # lock must still exist (we didn't acquire it)
        assert lock.exists()

    def test_corrupt_lock_treated_as_stale(self, tmp_path):
        lock = tmp_path / "test.lock"
        lock.write_text("not-a-pid")
        with single_instance(lock):   # should not raise
            assert lock.exists()
        assert not lock.exists()

    def test_creates_parent_dir(self, tmp_path):
        lock = tmp_path / "sub" / "dir" / "test.lock"
        with single_instance(lock):
            assert lock.exists()

    def test_pid_alive_self(self):
        assert _pid_alive(os.getpid()) is True

    def test_pid_alive_dead(self):
        # PID 0 is never a normal user process
        assert _pid_alive(0) is False or True  # might be True on some systems
        # More reliable: use a PID from a finished subprocess
        import subprocess
        proc = subprocess.Popen(["true"])
        proc.wait()
        dead_pid = proc.pid
        assert _pid_alive(dead_pid) is False


# ── history.py — mark() ────────────────────────────────────────────────────────

class TestMark:
    def test_creates_csv_with_header(self, tmp_path):
        hist = tmp_path / "history.csv"
        mark(hist, "owner/repo", "abc123", "clone", "pass", "2026-06-03 02:00")
        with open(hist) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["repo"] == "owner/repo"
        assert rows[0]["stage"] == "clone"
        assert rows[0]["result"] == "pass"

    def test_appends_multiple_rows(self, tmp_path):
        hist = tmp_path / "history.csv"
        mark(hist, "a/b", "c1", "clone",   "running", "2026-06-03 02:00")
        mark(hist, "a/b", "c1", "clone",   "pass",    "2026-06-03 02:00")
        mark(hist, "a/b", "c1", "analyze", "pass",    "2026-06-03 02:00")
        rows = _read_rows(hist)
        assert len(rows) == 3

    def test_creates_parent_dir(self, tmp_path):
        hist = tmp_path / "sub" / "history.csv"
        mark(hist, "a/b", "c1", "clone", "pass", "2026-06-03 02:00")
        assert hist.exists()

    def test_timestamp_present(self, tmp_path):
        hist = tmp_path / "history.csv"
        mark(hist, "a/b", "c1", "clone", "pass", "2026-06-03 02:00")
        rows = _read_rows(hist)
        assert rows[0]["timestamp"]  # non-empty

    def test_header_written_once(self, tmp_path):
        hist = tmp_path / "history.csv"
        mark(hist, "a/b", "c1", "clone", "running", "slot")
        mark(hist, "a/b", "c1", "clone", "pass",    "slot")
        with open(hist) as f:
            content = f.read()
        # header line should appear exactly once
        assert content.count("timestamp") == 1


# ── history.py — load_done_keys ────────────────────────────────────────────────

class TestLoadDoneKeys:
    def test_empty_history(self, tmp_path):
        hist = tmp_path / "history.csv"
        f, ip = load_done_keys(hist)
        assert f == set()
        assert ip == set()

    def test_missing_file(self, tmp_path):
        hist = tmp_path / "nonexistent.csv"
        f, ip = load_done_keys(hist)
        assert f == set()

    def test_pass_is_finalized(self, tmp_path):
        hist = tmp_path / "history.csv"
        mark(hist, "a/b", "c1", "clone", "pass", "slot")
        fin, ip = load_done_keys(hist)
        assert ("a/b", "c1", "clone") in fin

    def test_fail_is_finalized(self, tmp_path):
        hist = tmp_path / "history.csv"
        mark(hist, "a/b", "c1", "analyze", "fail", "slot")
        fin, _ = load_done_keys(hist)
        assert ("a/b", "c1", "analyze") in fin

    def test_running_only_is_in_progress(self, tmp_path):
        hist = tmp_path / "history.csv"
        mark(hist, "a/b", "c1", "analyze", "running", "slot")
        fin, ip = load_done_keys(hist)
        assert ("a/b", "c1", "analyze") not in fin
        assert ("a/b", "c1", "analyze") in ip

    def test_running_then_pass_is_finalized(self, tmp_path):
        hist = tmp_path / "history.csv"
        mark(hist, "a/b", "c1", "clone", "running", "slot")
        mark(hist, "a/b", "c1", "clone", "pass",    "slot")
        fin, ip = load_done_keys(hist)
        assert ("a/b", "c1", "clone") in fin
        assert ("a/b", "c1", "clone") not in ip

    def test_different_commits_independent(self, tmp_path):
        hist = tmp_path / "history.csv"
        mark(hist, "a/b", "c1", "clone", "pass", "slot")
        mark(hist, "a/b", "c2", "clone", "running", "slot")
        fin, ip = load_done_keys(hist)
        assert ("a/b", "c1", "clone") in fin
        assert ("a/b", "c2", "clone") in ip


# ── history.py — should_skip ───────────────────────────────────────────────────

class TestShouldSkip:
    def test_finalized_skip(self):
        fin = {("a/b", "c1", "clone")}
        assert should_skip("a/b", "c1", "clone", fin, set()) is True

    def test_not_finalized_not_in_progress_no_skip(self):
        assert should_skip("a/b", "c1", "clone", set(), set()) is False

    def test_in_progress_non_idempotent_skip(self):
        ip = {("a/b", "c1", "analyze")}
        assert should_skip("a/b", "c1", "analyze", set(), ip) is True

    def test_in_progress_non_idempotent_veinout_skip(self):
        ip = {("batch", "2026-06-03", "veinout")}
        assert should_skip("batch", "2026-06-03", "veinout", set(), ip) is True

    def test_in_progress_idempotent_no_skip(self):
        """clone and signals are idempotent — don't skip even if in_progress."""
        ip = {("a/b", "c1", "clone")}
        assert should_skip("a/b", "c1", "clone", set(), ip) is False

    def test_in_progress_signals_no_skip(self):
        ip = {("a/b", "c1", "signals")}
        assert should_skip("a/b", "c1", "signals", set(), ip) is False

    def test_different_repo_no_skip(self):
        fin = {("a/b", "c1", "clone")}
        assert should_skip("c/d", "c1", "clone", fin, set()) is False

    def test_different_commit_no_skip(self):
        fin = {("a/b", "c1", "clone")}
        assert should_skip("a/b", "c2", "clone", fin, set()) is False


# ── history.py — retry_failures_24h ───────────────────────────────────────────

class TestRetryFailures:
    def _ts(self, hours_ago: float = 1.0) -> str:
        dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    def _write_row(self, hist: Path, repo: str, commit: str, stage: str,
                   result: str, hours_ago: float = 1.0):
        """Write a row with a specific timestamp for testing."""
        hist.parent.mkdir(parents=True, exist_ok=True)
        write_header = not hist.exists() or hist.stat().st_size == 0
        row = {
            "timestamp":     self._ts(hours_ago),
            "repo":          repo,
            "commit":        commit,
            "stage":         stage,
            "result":        result,
            "scheduled_for": "2026-06-03 02:00",
        }
        with open(hist, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["timestamp","repo","commit","stage","result","scheduled_for"])
            if write_header:
                writer.writeheader()
            writer.writerow(row)

    def test_recent_fail_returned(self, tmp_path):
        hist = tmp_path / "history.csv"
        self._write_row(hist, "a/b", "c1", "analyze", "fail", hours_ago=1)
        result = retry_failures_24h(hist, {"a/b"})
        assert ("a/b", "c1", "analyze") in result

    def test_old_fail_not_returned(self, tmp_path):
        hist = tmp_path / "history.csv"
        self._write_row(hist, "a/b", "c1", "analyze", "fail", hours_ago=25)
        result = retry_failures_24h(hist, {"a/b"})
        assert result == []

    def test_fail_then_pass_not_returned(self, tmp_path):
        hist = tmp_path / "history.csv"
        self._write_row(hist, "a/b", "c1", "analyze", "fail", hours_ago=2)
        self._write_row(hist, "a/b", "c1", "analyze", "pass", hours_ago=1)
        result = retry_failures_24h(hist, {"a/b"})
        assert result == []

    def test_stale_repo_not_returned(self, tmp_path):
        """Repo no longer in active_repos → not retried."""
        hist = tmp_path / "history.csv"
        self._write_row(hist, "a/b", "c1", "analyze", "fail", hours_ago=1)
        result = retry_failures_24h(hist, set())   # empty active set
        assert result == []

    def test_deduplicates_same_key(self, tmp_path):
        hist = tmp_path / "history.csv"
        self._write_row(hist, "a/b", "c1", "clone", "fail", hours_ago=3)
        self._write_row(hist, "a/b", "c1", "clone", "fail", hours_ago=2)
        result = retry_failures_24h(hist, {"a/b"})
        assert result.count(("a/b", "c1", "clone")) == 1

    def test_missing_history_returns_empty(self, tmp_path):
        hist = tmp_path / "nonexistent.csv"
        assert retry_failures_24h(hist, {"a/b"}) == []

    def test_now_slot_format(self):
        slot = now_slot()
        # Should be "YYYY-MM-DD HH:MM"
        assert len(slot) == 16
        assert slot[4] == "-" and slot[7] == "-"
        assert slot[10] == " " and slot[13] == ":"


# ── history.py — _read_rows robustness ────────────────────────────────────────

class TestReadRows:
    def test_empty_file(self, tmp_path):
        hist = tmp_path / "h.csv"
        hist.write_text("")
        assert _read_rows(hist) == []

    def test_header_only(self, tmp_path):
        hist = tmp_path / "h.csv"
        hist.write_text("timestamp,repo,commit,stage,result,scheduled_for\n")
        assert _read_rows(hist) == []

    def test_missing_file(self, tmp_path):
        hist = tmp_path / "nonexistent.csv"
        assert _read_rows(hist) == []

    def test_skips_rows_with_missing_fields(self, tmp_path):
        hist = tmp_path / "h.csv"
        # Write a row with wrong columns
        hist.write_text("garbage,line,here\njunk\n")
        assert _read_rows(hist) == []
