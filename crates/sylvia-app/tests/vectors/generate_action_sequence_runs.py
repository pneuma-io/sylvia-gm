"""Record action-sequence runs for sylvia-app::action_sequences from the frozen Python's own tests.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_action_sequence_runs.py --record

(`.venv/bin/python` on Linux.) Reference tooling for the port, retired with the Python source.
The default run (without `--record`) rewrites the file from the runs it keeps, so it is
deterministic; `--record` reruns pytest, whose fixtures mint fresh identities, so a new
recording differs in its identities.

With `--record` this file runs as a pytest plugin over Python's action-sequence tests. It wraps
every owner operation `application/action_sequences.py` calls (the `SequenceOwner` trait's
methods), and records a call only when `action_sequences` itself made it: the owner's own
internals are not recorded. Each run is one top-level call of `read`, `compose`, `execute` or
`last_attempt_commit`, with its arguments, the ordered trace of owner calls (each with its
arguments and its answer or exception, and each guard's enter and exit), and the function's
result or exception. The wrappers record and pass through; they change nothing the tests do,
which `--check` shows by running the tests with and without the plugin.

Trigger Discipline's content is private, so a test that builds on it (its `td` fixture, or a
source naming the package) records nothing, and a run whose recording names the package, its
setting or its campaign is left out too.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUTPUT = HERE / "action_sequence_runs.json"
RECORDING_ENV = "SYLVIA_SEQUENCE_RECORDING"
RECORDED_TESTS = [
    "tests/test_action_sequences.py", "tests/test_action_sequence_provider.py",
    "tests/test_srd_creature_space_route_continuation.py", "tests/test_srd_lite_reaction_grants.py",
    "tests/test_gm_hotfix_upgrade.py", "tests/test_gm_memory_casting_hotfix.py",
    "tests/test_updater_move_observer.py", "tests/test_alpha_f6c_canonical_frontend.py",
    # Scenarios of this generator's own, for the paths Python's tests reach only with Trigger
    # Discipline or not at all: refusals, and an owner crash before or after a native commit.
    "crates/sylvia-app/tests/vectors/action_sequence_scenarios.py",
]
PRIVATE_MARKERS = ("trigger-discipline", "trigger_discipline", "trigger discipline", "siren", "queue-td")
PER_ENTRY = 200
DOCUMENT_BYTES = 1024
SEQUENCES = "sylvia_gm.application.action_sequences"


def plain(value: object) -> object:
    """A JSON copy of a frozen or plain value."""
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(plain(item) for item in value)
    if isinstance(value, Sequence) and not isinstance(value, str):
        return [plain(item) for item in value]
    if hasattr(value, "value") and type(value).__module__.startswith("sylvia_gm"):
        return value.value
    return value


# ── What a run records ─────────────────────────────────────────────────────────────────────


def error_record(error: BaseException) -> dict:
    return {"class": type(error).__name__, "message": str(error), "code": getattr(error, "code", None)}


def session_record(context) -> dict:
    session = context.session
    return {
        "session": {"session_id": session.session_id, "campaign_id": session.campaign_id,
                    "role": session.role.value, "status": session.status.value,
                    "provider_id": session.provider_id, "model_id": session.model_id},
        "messages": [{"session_id": message.session_id, "sequence": message.sequence,
                      "message_id": message.message_id, "kind": message.kind.value,
                      "content": plain(message.content), "canonical_sequence": message.canonical_sequence}
                     for message in context.messages],
    }


def snapshot_record(snapshot) -> dict:
    return {
        "campaign_id": snapshot.campaign_id, "revision": snapshot.revision,
        "events": [{"sequence": event.sequence, "event_type": event.event_type.value, "turn_id": event.turn_id,
                    "action_id": event.action_id} for event in getattr(snapshot, "events", ())],
        "actions": [{"action_id": action.action_id} for action in getattr(snapshot, "actions", ())],
        "consequences": [{"action_id": record.action_id, "ordinal": record.ordinal, "payload": plain(record.payload)}
                         for record in getattr(snapshot, "consequences", ())],
    }


def snapshot_argument(snapshot) -> dict:
    return {"campaign_id": snapshot.campaign_id, "revision": snapshot.revision}


def tool_record(tool) -> dict:
    controls = tool.player_controls
    return {"package_id": tool.package_id, "package_version": tool.package_version,
            "package_sha256": tool.package_sha256, "name": tool.name, "capability": tool.capability,
            "model_behavior": str(tool.model_behavior), "decision_title": tool.decision_title,
            "player_controls": {"available": controls.available, "response": controls.response,
                                "confirmation": controls.confirmation}}


def tool_argument(tool) -> dict:
    return {"name": tool.name, "package_sha256": tool.package_sha256}


def receipt_record(receipt) -> dict:
    from sylvia_gm.domain.authority import AwaitingChoiceReceipt

    if isinstance(receipt, AwaitingChoiceReceipt):
        return {"kind": "awaiting_choice", "campaign_id": receipt.campaign_id, "action_id": receipt.action_id,
                "basis_revision": receipt.basis_revision, "turn_id": receipt.turn_id,
                "choice": {"choice_id": receipt.choice.choice_id, "prompt": receipt.choice.prompt,
                           "options": [plain(dataclasses.asdict(option)) for option in receipt.choice.options]},
                "choice_sequence": receipt.choice_sequence}
    if isinstance(receipt, Mapping):
        return {"kind": "preview", "value": plain(receipt)}
    return {"kind": "receipt", "campaign_id": receipt.campaign_id, "action_id": receipt.action_id,
            "status": str(receipt.status), "result": plain(receipt.result),
            "resulting_revision": receipt.resulting_revision}


def control_record(control) -> dict:
    tool, parameters, label, actor_parameter = control
    return {"tool": tool_record(tool), "parameters": plain(parameters), "label": label,
            "actor_parameter": actor_parameter}


# Each recorded owner operation: (the trait method's name, how to record its arguments, how to
# record its answer).
def arguments_of(name: str, args: tuple, kwargs: dict) -> object:
    if name in ("player_control_scope",):
        return [snapshot_argument(args[0])]
    if name in ("resolve_panel_control", "resolve_card_control"):
        return [snapshot_argument(args[0]), plain(args[1]), plain(args[2])]
    if name == "check_player_action":
        return [tool_argument(args[0]), plain(args[1]), snapshot_argument(args[2])]
    if name == "player_action_preview":
        return [tool_argument(args[0]), plain(args[1]), snapshot_argument(args[2]), plain(args[3]), plain(args[4])]
    if name == "player_action_in_owned_work":
        return plain({**kwargs, "preview_only": kwargs.get("preview_only", False)})
    if name == "append_player_action_references":
        return [args[0], receipt_record(args[1])]
    if name == "append_session_message":
        return {"session_id": kwargs["session_id"], "message_id": kwargs["message_id"],
                "kind": kwargs["kind"].value, "content": plain(kwargs["content"])}
    if name == "player_action_markers":
        return [args[0], list(args[1]), args[2]]
    return [plain(item) for item in args] + ([plain(kwargs)] if kwargs else [])


def answer_of(name: str, value: object) -> object:
    if name == "append_session_message":
        return None  # The trait answers nothing; Python's saved message is not read.
    if name in ("active_dm_context", "session_context"):
        return session_record(value)
    if name == "campaign_session_contexts":
        return [session_record(context) for context in value]
    if name == "snapshot":
        return snapshot_record(value)
    if name == "action_hit":
        return None if value is None else {"payload_hash": value.payload_hash, "receipt": receipt_record(value.receipt)}
    if name == "has_pending_mechanics_action":
        return value is not None
    if name in ("resolve_panel_control", "resolve_card_control"):
        return control_record(value)
    if name == "player_action_in_owned_work":
        return receipt_record(value)
    if name == "catalog":
        return [tool_record(tool) for tool in value]
    return None if value is None else plain(value)


# ── Recording (pytest plugin) ──────────────────────────────────────────────────────────────

LOCAL = threading.local()
# Whether the running test builds on Trigger Discipline (set per test, read on every thread).
PRIVATE_TEST = {"value": False, "nodeid": None}


def pytest_runtest_setup(item):
    import inspect

    try:
        source = inspect.getsource(item.function).lower()
    except (OSError, TypeError):
        source = ""
    PRIVATE_TEST["value"] = "td" in getattr(item, "fixturenames", ()) or any(
        marker in source for marker in ("trigger", " td", "td_", "td)", "(td"))
    PRIVATE_TEST["nodeid"] = item.nodeid


def recording() -> list | None:
    """The running entry's trace, unless a recorded owner call is running: what that call does
    inside the owner (even through sequence code) is the owner's own work, not a sequence call."""
    if getattr(LOCAL, "inside_call", 0):
        return None
    return getattr(LOCAL, "trace", None)


def from_sequences(depth: int = 2) -> bool:
    return sys._getframe(depth).f_globals.get("__name__") == SEQUENCES


# A scenario's injected owner fault: the `nth` recorded call named `name` (and matching `where`)
# raises `RuntimeError`, before the real call or after it, as an owner crash would.
FAULT: dict = {}


def injected(name: str, args: tuple, kwargs: dict, when: str) -> bool:
    if not FAULT or FAULT.get("name") != name or FAULT.get("when") != when:
        return False
    if "where" in FAULT and not FAULT["where"](args, kwargs):
        return False
    FAULT["seen"] = FAULT.get("seen", 0) + 1
    return FAULT["seen"] == FAULT.get("nth", 1)


def record_call(name: str, function, args: tuple, kwargs: dict):
    trace = recording()
    if trace is None or not from_sequences(3):
        return function(*args, **kwargs)
    event = {"call": name, "args": arguments_of(name, args, kwargs)}
    trace.append(event)
    if injected(name, args, kwargs, "before"):
        event["raise"] = {"class": "RuntimeError", "message": "injected owner fault", "code": None}
        raise RuntimeError("injected owner fault")
    LOCAL.inside_call = getattr(LOCAL, "inside_call", 0) + 1
    try:
        value = function(*args, **kwargs)
        if injected(name, args, kwargs, "after"):
            event["raise"] = {"class": "RuntimeError", "message": "injected owner fault", "code": None}
            raise RuntimeError("injected owner fault")
    except Exception as error:
        event["raise"] = error_record(error)
        raise
    finally:
        LOCAL.inside_call -= 1
    event["return"] = answer_of(name, value)
    return value


class Guard:
    """A recorded `with` block around a guard's own context manager."""

    def __init__(self, name, inner, args, recorded):
        self.name, self.inner, self.args, self.recorded = name, inner, args, recorded

    def __enter__(self):
        trace = recording() if self.recorded else None
        event = None
        if trace is not None:
            event = {"guard": self.name, "enter": self.args}
            trace.append(event)
        try:
            return self.inner.__enter__()
        except Exception as error:
            if event is not None:
                event["raise"] = error_record(error)
            raise

    def __exit__(self, *exc):
        trace = recording() if self.recorded else None
        if trace is not None:
            trace.append({"guard": self.name, "exit": self.args})
        return self.inner.__exit__(*exc)


class RecordedLock:
    """`owner._narration_lock`: an `RLock` whose `with` blocks in `action_sequences` are recorded."""

    def __init__(self, inner):
        self._inner = inner

    def __enter__(self):
        if recording() is not None and from_sequences(2):
            recording().append({"guard": "narration", "enter": []})
            self._entered_by_sequences = getattr(self, "_entered_by_sequences", 0) + 1
            self._stack().append(True)
        else:
            self._stack().append(False)
        return self._inner.__enter__()

    def __exit__(self, *exc):
        if self._stack().pop() and recording() is not None:
            recording().append({"guard": "narration", "exit": []})
        return self._inner.__exit__(*exc)

    def _stack(self):
        if not hasattr(LOCAL, "lock_stack"):
            LOCAL.lock_stack = []
        return LOCAL.lock_stack

    def acquire(self, *args, **kwargs):
        return self._inner.acquire(*args, **kwargs)

    def release(self):
        return self._inner.release()


class RecordedCounts(dict):
    """`owner._campaign_work_counts`, whose `get` from `action_sequences` is recorded."""

    def get(self, key, default=None):
        value = dict.get(self, key, default)
        trace = recording()
        if trace is not None and from_sequences(2):
            trace.append({"call": "campaign_work_count", "args": [key], "return": int(value or 0)})
        return value


class RecordedAttempts(dict):
    """`owner._attempts`, whose `values()` from `action_sequences` is recorded."""

    def values(self):
        items = list(dict.values(self))
        trace = recording()
        if trace is not None and from_sequences(2):
            trace.append({"call": "attempts", "return": [
                {"campaign_id": item.campaign_id, "attempt_id": item.attempt_id, "turn_id": item.turn_id,
                 "terminal": bool(item.terminal)} for item in items]})
        return items


def pytest_configure(config):  # noqa: ARG001 - pytest hook
    import sylvia_gm.application.action_sequences as sequences
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.application.session import SessionApplication
    from sylvia_gm.infrastructure.sqlite_authority import SQLiteAuthorityStore

    sink = os.environ[RECORDING_ENV]
    owner_methods = {
        "_active_dm_context": "active_dm_context", "_player_control_scope": "player_control_scope",
        "_resolve_panel_control": "resolve_panel_control", "_resolve_card_control": "resolve_card_control",
        "_check_player_action": "check_player_action", "_player_action_preview": "player_action_preview",
        "campaign_panels": "campaign_panels", "_player_action_in_owned_work": "player_action_in_owned_work",
        "_append_player_action_references": "append_player_action_references", "_catalog": "catalog",
        "bind_narration_action": "bind_narration_action",
    }
    store_methods = {
        "snapshot": "snapshot", "action_hit": "action_hit", "pending_mechanics_action": "has_pending_mechanics_action",
        "current_open_turn": "current_open_turn", "campaign_session_contexts": "campaign_session_contexts",
        "player_action_markers": "player_action_markers",
    }
    session_methods = {"context": "session_context", "append_message": "append_session_message"}

    def wrap(cls, attribute, name):
        original = getattr(cls, attribute)

        def wrapper(*args, **kwargs):
            return record_call(name, original, args, kwargs)

        def bound(self, *args, **kwargs):
            return record_call(name, lambda *a, **k: original(self, *a, **k), args, kwargs)

        setattr(cls, attribute, bound)
        return wrapper

    for attribute, name in owner_methods.items():
        wrap(AuthorityOwner, attribute, name)
    for attribute, name in store_methods.items():
        wrap(SQLiteAuthorityStore, attribute, name)
    for attribute, name in session_methods.items():
        wrap(SessionApplication, attribute, name)

    for attribute, guard in (("_operation", "operation"), ("_direct_action_work", "direct_action_work")):
        original = getattr(AuthorityOwner, attribute)

        def make(original=original, guard=guard):
            def guarded(self, *args):
                return Guard(guard, original(self, *args), list(args), from_sequences(2) and recording() is not None)
            return guarded

        setattr(AuthorityOwner, attribute, make())
    original_lane = AuthorityOwner._lane

    def lane(self, campaign_id):
        return Guard("lane", original_lane(self, campaign_id), [campaign_id],
                     from_sequences(2) and recording() is not None)

    AuthorityOwner._lane = lane
    original_init = AuthorityOwner.__init__

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._narration_lock = RecordedLock(self._narration_lock)
        self._campaign_work_counts = RecordedCounts(self._campaign_work_counts)
        self._attempts = RecordedAttempts(self._attempts)

    AuthorityOwner.__init__ = init

    def entry(name, function):
        def run(*args, **kwargs):
            if getattr(LOCAL, "trace", None) is not None:
                return function(*args, **kwargs)
            LOCAL.trace = []
            arguments = {"positional": [plain(item) for item in args[1:]], "keywords": plain(kwargs)}
            try:
                value = function(*args, **kwargs)
                outcome = {"ok": plain(value) if value is not None else None}
                raised = None
            except Exception as error:
                outcome = {"error": error_record(error)}
                raised = error
            finally:
                trace, LOCAL.trace = LOCAL.trace, None
            try:
                line = json.dumps({"entry": name, "test": PRIVATE_TEST["nodeid"], "private": PRIVATE_TEST["value"],
                                   "arguments": arguments, "trace": trace, "outcome": outcome},
                                  ensure_ascii=True, allow_nan=False)
            except (TypeError, ValueError) as problem:
                line = json.dumps({"entry": name, "unrecordable": str(problem)})
            with open(sink, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            if raised is not None:
                raise raised
            return value
        return run

    for name in ("read", "compose", "execute", "last_attempt_commit"):
        setattr(sequences, name, entry(name, getattr(sequences, name)))


# ── Keeping runs ───────────────────────────────────────────────────────────────────────────


def run_tests(with_plugin: bool, sink: Path | None, basetemp: Path) -> subprocess.CompletedProcess:
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join([str(HERE), str(ROOT)])}
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--basetemp={basetemp}",
               "-rA", *RECORDED_TESTS]
    if with_plugin:
        environment[RECORDING_ENV] = str(sink)
        command[3:3] = ["-p", "generate_action_sequence_runs"]
    return subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, check=False)


def outcomes(done: subprocess.CompletedProcess) -> list[str]:
    """Each test's id and outcome, from `-rA`'s short summary."""
    lines = [line for line in done.stdout.splitlines()
             if line.split(" ", 1)[0] in {"PASSED", "FAILED", "ERROR", "SKIPPED", "XFAIL", "XPASS"}]
    return sorted(lines)


def record() -> tuple[list[dict], dict]:
    sink = HERE / "action_sequence_recording.jsonl"
    sink.unlink(missing_ok=True)
    basetemp = ROOT / ".sequence-recording-tmp"
    wrapped = run_tests(True, sink, basetemp)
    shutil.rmtree(basetemp, ignore_errors=True)
    plain_run = run_tests(False, None, basetemp)
    shutil.rmtree(basetemp, ignore_errors=True)
    check = {"wrapped": outcomes(wrapped), "unwrapped": outcomes(plain_run)}
    kept: dict[str, list[dict]] = {}
    seen = set()
    skipped = {"private": 0, "unrecordable": 0}
    for line in sink.read_text(encoding="utf-8").splitlines():
        if any(marker in line.lower() for marker in PRIVATE_MARKERS):
            skipped["private"] += 1
            continue
        item = json.loads(line)
        if item.pop("private", False):
            skipped["private"] += 1
            continue
        item.pop("test", None)
        if "unrecordable" in item:
            skipped["unrecordable"] += 1
            continue
        identity = hashlib.sha256(line.encode()).hexdigest()
        if identity in seen:
            continue
        seen.add(identity)
        group = kept.setdefault(item["entry"], [])
        if len(group) < PER_ENTRY:
            group.append(item)
    sink.unlink(missing_ok=True)
    runs = [item for entry in ("read", "compose", "execute", "last_attempt_commit") for item in kept.get(entry, [])]
    return runs, {"check": check, "skipped": skipped}


# The message fields the sequence code reads (`_load`, `_bound_queues`, `_bind_execution_basis`
# and `response_reservations`); a saved queue is kept whole.
READ_FIELDS = ("kind", "sequence_id", "attempt_id", "basis_message_id", "narration_basis_message_id")
SHARED_BYTES = 512


def trim_context(context: dict) -> dict:
    messages = []
    for message in context["messages"]:
        content = message["content"]
        if content.get("kind") != "action_sequence":
            content = {key: content[key] for key in READ_FIELDS if key in content}
        messages.append({**message, "content": content})
    return {**context, "messages": messages}


def trim(run: dict) -> dict:
    """Only what the sequence code reads of each answered session context."""
    for event in run["trace"]:
        answer = event.get("return")
        if event.get("call") in ("active_dm_context", "session_context") and answer:
            event["return"] = trim_context(answer)
        elif event.get("call") == "campaign_session_contexts" and answer:
            event["return"] = [trim_context(context) for context in answer]
    return run


def intern(value: object, shared: dict) -> object:
    """Each object or list kept once in `shared`, at any depth, as `{"__shared__": key}` (over SHARED_BYTES)."""
    if isinstance(value, dict):
        value = {key: intern(item, shared) for key, item in value.items()}
    elif isinstance(value, list):
        value = [intern(item, shared) for item in value]
    else:
        return value
    # Hashed in its own key order: two equal values in different orders are different records.
    text = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    if len(text) <= SHARED_BYTES:
        return value
    key = hashlib.sha256(text.encode()).hexdigest()[:24]
    shared[key] = value
    return {"__shared__": key}


def expand(value: object, shared: dict) -> object:
    if isinstance(value, dict) and set(value) == {"__shared__"}:
        return expand(shared[value["__shared__"]], shared)
    if isinstance(value, dict):
        return {key: expand(item, shared) for key, item in value.items()}
    if isinstance(value, list):
        return [expand(item, shared) for item in value]
    return value


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    if "--record" in sys.argv:
        runs, report = record()
        same = report["check"]["wrapped"] == report["check"]["unwrapped"]
        print(f"tests with and without the plugin: {'same' if same else 'DIFFERENT'} "
              f"({len(report['check']['wrapped'])} results); skipped {report['skipped']}")
        if not same:
            for line in sorted(set(report["check"]["wrapped"]) ^ set(report["check"]["unwrapped"])):
                print("  ", line)
            return 1
    else:
        previous = json.loads(OUTPUT.read_text(encoding="utf-8"))
        runs = [expand(run, previous.get("shared", {})) for run in previous["runs"]]
    shared: dict = {}
    kept = []
    for index, run in enumerate(runs):
        run = trim(run)
        run["label"] = f"{run['entry']} {index}"
        kept.append(intern(run, shared))
    text = json.dumps({"python": sys.version.split()[0], "shared": dict(sorted(shared.items())), "runs": kept},
                      ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    assert " at 0x" not in text
    lowered = text.lower()
    found = [marker for marker in PRIVATE_MARKERS if marker in lowered]
    assert not found, f"private markers in the vectors: {found}"
    OUTPUT.write_bytes((text + "\n").encode("ascii"))
    counts: dict[str, int] = {}
    for run in runs:
        key = f"{run['entry']} {'ok' if 'ok' in run['outcome'] else run['outcome']['error']['class']}"
        counts[key] = counts.get(key, 0) + 1
    print(len(runs), "runs:", dict(sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
