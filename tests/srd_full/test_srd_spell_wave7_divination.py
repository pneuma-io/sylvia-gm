"""Divination continuation consumes actual touch, source instances and actor statistics."""

from copy import deepcopy

import pytest

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_spell_wave3_information import SRD, _entity, _wall
from tests.srd_full.test_srd_spell_wave4_divination import _case, _cast


def _start_identify(state, data, params):
    _run(
        "begin_srd_spell_cast",
        state,
        participant_data=data,
        **(
            _begin(params)
            | {
                "divination_subject": params["divination_subject"],
                "divination_touch_uninterrupted": True,
            }
        ),
    )


def _finish_identify(state, data, params):
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=10,
        uninterrupted=True,
        divination_touch_uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    _run("cast_spell", state, participant_data=data, **params)
    return state[f"{SRD}.entities"]["hero"]["last_identify"]["information"]


def test_identify_preserved_touch_during_actual_movement_finishes():
    state, data, params = _case("identify")
    _start_identify(state, data, params)
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[1, 0, 0])
    assert state[f"{SRD}.entities"]["hero"]["extended_casting"]["spell"] == "identify"
    assert any("charges: 3" in item for item in _finish_identify(state, data, params))


def test_identify_touch_subject_departure_retires_only_actual_cast():
    state, data, params = _case("identify")
    params["divination_subject"] = "guard"
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = [5, 0, 0]
    _start_identify(state, data, params)
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[20, 0, 0])
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["extended_casting"] is None
    assert hero["resources"]["spell_slot_1"] == 3


@pytest.mark.parametrize("rating,expected", [(79, False), (80, True)])
def test_nature_powerful_creature_uses_admitted_cr_not_scene_numeric_fact(rating, expected):
    state, data, params = _case("commune_with_nature")
    params["divination_nature_facts"] = ["powerful_creature", "water", "portals"]
    params["divination_subject"] = ""
    state[f"{SRD}.entities"]["guard"].update(creature_type="fiend", challenge_rating_eighths=rating)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["metadata"]["challenge_rating"] = 1000 if rating == 79 else 0
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
    _run("cast_spell", state, participant_data=data, **params)
    facts = state[f"{SRD}.entities"]["hero"]["last_nature_divination"]["facts"]
    assert any("powerful_creature: guard" in fact for fact in facts) is expected


def test_identify_reads_actual_late_guidance_source_and_ignores_its_expired_predecessor():
    state, data, params = _case("identify")
    params["divination_subject"] = "guard"
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["position"] = [5, 0, 0]
    for identity in ["elder", "newer"]:
        _run(
            "prepare_srd_creature",
            state,
            creature_id=identity,
            name=identity,
            source="Synthetic public test caster",
            armor_class=12,
            hit_points=25,
            speed=30,
            initiative_modifier=0,
            size_rank=2,
            constitution_save=6,
            spellcasting_ability="wisdom",
            spellcasting_modifier=3,
            spell_attack_bonus=7,
            spell_save_dc=15,
            spells=[{"spell": "guidance", "uses_per_day": 0}],
        )
        scene["entities"].append(_entity(identity, [0, 0, 0], participant_ref=identity))
    guidance = {
        key: deepcopy(value)
        for key, value in params.items()
        if not key.startswith("divination") and not key.startswith("information")
    }
    for key in ["caster", "slot_resource", "material_item_ids"]:
        guidance.pop(key, None)
    guidance.update(
        spell="guidance", target="guard", selected_skill="athletics", caster_state="elder"
    )
    _run("cast_spell_outside_combat", state, **guidance)
    _start_identify(state, data, params)
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=9,
        uninterrupted=True,
        divination_touch_uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    guidance["caster_state"] = "newer"
    _run("cast_spell_outside_combat", state, **guidance)
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=1,
        uninterrupted=True,
        divination_touch_uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    _run("cast_spell", state, participant_data=data, **params)
    information = state[f"{SRD}.entities"]["hero"]["last_identify"]["information"]
    assert information.count("guidance") == 1


def test_identify_scene_alias_enumerates_actual_target_instance_and_its_expiry():
    state, data, params = _case("identify")
    hero = state[f"{SRD}.entities"]["hero"]
    data["spellcasting_grants"][0]["spells"].append("tongues")
    hero["prepared_spells"].append("tongues")
    data["wizard_spellbook"].append("tongues")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1].update(id="guard-scene", position=[5, 0, 0])
    params["divination_subject"] = "guard-scene"
    tongues_state, _, _ = _case("tongues")
    material = deepcopy(tongues_state[f"{SRD}.entities"]["hero"]["spell_materials"][0])
    material.update(id="tongues-material", inventory_item="tongues clay model")
    hero["spell_materials"].append(material)
    hero["inventory"].append(material["inventory_item"])
    tongues = deepcopy(params)
    tongues.update(
        spell="tongues", slot_resource="spell_slot_3", material_item_ids=["tongues-material"]
    )
    _run("cast_spell", state, participant_data=data, **tongues)
    _start_identify(state, data, params)
    assert _finish_identify(state, data, params).count("tongues") == 1
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    _start_identify(state, data, params)
    assert "tongues" not in _finish_identify(state, data, params)


def _capture_observer(monkeypatch):
    import sylvia_gm.application.package_runtime as runtime

    original = runtime.execute_tool
    answers = []

    def observe(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[0] == "resolve_srd_observer_visibility" and result.success:
            answers.append(dict(result.context))
        return result

    monkeypatch.setattr(runtime, "execute_tool", observe)
    return answers


def test_observer_tracking_preserves_other_hidden_and_does_not_grant_wall_or_blinded_sight(
    monkeypatch,
):
    answers = _capture_observer(monkeypatch)
    state, _, _, _ = _cast("mind_spike")
    entities = state[f"{SRD}.entities"]
    entities["guard"].update(hidden=True, hidden_check_total=40, conditions=["invisible"])
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"].append(_entity("other", [0, 5, 0], participant_ref="other"))

    def query(observer):
        _run(
            "resolve_srd_observer_visibility", state, observer_state=observer, subject_state="guard"
        )
        return answers[-1]

    tracked = query("hero")
    assert tracked["_observer_can_see_subject"] is True
    assert tracked["_observer_knows_location"] is True
    assert tracked["_observer_subject_hidden"] is False
    other = query("other")
    assert other["_observer_can_see_subject"] is False
    assert other["_observer_knows_location"] is False
    assert other["_observer_subject_hidden"] is True
    assert entities["guard"]["hidden"] is True
    scene["geometry"] = [_wall(1)]
    obstructed = query("hero")
    assert obstructed["_observer_can_see_subject"] is False
    assert obstructed["_observer_knows_location"] is True
    scene["geometry"] = []
    entities["hero"]["conditions"] = ["blinded"]
    blinded = query("hero")
    assert blinded["_observer_can_see_subject"] is False
    assert blinded["_observer_knows_location"] is True


def _search_turn(state):
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="hero",
        current_actor="hero",
        action_available=True,
        normal_action_spent=False,
    )


def test_search_uses_actual_hide_dc_and_does_not_disclose_failed_untracked_location():
    state, data, _, _ = _cast("mind_spike")
    target = state[f"{SRD}.entities"]["guard"]
    target.update(hidden=True, hidden_check_total=80, conditions=["invisible"])
    _search_turn(state)
    choices = {
        "actor": "hero",
        "actor_state": "hero",
        "target_state": "guard",
        "skill": "perception",
        "target_number": 1,
        "proficiency_factor": "none",
        "circumstantial_modifier": 0,
        "advantage": False,
        "disadvantage": False,
    }
    _run("search_srd_action", state, participant_data=data, **choices)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["last_search_target"]["location_known"] is True
    assert hero["last_search_target"]["position"] == [20, 0, 0]
    assert state[f"{SRD}.combat"]["action_available"] is False
    _run("end_concentration", state, caster_state="hero")
    _search_turn(state)
    _run("search_srd_action", state, participant_data=data, **choices)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["last_search_target"]["location_known"] is False
    assert hero["last_search_target"]["position"] is None
    assert hero["last_search_target"]["hidden_from_observer"] is True
    assert hero["last_search_target"]["tick"] == state[f"{SRD}.state"]["lasting_tick"]
    assert target["hidden"] is True


def test_hide_uses_actual_tracking_enemy_and_retains_hidden_for_other_observers(monkeypatch):
    answers = _capture_observer(monkeypatch)
    state, data, params = _case("mind_spike")
    _run(
        "prepare_srd_creature",
        state,
        creature_id="watcher",
        name="Watcher",
        source="Synthetic public test caster",
        armor_class=12,
        hit_points=100,
        speed=30,
        initiative_modifier=0,
        size_rank=2,
        constitution_save=6,
        spellcasting_ability="wisdom",
        spellcasting_modifier=3,
        spell_attack_bonus=7,
        spell_save_dc=15,
        spells=[{"spell": "mind_spike", "uses_per_day": 0}],
    )
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"].append(_entity("watcher", [20, 0, 0], participant_ref="watcher"))
    npc = deepcopy(params)
    for key in ["caster", "slot_resource", "material_item_ids"]:
        npc.pop(key, None)
    npc.update(caster_state="watcher", target="hero")
    state[f"{SRD}.entities"]["hero"].update(hp=500, max_hp=500)
    state[f"{SRD}.entities"]["hero"]["saving_throw_modifiers"]["wisdom"] = -100
    _run("cast_spell_outside_combat", state, **npc)
    assert state[f"{SRD}.entities"]["watcher"]["concentration_spell_id"] == "mind_spike"
    assert any(
        r.get("spell") == "mind_spike" and r.get("target") == "hero"
        for r in state[f"{SRD}.state"]["turn_effects"]
    )
    assert state[f"{SRD}.state"]["has_divination_spells"] is True
    state[f"{SRD}.entities"]["hero"]["fighting_actor_ids"] = ["watcher"]
    _search_turn(state)
    choices = {
        "actor": "hero",
        "actor_state": "hero",
        "heavily_obscured": True,
        "cover": "none",
        "outside_enemy_line_of_sight": True,
        "enemy_within_5_feet": False,
        "circumstantial_modifier": 100,
        "advantage": False,
        "disadvantage": False,
    }
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="actual enemy sight"):
        _run("hide_srd_action", state, participant_data=data, **choices)
    assert state == before
    with pytest.raises(RulesetPreconditionRejected, match="every saved current enemy"):
        _run("hide_srd_action", state, participant_data=data, enemy_states=[], **choices)
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [_wall(1)]
    _run("hide_srd_action", state, participant_data=data, **choices)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["hidden"] is True
    assert hero["hidden_check_total"] >= 100
    assert state[f"{SRD}.combat"]["action_available"] is False
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["geometry"] = []
    scene["entities"].append(_entity("other", [0, 5, 0], participant_ref="other"))
    _run("resolve_srd_observer_visibility", state, observer_state="watcher", subject_state="hero")
    assert answers[-1]["_observer_subject_hidden"] is False
    assert answers[-1]["_observer_can_see_subject"] is True
    _run("resolve_srd_observer_visibility", state, observer_state="other", subject_state="hero")
    assert answers[-1]["_observer_subject_hidden"] is True
    assert answers[-1]["_observer_can_see_subject"] is False
    assert state[f"{SRD}.entities"]["hero"]["hidden"] is True


def test_public_identify_moving_contact_survives_reopen_and_retry(tmp_path, monkeypatch):
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from tests import test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_npc_spell_casting import _act

    monkeypatch.setattr(public, "_spell_case", _case)
    database = tmp_path / "identify-moving.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "identify")
        mechanics = StatelessMcpMechanics(owner)
        schemas = {tool.name: tool.input_schema for tool in mechanics.list_tools("casting")}
        assert "move_srd_scene_creature" in schemas
        begin, begin_command = _act(
            owner,
            "begin_srd_spell_cast",
            **(
                _begin(params)
                | {"divination_subject": "orb", "divination_touch_uninterrupted": True}
            ),
        )
        assert begin.status is ActionStatus.COMMITTED
        moved, move_command = _act(
            owner, "move_srd_scene_creature", actor_state="hero", destination=[1, 0, 0]
        )
        assert moved.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(move_command) == moved
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(begin_command) == begin
        assert mechanics.execute_action(move_command) == moved
        continued, continue_command = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=10,
            uninterrupted=True,
            divination_touch_uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert continued.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(continue_command) == continued
        cast, cast_command = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED
        after = owner.snapshot("casting")
        assert any(
            "charges: 3" in value
            for value in after.state[f"{SRD}.entities"]["hero"]["last_identify"]["information"]
        )
        assert after.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
        assert mechanics.execute_action(cast_command) == cast
        assert owner.snapshot("casting").state == after.state
