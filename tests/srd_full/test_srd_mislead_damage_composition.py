"""A later real Fire Shield packet ends only the actual caster's Mislead Invisible grant."""

from functools import lru_cache

import pytest
from scripts.srd_full.srd_actor_spell_modifiers import refresh_exploration_runtime
from scripts.srd_full.srd_spell_damage_consumers import extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_illusions as illusions
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_spell_damage_consumers import SRD, _grant, _npc_attack


@pytest.fixture(scope="module")
def _final_damage_runtime():
    _, content = illusions._runtime()
    text = extend_runtime(content.decode())
    assert extend_runtime(text) == text
    text = refresh_exploration_runtime(text)
    return _load_runtime(text), text.encode()


@pytest.fixture(autouse=True)
def _actual_final_packet(monkeypatch, _final_damage_runtime):
    runtime, content = _final_damage_runtime

    @lru_cache
    def mechanic(name):
        return materialize_package_mechanic(
            package_id=SRD,
            package_version="0.3.1",
            package_sha256="a" * 64,
            runtime_bytes=content,
            declaration=ToolDeclaration(
                name=name,
                capability="dnd.srd.5.2.1.spells",
                input_schema=owned_payload(
                    budget._tool_schema(runtime["tools"][name], tool_name=name)
                ),
                writers=("dm",),
                turn_statuses=("open",),
                state_predicates=(),
            ),
        )

    monkeypatch.setattr(budget, "_mechanic", mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: runtime["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)

    class ActualMaximumFaces:
        def randint(self, low, high):
            assert low == 1
            return high

    monkeypatch.setattr(dice, "_rng", ActualMaximumFaces())


@pytest.mark.parametrize("fire_immune", [False, True])
def test_later_fire_shield_damage_ends_mislead_even_when_temp_hp_absorbs_it(fire_immune):
    state, data, mislead = illusions._case("mislead")
    shield = _grant(state, data, "fire_shield")
    shield.pop("target")
    shield["actor_spell_choice"] = "warm"
    budget._run("cast_spell_outside_combat", state, participant_data=data, **shield)
    budget._run("cast_spell_outside_combat", state, participant_data=data, **mislead)
    source = next(reversed(state[f"{SRD}.state"]["illusion_spell_sources"].values()))
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(hp=1000, max_hp=1000, temp_hp=0)
    assert "invisible" in hero["conditions"] and source["invisible_active"]
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(
        hp=1000,
        max_hp=1000,
        temp_hp=1000,
        multiattack=[{"attack": "Claw", "count": 1}],
        damage_immunities=["fire"] if fire_immune else [],
    )
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="guard",
        current_actor="guard",
        action_available=True,
        normal_action_spent=False,
    )
    result = _npc_attack(
        state, "Claw", attack_modifier=100, attack_mode="melee", attack_distance=5
    ).result
    current = state[f"{SRD}.state"]["illusion_spell_sources"][source["id"]]
    hero = state[f"{SRD}.entities"]["hero"]
    guard = state[f"{SRD}.entities"]["guard"]
    assert current["installed"] and hero["concentration_spell_id"] == "mislead"
    assert guard["hp"] == 1000
    if fire_immune:
        assert current["invisible_active"] and "invisible" in hero["conditions"]
        assert guard["temp_hp"] == 1000
        assert result["context"]["_fire_shield_damage_applied"] == 0
    else:
        assert not current["invisible_active"] and "invisible" not in hero["conditions"]
        assert guard["temp_hp"] < 1000
        assert result["context"]["_fire_shield_damage_applied"] > 0


@pytest.mark.parametrize("fire_immune", [False, True])
def test_shared_damage_guard_preserves_ordinary_invisibility_retaliation(fire_immune):
    state, data, _ = illusions._case("mislead")
    shield = _grant(state, data, "fire_shield")
    shield.pop("target")
    shield["actor_spell_choice"] = "warm"
    budget._run("cast_spell_outside_combat", state, participant_data=data, **shield)
    invisibility = _grant(state, data, "invisibility")
    invisibility.update(target="hero", targets=["hero"])
    budget._run("cast_spell_outside_combat", state, participant_data=data, **invisibility)
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(hp=1000, max_hp=1000, temp_hp=0)
    assert "invisible" in hero["conditions"]
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(
        hp=1000,
        max_hp=1000,
        temp_hp=1000,
        multiattack=[{"attack": "Claw", "count": 1}],
        damage_immunities=["fire"] if fire_immune else [],
    )
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="guard",
        current_actor="guard",
        action_available=True,
        normal_action_spent=False,
    )
    _npc_attack(state, "Claw", attack_modifier=100, attack_mode="melee", attack_distance=5)
    hero = state[f"{SRD}.entities"]["hero"]
    guard = state[f"{SRD}.entities"]["guard"]
    assert ("invisible" in hero["conditions"]) is fire_immune
    assert guard["hp"] == 1000
    assert (guard["temp_hp"] == 1000) is fire_immune
