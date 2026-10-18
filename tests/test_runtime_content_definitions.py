from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from sylvia_gm.application.package_runtime import PackageRuntimeError, validate_package_runtime
from sylvia_gm.application.schema_admission import validate_json_schema
from sylvia_gm.domain._content_schema_runtime import (
    PACKAGE_RUNTIME_SCHEMA,
    SCHEMA_ADMISSION_SUBSET_SCHEMA,
    SCHEMA_SUBSET_EXAMPLE,
)
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.content_validation import validate_content_shape
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import StatePredicate, ToolDeclaration
from sylvia_gm.runtime._runtime import RulesetRuntime


def _validate_installed_runtime(package_root: Path) -> None:
    record = json.loads((package_root / "package-record.json").read_text(encoding="utf-8"))
    declarations = tuple(
        ToolDeclaration(
            name=item["name"],
            capability=item["capability"],
            input_schema=owned_payload(item["input_schema"]),
            state_predicates=tuple(
                StatePredicate(
                    namespace=value["namespace"],
                    key=value["key"],
                    equals=value["equals"],
                    negated=value.get("negated", False),
                )
                for value in item["state_predicates"]
            ),
            turn_statuses=tuple(item["turn_statuses"]),
            writers=tuple(item["writers"]),
        )
        for item in record["tools"]
    )
    runtime = (package_root / "runtime" / "ruleset.yaml").read_bytes()
    raw = yaml.safe_load(runtime)
    Draft202012Validator(PACKAGE_RUNTIME_SCHEMA).validate(raw)
    validate_package_runtime(
        package_id=record["id"], declarations=declarations, runtime_bytes=runtime
    )


def test_runtime_definition_covers_neutral_and_srd_admitted_runtimes() -> None:
    root = Path.cwd()
    _validate_installed_runtime(root / "tests" / "fixtures" / "rulesets" / "signal-and-static")
    _validate_installed_runtime(root / "rulesets" / "srd-lite")


def test_schema_subset_definition_matches_admission_and_rejects_unknown_keyword() -> None:
    Draft202012Validator(SCHEMA_ADMISSION_SUBSET_SCHEMA).validate(SCHEMA_SUBSET_EXAMPLE)
    validate_json_schema(SCHEMA_SUBSET_EXAMPLE)
    invalid = {**SCHEMA_SUBSET_EXAMPLE, "patternProperties": {}}
    assert list(Draft202012Validator(SCHEMA_ADMISSION_SUBSET_SCHEMA).iter_errors(invalid))
    invalid_description = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": {"invented": True}},
        },
    }
    assert list(
        Draft202012Validator(SCHEMA_ADMISSION_SUBSET_SCHEMA).iter_errors(invalid_description)
    )
    with pytest.raises(CommandRejected, match="invalid description"):
        validate_json_schema(invalid_description)


def test_runtime_unknown_root_and_parameter_fields_reject_with_paths() -> None:
    with pytest.raises(PackageRuntimeError) as rejected:
        validate_package_runtime(
            package_id="example.rules",
            declarations=(),
            runtime_bytes=yaml.safe_dump(
                {
                    "sylvia_gm_runtime": {"format": 1, "state_namespace": "example.rules"},
                    "invented": True,
                    "tools": {
                        "act": {"parameters": [{"name": "x", "type": "integer", "guess": 1}]}
                    },
                }
            ).encode(),
        )
    assert {item.path for item in rejected.value.diagnostics} == {
        "$.invented",
        "$.tools.act.parameters[0].guess",
    }


def test_runtime_admission_rejects_duplicate_yaml_fields() -> None:
    with pytest.raises(PackageRuntimeError, match="YAML is invalid"):
        validate_package_runtime(
            package_id="example.rules",
            declarations=(),
            runtime_bytes=(
                b"sylvia_gm_runtime: {format: 1, state_namespace: example.rules}\n"
                b"tools: {}\ntools: {}\n"
            ),
        )


def test_authored_range_and_procedure_details_survive_runtime_loading() -> None:
    raw = {
        "sylvia_gm_runtime": {"format": 1, "state_namespace": "example.rules"},
        "tools": {"reach": {"range": {"type": "band", "value": "close"}}},
        "procedures": {
            "ready": {
                "trigger": "on_ready",
                "description": "Prepare play.",
                "narrative": {"template": "Ready."},
                "steps": [],
            }
        },
    }
    validate_content_shape("package_runtime", raw, file="runtime/ruleset.yaml")
    loaded = RulesetRuntime(ruleset_dict=raw)
    assert loaded.tools["reach"].range == {"type": "band", "value": "close"}
    assert loaded.procedures["ready"].description == "Prepare play."
    assert loaded.procedures["ready"].narrative == {"template": "Ready."}


@pytest.mark.parametrize(
    "field,value,path",
    [
        ("preconditions", [], "$.tools.act.preconditions"),
        ("result", {}, "$.tools.act.result"),
        ("range", {"type": "band", "value": "close", "unit": "feet"}, "$.tools.act.range.unit"),
    ],
)
def test_ignored_tool_fields_reject_at_package_admission(field, value, path) -> None:
    raw = {
        "sylvia_gm_runtime": {"format": 1, "state_namespace": "example.rules"},
        "tools": {"act": {field: value}},
    }
    with pytest.raises(PackageRuntimeError) as rejected:
        validate_package_runtime(
            package_id="example.rules",
            declarations=(),
            runtime_bytes=yaml.safe_dump(raw).encode(),
        )
    assert path in {item.path for item in rejected.value.diagnostics}
