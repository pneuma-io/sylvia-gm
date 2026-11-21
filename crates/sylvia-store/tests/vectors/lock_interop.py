"""Check that a Python owner and a Rust owner exclude each other on one data folder.

Run with the snapshot's interpreter from the repository root, after
`cargo build -p sylvia-store --example owner_lock_probe`:

    .venv/Scripts/python.exe -B crates/sylvia-store/tests/vectors/lock_interop.py <probe> <folder>

<probe> is target/debug/examples/owner_lock_probe(.exe); <folder> is an empty scratch folder.
It checks the owner lock and the second-instance probe (`probe_owner_live`) both ways.
It prints one line per check and exits 1 if any check fails. Reference tooling only.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from sylvia_gm.domain.errors import OwnerConflict  # noqa: E402
from sylvia_gm.infrastructure.owner_lock import (  # noqa: E402
    DatabaseOwnerLock,
    authority_lock_identity,
)
from sylvia_gm.infrastructure.owner_process_probe import probe_owner_live  # noqa: E402


def main() -> int:
    probe, folder = sys.argv[1], Path(sys.argv[2])
    folder.mkdir(parents=True, exist_ok=True)
    database = folder / "authority.sqlite3"
    identity = authority_lock_identity(database)
    failures = 0

    def check(name: str, ok: bool, detail: str) -> None:
        nonlocal failures
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")

    # 1. Python holds the lock; Rust is refused.
    held = DatabaseOwnerLock(identity)
    result = subprocess.run([probe, str(database), "try"], capture_output=True, text=True)
    check("python holds, rust refused", result.stdout.startswith("conflict"), result.stdout.strip())
    result = subprocess.run([probe, str(database), "probe"], capture_output=True, text=True)
    check("python holds, rust probe sees it", result.stdout.strip() == "live", result.stdout.strip())
    held.close()

    # 2. Rust holds the lock; Python is refused.
    holder = subprocess.Popen(
        [probe, str(database), "hold"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    assert holder.stdout is not None and holder.stdin is not None
    line = holder.stdout.readline().strip()
    check("rust acquires", line == "acquired", line)
    try:
        DatabaseOwnerLock(identity).close()
    except OwnerConflict as error:
        check("rust holds, python refused", True, f"OwnerConflict: {error}")
    else:
        check("rust holds, python refused", False, "Python acquired the lock")
    seen = probe_owner_live(database)
    check("rust holds, python probe sees it", seen.owner_live, seen.reason)
    holder.stdin.close()
    holder.wait(timeout=30)

    # 3. Both released: each side can take it again.
    result = subprocess.run([probe, str(database), "try"], capture_output=True, text=True)
    check("released, rust acquires", result.stdout.strip() == "acquired", result.stdout.strip())
    DatabaseOwnerLock(identity).close()
    check("released, python acquires", True, "acquired")
    result = subprocess.run([probe, str(database), "probe"], capture_output=True, text=True)
    check("released, rust probe sees it free", result.stdout.strip() == "free", result.stdout.strip())
    seen = probe_owner_live(database)
    check("released, python probe sees it free", not seen.owner_live, seen.reason)
    content = (folder / "authority.sqlite3.authority-owner.owner.lock").read_bytes()
    check("lock file content", content == b"0", repr(content))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
