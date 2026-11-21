"""Generate reference vectors for sylvia_domain's identity and lock types from the frozen Python.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe crates/sylvia-domain/tests/vectors/generate_identity_vectors.py

It reads the shipped closures from the Python owner's startup recording (the package rows it
writes on first start) and asks the Python snapshot for every verdict, message and hash. The
output is deterministic (fixed seed) and checked in, so the Rust tests need no Python.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

from sylvia_gm.domain.authority import (
    MechanicOutcome,
    MechanicsChoiceOption,
    MechanicsChoiceRequest,
    PackageDependency,
    PackageLock,
    PackageLockEntry,
    PackageRole,
    Participant,
    ParticipantMutation,
    StateMutation,
    StateValue,
    _is_reserved_canonical_channel,
    canonical_payload_is_safe,
    mechanic_outcome_is_safe,
    response_blocks_text,
    thaw_json_value,
    validate_authority_identity,
    validate_response_blocks,
)
from sylvia_gm.domain.package_library import (
    ActivationCommand,
    ExactPackageDependency,
    PackageCandidate,
    PackageFile,
    CandidateStatus,
    activation_payload_sha256,
    canonical_json_bytes,
    closure_sha256,
    content_root_for_record,
    parse_canonical_json_bytes,
    parse_content_json_bytes,
    participant_schema_sha256,
    validate_portable_path,
)

ROOT = Path(__file__).resolve().parents[4]
OUTPUT = Path(__file__).with_name("identity_vectors.json")
RECORDING = ROOT / "crates/sylvia-compare/recordings/windows/startup.recording.json"
SPACES = [chr(c) for c in range(sys.maxunicode + 1) if chr(c).isspace()]
HEX = "0123456789abcdef"
SHA = "a" * 64
rng = random.Random(20261121)


def outcome(call):
    """[error kind, message] or [None, result]."""
    try:
        return [None, call()]
    except (ValueError, TypeError) as error:
        return [type(error).__name__, str(error)]


def identity_cases():
    values: list[object] = [
        "", " ", "a", "a b", "x" * 300, "caf\u00e9", "\u540d\u524d", "\u200b", "a\u200b", None, 1, 1.5, True, [], {},
        SHA, SHA.upper(), SHA[:63], SHA + "a", "g" * 64, "\u00e9" * 64, "\uff10" * 64, " " + SHA[1:],
    ]
    for space in SPACES:
        values += [space, space + "a", "a" + space, "a" + space + "b"]
    for _ in range(200):
        length = rng.choice((63, 64, 64, 64, 65))
        values.append("".join(rng.choice(HEX + "AF g") for _ in range(length)))
    cases = []
    for value in values:
        for sha in (False, True):
            cases.append({
                "value": value, "sha256": sha,
                "outcome": outcome(lambda: validate_authority_identity(value, "Label", sha256=sha) and None),
            })
    return cases


def path_cases():
    values = [
        "a", "a/b", "a/b/c.json", "", "/a", "a\\b", "a\x00", "C:a", "c:/x", "1:a", "\u00e9:a", "a:b", "a//b",
        "./a", "a/.", "a/..", "a/../b", " a", "a ", "a/ b", "a/b ", "\x1ca", "a\x1f", "a\u3000",
        "a<b", "a>b", "a\"b", "a|b", "a?b", "a*b", "a\tb", "a\nb", "a\x7f", "a.", "a/b.", "a..b",
        "CON", "con", "Con.txt", "aux.json", "NUL.tar.gz", "COM1", "com9.x", "COM0", "LPT\u00b9", "lpt\u00b2",
        "COM\u00b3.md", "CONIN$", "conout$.x", "CON .txt", "CON  .txt", "CONS", "console", "xcon", "a/prn",
        "con\u0131n$", "\ufb01le", "\u01c5", "name with space.json", "\u65e5\u672c/\u8a9e.json", "\u202e", "a\u0085b", "a/\u00a0b",
        "-", "~", "$", "a$b", ".hidden", "a/.hidden", "...", "a/...", "COM\u00b9", "lpt\u00b3.txt",
    ]
    cases = []
    for value in values:
        cases.append({"value": value, "outcome": outcome(lambda: validate_portable_path(value) and None)})
    for value in (None, 1, [], {"a": 1}):
        cases.append({"value": value, "outcome": outcome(lambda: validate_portable_path(value) and None)})
    return cases


def recorded_closures():
    recording = json.loads(RECORDING.read_text(encoding="utf-8"))
    tables = recording["database"]["tables"]
    manifests = {row["package_id"]: row for row in tables["package_manifests"]["rows"]}
    closures = []
    for candidate in tables["package_candidates"]["rows"]:
        members = sorted(
            (row for row in tables["package_candidate_memberships"]["rows"]
             if row["candidate_id"] == candidate["candidate_id"]),
            key=lambda row: row["closure_order"],
        )
        entries = []
        for member in members:
            record = manifests[member["package_id"]]["canonical_json"]["$json"]
            entries.append({
                "package_id": record["id"], "version": record["version"],
                "sha256": record["content_sha256"], "role": record["role"],
                "load_order": member["closure_order"],
                "dependencies": [
                    {"package_id": d["id"], "version": d["version"], "sha256": d["content_sha256"],
                     "kind": d["kind"], "namespace": d["namespace"]}
                    for d in record["dependencies"]
                ],
                "capabilities": record["capabilities"],
            })
        closures.append({
            "candidate_id": candidate["candidate_id"],
            "entries": entries,
            "recorded_closure_sha256": candidate["closure_sha256"],
            "closure_sha256": closure_sha256(build_lock(entries)),
        })
    records = []
    for row in tables["package_manifests"]["rows"]:
        record = row["canonical_json"]["$json"]
        # The Rust test reads the record itself from the same recording.
        records.append({
            "package_id": row["package_id"],
            "recorded_content_sha256": row["content_sha256"],
            "content_root": content_root_for_record(record),
            "canonical_sha256": hashlib.sha256(canonical_json_bytes(record)).hexdigest(),
        })
    return closures, records


def build_lock(entries):
    return PackageLock(entries=tuple(
        PackageLockEntry(
            package_id=entry["package_id"], version=entry["version"], sha256=entry["sha256"],
            role=PackageRole(entry["role"]), load_order=entry["load_order"],
            dependencies=tuple(PackageDependency(**dependency) for dependency in entry["dependencies"]),
            capabilities=tuple(entry["capabilities"]),
        )
        for entry in entries
    ))


def lock_cases(closures):
    base = closures[0]["entries"]
    cases = []

    def add(name, entries):
        cases.append({
            "name": name, "entries": entries,
            "outcome": outcome(lambda: closure_sha256(build_lock(entries))),
        })

    def copy():
        return json.loads(json.dumps(base))

    add("empty", [])
    for closure in closures:
        add(closure["candidate_id"], closure["entries"])
    entries = copy(); entries[1]["role"] = "primary"; add("two primaries", entries)
    entries = copy(); entries[0]["role"] = "dependency"; add("no primary", entries)
    entries = copy(); entries[1]["package_id"] = entries[0]["package_id"]; add("repeated id", entries)
    entries = copy(); entries[1]["load_order"] = 0; add("repeated order", entries)
    entries = copy(); entries[0]["load_order"] = 5; add("unsorted order", entries)
    entries = copy(); entries[1]["load_order"] = 7; add("gapped order", entries)
    entries = copy(); entries[0]["load_order"] = -1; add("negative order", entries)
    entries = copy(); entries[0]["dependencies"][0]["sha256"] = "b" * 64; add("absent dependency", entries)
    entries = copy(); entries[0]["dependencies"].append(dict(entries[0]["dependencies"][0])); add(
        "repeated dependency", entries)
    entries = copy(); entries[0]["capabilities"].append(entries[0]["capabilities"][0]); add(
        "repeated capability", entries)
    entries = copy(); entries[0]["capabilities"].append(" x"); add("padded capability", entries)
    entries = copy(); entries[0]["sha256"] = "A" * 64; add("upper sha", entries)
    entries = copy(); entries[0]["version"] = ""; add("blank version", entries)
    entries = copy(); entries[0]["package_id"] = "\x1fid"; add("separator id", entries)
    entries = copy(); entries[0]["dependencies"][0]["namespace"] = " "; add("blank namespace", entries)
    entries = copy(); entries[0]["dependencies"][0]["kind"] = "k\u3000"; add("padded kind", entries)
    entries = copy(); entries[0]["dependencies"][0]["sha256"] = "z" * 64; add("bad dep sha", entries)
    entries = copy(); entries[0]["dependencies"][0]["version"] = "\t"; add("blank dep version", entries)
    entries = copy(); entries[0]["dependencies"][0]["package_id"] = ""; add("blank dep id", entries)
    for index in range(40):
        entries = []
        count = rng.randint(1, 5)
        for order in range(count):
            entries.append({
                "package_id": f"pkg.{index}.{order}", "version": f"{order}.0",
                "sha256": "".join(rng.choice(HEX) for _ in range(64)),
                "role": "primary" if order == 0 else "dependency",
                "load_order": order * rng.choice((1, 1, 2)),
                "dependencies": [], "capabilities": sorted({f"cap.{rng.randint(0, 9)}" for _ in range(3)}),
            })
        for entry in entries[:-1]:
            target = rng.choice(entries[1:])
            if target is not entry and rng.random() < 0.7:
                entry["dependencies"].append({
                    "package_id": target["package_id"], "version": target["version"],
                    "sha256": target["sha256"], "kind": "rules_reference", "namespace": "legacy"})
        add(f"generated {index}", entries)
    return cases


def record_cases():
    cases = []

    def add(kind, args, call):
        cases.append({"kind": kind, "args": args, "outcome": outcome(call)})

    for args in (
        ["a.json", 0, SHA], ["a.json", -1, SHA], ["a.json", 5, "A" * 64], ["CON", 1, SHA],
        ["../a", 1, SHA], ["a/b", 2**40, "b" * 64],
    ):
        add("package_file", args, lambda: PackageFile(*args) and None)
    for args in (
        ["ns", "kind", "id", "1.0", SHA], [" ", "kind", "id", "1.0", SHA], ["ns", "", "id", "1.0", SHA],
        ["ns", "kind", "id ", "1.0", SHA], ["ns", "kind", "id", "\x1d", SHA], ["ns", "kind", "id", "1", "x"],
        ["", "", "", "", ""],
    ):
        add("exact_dependency", args, lambda: ExactPackageDependency(*args) and None)
    for args in (
        ["id", "1.0", SHA, "kind", "legacy"], ["id", "1.0", SHA, "kind", " "], ["", "1.0", SHA, "kind", "ns"],
        ["id", "1.0", "x", "", "ns"], ["id", "", SHA, "kind", "ns"], ["id", "1", SHA, " k", "ns"],
    ):
        add("package_dependency", args, lambda: PackageDependency(*args) and None)
    for args in (
        ["c", "cand", "act", 0, "lock", "mig"], ["c", "cand", "act", -1, "lock", "mig"],
        [" c", "cand", "act", 0, "lock", "mig"], ["c", "", "act", 0, "lock", "mig"],
        ["c", "cand", "\u2028", 0, "lock", "mig"], ["c", "cand", "act", 3, "", "mig"],
        ["c", "cand", "act", 3, "lock", "mig "], ["campaign-\u00e9", "candidate-1", "a", 12, "lock-1", "m"],
    ):
        add("activation_command", args, lambda: activation_payload_sha256(ActivationCommand(*args)))
    for args in (
        ["p", "character", "pkg", "1.0", {"name": "A"}, None, None, None, "quarantined", None],
        ["p", "character", "pkg", "1.0", {}, SHA, "schema", SHA, "validated", None],
        ["p", "character", "pkg", "1.0", {}, SHA, None, SHA, "validated", None],
        ["p", "character", "pkg", "1.0", {}, None, None, None, "Validated", None],
        ["p", "character", "pkg", "1.0", {}, "x", None, None, "bogus", None],
        ["p", "character", "pkg", "1.0", {}, "x", None, None, "quarantined", None],
        ["p", "character", "pkg", "1.0", {}, None, " s", None, "quarantined", None],
        ["p", "character", "pkg", "1.0", {}, None, None, "B" * 64, "quarantined", None],
        ["p", "character", "pkg", "1.0", {}, None, None, None, "quarantined", ""],
        ["", "character", "pkg", "1.0", {}, None, None, None, "bogus", None],
        ["p", " ", "pkg", "1.0", {}, None, None, None, "quarantined", None],
        ["p", "c", "", "1.0", {}, None, None, None, "quarantined", None],
        ["p", "c", "pkg", "\x1e", {}, None, None, None, "quarantined", None],
    ):
        add("participant", args, lambda: Participant(*args) and None)
    for args in (["ns", "key"], [" ns", "key"], ["ns", ""], ["", ""]):
        add("state_value", args, lambda: StateValue(args[0], args[1], 1) and None)
    entry = PackageLockEntry("p", "1", SHA, PackageRole.PRIMARY, 0, (), ())
    add("package_candidate", [[]], lambda: PackageCandidate(
        "c", "i", CandidateStatus.VALIDATED, SHA, PackageLock(()), {}) and None)
    add("package_candidate", [["p"]], lambda: PackageCandidate(
        "c", "i", CandidateStatus.VALIDATED, SHA, PackageLock((entry,)), {}) and None)
    return cases


def schema_hash_cases():
    schema = {"type": "object", "properties": {"name": {"type": "string"}, "\u00e9": {"const": 1.5}}}
    entity = {"type": "object", "properties": {"hp": {"type": "integer"}}}
    cases = []
    for args in (
        [schema, None, [], None], [schema, None, [{"a": 1}], None], [schema, None, [], {"x": 1}],
        [schema, entity, [], None], [schema, entity, [{"path": "hp", "value": 10}], None],
        [schema, entity, [], {"rule": "x"}], [{}, {}, [], {}],
    ):
        cases.append({"args": args, "outcome": outcome(lambda: participant_schema_sha256(
            args[0], entity_state_schema=args[1], initial_state_calculations=args[2],
            initial_state_validation=args[3]))})
    return cases


def strict_json_texts():
    deep_ok = "[" * 16 + "]" * 16
    texts: list[tuple[bytes, dict]] = []
    none: dict = {}
    for text in (
        '{"a":1}', '{"b":2,"a":1}', ' {"a" : 1 } \n', '[1,2.0,-0,-0.0,1e2,1E-2,0.1]', '"x"', 'null', 'true',
        '{"a":1,"a":2}', '{"a":{"b":1,"b":1}}', '[{"x":1,"y":2,"x":3}]', '{"a":[[{"k":1,"k":1}]]}',
        '{"a":1,"\\u0061":1}', '{"a":1,"A":1}', '{"":1,"":2}', '[{"a":1},{"a":1}]',
        'NaN', '[Infinity]', '{"a":-Infinity}', '[1e400]', '{"a":1,}', '[1,]', "{'a':1}", '', ' ', '{',
        '01', '1.', '.5', '+1', '"\\x41"', '"\t"', '"\\u00e9"', '"\u00e9"', '{"\u00e9":"\u540d"}', '[1] [2]',
        '\ufeff{}', '"\\ud83d\\ude00"', '{"a":1}\x00', '123456789012345678901234567890',
        '"\\ud800"', '{"\\udc00":1}', deep_ok, "[" * 200 + "]" * 200,
        '{"a":true,"b":false,"c":null,"d":[],"e":{}}', '[18446744073709551615,-9223372036854775808]',
        '{"z":1,"y":{"b":1,"a":2}}', '{"a":1,"b":2}', '{"b":1,"a":1}',
    ):
        texts.append((text.encode("utf-8", "surrogatepass"), none))
    texts.append((b"\xff\xfe", none))
    texts.append((b'"\xc3"', none))
    texts.append((b'"\xed\xa0\x80"', none))
    nested = lambda depth: ("[" * (depth - 1) + "1" + "]" * (depth - 1)).encode()
    objects = lambda depth: ('{"a":' * (depth - 1) + "1" + "}" * (depth - 1)).encode()
    for depth in (3, 4, 5):
        for limits in ({"maximum_depth": 4, "maximum_nodes": 100},):
            texts += [(nested(depth), limits), (objects(depth), limits)]
    texts.append((b"[[]]", {"maximum_depth": 1, "maximum_nodes": 10}))
    texts.append((b"[[]]", {"maximum_depth": 2, "maximum_nodes": 10}))
    texts.append((b"[]", {"maximum_depth": 1, "maximum_nodes": 10}))
    texts.append((b"{}", {"maximum_depth": 1, "maximum_nodes": 10}))
    texts.append((b'{"a":[]}', {"maximum_depth": 1, "maximum_nodes": 10}))
    for nodes in (3, 4, 5, 6, 7):
        texts.append((b'{"a":1,"b":[2]}', {"maximum_depth": 8, "maximum_nodes": nodes}))
        texts.append((b"[1,[2,3]]", {"maximum_depth": 8, "maximum_nodes": nodes}))
    for members in (1, 2, 3):
        texts.append((b'{"a":1,"b":{"c":1,"d":2}}', {
            "maximum_depth": 8, "maximum_nodes": 100, "maximum_object_members": members}))
    texts.append((b"{}", {"maximum_depth": 4}))
    texts.append((b"{}", {"maximum_nodes": 4}))
    texts.append((b"{}", {"maximum_object_members": 4}))
    texts.append((b"{}", {"maximum_depth": 4, "maximum_object_members": 4}))
    texts.append((b'{"a":1,"a":1}', {"maximum_depth": 4, "maximum_nodes": 100}))
    texts.append(('{"x":"\u2028","y":1.5,"z":[true]}'.encode(), none))
    for _ in range(60):
        texts.append((random_json(rng).encode("utf-8"), none))
    return texts


def random_json(rng: random.Random, depth: int = 0) -> str:
    choice = rng.random()
    if depth > 3 or choice < 0.4:
        return rng.choice(['1', '-2', '3.25', '"s"', '"\u00e9\\n"', 'null', 'false', '1e5', '-0', '[]', '{}'])
    if choice < 0.7:
        return "[" + ",".join(random_json(rng, depth + 1) for _ in range(rng.randint(0, 3))) + "]"
    keys = [rng.choice("abcd\u00e9") for _ in range(rng.randint(0, 4))]
    return "{" + ",".join(f'"{key}":{random_json(rng, depth + 1)}' for key in keys) + "}"


def declared_difference(value, depth=1):
    """The docs/rust-port/differences.md row under which Rust refuses what Python accepts."""
    if depth > 127:
        return 4
    if isinstance(value, bool) or value is None or isinstance(value, float):
        return None
    if isinstance(value, int):
        return 1 if not -2**63 <= value < 2**64 else None
    if isinstance(value, str):
        return 3 if any(0xD800 <= ord(c) <= 0xDFFF for c in value) else None
    children = list(value.items()) if isinstance(value, dict) else [(None, item) for item in value]
    for key, item in children:
        found = (declared_difference(key, depth) if key is not None else None) or declared_difference(
            item, depth + 1)
        if found is not None:
            return found
    return None


def strict_json_cases():
    cases = []
    for raw, limits in strict_json_texts():
        result = {"hex": raw.hex(), "limits": limits}
        for name, parse in (("content", parse_content_json_bytes), ("canonical", parse_canonical_json_bytes)):
            try:
                value = parse(raw, label="Label", **limits)
                result[name] = ["ok", json.dumps(thaw_json_value(value))]
                difference = declared_difference(thaw_json_value(value))
                if difference is not None:
                    result["difference"] = difference
            except (ValueError, TypeError) as error:
                result[name] = [type(error).__name__, str(error)]
        cases.append(result)
    return cases


def channel_cases():
    names = [
        "reasoning", "private_reasoning", "rawReasoning", "ReasoningProvider", "thought", "hiddenThought",
        "internal_monologue", "monologue", "scratchpad", "Scratch_Pad", "modelResponse", "model_output_raw",
        "chainOfThought", "thought_chain", "diagnostics", "DIAGNOSTIC", "traceback", "stack_trace",
        "trace_stack", "trace", "inspectorTrace", "private-trace", "stack", "exception", "name", "hp",
        "rea\u017foning_private", "REASONING_PR\u0130VATE", "\ufb01nal", "private\u017fcratchpad", "\u017fcratchpad", "\uff32\uff25\uff21\uff33\uff2f\uff2e\uff29\uff2e\uff27_raw",
        "Stra\u00dfe", "diagno\u017ftic", "\u212aelvin", "tra\u0130ce_private", "scratch\u00adpad", "chain_of_thought",
        "thoughtchain", "raw", "provider_id", "model_response", "outputModel_raw", "", "_", "a1B2",
    ]
    cases = [{"name": name, "reserved": _is_reserved_canonical_channel(name)} for name in names]
    payloads = [
        {"a": 1}, {"a": {"reasoning_raw": 1}}, {"list": [{"x": [{"traceback": None}]}]}, [], [1, "trace"],
        {"value": "scratchpad"}, {"nested": {"ok": {"fine": [1, 2.5, None, True]}}},
    ]
    safety = [{"payload": payload, "safe": canonical_payload_is_safe(payload)} for payload in payloads]
    return cases, safety


def mechanic_outcome_cases():
    def option(option_id, label="Label"):
        return {"option_id": option_id, "label": label}

    two = [option("a"), option("b")]
    shapes = [
        {"result": {"status": "ok"}},
        {"result": {"status": 1}},
        {"result": {"status": None}},
        {"result": {"mechanics_choice_answers": []}},
        {"result": {"reasoning_raw": 1}},
        {"result": {}, "choice": {"choice_id": "c", "prompt": "Pick", "options": two}},
        {"result": {}, "choice": {"choice_id": "c", "prompt": "Pick", "options": two[:1]}},
        {"result": {}, "choice": {"choice_id": "c", "prompt": "Pick",
                                   "options": [option(f"o{i}") for i in range(17)]}},
        {"result": {}, "choice": {"choice_id": "c", "prompt": "\u00e9" * 2049, "options": two}},
        {"result": {}, "choice": {"choice_id": "c", "prompt": "\u00e9" * 2048, "options": two}},
        {"result": {}, "choice": {"choice_id": " c", "prompt": "Pick", "options": two}},
        {"result": {}, "choice": {"choice_id": "c", "prompt": "Pick", "options": [option("a"), option("a")]}},
        {"result": {}, "choice": {"choice_id": "c", "prompt": "Pick", "options": [option("a"), option("b", "")]}},
        {"result": {}, "choice": {"choice_id": "c", "prompt": "Pick",
                                   "options": [option("a"), option("b", "x" * 1025)]}},
        {"result": {}, "choice": {"choice_id": "c", "prompt": "Pick", "options": [option("a"), option("\t")]}},
        {"result": {}, "choice": {"choice_id": "c", "prompt": "Pick", "options": two},
         "mutations": [{"namespace": "n", "key": "k", "value": 1}]},
        {"result": {}, "mutations": [{"namespace": "n", "key": "k", "value": {"a": [1]}}]},
        {"result": {}, "mutations": [{"namespace": "privateReasoning", "key": "k", "value": 1}]},
        {"result": {}, "mutations": [{"namespace": "n", "key": "stacktrace", "value": 1}]},
        {"result": {}, "mutations": [{"namespace": "n", "key": "k", "value": {"diagnostic": 1}}]},
        {"result": {}, "consequences": [{"kind": "damage"}]},
        {"result": {}, "consequences": [{"kind": "participant_updated"}]},
        {"result": {}, "consequences": [{"kind": ""}]},
        {"result": {}, "consequences": [{"kind": 3}]},
        {"result": {}, "consequences": [{"kind": None}, {}]},
        {"result": {}, "consequences": [{"scratchpad": 1}]},
        {"result": {}, "participant_mutations": [{"participant_id": "p", "data": {"hp": 1}}]},
        {"result": {}, "participant_mutations": [{"participant_id": "", "data": {}}]},
        {"result": {}, "participant_mutations": [{"participant_id": "p", "data": {}},
                                                 {"participant_id": "p", "data": {}}]},
        {"result": {}, "participant_mutations": [{"participant_id": "p", "data": {"traceback": 1}}]},
    ]
    cases = []
    for shape in shapes:
        choice = shape.get("choice")
        value = MechanicOutcome(
            result=shape["result"],
            mutations=tuple(StateMutation(**item) for item in shape.get("mutations", [])),
            consequences=tuple(shape.get("consequences", [])),
            choice=None if choice is None else MechanicsChoiceRequest(
                choice_id=choice["choice_id"], prompt=choice["prompt"],
                options=tuple(MechanicsChoiceOption(**item) for item in choice["options"])),
            participant_mutations=tuple(
                ParticipantMutation(**item) for item in shape.get("participant_mutations", [])),
        )
        cases.append({"outcome": shape, "safe": mechanic_outcome_is_safe(value)})
    return cases


def response_block_cases():
    blocks = [
        [{"kind": "narration", "text": "The door opens."}],
        [{"kind": "npc_dialogue", "speaker_label": "Mara", "text": "Hello."},
         {"kind": "ooc", "text": "Roll initiative."}, {"kind": "narration", "text": "Wind."}],
        [], "text", [1], [{"kind": "narration", "text": " "}], [{"kind": "narration", "text": "\x1c"}],
        [{"kind": "narration", "text": "a", "extra": 1}], [{"kind": "npc_dialogue", "text": "a"}],
        [{"kind": "npc_dialogue", "speaker_label": " ", "text": "a"}],
        [{"kind": "npc_dialogue", "speaker_label": 3, "text": "a"}],
        [{"kind": "ooc", "speaker_label": "x", "text": "a"}], [{"kind": "aside", "text": "a"}],
        [{"kind": 1, "text": "a"}], [{"kind": "narration", "text": 2}], {"kind": "narration", "text": "a"},
    ]
    return [{"blocks": value, "outcome": outcome(lambda: response_blocks_text(value)),
             "validated": outcome(lambda: [dict(item) for item in validate_response_blocks(value)])}
            for value in blocks]


def text_cases():
    pool = [chr(c) for c in (
        *range(0x20, 0x7f), 0xdf, 0x130, 0x131, 0x149, 0x17f, 0x1c5, 0x1f0, 0x345, 0x390, 0x3c2, 0x1e9e,
        0x212a, 0x2126, 0xfb00, 0xfb01, 0xfb06, 0x10400, 0x1e921, 0xab70, 0x13a0, 0x1c80, 0x2c2f,
        0x1f88, 0x1fb3, 0xa7c0, 0x10570, 0x1e030, *(ord(s) for s in SPACES), 0x200b, 0xfeff,
    )]
    cases = []
    for _ in range(400):
        text = "".join(rng.choice(pool) for _ in range(rng.randint(0, 12)))
        cases.append({"text": text, "strip": text.strip(), "casefold": text.casefold(),
                      "upper": text.upper(), "len": len(text)})
    return cases


def main() -> None:
    closures, records = recorded_closures()
    channels, safety = channel_cases()
    vectors = {
        "python": sys.version.split()[0],
        "identities": identity_cases(),
        "paths": path_cases(),
        "closures": closures,
        "records": records,
        "locks": lock_cases(closures),
        "constructors": record_cases(),
        "participant_schema_sha256": schema_hash_cases(),
        "strict_json": strict_json_cases(),
        "channels": channels,
        "payload_safety": safety,
        "mechanic_outcomes": mechanic_outcome_cases(),
        "response_blocks": response_block_cases(),
        "text": text_cases(),
    }
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
