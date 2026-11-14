"""Source-owned orders use actual later turns, damage, budgets, and saved public actions."""

import json
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
from scripts.srd_full.srd_control_orders import ORDER_PROFILES, PUBLIC_TOOLS

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import PackageRuntimeError
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_attack_budget import _npc_attack
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case


def _case(monkeypatch, spell):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, params = _spell_case(spell)
    data.update(species="human", languages=["common"])
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    for target in ("guard", "other"):
        entity = state[f"{SRD}.entities"][target]
        entity.update(hp=100, max_hp=100, temp_hp=5, languages=["common"])
        entity["saving_throw_modifiers"]["wisdom"] = -100
    if spell == "dominate_beast":
        state[f"{SRD}.entities"]["guard"]["creature_type"] = "beast"
    params.update(targets=["guard"])
    if spell in ("suggestion", "mass_suggestion"):
        params.update(
            activity_words=["Guard", "the", "door"],
            activity_achievable=True,
            activity_harmful=False,
        )
    if spell == "geas":
        params.update(geas_command="Guard the door")
    return state, data, params


def _cast(monkeypatch, spell, **changes):
    state, data, params = _case(monkeypatch, spell)
    params.update(changes)
    if spell == "geas":
        _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
        _run(
            "continue_srd_spell_cast",
            state,
            caster_state="hero",
            elapsed_rounds=10,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
    out = _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert out.result["status"] == "committed"
    return state, data, params, out.result


def _turn(state, actor="guard", data=None):
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=False, current_actor=actor)
    return _run("begin_combat_turn", state, participant_data=data)


def _source(state, target="guard"):
    source_id = state[f"{SRD}.entities"][target]["control_order_source_ids"][0]
    return source_id, state[f"{SRD}.state"]["timed_condition_sources"][source_id]


def _reject(state, name, **parameters):
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(name, state, **parameters)
    assert state == before


def _admit_scene(state):
    entities = []
    for actor, point in [("hero", [0, 0, 0]), ("guard", [20, 0, 0]), ("other", [40, 30, 0])]:
        entities.append(
            {
                "id": actor,
                "map_id": "orders",
                "position": point,
                "footprint": {"kind": "box", "half_extents": [0.4, 0.4]},
                "height": 6,
                "facing": [1, 0],
                "active": True,
                "movement": "blocking",
                "visibility": "opaque",
                "metadata": {"participant_ref": actor, "plane": "material", "lighting": "bright"},
            }
        )
    state[f"{SRD}.state"]["spatial_scene"] = {
        "format": 1,
        "map": {
            "id": "orders",
            "revision": 0,
            "bounds": {"min": [-1000, -1000, -1000], "max": [1000, 1000, 1000]},
            "grid": {"cell_size": 5, "unit": "ft", "origin": [-1000, -1000]},
            "layers": [{"id": "world", "min_elevation": -1000, "max_elevation": 1000}],
        },
        "entities": entities,
        "geometry": [],
        "zones": [],
        "terrain": [],
    }


@pytest.mark.parametrize(
    "command,destination,cost",
    [
        ("approach", [5, 0, 0], 15),
        ("flee", [80, 0, 0], 60),
    ],
)
def test_command_movement_mutates_admitted_scene_and_actual_turn_budget(
    monkeypatch,
    command,
    destination,
    cost,
):
    state, _, _, _ = _cast(monkeypatch, "command", command=command)
    _admit_scene(state)
    source_id, _ = _source(state)
    _turn(state)
    _reject(state, "end_combat_turn")
    _run(
        "resolve_srd_control_order",
        state,
        actor_state="guard",
        caster_state="hero",
        source_id=source_id,
        destination=destination,
        safe_route=True,
        shortest_route=True,
    )
    scene = state[f"{SRD}.state"]["spatial_scene"]
    assert next(e for e in scene["entities"] if e["id"] == "guard")["position"] == destination
    assert state[f"{SRD}.combat"]["movement_spent"] == cost
    assert state[f"{SRD}.combat"]["turn_active"] is False


def test_compulsion_direction_spends_bonus_then_actual_movement_and_repeat_save(monkeypatch):
    state, data, _, _ = _cast(monkeypatch, "compulsion")
    _admit_scene(state)
    source_id, _ = _source(state)
    _turn(state, "hero", data)
    _run("designate_srd_compulsion_direction", state, caster_state="hero", direction=[1, 0, 0])
    assert state[f"{SRD}.entities"]["hero"]["bonus_action_available"] is False
    _turn(state)
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["wisdom"] = 100
    _run(
        "resolve_srd_control_order",
        state,
        actor_state="guard",
        caster_state="hero",
        source_id=source_id,
        destination=[50, 0, 0],
        safe_route=True,
        shortest_route=True,
    )
    assert state[f"{SRD}.combat"]["movement_remaining"] == 0
    assert "charmed" not in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_dominate_order_issues_for_free_then_actual_named_target_attack_completes(monkeypatch):
    state, data, _, _ = _cast(monkeypatch, "dominate_person")
    _admit_scene(state)
    source_id, _ = _source(state)
    _turn(state, "hero", data)
    _run(
        "issue_srd_dominated_order",
        state,
        caster_state="hero",
        target_state="guard",
        source_id=source_id,
        order_kind="attack",
        order_target_state="other",
    )
    assert state[f"{SRD}.combat"]["action_available"] is True
    _turn(state)
    _reject(state, "end_combat_turn")
    _npc_attack(state, "Claw", actor="guard", target="other", use_multiattack=False)
    assert state[f"{SRD}.combat"]["normal_action_spent"] is True
    assert state[f"{SRD}.state"]["timed_condition_sources"][source_id]["order_complete"] is True
    _run("end_combat_turn", state)


def test_dominate_commanded_reaction_authorizes_parent_and_spends_only_caster(monkeypatch):
    state, _, _, _ = _cast(monkeypatch, "dominate_person")
    _admit_scene(state)
    source_id, _ = _source(state)
    state[f"{SRD}.state"].update(
        pending_srd_actions={"event": {"id": "event"}}, pending_srd_action_stack=["event"]
    )
    _run(
        "command_srd_dominated_reaction",
        state,
        caster_state="hero",
        target_state="guard",
        source_id=source_id,
        pending_action_id="event",
    )
    assert state[f"{SRD}.entities"]["hero"]["reaction_available"] is False
    assert state[f"{SRD}.entities"]["guard"]["reaction_available"] is True
    assert state[f"{SRD}.state"]["pending_srd_actions"]["event"][
        "dominated_reaction_authorizations"
    ] == {"guard": {"caster_state_id": "hero", "source_id": source_id, "consumed": False}}
    _run("consume_srd_reaction", state, actor_state="guard", lasting_state="state",
         reaction="spell")
    assert state[f"{SRD}.entities"]["guard"]["reaction_available"] is False
    assert state[f"{SRD}.state"]["pending_srd_actions"]["event"][
        "dominated_reaction_authorizations"
    ]["guard"]["consumed"] is True
    _reject(state, "consume_srd_reaction", actor_state="guard", lasting_state="state",
            reaction="spell")


def test_commanded_reaction_cannot_use_a_retired_dominate_source(monkeypatch):
    state, _, _, _ = _cast(monkeypatch, "dominate_person")
    _admit_scene(state)
    source_id, _ = _source(state)
    state[f"{SRD}.state"].update(
        pending_srd_actions={"event": {"id": "event"}}, pending_srd_action_stack=["event"]
    )
    _run("command_srd_dominated_reaction", state, caster_state="hero", target_state="guard",
         source_id=source_id, pending_action_id="event")
    state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"] = False
    _reject(state, "consume_srd_reaction", actor_state="guard", lasting_state="state",
            reaction="spell")


@pytest.mark.parametrize("spell", ORDER_PROFILES)
def test_every_order_profile_casts_with_owned_source_and_actual_cost(monkeypatch, spell):
    state, _, params, result = _cast(monkeypatch, spell)
    source_id, source = _source(state)
    assert source["spell_id"] == spell
    assert source["caster_state_id"] == "hero"
    assert source["target_state_id"] == "guard"
    assert source["installed"] is True
    assert source_id in state[f"{SRD}.entities"]["guard"]["timed_condition_source_ids"]
    assert state[f"{SRD}.entities"]["hero"]["resources"][params["slot_resource"]] == 2
    assert len(result["context"]["_order_save_rolls"]) == 1
    assert "Wisdom save:" in result["result_card"]["details"][0]["value"]
    assert "{" not in result["result_card"]["details"][0]["value"]
    assert ("charmed" in state[f"{SRD}.entities"]["guard"]["conditions"]) == (spell != "command")


@pytest.mark.parametrize("spell", ["dominate_beast", "dominate_person"])
def test_ineligible_creature_automatically_saves_without_rolling_but_spends_slot(
    monkeypatch, spell
):
    state, data, params = _case(monkeypatch, spell)
    state[f"{SRD}.entities"]["guard"]["creature_type"] = "construct"
    out = _run("cast_spell_outside_combat", state, participant_data=data, **params).result
    assert out["context"]["_order_save_rolls"] == [None]
    assert state[f"{SRD}.entities"]["guard"].get("has_control_order_sources") is not True
    assert state[f"{SRD}.entities"]["hero"]["resources"][params["slot_resource"]] == 2


@pytest.mark.parametrize("command", ["drop", "grovel", "halt"])
def test_fixed_command_runs_on_next_real_turn_and_retires(monkeypatch, command):
    state, _, _, _ = _cast(monkeypatch, "command", command=command)
    target = state[f"{SRD}.entities"]["guard"]
    target.update(inventory=["spear", "pack"], held_items=["spear"], held_attack_names=["Spear"])
    source_id, _ = _source(state)
    _turn(state)
    target = state[f"{SRD}.entities"]["guard"]
    combat = state[f"{SRD}.combat"]
    if command == "halt":
        assert combat["turn_active"] is True
        assert combat["movement_remaining"] == 0
        assert combat["action_available"] is False
        assert target["bonus_action_available"] is False
        assert target["reaction_available"] is True
        _run("end_combat_turn", state)
    else:
        assert combat["turn_active"] is False
        if command == "drop":
            assert target["held_items"] == []
            assert target["dropped_items"] == ["spear"]
            assert target["inventory"] == ["pack"]
        else:
            assert "prone" in target["conditions"]
    assert state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"] is False


def test_dominate_initial_fighting_advantage_uses_committed_actor_history(monkeypatch):
    state, data, params = _case(monkeypatch, "dominate_person")
    state[f"{SRD}.entities"]["guard"]["fighting_actor_ids"] = ["hero"]
    result = _run("cast_spell_outside_combat", state, participant_data=data, **params).result
    assert len(result["context"]["_order_save_rolls"][0]["results"]) == 2


@pytest.mark.parametrize("spell", ["charm_person", "charm_monster"])
def test_existing_friendly_charms_derive_fighting_advantage_from_saved_actor_history(
    monkeypatch, spell
):
    state, data, params = _case(monkeypatch, spell)
    state[f"{SRD}.entities"]["guard"]["fighting_actor_ids"] = ["hero"]
    result = _run("cast_spell_outside_combat", state, participant_data=data, **params).result
    assert len(result["context"]["_spell_timed_condition_initial_save_rolls"][0]["results"]) == 2


@pytest.mark.parametrize("success", [False, True])
def test_dominate_repeats_save_on_actual_damage_including_temporary_hp(monkeypatch, success):
    state, _, _, _ = _cast(monkeypatch, "dominate_person")
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["wisdom"] = 100 if success else -100
    _run(
        "apply_srd_damage",
        state,
        target="guard",
        amount=1,
        adjustment=0,
        damage_type="fire",
        source_state_id="other",
    )
    target = state[f"{SRD}.entities"]["guard"]
    assert target["hp"] == 100
    assert target["temp_hp"] == 4
    assert len(target["last_control_damage_save_roll"]["results"]) == 1
    assert ("charmed" in target["conditions"]) is (not success)


@pytest.mark.parametrize("source", ["hero", "other", ""])
def test_suggestion_releases_only_for_caster_or_allied_damage(monkeypatch, source):
    state, _, _, _ = _cast(monkeypatch, "suggestion")
    _run(
        "apply_srd_damage",
        state,
        target="guard",
        amount=1,
        adjustment=0,
        damage_type="fire",
        source_state_id=source,
    )
    assert ("charmed" in state[f"{SRD}.entities"]["guard"]["conditions"]) is (source != "hero")


def test_suggestion_activity_spends_action_and_completion_removes_only_its_source(monkeypatch):
    state, _, _, _ = _cast(monkeypatch, "suggestion")
    _run("apply_srd_condition", state, target="guard", condition="charmed")
    source_id, _ = _source(state)
    _turn(state)
    _reject(state, "end_combat_turn")
    _run(
        "resolve_srd_control_activity",
        state,
        actor_state="guard",
        source_id=source_id,
        completed=True,
        completion_evidence="Door has been guarded for the requested hour",
        task_kind="work",
    )
    assert state[f"{SRD}.combat"]["normal_action_spent"] is True
    assert state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"] is False
    assert "charmed" in state[f"{SRD}.entities"]["guard"]["conditions"]
    _run("end_combat_turn", state)


@pytest.mark.parametrize(
    "spell,slot,rounds",
    [
        ("dominate_beast", 5, 100),
        ("dominate_person", 7, 600),
        ("dominate_person", 8, 4800),
        ("dominate_monster", 9, 4800),
        ("mass_suggestion", 7, 144000),
        ("mass_suggestion", 8, 432000),
        ("mass_suggestion", 9, 5270400),
        ("geas", 7, 5256000),
        ("geas", 9, None),
    ],
)
def test_source_duration_upcasts_are_exact(monkeypatch, spell, slot, rounds):
    state, _, _, _ = _cast(monkeypatch, spell, slot_resource=f"spell_slot_{slot}")
    _, source = _source(state)
    assert source["expires_tick"] == (
        None if rounds is None else source["started_tick"] + 2 * rounds
    )


def test_geas_damage_is_once_per_day_and_survives_indefinite_expiry(monkeypatch):
    state, _, _, _ = _cast(monkeypatch, "geas", slot_resource="spell_slot_9")
    source_id, source = _source(state)
    assert source["expires_tick"] is None
    target = state[f"{SRD}.entities"]["guard"]
    target["damage_resistances"] = ["psychic"]
    _run(
        "violate_srd_geas",
        state,
        target_state="guard",
        source_id=source_id,
        directly_counter=True,
        action_evidence="Left the assigned door unguarded",
    )
    first = state[f"{SRD}.entities"]["guard"]["hp"]
    assert 75 <= first <= 100
    _run(
        "violate_srd_geas",
        state,
        target_state="guard",
        source_id=source_id,
        directly_counter=True,
        action_evidence="Refused another guarding assignment",
    )
    assert state[f"{SRD}.entities"]["guard"]["hp"] == first
    state[f"{SRD}.state"]["lasting_tick"] += 28800
    _run("refresh_srd_timed_condition_target", state, target_state="guard", lasting_state="state")
    _run(
        "violate_srd_geas",
        state,
        target_state="guard",
        source_id=source_id,
        directly_counter=True,
        action_evidence="Again left the door unguarded the next day",
    )
    assert state[f"{SRD}.entities"]["guard"]["hp"] < first


def test_geas_suicidal_command_and_unknown_language_leave_no_effect_after_paid_cast(monkeypatch):
    state, _, _, _ = _cast(monkeypatch, "geas", geas_suicidal=True)
    assert state[f"{SRD}.entities"]["guard"].get("has_control_order_sources") is not True
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2


def test_mass_suggestion_twelve_targets_commit_and_thirteen_reject_atomically(monkeypatch):
    state, data, params = _case(monkeypatch, "mass_suggestion")
    targets = [f"subject_{i}" for i in range(13)]
    for target in targets:
        state[f"{SRD}.entities"][target] = deepcopy(state[f"{SRD}.entities"]["guard"])
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "cast_spell_outside_combat",
            state,
            participant_data=data,
            **(params | {"targets": targets}),
        )
    assert state == before
    out = _run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(params | {"targets": targets[:12]}),
    ).result
    assert len(out["context"]["_order_save_rolls"]) == 12
    assert all(
        "charmed" in state[f"{SRD}.entities"][target]["conditions"] for target in targets[:12]
    )


def test_public_command_reopens_retries_then_halts_actual_next_turn(tmp_path, monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "command.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = _create_spell_campaign(
            owner,
            "command",
            target_overrides={
                "saving_throw_modifiers": dict.fromkeys(
                    ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"],
                    -100,
                )
            },
        )
        receipt, command = _act(
            owner,
            "cast_spell_outside_combat",
            **(params | {"targets": ["guard"], "command": "halt"}),
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting").state
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved
        receipt, _ = _act(
            owner,
            "start_combat_encounter_entities",
            first_actor="guard",
            second_actor="other",
            first_surprised=False,
            second_surprised=False,
            tie_winner="first",
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        receipt, _ = _act(owner, "begin_combat_turn")
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        snapshot = owner.snapshot("casting").state
        assert snapshot[f"{SRD}.combat"]["movement_remaining"] == 0
        assert snapshot[f"{SRD}.combat"]["action_available"] is False
        assert snapshot[f"{SRD}.entities"]["guard"]["reaction_available"] is True


def test_actor_derived_elf_sleep_immunity_and_passive_perception_replace_redundant_facts(
    monkeypatch,
):
    state, data, params = _case(monkeypatch, "sleep")
    data.update(species="elf", skill_proficiencies=["perception"])
    data["abilities"]["wisdom"] = 18
    hero = state[f"{SRD}.entities"]["hero"]
    hero["saving_throw_modifiers"] = dict.fromkeys(data["abilities"], -100)
    result = _run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(params | {"targets": ["hero"]}),
    ).result
    assert result["context"]["_control_save_rolls"] == [None]
    assert state[f"{SRD}.entities"]["hero"]["does_not_sleep"] is True
    passive = _run(
        "read_srd_passive_perception",
        state,
        participant_data=data,
        target_state="hero",
        base_passive_perception=999,
    ).result
    assert passive["context"]["_passive_perception"] == 20


def test_enthrall_uses_saved_hostility_and_held_objects_require_saved_inventory(monkeypatch):
    state, data, params = _case(monkeypatch, "enthrall")
    state[f"{SRD}.entities"]["guard"]["fighting_actor_ids"] = ["hero"]
    result = _run("cast_spell_outside_combat", state, participant_data=data, **params).result
    assert result["context"]["_control_save_rolls"] == [None]
    state[f"{SRD}.entities"]["guard"]["inventory"] = ["spear"]
    _reject(state, "record_srd_control_held_items", target_state="guard", held_items=["wand"])
    _run(
        "record_srd_control_held_items",
        state,
        target_state="guard",
        held_items=["spear"],
        held_attack_names=["Spear"],
    )
    assert state[f"{SRD}.entities"]["guard"]["held_items"] == ["spear"]


@pytest.mark.parametrize("failure", ["unknown_language", "deafened", "harmful", "word_limit"])
def test_suggestion_language_and_activity_eligibility_are_enforced(monkeypatch, failure):
    state, data, params = _case(monkeypatch, "suggestion")
    target = state[f"{SRD}.entities"]["guard"]
    if failure == "unknown_language":
        target["languages"] = ["infernal"]
    if failure == "deafened":
        target["conditions"] = ["deafened"]
    if failure == "harmful":
        params["activity_harmful"] = True
    if failure == "word_limit":
        params["activity_words"] = ["word"] * 26
    before = deepcopy(state)
    if failure in ("harmful", "word_limit"):
        with pytest.raises(
            RulesetPreconditionRejected if failure == "harmful" else PackageRuntimeError
        ):
            _run("cast_spell_outside_combat", state, participant_data=data, **params)
        assert state == before
    else:
        result = _run("cast_spell_outside_combat", state, participant_data=data, **params).result
        assert result["context"]["_order_save_rolls"] == [None]
        assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
        assert "charmed" not in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_command_obeys_actual_mind_blank_source_even_without_charmed_condition(monkeypatch):
    state, data, params = _case(monkeypatch, "command")
    state[f"{SRD}.state"].update(lasting_tick=0, turn_effects=[])
    _run(
        "install_srd_timed_actor_defense",
        state,
        target_state="guard",
        caster_state="other",
        spell="mind_blank",
        spellcasting_modifier=4,
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state[f"{SRD}.entities"]["guard"].get("has_control_order_sources") is not True
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2


def test_suppressed_command_retires_after_its_actual_next_turn_instead_of_restoring_late(
    monkeypatch,
):
    state, _, _, _ = _cast(monkeypatch, "command", command="halt")
    source_id, _ = _source(state)
    state[f"{SRD}.state"]["timed_condition_sources"][source_id]["suppressed"] = True
    _run("resolve_srd_control_behavior_projection", state, target_state="guard")
    _turn(state)
    assert state[f"{SRD}.combat"]["action_available"] is True
    _run("end_combat_turn", state)
    assert state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"] is False


def test_public_order_tools_cannot_select_an_alternate_source_pool_or_clock():
    record = json.loads(
        (Path(__file__).parents[1] / "rulesets/dnd-srd-5.2.1/package-record.json").read_text(
            encoding="utf-8"
        )
    )
    declarations = {tool["name"]: tool["input_schema"] for tool in record["tools"]}
    assert all("lasting_state" not in declarations[name]["properties"] for name in PUBLIC_TOOLS)
