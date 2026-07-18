"""
runtime/lock.py — single-instance lock (atomic, owner-verified).

Usage:
    with single_instance():          # default lock at ~/.seam/harvest.lock
        ...

    with single_instance(path):      # custom path
        ...

Behaviour (TOCTOU-safe):
  - Acquisition uses os.open(O_CREAT | O_EXCL) — the OS guarantees only one
    process can create the lock file. No exists()/read()/unlink()/write() race.
  - Lock file content is JSON: {run_id, pid, created_at, ttl_sec}.
  - Stale lock (owner PID dead, or older than its TTL) → taken over; a live,
    unexpired owner → LockError.
  - Release verifies ownership (run_id + pid) before unlinking, so a process
    that lost its lock can never delete another run's lock.
  - Legacy plain-PID lock files are still understood (treated as no-TTL:
    alive PID → live lock, dead PID → stale).
"""
from __future__ import annotations

import contextlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


_DEFAULT_LOCK = Path.home() / ".seam" / "harvest.lock"

#: default lock time-to-live — a harvest run should never take this long.
DEFAULT_TTL_SEC = 6 * 3600

#: bound takeover retries so two racing processes can't loop forever.
_MAX_ATTEMPTS = 5


class LockError(Exception):
    """Raised when a live process already holds the lock."""


@dataclass
class LockInfo:
    run_id: str
    pid: int
    created_at: float
    ttl_sec: float

    def is_stale(self, now: Optional[float] = None) -> bool:
        """Stale = owner PID no longer alive, or lock older than its TTL."""
        now = time.time() if now is None else now
        if self.pid > 0 and not _pid_alive(self.pid):
            return True
        if self.ttl_sec > 0 and (now - self.created_at) > self.ttl_sec:
            return True
        return False


def read_lock_info(lock_path: Path) -> Optional[LockInfo]:
    """
    Parse the lock file. Returns None if unreadable/corrupt (→ treat as stale).
    Understands both the JSON format and the legacy plain-PID format.
    """
    try:
        raw = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict):   # a bare int is legacy-PID, not JSON lock
            return LockInfo(
                run_id=str(data.get("run_id", "")),
                pid=int(data.get("pid", 0)),
                created_at=float(data.get("created_at", 0.0)),
                ttl_sec=float(data.get("ttl_sec", DEFAULT_TTL_SEC)),
            )
    except (ValueError, TypeError, json.JSONDecodeError):
        pass
    # legacy format: plain PID, no timestamp → no TTL check possible
    try:
        pid = int(raw)
        return LockInfo(run_id="", pid=pid, created_at=time.time(), ttl_sec=0)
    except ValueError:
        return None


@contextlib.contextmanager
def single_instance(lock_path: Path = _DEFAULT_LOCK,
                    ttl_sec: float = DEFAULT_TTL_SEC):
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    my_info = LockInfo(
        run_id=uuid.uuid4().hex,
        pid=os.getpid(),
        created_at=time.time(),
        ttl_sec=ttl_sec,
    )
    payload = json.dumps({
        "run_id": my_info.run_id,
        "pid": my_info.pid,
        "created_at": my_info.created_at,
        "ttl_sec": my_info.ttl_sec,
    })

    acquired = False
    for _attempt in range(_MAX_ATTEMPTS):
        # ── atomic create: only ONE process can win O_CREAT|O_EXCL ────────
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                         0o644)
        except FileExistsError:
            existing = read_lock_info(lock_path)
            if existing is not None and not existing.is_stale():
                raise LockError(
                    f"another harvest is running (pid {existing.pid}), "
                    f"lock: {lock_path}"
                )
            # stale/corrupt → remove and retry the atomic create.
            # (If another process removes/creates it first, the next
            #  O_CREAT|O_EXCL attempt settles the race atomically.)
            try:
                lock_path.unlink()
            except OSError:
                pass
            continue

        try:
            os.write(fd, payload.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        acquired = True
        break

    if not acquired:
        raise LockError(f"could not acquire lock after {_MAX_ATTEMPTS} "
                        f"attempts: {lock_path}")

    try:
        yield my_info
    finally:
        _release(lock_path, my_info)


def _release(lock_path: Path, my_info: LockInfo) -> None:
    """Unlink the lock only if we still own it (run_id + pid match)."""
    current = read_lock_info(lock_path)
    if current is None:
        return
    if current.run_id != my_info.run_id or current.pid != my_info.pid:
        return  # someone took over (e.g. after our TTL expired) — not ours
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
