"""A real stored caster Curse joins printed transformed attack damage in one packet."""

from functools import lru_cache
from pathlib import Path
from random import Random

import pytest
from scripts.srd_full.srd_spell_damage_consumers import extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests.srd_full.test_srd_spell_damage_consumers import SRD, _record_rolls, _spell_case

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "seed,immune,resisted", [(0, False, False), (5, True, False), (5, False, True)]
)
def test_actual_transformed_poison_and_curse_keep_one_qualified_damage_packet(
    monkeypatch, seed, immune, resisted
):
    from tests import test_srd_spell_wave6_transformations as fixture

    original = (ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text()
    corrected = extend_runtime(original)
    assert extend_runtime(corrected) == corrected
    runtime = _load_runtime(corrected)

    @lru_cache
    def mechanic(name):
        return materialize_package_mechanic(
            package_id=SRD,
            package_version="0.3.1",
            package_sha256="b" * 64,
            runtime_bytes=corrected.encode(),
            declaration=ToolDeclaration(
                name=name,
                capability="dnd.srd.5.2.1.action-economy",
                input_schema=owned_payload(
                    budget._tool_schema(runtime["tools"][name], tool_name=name)
                ),
                writers=("dm",),
                turn_statuses=("open",),
                state_predicates=(),
            ),
        )

    monkeypatch.setattr(budget, "_mechanic", mechanic)
    transform = fixture._cast

    def curse_then_transform(state, data, parameters):
        del state[f"{SRD}.entities"]["guard"]
        budget._run(
            "prepare_srd_creature",
            state,
            creature_id="guard",
            name="Guard",
            source="Synthetic source caster before its actual transformation.",
            armor_class=12,
            hit_points=25,
            speed=30,
            initiative_modifier=0,
            size_rank=2,
            constitution_save=6,
            wisdom_save=0,
            creature_type="humanoid",
            spellcasting_ability="wisdom",
            spellcasting_modifier=3,
            spell_attack_bonus=7,
            spell_save_dc=15,
            spells=[{"spell": "bestow_curse", "uses_per_day": 1, "casting_level": 5}],
        )
        state[f"{SRD}.entities"]["guard"]["challenge_rating_eighths"] = 80
        state[f"{SRD}.entities"]["other"]["saving_throw_modifiers"]["wisdom"] = -100
        _, _, curse = _spell_case("bestow_curse")
        curse.pop("caster")
        curse.pop("slot_resource")
        curse.update(caster_state="guard", target="other", targets=["other"], curse_mode="damage")
        budget._run("cast_spell_outside_combat", state, participant_data=data, **curse)
        return transform(state, data, parameters)

    monkeypatch.setattr(fixture, "_cast", curse_then_transform)
    state = fixture._source_attack_state("venomous_snake", temp_hp=5)
    other = state[f"{SRD}.entities"]["other"]
    other["damage_immunities"] = ["poison"] if immune else []
    other["damage_resistances"] = ["necrotic"] if resisted else []
    budget._run(
        "install_srd_guidance_concentration",
        state,
        caster_state="other",
        target_state="other",
        lasting_state="state",
        casting_ability="wisdom",
        casting_modifier=1,
        casting_attack_bonus=3,
        casting_save_dc=11,
        casting_scaling_level=1,
        casting_concentration_save_modifier=100,
        selected_skill="athletics",
        duration_rounds=10,
    )
    monkeypatch.setattr(dice, "_rng", Random(seed))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    rolls = _record_rolls(monkeypatch)
    result = fixture._source_attack(state)
    critical = seed == 5
    assert result["_attack_outcome"] == ("critical_hit" if critical else "hit")
    assert result["_damage_roll"]["count"] == (2 if critical else 1)
    assert result["_form_extra_damage_roll"]["count"] == (2 if critical else 1)
    (curse,) = [r for r in rolls if r.sides == 8]
    assert curse.count == (2 if seed == 5 else 1)
    expected = result["_form_primary_damage_amount"]
    expected += 0 if immune else result["_form_extra_damage_amount"]
    expected += curse.total // 2 if resisted else curse.total
    assert result["_effective_damage"] == expected
    other = state[f"{SRD}.entities"]["other"]
    assert other["hp"] == 100 - max(0, expected - 5)
    assert other["temp_hp"] == max(0, 5 - expected)
    assert len([r for r in rolls if r.sides == 20]) == 2
    assert result["_form_concentration_save_dc"] == max(10, expected // 2)
