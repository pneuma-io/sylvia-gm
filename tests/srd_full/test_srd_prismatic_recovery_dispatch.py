"""Prismatic removal retains source registration through shared Recovery wrappers."""

from copy import deepcopy

import pytest
from scripts.srd_full.srd_control_behaviors import _call, _walk, _when
from scripts.srd_full.srd_prismatic_wall_sources import _bind_recovery
from scripts.srd_full.srd_spell_recovery import _tools
from scripts.srd_full.srd_spell_removal_adoption import _bind_selected_recovery


@pytest.mark.parametrize("wrapped", [False, True])
def test_prismatic_choice_survives_existing_recovery_dispatch(wrapped):
    tools = _tools()
    _bind_selected_recovery(tools)
    recovery = tools["cast_srd_recovery"]
    if wrapped:
        recovery["effects"] = [
            _when(
                "spell == 'dispel_magic' and recovery_choice == 'simulacrum'",
                [_call("dispel_srd_simulacrum_source", {})],
                recovery["effects"],
            )
        ]
    before = deepcopy(recovery)
    _bind_recovery(recovery)
    assert {k: v for k, v in recovery.items() if k != "effects"} == {
        k: v for k, v in before.items() if k != "effects"
    }
    retained = deepcopy(recovery["effects"][:-1])
    if wrapped:
        assert retained[0]["on_true"] == before["effects"][0]["on_true"]
    for effect in _walk(retained):
        if effect.get("set", {}).get("field") in {
            "_recovery_source_intents",
            "_recovery_source_owners",
        }:
            assert effect["set"]["value"].count("'prismatic_wall'") == 1
            effect["set"]["value"].remove("'prismatic_wall'")
    assert retained == before["effects"]
    branch = recovery["effects"][-1]
    assert branch["condition"] == "spell == 'dispel_magic' and recovery_choice == 'prismatic_wall'"
    assert branch["on_true"][0]["call"] == "dispel_srd_prismatic_wall_owned_source"
    assert branch["on_true"][0]["params"]["source_id"] == "{recovery_source_id}"
    once = deepcopy(recovery)
    _bind_recovery(recovery)
    assert recovery == once
