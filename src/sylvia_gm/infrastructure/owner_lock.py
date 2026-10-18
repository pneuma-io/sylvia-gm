"""Cross-process ownership lock for the one live SQLite authority."""

from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO

from sylvia_gm.domain.errors import OwnerConflict


class DatabaseOwnerLock:
    """Hold a non-blocking operating-system lock for an owner's lifetime."""

    def __init__(self, database_path: Path) -> None:
        self._path = database_path.with_name(f"{database_path.name}.owner.lock")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file: BinaryIO = self._path.open("a+b")
        self._file.seek(0, os.SEEK_END)
        if self._file.tell() == 0:
            self._file.write(b"0")
            self._file.flush()
        self._file.seek(0)
        try:
            self._lock()
        except OSError as error:
            self._file.close()
            raise OwnerConflict("The Sylvia GM SQLite authority already has a live owner") from error
        self._closed = False

    def _lock(self) -> None:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        fcntl.flock(  # type: ignore[attr-defined]
            self._file.fileno(),
            fcntl.LOCK_EX | fcntl.LOCK_NB,  # type: ignore[attr-defined]
        )

    def close(self) -> None:
        if self._closed:
            return
        self._file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(  # type: ignore[attr-defined]
                self._file.fileno(),
                fcntl.LOCK_UN,  # type: ignore[attr-defined]
            )
        self._file.close()
        self._closed = True
