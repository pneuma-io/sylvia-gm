"""Character reaction profiles come from owned weapons and saved character choices."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.test_srd_action_budget import SRD, _run
from tests.test_srd_attack_budget import _combat_case


def _reaction_case():
    state, data = _combat_case()
    state[f"{SRD}.combat"].update(active_actor="guard", current_actor="guard")
    state[f"{SRD}.entities"]["hero"].update(
        available_reactions=["opportunity_attack"], reaction_available=True, melee_reach=5,
    )
    return state, data


def test_character_reaction_cannot_use_an_invented_raw_attack_profile():
    state, data = _reaction_case()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="weapon"):
        _run("trigger_opportunity_attack", state, participant_data=data,
             reactor_state="hero", actor_can_see_target=True, target_can_see_actor=True,
             attack_distance=5, attack_modifier=99, damage_dice_count=20,
             damage_die_size="12", damage_modifier=99, damage_type="force")
    assert state == before


@pytest.mark.parametrize("two_handed,expression", [(False, "1d6"), (True, "1d8")])
def test_character_reaction_derives_weapon_profile_and_spends_only_reaction(two_handed, expression):
    state, data = _reaction_case()
    combat_before = deepcopy(state[f"{SRD}.combat"])
    _run("trigger_opportunity_attack", state, participant_data=data,
         reactor_state="hero", actor_can_see_target=True, target_can_see_actor=True,
         attack_distance=5, weapon="spear", wielding_two_handed=two_handed,
         attack_modifier=99, damage_dice_count=20, damage_die_size="12",
         damage_modifier=99, damage_type="force")
    pending = state[f"{SRD}.combat"]["pending_reaction"]
    assert pending["attack_modifier"] == 6
    assert pending["damage_modifier"] == 3
    assert pending["damage_type"] == "piercing"
    assert pending["damage_expression"] == expression
    assert state[f"{SRD}.entities"]["hero"]["reaction_available"] is True
    result = _run("resolve_opportunity_attack", state, participant_data=data,
                  target_is_charmer=False, fear_source_in_sight=False,
                  advantage=False, disadvantage=False)
    assert result.result["result_card"]["equation"]["modifier"] == 6
    assert state[f"{SRD}.entities"]["hero"]["reaction_available"] is False
    for key, value in combat_before.items():
        assert state[f"{SRD}.combat"][key] == value


@pytest.mark.parametrize("weapon,distance,two_handed,shield", [
    ("longbow", 5, True, False), ("greatsword", 5, False, False),
    ("greatsword", 5, True, True), ("spear", 10, False, False),
    ("not_owned", 5, False, False),
])
def test_illegal_character_reaction_weapon_is_atomic(weapon, distance, two_handed, shield):
    state, data = _reaction_case()
    hero = state[f"{SRD}.entities"]["hero"]
    hero["inventory"] += ["greatsword", "longbow"]
    if shield:
        hero["worn_shield"] = {"inventory_item": "shield"}
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("trigger_opportunity_attack", state, participant_data=data,
             reactor_state="hero", actor_can_see_target=True, target_can_see_actor=True,
             attack_distance=distance, weapon=weapon, wielding_two_handed=two_handed)
    assert state == before


@pytest.mark.parametrize("weapon,distance,seed", [("glaive", 10, 6), ("greatsword", 5, 37)])
def test_reaction_uses_weapon_reach_and_great_weapon_style(monkeypatch, weapon, distance, seed):
    monkeypatch.setattr(dice, "_rng", Random(seed))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data = _reaction_case()
    data["fighting_styles"] = [{"source": "fighter", "style": "great_weapon_fighting"}]
    state[f"{SRD}.entities"]["hero"]["inventory"].append(weapon)
    state[f"{SRD}.entities"]["guard"]["armor_class"] = 1
    _run("trigger_opportunity_attack", state, participant_data=data,
         reactor_state="hero", actor_can_see_target=True, target_can_see_actor=True,
         attack_distance=distance, weapon=weapon, wielding_two_handed=True)
    result = _run("resolve_opportunity_attack", state, participant_data=data,
                  target_is_charmer=False, fear_source_in_sight=False,
                  advantage=False, disadvantage=False)
    context = result.result["context"]
    raw = context["_damage_roll"]["results"]
    bonus = sum(max(0, 3 - face) for face in raw)
    assert context["_great_weapon_damage_bonus"] == bonus
    assert context["_damage_applied"] == sum(raw) + bonus + 3
    if seed == 37:
        assert context["_critical_hit"] is True and len(raw) == 4


@pytest.mark.parametrize("weapon", ["spear", "unarmed_strike"])
def test_public_character_reaction_reopens_pending_and_retries_once(tmp_path, weapon):
    import io
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
    from sylvia_gm.application.bundled_packages import bundled_srd_archive
    from sylvia_gm.domain.authority import ActionStatus, StateValue, WriterRole
    from tests.test_srd_second_wind import _installed_fighter

    database = tmp_path / "reaction.sqlite3"
    state, data = _reaction_case()
    state[f"{SRD}.combat"].update(active_actor_kind="monster", active_actor_can_act=True)
    trigger = ActionCommand(
        campaign_id="reaction", action_id="trigger", idempotency_key="trigger",
        owner_id="test", writer=WriterRole.DM, expected_revision=0, turn_id="turn",
        package_id=SRD, tool_name="trigger_opportunity_attack", parameters={
            "reactor_state": "hero", "actor_can_see_target": True,
            "target_can_see_actor": True, "attack_distance": 5,
            "weapon": weapon, "wielding_two_handed": weapon == "spear",
        },
    )
    with AuthorityOwner.open(database, "test") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(bundled_srd_archive()), install_id="srd",
        )
        fighter = _installed_fighter(candidate.package_lock, fighter=True)
        fighter = replace(fighter, data={**fighter.data, **data})
        owner.create_campaign(CampaignCreate(
            campaign_id="reaction", display_name="Synthetic reaction", writer=WriterRole.DM,
            package_lock=candidate.package_lock, turn_id="turn", participants=(fighter,),
            initial_state=tuple(StateValue(SRD, key, state[f"{SRD}.{key}"])
                                for key in ("combat", "entities", "state")),
        ))
        receipt = StatelessMcpMechanics(owner).execute_action(trigger)
        assert receipt.status is ActionStatus.COMMITTED
        pending = owner.snapshot("reaction")
        expected = "1d8" if weapon == "spear" else "fixed-1"
        assert pending.state[f"{SRD}.combat"]["pending_reaction"]["damage_expression"] == expected
    with AuthorityOwner.open(database, "test") as owner:
        assert owner.snapshot("reaction") == pending
        assert owner.execute_action(trigger) == receipt
        tool = ("resolve_opportunity_attack" if weapon == "spear"
                else "resolve_unarmed_opportunity_attack")
        choices = {"target_is_charmer": False, "fear_source_in_sight": False,
                   "advantage": False, "disadvantage": False}
        if weapon == "unarmed_strike":
            choices["effect"] = "damage"
        resolve = replace(trigger, action_id="resolve", idempotency_key="resolve",
                          expected_revision=pending.revision,
                          tool_name=tool, parameters=choices)
        result = StatelessMcpMechanics(owner).execute_action(resolve)
        assert result.status is ActionStatus.COMMITTED
        saved = owner.snapshot("reaction")
        assert saved.state[f"{SRD}.entities"]["hero"]["reaction_available"] is False
        assert saved.state[f"{SRD}.combat"]["action_available"] is True
    with AuthorityOwner.open(database, "test") as owner:
        assert owner.execute_action(resolve) == result
        assert owner.snapshot("reaction") == saved


@pytest.mark.parametrize("effect", ["damage", "grapple", "shove_prone", "shove_push"])
def test_unarmed_reaction_uses_shared_effects_without_spending_active_turn(effect):
    state, data = _reaction_case()
    data["size_rank"] = 2
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(size_rank=2, speed=30, saving_throw_modifiers={"strength": -20})
    before = deepcopy(state[f"{SRD}.combat"])
    _run("trigger_opportunity_attack", state, participant_data=data,
         reactor_state="hero", actor_can_see_target=True, target_can_see_actor=True,
         attack_distance=5, weapon="unarmed_strike")
    result = _run("resolve_unarmed_opportunity_attack", state, participant_data=data,
                  effect=effect, target_save_ability="strength", hand_free=True,
                  target_is_charmer=False, fear_source_in_sight=False)
    assert result.result["result_card"]["title"] == "Unarmed Opportunity Attack"
    for key, value in before.items():
        assert state[f"{SRD}.combat"][key] == value
    assert state[f"{SRD}.entities"]["hero"]["reaction_available"] is False
    target = state[f"{SRD}.entities"]["guard"]
    if effect == "damage":
        expected = 4 if result.result["context"]["_unarmed_success"] else 0
        assert target["hp"] == 1000 - expected
    elif effect == "grapple":
        assert "grappled" in target["conditions"]
    elif effect == "shove_prone":
        assert "prone" in target["conditions"]
    elif effect == "shove_push":
        assert target["last_forced_movement_feet"] == 5


def test_rejected_unarmed_reaction_preserves_pending_choice_and_reaction():
    state, data = _reaction_case()
    data["size_rank"] = 2
    state[f"{SRD}.entities"]["guard"].update(
        size_rank=2, saving_throw_modifiers={"strength": 2},
    )
    _run("trigger_opportunity_attack", state, participant_data=data,
         reactor_state="hero", actor_can_see_target=True, target_can_see_actor=True,
         attack_distance=5, weapon="unarmed_strike")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="free hand"):
        _run("resolve_unarmed_opportunity_attack", state, participant_data=data,
             effect="grapple", target_save_ability="strength", hand_free=False,
             target_is_charmer=False, fear_source_in_sight=False)
    assert state == before
    with pytest.raises(RulesetPreconditionRejected, match="Unarmed Strike"):
        _run("resolve_opportunity_attack", state, participant_data=data,
             target_is_charmer=False, fear_source_in_sight=False,
             advantage=False, disadvantage=False)
    assert state == before
