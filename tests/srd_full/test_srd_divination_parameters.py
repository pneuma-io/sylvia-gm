"""Named divination declaration trimming preserves source bodies and actual public composition."""

from copy import deepcopy
from functools import lru_cache
from random import Random

import pytest
from scripts.srd_full.srd_divination_parameters import OWNERS, _unused, bind_runtime
from scripts.srd_full.srd_retained_spell_parameters import _unused_parameters
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_special_damage as special
from tests import test_srd_spell_wave4_divination as divination
from tests.srd_full.test_srd_exploration_cast_delegate import _compact


def _calls(value):
    if isinstance(value, list):
        for item in value:
            yield from _calls(item)
    elif isinstance(value, dict):
        call = value.get("call")
        if isinstance(call, str) and call in OWNERS:
            yield call, value
        elif isinstance(call, dict) and call.get("call", call.get("name")) in OWNERS:
            yield call.get("call", call.get("name")), call
        for item in value.values():
            yield from _calls(item)


@lru_cache
def _trimmed():
    text = bind_runtime(_compact()[0].decode())
    runtime = _load_runtime(text)
    assert not validate_ruleset_data(runtime)
    return text.encode(), runtime["definitions"]["spells"]


def test_named_private_trim_preserves_every_source_effect_and_all_public_declarations():
    before = _load_runtime(_compact()[0])
    text = _trimmed()[0].decode()
    after = _load_runtime(text)
    assert bind_runtime(text) == text
    unused = {name: _unused(before["tools"][name]) for name in OWNERS}
    for name, original in before["tools"].items():
        result = deepcopy(after["tools"][name])
        old_calls, new_calls = list(_calls(original)), list(_calls(result))
        assert len(old_calls) == len(new_calls)
        for (old_name, old), (new_name, new) in zip(old_calls, new_calls, strict=True):
            assert old_name == new_name
            assert new["params"] == {
                key: value for key, value in old["params"].items() if key not in unused[old_name]
            }
            new["params"] = deepcopy(old["params"])
        if name in OWNERS:
            assert result["parameters"] == [
                parameter
                for parameter in original["parameters"]
                if parameter["name"] not in unused[name]
            ]
            result["parameters"] = deepcopy(original["parameters"])
        assert result == original
    assert {key: value for key, value in before.items() if key != "tools"} == {
        key: value for key, value in after.items() if key != "tools"
    }


@pytest.mark.parametrize("spell", ["mind_spike", "message"])
def test_actual_public_divination_call_keeps_complete_source_damage_and_result(monkeypatch, spell):
    state, data, params = divination._case(spell)
    original, trimmed = deepcopy(state), deepcopy(state)
    special.compiled_special.__wrapped__(monkeypatch, _compact())
    monkeypatch.setattr(dice, "_rng", Random(1))
    before = budget._run("cast_spell_outside_combat", original, participant_data=data, **params)
    special.compiled_special.__wrapped__(monkeypatch, _trimmed())
    monkeypatch.setattr(dice, "_rng", Random(1))
    after = budget._run("cast_spell_outside_combat", trimmed, participant_data=data, **params)
    assert before.result["status"] == after.result["status"] == "committed"
    assert original == trimmed
    assert before.mutations == after.mutations
    assert before.result == after.result


def test_source_adoption_references_retain_their_late_private_fields():
    tool = deepcopy(_load_runtime(_compact()[0])["tools"]["cast_srd_mind_spike"])
    fields = ("spell_cast_id", "spell_cast_owner", "actual_spell_level")
    for name in fields:
        if not any(parameter["name"] == name for parameter in tool["parameters"]):
            tool["parameters"].append({"name": name, "type": "string", "required": False})
    tool["effects"].append(
        {
            "call": "adopt_actual_source",
            "params": {
                "cast": "{spell_cast_id}",
                "owner": "{_spell_cast_owner_id}",
                "level": "{actual_spell_level}",
            },
        }
    )
    assert not set(fields) & _unused_parameters(tool)
