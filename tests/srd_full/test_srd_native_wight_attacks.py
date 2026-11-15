"""Actual source Wight weapons, mixed action allowance and current scene sunlight."""

import json
from copy import deepcopy

import pytest

from sylvia_gm import dice
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_native_wight_actions import (
    _native_mummy_fist_runtime as _native_mummy_fist_runtime,
)
from tests.srd_full.test_srd_native_wight_actions import (
    _native_mummy_glare_runtime as _native_mummy_glare_runtime,
)
from tests.srd_full.test_srd_native_wight_actions import (
    _native_wight_case,
)
from tests.srd_full.test_srd_native_wight_actions import (
    _native_wight_runtime as _native_wight_runtime,
)
from tests.srd_full.test_srd_spell_summoning import SRD


@pytest.fixture(scope="module")
def _native_wight_weapons_runtime(_native_wight_runtime):
    from scripts.srd_full.srd_native_wight_attacks import (
        bind_native_wight_attack_consumers,
        extend_runtime,
    )
    from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

    from sylvia_gm.domain.content_validation import validate_content_shape
    from sylvia_gm.runtime._schema_validator import validate_ruleset

    text = extend_runtime(_native_wight_runtime[1].decode())
    assert extend_runtime(text) == text
    text = bind_native_wight_attack_consumers(text)
    assert bind_native_wight_attack_consumers(text) == text
    assert not validate_ruleset(text)
    runtime = _load_runtime(text)
    validate_content_shape("package_runtime", runtime, file="native-wight-weapons")
    return runtime, text.encode()


@pytest.fixture
def native_wight_weapons_owner(monkeypatch, _native_wight_weapons_runtime):
    from functools import lru_cache

    import tests.srd_full.test_srd_action_budget as budget
    from sylvia_gm.application.package_runtime import materialize_package_mechanic
    from sylvia_gm.domain.authority import owned_payload
    from sylvia_gm.domain.package_library import ToolDeclaration

    runtime, encoded = _native_wight_weapons_runtime

    @lru_cache
    def mechanic(name):
        return materialize_package_mechanic(
            package_id=SRD,
            package_version="0.3.1",
            package_sha256="e" * 64,
            runtime_bytes=encoded,
            declaration=ToolDeclaration(
                name=name,
                capability="dnd.srd.5.2.1.spells",
                input_schema=owned_payload(
                    budget._tool_schema(
                        runtime["tools"][name],
                        tool_name="" if name == "restore_hit_points" else name,
                    )
                ),
                writers=("dm",),
                turn_statuses=("open",),
                state_predicates=(),
            ),
        )

    monkeypatch.setattr(budget, "_mechanic", mechanic)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    return runtime


def _case(variant="ordinary", sunlight=False):
    state, data, victim = _native_wight_case(variant)
    state[f"{SRD}.state"]["spatial_scene"]["zones"].append(
        {
            "id": "sunlight-fact",
            "map_id": "information",
            "shape": {"kind": "box", "min": [-1000, -1000, -1000], "max": [1000, 1000, 1000]},
            "metadata": {"plane": "material", "natural_sunlight": sunlight},
        }
    )
    return state, data, victim


def _attack(state, data, victim, name="Necrotic Sword", multi=False, **updates):
    relation = _run(
        "qualify_srd_created_actor_attack_geometry",
        state,
        actor_state="native_wight",
        target_state=victim,
    ).result["context"]
    parameters = {
        "actor": "native_wight",
        "target": victim,
        "attack_name": name,
        "attack_modifier": 4,
        "damage_expression": "d8",
        "damage_modifier": 2,
        "damage_type": "slashing" if name == "Necrotic Sword" else "piercing",
        "attack_mode": "melee" if name == "Necrotic Sword" else "ranged",
        "normal_range": 5 if name == "Necrotic Sword" else 150,
        "long_range": 5 if name == "Necrotic Sword" else 600,
        "attack_distance": relation["_created_attack_distance"],
        "actor_can_see_target": True,
        "target_can_see_actor": True,
        "target_is_charmer": False,
        "fear_source_in_sight": False,
        "advantage": False,
        "disadvantage": False,
        "use_multiattack": multi,
        "attack_uses_weapon": True,
    }
    parameters.update(updates)
    return _run(
        "resolve_adjudicated_creature_attack", state, participant_data=data, **parameters
    ).result


@pytest.mark.parametrize("name", ["Necrotic Sword", "Necrotic Bow"])
def test_native_wight_actual_weapon_critical_components_card_and_no_maximum_loss(
    native_wight_weapons_owner,
    name,
):
    state, data, victim = _case("resistance")
    state = json.loads(json.dumps(state))
    before = deepcopy(state[f"{SRD}.entities"][victim])
    dice._rng.seed(5)
    result = _attack(state, data, victim, name)
    context = result["context"]
    target = state[f"{SRD}.entities"][victim]
    assert context["_attack_roll"]["results"] == [20]
    assert context["_damage_roll"]["count"] == 2
    assert context["_wight_weapon_necrotic_roll"]["count"] == 2
    primary = context["_damage_roll"]["total"] + 2
    necrotic = context["_wight_weapon_necrotic_roll"]["total"] // 2
    assert context["_component_1_effective_damage"] == primary
    assert context["_component_2_effective_damage"] == necrotic
    assert target["hp"] == before["hp"] - primary - necrotic
    assert target["max_hp"] == before["max_hp"]
    assert not target.get("native_undead_max_hp_reduction_receipts")
    assert "Weapon Necrotic dice" in str(result["result_card"])
    assert not state[f"{SRD}.combat"]["action_available"]
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 0


def test_native_wight_mixed_multiattack_persists_and_replaces_only_one_with_life_drain(
    native_wight_weapons_owner,
):
    state, data, victim = _case("resistance")
    dice._rng.seed(31)
    first = _attack(state, data, victim, multi=True)
    assert first["context"]["_attack_outcome"] == "miss"
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 1
    state = json.loads(json.dumps(state))
    dice._rng.seed(31)
    _attack(state, data, victim, "Necrotic Bow", multi=True)
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 0
    saved = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _attack(state, data, victim, multi=True)
    assert state == saved

    state, data, victim = _case("resistance")
    dice._rng.seed(165)
    _run(
        "use_srd_native_wight_life_drain",
        state,
        participant_data=data,
        actor_state="native_wight",
        target_state=victim,
        use_multiattack=True,
    )
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 1
    state = json.loads(json.dumps(state))
    saved = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Only one weapon attack"):
        _run(
            "use_srd_native_wight_life_drain",
            state,
            participant_data=data,
            actor_state="native_wight",
            target_state=victim,
            use_multiattack=True,
        )
    assert state == saved
    dice._rng.seed(31)
    _attack(state, data, victim, "Necrotic Bow", multi=True)
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 0


def test_native_wight_sunlight_attack_and_non_save_d20_disadvantage(
    native_wight_weapons_owner,
):
    state, data, victim = _case("resistance", sunlight=True)
    dice._rng.seed(5)
    result = _attack(state, data, victim)
    assert len(result["context"]["_attack_roll"]["results"]) == 2
    assert result["context"]["_attack_roll_mode"] == "Disadvantage"
    assert state[f"{SRD}.entities"]["native_wight"]["skill_check_modifiers"] == {
        "perception": 3,
        "stealth": 4,
    }
    dice._rng.seed(5)
    check = _run(
        "check_srd_native_wight_ability",
        state,
        actor_state="native_wight",
        skill="perception",
        ability="wisdom",
        target_number=15,
    ).result["context"]
    assert len(check["_d20_test"]["results"]) == 2
    assert check["_ability_modifier"] == 3
    dice._rng.seed(5)
    save = _run(
        "resolve_srd_divination_save",
        state,
        target_state="native_wight",
        actor=None,
        ability="constitution",
        dc=13,
    ).result["context"]
    assert len(save["_d20_test"]["results"]) == 1


def test_native_wight_sunlight_containing_zones_ambiguity_and_unknown_reject_atomically(
    native_wight_weapons_owner,
):
    state, data, victim = _case("resistance", sunlight=False)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["zones"] = []
    scene["zones"].append(
        {
            "id": "sunlit-court",
            "map_id": "information",
            "shape": {"kind": "box", "min": [0, -10, -10], "max": [10, 10, 10]},
            "metadata": {"plane": "material", "natural_sunlight": True},
        }
    )
    result = _run("qualify_srd_native_wight_sunlight", state, actor_state="native_wight").result[
        "context"
    ]
    assert result["_wight_sunlight"] is True
    scene["zones"].append(
        {
            "id": "wrong-plane",
            "map_id": "information",
            "shape": deepcopy(scene["zones"][-1]["shape"]),
            "metadata": {"plane": "ethereal", "natural_sunlight": False},
        }
    )
    assert (
        _run("qualify_srd_native_wight_sunlight", state, actor_state="native_wight").result[
            "context"
        ]["_wight_sunlight"]
        is True
    )
    scene["zones"][-1]["metadata"]["plane"] = "material"
    saved = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="zones disagree"):
        _attack(state, data, victim)
    assert state == saved
    scene["zones"] = []
    saved = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Admit actual natural sunlight"):
        _attack(state, data, victim)
    assert state == saved


def test_native_wight_actual_paid_daylight_proves_sunlight_without_a_natural_fact(
    native_wight_weapons_owner,
    monkeypatch,
):
    import tests.srd_full.test_srd_native_wight_actions as native
    from tests.srd_full.test_srd_spell_summoning import _information_case, _recorded_corpse_case

    monkeypatch.setattr(
        native, "_recorded_corpse_case", lambda _spell: _recorded_corpse_case("daylight")
    )
    state, data, _ = _case("resistance")
    _run("end_combat_encounter", state, participant_data=data, end_reason="fighting_stopped")
    state[f"{SRD}.state"]["spatial_scene"]["zones"] = []
    _, _, parameters = _information_case("daylight")
    before = state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"]
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == before - 1
    state = json.loads(json.dumps(state))
    assert (
        _run("qualify_srd_native_wight_sunlight", state, actor_state="native_wight").result[
            "context"
        ]["_wight_sunlight"]
        is True
    )
