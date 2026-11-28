"""Paired runs for sessions and messages, part 1: Python's starting database and its outcomes.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-store/tests/vectors/generate_session_runs.py

It writes, under `sessions/`, `seed.sqlite3.gz` (the Python owner's profile with one small
synthetic package, a DM campaign with its TURN open and a Builder-writer campaign),
`<run>.sqlite3.gz` (the database after each run) and `session_runs.json` (each run's operations
and Python's outcome of each). `tests/sessions.rs` replays them on the Rust store. Identities
are explicit, so a re-run writes byte-identical files. Reference tooling only.
"""

from __future__ import annotations

import gzip
import io
import json
import random
import shutil
import sys
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_IDENTITIES = random.Random(20261122)
uuid.uuid4 = lambda: uuid.UUID(int=_IDENTITIES.getrandbits(128), version=4)  # type: ignore[assignment]

from generate_action_runs import synthetic_archive  # noqa: E402

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate  # noqa: E402
from sylvia_gm.domain.authority import PackageLock, WriterRole  # noqa: E402
from sylvia_gm.domain.session import SessionMessageKind, SessionRecord, SessionStatus  # noqa: E402
from sylvia_gm.infrastructure import sqlite_authority as a  # noqa: E402

OUT = Path(__file__).resolve().parent / "sessions"
DM = "campaign-sessions"
BUILDER = "campaign-builder"


def seed(folder: Path) -> Path:
    database = folder / "authority.sqlite3"
    with AuthorityOwner.open(database, "session-runs") as owner:
        owner.prepare_new_campaign_storage()
        candidate = owner.install_package_archive(
            io.BytesIO(synthetic_archive()), install_id="install-session-runs"
        )
        owner.create_campaign(
            CampaignCreate(
                display_name="Session runs", writer=WriterRole.DM,
                package_lock=candidate.package_lock, turn_id="turn-sessions", campaign_id=DM,
            )
        )
        owner.create_campaign(
            CampaignCreate(
                display_name="Builder runs", writer=WriterRole.BUILDER,
                package_lock=PackageLock(entries=()), turn_id="turn-builder", campaign_id=BUILDER,
            )
        )
    return database


def session(**fields: object) -> dict[str, object]:
    base: dict[str, object] = {
        "session_id": "dm-1", "campaign_id": DM, "role": "dm", "provider_id": "synthetic.provider",
        "model_id": "synthetic.provider/model", "status": "active", "parent_session_id": None,
        "selected_skill": None, "scope": [], "reasoning_effort": None, "reasoning_effort_once": None,
    }
    base.update(fields)
    return base


def create(**fields: object) -> dict[str, object]:
    return {"op": "create_agent_session", "record": session(**fields)}


def append(session_id: str, message_id: str, kind: str, content: object, canonical: int | None = None) -> dict[str, object]:
    return {"op": "append_agent_message", "session_id": session_id, "message_id": message_id,
            "kind": kind, "content": content, "canonical_sequence": canonical}


def chat(session_id: str, name: str = "First chat", provider: str = "synthetic.provider") -> dict[str, object]:
    return {"op": "create_builder_chat", "session_id": session_id, "provider_id": provider,
            "model_id": "synthetic.provider/model", "display_name": name}


RUNS: dict[str, list[dict[str, object]]] = {
    "agent_sessions": [
        create(),
        create(),
        create(provider_id="other.provider"),
        create(session_id="builder-on-dm", role="builder", selected_skill="BUILD CAMPAIGN PLAN"),
        create(session_id="dm-elsewhere", campaign_id="campaign-missing"),
        create(session_id="dm-effort", reasoning_effort="high", reasoning_effort_once="low"),
        append("dm-1", "m1", "talk", {"text": "Hello.", "count": 1}),
        append("dm-1", "m1", "talk", {"text": "Hello.", "count": 1}),
        append("dm-1", "m1", "talk", {"count": 1.0, "text": "Hello."}),
        append("dm-1", "m1", "talk", {"text": "Changed."}),
        append("dm-1", "m2", "canonical_reference", {"event": "x"}, canonical=999),
        append("dm-1", "m3", "talk", {"reasoning_private": "x"}),
        append("missing-session", "m1", "talk", {"text": "x"}),
        {"op": "update_agent_session_status", "session_id": "dm-1", "status": "stopped"},
        append("dm-1", "m4", "talk", {"text": "Not while stopped."}),
        append("dm-1", "m5", "evidence", {"kind": "session_attempt_terminal", "attempt_id": "a1"}),
        {"op": "update_agent_session_status", "session_id": "dm-1", "status": "completed"},
        {"op": "resume_agent_session", "session_id": "dm-1"},
        {"op": "resume_agent_session", "session_id": "dm-1"},
        {"op": "update_agent_session_model", "session_id": "dm-1", "provider_id": "next.provider",
         "model_id": "next.provider/model"},
        {"op": "update_agent_session_reasoning", "session_id": "dm-1", "effort": "medium", "once": None},
        {"op": "update_agent_session_reasoning", "session_id": "dm-1", "effort": "Not A Name", "once": None},
        {"op": "session_context", "session_id": "dm-1"},
        {"op": "session_context", "session_id": "missing-session"},
    ],
    "builder_campaign_session": [
        create(session_id="builder-1", campaign_id=BUILDER, role="builder",
               selected_skill="BUILD CAMPAIGN PLAN"),
        append("builder-1", "u1", "user", {"text": "Make it rain."}),
        {"op": "update_agent_session_status", "session_id": "builder-1", "status": "review_required"},
        append("builder-1", "u2", "user", {"text": "Not while reviewing."}),
        {"op": "update_agent_session_status", "session_id": "builder-1", "status": "active"},
        {"op": "update_agent_session_status", "session_id": "builder-1", "status": "stopped"},
        {"op": "session_context", "session_id": "builder-1"},
    ],
    "builder_chats": [
        chat("chat-1"),
        chat("chat-1"),
        chat("chat-1", provider="other.provider"),
        chat("chat-2", name="Second chat"),
        create(session_id="chat-1"),
        {"op": "builder_chat_index"},
        {"op": "rename_builder_chat", "session_id": "chat-1", "display_name": "Automatic title", "automatic": True},
        {"op": "rename_builder_chat", "session_id": "chat-1", "display_name": "My title", "automatic": False},
        {"op": "rename_builder_chat", "session_id": "chat-1", "display_name": "Another automatic", "automatic": True},
        append("chat-1", "c1", "user", {"text": "Hi."}),
        append("chat-1", "c2", "canonical_reference", {"event": "x"}, canonical=1),
        {"op": "update_agent_session_status", "session_id": "chat-2", "status": "stopped"},
        {"op": "resume_agent_session", "session_id": "chat-2"},
        {"op": "update_agent_session_model", "session_id": "chat-2", "provider_id": "next.provider",
         "model_id": "next.provider/model"},
        {"op": "update_agent_session_reasoning", "session_id": "chat-2", "effort": "high", "once": "low"},
        {"op": "session_context", "session_id": "chat-1"},
        {"op": "make_workspace", "session_id": "chat-2"},
        {"op": "delete_builder_chat", "session_id": "chat-2"},
        {"op": "workspace_exists", "session_id": "chat-2"},
        {"op": "delete_builder_chat", "session_id": "chat-2"},
        chat("CON", name="Reserved name"),
        {"op": "delete_builder_chat", "session_id": "CON"},
        {"op": "builder_chat_index"},
    ],
}


def ordered(value: object) -> object:
    """Plain JSON in Python's own key order; `_dump` would sort every mapping."""
    if isinstance(value, Mapping):
        return {str(key): ordered(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [ordered(item) for item in value]
    return value


def plain(value: object) -> object:
    a._dump(value)  # canonical JSON still refuses what it refuses
    return ordered(value)


def session_json(record: SessionRecord) -> dict[str, object]:
    return {
        "session_id": record.session_id, "campaign_id": record.campaign_id, "role": record.role.value,
        "provider_id": record.provider_id, "model_id": record.model_id, "status": record.status.value,
        "parent_session_id": record.parent_session_id, "selected_skill": record.selected_skill,
        "scope": list(record.scope), "reasoning_effort": record.reasoning_effort,
        "reasoning_effort_once": record.reasoning_effort_once,
    }


def value_json(value: object) -> object:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, SessionRecord):
        return session_json(value)
    if isinstance(value, tuple):
        return [value_json(item) for item in value]
    name = type(value).__name__
    if name == "BuilderChatRecord":
        return {"session": session_json(value.session), "display_name": value.display_name,  # type: ignore[attr-defined]
                "created_ordinal": value.created_ordinal}  # type: ignore[attr-defined]
    if name == "SessionMessage":
        return {"session_id": value.session_id, "sequence": value.sequence,  # type: ignore[attr-defined]
                "message_id": value.message_id, "kind": value.kind.value,  # type: ignore[attr-defined]
                "content": plain(value.content), "canonical_sequence": value.canonical_sequence}  # type: ignore[attr-defined]
    if name == "SessionContext":
        return {"session": session_json(value.session),  # type: ignore[attr-defined]
                "messages": [value_json(message) for message in value.messages]}  # type: ignore[attr-defined]
    raise TypeError(f"unexpected outcome {name}")


def run(store: a.SQLiteAuthorityStore, root: Path, op: dict[str, object]) -> dict[str, object]:
    kind = str(op["op"])
    args = {key: value for key, value in op.items() if key != "op"}
    try:
        if kind == "create_agent_session":
            fields = dict(args["record"])  # type: ignore[arg-type]
            fields["role"] = WriterRole(fields["role"])
            fields["status"] = SessionStatus(fields["status"])
            fields["scope"] = tuple(fields["scope"])  # type: ignore[arg-type]
            value: object = store.create_agent_session(SessionRecord(**fields))  # type: ignore[arg-type]
        elif kind == "append_agent_message":
            args["kind"] = SessionMessageKind(args["kind"])
            value = store.append_agent_message(**args)  # type: ignore[arg-type]
        elif kind == "update_agent_session_status":
            value = store.update_agent_session_status(str(args["session_id"]), SessionStatus(args["status"]))
        elif kind == "update_agent_session_reasoning":
            value = store.update_agent_session_reasoning(
                str(args["session_id"]), effort=args["effort"], once=args["once"],  # type: ignore[arg-type]
            )
        elif kind == "make_workspace":
            folder = root / "builder-chats" / str(args["session_id"]) / "notes"
            folder.mkdir(parents=True)
            (folder / "draft.txt").write_text("draft", encoding="utf-8")
            value = None
        elif kind == "workspace_exists":
            value = (root / "builder-chats" / str(args["session_id"])).exists()
        elif kind == "builder_chat_index":
            value = store.builder_chat_index()
        else:
            value = getattr(store, kind)(**args)
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    return {"ok": value_json(value)}


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    record: dict[str, object] = {"python": sys.version.split()[0], "runs": {}}
    with tempfile.TemporaryDirectory(dir=OUT.parent) as scratch:
        database = seed(Path(scratch) / "seed")
        checkpoint = a.sqlite3.connect(database)
        checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        checkpoint.close()
        seed_bytes = database.read_bytes()
        (OUT / "seed.sqlite3.gz").write_bytes(gzip.compress(seed_bytes, mtime=0))
        for name, operations in RUNS.items():
            folder = Path(scratch) / name
            folder.mkdir()
            (folder / "authority.sqlite3").write_bytes(seed_bytes)
            store = a._open_for_application_owner(folder / "authority.sqlite3")
            try:
                outcomes = [run(store, folder, op) for op in operations]
            finally:
                store.close()
            checkpoint = a.sqlite3.connect(folder / "authority.sqlite3")
            checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            checkpoint.close()
            data = (folder / "authority.sqlite3").read_bytes()
            (OUT / f"{name}.sqlite3.gz").write_bytes(gzip.compress(data, mtime=0))
            record["runs"][name] = {"operations": operations, "outcomes": outcomes}  # type: ignore[index]
    text = json.dumps(record, indent=1, ensure_ascii=True) + "\n"
    (OUT / "session_runs.json").write_bytes(text.encode("utf-8"))


if __name__ == "__main__":
    main()
