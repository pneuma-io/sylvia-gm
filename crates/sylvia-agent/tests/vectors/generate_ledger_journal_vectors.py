"""Write ledger_journal_vectors.json: the wire journal and Claude Code's native records (L3).

Run from the repository root with the snapshot's interpreter, after `cargo build -p
sylvia-compare`:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_ledger_journal_vectors.py

As `generate_ledger_state_vectors.py` (whose renaming and encoding this reuses), two sources of
cases, every answer Python's own:

- `traced`: each distinct call real GM requests make to these functions, with the Python owner
  in forced ledger mode over the `ruleless-turn`, `srd-mechanics-turn`, `fail-retry`,
  `stop-redirect`, `opening-retry` and `reopen` sessions. `reopen` restarts the owner, so
  `load_journal` rebuilds real saved journals. Unlike the L2 generators, the traced inputs are
  kept exactly (no identities renamed), since renaming inside saved entries would break their
  chain hashes; the traced owner's seeded `uuid4` makes each run the same. A `JournalState` is
  `{"__journal__": fields}` (integer keys as text, `present` as sorted pairs).
- `synthetic`: saved records built here, valid chains and each way one breaks (version, owner,
  base count, previous head, entries, head, generations), and the native-record functions,
  which only the `claude_code` route reaches. `native_record`'s `uuid4` is fixed here.

Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

import generate_ledger_state_vectors as state  # noqa: E402
from sylvia_gm.agent import _context_ledger as ledger  # noqa: E402

FUNCTIONS = ["_genesis", "_chain", "_valid_entry", "saved_tip", "load_journal", "native_gap",
             "native_inputs", "native_answers", "native_record"]
SESSIONS = ["ruleless-turn", "srd-mechanics-turn", "fail-retry", "stop-redirect", "opening-retry", "reopen"]
RECORD_ID = uuid.UUID(int=0x5EED, version=4)


class FixedUuid:
    """`uuid` for `native_record`: one fixed id, so its record is the same on every run."""

    @staticmethod
    def uuid4() -> uuid.UUID:
        return RECORD_ID


def journal(fields: dict[str, object]) -> object:
    return ledger.JournalState(
        epoch=fields["epoch"], generation=fields["generation"], entries=decode(fields["entries"]),
        head=fields["head"], view=decode(fields["view"]), keyframe_bytes=fields["keyframe_bytes"],
        update_bytes=fields["update_bytes"], heads={int(key): value for key, value in fields["heads"].items()},
        present=frozenset((label, digest) for label, digest in fields["present"]),
        native={int(key): decode(value) for key, value in fields["native"].items()},
        thinking_floor=fields["thinking_floor"], last_prefix=fields["last_prefix"],
        last_toolset=fields["last_toolset"])


def decode(value: object) -> object:
    if isinstance(value, dict) and set(value) == {"__journal__"}:
        return journal(value["__journal__"])
    if isinstance(value, dict) and set(value) not in ({"__tagged__"}, {"__set__"}):
        return {key: decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode(item) for item in value]
    return state.decode(value)


def encode(value: object) -> object:
    if isinstance(value, ledger.JournalState):
        return {"__journal__": {
            "epoch": value.epoch, "generation": value.generation, "entries": encode(value.entries),
            "head": value.head, "view": encode(value.view), "keyframe_bytes": value.keyframe_bytes,
            "update_bytes": value.update_bytes, "heads": {str(k): v for k, v in value.heads.items()},
            "present": sorted([label, digest] for label, digest in value.present),
            "native": {str(k): encode(v) for k, v in value.native.items()},
            "thinking_floor": value.thinking_floor, "last_prefix": encode(value.last_prefix),
            "last_toolset": encode(value.last_toolset)}}
    if isinstance(value, dict):
        return {str(key): encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    return state.encode(value)


def call(function: str, args: list[object], kwargs: dict[str, object]) -> dict[str, object]:
    case: dict[str, object] = {"function": function, "args": args, "kwargs": kwargs}
    saved_uuid = ledger.uuid
    ledger.uuid = FixedUuid  # type: ignore[assignment]
    try:
        result = getattr(ledger, function)(*decode(args), **decode(kwargs))
    except Exception as error:  # noqa: BLE001 - the class is the vector
        case["raised"] = {"class": type(error).__name__, "message": str(error)}
    else:
        case["result"] = encode(result)
    finally:
        ledger.uuid = saved_uuid
    return case


def traced() -> list[dict[str, object]]:
    compare = ROOT / "target" / "debug" / ("sylvia-compare.exe" if sys.platform == "win32" else "sylvia-compare")
    python = Path(sys.executable).resolve()
    entries: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="ledger-journal-") as folder:
        for session in SESSIONS:
            trace = Path(folder) / f"{session}.jsonl"
            subprocess.run(
                [str(compare), "record", "--session",
                 str(ROOT / f"crates/sylvia-compare/sessions/{session}.session.json"),
                 "--out", str(Path(folder) / session), "--owner", str(python), "--arg", "-B",
                 "--arg", str(HERE / "trace_ledger_functions.py"),
                 "--env", "SYLVIA_GM_CONTEXT_MODE=ledger", "--env", f"SYLVIA_LEDGER_TRACE={trace}",
                 "--env", "SYLVIA_LEDGER_TRACED=" + ",".join(FUNCTIONS)],
                capture_output=True, cwd=ROOT)
            if trace.exists():
                entries.extend(json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines())
    # Kept exactly as traced: renaming the identities inside saved entries would break their
    # chain hashes, and the traced owner's seeded `uuid4` already makes each run the same.
    seen: set[str] = set()
    cases = []
    for entry in entries:
        args, kwargs = entry["args"], entry["kwargs"]
        key = json.dumps([entry["function"], args, kwargs], sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        cases.append(call(str(entry["function"]), args, kwargs))
    return cases


OWNER = {"owner": "gm", "id": 1}
SEAL = ledger.host_seal("session-1")


def entry(content: str, c: str = "conversation", role: str = "user") -> dict[str, object]:
    return {"c": c, "m": {"role": role, "content": content}}


def chain_records(epoch: str, generation: object, batches: list[list[dict[str, object]]],
                  **fields: object) -> list[dict[str, object]]:
    """Valid saved journal records: one per batch, each linked to the one before."""
    head = ledger._genesis(epoch, generation)
    records, count = [], 0
    for index, batch in enumerate(batches):
        new_head = ledger._chain(head, batch)
        records.append({"kind": "gm_wire_journal", "version": 1, "journal_record_id": f"j-{index}",
                        "context_owner": OWNER, "epoch": epoch, "generation": generation,
                        "base_count": count, "previous_head": head, "head": new_head, "entries": batch,
                        "keyframe_bytes": 10 * (index + 1), "update_bytes": index, **fields})
        head, count = new_head, count + len(batch)
    return records


def synthetic() -> list[dict[str, object]]:
    cases: list[tuple[str, list[object], dict[str, object]]] = []
    add = lambda function, *args, **kwargs: cases.append((function, list(args), kwargs))  # noqa: E731

    for epoch, generation in [("e", 0), ("eé", 3), ("e", True), ("", -1)]:
        add("_genesis", epoch, generation)
    add("_chain", "head", [])
    add("_chain", "head", [entry("a"), {"z": 1, "a": "é"}])
    for value in [entry("a"), entry("a", c="state"), entry("a", c="tools"), entry("a", c="other"),
                  {"c": "conversation", "m": {"role": 5}}, {"c": "conversation", "m": "x"}, {"c": "latest"},
                  "entry", {"c": ["conversation"], "m": {"role": "user"}}]:
        add("_valid_entry", value)

    keyframe = ledger.STATE_LABEL + " (application-owned current data):\n" + json.dumps({"campaign": {"revision": 1}})
    batches = [[entry("hello"), entry(ledger.seal_prefix(SEAL) + keyframe, c="state", role="system")],
               [entry("reply", role="assistant"), entry("more")]]
    good = chain_records("e", 0, batches, prefix_sha256="p1", toolset_sha256="t1", thinking_floor=2)
    good[1]["thinking_floor"] = 1
    good[1]["prefix_sha256"] = ""
    later = chain_records("e", 1, [[entry("new generation")]])
    other_epoch = chain_records("x", 0, [[entry("elsewhere")]])
    stub = {"kind": "gm_wire_journal", "omitted_from_export": True, "epoch": "e", "generation": 9}

    def broken(field: str, value: object) -> list[dict[str, object]]:
        records = json.loads(json.dumps(good))
        records[1][field] = value
        return records

    loads = {
        "empty": [], "valid": good, "valid and other epochs": [*other_epoch, stub, *good, "not a record"],
        "two generations": [*good, *later], "version": broken("version", 2), "version float": broken("version", 1.0),
        "owner": broken("context_owner", {"owner": "other"}), "base count": broken("base_count", 5),
        "previous head": broken("previous_head", "x"), "head": broken("head", "x"),
        "entries": broken("entries", "x"), "invalid entry": broken("entries", [{"c": "nope", "m": {"role": "u"}}]),
        "generation text": [*good, {**later[0], "generation": "1"}],
        "bool generation": chain_records("e", True, [[entry("b")]]),
        "keyframe bytes text": broken("keyframe_bytes", "7"),
        "keyframe bytes missing": [good[0], {k: v for k, v in good[1].items() if k != "keyframe_bytes"}],
        "keyframe bytes list": broken("keyframe_bytes", [1]),
    }
    head1 = good[0]["head"]
    native = lambda **fields: {"kind": "gm_native_records", "version": 1, "native_record_id": "n",  # noqa: E731
                               "context_owner": OWNER, "epoch": "e", "generation": 0, "boundary": 2,
                               "head": head1, "status": "collected", "records": [{"r": 1}], **fields}
    loads["native"] = [*good, native(), native(boundary=4, head=good[1]["head"], status="unknown", records=[],
                                               answer={"role": "tool"}),
                       native(boundary=2, records=[{"r": 2}]), native(boundary=3, head=None),
                       native(boundary=-1, head=None), native(boundary=True, head=None),
                       native(status="lost"), native(version=2), native(generation=1), native(records="ab"),
                       native(context_owner={"owner": "other"}), native(boundary="2"),
                       native(omitted_from_export=True), native(answer="x")]
    loads["native unhashable status"] = [*good, native(status=["collected"])]
    loads["native unhashable status, earlier mismatch"] = [*good, native(status=["collected"], version=2)]
    for name, records in loads.items():
        for owner in [OWNER, None]:
            add("load_journal", records, epoch="e", context_owner=owner, seal=SEAL)
    add("load_journal", good, epoch="e", context_owner={"id": 1.0, "owner": "gm"}, seal=SEAL)
    for records in [[], good, [*good, *later], [*good, stub], [{**good[0], "generation": True}],
                    [{**good[0], "entries": "abc", "base_count": "2", "head": None}],
                    [{**good[0], "base_count": 2.7}], [{**good[0], "base_count": [1]}], [{**good[0], "generation": 1.0}]]:
        add("saved_tip", records, "e")

    loaded, _ = ledger.load_journal(loads["native"], epoch="e", context_owner=OWNER, seal=SEAL)
    fresh = ledger.JournalState.fresh("e", 0)
    gap = ledger.JournalState.fresh("e", 0)
    gap.entries = [entry("a"), entry("b"), entry("c")]
    gap.heads.update({1: "h1", 2: "h2"})
    for given in [loaded, fresh, gap]:
        encoded = encode(given)
        for end in [0, 1, 2, 3, 4, 5, 9]:
            add("native_gap", encoded, end)
        add("native_inputs", encoded)
        add("native_answers", encoded)
    gap.native = {1: {"status": "collected", "records": "xy"}, 2: {"status": "unknown", "records": []}}
    add("native_gap", encode(gap), 3)
    add("native_inputs", encode(gap))
    gap.native = {2: {"status": "collected", "records": [1], "answer": {"a": 1}}, 1: {"status": "collected"}}
    add("native_gap", encode(gap), 3)
    add("native_inputs", encode(gap))
    add("native_answers", encode(gap))
    add("native_record", encode(loaded), context_owner=OWNER, boundary=2, status="collected", reason=None,
        records=[{"r": 1}], answer={"role": "tool", "content": "x"})
    add("native_record", encode(loaded), context_owner=None, boundary=7, status="unknown", reason="flush_incomplete",
        records=[])
    return [call(function, args, kwargs) for function, args, kwargs in cases]


def main() -> None:
    vectors = {"record_id": RECORD_ID.hex, "traced": traced(), "synthetic": synthetic()}
    out = HERE / "ledger_journal_vectors.json"
    out.write_bytes((json.dumps(vectors, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
    counts: dict[str, int] = {}
    for case in vectors["traced"]:
        counts[str(case["function"])] = counts.get(str(case["function"]), 0) + 1
    print("traced", counts, "synthetic", len(vectors["synthetic"]), file=sys.stderr)


if __name__ == "__main__":
    main()
