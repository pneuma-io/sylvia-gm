"""Generate paired runs for the legacy Builder chat migration's store half.

Run from the repository root after `uv sync --extra dev`:

    .venv/Scripts/python.exe -I crates/sylvia-store/tests/vectors/legacy_builder/generate_legacy_builder_store.py

Two bases are built with `tests/test_builder_session_migration.py`'s own set-up (read, never
changed): a player-character content chat still open in its interview (`open`) and the same chat
after it returned (`returned`), and the open chat explicitly stopped (`stopped`, whose status the
detachment keeps). Each is saved gzipped with its storage root's files.

`detach` cases run on a copy of a base and record the outcome (or Python's exception class, code
and message), the four session tables and the storage tree:
- the real migration (`migrate_legacy_builder_chats` with the owner's catalog), with every
  `retain_legacy_workspace` and `detach_legacy_builder_chat` call it makes recorded, so the Rust
  side replays exactly those calls; then the same detach again, which finds nothing to move;
- the refusals: a changed message count, a purpose that is not content, a purpose that does not
  match the saved link, an unhashable purpose, an unknown session.

`workspace` cases run `retain_legacy_workspace` on small storage roots: a copy, an absent source,
a retry, existing targets with no, another or an oversized receipt, a receipt in the source, and
identities that are not directory names.
Reference tooling for the port, retired with the Python source.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
HERE = Path(__file__).parent
TABLES = ["agent_sessions", "agent_session_messages", "standalone_builder_chats", "standalone_builder_chat_messages"]


def skipped(relative: str) -> bool:
    """The database, its lock and the owner's pre-migration copies are not the workspace, and
    the bundled starter characters are what the migration's catalog writes (ported with it)."""
    return relative.startswith(("authority.sqlite3", "migration-sources", "bundled-srd-starters"))


def tree(root: Path) -> list:
    entries = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if skipped(relative):
            continue
        if path.is_dir():
            entries.append([relative, "dir", None])
        else:
            entries.append([relative, "file", hashlib.sha256(path.read_bytes()).hexdigest()])
    return entries


def files(root: Path) -> dict:
    return {path.relative_to(root).as_posix(): path.read_bytes().decode("utf-8")
            for path in sorted(root.rglob("*"))
            if path.is_file() and not skipped(path.relative_to(root).as_posix())}


def tables(database: Path) -> dict:
    with sqlite3.connect(database) as connection:
        return {table: [list(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                for table in TABLES}


def outcome(call) -> dict:
    try:
        value = call()
    except Exception as error:  # noqa: BLE001  (recorded as Python raised it)
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    return {"ok": value}


def main() -> None:
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    from sylvia_gm.application import builder_session_migration
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import owned_payload
    from sylvia_gm.infrastructure import builder_workspace_migration
    from sylvia_gm.interfaces.owner_process import _campaign_creation_catalog
    from sylvia_gm.domain.package_library import canonical_json_bytes
    from tests.test_builder_session_migration import seed_legacy_character

    def thaw(value):
        return json.loads(canonical_json_bytes(value))

    work = Path(tempfile.mkdtemp(prefix="legacy-builder-store-"))
    bases = {}
    for name, returned, stopped in (("open", False, False), ("returned", True, False), ("stopped", False, True)):
        root = work / f"base-{name}"
        root.mkdir()
        with AuthorityOwner.open(root / "authority.sqlite3", f"seed-{name}") as owner:
            seed_legacy_character(owner, root, returned=returned)
            if stopped:
                owner.stop_agent_session("old-character")
            context = owner.session_context("old-character")
            bases[name] = {"files": files(root), "message_count": len(context.messages),
                           "campaign_id": context.session.campaign_id}
        data = (root / "authority.sqlite3").read_bytes()
        assert str(work).encode() not in data and str(ROOT).encode() not in data
        (HERE / f"base-{name}.sqlite3.gz").write_bytes(gzip.compress(data, mtime=0))

    def copy(base: str, case: str) -> Path:
        root = work / case
        shutil.copytree(work / f"base-{base}", root)
        return root

    detach = []
    for base in bases:
        # The real migration, recording the store calls it makes.
        root = copy(base, f"migrate-{base}")
        calls = []
        retain, original_retain = builder_session_migration.retain_legacy_workspace, None
        original_retain = retain

        def recording_retain(storage_root, *, campaign_id, session_id):
            calls.append({"call": "retain", "campaign_id": campaign_id, "session_id": session_id})
            return original_retain(storage_root, campaign_id=campaign_id, session_id=session_id)

        builder_session_migration.retain_legacy_workspace = recording_retain
        try:
            with AuthorityOwner.open(root / "authority.sqlite3", "migrate") as owner:
                original_detach = owner.detach_legacy_builder_chat

                def recording_detach(**kwargs):
                    calls.append({"call": "detach", **{key: value for key, value in kwargs.items()}})
                    return original_detach(**kwargs)

                owner.detach_legacy_builder_chat = recording_detach
                result = outcome(lambda: list(builder_session_migration.migrate_legacy_builder_chats(
                    owner, root, _campaign_creation_catalog(root, owner))))
                after_first = {"tables": tables(root / "authority.sqlite3"), "tree": tree(root)}
                last = next(call for call in reversed(calls) if call["call"] == "detach")
                again = outcome(lambda: original_detach(**{key: value for key, value in last.items() if key != "call"}))
        finally:
            builder_session_migration.retain_legacy_workspace = original_retain
        detach.append({"name": f"migrate-{base}", "base": base, "migration": result, "calls": [
            {**call, "launch_context": thaw(call["launch_context"])} if "launch_context" in call else call
            for call in calls], **after_first})
        detach.append({"name": f"again-{base}", "base": base, "after": f"migrate-{base}",
                       "calls": [{**{key: (thaw(value) if key == "launch_context" else value)
                                     for key, value in last.items()}}],
                       "outcome": again, "tables": tables(root / "authority.sqlite3"), "tree": tree(root)})

        count = bases[base]["message_count"]
        for case, purpose, message_count in (
            ("stale-count", "player_character", count + 1),
            ("purpose-not-content", "campaign_customization", count),
            ("purpose-mismatch", "setting", count),
            ("purpose-list", ["player_character"], count),
        ):
            root = copy(base, f"{case}-{base}")
            launch = {"kind": "builder_launch_context", "purpose": purpose}
            with AuthorityOwner.open(root / "authority.sqlite3", "refuse") as owner:
                result = outcome(lambda: owner.detach_legacy_builder_chat(
                    session_id="old-character", expected_message_count=message_count,
                    launch_context=owned_payload(launch), display_name="Character · Test"))
            detach.append({"name": f"{case}-{base}", "base": base, "calls": [
                {"call": "detach", "session_id": "old-character", "expected_message_count": message_count,
                 "launch_context": launch, "display_name": "Character · Test"}],
                "outcome": result, "tables": tables(root / "authority.sqlite3"), "tree": tree(root)})
    root = copy("open", "unknown-session")
    launch = {"kind": "builder_launch_context", "purpose": "player_character"}
    with AuthorityOwner.open(root / "authority.sqlite3", "unknown") as owner:
        result = outcome(lambda: owner.detach_legacy_builder_chat(
            session_id="no-such-chat", expected_message_count=1,
            launch_context=owned_payload(launch), display_name="Character · Test"))
    detach.append({"name": "unknown-session", "base": "open", "calls": [
        {"call": "detach", "session_id": "no-such-chat", "expected_message_count": 1,
         "launch_context": launch, "display_name": "Character · Test"}],
        "outcome": result, "tables": tables(root / "authority.sqlite3"), "tree": tree(root)})

    workspace = []
    retain = builder_workspace_migration.retain_legacy_workspace
    receipt = ".sylvia-legacy-workspace.json"
    seeds = {
        "copy": {"campaigns/c-1/drafts/character.md": "# Vela\r\n\r\nA relay courier.",
                 "campaigns/c-1/notes/deep/a.txt": "a", "campaigns/c-1/top.json": "{}"},
        "empty-source": {},
        "existing-no-receipt": {"builder-chats/s-1/kept.txt": "kept", "campaigns/c-1/a.txt": "a"},
        "existing-other-receipt": {
            f"builder-chats/s-1/{receipt}": '{"campaign_id":"c-2","format":1,"session_id":"s-1"}'},
        "existing-same-receipt-spaced": {
            f"builder-chats/s-1/{receipt}": '{ "session_id": "s-1", "format": 1.0, "campaign_id": "c-1" }'},
        "oversized-receipt": {f"builder-chats/s-1/{receipt}": "x" * 5000},
        "bad-json-receipt": {f"builder-chats/s-1/{receipt}": "not json"},
        "source-has-receipt": {f"campaigns/c-1/{receipt}": "{}"},
    }
    for case, seeded in seeds.items():
        root = work / f"workspace-{case}"
        root.mkdir()
        for relative, text in seeded.items():
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text(text, encoding="utf-8", newline="")
        result = outcome(lambda: retain(root, campaign_id="c-1", session_id="s-1").relative_to(root).as_posix())
        record = {"name": case, "files": seeded, "campaign_id": "c-1", "session_id": "s-1",
                  "outcomes": [result]}
        if case == "copy":
            record["outcomes"].append(outcome(
                lambda: retain(root, campaign_id="c-1", session_id="s-1").relative_to(root).as_posix()))
        record["tree"] = tree(root)
        workspace.append(record)
    for campaign_id, session_id in ((".", "s-1"), ("..", "s-1"), ("c-1", "a/b"), ("c-1", "a\\b"),
                                    ("c:1", "s-1"), ("c-1", "..")):
        root = work / "workspace-names"
        root.mkdir(exist_ok=True)
        workspace.append({"name": f"identity {campaign_id!r} {session_id!r}", "files": {},
                          "campaign_id": campaign_id, "session_id": session_id,
                          "outcomes": [outcome(lambda: retain(root, campaign_id=campaign_id,
                                                              session_id=session_id).as_posix())],
                          "tree": []})

    record = {"python": sys.version.split()[0], "bases": bases, "detach": detach, "workspace": workspace}
    (HERE / "store_runs.json").write_text(json.dumps(record, indent=1, ensure_ascii=True) + "\n",
                                          encoding="utf-8", newline="\n")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
