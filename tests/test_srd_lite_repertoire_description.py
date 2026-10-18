"""Pure checks of captured manifest shape and the actual description producer."""

import ast
import json
import os
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest

from sylvia_gm.application.package_materialization import _participant_artifact_hashes
from sylvia_gm.domain.content_validation import ContentShapeError, validate_content_shape
from sylvia_gm.domain.package_library import content_root_for_record, manifest_from_record

SOURCE = Path(os.environ.get("SYLVIA_LITE_SOURCE_ROOT", Path(__file__).resolve().parents[1]))
RETAINED = SOURCE.parent
HELPERS = {}
exec(compile((SOURCE / "scripts/srd_lite_repertoire.py").read_text(), "repertoire", "exec"), HELPERS)


@pytest.fixture
def runtime():
    """Read only the existing JSON-in-YAML definition seams, without loading a graph."""
    tree = ast.parse((SOURCE / "scripts/srd_lite_catalogue.py").read_text())
    selected = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "SELECTED" for target in node.targets)
    )
    definitions = {"spell_grants": {}, "spell_casting": {}}
    section = None
    with (SOURCE / "rulesets/srd-lite/runtime/ruleset.yaml").open(encoding="utf-8") as stream:
        for line in stream:
            if line.startswith("  ") and not line.startswith("   "):
                section = line.strip().split(":", 1)[0]
            if section in definitions and line.startswith("    ") and ": {" in line:
                name, value = line.strip().split(": ", 1)
                definitions[section][name] = json.loads(value)
    daggers = ast.parse((SOURCE / "scripts/srd_lite_daggers.py").read_text())
    definitions["spell_casting"]["cloud_of_daggers"] = next(
        ast.literal_eval(node.value)
        for node in ast.walk(daggers)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Dict)
        and any(isinstance(key, ast.Constant) and key.value == "classes" for key in node.value.keys)
        and any(
            isinstance(target, ast.Subscript)
            and isinstance(target.slice, ast.Constant)
            and target.slice.value == "cloud_of_daggers"
            for target in node.targets
        )
    )
    ward = ast.parse((SOURCE / "scripts/srd_lite_blade_ward_support.py").read_text())
    definitions["spell_casting"]["blade_ward"] = next(
        ast.literal_eval(node.value)
        for node in ward.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "HEADER" for target in node.targets)
    )
    definitions["spell_casting"] = {
        name: row for name, row in definitions["spell_casting"].items() if name in selected
    }
    assert set(definitions["spell_casting"]) == selected
    return {"definitions": definitions}


@pytest.fixture
def captured_record():
    path = RETAINED / "attempts/generation-013/product/package-record.json"
    return json.loads(path.read_bytes())


def completed_record(record):
    result = deepcopy(record)
    with zipfile.ZipFile(RETAINED / "inputs/baseline.sylvia-gm-package") as archive:
        baseline = json.loads(archive.read("manifest.json"))
    result["files"] = next(row for row in baseline["packages"] if row["role"] == "primary")[
        "files"
    ]
    _participant_artifact_hashes(result)
    result["content_sha256"] = content_root_for_record(result)
    manifest_from_record(result, format_version=4)
    return result


def description_from_real_producer(prefix, runtime):
    tree = ast.parse((SOURCE / "scripts/srd_lite_character_validation.py").read_text())
    composer = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "compose_character_validation"
    )
    annotations = [
        node for node in composer.body
        if isinstance(node, ast.AugAssign)
        and isinstance(node.target, ast.Subscript)
        and isinstance(node.target.value, ast.Name)
        and node.target.value.id == "program"
        and isinstance(node.target.slice, ast.Constant)
        and node.target.slice.value == "description"
    ]
    program = {"description": prefix}
    namespace = {**HELPERS, "program": program, "runtime": runtime}
    code = compile(ast.Module(body=[annotations[-1]], type_ignores=[]), "real producer", "exec")
    exec(code, namespace)
    return program["description"]


def test_actual_producer_fits_real_portable_manifest_without_other_changes(runtime, captured_record):
    detail = HELPERS["repertoire_guidance"](runtime)
    original = captured_record["participant_artifacts"][0]["initial_state_validation"]["description"]
    assert original.endswith("\n" + detail)
    prefix = original[: -len("\n" + detail)]
    fixed = deepcopy(captured_record)
    description = description_from_real_producer(prefix, runtime)
    fixed["participant_artifacts"][0]["initial_state_validation"]["description"] = description
    assert description.startswith(prefix + "\n")
    assert len(original) == 4668 and len(description) <= 2000
    validate_content_shape("package_archive", {"format": 4, "packages": [completed_record(fixed)]})
    fixed["participant_artifacts"][0]["initial_state_validation"]["description"] = original
    assert fixed == captured_record


def test_captured_failure_and_unchanged_length_owner(captured_record):
    value = {"format": 4, "packages": [completed_record(captured_record)]}
    with pytest.raises(ContentShapeError) as failure:
        validate_content_shape("package_archive", value)
    assert [(row.code, row.path) for row in failure.value.diagnostics] == [
        ("content_maxLength_invalid",
         "$.packages[0].participant_artifacts[0].initial_state_validation.description")
    ]


def test_summary_preserves_actual_counts_and_mandatory_shortfalls(runtime):
    before = deepcopy(runtime)
    summary = HELPERS["repertoire_summary"](runtime)
    for source, policy in sorted(runtime["definitions"]["spell_grants"].items()):
        if policy["class_id"] != source:
            continue
        for level in (1, 2):
            if level < policy["minimum"]:
                continue
            row = HELPERS["repertoire_choices"](runtime, source, level)
            required = lambda count: str(count) if isinstance(count, int) and count >= 0 else "policy"
            assert (
                f"{source} level {level}: cantrips {len(row['cantrips'])}/"
                f"{required(row['required_cantrips'])}; spells {len(row['spells'])}/"
                f"{required(row['required_spells'])}."
            ) in summary
    for identity in ("hunters_mark", "speak_with_animals", "divine_smite"):
        assert f"mandatory {identity} unavailable" in summary
    assert "wizard level 2: spellbook 7/8 distinct eligible entries" in summary
    assert "never duplicate choices or use pending spells" in summary
    assert "not character acceptance" in summary
    assert "retained rules govern every later earned level" in summary
    assert runtime == before


def test_variable_requirements_stay_rule_dependent(runtime):
    runtime["definitions"]["spell_grants"]["wizard"]["complete_spells"][0] = -1
    runtime["definitions"]["spell_grants"]["cleric"]["complete_cantrips"][0] = None
    summary = HELPERS["repertoire_summary"](runtime)
    assert "cleric level 1: cantrips 1/policy" in summary
    assert "wizard level 1: cantrips 2/3; spells 7/policy" in summary
    assert "/-1" not in summary and "/None" not in summary


def test_detailed_guidance_remains_complete(runtime, captured_record):
    detail = HELPERS["repertoire_guidance"](runtime)
    description = captured_record["participant_artifacts"][0]["initial_state_validation"]["description"]
    assert description.endswith("\n" + detail)
    assert len(detail) == 4249
    assert "eligible cantrips blade_ward, true_strike" in detail
    assert "book capacity shortfall 1" in detail
