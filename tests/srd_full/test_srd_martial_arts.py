"""Monk alternatives use one class/equipment calculation across attacks and reactions."""

from copy import deepcopy

import pytest

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_attack_budget import _attack
from tests.srd_full.test_srd_unarmed_creatures import _case, _strike


@pytest.mark.parametrize("effect", ["damage", "grapple"])
def test_monk_unarmed_strike_uses_dexterity_and_earned_damage_die(effect):
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": 5}], level=5)
    data["abilities"].update(strength=10, dexterity=18)
    result = _strike(state, data, effect, attack_ability="dexterity", use_martial_arts_die=True)
    context = result.result["context"]
    assert context["_save_dc"] == 15
    if effect == "damage":
        assert result.result["result_card"]["equation"]["modifier"] == 7
        if context["_unarmed_success"]:
            rolls = context["_unarmed_damage_roll"]["results"]
            assert all(1 <= face <= 8 for face in rolls)
            assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000 - sum(rolls) - 4


@pytest.mark.parametrize("level,die", [(1, "1d6"), (5, "1d8"), (11, "1d10"), (17, "1d12")])
def test_martial_arts_die_uses_monk_level_not_total_level(level, die):
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": level}, {"id": "fighter", "level": 2}],
                level=level + 2)
    result = _run("derive_srd_martial_arts", state, participant_data=data,
                  actor="hero", actor_state="hero")
    assert result.result["context"]["_martial_arts_die"] == die
    assert result.result["context"]["_martial_arts_available"] is True


@pytest.mark.parametrize("problem", ["armor", "shield", "non_monk_weapon", "unowned", "class"])
def test_martial_arts_rejects_ineligible_choices_without_spending(problem):
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": 5}], level=5)
    hero = state[f"{SRD}.entities"]["hero"]
    choices = {"attack_ability": "dexterity", "use_martial_arts_die": True}
    if problem in ("armor", "shield"):
        hero[f"worn_{problem}"] = {"inventory_item": problem}
    elif problem in ("non_monk_weapon", "unowned"):
        choices["other_wielded_weapons"] = ["greatsword"]
        if problem == "non_monk_weapon":
            hero["inventory"].append("greatsword")
    else:
        data["classes"] = [{"id": "fighter", "level": 5}]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _strike(state, data, "damage", **choices)
    assert state == before


def test_monk_reaction_uses_dexterity_with_monk_weapon_and_carried_non_monk_weapon():
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": 5}], level=5)
    data["abilities"].update(strength=10, dexterity=18)
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(available_reactions=["opportunity_attack"], melee_reach=5)
    hero["inventory"].append("greatsword")
    state[f"{SRD}.combat"].update(active_actor="guard", current_actor="guard")
    _run("trigger_opportunity_attack", state, participant_data=data,
         reactor_state="hero", actor_can_see_target=True, target_can_see_actor=True,
         attack_distance=5, weapon="unarmed_strike")
    result = _run("resolve_unarmed_opportunity_attack", state, participant_data=data,
                  effect="shove_prone", target_save_ability="strength",
                  attack_ability="dexterity", other_wielded_weapons=["spear"],
                  target_is_charmer=False, fear_source_in_sight=False)
    assert result.result["context"]["_save_dc"] == 15
    assert state[f"{SRD}.combat"]["action_available"] is True
    assert state[f"{SRD}.entities"]["hero"]["reaction_available"] is False


@pytest.mark.parametrize("reaction", [False, True])
def test_monk_weapon_attack_and_reaction_use_the_shared_profile(reaction):
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": 5}], level=5)
    data["abilities"].update(strength=10, dexterity=18)
    if reaction:
        state[f"{SRD}.combat"].update(active_actor="guard", current_actor="guard")
        state[f"{SRD}.entities"]["hero"].update(
            available_reactions=["opportunity_attack"], melee_reach=5,
        )
        _run("trigger_opportunity_attack", state, participant_data=data,
             reactor_state="hero", actor_can_see_target=True, target_can_see_actor=True,
             attack_distance=5, weapon="spear", attack_ability="dexterity",
             use_martial_arts_die=True)
        pending = state[f"{SRD}.combat"]["pending_reaction"]
        assert pending["attack_modifier"] == 7 and pending["damage_expression"] == "1d8"
    else:
        result = _attack(state, data, weapon="spear", attack_ability="dexterity",
                         use_martial_arts_die=True)
        assert result.result["context"]["_attack_modifier"] == 7
        assert result.result["context"]["_damage_expression"] == "1d8"


@pytest.mark.parametrize("level,sequence,count,spent", [
    (1, "martial_arts", 1, 0), (2, "flurry", 2, 1), (10, "flurry", 3, 1),
])
def test_monk_bonus_strikes_preserve_action_and_charge_focus_once(level, sequence, count, spent):
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": level}], level=level)
    hero = state[f"{SRD}.entities"]["hero"]
    focus = level if level >= 2 else 0
    hero["resources"]["focus_point"] = focus
    hero.setdefault("resource_maxima", {})["focus_point"] = focus
    for index in range(count):
        _run("resolve_monk_bonus_strike", state, participant_data=data,
             actor="hero", actor_state="hero", target_state="guard", effect="damage",
             sequence=sequence if index == 0 else "flurry_followup", distance=5,
             actor_can_see_target=True, target_can_see_actor=True,
             target_is_charmer=False, fear_source_in_sight=False)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["resources"]["focus_point"] == focus - spent
    assert hero["bonus_action_available"] is False
    assert hero.get("flurry_attacks_remaining", 0) == 0
    assert state[f"{SRD}.combat"]["action_available"] is True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("resolve_monk_bonus_strike", state, participant_data=data,
             actor="hero", actor_state="hero", target_state="guard", effect="damage",
             sequence="flurry_followup", distance=5,
             actor_can_see_target=True, target_can_see_actor=True,
             target_is_charmer=False, fear_source_in_sight=False)
    assert state == before


@pytest.mark.parametrize("problem", ["spent", "focus", "capacity", "armor", "class", "free_hand"])
def test_illegal_monk_bonus_strike_preserves_all_state(problem):
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": 2}], level=2)
    hero = state[f"{SRD}.entities"]["hero"]
    hero["resources"]["focus_point"] = 2
    hero["resource_maxima"] = {"focus_point": 2}
    sequence, effect = "flurry", "damage"
    if problem == "spent":
        hero["bonus_action_available"] = False
    elif problem == "focus":
        hero["resources"]["focus_point"] = 0
    elif problem == "capacity":
        hero["resource_maxima"]["focus_point"] = 99
    elif problem == "armor":
        hero["worn_armor"] = {"inventory_item": "leather_armor"}
        sequence = "martial_arts"
    elif problem == "class":
        data["classes"] = [{"id": "fighter", "level": 2}]
    else:
        effect = "grapple"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("resolve_monk_bonus_strike", state, participant_data=data,
             actor="hero", actor_state="hero", target_state="guard", effect=effect,
             sequence=sequence, distance=5, target_save_ability="strength", hand_free=False,
             actor_can_see_target=True, target_can_see_actor=True,
             target_is_charmer=False, fear_source_in_sight=False)
    assert state == before


def test_flurry_can_use_strength_while_armored_and_cannot_carry_to_next_turn():
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": 2}], level=2)
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(worn_armor={"inventory_item": "leather_armor"},
                resource_maxima={"focus_point": 2})
    hero["resources"]["focus_point"] = 2
    _run("resolve_monk_bonus_strike", state, participant_data=data,
         actor="hero", actor_state="hero", target_state="guard", effect="shove_prone",
         sequence="flurry", distance=5, target_save_ability="strength", target_forego_save=True,
         actor_can_see_target=True, target_can_see_actor=True,
         target_is_charmer=False, fear_source_in_sight=False)
    assert state[f"{SRD}.entities"]["hero"]["flurry_attacks_remaining"] == 1
    assert "prone" in state[f"{SRD}.entities"]["guard"]["conditions"]
    _run("end_combat_turn", state, participant_data=data, actor="hero")
    assert state[f"{SRD}.entities"]["hero"]["flurry_attacks_remaining"] == 0
    assert state[f"{SRD}.combat"]["monk_bonus_strike_available"] is False


@pytest.mark.parametrize("level,expected", [(1, 0), (2, 2), (10, 10)])
def test_creation_derives_focus_from_monk_level(level, expected):
    from sylvia_gm.application.initial_character_state import materialize_initial_character_state
    from tests.srd_full.test_reusable_participants import (
        _participant,
        _participant_authority,
        _starting_state,
    )

    _, declaration = _participant_authority()
    profile = _participant("Monk")
    profile.update(level=level, classes=[{"id": "monk", "level": level}], fighting_styles=[])
    if level >= 3:
        profile["subclasses"] = {"monk": "warrior_of_the_open_hand"}
    authored = _starting_state()
    authored.update(worn_armor=None, worn_shield=None)
    completed = materialize_initial_character_state(
        profile, authored, entity_state_schema=declaration.entity_state_schema,
        initial_state_calculations=declaration.initial_state_calculations,
        initial_state_validation=declaration.initial_state_validation,
    )
    assert completed["resources"].get("focus_point", 0) == expected
    assert completed["resource_maxima"].get("focus_point", 0) == expected


def test_focus_level_up_preserves_spent_points_and_reopens(tmp_path):
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from sylvia_gm.domain.errors import CommandRejected
    from tests.srd_full.test_srd_advancement import award, build, command, create, resolve

    database = tmp_path / "monk-level.db"
    with AuthorityOwner.open(database, "test") as owner:
        create(owner, level=2, participant_overrides={
            "classes": [{"id": "monk", "level": 2}], "fighting_styles": [],
        }, entity_overrides={
            "resources": {"hit_die": 0, "focus_point": 0},
            "resource_maxima": {"hit_die": 2, "focus_point": 2},
        })
        award(owner)
        proposal = build(owner)
        proposal["class_id"] = "monk"
        proposal["build"][0]["subclasses"] = {"monk": "warrior_of_the_open_hand"}
        proposal["build"][0]["features"] = [{
            "id": "subclass", "source": "SRD Monk", "choice": "Warrior of the Open Hand",
        }]
        proposal["build"][0]["resources"] = [
            {"id": "focus_point", "maximum": 99, "source": "Monk level"},
        ]
        before = owner.snapshot("advancement")
        with pytest.raises(CommandRejected, match="Focus capacity"):
            owner.execute_action(command(owner, "level_up_srd_character", proposal, "invented"))
        assert owner.snapshot("advancement").state == before.state
        proposal["build"][0]["resources"] = []
        waiting = owner.execute_action(command(owner, "level_up_srd_character", proposal, "level"))
        assert resolve(owner, waiting).status is ActionStatus.COMMITTED
        saved = owner.snapshot("advancement")
        hero = saved.state[f"{SRD}.entities"]["hero"]
        assert hero["resource_maxima"]["focus_point"] == 3
        assert hero["resources"]["focus_point"] == 1
        assert hero["hp"] == 5
    with AuthorityOwner.open(database, "test") as owner:
        assert owner.snapshot("advancement").state == saved.state



@pytest.mark.parametrize("monk_level", [2, 6])
def test_public_bonus_strikes_survive_reopen_after_action_spent_and_restore_on_rest(
    tmp_path, monkeypatch, monk_level,
):
    import io
    from dataclasses import replace
    from random import Random

    from sylvia_gm import dice
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
    from tests.srd_full.full_package_fixture import bundled_srd_archive
    from sylvia_gm.domain.authority import ActionStatus, StateValue, WriterRole
    from tests.srd_full.test_srd_second_wind import _installed_fighter

    monkeypatch.setattr(dice, "_rng", Random(6))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "monk-combat.db"
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": monk_level}],
                level=monk_level, fighting_styles=[])
    if monk_level == 6:
        data["subclasses"] = {"monk": "warrior_of_the_open_hand"}
        state[f"{SRD}.entities"]["guard"].update(
            armor_class=1, damage_immunities=["bludgeoning"],
            saving_throw_modifiers={"constitution": 0},
        )
    state[f"{SRD}.combat"].update(turn_active=False, phase="awaiting_turn_start")
    state[f"{SRD}.entities"]["hero"].update(
        resource_maxima={"focus_point": monk_level}, resources={"focus_point": monk_level},
    )
    with AuthorityOwner.open(database, "test") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(bundled_srd_archive()), install_id="srd",
        )
        monk = _installed_fighter(candidate.package_lock, fighter=True)
        monk = replace(monk, data={**monk.data, **data})
        owner.create_campaign(CampaignCreate(
            campaign_id="monk", display_name="Synthetic Monk", writer=WriterRole.DM,
            package_lock=candidate.package_lock, turn_id="turn", participants=(monk,),
            initial_state=tuple(StateValue(SRD, key, state[f"{SRD}.{key}"])
                                for key in ("combat", "entities", "state")),
        ))
        sequence = 0

        def execute(name, **parameters):
            nonlocal sequence
            sequence += 1
            action = ActionCommand(
                campaign_id="monk", action_id=str(sequence), idempotency_key=str(sequence),
                owner_id="test", writer=WriterRole.DM,
                expected_revision=owner.snapshot("monk").revision,
                turn_id="turn", package_id=SRD, tool_name=name, parameters=parameters,
            )
            result = StatelessMcpMechanics(owner).execute_action(action)
            assert result.status is ActionStatus.COMMITTED
            return action, result

        execute("begin_combat_turn")
        execute("take_dodge_action", actor="hero", actor_state="hero")
        assert owner.snapshot("monk").state[f"{SRD}.combat"]["action_available"] is False
        names = {tool.name for tool in StatelessMcpMechanics(owner).list_tools("monk")}
        assert "resolve_monk_bonus_strike" in names
        assert "consume_srd_bonus_action" not in names
        action, receipt = execute(
            "resolve_monk_bonus_strike", actor="hero", actor_state="hero", target_state="guard",
            sequence="flurry", effect="damage", distance=5,
            unarmed_damage_type="force" if monk_level == 6 else "bludgeoning",
            open_hand_effect="addle" if monk_level == 6 else "none",
            use_stunning_strike=monk_level == 6, stunning_forego_save=True,
            actor_can_see_target=True, target_can_see_actor=True,
            target_is_charmer=False, fear_source_in_sight=False,
        )
        saved = owner.snapshot("monk")
        assert saved.state[f"{SRD}.entities"]["hero"]["flurry_attacks_remaining"] == 1
        if monk_level == 6:
            guard = saved.state[f"{SRD}.entities"]["guard"]
            assert guard["hp"] == 996 and guard["opportunity_attacks_blocked"] is True
            assert "stunned" in guard["conditions"]
    with AuthorityOwner.open(database, "test") as owner:
        assert owner.snapshot("monk") == saved
        assert owner.execute_action(action) == receipt
        followup = replace(action, action_id="followup", idempotency_key="followup",
                           expected_revision=saved.revision,
                           parameters={**action.parameters, "sequence": "flurry_followup",
                                       "use_stunning_strike": False})
        result = StatelessMcpMechanics(owner).execute_action(followup)
        assert result.status is ActionStatus.COMMITTED
        names = {tool.name for tool in StatelessMcpMechanics(owner).list_tools("monk")}
        assert "resolve_monk_bonus_strike" not in names
        hero = owner.snapshot("monk").state[f"{SRD}.entities"]["hero"]
        assert hero["resources"]["focus_point"] == monk_level - (2 if monk_level == 6 else 1)
        execute("end_combat_encounter", end_reason="fighting_stopped")
        execute("begin_srd_rest", target_state="hero", kind="short", activity="light")
        execute("advance_srd_lasting_time", elapsed_rounds=600)
        execute("complete_srd_rest", target_state="hero")
        hero = owner.snapshot("monk").state[f"{SRD}.entities"]["hero"]
        assert hero["resources"]["focus_point"] == monk_level


@pytest.mark.parametrize("consumer", ["feature", "spell", "light_weapon"])
def test_other_bonus_action_consumers_remove_monk_option(consumer):
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": 2}], level=2)
    _run("refresh_srd_monk_bonus_availability", state, participant_data=data, actor_state="hero")
    assert state[f"{SRD}.combat"]["monk_bonus_strike_available"] is True
    if consumer == "feature":
        state[f"{SRD}.entities"]["hero"]["available_bonus_actions"] = ["synthetic_grant"]
        _run("spend_srd_bonus_action", state, participant_data=data,
             actor="hero", actor_state="hero", bonus_action="synthetic_grant")
    elif consumer == "spell":
        _run("spend_spell_casting_budget", state, participant_data=data,
             caster_state="hero", casting_time="bonus_action", slot_resource="cantrip")
    else:
        _attack(state, data, weapon="dagger")
        _attack(state, data, weapon="scimitar", attack_sequence="light_bonus")
    assert state[f"{SRD}.entities"]["hero"]["bonus_action_available"] is False
    assert state[f"{SRD}.combat"]["monk_bonus_strike_available"] is False


@pytest.mark.parametrize("feature,spend_focus", [
    ("patient_defense", False), ("patient_defense", True),
    ("step_of_the_wind", False), ("step_of_the_wind", True),
])
def test_monk_focus_actions_apply_effects_without_spending_normal_action(feature, spend_focus):
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": 2}], level=2)
    hero = state[f"{SRD}.entities"]["hero"]
    initial_focus = 2 if spend_focus else 0
    hero.update(resources={"focus_point": initial_focus}, resource_maxima={"focus_point": 2})
    state[f"{SRD}.combat"].update(movement_spent=5, movement_remaining=25)
    result = _run("use_monk_focus_action", state, participant_data=data,
                  actor="hero", actor_state="hero", feature=feature, spend_focus=spend_focus)
    hero, combat = state[f"{SRD}.entities"]["hero"], state[f"{SRD}.combat"]
    assert hero["resources"]["focus_point"] == initial_focus - int(spend_focus)
    assert hero["bonus_action_available"] is False and combat["action_available"] is True
    if feature == "patient_defense":
        assert combat["disengaged_actor"] == "hero"
        assert hero.get("dodging", False) is spend_focus
    else:
        assert combat["movement_remaining"] == 55 and combat["dashes_taken"] == 1
        assert (combat.get("disengaged_actor") == "hero") is spend_focus
        assert hero.get("jump_distance_multiplier", 1) == (2 if spend_focus else 1)
    assert result.result["result_card"]["title"] == (
        "Patient Defense" if feature == "patient_defense" else "Step of the Wind"
    )


@pytest.mark.parametrize("existing,choice", [(0, "higher"), (99, "higher"), (99, "new")])
def test_heightened_patient_defense_rolls_temporary_hp_without_stacking(existing, choice):
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": 10}], level=10)
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(resources={"focus_point": 10}, resource_maxima={"focus_point": 10},
                temp_hp=existing)
    result = _run("use_monk_focus_action", state, participant_data=data,
                  actor="hero", actor_state="hero", feature="patient_defense", spend_focus=True,
                  temporary_hp_choice=choice)
    context = result.result["context"]
    rolls = context["_focus_temp_hp_roll"]["results"]
    assert len(rolls) == 2 and all(1 <= face <= 8 for face in rolls)
    expected = sum(rolls) if choice == "new" else max(existing, sum(rolls))
    assert state[f"{SRD}.entities"]["hero"]["temp_hp"] == expected
    assert state[f"{SRD}.entities"]["hero"]["hp"] == hero["hp"]


def test_step_wind_jump_uses_double_distance_and_expires_at_turn_end():
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": 2}], level=2)
    data["abilities"]["strength"] = 10
    state[f"{SRD}.entities"]["hero"].update(
        resources={"focus_point": 2}, resource_maxima={"focus_point": 2},
    )
    state[f"{SRD}.combat"].update(movement_spent=0)
    _run("use_monk_focus_action", state, participant_data=data,
         actor="hero", actor_state="hero", feature="step_of_the_wind", spend_focus=True)
    _run("spend_combat_movement", state, participant_data=data,
         distance=10, difficult_terrain=False)
    _run("spend_combat_movement", state, participant_data=data,
         distance=20, difficult_terrain=False, movement_kind="long_jump", running_start=True)
    assert state[f"{SRD}.combat"]["movement_remaining"] == 30
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="jump"):
        _run("spend_combat_movement", state, participant_data=data,
             distance=21, difficult_terrain=False, movement_kind="long_jump", running_start=True)
    assert state == before
    _run("end_combat_turn", state, participant_data=data, actor="hero")
    assert state[f"{SRD}.entities"]["hero"]["jump_distance_multiplier"] == 1


def test_heightened_step_moves_one_willing_companion_without_spending_their_movement():
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": 10}], level=10)
    entities = state[f"{SRD}.entities"]
    entities["hero"].update(resources={"focus_point": 10}, resource_maxima={"focus_point": 10})
    entities["guard"].update(movement_remaining={"ground": 30}, size_rank=3)
    state[f"{SRD}.combat"].update(movement_spent=0)
    _run("use_monk_focus_action", state, participant_data=data,
         actor="hero", actor_state="hero", feature="step_of_the_wind", spend_focus=True,
         companion=[{"entity_id": "guard", "distance": 5, "willing": True}])
    result = _run("spend_combat_movement", state, participant_data=data,
                  distance=15, difficult_terrain=True)
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["last_forced_movement_feet"] == 15
    assert guard["last_forced_movement_source"] == "hero"
    assert guard["movement_remaining"]["ground"] == 30
    assert result.result["context"]["_carried_companion_name"] == "Guard"
    _run("end_combat_turn", state, participant_data=data, actor="hero")
    assert state[f"{SRD}.combat"]["step_wind_companion"] is None


@pytest.mark.parametrize("problem", ["focus", "spent", "level", "unwilling", "distant", "size"])
def test_invalid_focus_action_or_companion_is_atomic(problem):
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": 10}], level=10)
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(resources={"focus_point": 10}, resource_maxima={"focus_point": 10})
    companion = {"entity_id": "guard", "distance": 5, "willing": True}
    if problem == "focus":
        hero["resources"]["focus_point"] = 0
    elif problem == "spent":
        hero["bonus_action_available"] = False
    elif problem == "level":
        data.update(classes=[{"id": "monk", "level": 9}], level=9)
        hero.update(resources={"focus_point": 9}, resource_maxima={"focus_point": 9})
    elif problem == "unwilling":
        companion["willing"] = False
    elif problem == "distant":
        companion["distance"] = 6
    else:
        state[f"{SRD}.entities"]["guard"]["size_rank"] = 4
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("use_monk_focus_action", state, participant_data=data,
             actor="hero", actor_state="hero", feature="step_of_the_wind", spend_focus=True,
             companion=[companion])
    assert state == before


def test_standing_high_jump_preserves_half_feet_and_difficult_landing_roll_has_consequence():
    state, data = _case()
    data["abilities"].update(strength=10, dexterity=3)
    data["skill_proficiencies"] = []
    state[f"{SRD}.combat"].update(movement_spent=0)
    _run("spend_combat_movement", state, participant_data=data,
         distance=1.5, difficult_terrain=False, movement_kind="high_jump")
    assert state[f"{SRD}.combat"]["movement_remaining"] == 28.5
    result = _run("spend_combat_movement", state, participant_data=data,
                  distance=5, difficult_terrain=True, movement_kind="long_jump")
    context = result.result["context"]
    assert state[f"{SRD}.combat"]["movement_remaining"] == 23.5
    assert context["_jump_landing_total"] == context["_jump_landing_roll"]["total"] - 4
    assert ("prone" in state[f"{SRD}.entities"]["hero"]["conditions"]) is (
        context["_jump_landing_outcome"] == "failure"
    )


@pytest.mark.parametrize("feature", ["patient_defense", "step_of_the_wind"])
def test_public_focus_action_reopens_and_preserves_real_effects(tmp_path, feature):
    import io
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
    from tests.srd_full.full_package_fixture import bundled_srd_archive
    from sylvia_gm.domain.authority import ActionStatus, StateValue, WriterRole
    from tests.srd_full.test_srd_second_wind import _installed_fighter

    database = tmp_path / "focus.db"
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": 10}], level=10, fighting_styles=[],
                subclasses={"monk": "warrior_of_the_open_hand"})
    state[f"{SRD}.combat"].update(turn_active=False, phase="awaiting_turn_start")
    state[f"{SRD}.entities"]["hero"].update(
        resources={"focus_point": 10}, resource_maxima={"focus_point": 10},
    )
    sequence = 0

    def execute(name, **parameters):
        nonlocal sequence
        sequence += 1
        action = ActionCommand(
            campaign_id="focus", action_id=str(sequence), idempotency_key=str(sequence),
            owner_id="test", writer=WriterRole.DM,
            expected_revision=owner.snapshot("focus").revision, turn_id="turn",
            package_id=SRD, tool_name=name, parameters=parameters,
        )
        receipt = StatelessMcpMechanics(owner).execute_action(action)
        assert receipt.status is ActionStatus.COMMITTED
        return action, receipt

    with AuthorityOwner.open(database, "test") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(bundled_srd_archive()), install_id="srd",
        )
        actor = _installed_fighter(candidate.package_lock, fighter=True)
        actor = replace(actor, data={**actor.data, **data})
        owner.create_campaign(CampaignCreate(
            campaign_id="focus", display_name="Synthetic Focus", writer=WriterRole.DM,
            package_lock=candidate.package_lock, turn_id="turn", participants=(actor,),
            initial_state=tuple(StateValue(SRD, key, state[f"{SRD}.{key}"])
                                for key in ("combat", "entities", "state")),
        ))
        execute("begin_combat_turn")
        assert "use_monk_focus_action" in {
            tool.name for tool in StatelessMcpMechanics(owner).list_tools("focus")
        }
        companion = ([{"entity_id": "guard", "distance": 5, "willing": True}]
                     if feature == "step_of_the_wind" else [])
        action, receipt = execute("use_monk_focus_action", actor="hero", actor_state="hero",
                                  feature=feature, spend_focus=True, companion=companion)
        if feature == "step_of_the_wind":
            movement, movement_receipt = execute(
                "spend_combat_movement", distance=15, difficult_terrain=False,
                movement_kind="vertical_surface",
            )
            assert "Acrobatic Movement" in str(movement_receipt.result["result_card"])
        saved = owner.snapshot("focus")
        assert saved.state[f"{SRD}.combat"]["action_available"] is True
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["focus_point"] == 9
    with AuthorityOwner.open(database, "test") as owner:
        assert owner.snapshot("focus") == saved
        assert owner.execute_action(action) == receipt
        assert "use_monk_focus_action" not in {
            tool.name for tool in StatelessMcpMechanics(owner).list_tools("focus")
        }
        if feature == "step_of_the_wind":
            assert owner.execute_action(movement) == movement_receipt
            assert owner.snapshot("focus") == saved
            execute("spend_combat_movement", distance=15, difficult_terrain=False,
                    movement_kind="liquid_surface")
            guard = owner.snapshot("focus").state[f"{SRD}.entities"]["guard"]
            assert guard["last_forced_movement_feet"] == 15
            assert guard["last_forced_movement_provokes"] is False
        else:
            hero = saved.state[f"{SRD}.entities"]["hero"]
            assert hero["dodging"] is True and 2 <= hero["temp_hp"] <= 16
        execute("end_combat_turn")
        final = owner.snapshot("focus")
        assert final.state[f"{SRD}.combat"]["step_wind_companion"] is None
        assert final.state[f"{SRD}.entities"]["hero"]["jump_distance_multiplier"] == 1


@pytest.mark.parametrize("route", ["action", "bonus", "reaction"])
def test_empowered_strikes_force_choice_reaches_shared_damage(monkeypatch, route):
    from random import Random

    from sylvia_gm import dice

    monkeypatch.setattr(dice, "_rng", Random(6))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": 6}], level=6)
    hero, guard = state[f"{SRD}.entities"]["hero"], state[f"{SRD}.entities"]["guard"]
    hero.update(resources={"focus_point": 6}, resource_maxima={"focus_point": 6},
                available_reactions=["opportunity_attack"], melee_reach=5)
    guard.update(armor_class=1, damage_immunities=["bludgeoning"])
    if route == "action":
        result = _strike(state, data, "damage", unarmed_damage_type="force")
    elif route == "bonus":
        result = _run("resolve_monk_bonus_strike", state, participant_data=data,
                      actor="hero", actor_state="hero", target_state="guard", effect="damage",
                      sequence="flurry", distance=5, unarmed_damage_type="force",
                      actor_can_see_target=True, target_can_see_actor=True,
                      target_is_charmer=False, fear_source_in_sight=False)
    else:
        state[f"{SRD}.combat"].update(active_actor="guard", current_actor="guard")
        _run("trigger_opportunity_attack", state, participant_data=data,
             reactor_state="hero", actor_can_see_target=True, target_can_see_actor=True,
             attack_distance=5, weapon="unarmed_strike")
        result = _run("resolve_unarmed_opportunity_attack", state, participant_data=data,
                      effect="damage", unarmed_damage_type="force",
                      target_is_charmer=False, fear_source_in_sight=False)
    assert result.result["context"]["_unarmed_success"] is True
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 996
    details = {row["label"]: row["value"] for row in result.result["result_card"]["details"]}
    assert details["Damage type"] == "Force"


def _open_hand(state, data, technique, **choices):
    return _run("resolve_monk_bonus_strike", state, participant_data=data, **{
        "actor": "hero", "actor_state": "hero", "target_state": "guard", "effect": "damage",
        "sequence": "flurry", "distance": 5, "actor_can_see_target": True,
        "target_can_see_actor": True, "target_is_charmer": False, "fear_source_in_sight": False,
        "open_hand_effect": technique, **choices,
    })


@pytest.mark.parametrize("technique", ["addle", "push", "topple"])
def test_open_hand_flurry_uses_fixed_effects_and_wisdom_save_dc(monkeypatch, technique):
    from random import Random

    from sylvia_gm import dice

    monkeypatch.setattr(dice, "_rng", Random(6))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": 3}], level=3,
                subclasses={"monk": "warrior_of_the_open_hand"})
    data["abilities"].update(wisdom=14, dexterity=18)
    hero, guard = state[f"{SRD}.entities"]["hero"], state[f"{SRD}.entities"]["guard"]
    hero.update(resources={"focus_point": 3}, resource_maxima={"focus_point": 3})
    guard.update(armor_class=1, size_rank=5, reaction_available=True,
                 saving_throw_modifiers={"strength": -20, "dexterity": -20})
    result = _open_hand(state, data, technique, open_hand_push_feet=10)
    context = result.result["context"]
    target = state[f"{SRD}.entities"]["guard"]
    assert context["_open_hand_outcome"] == "Applied"
    assert state[f"{SRD}.entities"]["hero"]["resources"]["focus_point"] == 2
    if technique == "addle":
        assert target["opportunity_attacks_blocked"] is True
        assert target["reaction_available"] is True
    else:
        assert context["_open_hand_save_dc"] == 12
        assert context["_open_hand_save_ability"] == (
            "strength" if technique == "push" else "dexterity"
        )
        if technique == "push":
            assert target["last_forced_movement_feet"] == 10
            assert target["last_forced_movement_source"] == "hero"
        else:
            assert "prone" in target["conditions"]


@pytest.mark.parametrize("problem", ["force_level", "force_grapple", "subclass", "not_flurry"])
def test_unearned_monk_hit_options_reject_atomically(problem):
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": 5}], level=5)
    state[f"{SRD}.entities"]["hero"].update(
        resources={"focus_point": 5}, resource_maxima={"focus_point": 5},
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        if problem.startswith("force"):
            if problem == "force_grapple":
                data.update(classes=[{"id": "monk", "level": 6}], level=6)
            _strike(state, data, "grapple" if problem == "force_grapple" else "damage",
                    unarmed_damage_type="force")
        else:
            if problem == "not_flurry":
                data["subclasses"] = {"monk": "warrior_of_the_open_hand"}
            _open_hand(state, data, "push",
                       sequence="martial_arts" if problem == "not_flurry" else "flurry")
    assert state == before


@pytest.mark.parametrize("case,outcome", [
    ("miss", "No hit; no additional effect"), ("save", "Resisted"), ("immunity", "Immune"),
])
def test_open_hand_cannot_apply_through_a_miss_successful_save_or_immunity(
    monkeypatch, case, outcome,
):
    from random import Random

    from sylvia_gm import dice

    monkeypatch.setattr(dice, "_rng", Random(6))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": 3}], level=3,
                subclasses={"monk": "warrior_of_the_open_hand"})
    state[f"{SRD}.entities"]["hero"].update(
        resources={"focus_point": 3}, resource_maxima={"focus_point": 3},
    )
    target = state[f"{SRD}.entities"]["guard"]
    target.update(armor_class=1000 if case == "miss" else 1,
                  saving_throw_modifiers={"dexterity": 30 if case == "save" else -20})
    if case == "immunity":
        target["condition_immunities"] = ["prone"]
    result = _open_hand(state, data, "topple")
    assert result.result["context"]["_open_hand_outcome"] == outcome
    assert "prone" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    if case == "miss":
        assert result.result["context"]["_open_hand_save_roll"] is None


def test_addle_blocks_opportunity_attacks_until_the_targets_next_turn(monkeypatch):
    from random import Random

    from sylvia_gm import dice

    monkeypatch.setattr(dice, "_rng", Random(6))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": 3}], level=3,
                subclasses={"monk": "warrior_of_the_open_hand"})
    state[f"{SRD}.entities"]["hero"].update(
        resources={"focus_point": 3}, resource_maxima={"focus_point": 3},
    )
    state[f"{SRD}.entities"]["guard"].update(
        armor_class=1, available_reactions=["opportunity_attack"], reaction_available=True,
        melee_reach=5,
    )
    _open_hand(state, data, "addle")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="cannot make Opportunity Attacks"):
        _run("trigger_opportunity_attack", state, participant_data=data,
             reactor_state="guard", actor_can_see_target=True, target_can_see_actor=True,
             attack_distance=5)
    assert state == before
    assert state[f"{SRD}.entities"]["guard"]["reaction_available"] is True
    _run("end_combat_turn", state, participant_data=data, actor="hero")
    state[f"{SRD}.combat"]["current_actor"] = "guard"
    _run("begin_combat_turn", state, participant_data=data, actor="guard")
    assert state[f"{SRD}.entities"]["guard"]["opportunity_attacks_blocked"] is False
    assert state[f"{SRD}.state"]["opportunity_blocked_targets"] == []
