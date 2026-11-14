"""Final declaration sharing preserves real tools, schemas, text seams and regeneration."""

import hashlib
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts import srd_parameter_declarations as sharing
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_runtime_defaults import compact_runtime_defaults
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.application.package_runtime import _runtime_parameter_schema, _validate_shape
from sylvia_gm.runtime._runtime import RulesetRuntime

CURRENT = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-parameter-declarations/ruleset.yaml")
)
CURRENT_HASH = "1f18ab6bfdf306ed1730403dbd09097b8c5788ddb4877a0caa2416846b59e529"


def _small():
    runtime = {"id": "test.parameter_sharing", "tools": {}}
    for index in range(4):
        runtime["tools"][f"example_{index}"] = {
            "description": "Exact independent declarations.",
            "parameters": [
                {
                    "name": "lasting_state",
                    "resolve": "entity",
                    "required": False,
                    "default": "state",
                },
                {"name": "amount", "type": "integer", "minimum": 0, "default": None},
                {
                    "name": "choices",
                    "type": "array",
                    "required": False,
                    "default": [],
                    "items": {"type": "string", "enum": ["a", "b"]},
                    "model_hidden": True,
                },
            ],
            "pre_conditions": [],
            "effects": [],
        }
    return (
        "# Original tool section remains a regeneration anchor.\n"
        + yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
        + "# Original trailing note.\n"
    )


def test_exact_profitable_descriptors_share_without_changing_defaults_order_or_schemas():
    original = _small()
    data = yaml.load(original, Loader=yaml.CSafeLoader)
    shared = sharing.compact_runtime(original)
    assert shared != original
    pooled = yaml.load(shared, Loader=yaml.CSafeLoader)
    assert set(pooled[sharing.POOL])
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", name) for name in pooled[sharing.POOL])
    assert sharing._resolve(pooled) == data
    assert _load_runtime(shared) == _load_runtime(original)
    restored = sharing.restore_runtime(shared)
    assert yaml.load(restored, Loader=yaml.CSafeLoader) == data
    assert "# Original tool section remains a regeneration anchor." in restored
    assert "# Original trailing note." in restored
    assert sharing.MARKER not in restored
    assert sharing.compact_runtime(restored) == shared
    assert sharing.compact_runtime(shared) == shared
    for name, tool in data["tools"].items():
        assert _tool_schema(tool) == _tool_schema(sharing._resolve(pooled)["tools"][name])


def test_unequal_descriptors_and_identifier_collision_cannot_merge(monkeypatch):
    source = yaml.load(_small(), Loader=yaml.CSafeLoader)
    source["tools"]["example_0"]["parameters"][0]["model_hidden"] = True
    source["tools"]["example_1"]["parameters"][1]["minimum"] = 1
    text = yaml.dump(source, Dumper=yaml.CSafeDumper, sort_keys=False)
    assert (
        sharing._resolve(yaml.load(sharing.compact_runtime(text), Loader=yaml.CSafeLoader))
        == source
    )
    monkeypatch.setattr(sharing, "_identifier", lambda descriptor, key: "param_collision")
    with pytest.raises(ValueError, match="identifier collision"):
        sharing.compact_runtime(_small())


def test_commented_and_anchored_authored_descriptors_remain_in_place():
    text = """# Existing source marker
tools:
  first:
    parameters:
    - &original
      name: amount
      type: integer # Keep the authored type comment.
    effects: []
  second:
    parameters:
    - *original
    effects: []
  third:
    parameters: [{name: amount, type: integer}]
    effects: []
"""
    assert sharing.compact_runtime(text) == text


def test_inverse_supports_mixed_flow_and_anchored_ids_and_preserves_pool_comments():
    text = """# Public tool marker
tools:
  first:
    parameters: [amount, {name: inline}]
    effects: []
  second:
    parameters:
    - &saved amount
    effects: []
  third:
    parameters:
    - *saved
    effects: []
# Authored pool section comment
parameter_declarations:
  # Pool note remains available after expansion.
  amount:
    name: amount
    type: integer # Inline pool note.
    description: 'Literal # hash is a value.'
"""
    original = yaml.load(text, Loader=yaml.CSafeLoader)
    restored = sharing.restore_runtime(text)
    assert yaml.load(restored, Loader=yaml.CSafeLoader) == sharing._resolve(original)
    assert "# Public tool marker" in restored
    assert "# Authored pool section comment" in restored
    assert "# Pool note remains available after expansion." in restored
    assert "# Inline pool note." in restored
    assert "Literal # hash is a value." in restored
    assert "&saved" in restored and "*saved" in restored


def test_inverse_rejects_unknown_or_malformed_reference_before_text_changes():
    for pool in [{"known": {"name": "amount"}}, {"missing": "missing"}]:
        text = yaml.dump({sharing.POOL: pool, "tools": {"example": {"parameters": ["missing"]}}})
        before = deepcopy(text)
        with pytest.raises(ValueError):
            sharing.restore_runtime(text)
        assert text == before


@lru_cache
def _actual_pair():
    encoded = CURRENT.read_bytes()
    assert hashlib.sha256(encoded).hexdigest() == CURRENT_HASH
    original = encoded.decode().replace("\r\n", "\n")
    shared = sharing.compact_runtime(original)
    old = yaml.load(original, Loader=yaml.CSafeLoader)
    pooled = yaml.load(shared, Loader=yaml.CSafeLoader)
    new = sharing._resolve(pooled)
    return original, shared, old, pooled, new


def test_every_actual_289_tool_schema_graph_and_regeneration_seam_is_unchanged():
    original, shared, before, pooled, after = _actual_pair()
    assert before == after
    assert _load_runtime(original) == _load_runtime(shared)
    old, new = RulesetRuntime(ruleset_dict=before), RulesetRuntime(ruleset_dict=pooled)
    assert old.tools == new.tools
    assert old.model_tool_names == new.model_tool_names
    assert old.procedures == new.procedures
    assert len(old.tools) == 1584
    for name in before["tools"]:
        assert _tool_schema(before["tools"][name]) == _tool_schema(after["tools"][name])
        assert _runtime_parameter_schema(old.tools[name]) == _runtime_parameter_schema(
            new.tools[name]
        )
    _validate_shape(pooled)
    _validate_shape(after)
    original_nodes = sharing._nodes(sharing._document(original))
    shared_nodes = sharing._nodes(sharing._document(shared))
    assert original_nodes == 530456
    assert shared_nodes < 500000
    assert original_nodes - shared_nodes > 55000
    restored = sharing.restore_runtime(shared)
    assert yaml.load(restored, Loader=yaml.CSafeLoader) == before

    def comments(text):
        return [line for line in text.splitlines() if line.lstrip().startswith("#")]

    assert comments(restored) == comments(original)
    assert sharing.compact_runtime(restored) == shared
    assert sharing.compact_runtime(shared) == shared
    # Source emitters receive defaults only after the parameter IDs have been expanded.
    assert _load_runtime(compact_runtime_defaults(restored)) == _load_runtime(shared)
