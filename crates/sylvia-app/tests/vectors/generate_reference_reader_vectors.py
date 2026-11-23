"""Generate reference vectors for builder_references' reader half and compatible_character_copy.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_reference_reader_vectors.py

The owner, archives and storage root are the selection vectors' (`generate_selection_vectors`):
builder_references' fake owner over real admitted archives. The references are captured from the
Last Cart Through snapshot's closure (`capture_content_references`). Records:
- `launch`: `launch_references` over session contexts (launch evidence, Builder tool results
  that select a Ruleset or Setting, an unhashable tool name, other evidence);
- `candidate`: `candidate_for_references` on the captured references and changed copies;
- `examples`: `with_character_examples` for both purposes and changed references;
- `reader`: `reader_for_references`: the paths, then each read (the text's SHA-256 or the error),
  on references with character examples, a character profile and a Plan;
- `authored`: `authored_character_state` over declarations' calculations;
- `copy`: `copy_compatible_character` from a saved Character to bindings and schemas.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path[:0] = [str(ROOT / "src"), str(ROOT), str(HERE)]

import generate_character_profile_vectors as profiles  # noqa: E402
import generate_selection_vectors as sel  # noqa: E402
from sylvia_gm.application import builder_references as references  # noqa: E402
from sylvia_gm.application import bundled_campaign_plans as bcp  # noqa: E402
from sylvia_gm.application import bundled_packages as bundled  # noqa: E402
from sylvia_gm.application import compatible_character_copy as copying  # noqa: E402
from sylvia_gm.application.character_profiles import save_character_profile  # noqa: E402
from sylvia_gm.application.reusable_participants import save_reusable_participant  # noqa: E402
from sylvia_gm.domain.authority import PackageRole, WriterRole  # noqa: E402
from sylvia_gm.domain.campaign_plan import PackageBinding  # noqa: E402
from sylvia_gm.domain.package_library import canonical_json_bytes  # noqa: E402
from sylvia_gm.domain.session import (  # noqa: E402
    SessionContext,
    SessionMessage,
    SessionMessageKind,
    SessionRecord,
)

OUTPUT = HERE / "reference_reader_vectors.json"
NL = chr(10)
SCHEMA = {"type": "object", "properties": {"home": {"type": "string"}}, "required": ["home"],
          "additionalProperties": False}


def thaw(value):
    return json.loads(canonical_json_bytes(value))


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def error_of(error, roots):
    return sel.error_of(error, roots)


def context_of(messages):
    session = SessionRecord(session_id="builder-reader", campaign_id=None, role=WriterRole.BUILDER,
                            provider_id="provider", model_id="model")
    return SessionContext(session=session, messages=tuple(
        SessionMessage(session_id="builder-reader", sequence=index + 1, message_id=f"message-{index}",
                       kind=SessionMessageKind(kind), content=content)
        for index, (kind, content) in enumerate(messages)))


def main() -> None:
    sel.clear_caches()
    archives = {
        "cart": sel.Archive(bcp.bundled_plan("last-cart-through").package_snapshot),
        "debt": sel.Archive(bcp.bundled_plan("a-debt-in-iron").package_snapshot),
        "retained": sel.Archive(bcp.bundled_plan("the-rift-awakening").package_snapshot),
        "current": sel.Archive(bundled.bundled_rift_archive()),
        "riichi": sel.Archive((ROOT / sel.RIICHI).read_bytes()),
    }
    names = ["cart", "debt", "retained", "current", "riichi"]
    with tempfile.TemporaryDirectory() as scratch:
        storage = Path(scratch) / "storage"
        storage.mkdir()
        roots = (Path(scratch), Path(scratch).resolve())
        owner = sel.Owner(archives, names)
        cart = archives["cart"]
        primary = next(entry for entry in cart.lock.entries if entry.role is PackageRole.PRIMARY)
        setting = bcp.bundled_plan("last-cart-through").plan.payload["bindings"]["campaign_setting"]
        catalog = [{"kind": "ruleset", "id": primary.package_id, "version": primary.version,
                    "content_sha256": primary.sha256, "selection_id": "cand-cart",
                    "compatible_settings": [{"id": setting["id"], "candidate_id": "cand-cart"}]},
                   {**bcp.bundled_plan("last-cart-through").catalog_payload(), "bundled": True}]
        captured = thaw(references.capture_content_references(
            owner, storage, catalog, ruleset_id="cand-cart", setting_id=setting["id"],
            setting_candidate_id="cand-cart", plan_id="last-cart-through"))
        profile = save_character_profile(storage, operation_id="reader-profile",
                                         source=json.dumps(profiles.BASES["v2"]))
        # --- launch_references ---
        launch_cases = []
        launch_contexts = {
            "none": [],
            "launch": [("evidence", {"kind": "builder_launch_context", "references": {"candidate_id": "cand-cart"}})],
            "launch_not_mapping": [("evidence", {"kind": "builder_launch_context", "references": ["x"]})],
            "launch_first": [("evidence", {"kind": "builder_launch_context", "references": {"a": 1}}),
                             ("evidence", {"kind": "builder_launch_context", "references": {"b": 2}})],
            "tool_ruleset": [("evidence", {"kind": "builder_tool_result", "tool_name": "select_builder_ruleset",
                                           "result": {"success": True, "references": {"c": 3}}})],
            "tool_latest": [("evidence", {"kind": "builder_tool_result", "tool_name": "select_builder_ruleset",
                                          "result": {"success": True, "references": {"old": 1}}}),
                            ("evidence", {"kind": "builder_tool_result", "tool_name": "select_builder_setting",
                                          "result": {"success": True, "references": {"new": 1}}})],
            "tool_failed": [("evidence", {"kind": "builder_tool_result", "tool_name": "select_builder_setting",
                                          "result": {"success": False, "references": {"d": 4}}})],
            "tool_truthy": [("evidence", {"kind": "builder_tool_result", "tool_name": "select_builder_setting",
                                          "result": {"success": 1, "references": {"d": 4}}})],
            "tool_other": [("evidence", {"kind": "builder_tool_result", "tool_name": "save_plan",
                                         "result": {"success": True, "references": {"e": 5}}})],
            "tool_unhashable": [("evidence", {"kind": "builder_tool_result", "tool_name": ["select_builder_ruleset"],
                                              "result": {"success": True, "references": {"f": 6}}})],
            "tool_no_references": [("evidence", {"kind": "builder_tool_result", "tool_name": "select_builder_ruleset",
                                                 "result": {"success": True, "references": "x"}}),
                                   ("evidence", {"kind": "builder_tool_result", "tool_name": "select_builder_ruleset",
                                                 "result": {"success": True, "references": {"g": 7}}})],
            "user_kind": [("user", {"kind": "builder_launch_context", "references": {"h": 8}})],
        }
        for name, messages in launch_contexts.items():
            try:
                outcome = ["ok", None if (value := references.launch_references(context_of(messages))) is None
                           else thaw(value)]
            except Exception as error:  # every class is behaviour
                outcome = error_of(error, roots)
            launch_cases.append({"name": name, "messages": [[kind, content] for kind, content in messages],
                                 "outcome": outcome})
        # --- candidate_for_references ---
        def changed(edit):
            value = copy.deepcopy(captured)
            edit(value)
            return value
        candidate_refs = {
            "none": None,
            "captured": captured,
            "no_candidate": changed(lambda v: v.update(candidate_id=None)),
            "unknown": changed(lambda v: v.update(candidate_id="cand-missing")),
            "broken": changed(lambda v: v.update(candidate_id="cand-broken")),
            "fewer_packages": changed(lambda v: v["packages"].pop()),
            "other_version": changed(lambda v: v["packages"][0].update(version="0.0.0")),
            "no_packages": changed(lambda v: v.pop("packages")),
            "repeated_package": changed(lambda v: v["packages"].append(copy.deepcopy(v["packages"][0]))),
            "package_missing_id": changed(lambda v: v["packages"][0].pop("id")),
            "package_unhashable": changed(lambda v: v["packages"][0].update(version=["x"])),
            "packages_text": changed(lambda v: v.update(packages="ab")),
            "number_candidate": changed(lambda v: v.update(candidate_id=5)),
        }
        candidate_cases = []
        for name, value in candidate_refs.items():
            sel.clear_caches()
            try:
                found = references.candidate_for_references(owner, value)
                outcome = ["ok", None if found is None else found.candidate_id]
            except Exception as error:  # every class is behaviour
                outcome = error_of(error, roots)
            candidate_cases.append({"name": name, "references": value, "outcome": outcome})
        # --- with_character_examples ---
        example_cases = []
        for name, value, purpose in (
            ("player", captured, "player_character"), ("companion", captured, "companion"),
            ("no_setting", changed(lambda v: [p.update(prefix="extra") for p in v["packages"] if p["prefix"] == "setting"]),
             "player_character"),
            ("no_candidate", candidate_refs["no_candidate"], "player_character"),
            ("mismatched", candidate_refs["other_version"], "player_character"),
            ("prefix_missing", changed(lambda v: v["packages"][0].pop("prefix")), "player_character"),
        ):
            sel.clear_caches()
            try:
                result = thaw(references.with_character_examples(owner, storage, value, purpose=purpose))
                outcome = ["ok", result]
            except Exception as error:  # every class is behaviour
                outcome = error_of(error, roots)
            example_cases.append({"name": name, "references": value, "purpose": purpose, "outcome": outcome})
        with_examples = thaw(references.with_character_examples(owner, storage, captured, purpose="player_character"))
        # --- reader_for_references ---
        reader_refs = {
            "none": None,
            "captured": captured,
            "examples_profile": {**with_examples, "character_profile": thaw(profile.snapshot())},
            "bad_profile": {**captured, "character_profile": {"artifact_id": "x"}},
            "no_plan": changed(lambda v: v.update(plan=None)),
            "plan_text": changed(lambda v: v.update(plan="text")),
            "empty": {},
        }
        reader_cases = []
        for name, value in reader_refs.items():
            sel.clear_caches()
            try:
                reader = references.reader_for_references(owner, value)
            except Exception as error:  # every class is behaviour
                reader_cases.append({"name": name, "references": value, "outcome": error_of(error, roots)})
                continue
            if reader is None:
                reader_cases.append({"name": name, "references": value, "outcome": ["ok", None]})
                continue
            reads = {}
            chosen = [path for path in reader.paths if not path.startswith(("ruleset/", "setting/"))]
            chosen += [path for path in reader.paths if path.startswith(("ruleset/", "setting/"))][:6]
            chosen += ["nowhere/file.txt", "ruleset/"]
            for path in chosen:
                try:
                    reads[path] = ["ok", digest(reader.read(path))]
                except Exception as error:  # every class is behaviour
                    reads[path] = error_of(error, roots)
            reader_cases.append({"name": name, "references": value,
                                 "outcome": ["ok", {"paths": list(reader.paths), "reads": reads}]})
        # --- authored_character_state ---
        declaration = next(iter(cart.declarations[(primary.package_id, primary.version, primary.sha256)]))
        starter = next(iter(sel.starters.bundled_starter_participants(
            storage, package=primary, declaration=declaration,
            campaign_setting=PackageBinding(setting["id"], setting["version"], setting["content_sha256"]))))
        import dataclasses
        authored_cases = []
        for name, calculations in (
            ("declared", None),
            ("none", []),
            ("missing_leaf", [{"set": {"field": "nowhere.deep.leaf"}}]),
            ("scalar_parent", [{"set": {"field": "hp.max"}}]),
            ("bad_target", [{"set": {"field": 5}}]),
            ("bad_set", [{"set": "hp"}]),
            ("top_level", [{"set": {"field": "hp"}}]),
        ):
            chosen = declaration if calculations is None else dataclasses.replace(
                declaration, initial_state_calculations=tuple(calculations))
            try:
                outcome = ["ok", thaw(copying.authored_character_state(starter.entity_state, chosen))]
            except Exception as error:  # every class is behaviour
                outcome = error_of(error, roots)
            authored_cases.append({"name": name, "calculations": None if calculations is None else calculations,
                                   "outcome": outcome})
        # --- copy_compatible_character ---
        binding = PackageBinding(setting["id"], setting["version"], setting["content_sha256"])
        source = save_reusable_participant(
            storage, operation_id="reader-source", role="player_character", summary="A copied hero.",
            authored_profile="A hero worth copying.", participant=starter.participant,
            entity_state=copying.authored_character_state(starter.entity_state, declaration),
            package=primary, declaration=declaration, campaign_setting=binding)
        sparse = dataclasses.replace(source, authored_profile=None)
        recast = dataclasses.replace(source, role="companion")
        copy_cases = []
        for name, item, expected, schema, chosen_declaration in (
            ("same", source, source.content_sha256, None, declaration),
            ("stale", source, "0" * 64, None, declaration),
            ("bad_digest", source, "ABC", None, declaration),
            ("sparse", sparse, sparse.content_sha256, None, declaration),
            ("needs_home", source, source.content_sha256, SCHEMA, declaration),
            ("recast", recast, recast.content_sha256, None, declaration),
            ("no_state_schema", source, source.content_sha256, None,
             dataclasses.replace(declaration, entity_state_schema=None, initial_state_calculations=(),
                                 initial_state_validation=None)),
            ("narrow_schema", source, source.content_sha256, None, dataclasses.replace(
                declaration, schema={"type": "object", "properties": {"name": {"type": "integer"}},
                                     "required": ["name"], "additionalProperties": True})),
        ):
            sel.clear_caches()
            try:
                saved = copying.copy_compatible_character(
                    storage, source=item, expected_content_sha256=expected, operation_id=f"copy-{name}",
                    package=primary, declaration=chosen_declaration, campaign_setting=binding,
                    setting_character_schema=schema)
                outcome = ["ok", {"artifact_id": saved.artifact_id, "content_sha256": saved.content_sha256,
                                  "entity_state": thaw(saved.entity_state)}]
            except Exception as error:  # every class is behaviour
                outcome = error_of(error, roots)
            copy_cases.append({"name": name, "outcome": outcome})
    sel.clear_caches()
    vectors = {"python": ".".join(sys.version.split()[0].split(".")[:2]), "riichi": sel.RIICHI,
               "profile_source": profiles.BASES["v2"], "captured": captured, "launch": launch_cases,
               "candidate": candidate_cases, "examples": example_cases, "reader": reader_cases,
               "authored": authored_cases, "copy": copy_cases, "source_artifact_id": source.artifact_id}
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + NL, encoding="utf-8",
                      newline=NL)
    for group in ("launch", "candidate", "examples", "reader", "authored", "copy"):
        for case in vectors[group]:
            outcome = case["outcome"]
            print(group, case["name"], ascii(outcome[:3] if outcome[0] != "ok" else ["ok"])[:120])


if __name__ == "__main__":
    main()
