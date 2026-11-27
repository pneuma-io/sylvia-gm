"""Write loop_idle_vectors.json: `CampaignSessionLoop`'s answers with no live run (the stub).

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_loop_idle_vectors.py

Python's `CampaignSessionLoop` is built over a recording owner that answers
`session_activity_basis` with a given basis. No run is started, so each read-only method and
each method that needs a live run answers as it does on a loop with no provider work. The
`builder_activity` cases cover every branch of its saved-outcome projection. Each case holds
the call, the owner calls it made with their answers, and its result or what it raised.
Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

from sylvia_gm.agent._session_loop import CampaignSessionLoop  # noqa: E402
from sylvia_gm.domain.authority import WriterRole  # noqa: E402
from sylvia_gm.domain.session import (  # noqa: E402
    BUILDER_SKILLS,
    SessionActivityBasis,
    SessionMessage,
    SessionMessageKind,
    SessionRecord,
)

OUTPUT = HERE / "loop_idle_vectors.json"


def plain(value: object) -> object:
    import dataclasses
    import enum
    from collections.abc import Mapping
    if isinstance(value, enum.Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if not isinstance(value, (str, bytes)) and hasattr(value, "__iter__"):
        # Frozen JSON lists (the saved content's arrays).
        return [plain(item) for item in value]
    return value


class Owner:
    """An owner that answers `session_activity_basis` with one basis and logs each call."""

    def __init__(self, basis: SessionActivityBasis | None) -> None:
        self.basis = basis
        self.calls: list[dict[str, object]] = []

    def session_activity_basis(self, session_id: str, attempt_id: str | None = None) -> object:
        self.calls.append({"event": "call", "name": "session_activity_basis",
                           "args": [session_id, attempt_id], "kwargs": {},
                           "result": plain(self.basis)})
        return self.basis


def session(role: WriterRole) -> SessionRecord:
    # A Builder session in a campaign selects one construction skill.
    skill = sorted(BUILDER_SKILLS)[0] if role is WriterRole.BUILDER else None
    return SessionRecord(session_id="session-1", campaign_id="campaign-1", role=role,
                         provider_id="provider-1", model_id="model-1", selected_skill=skill)


def message(sequence: int, kind: SessionMessageKind, content: dict[str, object]) -> SessionMessage:
    return SessionMessage(session_id="session-1", sequence=sequence,
                          message_id=f"message-{sequence}", kind=kind, content=content)


ACCEPTED = message(3, SessionMessageKind.USER, {"text": "Go on", "attempt_id": "attempt-7"})
BUILDER_START = message(3, SessionMessageKind.EVIDENCE,
                        {"kind": "builder_start", "attempt_id": "attempt-7"})
TERMINAL = message(4, SessionMessageKind.EVIDENCE,
                   {"kind": "agent_attempt_terminal", "attempt_id": "attempt-7",
                    "status": "completed"})


def basis(role: WriterRole, *, accepted=None, terminal=None, compaction=False):  # type: ignore[no-untyped-def]
    return SessionActivityBasis(session=session(role), accepted=accepted, terminal=terminal,
                                has_compaction=compaction)


BUILDER_ACTIVITY = [
    ("dm_without_compaction", basis(WriterRole.DM, accepted=ACCEPTED), None),
    ("builder_no_attempt", basis(WriterRole.BUILDER), None),
    ("builder_accepted_interrupted", basis(WriterRole.BUILDER, accepted=ACCEPTED), None),
    ("builder_start_interrupted", basis(WriterRole.BUILDER, accepted=BUILDER_START), None),
    ("builder_terminal", basis(WriterRole.BUILDER, accepted=ACCEPTED, terminal=TERMINAL), None),
    ("builder_terminal_status_kept",
     basis(WriterRole.BUILDER, accepted=ACCEPTED,
           terminal=message(4, SessionMessageKind.EVIDENCE,
                            {"kind": "agent_attempt_terminal", "attempt_id": "attempt-7",
                             "status": {"odd": [1, 2]}})), None),
    ("builder_given_attempt_unavailable", basis(WriterRole.BUILDER), "attempt-9"),
    ("builder_given_attempt_interrupted", basis(WriterRole.BUILDER, accepted=ACCEPTED), "attempt-7"),
    # The given attempt is projected even when the saved record names another.
    ("builder_given_attempt_other_record", basis(WriterRole.BUILDER, accepted=ACCEPTED), "attempt-8"),
    ("dm_with_compaction", basis(WriterRole.DM, accepted=ACCEPTED, compaction=True), None),
    ("dm_with_compaction_no_attempt", basis(WriterRole.DM, compaction=True), None),
    ("accepted_attempt_number",
     basis(WriterRole.BUILDER,
           accepted=message(3, SessionMessageKind.USER, {"text": "Go", "attempt_id": 12})), None),
    ("accepted_without_attempt",
     basis(WriterRole.BUILDER, accepted=message(3, SessionMessageKind.USER, {"text": "Go"})), None),
    ("terminal_without_status",
     basis(WriterRole.BUILDER, accepted=ACCEPTED,
           terminal=message(4, SessionMessageKind.EVIDENCE,
                            {"kind": "agent_attempt_terminal", "attempt_id": "attempt-7"})), None),
]


def outcome(call) -> dict[str, object]:  # type: ignore[no-untyped-def]
    try:
        return {"result": plain(call())}
    except Exception as error:  # noqa: BLE001 - the reference records every outcome
        return {"raised": {"class": type(error).__name__, "message": str(error),
                           "code": getattr(error, "code", None)}}


def main() -> None:
    cases: list[dict[str, object]] = []
    for name, answer, attempt_id in BUILDER_ACTIVITY:
        owner = Owner(answer)
        loop = CampaignSessionLoop(owner)  # type: ignore[arg-type]
        found = outcome(lambda: loop.builder_activity("session-1", attempt_id=attempt_id))
        cases.append({"name": name, "method": "builder_activity", "attempt_id": attempt_id,
                      "calls": owner.calls, **found})
    owner = Owner(None)
    loop = CampaignSessionLoop(owner)  # type: ignore[arg-type]
    for method, call in [
        ("activity_progress", lambda: loop.activity_progress("session-1")),
        ("context_status_hint", lambda: loop.context_status_hint("session-1")),
        ("provisional_session_projection", lambda: loop.provisional_session_projection("session-1")),
        ("assert_idle", lambda: loop.assert_idle("session-1")),
        ("steer", lambda: loop.steer("session-1", "Wait", message_id="message-9")),
        ("stop", lambda: loop.stop("session-1")),
        ("expire", lambda: loop.expire("session-1", now=10.0)),
        ("disconnect", lambda: loop.disconnect("session-1", now=10.0)),
        ("reconnect", lambda: loop.reconnect("session-1", now=10.0, acknowledged_sequence=3)),
    ]:
        found = outcome(call)
        cases.append({"name": method, "method": method, "calls": list(owner.calls), **found})
        assert not owner.calls, f"{method} read the owner"
    text = json.dumps({"cases": cases}, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
    OUTPUT.write_bytes(text.encode("utf-8"))
    print(len(cases), "cases")
    for case in cases:
        print(" ", case["name"], "|", case.get("result", case.get("raised")))


if __name__ == "__main__":
    main()
