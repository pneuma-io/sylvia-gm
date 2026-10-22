"""Fire Shield light consumes exact saved sources without advanced spell declarations."""

import pytest

from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_result_cards import SRD
from tests.srd_full.test_srd_spell_wave_defenses import _case


@pytest.mark.parametrize("mode", ["fire_shield_warm", "fire_shield_chill"])
def test_actor_spell_light_source_subject_and_expiry(mode):
    state, _, _ = _case("sanctuary", targets=["guard"])
    state[f"{SRD}.state"]["lasting_tick"] = 0
    _run(
        "install_srd_actor_spell_modifier",
        state,
        target_state="guard",
        caster_state="hero",
        lasting_state="state",
        spell="fire_shield",
        mode=mode,
        save_dc=19,
        duration_rounds=100,
        concentration=False,
    )

    def light(subject):
        return _run(
            "check_srd_actor_spell_light", state, actor_state=subject, lasting_state="state"
        ).result["context"]

    active = light("guard")
    assert (active["_actor_spell_bright_light_feet"], active["_actor_spell_dim_light_feet"]) == (
        10,
        20,
    )
    assert light("other")["_actor_spell_bright_light_feet"] == 0
    state[f"{SRD}.state"]["lasting_tick"] += 200
    assert light("guard")["_actor_spell_dim_light_feet"] == 0
