"""Pure checks of the real Lite annotation block and portable tool consumers."""

import ast
import json
import os
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from sylvia_gm.application.package_runtime import package_tool_description
from sylvia_gm.domain.package_library import content_root_for_record, manifest_from_record

SOURCE = Path(os.environ.get("SYLVIA_LITE_SOURCE_ROOT", Path(__file__).resolve().parents[1]))


def annotate(runtime, record):
    """Execute the production annotation slice, without composition or native work."""
    path = SOURCE / "scripts/srd_lite_build.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    composer = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "compose"
    )
    baseline_context = next(node for node in composer.body if isinstance(node, ast.With))
    start = next(
        index
        for index, node in enumerate(baseline_context.body)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "repertoire_note"
            for target in node.targets
        )
    )
    annotation = baseline_context.body[start : start + 2]
    assert isinstance(annotation[1], ast.For), (
        "The production annotation slice must remain explicit"
    )
    code = compile(
        ast.fix_missing_locations(ast.Module(body=annotation, type_ignores=[])), str(path), "exec"
    )
    exec(code, {"runtime": runtime, "record": record})


@pytest.fixture
def record():
    with zipfile.ZipFile(SOURCE.parent / "inputs/baseline.sylvia-gm-package") as archive:
        manifest = json.loads(archive.read("manifest.json"))
    return next(row for row in manifest["packages"] if row["id"] == "sylvia.srd-lite.bg3")


@pytest.fixture
def runtime():
    return {
        "sylvia_gm_runtime": {"format": 1, "state_namespace": "sylvia.srd-lite.bg3"},
        "features": {},
        "abilities": {},
        "tools": {
            "level_up_srd_character": {
                "description": "Retain earned progression requirements.",
                "parameters": [],
                "effects": [],
            },
            "validate_srd_spell_grants": {
                "description": "Require complete legal spell grants.",
                "parameters": [],
                "effects": [],
            },
            "other_tool": {"description": "Unaffected tool.", "parameters": [], "effects": []},
        },
    }


def parse_record(record):
    record["content_sha256"] = content_root_for_record(record)
    return manifest_from_record(record, format_version=4)


def test_real_annotation_preserves_portable_tool_contract(record, runtime):
    before = deepcopy(record)
    annotate(runtime, record)
    parsed = parse_record(record)
    assert record == before
    assert any(tool.name == "level_up_srd_character" for tool in parsed.tools)
    assert not any(tool.name == "validate_srd_spell_grants" for tool in parsed.tools)


def test_runtime_guidance_retains_mechanics_and_other_descriptions(record, runtime):
    before = deepcopy(runtime)
    annotate(runtime, record)
    for name in ("level_up_srd_character", "validate_srd_spell_grants"):
        assert runtime["tools"][name]["description"].startswith(
            before["tools"][name]["description"] + "\n"
        )
        assert (
            "Full complete-grant and earned-level requirements remain unchanged"
            in runtime["tools"][name]["description"]
        )
        assert (
            "Pending profiles cannot fill missing choices" in runtime["tools"][name]["description"]
        )
        assert {
            key: value for key, value in runtime["tools"][name].items() if key != "description"
        } == {key: value for key, value in before["tools"][name].items() if key != "description"}
    assert runtime["tools"]["other_tool"] == before["tools"]["other_tool"]


def test_real_catalog_description_consumer_reads_runtime_guidance(record, runtime):
    tool = next(row for row in record["tools"] if row["name"] == "level_up_srd_character")
    tool["input_schema"] = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }
    annotate(runtime, record)
    parsed = parse_record(record)
    declaration = next(tool for tool in parsed.tools if tool.name == "level_up_srd_character")
    description = package_tool_description(
        package_id=parsed.package_id,
        declaration=declaration,
        runtime_bytes=yaml.safe_dump(runtime).encode("utf-8"),
    )
    assert description == runtime["tools"]["level_up_srd_character"]["description"]


@pytest.mark.parametrize("field", ["description", "unknown_tool_field"])
def test_portable_parser_still_rejects_undeclared_tool_fields(record, field):
    tool = next(row for row in record["tools"] if row["name"] == "level_up_srd_character")
    tool[field] = "Unsupported manifest metadata"
    with pytest.raises(ValueError, match="^Package tool fields do not match the portable format$"):
        parse_record(record)
