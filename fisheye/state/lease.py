"""Process-held local analyzer ownership; the OS releases it after a crash."""

from __future__ import annotations

import os
from pathlib import Path


class AnalysisLease:
    def __init__(self, database):
        self.path = Path(str(Path(database).resolve()) + ".analysis.lock")
        self._handle = None

    def acquire(self):
        if self._handle is not None:
            return
        handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                if self.path.stat().st_size == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError("Another analysis runtime owns this database; use one server worker") from exc
        self._handle = handle

    def release(self):
        if self._handle is not None:
            # Keep the lock file: unlinking it permits two owners on different inodes.
            self._handle.close()
            self._handle = None
