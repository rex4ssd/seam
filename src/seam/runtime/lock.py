"""
runtime/lock.py — single-instance PID lock.

Usage:
    with single_instance():          # default lock at ~/.seam/harvest.lock
        ...

    with single_instance(path):      # custom path
        ...

Behaviour:
  - Stale lock (PID no longer alive) → silently removed, proceed.
  - Live lock (PID alive)            → raises LockError immediately.
  - Lock file written with our PID, removed on exit (even on exception).
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path


_DEFAULT_LOCK = Path.home() / ".seam" / "harvest.lock"


class LockError(Exception):
    """Raised when a live process already holds the lock."""


@contextlib.contextmanager
def single_instance(lock_path: Path = _DEFAULT_LOCK):
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # ── check existing lock ───────────────────────────────────────────────
    if lock_path.exists():
        raw = ""
        try:
            raw = lock_path.read_text(encoding="utf-8").strip()
            existing_pid = int(raw)
        except (ValueError, OSError):
            # corrupt / unreadable → treat as stale
            existing_pid = 0

        if existing_pid > 0 and _pid_alive(existing_pid):
            raise LockError(
                f"another harvest is running (pid {existing_pid}), "
                f"lock: {lock_path}"
            )
        # stale lock — remove it
        try:
            lock_path.unlink()
        except OSError:
            pass

    # ── write our PID ─────────────────────────────────────────────────────
    lock_path.write_text(str(os.getpid()), encoding="utf-8")
    try:
        yield
    finally:
        try:
            lock_path.unlink()
        except OSError:
            pass


def _pid_alive(pid: int) -> bool:
    """Return True if *pid* is a live process on this machine."""
    try:
        os.kill(pid, 0)   # signal 0 = existence check only
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True       # process exists but we can't signal it
