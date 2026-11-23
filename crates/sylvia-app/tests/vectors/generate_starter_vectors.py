"""Generate reference vectors for sylvia_app::bundled_starter_characters.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_starter_vectors.py

Each scenario starts with Python's caches cleared (one fresh `BundledPackages` on the Rust side)
and runs steps in order on named storage roots:
- `starters` / `available`: `bundled_starter_participants` or `available_reusable_participants`
  with the bundled Rift Ruleset's package and declaration, a binding, a legacy binding, a Setting
  schema, and an `on_invalid` recorder;
- `save`: `save_reusable_participant` of a library character into a root;
- `remove`: delete a folder under a root (to show a cached roster is not saved again);
- `corrupt`: overwrite one library file.
Each call records its participants (every field), or its error, and the root's tree afterwards.
Paths in errors are masked as `<ROOT>`.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from sylvia_gm.application import bundled_campaign_plans as bcp  # noqa: E402
from sylvia_gm.application import bundled_packages as bundled  # noqa: E402
from sylvia_gm.application import bundled_starter_characters as starters  # noqa: E402
from sylvia_gm.application.reusable_participants import save_reusable_participant  # noqa: E402
from sylvia_gm.domain.authority import PackageLockEntry, PackageRole  # noqa: E402
from sylvia_gm.domain.campaign_plan import PackageBinding  # noqa: E402
from sylvia_gm.domain.package_library import canonical_json_bytes  # noqa: E402

OUTPUT = Path(__file__).with_name("starter_vectors.json")
NL = chr(10)
B = chr(92)
SCHEMA = {"type": "object", "properties": {"home": {"type": "string"}}, "required": ["home"],
          "additionalProperties": False}


def thaw(value):
    return json.loads(canonical_json_bytes(value))


def mask(text, roots):
    for root in roots:
        for form in (str(root).replace(B, B + B), str(root), str(root).replace(B, "/")):
            text = text.replace(form, "<ROOT>")
    parts = text.split("<ROOT>")
    for index in range(1, len(parts)):
        path, quote, rest = parts[index].partition("'")
        parts[index] = path.replace(B + B, "/").replace(B, "/") + quote + rest
    return "<ROOT>".join(parts)


def tree(root):
    if not root.exists():
        return None
    return [[path.relative_to(root).as_posix(), None if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()]
            for path in sorted(root.rglob("*"))]


def participant_payload(item):
    return {field.name: thaw(getattr(item, field.name)) if getattr(item, field.name) is not None else None
            for field in dataclasses.fields(item)}


def clear_caches():
    starters._ROSTERS.clear()
    for module in (bundled, bcp, starters):
        for value in vars(module).values():
            if callable(getattr(value, "cache_clear", None)):
                value.cache_clear()


def fixtures():
    authority = bundled.bundled_rift_authority()
    declaration = bundled.bundled_rift_participant_declaration()
    package = PackageLockEntry(authority.ruleset.package_id, authority.ruleset.version,
                               authority.ruleset.content_sha256, PackageRole.PRIMARY, 0, (), ())
    rows = json.loads((ROOT / "src/sylvia_gm/campaign/srd-starters.json").read_text(encoding="utf-8"))
    return authority, declaration, package, rows


def scenarios(authority, rows):
    setting = authority.setting.as_payload()
    other_version = {**setting, "version": "0.0.1"}
    other_sha = {**setting, "content_sha256": "0" * 64}
    other_id = {**setting, "id": "pneuma.other-lands"}
    ruleset = authority.ruleset.as_payload()
    fighter = next(row for row in rows if row["id"] == "fighter")

    def save(root, operation, name, binding=setting, role="player_character", setting_data=None):
        participant = copy.deepcopy(fighter["participant"])
        participant["name"] = name
        entity_state = copy.deepcopy(fighter["entity_state"])
        return {"step": "save", "root": root, "operation_id": operation, "role": role,
                "summary": f"{name} from the library.", "authored_profile": f"{name} is saved.",
                "participant": participant, "entity_state": entity_state, "binding": binding,
                "setting_data": setting_data}

    def call(kind, root, binding=setting, **extra):
        return {"step": kind, "root": root, "binding": binding, "legacy": None, "schema": None,
                "declaration_kind": None, "ruleset": ruleset, **extra}

    return {
        "exact": [call("starters", "a"), call("available", "a")],
        "cached": [call("starters", "a"), {"step": "remove", "root": "a", "path": "bundled-srd-starters"},
                   call("starters", "a"), call("starters", "b"), call("starters", "a", schema={}),
                   call("starters", "a", schema=SCHEMA)],
        "refused": [call("starters", "a", declaration_kind="companion"),
                    call("starters", "a", ruleset={**ruleset, "id": "other.ruleset"}),
                    call("starters", "a", binding=other_id), call("starters", "a", binding=other_version),
                    call("starters", "a", binding=other_sha), call("available", "a", binding=other_version)],
        "schema_first": [call("starters", "a", schema=SCHEMA), call("starters", "a")],
        "library": [save("a", "saved-one", "Saved One"), save("a", "saved-two", "Saved Two"),
                    save("a", "older", "Older", binding=other_version),
                    call("available", "a"), call("available", "a", legacy=other_version),
                    {"step": "corrupt", "root": "a", "path": "participants", "text": "not json"},
                    call("available", "a")],
        "library_only": [save("a", "saved-one", "Saved One"),
                         call("available", "a", declaration_kind="companion"),
                         call("available", "a", binding=other_version, legacy=setting)],
    }


def run_scenario(steps, work, authority, declaration, package):
    clear_caches()
    roots = {}
    results = []
    for step in steps:
        root = roots.setdefault(step["root"], work / step["root"])
        root.mkdir(parents=True, exist_ok=True)
        masks = (work, work.resolve())
        if step["step"] == "remove":
            shutil.rmtree(root / step["path"], ignore_errors=True)
            results.append({"tree": tree(root)})
            continue
        if step["step"] == "corrupt":
            library = root / step["path"]
            target = sorted(library.iterdir())[0] / "record.json"
            target.write_text(step["text"], encoding="utf-8")
            results.append({"corrupted": target.relative_to(root).as_posix(), "tree": tree(root)})
            continue
        binding = PackageBinding(step["binding"]["id"], step["binding"]["version"], step["binding"]["content_sha256"])
        if step["step"] == "save":
            try:
                saved = save_reusable_participant(
                    root, operation_id=step["operation_id"], role=step["role"], summary=step["summary"],
                    authored_profile=step["authored_profile"], participant=step["participant"],
                    entity_state=step["entity_state"], package=package, declaration=declaration,
                    campaign_setting=binding, setting_data=step["setting_data"])
                result = ["ok", participant_payload(saved)]
            except Exception as error:  # every class is behaviour
                result = [type(error).__name__, mask(str(error), masks)]
            results.append({"outcome": result, "tree": tree(root)})
            continue
        rules = step["ruleset"]
        chosen_package = package if rules["id"] == package.package_id else PackageLockEntry(
            rules["id"], rules["version"], rules["content_sha256"], PackageRole.PRIMARY, 0, (), ())
        chosen = declaration if step["declaration_kind"] is None else dataclasses.replace(
            declaration, kind=step["declaration_kind"])
        legacy = None if step["legacy"] is None else PackageBinding(
            step["legacy"]["id"], step["legacy"]["version"], step["legacy"]["content_sha256"])
        invalid = []
        try:
            if step["step"] == "starters":
                items = starters.bundled_starter_participants(
                    root, package=chosen_package, declaration=chosen, campaign_setting=binding,
                    setting_character_schema=step["schema"])
            else:
                items = starters.available_reusable_participants(
                    root, package=chosen_package, declaration=chosen, campaign_setting=binding,
                    legacy_campaign_setting=legacy, setting_character_schema=step["schema"],
                    on_invalid=lambda name, error: invalid.append([name, type(error).__name__,
                                                                   mask(str(error), masks)]))
            result = ["ok", [participant_payload(item) for item in items]]
        except Exception as error:  # every class is behaviour
            result = [type(error).__name__, mask(str(error), masks), getattr(error, "code", None)]
        results.append({"outcome": result, "invalid": invalid, "tree": tree(root)})
    return results


def main() -> None:
    authority, declaration, package, rows = fixtures()
    cases = []
    with tempfile.TemporaryDirectory() as scratch:
        for name, steps in scenarios(authority, rows).items():
            work = Path(scratch) / name
            work.mkdir()
            cases.append({"name": name, "steps": steps,
                          "results": run_scenario(steps, work, authority, declaration, package)})
    clear_caches()
    vectors = {"python": ".".join(sys.version.split()[0].split(".")[:2]), "cases": cases}
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + NL, encoding="utf-8",
                      newline=NL)
    for case in cases:
        for step, result in zip(case["steps"], case["results"]):
            outcome = result.get("outcome")
            shown = (outcome[0], len(outcome[1])) if outcome and outcome[0] == "ok" and isinstance(outcome[1], list) \
                else (ascii(outcome)[:120] if outcome else "")
            print(case["name"], step["step"], step["root"], shown, len(result["tree"] or []),
                  ascii(result.get("invalid"))[:120])


if __name__ == "__main__":
    main()
