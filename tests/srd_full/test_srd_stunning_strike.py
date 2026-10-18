"""Stunning Strike shares saved turn effects across attack budgets and later turns."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_attack_budget import _attack
from tests.srd_full.test_srd_unarmed_creatures import _case, _strike


def _monk(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(6))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": 5}], level=5, fighting_styles=[])
    data["abilities"]["wisdom"] = 16
    state[f"{SRD}.entities"]["hero"].update(
        resources={"focus_point": 5}, resource_maxima={"focus_point": 5},
        available_reactions=["opportunity_attack"], melee_reach=5,
    )
    state[f"{SRD}.entities"]["guard"].update(
        armor_class=1, saving_throw_modifiers={"constitution": 0},
    )
    state[f"{SRD}.combat"].update(turn_sequence=1, movement_spent=0, dashes_taken=0)
    return state, data


@pytest.mark.parametrize("route", [
    "unarmed", "weapon", "bonus", "unarmed_reaction", "weapon_reaction",
])
def test_stunning_strike_hits_use_fixed_save_focus_and_shared_expiry(monkeypatch, route):
    state, data = _monk(monkeypatch)
    choices = {"use_stunning_strike": True, "stunning_forego_save": True}
    if route == "unarmed":
        result = _strike(state, data, "damage", **choices)
    elif route == "weapon":
        result = _attack(state, data, weapon="spear", **choices)
    elif route == "bonus":
        result = _run("resolve_monk_bonus_strike", state, participant_data=data,
                      actor="hero", actor_state="hero", target_state="guard", effect="damage",
                      sequence="flurry", distance=5, actor_can_see_target=True,
                      target_can_see_actor=True, target_is_charmer=False,
                      fear_source_in_sight=False, **choices)
    else:
        state[f"{SRD}.combat"].update(active_actor="guard", current_actor="guard")
        _run("trigger_opportunity_attack", state, participant_data=data,
             reactor_state="hero", actor_can_see_target=True, target_can_see_actor=True,
             attack_distance=5, weapon="spear" if route == "weapon_reaction" else "unarmed_strike")
        params = {"effect": "damage"} if route == "unarmed_reaction" else {
            "advantage": False, "disadvantage": False,
        }
        result = _run("resolve_unarmed_opportunity_attack" if route == "unarmed_reaction"
                      else "resolve_opportunity_attack", state, participant_data=data,
                      target_is_charmer=False, fear_source_in_sight=False, **params, **choices)
    assert result.result["context"]["_stunning_save_dc"] == 14
    assert result.result["context"]["_stunning_focus_spent"] == 1
    assert "Stunning Strike" in str(result.result["result_card"])
    expected_focus = 3 if route == "bonus" else 4
    assert state[f"{SRD}.entities"]["hero"]["resources"]["focus_point"] == expected_focus
    assert "stunned" in state[f"{SRD}.entities"]["guard"]["conditions"]
    _run("end_combat_turn", state)
    state[f"{SRD}.combat"]["current_actor"] = "hero"
    _run("begin_combat_turn", state, participant_data=data)
    assert "stunned" not in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_successful_save_halves_movement_and_consumes_next_attack_advantage(monkeypatch):
    state, data = _monk(monkeypatch)
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["constitution"] = 30
    result = _strike(state, data, "damage", use_stunning_strike=True)
    assert result.result["context"]["_stunning_save_total"] >= 31
    assert "stunned" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    next_attack = _attack(state, data, weapon="spear")
    assert next_attack.result["context"]["_effective_advantage"] is True
    assert not any(
        e["kind"] == "next_attack_advantage" for e in state[f"{SRD}.state"]["turn_effects"]
    )
    _run("end_combat_turn", state)
    state[f"{SRD}.combat"]["current_actor"] = "guard"
    _run("begin_combat_turn", state)
    assert state[f"{SRD}.combat"]["movement_remaining"] == 15
    _run("apply_srd_dash", state, actor_state="guard")
    assert state[f"{SRD}.combat"]["movement_remaining"] == 30
    _run("end_combat_turn", state)
    state[f"{SRD}.combat"]["current_actor"] = "hero"
    _run("begin_combat_turn", state, participant_data=data)
    assert state[f"{SRD}.entities"]["guard"]["turn_speed_halved"] is False


@pytest.mark.parametrize("problem", ["level", "focus", "weapon", "grapple", "repeat"])
def test_illegal_stunning_choice_rejects_without_attack_or_resource_changes(monkeypatch, problem):
    state, data = _monk(monkeypatch)
    if problem == "level":
        data["classes"][0]["level"] = 4
    elif problem == "focus":
        state[f"{SRD}.entities"]["hero"]["resources"]["focus_point"] = 0
    elif problem == "repeat":
        _strike(state, data, "damage", use_stunning_strike=True, stunning_forego_save=True)
    elif problem == "weapon":
        state[f"{SRD}.entities"]["hero"]["inventory"].append("longsword")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        if problem == "weapon":
            _attack(state, data, weapon="longsword", use_stunning_strike=True)
        else:
            _strike(state, data, "grapple" if problem == "grapple" else "damage",
                    use_stunning_strike=True, stunning_forego_save=True)
    assert state == before


def test_miss_preserves_focus_and_use_while_stun_immunity_spends_both(monkeypatch):
    state, data = _monk(monkeypatch)
    state[f"{SRD}.entities"]["guard"]["armor_class"] = 99
    _strike(state, data, "damage", use_stunning_strike=True)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["focus_point"] == 5
    assert state[f"{SRD}.entities"]["hero"].get("stunning_strike_turn") is None
    state[f"{SRD}.entities"]["guard"].update(armor_class=1, condition_immunities=["stunned"])
    _strike(state, data, "damage", use_stunning_strike=True, stunning_forego_save=True)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["focus_point"] == 4
    assert "stunned" not in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_stunning_can_be_used_again_on_another_creatures_turn(monkeypatch):
    state, data = _monk(monkeypatch)
    _strike(state, data, "damage", use_stunning_strike=True, stunning_forego_save=True)
    _run("end_combat_turn", state)
    state[f"{SRD}.combat"]["current_actor"] = "guard"
    _run("begin_combat_turn", state)
    assert state[f"{SRD}.combat"]["turn_sequence"] == 2
    assert state[f"{SRD}.combat"]["active_actor_can_act"] is False
    # An Opportunity Attack is not possible against this stunned target's movement;
    # exercise the once-per-turn consumer independently of that physical trigger.
    _run("resolve_srd_stunning_strike", state, participant_data=data,
         actor="hero", actor_state="hero", target_state="guard", weapon="spear",
         hit=True, forego_save=True)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["focus_point"] == 3
    _run("remove_srd_condition", state, target="guard", condition="stunned")
    assert state[f"{SRD}.combat"]["active_actor_can_act"] is True


def test_stun_ends_concentration_and_elapsed_time_expires_it_outside_combat(monkeypatch):
    state, data = _monk(monkeypatch)
    state[f"{SRD}.entities"]["guard"].update(
        concentration_spell_id="guidance",
        concentration_effect_family="lasting_reactive_concentration",
        concentration_target_state_id="hero", concentration_sequence=1,
        concentration_expires_tick=20,
    )
    state[f"{SRD}.entities"]["hero"]["guidance_head_caster_state_id"] = "guard"
    _run("resolve_srd_stunning_strike", state, participant_data=data,
         actor="hero", actor_state="hero", target_state="guard", weapon="spear",
         hit=True, forego_save=True)
    assert state[f"{SRD}.entities"]["guard"]["concentration_spell_id"] is None
    assert state[f"{SRD}.entities"]["hero"]["guidance_head_caster_state_id"] is None
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert "stunned" not in state[f"{SRD}.entities"]["guard"]["conditions"]


@pytest.mark.parametrize("other_source", ["before", "after", "timed", "removed"])
def test_stun_expiry_preserves_sources_and_explicit_removal_stays_removed(
    monkeypatch, other_source,
):
    state, data = _monk(monkeypatch)
    if other_source == "before":
        _run("apply_srd_condition", state, target="guard", condition="stunned")
    _strike(state, data, "damage", use_stunning_strike=True, stunning_forego_save=True)
    if other_source == "after":
        _run("apply_srd_condition", state, target="guard", condition="stunned")
    if other_source == "timed":
        _run("install_srd_turn_effect", state, caster_state="guard", target_state="guard",
             lasting_state="state", spell="another_stun", effect_label="Another Stun",
             kind="stunned", expiry_owner="caster", expiry_phase="start", magnitude=0,
             dice_count=0, damage_die="d4", damage_type="bludgeoning")
    if other_source == "removed":
        _run("remove_srd_condition", state, target="guard", condition="stunned")
        _run("project_srd_turn_modifiers", state, target_state="guard", lasting_state="state")
        assert "stunned" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    _run("end_combat_turn", state)
    state[f"{SRD}.combat"]["current_actor"] = "hero"
    _run("begin_combat_turn", state, participant_data=data)
    stunned = "stunned" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert stunned is (other_source != "removed")


# New repair controls for appending to the isolated test_srd_stunning_strike.py.
# New controls verified under the assigned isolated CPU lease.


def _install_stunned_removal_control(state, *, caster, target, spell, kind="stunned"):
    """Produce the established source record through its native installer."""
    return _run(
        "install_srd_turn_effect",
        state,
        caster_state=caster,
        target_state=target,
        lasting_state="state",
        spell=spell,
        effect_label=spell,
        kind=kind,
        expiry_owner="caster",
        expiry_phase="start",
        magnitude=0,
        dice_count=0,
        damage_die="d4",
        damage_type="bludgeoning",
    )


def test_stunned_removal_clears_matching_target_sources_and_preserves_other_effects(monkeypatch):
    state, data = _monk(monkeypatch)
    state[f"{SRD}.entities"]["other"] = deepcopy(state[f"{SRD}.entities"]["guard"])
    _strike(state, data, "damage", use_stunning_strike=True, stunning_forego_save=True)
    _install_stunned_removal_control(state, caster="guard", target="guard", spell="another_stun")
    _install_stunned_removal_control(
        state, caster="hero", target="other", spell="other_target_stun"
    )
    _install_stunned_removal_control(
        state, caster="guard", target="guard", spell="independent_poison", kind="poisoned"
    )
    effects = deepcopy(state[f"{SRD}.state"]["turn_effects"])
    matching = [e for e in effects if e["kind"] == "stunned" and e["target"] == "guard"]
    assert {(e["spell"], e["caster"], e["target"]) for e in matching} == {
        ("stunning_strike", "hero", "guard"),
        ("another_stun", "guard", "guard"),
    }
    assert all(isinstance(e, dict) and isinstance(e["target"], str) for e in effects)
    kept = [e for e in effects if e["kind"] != "stunned" or e["target"] != "guard"]
    other_before = deepcopy(state[f"{SRD}.entities"]["other"])
    resources_before = deepcopy(state[f"{SRD}.entities"]["hero"]["resources"])

    _run("remove_srd_condition", state, target="guard", condition="stunned")

    assert state[f"{SRD}.state"]["turn_effects"] == kept
    guard = state[f"{SRD}.entities"]["guard"]
    assert "stunned" not in guard["conditions"] and "poisoned" in guard["conditions"]
    assert guard["turn_effect_stunned"] is False
    assert guard["turn_effect_unowned_stunned"] is False
    assert state[f"{SRD}.entities"]["other"] == other_before
    assert state[f"{SRD}.entities"]["hero"]["resources"] == resources_before
    _run("project_srd_turn_modifiers", state, target_state="guard", lasting_state="state")
    assert "stunned" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.state"]["turn_effects"] == kept


def _create_source_stunned_defense_campaign(owner):
    """Admit actual native activation and source-owned Stunned through the saved owner."""
    import io
    from dataclasses import replace

    from sylvia_gm.application.authority import CampaignCreate
    from sylvia_gm.domain.authority import StateValue, WriterRole
    from tests.srd_full.test_srd_disciplined_survivor import _monk as defense_monk
    from tests.srd_full.test_srd_second_wind import _installed_fighter
    from tests.srd_full.test_srd_superior_defense import _activate, _defense_archive

    state, data = defense_monk(18)
    state[f"{SRD}.entities"]["hero"].update(
        hp=100, max_hp=100, temp_hp=0, damage_resistances=["cold", "force"]
    )
    _activate(state, data)
    assert state[f"{SRD}.entities"]["hero"]["superior_defense_expires_tick"] == 20
    assert state[f"{SRD}.entities"]["hero"]["resources"]["focus_point"] == 15
    _install_stunned_removal_control(
        state, caster="guard", target="hero", spell="source_owned_stun"
    )
    assert state[f"{SRD}.entities"]["hero"]["superior_defense_expires_tick"] is None
    assert "stunned" in state[f"{SRD}.entities"]["hero"]["conditions"]
    assert state[f"{SRD}.state"]["turn_effects"][0]["target"] == "hero"
    state[f"{SRD}.combat"].update(
        encounter_active=False, turn_active=False, phase="awaiting_turn_start"
    )
    package = owner.install_package_archive(
        io.BytesIO(_defense_archive()), install_id="source-stunned-defense"
    )
    actor = _installed_fighter(package.package_lock, fighter=True)
    actor = replace(actor, data={**actor.data, **data})
    owner.create_campaign(
        CampaignCreate(
            campaign_id="defense",
            display_name="Source-owned Stunned removal",
            writer=WriterRole.DM,
            package_lock=package.package_lock,
            turn_id="turn",
            participants=(actor,),
            initial_state=tuple(
                StateValue(SRD, key, state[f"{SRD}.{key}"])
                for key in ("combat", "entities", "state")
            ),
        )
    )


def test_stunned_removal_rejects_unsupported_source_selector_without_state_change(tmp_path):
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.errors import MechanicsRejected
    from tests.srd_full.test_srd_superior_defense import _defense_action

    with AuthorityOwner.open(tmp_path / "stunned-source-selector.db", "test") as owner:
        owner.set_admin_full_audit(True)
        _create_source_stunned_defense_campaign(owner)
        before = owner.snapshot("defense")
        command = _defense_action(
            "remove_srd_condition",
            "unsupported-source",
            0,
            target="hero",
            condition="stunned",
            source_id="guard",
        )
        with pytest.raises(MechanicsRejected) as rejected:
            owner.execute_action(command)
        assert rejected.value.state_changed is False
        assert {(issue.path, issue.kind) for issue in rejected.value.validation_issues} == {
            ("source_id", "unexpected_field")
        }
        assert owner.snapshot("defense") == before


@pytest.mark.parametrize("lifecycle", ["explicit_removal", "native_expiry"])
def test_source_owned_stunned_reopens_retries_and_keeps_defense_terminated(tmp_path, lifecycle):
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from tests.srd_full.test_srd_superior_defense import _defense_action

    database = tmp_path / f"source-stunned-{lifecycle}.db"
    with AuthorityOwner.open(database, "test") as owner:
        owner.set_admin_full_audit(True)
        _create_source_stunned_defense_campaign(owner)
        seeded = owner.snapshot("defense")
        assert seeded.revision == 0 and seeded.actions == ()
    command = (
        _defense_action("remove_srd_condition", "remove", 0, target="hero", condition="stunned")
        if lifecycle == "explicit_removal"
        else _defense_action("advance_srd_lasting_time", "expiry", 0, elapsed_rounds=1)
    )
    with AuthorityOwner.open(database, "test") as owner:
        owner.set_admin_full_audit(True)
        assert owner.snapshot("defense") == seeded
        receipt = owner.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        removed = owner.snapshot("defense")
        assert removed.revision == 1 and len(removed.actions) == 1
        assert removed.state[f"{SRD}.state"]["turn_effects"] == []
        hero = removed.state[f"{SRD}.entities"]["hero"]
        assert "stunned" not in hero["conditions"]
        assert hero["superior_defense_expires_tick"] is None
        assert hero["resources"]["focus_point"] == 15
    with AuthorityOwner.open(database, "test") as owner:
        owner.set_admin_full_audit(True)
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("defense") == removed
        projection = _defense_action("advance_srd_lasting_time", "later-time", 1, elapsed_rounds=1)
        projected_receipt = owner.execute_action(projection)
        projected = owner.snapshot("defense")
        assert projected_receipt.status is ActionStatus.COMMITTED
        assert projected.state[f"{SRD}.state"]["turn_effects"] == []
        assert projected.state[f"{SRD}.state"]["superior_defense_target_ids"] == []
        assert "stunned" not in projected.state[f"{SRD}.entities"]["hero"]["conditions"]
        assert projected.state[f"{SRD}.entities"]["hero"]["superior_defense_expires_tick"] is None
    with AuthorityOwner.open(database, "test") as owner:
        owner.set_admin_full_audit(True)
        assert owner.snapshot("defense") == projected
        fire = _defense_action(
            "apply_srd_damage",
            "fire",
            2,
            target="hero",
            amount=21,
            adjustment=0,
            damage_type="fire",
        )
        fire_receipt = owner.execute_action(fire)
        assert fire_receipt.status is ActionStatus.COMMITTED
        assert owner.snapshot("defense").state[f"{SRD}.entities"]["hero"]["hp"] == 79
        force = _defense_action(
            "apply_srd_damage",
            "force",
            3,
            target="hero",
            amount=21,
            adjustment=0,
            damage_type="force",
        )
        force_receipt = owner.execute_action(force)
        assert force_receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("defense")
        hero = saved.state[f"{SRD}.entities"]["hero"]
        assert hero["hp"] == 69 and hero["damage_resistances"] == ["cold", "force"]
        assert hero["resources"]["focus_point"] == 15
        assert hero["superior_defense_expires_tick"] is None
        assert saved.revision == 4 and len(saved.actions) == 4
    with AuthorityOwner.open(database, "test") as owner:
        owner.set_admin_full_audit(True)
        assert owner.execute_action(command) == receipt
        assert owner.execute_action(projection) == projected_receipt
        assert owner.execute_action(fire) == fire_receipt
        assert owner.execute_action(force) == force_receipt
        assert owner.snapshot("defense") == saved
