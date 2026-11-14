"""The existing neutral string default keeps exact tools, schemas and admission."""

import hashlib
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_prismatic_damage_packets import bind_runtime as bind_prism
from scripts.srd_full.srd_qualified_packet_implementations import bind_runtime as bind_packets
from scripts.srd_full.srd_received_damage_lifecycle import bind_runtime as bind_received
from scripts.srd_full.srd_runtime_defaults import compact_runtime_defaults, restore_tool_defaults
from scripts.srd_full.srd_warding_bond_damage import bind_runtime_consumers as bind_bond

from sylvia_gm.application.package_runtime import (
    PackageRuntimeError,
    _parse_runtime,
    _runtime_parameter_schema,
)
from sylvia_gm.domain._content_schema_runtime import _PARAMETER
from sylvia_gm.domain._runtime_executable_reference import PARAMETER_FIELDS
from sylvia_gm.runtime._runtime import RulesetRuntime
from sylvia_gm.runtime._schema_validator import _validate_tool_params, validate_ruleset_data
from tests.srd_full.test_srd_qualified_packet_implementations import _dream_successor
from tests.srd_full.test_srd_received_damage_lifecycle import PARENT, PARENT_SHA, _nodes

NATIVE = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-runtime-string-defaults/ruleset.yaml")
)
NATIVE_SHA = "8499f04a288bd35c92302c21cd01345c43111c9223b60f91a032af071ac62d07"
SRD = "wotc.srd.5.2.1"


@lru_cache
def _native_pair():
    raw = NATIVE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == NATIVE_SHA
    original = raw.decode()
    compact = compact_runtime_defaults(original)
    return original, compact


@lru_cache
def _compound_pair():
    from scripts.srd_full.srd_retained_nullable_captures import bind_runtime

    raw = PARENT.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == PARENT_SHA
    current = bind_prism(bind_bond(_dream_successor(bind_received(raw.decode()))))
    current = bind_runtime(bind_packets(current))
    return current, compact_runtime_defaults(current)


@pytest.mark.parametrize("value", [None, "", " ", "not-a-type", False, 12])
def test_explicit_invalid_type_still_rejects(value):
    assert _validate_tool_params("explicit", [{"name": "value", "type": value}])
    graph = {
        "sylvia_gm_runtime": {"format": 1, "state_namespace": SRD},
        "tools": {"generic": {"parameters": [{"name": "value", "type": value}], "effects": []}},
    }
    with pytest.raises(PackageRuntimeError):
        _parse_runtime(package_id=SRD, runtime_bytes=yaml.safe_dump(graph).encode(), authoring=True)


def test_missing_type_uses_the_existing_default_with_identical_generic_schema():
    implicit = {"parameters": [{"name": "value"}], "effects": []}
    explicit = {"parameters": [{"name": "value", "type": "string"}], "effects": []}
    assert not _validate_tool_params("implicit", implicit["parameters"])
    assert _tool_schema(implicit) == _tool_schema(explicit)
    assert _PARAMETER["required"] == ["name"]
    assert PARAMETER_FIELDS["required"] == ["name"]
    assert "type" in PARAMETER_FIELDS["optional"]
    before = RulesetRuntime(ruleset_dict={"tools": {"generic": explicit}})
    after = RulesetRuntime(ruleset_dict={"tools": {"generic": implicit}})
    assert before.tools == after.tools
    graph = {
        "sylvia_gm_runtime": {"format": 1, "state_namespace": SRD},
        "tools": {"generic": implicit},
    }
    admitted = _parse_runtime(
        package_id=SRD, runtime_bytes=yaml.safe_dump(graph).encode(), authoring=True
    )
    assert admitted.tools == after.tools


def test_standalone_only_omission_preserves_other_types_inline_anchors_and_aliases():
    original = """tools:
  generic:
    parameters:
    - name: omitted
      type: string
    - name: retained
      type: integer
      required: false
    - {name: inline, type: string}
    - name: anchored
      type: &saved string
    - name: alias
      type: *saved
    effects: []
"""
    compact = compact_runtime_defaults(original)
    assert "      type: string\n" not in compact
    assert "type: integer" in compact and "required: false" in compact
    assert "{name: inline, type: string}" in compact
    assert "type: &saved string" in compact and "type: *saved" in compact
    assert compact_runtime_defaults(compact) == compact
    old = yaml.safe_load(original)
    new = yaml.safe_load(compact)
    assert RulesetRuntime(ruleset_dict=old).tools == RulesetRuntime(ruleset_dict=new).tools
    assert restore_tool_defaults(deepcopy(new)) == restore_tool_defaults(deepcopy(old))


def test_genuine_native_all_tools_schemas_strict_admission_and_regeneration_preserved():
    original, compact = _native_pair()
    old = yaml.load(original, Loader=yaml.CSafeLoader)
    new = yaml.load(compact, Loader=yaml.CSafeLoader)
    before = _parse_runtime(package_id=SRD, runtime_bytes=original.encode(), authoring=True)
    after = _parse_runtime(package_id=SRD, runtime_bytes=compact.encode(), authoring=True)
    assert before.tools == after.tools
    assert before.model_tool_names == after.model_tool_names
    for name in before.tools:
        assert _runtime_parameter_schema(before.tools[name]) == _runtime_parameter_schema(
            after.tools[name]
        )
        assert _tool_schema(old["tools"][name], tool_name=name) == _tool_schema(
            new["tools"][name], tool_name=name
        )
    assert {k: v for k, v in old.items() if k != "tools"} == {
        k: v for k, v in new.items() if k != "tools"
    }
    assert restore_tool_defaults(deepcopy(old)) == restore_tool_defaults(deepcopy(new))
    assert compact_runtime_defaults(compact) == compact


def test_actual282_complete_factored_graph_fits_original_node_cap_with_exact_tools():
    batched, compact = _compound_pair()
    original = yaml.load(batched, Loader=yaml.CSafeLoader)
    actual = yaml.load(compact, Loader=yaml.CSafeLoader)
    before = RulesetRuntime(ruleset_dict=original)
    assert not validate_ruleset_data(actual)
    admitted = _parse_runtime(package_id=SRD, runtime_bytes=compact.encode(), authoring=True)
    assert before.tools == admitted.tools
    assert _nodes(actual) < 500000
    assert restore_tool_defaults(deepcopy(actual)) == restore_tool_defaults(deepcopy(original))
    for name in original["tools"]:
        assert _tool_schema(original["tools"][name], tool_name=name) == _tool_schema(
            actual["tools"][name], tool_name=name
        )
    print("actual282 full compounded runtime nodes", _nodes(original), _nodes(actual))


def test_genuine282_complete_archive_materializes_with_unchanged_public_artifacts(monkeypatch):
    from sylvia_gm.application import bundled_packages

    _, compact = _compound_pair()
    monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: PARENT.parents[1])
    primary, reference = bundled_packages._srd_drafts()
    payloads = {**primary.payloads, "runtime/ruleset.yaml": compact.encode()}
    current = replace(primary, payloads=payloads)
    assert current.record == primary.record
    assert all(
        value == current.payloads[key]
        for key, value in primary.payloads.items()
        if key != "runtime/ruleset.yaml"
    )
    archive = bundled_packages._archive((current, reference))
    assert archive.startswith(b"PK")
    print("genuine282 compacted complete archive bytes", len(archive))
