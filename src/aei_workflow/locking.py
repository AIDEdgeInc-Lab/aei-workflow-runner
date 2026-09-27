"""One run of a workflow at a time, per store. A lock file created exclusively (O_EXCL), holding who took it.

Stale locks (the process died) are recovered automatically, but only when it is certain: same host, and the recorded process no
longer exists. Anything uncertain -- another host sharing the folder, an unreadable lock file, a platform where liveness cannot
be tested -- is treated as HELD and reported, never stolen. `velorona-run unlock --force` is the customer's explicit override.
"""

from __future__ import annotations

import json
import os
import socket
import uuid

from .schema import valid_id
from .workflow import utc_now


class LockHeld(Exception):
    def __init__(self, info: dict, reason: str):
        super().__init__(reason)
        self.info, self.reason = info, reason


def _pid_alive(pid: int):
    """True/False when known; None when this platform cannot say (treated as alive by the caller)."""
    if os.name != "posix":
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def lock_path(store_root: str, workflow_id: str) -> str:
    if not valid_id(workflow_id):
        raise ValueError(f"invalid workflow id: {workflow_id!r}")
    return os.path.join(store_root, "locks", f"{workflow_id}.lock")


def read_lock(store_root: str, workflow_id: str):
    try:
        with open(lock_path(store_root, workflow_id), "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"unreadable": True}


class WorkflowLock:
    def __init__(self, store_root: str, workflow_id: str):
        self.path, self.workflow_id = lock_path(store_root, workflow_id), workflow_id
        self.token = uuid.uuid4().hex
        self.recovered_from = None   # info of a stale lock that was replaced, if any
        self.held = False

    def acquire(self) -> "WorkflowLock":
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        info = {"pid": os.getpid(), "host": socket.gethostname(), "started_at": utc_now(), "token": self.token}
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                existing = read_lock(os.path.dirname(os.path.dirname(self.path)), self.workflow_id)
                if existing is None:
                    continue  # released between our attempt and our read: try again
                if existing.get("unreadable"):
                    raise LockHeld(existing, "the lock file cannot be read; if no run is active, remove it with `velorona-run unlock --force`")
                same_host = existing.get("host") == info["host"]
                alive = _pid_alive(existing["pid"]) if same_host and isinstance(existing.get("pid"), int) else None
                if same_host and alive is False:
                    self.recovered_from = existing
                    os.remove(self.path)  # certain: that process is gone
                    continue
                why = ("a run is in progress (pid %s)" % existing.get("pid")) if alive else \
                      "the lock belongs to another host or its process cannot be checked on this platform"
                raise LockHeld(existing, f"{why}; if it is stale, remove it with `velorona-run unlock --force`")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(info, f)
                f.flush()
                os.fsync(f.fileno())
            self.held = True
            return self
        raise LockHeld({}, "could not acquire the lock")

    def release(self) -> None:
        if not self.held:
            return
        cur = read_lock(os.path.dirname(os.path.dirname(self.path)), self.workflow_id)
        if cur and cur.get("token") == self.token:  # never remove a lock somebody else took after us
            try:
                os.remove(self.path)
            except FileNotFoundError:
                pass
        self.held = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()


def force_unlock(store_root: str, workflow_id: str):
    """Remove the lock whatever it says. Returns the info it held (or None). Only ever called on the customer's explicit request."""
    info = read_lock(store_root, workflow_id)
    try:
        os.remove(lock_path(store_root, workflow_id))
    except FileNotFoundError:
        pass
    return info
