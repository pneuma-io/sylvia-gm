"""Generate reference vectors for sylvia_app::reusable_participants.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_reusable_participant_vectors.py

Contexts (package lock entry, declaration, Setting binding):
- `current`: SRD Lite's repository record (its own declaration and starters);
- `legacy`: the SRD Lite 0.3.0 fixture archive and its retained Character File identity.
Sections:
- `parses`: `parse_setting_character` on the shipped and fixture Character Files (current, legacy
  migration through the retained equipment contract, and the 0.3.0 file), for both roles, with
  envelope mutations and malformed sources. Sources are stored verbatim.
- `steps`: one scenario in a fresh storage root, which the Rust test replays. The twelve bundled
  starters are drafted and saved (with and without a source profile), then loaded in matching
  and mismatched contexts. Records, payloads and profiles are tampered with (each undone), and
  the library is listed and entries deleted.
Machine paths are masked as <ROOT>. Outcomes are a shape or [class, message, code, diagnostics,
issues].
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.application import reusable_participants as rp  # noqa: E402
from sylvia_gm.domain.authority import PackageLockEntry, PackageRole, thaw_json_value  # noqa: E402
from sylvia_gm.domain.campaign_plan import PackageBinding  # noqa: E402
from sylvia_gm.domain.package_library import (  # noqa: E402
    ParticipantArtifactDeclaration,
    canonical_json_bytes,
    manifest_from_record,
)

OUTPUT = Path(__file__).with_name("reusable_participant_vectors.json")
SRD = "rulesets/srd-lite/package-record.json"
STARTERS = "src/sylvia_gm/campaign/srd-starters.json"
LEGACY_ARCHIVE = "tests/fixtures/SRD-Lite-117-QA-Legacy-Contract-Clean.sylvia-gm-package"
LEGACY_IDENTITY = "tests/fixtures/lite-tavin-retained-legacy.identity.json"
FILES = ["tests/fixtures/lite-tavin-bell.json", "campaign-plans/a-debt-in-iron/setting-files/characters/tavin-bell.json",
         "tests/fixtures/lite-tavin-retained-legacy.json"]
SETTING = ["pneuma.severed-lands", "1.0.0", "a" * 64]
rng = random.Random(20261124)
B, NL = chr(92), chr(10)
JUNK = [None, True, 0, 1, 2.0, -1, "", " x", "x", [], ["x"], {}, {"k": 1}]


def declaration_of(artifact):
    return ParticipantArtifactDeclaration(
        kind=artifact["kind"], schema_id=artifact["schema_id"], schema_sha256=artifact["schema_sha256"],
        schema=artifact["schema"], entity_state_schema=artifact.get("entity_state_schema"),
        initial_state_calculations=tuple(artifact.get("initial_state_calculations") or ()),
        initial_state_validation=artifact.get("initial_state_validation"))


def contexts():
    record = json.loads((ROOT / SRD).read_text(encoding="utf-8"))
    manifest = manifest_from_record(record, format_version=4)
    current = (PackageLockEntry(manifest.package_id, manifest.version, manifest.content_sha256, PackageRole.PRIMARY,
                                0, (), ()),
               next(item for item in manifest.participant_artifacts if item.kind == "player_character"),
               PackageBinding(*SETTING))
    with zipfile.ZipFile(ROOT / LEGACY_ARCHIVE) as archive:
        packages = json.loads(archive.read("manifest.json"))["packages"]
    primary = next(item for item in packages if item["id"] == "sylvia.srd-lite.bg3")
    setting = json.loads((ROOT / LEGACY_IDENTITY).read_text(encoding="utf-8"))["campaign_setting"]
    legacy = (PackageLockEntry(primary["id"], primary["version"], primary["content_sha256"], PackageRole.PRIMARY, 0,
                               (), ()),
              declaration_of(primary["participant_artifacts"][0]),
              PackageBinding(setting["id"], setting["version"], setting["content_sha256"]))
    return {"current": current, "legacy": legacy}


def context_spec(context):
    package, declaration, setting = context
    return {"package": [package.package_id, package.version, package.sha256],
            "setting": [setting.package_id, setting.version, setting.content_sha256]}


def digest_of(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


VALID_PROFILE = {"artifact_id": "character-profile-" + "1" * 24 + "-" + "d" * 16, "content_sha256": "d" * 64,
                 "name": "Ada"}
PROFILES = [VALID_PROFILE, None, None, {"profile_id": "pneuma.profile.x", "revision": 1}]


def participant_shape(item):
    return {"artifact_id": item.artifact_id, "role": item.role, "name": item.name, "summary": item.summary,
            "authored_profile_sha256": None if item.authored_profile is None else
            hashlib.sha256(item.authored_profile.encode("utf-8")).hexdigest(),
            "content_sha256": item.content_sha256,
            "participant_sha256": digest_of(item.participant), "entity_state_sha256": digest_of(item.entity_state),
            "setting_data_sha256": digest_of(item.setting_data),
            "source_profile": thaw_json_value(item.source_profile),
            "source_migration": thaw_json_value(item.source_migration),
            "catalog": thaw_json_value(item.catalog_payload()),
            "rift_source_sha256": hashlib.sha256(canonical_json_bytes(item.rift_source())).hexdigest()}


class Scenario:
    def __init__(self, storage):
        self.storage = storage
        self.backups = {}

    def mask(self, text):
        for root in (str(self.storage), str(self.storage.resolve())):
            for form in (root.replace(B, B + B), root, root.replace(B, "/")):
                text = text.replace(form, "<ROOT>")
        parts = text.split("<ROOT>")
        for index in range(1, len(parts)):
            path, quote, rest = parts[index].partition("'")
            parts[index] = path.replace(B + B, "/").replace(B, "/") + quote + rest
        return "<ROOT>".join(parts)

    def outcome(self, call, shape):
        try:
            return ["ok", shape(call())]
        except Exception as error:  # every class is behaviour
            diagnostics = getattr(error, "diagnostics", None) or ()
            issues = getattr(error, "issues", None) or ()
            return [type(error).__name__, self.mask(str(error)), getattr(error, "code", None),
                    [item.as_payload() for item in diagnostics], [item.as_payload() for item in issues]]


def files_of(entry):
    return {item.name: hashlib.sha256(item.read_bytes()).hexdigest() for item in sorted(entry.iterdir())}


def mutated_sources(text):
    envelope = json.loads(text)
    found = []
    paths = []

    def walk(value, path):
        paths.append(path)
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, (*path, key))
    walk(envelope, ())
    for _ in range(18):
        path = rng.choice(paths[1:])
        changed = copy.deepcopy(envelope)
        parent = changed
        for part in path[:-1]:
            parent = parent[part]
        if rng.random() < 0.25:
            del parent[path[-1]]
        else:
            parent[path[-1]] = rng.choice(JUNK)
        found.append(json.dumps(changed, indent=2))
    return found


def parse_cases(contexts_by_name, scenario):
    cases = []
    sources = []
    for path in FILES:
        text = (ROOT / path).read_text(encoding="utf-8")
        envelope = json.loads(text)
        sources.append((path, text, envelope["id"]))
        for index, mutated in enumerate(mutated_sources(text)):
            sources.append((f"{path}#{index}", mutated, envelope["id"]))
    text0 = (ROOT / FILES[0]).read_text(encoding="utf-8")
    envelope0 = json.loads(text0)
    sources += [
        ("empty", "", "c"), ("nul", text0.replace("{", "{" + chr(0), 1), envelope0["id"]),
        ("duplicate", text0.replace('"summary":', '"summary": "x", "summary":', 1), envelope0["id"]),
        ("nan", text0.replace('"summary":', '"extra": NaN, "summary":', 1), envelope0["id"]),
        ("deep", text0.replace('"setting_data":', '"setting_data": ' + "[" * 40 + "]" * 40 + ', "x":', 1), envelope0["id"]),
        ("trailing", text0 + "x", envelope0["id"]), ("wrong_id", text0, "someone-else"), ("long_id", text0, "c" * 161),
        ("version_1", json.dumps({**{k: v for k, v in envelope0.items() if k != "setting_data"}, "format_version": 1}),
         envelope0["id"]),
        ("version_true", json.dumps({**envelope0, "format_version": True}), envelope0["id"]),
        ("version_float", json.dumps({**envelope0, "format_version": 2.0}), envelope0["id"]),
    ]
    for name, text, character_id in sources:
        for context_name in ("current", "legacy"):
            package, declaration, setting = contexts_by_name[context_name]
            for role in ("player_character", "companion"):
                if name.count("#") and role == "companion":
                    continue
                case = {"name": name, "source": text, "character_id": character_id, "context": context_name,
                        "role": role, "outcome": scenario.outcome(lambda: rp.parse_setting_character(
                            text, character_id=character_id, role=role, package=package,
                            declaration=declaration, campaign_setting=setting), participant_shape)}
                if name == "nan":
                    # Differences row 2: Python reads NaN; the port refuses the file as invalid JSON.
                    case["row"] = 2
                cases.append(case)
    return cases


def scenario_steps(contexts_by_name, scenario):
    current = contexts_by_name["current"]
    starters = json.loads((ROOT / STARTERS).read_text(encoding="utf-8"))
    steps = []

    def step(**fields):
        fields["outcome"] = run(fields)
        steps.append(fields)
        return fields["outcome"]

    def context_of(name):
        package, declaration, setting = contexts_by_name[name if name in contexts_by_name else "current"]
        if name == "other_setting":
            setting = PackageBinding(setting.package_id, "9.9.9", setting.content_sha256)
        if name == "other_package":
            package = PackageLockEntry(package.package_id, "0.0.1", package.sha256, PackageRole.PRIMARY, 0, (), ())
        return package, declaration, setting

    def run(fields):
        op = fields["op"]
        storage = scenario.storage
        if op in ("draft", "save"):
            row = starters[fields["starter"]]
            participant = copy.deepcopy(row["participant"])
            authored = copy.deepcopy(row["entity_state"])
            for kind, path, value in fields.get("edits", []):
                target = participant if path[0] == "participant" else authored
                if kind == "delete":
                    target.pop(path[1], None)
                else:
                    target[path[1]] = value
            package, declaration, setting = context_of(fields["context"])
            summary = fields.get("summary", row["summary"])
            profile = fields.get("authored_profile", row["authored_profile"])
            if op == "draft":
                return scenario.outcome(lambda: rp.validate_reusable_participant_draft(
                    role=fields["role"], summary=summary, authored_profile=profile, participant=participant,
                    entity_state=authored, declaration=declaration),
                    lambda result: [hashlib.sha256(result[0].encode()).hexdigest(), *[digest_of(part) for part in result[1:]]])
            return scenario.outcome(lambda: rp.save_reusable_participant(
                storage, operation_id=fields["operation_id"], role=fields["role"], summary=summary,
                authored_profile=profile, participant=participant, entity_state=authored, package=package,
                declaration=declaration, campaign_setting=setting, source_profile=fields.get("source_profile")),
                lambda item: {**participant_shape(item), "files": files_of(storage / "participants" / item.artifact_id),
                              "storage": sorted(path.name for path in storage.iterdir())})
        if op == "load":
            package, declaration, setting = context_of(fields["context"])
            legacy = PackageBinding(*fields["legacy"]) if fields.get("legacy") else None
            return scenario.outcome(lambda: rp.load_reusable_participant(
                storage, fields["artifact"], package=package, declaration=declaration, campaign_setting=setting,
                legacy_campaign_setting=legacy), participant_shape)
        if op == "list":
            package, declaration, setting = context_of(fields["context"])
            invalid = []
            handler = ((lambda name, error: invalid.append([name, type(error).__name__, scenario.mask(str(error))]))
                       if fields["handler"] else None)
            result = scenario.outcome(lambda: [participant_shape(item) for item in rp.compatible_reusable_participants(
                storage, package=package, declaration=declaration, campaign_setting=setting, on_invalid=handler)],
                lambda value: value)
            return [*result, invalid] if result[0] == "ok" else result
        if op == "delete":
            return scenario.outcome(lambda: rp.delete_reusable_participant(
                storage, fields["artifact"], expected_content_sha256=fields["hash"],
                confirmation=fields["confirmation"]), lambda value: value)
        entry = storage / "participants" / fields.get("artifact", "")
        if op == "backup":
            shutil.copytree(entry, storage.parent / ("backup-" + fields["label"]))
        elif op == "restore":
            shutil.rmtree(entry)
            shutil.copytree(storage.parent / ("backup-" + fields["label"]), entry)
        elif op == "edit_json":
            path = entry / fields["name"]
            value = json.loads(path.read_text(encoding="utf-8"))
            for kind, keys, item in fields["edits"]:
                parent = value
                for key in keys[:-1]:
                    parent = parent[key]
                if kind == "delete":
                    parent.pop(keys[-1], None)
                else:
                    parent[keys[-1]] = item
            path.write_bytes(canonical_json_bytes(value))
        elif op == "write":
            (entry / fields["name"]).write_bytes(bytes.fromhex(fields["hex"]))
        elif op == "remove":
            (entry / fields["name"]).unlink()
        elif op == "mkdir_entry":
            (storage / "participants" / fields["name"]).mkdir()
        else:
            raise AssertionError(op)
        return ["ok", None]

    roles = ["player_character", "companion"]
    saved = []
    for index in range(len(starters)):
        role = roles[index % 2]
        step(op="draft", starter=index, role=role, context="current")
        outcome = step(op="save", starter=index, role=role, context="current", operation_id=f"save-{index}",
                       source_profile=PROFILES[index % 4])
        if outcome[0] == "ok":
            saved.append((index, outcome[1]["artifact_id"], outcome[1]["content_sha256"], role))
    step(op="save", starter=0, role="player_character", context="current", operation_id="save-0", source_profile=VALID_PROFILE)
    for edits, extra in [([["set", ["participant", "name"], " Spaced"]], {}), ([["set", ["participant", "level"], "x"]], {}),
                         ([["delete", ["participant", "abilities"], None]], {}), ([["set", ["entity_state", "max_hp"], 5]], {}),
                         ([], {"summary": " bad"}), ([], {"summary": ""}), ([], {"authored_profile": ""}),
                         ([], {"authored_profile": " x"}), ([], {"authored_profile": "x" * 300000}),
                         ([], {"role": "villain"}), ([], {"operation_id": ""}), ([], {"context": "legacy"})]:
        fields = {"op": "save", "starter": 1, "role": extra.pop("role", "player_character"), "context": extra.pop("context", "current"),
                  "operation_id": extra.pop("operation_id", "save-bad"), "edits": edits, **extra}
        step(**fields)
        step(**{**fields, "op": "draft"})
    legacy_setting = list(SETTING)
    for _, artifact, _, _ in saved[:4]:
        for context in ("current", "legacy", "other_setting", "other_package"):
            step(op="load", artifact=artifact, context=context)
        step(op="load", artifact=artifact, context="current", legacy=legacy_setting)
    step(op="load", artifact="participant-bad", context="current")
    step(op="load", artifact="participant-" + "0" * 24 + "-" + "0" * 16, context="current")
    for handler in (True, False):
        step(op="list", context="current", handler=handler)
    step(op="list", context="other_setting", handler=True)
    _, artifact, digest, _ = saved[0]
    tampers = [
        ("record.json", [["set", ["format_version"], 1]]), ("record.json", [["set", ["format_version"], True]]),
        ("record.json", [["set", ["format_version"], 2]]), ("record.json", [["set", ["format_version"], 4]]),
        ("record.json", [["set", ["format_version"], 6]]), ("record.json", [["set", ["format_version"], 4611686018427387904]]),
        ("record.json", [["set", ["extra"], 1]]), ("record.json", [["delete", ["source_profile"], None]]),
        ("record.json", [["set", ["name"], "Other"]]), ("record.json", [["set", ["summary"], " x"]]),
        ("record.json", [["set", ["summary"], 5]]), ("record.json", [["set", ["role"], "companion"]]),
        ("record.json", [["set", ["role"], "villain"]]), ("record.json", [["set", ["content_sha256"], "b" * 64]]),
        ("record.json", [["set", ["schema_sha256"], "b" * 64]]), ("record.json", [["set", ["source_profile"], {"bad": 1}]]),
        ("record.json", [["delete", ["campaign_setting_id"], None], ["delete", ["campaign_setting_version"], None],
                         ["delete", ["campaign_setting_sha256"], None], ["delete", ["source_profile"], None],
                         ["set", ["format_version"], 1]]),
        ("payload.json", [["delete", ["setting_data"], None]]), ("payload.json", [["set", ["extra"], 1]]),
        ("payload.json", [["set", ["participant", "name"], "Other"]]), ("payload.json", [["set", ["entity_state", "max_hp"], 1]]),
        ("payload.json", [["set", ["participant"], []]]), ("payload.json", [["set", ["setting_data"], {"x": 1}]]),
    ]
    for name, edits in tampers:
        label = f"t{len(steps)}"
        step(op="backup", artifact=artifact, label=label)
        step(op="edit_json", artifact=artifact, name=name, edits=edits)
        step(op="load", artifact=artifact, context="current")
        step(op="load", artifact=artifact, context="current", legacy=legacy_setting)
        step(op="restore", artifact=artifact, label=label)
    for name, data in [("profile.md", b"\xff"), ("profile.md", b" padded"), ("record.json", b"\xef\xbb\xbf{}"),
                       ("payload.json", b"[1,"), ("profile.md", None), ("payload.json", None)]:
        label = f"t{len(steps)}"
        step(op="backup", artifact=artifact, label=label)
        if data is None:
            step(op="remove", artifact=artifact, name=name)
        else:
            step(op="write", artifact=artifact, name=name, hex=data.hex())
        step(op="load", artifact=artifact, context="current")
        step(op="list", context="current", handler=True)
        step(op="restore", artifact=artifact, label=label)
    step(op="mkdir_entry", name="not-a-participant")
    step(op="list", context="current", handler=True)
    step(op="list", context="current", handler=False)
    exact = f"permanently delete {artifact}"
    for target, value, confirmation in [("bad", digest, exact), (artifact, "XYZ", exact), (artifact, digest, "no"),
                                        (artifact, "c" * 64, exact), (artifact, digest, exact), (artifact, digest, exact)]:
        step(op="delete", artifact=target, hash=value, confirmation=confirmation)
    step(op="load", artifact=artifact, context="current")
    return steps


def main() -> None:
    by_name = contexts()
    with tempfile.TemporaryDirectory() as scratch:
        storage = Path(scratch) / "storage"
        storage.mkdir()
        scenario = Scenario(storage)
        parses = parse_cases(by_name, scenario)
        steps = scenario_steps(by_name, scenario)
    vectors = {"python": ".".join(sys.version.split()[0].split(".")[:2]), "srd": SRD, "starters": STARTERS,
               "legacy_archive": LEGACY_ARCHIVE, "legacy_identity": LEGACY_IDENTITY, "setting": SETTING,
               "contexts": {name: context_spec(context) for name, context in by_name.items()},
               "parses": parses, "steps": steps}
    text = json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + NL
    assert str(ROOT) not in text and str(ROOT).replace(B, "/") not in text
    OUTPUT.write_text(text, encoding="utf-8", newline=NL)
    kinds = {}
    for case in parses + steps:
        result = case["outcome"]
        key = case.get("op", "parse") + ": " + ("ok" if result[0] == "ok" else f"{result[0]} {result[1][:50]}")
        kinds[key] = kinds.get(key, 0) + 1
    print(len(parses), "parses,", len(steps), "steps,", len(text), "bytes")
    for key in sorted(kinds):
        print(" ", kinds[key], ascii(key))


if __name__ == "__main__":
    main()
