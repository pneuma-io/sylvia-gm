"""Recovery spells exercise actual casts, source lifetimes, revival and later rest consequences."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.test_spatial_engine import _geometry
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_attack_budget import _attack, _combat_case
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_npc_spell_casting import _act, _prepare_mage
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case
from tests.srd_full.test_srd_spell_wave3_orders import _admit_scene


def _fixture(spell, *, death_tick=0, now=0, **body):
    state, data, params = _spell_case(spell)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{SRD}.state"]["lasting_tick"] = now
    target = state[f"{SRD}.entities"]["guard"]
    target.update(
        is_alive=False, hp=0, temp_hp=0, conditions=["dead", "unconscious"], exhaustion_level=2
    )
    _run(
        "record_srd_deceased_body",
        state,
        target_state="guard",
        death_tick=death_tick,
        old_age=body.get("old_age", False),
        soul_willing=body.get("soul_willing", True),
        body_available=body.get("body_available", True),
        vital_parts_present=body.get("vital_parts_present", True),
        living_creature_type="humanoid",
        name="Guard",
    )
    return state, data, params


def _cast(state, data, params):
    if params["spell"] in {"raise_dead", "resurrection", "true_resurrection", "reincarnate"}:
        _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
        _run(
            "continue_srd_spell_cast",
            state,
            caster_state="hero",
            elapsed_rounds=600,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
    return _run("cast_spell_outside_combat", state, participant_data=data, **params)


@pytest.mark.parametrize(
    "spell,hp",
    [("revivify", 1), ("raise_dead", 1), ("resurrection", 20), ("true_resurrection", 20)],
)
def test_named_revival_restores_source_hp_and_dead_glossary_consequences(spell, hp):
    state, data, params = _fixture(spell)
    _cast(state, data, params)
    target = state[f"{SRD}.entities"]["guard"]
    assert target["is_alive"] is True and target["hp"] == hp
    assert (
        target["exhaustion_level"] == (0 if spell == "true_resurrection" else 1)
        and "dead" not in target["conditions"]
    )
    assert target["death_tick"] is None and target["attuned_items"] == []


@pytest.mark.parametrize("body", [{"soul_willing": False}, {"old_age": True}])
def test_revivify_known_invalid_target_spends_actual_slot_and_consumed_diamonds(body):
    state, data, params = _fixture("revivify", **body)
    _cast(state, data, params)
    target = state[f"{SRD}.entities"]["guard"]
    caster = state[f"{SRD}.entities"]["hero"]
    assert target["is_alive"] is False and target["hp"] == 0
    assert caster["resources"]["spell_slot_3"] == 2
    assert caster["spell_materials"][0]["quantity"] == 2


def test_missing_death_facts_reject_atomically():
    state, data, params = _spell_case("revivify")
    state[f"{SRD}.entities"]["guard"].update(is_alive=False, hp=0)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="death/body/soul"):
        _run("cast_spell", state, participant_data=data, **params)
    assert state == before


def test_actual_lethal_damage_records_death_time_once():
    state, _, _ = _spell_case("revivify")
    state[f"{SRD}.state"]["lasting_tick"] = 16
    target = state[f"{SRD}.entities"]["guard"]
    target["creature_kind"] = "monster"
    _run("apply_srd_damage", state, target="guard", amount=100, damage_type="force", adjustment=0)
    assert state[f"{SRD}.entities"]["guard"]["death_tick"] == 16


def test_gentle_repose_excludes_only_protected_elapsed_time():
    state, data, params = _fixture("gentle_repose", now=10)
    _cast(state, data, params)
    assert state[f"{SRD}.entities"]["guard"]["gentle_repose_until"] == 288010
    state[f"{SRD}.state"]["lasting_tick"] = 200
    _run("refresh_srd_corpse_protection", state, target_state="guard")
    target = state[f"{SRD}.entities"]["guard"]
    assert target["death_protected_ticks"] == 190
    _run("refresh_srd_corpse_protection", state, target_state="guard")
    assert state[f"{SRD}.entities"]["guard"]["death_protected_ticks"] == 190


def _control_source(
    state, *, level=1, spell="charm_person", condition="charmed", profile="friendly_charm"
):
    result = _run(
        "install_srd_timed_condition_source",
        state,
        target_state="guard",
        lasting_state="state",
        spell=spell,
        condition=condition,
        save_dc=10,
        duration_rounds=100,
        repeat_save_timing="none",
        caster_state="hero",
        concentration_sequence=0,
        control_profile=profile,
        actual_spell_level=level,
    )
    return result.result["context"]["_timed_condition_source_id"]


def test_actual_dispel_cast_retires_lower_level_source_without_erasing_unowned_condition():
    state, data, params = _spell_case("dispel_magic")
    _control_source(state)
    _run("apply_srd_condition", state, target="guard", condition="poisoned")
    _run("cast_spell", state, participant_data=data, **params)
    target = state[f"{SRD}.entities"]["guard"]
    assert "charmed" not in target["conditions"] and "poisoned" in target["conditions"]


def test_dispel_unknown_imported_level_rejects_without_inventing_a_base_level():
    state, data, params = _spell_case("dispel_magic")
    _control_source(state, level=-1)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="actual casting level"):
        _run("cast_spell", state, participant_data=data, **params)
    assert state == before


def test_remove_curse_ends_geas_and_curse_sources_but_preserves_charm():
    state, data, params = _spell_case("remove_curse")
    first = _control_source(state, spell="geas", profile="geas")
    second = _control_source(state, spell="bestow_curse", condition="none", profile="curse")
    third = _control_source(state)
    _run("cast_spell", state, participant_data=data, **params)
    records = state[f"{SRD}.state"]["timed_condition_sources"]
    assert not records[first]["installed"] and not records[second]["installed"]
    assert records[third]["installed"]


def test_remove_curse_breaks_only_selected_actual_item_attunement_keeps_intrinsic_curse():
    state, data, params = _spell_case("remove_curse")
    state[f"{SRD}.entities"]["guard"]["inventory"] = ["cursed_ring", "cursed_sword"]
    for item in ["cursed_ring", "cursed_sword"]:
        _run("record_srd_cursed_item", state, target_state="guard", item=item, attuned=True)
    params["recovery_item"] = "cursed_ring"
    _run("cast_spell", state, participant_data=data, **params)
    items = state[f"{SRD}.entities"]["guard"]["cursed_items"]
    assert items[0]["intrinsic_curse"] and not items[0]["attuned"] and items[1]["attuned"]


def test_prayer_actual_cast_grants_short_rest_resources_and_once_per_long_rest_healing():
    state, data, params = _spell_case("prayer_of_healing")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _admit_scene(state)
    target = state[f"{SRD}.entities"]["guard"]
    target.update(
        hp=1,
        max_hp=100,
        resources={"hit_die": 1, "second_wind": 0},
        resource_maxima={"second_wind": 2},
    )
    _run(
        "begin_srd_spell_cast",
        state,
        participant_data=data,
        prayer_targets=["guard"],
        **_begin(params),
    )
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=100,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    target = state[f"{SRD}.entities"]["guard"]
    assert target["hp"] > 1 and target["prayer_of_healing_used"]
    assert target["short_rest_hit_dice_available"]


def _feast_case():
    state, data, params = _spell_case("heroes_feast")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{SRD}.entities"]["guard"]["temp_hp"] = 0
    _admit_scene(state)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["position"] = [16, 5, 0]
    floor = _geometry("floor", [-5, -15, -1], [25, 15, 0], visibility="clear")
    floor.update(map_id="orders", metadata={"plane": "material"})
    scene["geometry"] = [floor]
    params.update(recovery_position=[10, 0, 0], recovery_surface="floor")
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=100,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    feast_id = state[f"{SRD}.entities"]["hero"]["last_feast_id"]
    return state, data, params, feast_id


def test_feast_only_grants_hp_after_actual_hour_then_resists_poison():
    state, _, _, feast_id = _feast_case()
    _run("begin_srd_feast_consumption", state, target_state="guard", feast_id=feast_id)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="full one-hour"):
        _run("finish_srd_feast_consumption", state, target_state="guard", feast_id=feast_id)
    assert state == before
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    _run("finish_srd_feast_consumption", state, target_state="guard", feast_id=feast_id)
    target = state[f"{SRD}.entities"]["guard"]
    assert target["max_hp"] > 20 and target["hp"] == target["max_hp"] - 1
    assert target["feast_completed"] == feast_id
    _run("apply_srd_damage", state, target="guard", amount=10, damage_type="poison", adjustment=0)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == target["hp"] - 5


def test_greater_restoration_ends_one_selected_curse_and_preserves_other_sources():
    state, data, params = _spell_case("greater_restoration")
    first = _control_source(state, spell="geas", profile="geas")
    second = _control_source(state, spell="bestow_curse", condition="none", profile="curse")
    params.update(recovery_choice="curse", recovery_source_id=first)
    _run("cast_spell", state, participant_data=data, **params)
    sources = state[f"{SRD}.state"]["timed_condition_sources"]
    assert not sources[first]["installed"] and sources[second]["installed"]


def test_effective_ability_reduction_changes_actual_casting_then_selected_score_restores():
    state, data, params = _spell_case("greater_restoration")
    _run(
        "apply_srd_ability_score_reduction",
        state,
        participant_data=data,
        target_state="hero",
        target_character="hero",
        ability="wisdom",
        amount=4,
        source_id="drain.wisdom",
    )
    _run(
        "apply_srd_ability_score_reduction",
        state,
        participant_data=data,
        target_state="hero",
        target_character="hero",
        ability="strength",
        amount=2,
        source_id="drain.strength",
    )
    params.update(target="hero", recovery_choice="wisdom")
    result = _run("cast_spell", state, participant_data=data, **params)
    hero = state[f"{SRD}.entities"]["hero"]
    assert result.result["context"]["_casting_profile"]["modifier"] == 2
    assert hero["ability_scores"]["wisdom"] == data["abilities"]["wisdom"]
    assert hero["ability_scores"]["strength"] == data["abilities"]["strength"] - 2
    assert hero["has_recovery_ability_reductions"]


def test_death_keeps_unexpired_target_source_then_actual_revivify_restores_it():
    state, data, params = _spell_case("revivify")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    target = state[f"{SRD}.entities"]["guard"]
    target["creature_kind"] = "monster"
    source = _control_source(state, spell="blindness_deafness", condition="blinded", profile="none")
    _run("apply_srd_damage", state, target="guard", amount=100, damage_type="force", adjustment=0)
    _run(
        "record_srd_deceased_body",
        state,
        target_state="guard",
        death_tick=0,
        old_age=False,
        soul_willing=True,
        body_available=True,
        vital_parts_present=True,
        living_creature_type="humanoid",
        name="Guard",
    )
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert state[f"{SRD}.state"]["timed_condition_sources"][source]["installed"]
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert "blinded" in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_elapsed_duration_retires_condition_source_while_target_is_dead():
    state, _, _ = _spell_case("revivify")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{SRD}.entities"]["guard"]["creature_kind"] = "monster"
    source = _control_source(state, spell="blindness_deafness", condition="blinded", profile="none")
    _run("apply_srd_damage", state, target="guard", amount=100, damage_type="force", adjustment=0)
    _run("advance_srd_lasting_time", state, elapsed_rounds=101)
    assert not state[f"{SRD}.state"]["timed_condition_sources"][source]["installed"]


def test_true_resurrection_creates_missing_body_at_actual_unoccupied_scene_position():
    state, data, params = _fixture("true_resurrection", body_available=False)
    _admit_scene(state)
    params.update(
        caster_can_touch_target=False, recovery_position=[5, 0, 0], recovery_spoken_name="Guard"
    )
    _cast(state, data, params)
    target = state[f"{SRD}.entities"]["guard"]
    scene = state[f"{SRD}.state"]["spatial_scene"]
    assert target["is_alive"] and target["body_available"] and target["exhaustion_level"] == 0
    assert next(e for e in scene["entities"] if e["id"] == "guard")["position"] == [5, 0, 0]


def test_heal_ends_actual_blinded_deafened_poisoned_sources_and_preserves_charm():
    state, data, params = _spell_case("heal")
    for condition in ["blinded", "deafened", "poisoned"]:
        _run("apply_srd_condition", state, target="guard", condition=condition)
    charm = _control_source(state)
    _run("cast_spell", state, participant_data=data, **params)
    target = state[f"{SRD}.entities"]["guard"]
    assert all(c not in target["conditions"] for c in ["blinded", "deafened", "poisoned"])
    assert state[f"{SRD}.state"]["timed_condition_sources"][charm]["installed"]


def test_dispel_evil_break_enchantment_uses_real_magic_action_and_only_qualified_source():
    state, data, params = _spell_case("dispel_evil_and_good")
    state[f"{SRD}.entities"]["hero"]["creature_type"] = "humanoid"
    state[f"{SRD}.entities"]["other"]["creature_type"] = "fiend"
    _admit_scene(state)
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = [5, 0, 0]
    first = _run(
        "install_srd_timed_condition_source",
        state,
        target_state="guard",
        lasting_state="state",
        spell="charm_monster",
        condition="charmed",
        save_dc=10,
        duration_rounds=100,
        repeat_save_timing="none",
        caster_state="other",
        concentration_sequence=0,
        control_profile="friendly_charm",
        actual_spell_level=4,
    ).result["context"]["_timed_condition_source_id"]
    second = _control_source(state)
    _run("cast_spell", state, participant_data=data, **params)
    state[f"{SRD}.combat"].update(action_available=True, normal_action_spent=False)
    _run(
        "use_srd_dispel_evil_and_good",
        state,
        caster_state="hero",
        target_state="guard",
        choice="break_enchantment",
        can_touch=True,
    )
    sources = state[f"{SRD}.state"]["timed_condition_sources"]
    assert not sources[first]["installed"] and sources[second]["installed"]
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    assert state[f"{SRD}.combat"]["normal_action_spent"]


def test_paid_greater_restoration_cures_only_contact_other_plane_source():
    state, data, params = _spell_case("greater_restoration")
    contact = _control_source(
        state, spell="contact_other_plane", condition="incapacitated", profile="none"
    )
    independent = _control_source(state, spell="sleep", condition="incapacitated", profile="sleep")
    params["recovery_choice"] = "contact_other_plane"
    _run("cast_spell", state, participant_data=data, **params)
    sources = state[f"{SRD}.state"]["timed_condition_sources"]
    assert not sources[contact]["installed"] and sources[independent]["installed"]
    assert "incapacitated" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2


@pytest.mark.parametrize("level,succeeded", [(7, True), (9, False)])
def test_dispel_higher_level_uses_actual_unproficient_casting_ability_check(
    monkeypatch, level, succeeded
):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, params = _spell_case("dispel_magic")
    source = _control_source(state, level=level)
    result = _run("cast_spell", state, participant_data=data, **params)
    assert result.result["status"] == "committed"
    assert state[f"{SRD}.state"]["timed_condition_sources"][source]["installed"] is not succeeded


def test_dispelling_one_bless_target_preserves_other_targets_and_caster_concentration():
    state, data, params = _spell_case("bless")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    data["spellcasting_grants"][0]["spells"].append("dispel_magic")
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("dispel_magic")
    _run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(params | {"targets": ["guard", "other"]}),
    )
    _, _, dispel = _spell_case("dispel_magic")
    _run("cast_spell_outside_combat", state, participant_data=data, **dispel)
    entities = state[f"{SRD}.entities"]
    assert entities["guard"]["concentration_d20_modifier_source_ids"] == []
    assert entities["other"]["concentration_d20_modifier_source_ids"] == ["hero"]
    assert entities["hero"]["concentration_spell_id"] == "bless"


def test_feast_expiry_restores_independent_frightened_source_and_original_hp_maximum():
    state, _, _, feast_id = _feast_case()
    source = _control_source(state, condition="frightened", spell="fear", profile="fear")
    state[f"{SRD}.state"]["timed_condition_sources"][source]["expires_tick"] = 100000
    _run("begin_srd_feast_consumption", state, target_state="guard", feast_id=feast_id)
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    target = state[f"{SRD}.entities"]["guard"]
    assert "frightened" not in target["conditions"] and target["max_hp"] > 20
    _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    target = state[f"{SRD}.entities"]["guard"]
    assert "frightened" in target["conditions"] and target["max_hp"] == 20
    assert state[f"{SRD}.state"]["timed_condition_sources"][source]["installed"]


def test_harm_maximum_hp_reduction_is_reversed_by_actual_greater_restoration():
    state, data, params = _spell_case("harm")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    target = state[f"{SRD}.entities"]["guard"]
    target.update(hp=1000, max_hp=1000, temp_hp=0)
    target["saving_throw_modifiers"]["constitution"] = -100
    data["spellcasting_grants"][0]["spells"].append("greater_restoration")
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("greater_restoration")
    _, _, cure = _spell_case("greater_restoration")
    cure_materials = _spell_case("greater_restoration")[0][f"{SRD}.entities"]["hero"]
    state[f"{SRD}.entities"]["hero"]["spell_materials"] = cure_materials["spell_materials"]
    state[f"{SRD}.entities"]["hero"]["inventory"] += [
        item["inventory_item"] for item in cure_materials["spell_materials"]
    ]
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    reduced = state[f"{SRD}.entities"]["guard"]["max_hp"]
    assert reduced < 1000
    _run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(cure | {"recovery_choice": "maximum_hp"}),
    )
    target = state[f"{SRD}.entities"]["guard"]
    assert target["max_hp"] == 1000 and target["hp"] <= reduced


def test_public_npc_reincarnate_reopens_body_roll_then_updates_actual_character_once(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "reincarnate.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, data, params = _create_spell_campaign(
            owner,
            "reincarnate",
            participant_overrides={"species": "goliath", "skill_proficiencies": ["arcana"]},
            entity_overrides={
                "is_alive": False,
                "hp": 0,
                "conditions": ["dead"],
                "creature_type": "humanoid",
            },
        )
        hero = owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]
        supplies = [
            dict(item) | {"components": list(item["components"])}
            for item in hero["spell_materials"]
        ]
        receipt, _ = _prepare_mage(
            owner,
            [{"spell": "reincarnate", "uses_per_day": 1, "casting_level": 5}],
            inventory=[item["inventory_item"] for item in supplies],
            spell_materials=supplies,
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        receipt, _ = _act(
            owner,
            "record_srd_deceased_body",
            target_state="hero",
            death_tick=0,
            old_age=False,
            soul_willing=True,
            body_available=True,
            vital_parts_present=True,
            living_creature_type="humanoid",
            name="Mage",
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        params.pop("caster")
        params.pop("slot_resource")
        params.update(caster_state="mage", target="hero")
        receipt, _ = _act(owner, "begin_srd_spell_cast", **_begin(params))
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        receipt, _ = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="mage",
            elapsed_rounds=600,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        receipt, command = _act(owner, "cast_spell_outside_combat", **params)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        body = saved.state[f"{SRD}.entities"]["hero"]["reincarnation"]
        assert body["roll"]["total"] == 7 and body["species"] == "halfling"
        assert saved.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["reincarnate"] == 0
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        receipt, command = _act(
            owner,
            "complete_srd_reincarnation",
            target_state="hero",
            target_character="hero",
            hit_points=1,
            skill_proficiencies=data["skill_proficiencies"],
            origin_feats=data["origin_feats"],
            spellcasting_grants=data["spellcasting_grants"],
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        completed = owner.snapshot("casting")
        hero = completed.state[f"{SRD}.entities"]["hero"]
        participant = next(p for p in completed.participants if p.participant_id == "hero")
        assert hero["is_alive"] and hero["hp"] == 1 and hero["size_rank"] == 1
        assert participant.data["species"] == "halfling"
        assert participant.data["spellcasting_grants"] == data["spellcasting_grants"]
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting").state == completed.state


def test_prayer_actual_exit_and_return_loses_only_that_creatures_benefit():
    state, data, params = _spell_case("prayer_of_healing")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _admit_scene(state)
    state[f"{SRD}.state"]["spatial_scene"]["entities"][2]["position"] = [0, 20, 0]
    _run(
        "begin_srd_spell_cast",
        state,
        participant_data=data,
        prayer_targets=["guard", "other"],
        **_begin(params),
    )
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[35, 0, 0])
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=1,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[20, 0, 0])
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=99,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    entities = state[f"{SRD}.entities"]
    assert entities["guard"].get("prayer_of_healing_used") is not True
    assert entities["other"]["prayer_of_healing_used"] is True


def test_revived_attack_penalty_is_consumed_by_real_weapon_attack():
    state, data = _combat_case()
    state[f"{SRD}.entities"]["hero"]["revival_penalty"] = -4
    result = _attack(state, data).result
    context = result["context"]
    assert context["_attack_adjusted_total"] == (
        context["_attack_roll"]["total"] + context["_attack_roll"]["modifier"] - 4
    )


def test_actual_raise_dead_penalty_changes_the_later_saving_throw_outcome(monkeypatch):
    state, data, params = _fixture("raise_dead")
    _cast(state, data, params)
    target = state[f"{SRD}.entities"]["guard"]
    assert target["revival_penalty"] == -4
    monkeypatch.setattr(dice, "_rng", Random(1))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    result = _run(
        "d20_test", state, actor_state="guard", kind="saving_throw", ability="constitution",
        ability_modifier=0, proficiency_bonus=0, proficiency_factor="none",
        circumstantial_modifier=0, target_number=2,
    ).result["context"]
    raw = result["_d20_test"]
    expected = raw["total"] + raw["modifier"] - target["exhaustion_level"] * 2 - 4
    assert result["_adjusted_total"] == expected
    assert result["_combined_modifier"] == expected - raw["total"]
    assert result["_outcome"] == "failure"


def test_raise_dead_penalty_decreases_only_after_actual_long_rest():
    state, data, params = _fixture("raise_dead")
    _cast(state, data, params)
    target = state[f"{SRD}.entities"]["guard"]
    assert target["revival_penalty"] == -4
    _run("begin_srd_rest", state, target_state="guard", kind="long", activity="sleep")
    _run("advance_srd_lasting_time", state, elapsed_rounds=4800)
    _run("complete_srd_rest", state, target_state="guard")
    assert state[f"{SRD}.entities"]["guard"]["revival_penalty"] == -3


def test_true_resurrection_cures_suppressed_owned_conditions_and_curses():
    state, data, params = _fixture("true_resurrection")
    charm = _control_source(state)
    curse = _control_source(state, spell="bestow_curse", condition="none", profile="curse")
    state[f"{SRD}.state"]["timed_condition_sources"][charm]["suppressed"] = True
    _cast(state, data, params)
    sources = state[f"{SRD}.state"]["timed_condition_sources"]
    target = state[f"{SRD}.entities"]["guard"]
    assert not sources[charm]["installed"] and not sources[curse]["installed"]
    assert target["conditions"] == [] and target["exhaustion_level"] == 0


@pytest.mark.parametrize("modifier,dismissed", [(-100, True), (100, False)])
def test_dispel_evil_dismissal_rolls_actual_save_and_changes_only_scene_plane(modifier, dismissed):
    state, data, params = _spell_case("dispel_evil_and_good")
    _admit_scene(state)
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = [5, 0, 0]
    target = state[f"{SRD}.entities"]["guard"]
    target.update(creature_type="fiend", home_plane="nine_hells")
    target["saving_throw_modifiers"]["charisma"] = modifier
    _run("cast_spell", state, participant_data=data, **params)
    state[f"{SRD}.combat"].update(action_available=True, normal_action_spent=False)
    _run(
        "use_srd_dispel_evil_and_good",
        state,
        caster_state="hero",
        target_state="guard",
        choice="dismissal",
    )
    scene = state[f"{SRD}.state"]["spatial_scene"]
    guard = next(e for e in scene["entities"] if e["id"] == "guard")
    assert guard["metadata"]["plane"] == ("nine_hells" if dismissed else "material")
    assert guard["position"] == [5, 0, 0]
    assert state[f"{SRD}.combat"]["normal_action_spent"]
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None


def test_prayer_earned_hit_die_choice_and_repeat_gate_require_actual_long_rest():
    state, data, params = _spell_case("prayer_of_healing")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _admit_scene(state)
    state[f"{SRD}.entities"]["guard"].update(
        hp=1,
        max_hp=100,
        hit_die_size=8,
        constitution_modifier=0,
        resources={"hit_die": 3},
        resource_maxima={"hit_die": 3},
    )

    def pray():
        _run(
            "begin_srd_spell_cast",
            state,
            participant_data=data,
            prayer_targets=["guard"],
            **_begin(params),
        )
        _run(
            "continue_srd_spell_cast",
            state,
            caster_state="hero",
            elapsed_rounds=100,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        _run("cast_spell_outside_combat", state, participant_data=data, **params)

    pray()
    hp = state[f"{SRD}.entities"]["guard"]["hp"]
    _run("take_srd_short_rest_hit_die", state, target_state="guard")
    assert state[f"{SRD}.entities"]["guard"]["hp"] > hp
    assert state[f"{SRD}.entities"]["guard"]["resources"]["hit_die"] == 2
    hp = state[f"{SRD}.entities"]["guard"]["hp"]
    pray()
    assert state[f"{SRD}.entities"]["guard"]["hp"] == hp
    _run("begin_srd_rest", state, target_state="guard", kind="long", activity="sleep")
    _run("advance_srd_lasting_time", state, elapsed_rounds=4800)
    _run("complete_srd_rest", state, target_state="guard")
    assert state[f"{SRD}.entities"]["guard"]["prayer_of_healing_used"] is False


def test_resurrecting_old_body_blocks_casting_until_actual_caster_long_rest():
    state, data, params = _fixture("resurrection", now=10512000)
    _cast(state, data, params)
    assert state[f"{SRD}.entities"]["hero"]["resurrection_tax"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Long Rest"):
        _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    assert state == before
    _run("begin_srd_rest", state, target_state="hero", kind="long", activity="sleep")
    _run("advance_srd_lasting_time", state, elapsed_rounds=4800)
    _run("complete_srd_rest", state, target_state="hero")
    assert state[f"{SRD}.entities"]["hero"]["resurrection_tax"] is False
