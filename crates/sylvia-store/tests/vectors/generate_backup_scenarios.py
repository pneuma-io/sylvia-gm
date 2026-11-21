"""Record what the Python store's `backup_database` does for a fixed set of targets.

Run with the snapshot's own interpreter, from the repository root, once on each platform:

    .venv/Scripts/python.exe -B crates/sylvia-store/tests/vectors/generate_backup_scenarios.py
    .venv/bin/python -B crates/sylvia-store/tests/vectors/generate_backup_scenarios.py

It writes `backup_scenarios.<platform>.json`. Every scenario starts from a fresh data folder
whose authority the Python store created and prepared, builds the same files in it, calls
`backup_database(target)` and records the outcome: success, with the copy's schema state, or
the failure's code and message. `tests/backup.rs` replays the same scenarios against the Rust
store and must get the same outcomes. Reference tooling only.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from sylvia_gm.infrastructure import sqlite_authority as a  # noqa: E402

OUT = Path(__file__).resolve().parent
PLATFORM = "windows" if sys.platform == "win32" else "linux"


def unused_drive() -> str | None:
    for letter in "ZYXWVUTSRQPONMLKJIHGFE":
        if not os.path.exists(f"{letter}:\\"):
            return f"{letter}:\\"
    return None


def scenarios(data: Path) -> dict[str, Path]:
    """Name -> target. Setup of each target's surroundings is in `prepare`."""
    cases = {
        "new_target": data / "backups" / "copy.sqlite3",
        "existing_target_replaced": data / "backups" / "existing.sqlite3",
        "parent_missing": data / "missing" / "copy.sqlite3",
        "target_is_directory": data / "backups" / "directory.sqlite3",
        "target_is_source": data / "authority.sqlite3",
        "target_is_source_wal": data / "authority.sqlite3-wal",
        "target_is_source_lock": data / "authority.sqlite3.authority-owner.owner.lock",
        "destination_owner_lock": data / "backups" / "locked.sqlite3",
        "parent_is_file": data / "backups" / "plain.txt" / "copy.sqlite3",
        # ctypes reads a path only up to U+0000; os.open refuses it.
        "nul_in_name": data / "backups" / "co\x00py.sqlite3",
    }
    if PLATFORM == "windows":
        cases["name_with_colon"] = data / "backups" / "copy:stream.sqlite3"
        cases["unc_admin_share"] = Path("\\\\localhost\\c$\\sylvia-backup-scenario.sqlite3")
        drive = unused_drive()
        if drive is not None:
            cases["unmapped_drive"] = Path(drive) / "copy.sqlite3"
    else:
        cases["target_is_symlink"] = data / "backups" / "link.sqlite3"
        cases["parent_is_symlink"] = data / "linked" / "copy.sqlite3"
        cases["tmpfs_volume"] = Path("/dev/shm/sylvia-backup-scenario.sqlite3")
    return cases


def prepare(data: Path, name: str, target: Path) -> None:
    backups = data / "backups"
    backups.mkdir(exist_ok=True)
    if name == "existing_target_replaced":
        target.write_bytes(b"old bytes")
    elif name == "target_is_directory":
        target.mkdir()
    elif name == "destination_owner_lock":
        target.with_name(f"{target.name}.owner.lock").write_bytes(b"0")
    elif name == "parent_is_file":
        target.parent.write_bytes(b"plain")
    elif name == "target_is_symlink":
        (backups / "real.sqlite3").write_bytes(b"real")
        target.symlink_to(backups / "real.sqlite3")
    elif name == "parent_is_symlink":
        (data / "linked").symlink_to(backups, target_is_directory=True)


def copy_state(path: Path) -> dict[str, object]:
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        return {
            "user_version": connection.execute("PRAGMA user_version").fetchone()[0],
            "tables": connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE type = 'table'"
            ).fetchone()[0],
            "migrations": connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0],
        }
    finally:
        connection.close()


def main() -> None:
    results: dict[str, object] = {}
    with tempfile.TemporaryDirectory(dir=OUT) as scratch:
        for name in scenarios(Path(scratch) / "probe"):
            data = Path(scratch) / name
            data.mkdir()
            store = a._open_for_application_owner(data / "authority.sqlite3")
            store.prepare_new_campaign_storage()
            target = scenarios(data)[name]
            prepare(data, name, target)
            try:
                store.backup_database(target)
            except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
                results[name] = {
                    "ok": False,
                    "type": type(error).__name__,
                    "code": getattr(error, "code", None),
                    "message": str(error),
                }
            else:
                leftovers = sorted(
                    entry.name for entry in target.parent.iterdir() if entry.name != target.name
                )
                results[name] = {"ok": True, "copy": copy_state(target), "siblings": leftovers}
            finally:
                store.close()
    record = {"python": sys.version.split()[0], "platform": PLATFORM, "scenarios": results}
    text = json.dumps(record, indent=1, ensure_ascii=True) + "\n"
    (OUT / f"backup_scenarios.{PLATFORM}.json").write_bytes(text.encode("utf-8"))


if __name__ == "__main__":
    main()
