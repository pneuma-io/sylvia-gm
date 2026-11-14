"""Default omission preserves authentic parsed tools, schemas and subsequent generation."""

import hashlib
from functools import lru_cache
from pathlib import Path

import yaml
from scripts.srd_full.srd_runtime_defaults import compact_runtime_defaults
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.application.package_runtime import _runtime_parameter_schema
from sylvia_gm.runtime._runtime import RulesetRuntime
from sylvia_gm.runtime._schema_validator import validate_ruleset

NATIVE = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-runtime-defaults/ruleset.yaml")
)


@lru_cache
def _actual_pair():
    content = NATIVE.read_bytes()
    assert hashlib.sha256(content).hexdigest() == (
        "8499f04a288bd35c92302c21cd01345c43111c9223b60f91a032af071ac62d07"
    )
    before = content.decode().replace("\r\n", "\n")
    after = compact_runtime_defaults(before)
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    return before, after, yaml.load(before, Loader=loader), yaml.load(after, Loader=loader)


def test_every_actual_native_parsed_tool_schema_and_effect_remains_identical():
    before, after, old_data, new_data = _actual_pair()
    old, new = RulesetRuntime(ruleset_dict=old_data), RulesetRuntime(ruleset_dict=new_data)
    assert old.tools == new.tools
    assert old.model_tool_names == new.model_tool_names
    assert {k: v for k, v in old_data.items() if k != "tools"} == {
        k: v for k, v in new_data.items() if k != "tools"
    }
    for name in old.tools:
        assert _runtime_parameter_schema(old.tools[name]) == _runtime_parameter_schema(
            new.tools[name]
        )
    assert after != before
    assert not validate_ruleset(after)


def test_final_compaction_repeats_and_emitters_receive_exact_original_defaults():
    before, after, old_data, new_data = _actual_pair()
    assert compact_runtime_defaults(after) == after
    assert _load_runtime(after) == _load_runtime(before)
    for name, tool in old_data["tools"].items():
        if tool.get("pre_conditions") == []:
            assert "pre_conditions" not in new_data["tools"][name]
        for old, new in zip(tool["parameters"], new_data["tools"][name]["parameters"], strict=True):
            if old.get("required", True) is False:
                assert new["required"] is False
