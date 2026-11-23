"""Generate vectors for sylvia-app::package_mechanic (`materialize_package_mechanic` and the
mechanic it returns) and sylvia-app::result_cards from the frozen Python.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_package_mechanic_vectors.py

(`.venv/bin/python` on Linux.) Deterministic and checked in. Reference tooling for the port,
retired with the Python source.

Each case is a runtime, a declaration and a `MechanicContext`; the record is what
`materialize_package_mechanic(...)` raises or what its mechanic returns or raises:

- `{"outcome": ...}`: the `MechanicOutcome`, or its SHA-256 (`outcome_sha256`) when its
  canonical JSON passes 4 KiB;
- `{"rejected": [message, refusal_reasons, field_paths]}`: a `RulesetPreconditionRejected`;
- `{"failed": [class, str(error), str(error.__cause__) or None]}`: any other exception;
- `{"pending": effect}`: the run reached a spatial effect (`spatial_query`, `spatial_mutation`,
  `shot_effect`, `aoe`), which the port refuses by name until its spatial effect handlers land.
  The generator replaces those handlers with one that raises a `BaseException`, so no Python
  catch-all turns it into a gameplay failure, exactly as the port's `PortPending` passes through.

Python's `secrets.SystemRandom().shuffle` is replaced by keeping the order, and the Rust test runs
with `with_identity_shuffle_for_vectors`, so shuffled state compares exactly.

Two sources of contexts:

- **Targeted cases** on the conformance fixture with added tools: every refusal and
  publication rule, choices, state-bound parameters, nested calls, procedures, logs, result
  cards and activity labels.
- **Recorded contexts** from Python's own tests (`--record`): this file runs as a pytest plugin
  over SRD Lite, Riichi and runtime tests and keeps the contexts their mechanics received; the
  default run replays the kept contexts, so regenerating does not rerun pytest.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import os
import secrets
import subprocess
import sys
import zipfile
from collections.abc import Mapping
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUTPUT = HERE / "package_mechanic_vectors.json"
RECORDING_ENV = "SYLVIA_MECHANIC_RECORDING"
FIXTURE = "tests/fixtures/rulesets/signal-and-static"
SRD = "rulesets/srd-lite"
RIICHI = "rulesets/riichi-mahjong/riichi-mahjong-0.1.1.sylvia-gm-package"
# The effects the port refuses by name until its C3b.
SPATIAL = ("shot_effect", "aoe")
INLINE_LIMIT = 4096
RECORDED_TESTS = [
    "tests/test_srd_lite_foundation.py", "tests/test_srd_action_budget.py",
    "tests/test_alpha_04_ruleset_neutral_package.py", "tests/test_nested_runtime_bindings.py",
    "tests/test_result_cards.py", "tests/test_package_runtime_loading.py", "tests/test_player_actions.py",
    "tests/test_srd_superior_defense.py", "tests/test_srd_self_restoration.py",
    "tests/test_srd_disciplined_survivor.py", "tests/test_launch_lite_safety.py",
]
PER_TOOL = 4

os.environ.pop("SYLVIA_RUNTIME_CACHE_DIR", None)


# ── Recording (pytest plugin) ─────────────────────────────────────────────────────────────


def _thaw(value):
    from sylvia_gm.domain.authority import thaw_json_value

    return thaw_json_value(value)


def _request_json(request) -> dict:
    return {"choice_id": request.choice_id, "prompt": request.prompt,
            "options": [[option.option_id, option.label] for option in request.options]}


def context_json(context) -> dict:
    resolution = context.choice_resolution
    return {
        "campaign_id": context.campaign_id, "turn_id": context.turn_id, "action_id": context.action_id,
        "state": _thaw(context.state), "parameters": _thaw(context.parameters),
        "participants": _thaw(context.participants),
        "choice_resolution": None if resolution is None else {
            "choice_id": resolution.choice_id, "option_id": resolution.option_id,
            "expected_request": _request_json(resolution.expected_request),
            "prior_answers": [{"resolution_id": answer.resolution_id, "request": _request_json(answer.request),
                               "option_id": answer.option_id, "session_id": answer.session_id}
                              for answer in resolution.prior_answers],
        },
    }


def declaration_json(declaration) -> dict:
    return {"name": declaration.name, "capability": declaration.capability,
            "input_schema": _thaw(declaration.input_schema),
            "turn_statuses": list(declaration.turn_statuses), "writers": list(declaration.writers)}


def pytest_configure(config):  # noqa: ARG001 - pytest hook
    """Keep every context a package mechanic receives while Python's tests run."""
    import sylvia_gm.application.authority as authority
    import sylvia_gm.application.package_runtime as pr

    path = os.environ[RECORDING_ENV]
    original = pr.materialize_package_mechanic
    counts: dict[tuple, int] = {}

    def recording(**arguments):
        mechanic = original(**arguments)
        runtime = arguments["runtime_bytes"]
        digest = hashlib.sha256(runtime).hexdigest()
        key = (arguments["package_id"], arguments["declaration"].name, digest)

        def execute(context):
            # Only a bounded sample per tool, and large runtimes by their hash alone.
            counts[key] = counts.get(key, 0) + 1
            if counts[key] <= 3 * PER_TOOL:
                with open(path, "a", encoding="utf-8") as sink:
                    sink.write(json.dumps({
                        "package_id": arguments["package_id"], "package_version": arguments["package_version"],
                        "package_sha256": arguments["package_sha256"],
                        "declaration": declaration_json(arguments["declaration"]), "runtime_sha256": digest,
                        "runtime_hex": runtime.hex() if len(runtime) <= 64 * 1024 else None,
                        "context": context_json(context),
                    }, ensure_ascii=True, allow_nan=False) + "\n")
            return mechanic(context)

        return execute

    pr.materialize_package_mechanic = recording
    authority.materialize_package_mechanic = recording


def record() -> list[dict]:
    """Run Python's tests with this file as a plugin and keep up to `PER_TOOL` distinct contexts
    per runtime tool."""
    sink = HERE / "package_mechanic_recording.jsonl"
    sink.unlink(missing_ok=True)
    basetemp = ROOT / ".mechanic-recording-tmp"
    environment = {**os.environ, RECORDING_ENV: str(sink), "PYTHONPATH": str(HERE)}
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "generate_package_mechanic_vectors",
                    "-p", "no:cacheprovider", f"--basetemp={basetemp}", *RECORDED_TESTS],
                   cwd=ROOT, env=environment, check=False)
    known = known_runtimes()
    kept: dict[str, list[dict]] = {}
    seen = set()
    for line in sink.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        digest, runtime_hex = item.pop("runtime_sha256"), item.pop("runtime_hex")
        if digest in known:
            item["runtime"] = known[digest]
        elif runtime_hex is not None:
            item["runtime"] = stored(bytes.fromhex(runtime_hex))
        else:
            continue
        identity = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
        if identity in seen:
            continue
        seen.add(identity)
        group = kept.setdefault(f"{item['package_id']} {item['declaration']['name']} {digest}", [])
        if len(group) < PER_TOOL:
            group.append(item)
    sink.unlink(missing_ok=True)
    import shutil

    shutil.rmtree(basetemp, ignore_errors=True)
    return [item for key in sorted(kept) for item in kept[key]]


def known_runtimes() -> dict[str, dict]:
    srd = (ROOT / SRD / "runtime/ruleset.yaml").read_bytes()
    archive = zipfile.ZipFile(ROOT / RIICHI)
    riichi_id = json.loads(archive.read("manifest.json"))["packages"][0]["id"]
    riichi = archive.read(f"packages/{riichi_id}/runtime/ruleset.yaml")
    fixture = (ROOT / FIXTURE / "runtime/ruleset.yaml").read_bytes()
    return {hashlib.sha256(srd).hexdigest(): {"source": f"{SRD}/runtime/ruleset.yaml"},
            hashlib.sha256(riichi).hexdigest(): {"riichi": True},
            hashlib.sha256(fixture).hexdigest(): {"source": f"{FIXTURE}/runtime/ruleset.yaml"}}


# ── Running one case ──────────────────────────────────────────────────────────────────────


class PortPending(BaseException):
    """A spatial effect reached; no `except Exception` in the runtime can catch it."""


def install_patches() -> None:
    import jsonschema._keywords

    import sylvia_gm.application.package_runtime as pr
    import sylvia_gm.runtime._effect_executor as effect_executor

    jsonschema._keywords.set = lambda iterable: list(dict.fromkeys(iterable))  # row 6

    class _SortedSet(set):
        def __iter__(self):
            items = list(super().__iter__())
            try:
                return iter(sorted(items))
            except TypeError:
                return iter(items)

    pr.set = _SortedSet

    class _InOrder:
        def shuffle(self, values):
            return None

    effect_executor.secrets = type("secrets", (), {"SystemRandom": _InOrder, "token_hex": secrets.token_hex})

    def pending(name):
        def handler(*_args, **_kwargs):
            raise PortPending(name)

        return handler

    for name in SPATIAL:
        effect_executor._EFFECT_HANDLERS[name] = pending(name)


DOCUMENTS: dict[str, str] = {}


def stored(data: bytes) -> dict:
    """A runtime kept once in the vector file's `documents` table, by its SHA-256."""
    digest = hashlib.sha256(data).hexdigest()
    DOCUMENTS[digest] = data.hex()
    return {"document": digest}


def runtime_bytes(reference: dict) -> bytes:
    if "document" in reference:
        return bytes.fromhex(DOCUMENTS[reference["document"]])
    if "hex" in reference:
        return bytes.fromhex(reference["hex"])
    if "riichi" in reference:
        archive = zipfile.ZipFile(ROOT / RIICHI)
        riichi_id = json.loads(archive.read("manifest.json"))["packages"][0]["id"]
        return archive.read(f"packages/{riichi_id}/runtime/ruleset.yaml")
    return (ROOT / reference["source"]).read_bytes()


def request_of(value: dict):
    from sylvia_gm.domain.authority import MechanicsChoiceOption, MechanicsChoiceRequest

    return MechanicsChoiceRequest(value["choice_id"], value["prompt"],
                                  tuple(MechanicsChoiceOption(option, label) for option, label in value["options"]))


def context_of(value: dict):
    from sylvia_gm.domain.authority import (
        MechanicContext,
        MechanicsChoiceAnswer,
        MechanicsChoiceResolution,
        owned_payload,
    )

    resolution = value["choice_resolution"]
    return MechanicContext(
        campaign_id=value["campaign_id"], turn_id=value["turn_id"], action_id=value["action_id"],
        state=owned_payload(value["state"]), parameters=owned_payload(value["parameters"]),
        participants=owned_payload(value["participants"]),
        choice_resolution=None if resolution is None else MechanicsChoiceResolution(
            choice_id=resolution["choice_id"], option_id=resolution["option_id"],
            expected_request=request_of(resolution["expected_request"]),
            prior_answers=tuple(MechanicsChoiceAnswer(answer["resolution_id"], request_of(answer["request"]),
                                                      answer["option_id"], answer["session_id"])
                                for answer in resolution["prior_answers"])),
    )


def declaration_of(value: dict):
    from sylvia_gm.domain.authority import owned_payload
    from sylvia_gm.domain.package_library import ToolDeclaration

    return ToolDeclaration(name=value["name"], capability=value["capability"],
                           input_schema=owned_payload(value["input_schema"]), state_predicates=(),
                           turn_statuses=tuple(value["turn_statuses"]), writers=tuple(value["writers"]))


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def outcome_json(outcome) -> dict:
    return {
        "result": _thaw(outcome.result),
        "mutations": [[m.namespace, m.key, _thaw(m.value)] for m in outcome.mutations],
        "participant_mutations": [[m.participant_id, _thaw(m.data)] for m in outcome.participant_mutations],
        "consequences": [_thaw(item) for item in outcome.consequences],
        "choice": None if outcome.choice is None else _request_json(outcome.choice),
    }


def run(case: dict) -> dict:
    """Materialize and execute one case as `AuthorityOwner` does."""
    import sylvia_gm.application.package_runtime as pr
    from sylvia_gm.domain.errors import RulesetPreconditionRejected

    data = runtime_bytes(case["runtime"])
    try:
        mechanic = pr.materialize_package_mechanic(
            package_id=case["package_id"], package_version=case["package_version"],
            package_sha256=case["package_sha256"], declaration=declaration_of(case["declaration"]),
            runtime_bytes=data)
    except Exception as error:  # noqa: BLE001 - every exception class is behaviour here
        return {"materialize": [type(error).__name__, str(error)]}
    try:
        outcome = mechanic(context_of(case["context"]))
    except PortPending as pending:
        return {"pending": pending.args[0]}
    except RulesetPreconditionRejected as error:
        return {"rejected": [str(error), list(error.refusal_reasons), list(error.field_paths)]}
    except Exception as error:  # noqa: BLE001
        cause = error.__cause__
        return {"failed": [type(error).__name__, str(error), None if cause is None else str(cause)]}
    record = outcome_json(outcome)
    encoded = canonical(record)
    if len(encoded) > INLINE_LIMIT:
        return {"outcome_sha256": hashlib.sha256(encoded).hexdigest(), "status": record["result"].get("status")}
    return {"outcome": record}


# ── Targeted cases ────────────────────────────────────────────────────────────────────────


def fixture_base() -> tuple[dict, dict]:
    import yaml

    record = json.loads((ROOT / FIXTURE / "package-record.json").read_text(encoding="utf-8"))
    data = yaml.safe_load((ROOT / FIXTURE / "runtime/ruleset.yaml").read_text(encoding="utf-8"))
    return record, data


def declaration_for(data: dict, name: str, package_id: str) -> dict:
    """The declaration `_validate_runtime_declaration` accepts: the runtime's own schema."""
    import yaml

    import sylvia_gm.application.package_runtime as pr

    runtime = pr._load_runtime(package_id=package_id, runtime_bytes=yaml.safe_dump(data).encode(), authoring=False)
    return {"name": name, "capability": "signal-static.resolve",
            "input_schema": pr._runtime_parameter_schema(runtime.tools[name]),
            "turn_statuses": ["open"], "writers": ["dm"]}


CHOICE_OPTIONS = [{"id": "attack", "label": "Attack {target.name}"}, {"id": "flee", "label": "Flee"}]
REQUEST = {"choice_id": "c1", "prompt": "Choose for Orc", "options": [["attack", "Attack Orc"], ["flee", "Flee"]]}
TARGET = {"name": "target", "type": "string", "resolve": "entity"}
ACTOR = {"name": "actor", "type": "string", "resolve": "participant", "required": False}
AMOUNT = {"name": "amount", "type": "integer", "default": 2}

CARDS = [
    {"version": 1, "title": "Rolled {x}"},
    {"version": 1, "title": {"path": "context._title"}},
    {"version": 1, "title": {"path": "parameters.target"}, "subtitle": "Sub"},
    {"version": 1, "title": {"path": "context._missing"}},
    {"version": 1, "summary": [{"label": "Roll", "value": {"path": "context._roll"}, "emphasis": True},
                               {"label": "Zero", "value": {"path": "context._zero"}, "omit_zero": True},
                               {"label": "Gone", "value": {"path": "context._missing"}, "optional": True},
                               {"label": "Null", "value": {"path": "context._none"}, "optional": True},
                               {"value": "literal"}]},
    {"version": 1, "summary": [{"label": "Hit", "value": {"path": "context._hit", "labels": {"true": "Hit", "false": "Miss"}}}]},
    {"version": 1, "summary": [{"label": "Kind", "value": {"path": "context._kind", "labels": {"a": "Alpha"}}}]},
    {"version": 1, "summary": [{"label": "Num", "value": {"path": "context._roll", "labels": {"7": "Seven", "7.0": "x"}}}]},
    {"version": 1, "summary": [{"label": "Float", "value": {"path": "context._half", "labels": {"0.5": "Half"}}}]},
    {"version": 1, "summary": [{"label": "None", "value": {"path": "context._none", "labels": {"None": "Nothing"}}}]},
    {"version": 1, "summary": [{"label": "List", "value": {"path": "context._rolls"}}]},
    {"version": 1, "summary": {"path": "context._rows"}},
    {"version": 1, "summary": {"path": "context._rows_ref"}},
    {"version": 1, "summary": {"path": "context._rows", "x": 1}},
    {"version": 1, "summary": [{"label": "Roll", "value": {"path": "context._roll"},
                                "equation": {"die_sides": 20, "roll": {"path": "context._d"}, "modifier": 3,
                                             "total": {"path": "context._roll"}}}]},
    {"version": 1, "equation": {"die_sides": 6, "roll": {"path": "context._d"}, "modifier": 0, "total": {"path": "context._roll"},
                                "rolls": {"path": "context._rolls"}}, "title": "Eq"},
    {"version": 1, "equation": {"die_sides": 6, "roll": {"path": "context._none"}, "modifier": 0, "total": 1, "optional": True},
     "title": "Opt"},
    {"version": 1, "equation": {"die_sides": 6, "roll": 1, "modifier": 0, "optional": True}, "title": "Missing total"},
    {"version": 1, "equation": {"die_sides": 6, "roll": 1, "modifier": 0}, "title": "Missing total strict"},
    {"version": 1, "equation": {"die_sides": 1, "roll": 1, "modifier": 0, "total": 1}, "title": "Bad die"},
    {"version": 1, "equation": {"die_sides": 6, "roll": 1, "modifier": 0, "total": 1, "rolls": [1, 7]}, "title": "Bad pool"},
    {"version": 1, "equations": [{"label": "A", "value": 1, "equation": {"die_sides": 4, "roll": 2, "modifier": 1, "total": 3}}]},
    {"version": 1, "equations": [{"label": "A", "value": 1}]},
    {"version": 1, "title": "Footprint", "footprint": "compact", "details_label": "More",
     "details": [{"label": "D", "value": {"path": "context._title"}}]},
    {"version": 1, "title": "Footprint", "footprint": "huge"},
    {"version": 1, "actions": [{"label": "Again", "tool": "resolve_intrusion",
                                "parameters": {"skill": "intrusion", "pressure": {"path": "context._roll"}}}]},
    {"version": 1, "actions": [{"label": "Bad", "tool": "bad-tool", "parameters": {}}]},
    {"version": 1, "actions": [{"label": "Bad", "tool": "ok", "parameters": {}, "x": 1}]},
    {"version": 2, "title": "Version"}, {"version": True, "title": "Bool version"}, {"version": 1, "title": "T", "extra": 1},
    {"version": 1}, {"version": 1, "summary": []}, "not a card",
    {"version": 1, "title": "x" * 201}, {"version": 1, "title": "   "}, {"version": 1, "title": {"path": "context._long"}},
    {"version": 1, "title": {"path": "context"}}, {"version": 1, "title": {"path": "secret.x"}},
    {"version": 1, "title": {"path": "context._title.deeper"}}, {"version": 1, "title": {"path": "context..x"}},
    {"version": 1, "title": {"path": ".".join(["context"] * 17)}},
    {"version": 1, "summary": [{"label": "Internal", "value": "x"}], "title": "rawReasoning"},
    {"version": 1, "summary": [{"label": "Row", "value": 1, "emphasis": "yes"}]},
    {"version": 1, "summary": [{"label": "Row", "value": 1, "weird": True}]},
    {"version": 1, "summary": [{"label": "Row", "value": {"path": "context._missing"}}]},
    {"version": 1, "summary": [{"label": "Row", "value": {"path": "context._rows"}}]},
    {"version": 1, "summary": [{"label": "Row", "value": {"path": "context._nested"}}]},
    {"version": 1, "summary": [{"label": "Row", "value": [1, 2, {"a": 1}]}]},
    {"version": 1, "summary": [{"label": "Row", "value": "y" * 4001}]},
    # Character, not byte, bounds; Python's float repr for labels; the path depth bound; a
    # details-only card; an equation-only card (dropped); a private channel in action parameters.
    {"version": 1, "title": "\u00e9" * 150},
    {"version": 1, "summary": [{"label": "Big", "value": {"path": "context._big", "labels": {"1e+16": "Huge"}}},
                               {"label": "Tiny", "value": {"path": "context._tiny", "labels": {"1e-07": "Tiny"}}}]},
    {"version": 1, "title": {"path": "context._deep" + ".a" * 15}},
    {"version": 1, "title": {"path": "context._deep16" + ".a" * 14}},
    {"version": 1, "details": [{"label": "D", "value": 1}]},
    {"version": 1, "equation": {"die_sides": 4, "roll": 2, "modifier": 1, "total": 3}},
    {"version": 1, "title": "T", "actions": [{"label": "Go", "tool": "go", "parameters": {"private_reasoning": 1}}]},
]
LABELS = [None, "Quick move", {"path": "context._title"}, "x" * 81, {"path": "context._rolls"}, "   ", 5, "\u00e9" * 60]
LABEL_CARDS = [{"version": 1, "title": "Only a title"}, {"version": 1, "title": "T", "summary": [{"value": 1}]},
               {"version": 1, "title": "T", "subtitle": "S"},
               {"version": 1, "title": "T", "equation": {"die_sides": 4, "roll": 2, "modifier": 1, "total": 3}}, None]


def nested(depth: int, leaf: str) -> object:
    value: object = leaf
    for _ in range(depth):
        value = {"a": value}
    return value


def scene_vars() -> list:
    return [{"set": {"field": "_title", "value": "'Strike'"}}, {"set": {"field": "_roll", "value": 7}},
            {"set": {"field": "_zero", "value": 0}}, {"set": {"field": "_none", "value": None}},
            {"set": {"field": "_hit", "value": True}}, {"set": {"field": "_kind", "value": "'a'"}},
            {"set": {"field": "_half", "value": 0.5}}, {"set": {"field": "_d", "value": 4}},
            {"set": {"field": "_rolls", "value": [1, 3]}}, {"set": {"field": "_long", "value": "'" + "z" * 300 + "'"}},
            {"set": {"field": "_nested", "value": {"a": 1}}}, {"set": {"field": "_big", "value": 1e16}},
            {"set": {"field": "_tiny", "value": 1e-7}}, {"set": {"field": "_deep", "value": nested(15, "'Deep'")}},
            {"set": {"field": "_deep16", "value": nested(14, "'Deep'")}},
            {"set": {"field": "_rows", "value": [{"label": "R", "value": 1}, {"label": "S", "value": 2, "emphasis": True}]}},
            {"set": {"field": "_rows_ref", "value": [{"label": "R", "value": {"path": "context._roll"}}]}}]


def targeted() -> list[dict]:
    sys.path.insert(0, str(ROOT / "crates/sylvia-engine/tests/vectors"))
    from generate_spatial_scene_vectors import QUERIES, QUERY_SCENE

    record, base = fixture_base()
    package_id = record["id"]
    cases: list[dict] = []

    def document(name: str, data: dict) -> dict:
        import yaml

        return stored(yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode("utf-8"))

    # Synthetic tools run in a minimal runtime: the descriptor, the fixture's decision boundary and
    # the tools a case needs.
    minimal = {"sylvia_gm_runtime": base["sylvia_gm_runtime"], "model_decision_boundary": base["model_decision_boundary"],
               "tools": {}}

    def tool_doc(tools: dict, procedures=None, boundary=None, data=None) -> dict:
        edited = copy.deepcopy(data or minimal)
        edited["tools"].update(copy.deepcopy(tools))
        if procedures is not None:
            edited["procedures"] = procedures
        if boundary is not None:
            edited["model_decision_boundary"] = boundary
        return edited

    state = {
        f"{package_id}.state": {"counter": 1, "items": ["a", "b", "c"], "mode": "duel", "active_actor": "orc",
                                "entities": ["orc"], "resources": {"charge": 3}, "stress": 0,
                                "scene": copy.deepcopy(QUERY_SCENE), "plain": {"map": 1}},
        f"{package_id}.entities": {
            "orc": {"hp": 5, "max_hp": 9, "name": "Orc", "conditions": ["prone"], "id": "orc"},
            "elf": {"hp": 7, "max_hp": 7, "name": "Elf", "conditions": []},
        },
        f"{package_id}.combat": {"round": 2, "turn_index": 0, "leader": "orc"},
    }
    participants = {"pc": {"package_id": package_id, "data": {"hp": 3, "name": "Pc"}},
                    "guest": {"package_id": "other.pkg", "data": {"hp": 4}}}

    def context(parameters=None, *, choice=None, state_value=None, participants_value=None, ids=("c1", "t1", "a1")):
        return {"campaign_id": ids[0], "turn_id": ids[1], "action_id": ids[2],
                "state": state if state_value is None else state_value, "parameters": parameters or {},
                "participants": participants if participants_value is None else participants_value,
                "choice_resolution": choice}

    def case(label, data, name, parameters=None, *, package=package_id, declaration=None, **options):
        try:
            declared = declaration or declaration_for(data, name, package_id)
        except Exception as error:  # noqa: BLE001 - a document the execution load refuses
            raise SystemExit(f"{label}: the targeted document does not load: {type(error).__name__}: {error}") from error
        item = {"name": label, "package_id": package, "package_version": "0.1.0", "package_sha256": "ab" * 32,
                "declaration": declared, "runtime": document(label, data),
                "context": context(parameters, **options)}
        cases.append({**item, **run(item)})

    # The fixture's own tools, and the seed basis: each identity changes the rolls.
    for name, parameters in (("resolve_intrusion", {"skill": "intrusion", "pressure": 2}),
                             ("apply_operation_cost", {"stress": 1, "wounds": 0, "charge_spent": 1, "progress": 2}),
                             ("run_program", {"program": "signal_boost", "amount": 1})):
        case(f"fixture {name}", base, name, parameters)
    for ids in (("c2", "t1", "a1"), ("c1", "t2", "a1"), ("c1", "t1", "a2")):
        case(f"seed {ids}", base, "resolve_intrusion", {"skill": "intrusion", "pressure": 2}, ids=ids)
    case("seed parameters", base, "resolve_intrusion", {"skill": "intrusion", "pressure": 3})
    case("archived Full", base, "resolve_intrusion", {"skill": "intrusion", "pressure": 2}, package="wotc.srd.5.2.1")
    mismatch = declaration_for(base, "resolve_intrusion", package_id)
    case("declaration mismatch", base, "resolve_intrusion", {},
         declaration={**mismatch, "input_schema": {"type": "object", "properties": {}}})
    case("parameter validation", base, "resolve_intrusion", {"skill": "intrusion"})
    case("bad state root", base, "run_program", {"program": "signal_boost", "amount": 1},
         state_value={**state, f"{package_id}.state": 5})
    case("bad participants", base, "run_program", {"program": "signal_boost", "amount": 1},
         participants_value={"pc": 5})

    def synthetic(name, effects, *, parameters=(TARGET, ACTOR, AMOUNT), pre=None, metadata=None, extra=None,
                  procedures=None, boundary=None):
        tool = {"description": f"Synthetic {name}.", "parameters": [dict(p) for p in parameters], "effects": effects}
        if pre is not None:
            tool["pre_conditions"] = pre
        if metadata is not None:
            tool["metadata"] = metadata
        return tool_doc({name: tool, **(extra or {})}, procedures=procedures, boundary=boundary)

    target = {"target": "orc", "actor": "pc"}
    # Refusals.
    case("precondition", synthetic("gate", [{"set": {"field": "_x", "value": 1}}],
                                   pre=[{"condition": "target.hp > 10", "error": "{target.name} is too weak"},
                                        {"condition": "target.hp > 20", "error": "Missing input 'amount'"}]), "gate", target)
    case("precondition unsafe", synthetic("gate", [], pre=[{"condition": "false", "error": "bad\nreason"}]), "gate", target)
    case("precondition empty", synthetic("gate", [], pre=[{"condition": "false", "error": ""}]), "gate", target)
    case("require", synthetic("gate", [{"require": {"condition": "target.hp > 10", "error": "Too weak"}}]), "gate", target)
    for references, label in ((["ghost"], "one"), (["ghost", "wraith"], "two"), (["bad id!"], "unsafe"),
                              (["a", "b", "c", "d"], "four")):
        params = [{"name": "targets", "type": "entity_id_list", "resolve": "entity_list"}]
        case(f"unknown references {label}", synthetic("aim", [], parameters=params), "aim", {"targets": references})
    case("unknown reference", synthetic("aim", []), "aim", {"target": "ghost"})
    case("effect failure", synthetic("boom", [{"set": {"field": "_x", "value": "1 / 0"}}, {"heal": {"target": "'nobody'", "amount": 1}}]),
         "boom", target)
    case("nested refusal", synthetic("boom", [{"call": "inner", "params": {}}], extra={"inner": {
        "description": "Inner.", "parameters": [], "effects": [{"require": {"condition": "false", "error": "Inner refused"}}]}}),
         "boom", target)
    # Publication.
    shuffle = {"shuffle": {"collection": "state.items"}, "store_as": "_deck"}
    publish = {"publish": {"fields": {"top": "_deck[0]", "count": "len(_deck)"}}}
    case("hidden random", synthetic("deal", [shuffle]), "deal", target)
    case("hidden random published", synthetic("deal", [shuffle, publish]), "deal", target)
    case("hidden random stored", synthetic("deal", [shuffle, {"set": {"field": "state.wall", "value": "_deck"}}, publish]),
         "deal", target)
    isolated = {**base["model_decision_boundary"], "isolated_participant": {
        "instructions": "Play only this seat.", "observation_path": ["game"], "persona_path": ["persona"],
        "actions_path": ["game", "decision_actions"],
        "public_observation": {"namespace": package_id, "key": "state.public_observation"}}}
    case("isolated", synthetic("move", [{"set": {"field": "_x", "value": 1}}], boundary=isolated), "move", target)
    case("isolated published", synthetic("move", [{"publish": {"fields": {"x": 1}}}], boundary=isolated), "move", target)
    case("published", synthetic("show", [{"set": {"field": "_secret", "value": 9}}, {"publish": {"fields": {"shown": "_secret"}}}]),
         "show", target)
    # Writes, logs and narration.
    case("writes", synthetic("write", [
        {"set": {"field": "state.counter", "value": "state.counter + amount"}},
        {"apply_damage": {"target": "target", "amount": 2}}, {"apply_condition": {"target": "target", "condition": "'stunned'"}},
        {"create_entity": {"id": "'goblin'", "value": {"hp": 3}}},
        {"set_participant": {"participant": "_actor_id", "field": "hp", "value": "actor.data.hp + 1"}},
        {"log": {"message": "{target.name} took a hit"}}, {"log": {"message": "lvl", "level": 3}},
        {"narrate": "The {target.name} snarls"}, {"narrate": {"text": "t", "style": "system", "priority": "high"}},
    ]), "write", target)
    for label, value in (("dict", {"k": 1}), ("text", "'ab'"), ("number", 5), ("numbers", [1, 2]), ("empty", []),
                         ("kind", [{"kind": "mine", "message": "m"}])):
        case(f"logs {label}", synthetic("logs", [{"set": {"field": "_logs", "value": value}}]), "logs", target)
    # State-bound parameters and nested calls.
    bound = [{"name": "mode", "type": "string", "from_state": "state.mode"},
             {"name": "leader", "type": "string", "from_state": "combat.leader"}, AMOUNT]
    case("bound", synthetic("bound", [{"set": {"field": "_seen", "value": "mode + '/' + leader"}}], parameters=bound),
         "bound", {"mode": "ignored"})
    case("bound missing", synthetic("bound", [], parameters=[{"name": "gone", "type": "string", "from_state": "state.gone"}]),
         "bound", {})
    case("bound combat missing", synthetic("bound", [], parameters=[{"name": "gone", "type": "string", "from_state": "combat.gone"}]),
         "bound", {})
    inner = {"inner": {"description": "Inner.", "parameters": [{"name": "mode", "type": "string", "from_state": "state.mode"},
                                                                {"name": "extra", "type": "integer", "default": 4}],
                       "effects": [{"set": {"field": "state.inner_mode", "value": "mode"}},
                                   {"set": {"field": "state.inner_extra", "value": "extra"}}]}}
    case("nested bound", synthetic("outer", [{"call": "inner", "params": {}}], extra=inner), "outer", target)
    case("nested explicit", synthetic("outer", [{"call": "inner", "params": {"mode": "'given'", "extra": 1}}], extra=inner),
         "outer", target)
    gone = {"inner": {**inner["inner"], "parameters": [{"name": "gone", "type": "string", "from_state": "state.gone"}]}}
    case("nested bound missing", synthetic("outer", [{"call": "inner", "params": {}}], extra=gone), "outer", target)
    # Procedures.
    procedures = {"on_hit": {"trigger": "on_hit", "steps": [{"set": {"field": "state.procedure_ran", "value": True}},
                                                            {"log": {"message": "procedure"}}]}}
    case("procedure", synthetic("hit", [{"trigger": "on_hit"}], procedures=procedures), "hit", target)
    # Spatial effects pass through every layer, including a nested call and a procedure.
    spatial = {"spatial_query": {"scene": "state.scene", "operation": "snapshot"}, "store_as": "_q"}
    case("spatial direct", synthetic("look", [spatial]), "look", target)
    case("spatial conditional", synthetic("look", [{"condition": "true", "on_true": [spatial]}]), "look", target)
    case("spatial skipped", synthetic("look", [{"condition": "false", "on_true": [spatial]}, {"set": {"field": "_x", "value": 1}}]),
         "look", target)
    case("spatial nested", synthetic("outer", [{"call": "inner", "params": {}}],
                                     extra={"inner": {"description": "Inner.", "parameters": [], "effects": [spatial]}}),
         "outer", target)
    case("spatial procedure", synthetic("hit", [{"trigger": "on_look"}],
                                        procedures={"on_look": {"trigger": "on_look", "steps": [spatial]}}), "hit", target)
    case("spatial for_each", synthetic("look", [{"set": {"field": "_xs", "value": [1, 2]}},
                                                {"for_each": {"collection": "_xs", "as": "_i", "do": [spatial]}}]),
         "look", target)
    mutation = {"scene": "state.scene", "map_id": "m", "expected_revision": 1, "target": {"kind": "entity", "id": "x"},
                "patch": {"active": False}}
    case("spatial mutation", synthetic("move", [{"spatial_mutation": mutation, "store_as": "_m"}]), "move", target)
    # Spatial queries and mutations through the bridge (C3a).
    def query_effect(kind, store_as="_q", **changes):
        body = {key: value for key, value in QUERIES[kind].items() if key != "kind"}
        body.update(changes)
        return {"spatial_query": {"scene": "state.scene", "operation": kind, "query": body}, "store_as": store_as}

    for kind in QUERIES:
        case(f"query {kind}", synthetic("look", [query_effect(kind), {"set": {"field": "_seen", "value": "1"}}]),
             "look", target)
    case("query cached", synthetic("look", [query_effect("ray"), query_effect("ray", "_r")]), "look", target)
    case("query snapshot twice", synthetic("look", [
        {"spatial_query": {"scene": "state.scene", "operation": "snapshot"}, "store_as": "_s1"},
        {"spatial_query": {"scene": "state.scene", "operation": "snapshot"}, "store_as": "_s2"}]), "look", target)
    snapped = {"spatial_query": {"scene": "state.scene", "operation": "ray", "snapshot": "{_snap}",
                                 "query": {k: v for k, v in QUERIES["ray"].items() if k != "kind"}}, "store_as": "_q"}
    case("query on a snapshot", synthetic("look", [
        {"spatial_query": {"scene": "state.scene", "operation": "snapshot"}, "store_as": "_snap"}, snapped]), "look", target)
    case("query on an unknown snapshot", synthetic("look", [
        {"set": {"field": "_snap", "value": {"snapshot_id": "nope"}}}, snapped]), "look", target)
    case("query on no snapshot", synthetic("look", [snapped]), "look", target)
    case("query missing scene", synthetic("look", [
        {"spatial_query": {"scene": "state.absent", "operation": "snapshot"}, "store_as": "_q"}]), "look", target)
    case("query invalid", synthetic("look", [query_effect("sweep", moving_entity_id="nobody")]), "look", target)
    move = {"scene": "state.scene", "map_id": "keep", "expected_revision": 3, "target": {"kind": "entity", "id": "orc"},
            "patch": {"position": [6.5, 6.5, 0]}}
    case("mutation then query", synthetic("move", [{"spatial_mutation": move, "store_as": "_m"}, query_effect("ray"),
                                                   {"set": {"field": "_after", "value": "state.scene.map.revision"}}]),
         "move", target)
    case("mutation stale", synthetic("move", [{"spatial_mutation": {**move, "expected_revision": 2}, "store_as": "_m"}]),
         "move", target)
    case("overlap survives a move", synthetic("move", [query_effect("circle_overlap", "_o1"),
                                                       {"spatial_mutation": move, "store_as": "_m"},
                                                       query_effect("circle_overlap", "_o2")]), "move", target)
    case("overlap moved fixed body", synthetic("move", [
        query_effect("circle_overlap", "_o1"),
        {"spatial_mutation": {**move, "target": {"kind": "entity", "id": "elf"}, "patch": {"position": [11, 10, 0]}},
         "store_as": "_m"},
        query_effect("circle_overlap", "_o2")]), "move", target)
    case("private copies", synthetic("look", [
        query_effect("path"),
        {"set": {"field": "_copy", "value": "_q"}}, {"set": {"field": "_cells", "value": "_q.cells"}},
        {"set": {"field": "_count", "value": "len(_q.cells)"}}, {"set": {"field": "_found", "value": "_q.found"}},
        {"set": {"field": "_twice", "value": "_cells"}},
        {"for_each": {"collection": "_q.cells", "as": "_cell", "do": [{"set": {"field": "_last_x", "value": "_cell[0]"}}]}},
        {"list_push": {"list": "_bag", "value": "_q"}}, {"list_push": {"list": "_plain", "value": "1"}},
        {"list_pop": {"list": "_bag"}, "store_as": "_popped"},
        {"set": {"field": "_entities", "value": "state.scene.entities"}},
        {"set": {"field": "_near", "value": "xstate.scene"}},
        {"set": {"field": "_plain_map", "value": "state.plain"}},
        {"publish": {"fields": {"count": "_count", "cells": "_cells"}}}]), "look", target)
    case("private copies unpublished", synthetic("look", [
        query_effect("path"), {"set": {"field": "_copy", "value": "_q"}}, {"set": {"field": "_count", "value": "len(_q.cells)"}},
        {"set": {"field": "_entities", "value": "state.scene.entities"}}, {"set": {"field": "_plain_map", "value": "state.plain"}}]),
         "look", target)
    state_bound = [TARGET, {"name": "scene_map", "type": "object", "from_state": "state.scene.map"},
                   {"name": "plain_map", "type": "object", "from_state": "state.plain"}]
    case("private state parameters", synthetic("look", [query_effect("ray")], parameters=state_bound), "look", target)
    case("state parameters without a scene", synthetic("look", [{"set": {"field": "_x", "value": 1}}], parameters=state_bound),
         "look", target)
    many = {"for_each": {"collection": "_xs", "as": "_i", "do": [
        {"spatial_query": {"scene": "state.scene", "operation": "ray",
                           "query": {**{k: v for k, v in QUERIES["ray"].items() if k != "kind"}, "destination": ["{_i}", 10, 1.5]}},
         "store_as": "_q"}]}}
    case("evidence limit", synthetic("look", [{"set": {"field": "_xs", "value": [2 + i / 10 for i in range(130)]}}, many]),
         "look", target)
    case("evidence just under the limit", synthetic("look", [{"set": {"field": "_xs", "value": [2 + i / 10 for i in range(128)]}}, many]),
         "look", target)
    shot = {"scene": "state.scene", "snapshot": "_q", "source_id": "a", "target_id": "b", "origin": [0, 0],
            "destination": [1, 1], "delay_ms": 0, "miss": False, "contacts": []}
    case("shot effect", synthetic("shoot", [{"shot_effect": shot}]), "shoot", target)
    case("aoe", synthetic("blast", [{"aoe": {"shape": "sphere", "size": 10}, "do": []}]), "blast", target)
    # Code not yet ported passes through every layer: an `aoe` (C3b) inside a nested call, a
    # procedure, a loop and a conditional.
    blast = {"aoe": {"shape": "sphere", "size": 10}, "do": []}
    case("pending nested", synthetic("outer", [{"call": "inner", "params": {}}],
                                     extra={"inner": {"description": "Inner.", "parameters": [], "effects": [blast]}}),
         "outer", target)
    case("pending procedure", synthetic("hit", [{"trigger": "on_blast"}],
                                        procedures={"on_blast": {"trigger": "on_blast", "steps": [blast]}}), "hit", target)
    case("pending for_each", synthetic("look", [{"set": {"field": "_xs", "value": [1, 2]}},
                                                {"for_each": {"collection": "_xs", "as": "_i", "do": [blast]}}]), "look", target)
    case("pending conditional", synthetic("look", [{"condition": "true", "on_true": [blast]}]), "look", target)
    # Choices.
    choose = {"request_choice": {"choice_id": "c1", "prompt": "Choose for {target.name}", "options": CHOICE_OPTIONS},
              "store_as": "_choice"}
    after = {"set": {"field": "state.chosen", "value": "_choice"}}
    choice_doc = synthetic("choose", [choose, after])
    case("choice suspends", choice_doc, "choose", target)
    resolution = {"choice_id": "c1", "option_id": "flee", "expected_request": REQUEST, "prior_answers": []}
    case("choice resumes", choice_doc, "choose", target, choice=resolution)
    case("choice wrong id", choice_doc, "choose", target, choice={**resolution, "choice_id": "c2"})
    case("choice twice", synthetic("choose", [choose, choose, after]), "choose", target, choice=resolution)
    prior = [{"resolution_id": "r1", "request": REQUEST, "option_id": "flee", "session_id": None}]
    case("choice prior", synthetic("choose", [choose, choose, after]), "choose", target,
         choice={**resolution, "option_id": "attack", "prior_answers": prior})
    case("choice unused", synthetic("plain", [{"set": {"field": "_x", "value": 1}}]), "plain", target, choice=resolution)
    case("choice after shuffle", synthetic("choose", [shuffle, choose]), "choose", target)
    # A procedure's context holds no choice control, so its request suspends while the outer
    # resolution is still unconsumed.
    in_procedure = {"on_choose": {"trigger": "on_choose", "steps": [
        {"request_choice": {"choice_id": "c1", "prompt": "Choose", "options": CHOICE_OPTIONS[1:] + [
            {"id": "stay", "label": "Stay"}]}, "store_as": "_choice"}]}}
    procedure_choice = synthetic("choose", [{"trigger": "on_choose"}], procedures=in_procedure)
    case("choice in procedure", procedure_choice, "choose", target)
    case("choice in procedure resolved", procedure_choice, "choose", target, choice=resolution)
    # Result cards and activity labels.
    for index, card in enumerate(CARDS):
        case(f"card {index}", synthetic("card", scene_vars(), metadata={"result_card": card}), "card", target)
    for index, label in enumerate(LABELS):
        for card in LABEL_CARDS:
            metadata = {"activity_label": label}
            if card is not None:
                metadata["result_card"] = card
            case(f"label {index} {card is not None}", synthetic("card", scene_vars(), metadata=metadata), "card", target)
    case("card on bound parameters", synthetic("card", scene_vars(), parameters=bound,
                                                metadata={"result_card": {"version": 1, "title": {"path": "parameters.mode"}}}),
         "card", {})
    case("card published", synthetic("card", [*scene_vars(), {"publish": {"fields": {"shown": "_title"}}}],
                                      metadata={"result_card": {"version": 1, "title": {"path": "context.shown"}},
                                                "activity_label": {"path": "context._title"}}), "card", target)
    case("metadata not a mapping", synthetic("card", scene_vars(), metadata=["x"]), "card", target)
    return cases


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    install_patches()
    if "--record" in sys.argv:
        recorded = record()
    else:
        previous = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {}
        recorded = previous.get("recorded_inputs", [])
        DOCUMENTS.update({digest: previous["documents"][digest] for item in recorded
                          if (digest := item["runtime"].get("document")) is not None})
    cases = targeted()
    replayed = []
    for item in recorded:
        replayed.append(run(item))
    OUTPUT.write_bytes((json.dumps({"python": sys.version.split()[0], "documents": dict(sorted(DOCUMENTS.items())),
                                    "cases": cases,
                                    "recorded_inputs": recorded, "recorded": replayed},
                                   ensure_ascii=True, allow_nan=False) + "\n").encode("ascii"))
    kinds = {}
    for item in [*cases, *replayed]:
        kind = next(key for key in ("materialize", "pending", "rejected", "failed", "outcome", "outcome_sha256") if key in item)
        kinds[kind] = kinds.get(kind, 0) + 1
    print(f"{len(cases)} targeted cases, {len(recorded)} recorded contexts: {kinds}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
