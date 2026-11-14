"""Static native-owner proofs; paid/runtime journeys are deliberately separate."""

import os
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_attack_rider_d20 import (
    CHECK_EXPORTS,
    FINDING_PARAMETER,
    OWNERS,
    _attack_additions,
    _check_additions,
    _check_group,
    _emit,
    _finding_preconditions,
    _finding_prelude,
    bind_tools,
    restore_tools,
)

NATIVE_SOURCE = Path(
    os.environ.get(
        "FC14_RIDER_D20_NATIVE_SOURCE",
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/fc14-rider-d20-native-source/native-check-tools.yaml"),
    )
)


def _native():
    tools = yaml.safe_load(NATIVE_SOURCE.read_text(encoding="utf-8"))["tools"]
    # Match the existing package reader's restored default, without loading a runtime.
    for tool in tools.values():
        tool.setdefault("pre_conditions", [])
    return tools


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _dice(tool):
    return [node for node in _walk(tool["effects"]) if "dice" in node]


def test_native_rider_binding_is_exactly_reversible_and_adds_no_dice_or_costs():
    original = _native()
    parent = deepcopy(original)
    changed = bind_tools(original)
    assert original == parent
    assert set(changed) == set(OWNERS)
    assert restore_tools(changed) == original
    assert bind_tools(changed) == changed
    for name in OWNERS:
        assert _dice(changed[name]) == _dice(original[name])
        assert changed[name]["metadata"] == original[name]["metadata"]
        assert changed[name]["description"] == original[name]["description"]
        if name != "resolve_srd_attack_conditions":
            assert changed[name]["parameters"] == original[name]["parameters"] + [FINDING_PARAMETER]
            assert changed[name]["pre_conditions"] == (
                original[name].get("pre_conditions", []) + _finding_preconditions(name)
            )


def test_hex_and_mark_flags_only_join_the_existing_native_check_path_before_roll():
    original = _native()
    changed = bind_tools(original)
    source = original["d20_test"]
    bound = changed["d20_test"]
    assert _check_group(bound) == _check_group(source) + _check_additions()
    hook = _check_additions()[-1]
    assert hook["condition"].startswith("kind == 'ability_check' and ")
    call = next(e for e in hook["on_true"] if e.get("call") == "check_srd_attack_rider_d20")
    assert call["params"] == {
        "actor_state": "{_actor_state_id}",
        "kind": "ability_check",
        "ability": "{ability}",
        "skill": "{_rider_native_skill}",
        "subject_state_id": "{_rider_native_subject}",
    }
    assert call["export_context"] == {field: field for field in CHECK_EXPORTS}
    assert all("saving_throw" not in str(e) for e in _check_additions())
    # The exact same existing native parent condition excludes every save from the additions.
    for index, original_effect in enumerate(source["effects"]):
        if original_effect.get("condition") == "actor_state != null" and any(
            e.get("condition") == "kind == 'ability_check' or kind == 'attack_roll'"
            for e in original_effect.get("on_true", [])
        ):
            continue
        assert bound["effects"][index] == original_effect


def test_search_forwards_only_actual_finding_identity_before_its_one_native_roll():
    original = _native()
    changed = bind_tools(original)
    for name in ("ability_check", "search_srd_action"):
        source_call = next(e for e in original[name]["effects"] if e.get("call") == "d20_test")
        effects = changed[name]["effects"]
        call = next(e for e in effects if e.get("call") == "d20_test")
        index = effects.index(call)
        prelude = _finding_prelude(name)
        assert effects[index - len(prelude) : index] == prelude
        assert prelude[0]["set"]["value"] is None
        assert call["params"] == {
            **source_call["params"],
            "finding_target": "{_rider_native_finding_id}",
        }
        assert call["export_context"] == {
            **source_call["export_context"],
            **{field: field for field in CHECK_EXPORTS},
        }
    search = changed["search_srd_action"]
    assert _finding_preconditions("search_srd_action")[-1] in search["pre_conditions"]
    assert _finding_prelude("search_srd_action")[-1] == {
        "condition": "target_state != null",
        "on_true": [{"set": {"field": "_rider_native_finding_id", "value": "_target_state_id"}}],
    }


def test_shining_preserves_actual_sight_disadvantage_and_only_adds_source_advantage():
    original = _native()["resolve_srd_attack_conditions"]
    bound = bind_tools(_native())["resolve_srd_attack_conditions"]
    assert bound["parameters"] == original["parameters"]
    assert bound["pre_conditions"] == original["pre_conditions"]
    assert bound["effects"] == original["effects"] + _attack_additions()
    assert _attack_additions()[0] == {"set": {"field": "_rider_attack_advantage", "value": False}}
    call = _attack_additions()[1]["on_true"][0]
    assert call["params"] == {"target_state": "{_target_state_id}"}
    assert call["export_context"] == {"_rider_attack_advantage": "_rider_attack_advantage"}
    assert "can_see" not in str(_attack_additions())
    assert "_condition_disadvantage" not in str(_attack_additions())


def test_changed_native_or_owned_hooks_fail_without_mutating_parent():
    original = _native()
    wrong = deepcopy(original)
    group = _check_group(wrong["d20_test"])
    native = next(
        e
        for e in wrong["d20_test"]["effects"]
        if any(child.get("on_true") is group for child in e.get("on_true", []))
    )
    native["condition"] = "actor_state != null and altered_owner == true"
    with pytest.raises(ValueError, match="actor-state guard"):
        bind_tools(wrong)
    bound = bind_tools(original)
    unknown = deepcopy(bound)
    _check_group(unknown["d20_test"])[-1]["on_true"][-1]["set"]["value"] = "true"
    before = deepcopy(unknown)
    with pytest.raises(ValueError, match="Unknown rider D20 source hook"):
        bind_tools(unknown)
    assert unknown == before


def test_native_fragment_emission_keeps_surrounding_bytes_and_round_trips_exactly():
    source = NATIVE_SOURCE.read_text(encoding="utf-8")
    prefix = "# Literal native owner fixture, with surrounding source kept verbatim.\n"
    suffix = "# Unowned trailing source stays unchanged.\n"
    changed = bind_tools(_native())
    rendered = _emit(prefix + source + suffix, changed)
    assert rendered.startswith(prefix)
    assert rendered.endswith(suffix)
    assert yaml.safe_load(rendered)["tools"] == changed
    assert _emit(rendered, bind_tools(changed)) == rendered
    restored = yaml.safe_load(_emit(rendered, restore_tools(changed)))["tools"]
    assert restored == _native()
