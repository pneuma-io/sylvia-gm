"""Generate reference vectors for sylvia_app::package_library and initial_character_state.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_package_library_vectors.py

Sections:
- `contracts`: `validate_initial_character_contract` and `initial_character_input_schema` on the
  shipped SRD Lite player declaration (read by the Rust test from the package record), random
  and targeted edits of it.
- `installs`: `install_package_archive` with a store that records its calls. Archives are the
  repository packages under 1 MB and two of the 26 larger ones (read from their paths; see
  `LARGE_KEPT`), and archives built here from the small Ruleset source
  of the ruleset-source vectors (`compile_ruleset_source`, then `materialize_package_archive`;
  the Rust test rebuilds them with its own ports, which those vectors prove byte for byte).
- `predicates`, `admissions`, `surfaces`, `rebinds`, `migrations`: the module's pure functions.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import copy
import glob
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from generate_ruleset_source_vectors import SMALL  # noqa: E402

from sylvia_gm.application.initial_character_state import (  # noqa: E402
    initial_character_input_schema,
    validate_initial_character_contract,
)
from sylvia_gm.application.package_library import (  # noqa: E402
    declaration_admitted,
    dependency_only_manifest_rebind,
    install_package_archive,
    project_surfaces,
    projection_bytes,
    run_migration_outside_sqlite,
    state_predicates_match,
)
from sylvia_gm.application.package_materialization import materialize_package_archive  # noqa: E402
from sylvia_gm.domain.authority import thaw_json_value  # noqa: E402
from sylvia_gm.builder._ruleset_source import compile_ruleset_source  # noqa: E402
from sylvia_gm.domain.package_library import (  # noqa: E402
    PackageCandidate,
    StatePredicate,
    SurfaceDeclaration,
    ToolDeclaration,
    canonical_json_bytes,
    closure_sha256,
)
from sylvia_gm.domain.package_library import CandidateStatus  # noqa: E402

OUTPUT = Path(__file__).with_name("package_library_vectors.json")
SRD = "rulesets/srd-lite/package-record.json"
NEWLINE = chr(10)
rng = random.Random(20261122)
JUNK = [None, True, 0, 2, 1.5, "", "x", "participant.name", "foo.bar", "1 +", [], ["x"], {}, {"type": "string"},
        {"set": {"field": "name", "value": "participant.name"}}]


def apply(document, edits):
    document = copy.deepcopy(document)
    for op, path, value in edits:
        parent = document
        for part in path[:-1]:
            parent = parent[part]
        if op == "delete":
            del parent[path[-1]]
        elif op == "append":
            parent[path[-1]].append(copy.deepcopy(value))
        else:
            parent[path[-1]] = copy.deepcopy(value)
    return document


def leaves(value, path=()):
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from leaves(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from leaves(item, (*path, index))


def key_order(value) -> list:
    order, pending = [], [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            order.append(list(item))
            pending.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            pending.extend(reversed(item))
    return order


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def error_outcome(error: Exception) -> list:
    diagnostics = getattr(error, "diagnostics", None) or ()
    return [type(error).__name__, str(error), [item.as_payload() for item in diagnostics],
            getattr(error, "code", None)]


def attempt(call):
    try:
        value = call()
    except Exception as error:  # every class is behaviour
        return error_outcome(error)
    return ["ok", thaw_json_value(value) if value is not None and not isinstance(value, bool) else value]


# ── contracts ────────────────────────────────────────────────────────────────────────────


def contract_cases(base) -> list:
    cases = []

    def add(edits):
        declaration = apply(base, edits)
        schema, calculations = declaration["entity_state_schema"], declaration["initial_state_calculations"]
        validation = declaration.get("initial_state_validation")
        # A manifest's declaration always carries a mapping schema and a list of calculations,
        # which is what the Rust functions take.
        if not isinstance(schema, dict) or not isinstance(calculations, list):
            return
        contract = attempt(lambda: validate_initial_character_contract(schema, calculations, validation))
        input_schema = attempt(lambda: initial_character_input_schema(schema, calculations))
        if input_schema[0] == "ok":
            value = input_schema[1]
            input_schema = ["ok", digest(canonical_json_bytes(value)), digest(canonical_json_bytes(key_order(value)))]
        cases.append({"edits": edits, "contract": contract, "input_schema": input_schema})

    add([])
    paths = [(p, v) for p, v in leaves(base) if p and p[0] in ("entity_state_schema", "initial_state_calculations")]
    for _ in range(220):
        path, value = rng.choice(paths)
        if isinstance(value, dict) and value and rng.random() < 0.3:
            add([["delete", [*path, rng.choice(list(value))], None]])
        else:
            add([["set", list(path), rng.choice(JUNK)]])
    calc = lambda field, value, **extra: {"set": {"field": field, "value": value}, **extra}  # noqa: E731
    targeted = [
        [["set", ["initial_state_calculations"], []]],
        [["set", ["initial_state_calculations"], [calc("name", "participant.name")] * 129]],
        [["set", ["initial_state_calculations"], [calc("name", "participant.name")] * 128]],
        [["set", ["initial_state_calculations", 0], {"set": {"field": "name", "value": "participant.name"}, "x": 1}]],
        [["set", ["initial_state_calculations", 0], calc("name", "participant.name", condition="")]],
        [["set", ["initial_state_calculations", 0], calc("name", "participant.name", condition=None)]],
        [["set", ["initial_state_calculations", 0], calc("name", "participant.name", condition="true")]],
        [["set", ["initial_state_calculations", 0], calc("name", "participant.name", condition="foo.x")]],
        [["set", ["initial_state_calculations", 0], calc("a..b", "participant.name")]],
        [["set", ["initial_state_calculations", 0], calc("_hidden", "participant.name")]],
        [["set", ["initial_state_calculations", 0], calc("name", "")]],
        [["set", ["initial_state_calculations", 0], calc("name", "1 +")]],
        [["set", ["initial_state_calculations", 0], calc("name", "foo.bar")]],
        [["set", ["initial_state_calculations", 0], calc("name", "participant.items[other.x]")]],
        [["set", ["initial_state_calculations", 0], calc("name", "len(rules.x) + setting_data.y")]],
        [["append", ["initial_state_calculations"], calc("name", "participant.name")]],
        [["set", ["entity_state_schema", "type"], "array"]],
        [["set", ["entity_state_schema", "properties", "name", "type"], "nope"]],
        [["set", ["entity_state_schema", "extra_keyword"], 1]],
        [["set", ["initial_state_validation"], {"description": "x"}]],
        [["set", ["initial_state_validation"], None]],
    ]
    for edits in targeted:
        add(edits)
    return cases


# ── installs ─────────────────────────────────────────────────────────────────────────────


class Store:
    def __init__(self):
        self.calls = []

    def stage_package_archive(self, admitted, install_id):
        self.calls.append(["stage", install_id, closure_sha256(admitted.package_lock)])

    def publish_package_archive(self, admitted, install_id):
        self.calls.append(["publish", install_id])
        return PackageCandidate("candidate", install_id, CandidateStatus.VALIDATED,
                                closure_sha256(admitted.package_lock), admitted.package_lock, {})


# The small source exercises the compiler's feature detection with a text pre-condition, a
# `publish` effect, a spatial procedure step and a `participant.*` state read, which the package runtime refuses; every built
# archive replaces them with plain declarations, so each spec changes one thing.
INSTALLABLE = [["set", ["mechanic_families", "pick", "pre_conditions"], []],
               ["set", ["mechanic_families", "pick", "effects"], [{"set": {"field": "_picked", "value": 1}}]],
               ["set", ["procedures"], {}],
               ["set", ["mechanic_families", "pick", "parameters", 3, "from_state"], "state.level"]]


def built_archive(spec) -> bytes:
    edits = [*INSTALLABLE, *spec.get("source_edits", [])]
    draft = compile_ruleset_source(json.dumps(apply(SMALL, edits)).encode("utf-8")).draft
    payloads = dict(draft.payloads)
    for path, text in spec.get("payloads", {}).items():
        payloads[path] = text.encode("utf-8")
    if spec.get("link"):
        declarations = json.loads(payloads["content/content-declarations.json"])
        links = declarations["inclusions"][0]["links"]
        links["artifacts"] = sorted({*links["artifacts"], *spec["link"]})
        payloads["content/content-declarations.json"] = canonical_json_bytes(declarations)
    record = apply(draft.record, spec.get("record_edits", []))
    draft = type(draft)(record, payloads, draft.format_version)
    archive = materialize_package_archive((draft,)).archive
    archive.seek(0)
    return archive.read()


# An archive carrying an SRD Lite runtime (3 MB) takes about 40 s to validate in a debug build,
# so of those 26 the test installs two representatives (the current SRD Lite inside the bundled
# Rift Setting, and an older fixture version); every smaller repository archive is installed.
LARGE = 1024 * 1024
LARGE_KEPT = {"campaign-plans/rift-awakening/setting.sylvia-gm-package", "tests/fixtures/lite-0.3.28.sylvia-gm-package"}
OMITTED: list[str] = []


def install_outcome(archive: bytes) -> list:
    store = Store()
    try:
        import io
        candidate = install_package_archive(store, io.BytesIO(archive), install_id="install-1")
    except Exception as error:  # every class is behaviour
        return [*error_outcome(error), store.calls]
    return ["ok", store.calls, candidate.closure_sha256]


def install_cases() -> list:
    cases = []
    for path in sorted(str(Path(p).relative_to(ROOT)).replace(chr(92), "/")
                       for p in glob.glob(str(ROOT / "**/*.sylvia-gm-package"), recursive=True)):
        if (ROOT / path).stat().st_size > LARGE and path not in LARGE_KEPT:
            OMITTED.append(path)
            continue
        cases.append({"repository": path, "outcome": install_outcome((ROOT / path).read_bytes())})
    artifact = ["participant_artifacts", 0]
    named_schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
    named = lambda value: {"set": {"field": "name", "value": value}}  # noqa: E731
    specs = [
        {},
        {"payloads": {"runtime/ruleset.yaml": "tools: [1"}},
        {"payloads": {"runtime/ruleset.yaml": "sylvia_gm_runtime: {format: 1}"}},
        {"payloads": {"character-requirements.json": "{}"}, "link": ["character-requirements.json"]},
        {"source_edits": [["set", [*artifact, "entity_state_schema"], named_schema],
                          ["set", [*artifact, "initial_state_calculations"], [named("participant.name")]]]},
        {"source_edits": [["set", [*artifact, "entity_state_schema"], named_schema],
                          ["set", [*artifact, "initial_state_calculations"], [named("1 +")]]]},
        {"source_edits": [["set", [*artifact, "entity_state_schema"], named_schema],
                          ["set", [*artifact, "initial_state_calculations"], [named("foo.bar")]]]},
        {"source_edits": [["set", [*artifact, "entity_state_schema"], named_schema],
                          ["set", [*artifact, "initial_state_calculations"], [named("participant.name")]],
                          ["set", [*artifact, "initial_state_validation"], {"description": "x"}]]},
        {"source_edits": [["set", ["public_actions", 0, "name"], "renamed"]]},
        {"record_edits": [["set", ["tools", 0, "name"], "missing_tool"]]},
    ]
    for spec in specs:
        try:
            archive = built_archive(spec)
        except Exception as error:  # the archive itself is refused before installation
            cases.append({"built": spec, "outcome": ["build", *error_outcome(error)]})
            continue
        cases.append({"built": spec, "sha256": digest(archive), "outcome": install_outcome(archive)})
    return cases


# ── pure functions ───────────────────────────────────────────────────────────────────────


def predicate_from(spec):
    return StatePredicate(spec[0], spec[1], spec[2], spec[3])


PREDICATES = [["ns", "phase", "open", False], ["ns", "phase", "open", True], ["ns", "board.turn", 1, False],
              ["ns", "board.turn", True, False], ["ns", "board.deep.x", None, False], ["ns", "missing", None, False],
              ["ns", "missing", None, True], ["ns", "board", {"turn": 1, "deep": {"x": None}}, False],
              ["ns", "list.0", 1, False], ["other", "phase", "open", False], ["ns", "a.b", 2, False]]
STATES = [{}, {"ns.phase": "open"}, {"ns.phase": "closed"}, {"ns.board": {"turn": 1, "deep": {"x": None}}},
          {"ns.board": {"turn": 1.0}}, {"ns.list": [1, 2]}, {"ns.a.b": 2, "ns.a": {"b": 3}}, {"ns.a": {"b": 2}},
          {"ns.missing": None}, {"ns.board": "text"}]


def surface_from(spec):
    return SurfaceDeclaration(spec["identity"], spec["capability"], spec.get("title"), spec.get("icon"),
                              spec.get("renderer"), spec.get("source"), spec.get("group"), spec.get("order", 0),
                              spec.get("default_size", 180), spec.get("minimum_size", 96),
                              spec.get("default_placement", "docked"), tuple(spec.get("allowed_placements", ["docked"])),
                              tuple(spec.get("actions", [])))


SURFACES = [
    {"identity": "a", "capability": "c1", "source": {"kind": "state", "key": "x"}, "title": "A", "order": 2,
     "actions": [{"id": "go"}]},
    {"identity": "b", "capability": "c2", "source": {"kind": "plan"}},
    {"identity": "c", "capability": "c1", "source": {"kind": "keyed_values", "entries": [
        {"key": {"kind": "literal"}, "value": {"kind": "participants"}}]}},
    {"identity": "d", "capability": "c1", "source": {"kind": "keyed_values", "entries": [{"key": {"kind": "plan"}}]}},
    {"identity": "e", "capability": "c1", "source": {"kind": "object", "fields": {"x": {"kind": "state"}}}},
    {"identity": "f", "capability": "c1", "source": {"kind": "object", "fields": {"x": {"kind": "plan"}}}},
    {"identity": "g", "capability": "c1", "source": None},
    {"identity": "h", "capability": "c1", "source": {"kind": "keyed_values", "entries": ""}},
    {"identity": "i", "capability": "c1", "source": {"kind": "keyed_values", "entries": "x"}},
    {"identity": "j", "capability": "c3", "source": {"kind": "literal"}, "renderer": "table", "group": "g"},
]


def main() -> None:
    record = json.loads((ROOT / SRD).read_text(encoding="utf-8"))
    base = record["participant_artifacts"][0]
    predicates = []
    for spec in PREDICATES:
        for state in STATES:
            predicates.append({"predicates": [spec], "state": state,
                               "outcome": state_predicates_match((predicate_from(spec),), state)})
    predicates.append({"predicates": PREDICATES[:2], "state": STATES[1],
                       "outcome": state_predicates_match(tuple(map(predicate_from, PREDICATES[:2])), STATES[1])})
    admissions = []
    tool = {"name": "t", "capability": "c1", "input_schema": {}, "state_predicates": [["ns", "phase", "open", False]],
            "turn_statuses": ["open"], "writers": ["dm"]}
    for capabilities in (["c1"], ["c2"], []):
        for status in ("open", "closed"):
            for writer in ("dm", "player"):
                for state in (STATES[1], STATES[2]):
                    declaration = ToolDeclaration(tool["name"], tool["capability"], tool["input_schema"],
                                                  tuple(map(predicate_from, tool["state_predicates"])),
                                                  tuple(tool["turn_statuses"]), tuple(tool["writers"]))
                    admissions.append({"capabilities": capabilities, "status": status, "writer": writer, "state": state,
                                       "outcome": declaration_admitted(declaration, state=state, turn_status=status,
                                                                       writer=writer,
                                                                       declared_capabilities=frozenset(capabilities))})
    surfaces = []
    for active in (["c1"], ["c1", "c2", "c3"], []):
        def project():
            projection = project_surfaces(tuple(map(surface_from, SURFACES[:3])), tuple(map(surface_from, SURFACES)),
                                          active_capabilities=frozenset(active))
            return {"prompt_fragment_ids": list(projection.prompt_fragment_ids),
                    "ui_surface_ids": list(projection.ui_surface_ids),
                    "bytes": projection_bytes(projection).decode("utf-8")}
        surfaces.append({"active": active, "outcome": attempt(project)})
    for kind in ([1], {"a": 1}):
        bad = {**SURFACES[0], "source": {"kind": kind}}
        surfaces.append({"active": ["c1"], "extra": bad, "outcome": attempt(lambda: project_surfaces(
            (), (surface_from(bad),), active_capabilities=frozenset(["c1"])))})
    rebinds = []
    pairs = [('{"id": "a", "dependencies": [1], "content_sha256": "x"}', '{"id": "a", "dependencies": [], "content_sha256": "y"}'),
             ('{"id": "a"}', '{"id": "b"}'), ('{"id": 1}', '{"id": 1.0}'), ('{"a": [true]}', '{"a": [1]}'),
             ('[]', '{}'), ('{"id": "a"}', '{'), ('{"b": 1, "a": 2}', '{"a": 2, "b": 1}')]
    for original, current in pairs:
        rebinds.append({"original": original, "current": current,
                        "outcome": attempt(lambda: dependency_only_manifest_rebind(original, current))})
    migrations = []
    for name, migrate in (("identity", lambda state: state), ("list", lambda state: [1]),
                          ("plain_key", lambda state: {**state, "plain": 1}),
                          ("add_dotted", lambda state: {**state, "ns.added": {"x": 1}})):
        state = {"ns.a": 1}
        migrations.append({"migrate": name, "state": state,
                           "outcome": attempt(lambda: run_migration_outside_sqlite(state, migrate))})
    vectors = {"python": ".".join(sys.version.split()[0].split(".")[:2]), "srd": SRD, "small": SMALL,
               "installable": INSTALLABLE, "contracts": contract_cases(base), "installs": install_cases(), "predicates": predicates,
               "admissions": admissions, "surfaces": surfaces, "rebinds": rebinds, "migrations": migrations,
               "predicate_specs": PREDICATES, "surface_specs": SURFACES, "omitted_large_archives": OMITTED}
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + NEWLINE, encoding="utf-8",
                      newline=NEWLINE)
    for section in ("contracts", "installs"):
        kinds = {}
        for case in vectors[section]:
            result = case.get("contract", case.get("outcome"))
            key = result[0] if result[0] in ("ok", "build") else (result[3] or result[0])
            kinds[key] = kinds.get(key, 0) + 1
        print(section, len(vectors[section]), dict(sorted(kinds.items())))
    print(OUTPUT.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
