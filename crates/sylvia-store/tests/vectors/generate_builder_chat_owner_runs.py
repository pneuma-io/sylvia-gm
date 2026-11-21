"""Owner-level Builder chat rename and delete: Python's starting database and its outcomes.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-store/tests/vectors/generate_builder_chat_owner_runs.py

It drives `AuthorityOwner.rename_builder_chat` and `delete_builder_chat` (the identity and
confirmation checks, the non-blocking `builder-chat:<id>` lane, `SessionApplication`'s session
lane, then the store). A `busy` operation holds the owner lane `builder-chat:<id>` from another
thread, as a running Builder turn does, while the operation runs. It writes, under
`builder_chat_owner/`, `seed.sqlite3.gz` (three standalone Builder chats), `owner_runs.sqlite3.gz`
(the database afterwards) and `builder_chat_owner_runs.json` (the operations and Python's
outcome of each). `tests/builder_chat_owner.rs` replays them through the Rust store handle.
Identities are explicit and the owner's UUIDs are seeded, so a re-run writes byte-identical
files. Reference tooling only.
"""

from __future__ import annotations

import gzip
import json
import random
import shutil
import sqlite3
import sys
import tempfile
import threading
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

_IDENTITIES = random.Random(20261124)
uuid.uuid4 = lambda: uuid.UUID(int=_IDENTITIES.getrandbits(128), version=4)  # type: ignore[assignment]

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.domain.session import BuilderChatRecord, SessionRecord  # noqa: E402

OUT = Path(__file__).resolve().parent / "builder_chat_owner"
CHATS = ("chat-a", "chat-b", "chat-c")


def delete(session_id: str, confirmation: str | None = None, busy: bool = False) -> dict[str, object]:
    return {"op": "delete_builder_chat", "session_id": session_id, "busy": busy,
            "confirmation": f"permanently delete {session_id}" if confirmation is None else confirmation}


def rename(session_id: str, display_name: str, automatic: bool = False, busy: bool = False) -> dict[str, object]:
    return {"op": "rename_builder_chat", "session_id": session_id, "display_name": display_name,
            "automatic": automatic, "busy": busy}


OPERATIONS: list[dict[str, object]] = [
    # Identity refusals come before the confirmation check.
    delete(""), delete("   "), delete(" chat-a"), delete("chat-a "), delete("."), delete(".."),
    delete("a/b"), delete("a\\b"), delete("a:b"), delete("../chat-a"), delete("C:chat"),
    delete(".", confirmation="wrong"), delete("a/b", confirmation=""),
    # The confirmation must be exact.
    delete("chat-a", confirmation=""), delete("chat-a", confirmation="permanently delete chat-a "),
    delete("chat-a", confirmation="Permanently delete chat-a"),
    delete("chat-a", confirmation="permanently delete  chat-a"),
    delete("chat-a", confirmation="permanently delete chat-b"),
    # Builder work holds the chat's owner lane: deletion is refused, not queued; rename does not
    # take that lane.
    delete("chat-a", busy=True), rename("chat-a", "Renamed while busy", busy=True),
    delete("chat-a", confirmation="wrong", busy=True),
    # Through to the store.
    delete("chat-missing"), delete("..chat"), delete("a\u0000b"), delete("CON"),
    rename("chat-a", "Map notes"), rename("chat-a", "Automatic title", automatic=True),
    rename("chat-c", "Automatic title", automatic=True), rename("chat-missing", "Name"),
    rename(" chat-a", "Name"), rename("chat-a", "   "),
    {"op": "make_workspace", "session_id": "chat-b"},
    delete("chat-b"), {"op": "workspace_exists", "session_id": "chat-b"},
    delete("chat-b"), rename("chat-b", "Gone"),
    {"op": "builder_chat_index"},
]


def session_json(record: SessionRecord) -> dict[str, object]:
    return {
        "session_id": record.session_id, "campaign_id": record.campaign_id, "role": record.role.value,
        "provider_id": record.provider_id, "model_id": record.model_id, "status": record.status.value,
        "parent_session_id": record.parent_session_id, "selected_skill": record.selected_skill,
        "scope": list(record.scope), "reasoning_effort": record.reasoning_effort,
        "reasoning_effort_once": record.reasoning_effort_once,
    }


def chat_json(chat: BuilderChatRecord) -> dict[str, object]:
    return {"session": session_json(chat.session), "display_name": chat.display_name,
            "created_ordinal": chat.created_ordinal}


class _Holder:
    """Holds one owner lane on another thread, as a running Builder turn holds it."""

    def __init__(self, owner: AuthorityOwner, key: str) -> None:
        self._lane = owner._lane(key)
        self._held, self._release = threading.Event(), threading.Event()
        self._thread = threading.Thread(target=self._hold)

    def _hold(self) -> None:
        with self._lane:
            self._held.set()
            self._release.wait()

    def __enter__(self) -> None:
        self._thread.start()
        self._held.wait()

    def __exit__(self, *_: object) -> None:
        self._release.set()
        self._thread.join()


def run(owner: AuthorityOwner, root: Path, op: dict[str, object]) -> dict[str, object]:
    kind, session_id = str(op["op"]), str(op["session_id"]) if "session_id" in op else ""
    try:
        if kind == "make_workspace":
            folder = root / "builder-chats" / session_id / "notes"
            folder.mkdir(parents=True)
            (folder / "draft.txt").write_text("draft", encoding="utf-8")
            return {"ok": None}
        if kind == "workspace_exists":
            return {"ok": (root / "builder-chats" / session_id).exists()}
        if kind == "builder_chat_index":
            return {"ok": [chat_json(chat) for chat in owner.builder_chat_index()]}
        holder = _Holder(owner, f"builder-chat:{session_id}") if op["busy"] else None
        if holder is not None:
            holder.__enter__()
        try:
            if kind == "delete_builder_chat":
                value = chat_json(owner.delete_builder_chat(session_id, confirmation=str(op["confirmation"])))
            else:
                value = chat_json(owner.rename_builder_chat(
                    session_id, str(op["display_name"]), automatic=bool(op["automatic"])))
        finally:
            if holder is not None:
                holder.__exit__()
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    return {"ok": value}


def checkpoint(database: Path) -> bytes:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    return database.read_bytes()


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    with tempfile.TemporaryDirectory(dir=OUT.parent) as scratch:
        root = Path(scratch)
        database = root / "authority.sqlite3"
        with AuthorityOwner.open(database, "builder-chat-owner-runs") as owner:
            owner.prepare_new_campaign_storage()
            for session_id in CHATS:
                owner.create_builder_chat(provider_id="synthetic.provider",
                                          model_id="synthetic.provider/model", session_id=session_id)
        (OUT / "seed.sqlite3.gz").write_bytes(gzip.compress(checkpoint(database), mtime=0))
        with AuthorityOwner.open(database, "builder-chat-owner-runs") as owner:
            outcomes = [run(owner, root, op) for op in OPERATIONS]
        (OUT / "owner_runs.sqlite3.gz").write_bytes(gzip.compress(checkpoint(database), mtime=0))
    record = {"python": sys.version.split()[0], "operations": OPERATIONS, "outcomes": outcomes}
    text = json.dumps(record, indent=1, ensure_ascii=True) + "\n"
    (OUT / "builder_chat_owner_runs.json").write_bytes(text.encode("utf-8"))


if __name__ == "__main__":
    main()
