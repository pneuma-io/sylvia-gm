"""Public Sorcerous Burst attacks actual prepared scenery, with one roll and one cost."""

from copy import deepcopy

import pytest

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import PackageRuntimeError
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.runtime import _effect_executor
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cases
from tests.srd_full.test_srd_spell_damage_consumers import _enfeeble, _record_rolls
from tests.srd_full.test_srd_spell_special_damage import ActualDiceSequence
from tests.srd_full.test_srd_spell_special_damage import compiled_special as compiled_special
from tests.srd_full.test_srd_spell_special_damage import special_runtime as special_runtime
from tests.srd_full.test_srd_spell_wave3_information import _entity, _scene, _wall

SRD = cases.SRD


def _record_burst_rolls(monkeypatch):
    """Observe ordinary and exploding dice at their real execution owners."""
    rolls = _record_rolls(monkeypatch)
    actual_explode = _effect_executor.dice_explode

    def explode(*args, **kwargs):
        result = actual_explode(*args, **kwargs)
        rolls.append(result)
        return result

    monkeypatch.setattr(_effect_executor, "dice_explode", explode)
    return rolls


def _object_case():
    state, data, parameters = cases._spell_case("sorcerous_burst")
    hero = _entity("hero", [0, 0, 0], participant_ref="hero", creature_type="humanoid")
    wooden = _entity("wooden-target", [20, 0, 0], is_object=True)
    state[f"{SRD}.state"]["spatial_scene"] = _scene(hero, wooden)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    budget._run(
        "prepare_srd_spell_object",
        state,
        object_id="wooden-target",
        material="wood",
        size="medium",
        durability="resilient",
    )
    parameters.pop("target", None)
    parameters.update(object_target_id="wooden-target", selected_damage_type="fire")
    return state, data, parameters


@pytest.mark.parametrize(
    "critical,extra,damage_type",
    [
        (False, True, "fire"),
        (True, True, "fire"),
        (False, False, "fire"),
        (False, True, "poison"),
        (False, True, "psychic"),
    ],
)
def test_actual_object_burst_preserves_one_attack_explosion_cap_and_typed_hp(
    monkeypatch,
    critical,
    extra,
    damage_type,
):
    state, data, parameters = _object_case()
    original = deepcopy(state[f"{SRD}.entities"]["guard"])
    monkeypatch.setattr(dice, "_rng", ActualDiceSequence(d20=20 if critical else 10))
    rolls = _record_burst_rolls(monkeypatch)
    result = budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(parameters | {"selected_damage_type": damage_type, "sorcerous_burst_extra_dice": extra}),
    ).result
    (attack,) = [roll for roll in rolls if roll.sides == 20]
    (damage,) = [roll for roll in rolls if roll.sides == 8]
    assert attack.count == 1
    initial = 8 if critical else 4
    assert damage.count == initial
    assert len(damage.results) == initial + (4 if extra else 0)
    profile = state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["metadata"]["srd_spell_object"]
    assert profile["hp"] == (18 if damage_type in {"poison", "psychic"} else 0)
    assert profile["destroyed"] is (damage_type not in {"poison", "psychic"})
    assert state[f"{SRD}.entities"]["guard"] == original
    assert "wooden-target" in str(result["result_card"]["details"])


def test_object_burst_miss_keeps_actual_hp_and_has_no_damage_roll(monkeypatch):
    state, data, parameters = _object_case()
    monkeypatch.setattr(dice, "_rng", ActualDiceSequence(d20=1))
    rolls = _record_rolls(monkeypatch)
    result = budget._run(
        "cast_spell_outside_combat", state, participant_data=data, **parameters
    ).result
    assert [roll.sides for roll in rolls] == [20]
    assert result["context"]["_spell_attack_kind"] == "miss"
    assert result["context"]["_spell_damage_amount"] == 0
    assert "No damage roll (miss)." in str(result["result_card"]["details"])
    profile = state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["metadata"]["srd_spell_object"]
    assert profile["hp"] == 18 and not profile["destroyed"]


def test_object_burst_consumes_one_actual_magic_action_and_rejects_second_atomically(monkeypatch):
    state, data, parameters = _object_case()
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=True)
    monkeypatch.setattr(dice, "_rng", ActualDiceSequence(d8=[1, 1, 1, 1]))
    rolls = _record_rolls(monkeypatch)
    resources = deepcopy(state[f"{SRD}.entities"]["hero"]["resources"])
    parameters["sorcerous_burst_extra_dice"] = False
    budget._run("cast_spell", state, participant_data=data, **parameters)
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert state[f"{SRD}.entities"]["hero"]["resources"] == resources
    assert [roll.sides for roll in rolls] == [20, 8]
    before = deepcopy(state)
    with pytest.raises((RulesetPreconditionRejected, PackageRuntimeError)):
        budget._run("cast_spell", state, participant_data=data, **parameters)
    assert state == before and len(rolls) == 2


@pytest.mark.parametrize("unsupported", ["range", "total_cover", "both_targets"])
def test_object_burst_rejects_actual_blocked_or_ambiguous_targets_atomically(
    monkeypatch, unsupported
):
    state, data, parameters = _object_case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    if unsupported == "range":
        scene["entities"][1]["position"] = [121, 0, 0]
    elif unsupported == "total_cover":
        scene["geometry"].append(_wall(1))
    else:
        parameters["target"] = "guard"
    rolls = _record_rolls(monkeypatch)
    before = deepcopy(state)
    with pytest.raises((RulesetPreconditionRejected, PackageRuntimeError)):
        budget._run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state == before and not rolls


def test_object_burst_applies_one_actual_source_penalty_and_preserves_raw_dice(monkeypatch):
    state, data, parameters = _object_case()
    _enfeeble(state, data)
    monkeypatch.setattr(dice, "_rng", ActualDiceSequence(d8=[1, 1, 1, 1, 8]))
    rolls = _record_rolls(monkeypatch)
    result = budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(parameters | {"sorcerous_burst_extra_dice": False}),
    ).result
    raw, penalty = [roll for roll in rolls if roll.sides == 8]
    assert raw.results == (1, 1, 1, 1) and penalty.total == 8
    assert result["context"]["_spell_damage"]["total"] == 4
    assert result["context"]["_spell_damage_amount"] == 0
    profile = state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["metadata"]["srd_spell_object"]
    assert profile["hp"] == 18


def test_already_applied_object_damage_notifies_actual_source_after_persistence(monkeypatch):
    state, data, parameters = _object_case()
    data["classes"] = [{"id": "sorcerer", "level": 8}, {"id": "bard", "level": 9}]
    data["spellcasting_grants"].append(
        {"source": "bard", "ability": "charisma", "spells": ["mislead"]}
    )
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("mislead")
    _, _, mislead = cases._spell_case("mislead")
    mislead.update(
        illusion_position=[0, 10, 0],
        illusion_size=[5, 5, 5],
        illusion_description="The caster's illusory double.",
    )
    budget._run("cast_spell_outside_combat", state, participant_data=data, **mislead)
    source = next(reversed(state[f"{SRD}.state"]["illusion_spell_sources"].values()))
    assert source["invisible_active"]
    events = []
    actual_call = _effect_executor._EFFECT_HANDLERS["call"]

    def observe(body, effect, context, executor):
        if body == "end_srd_mislead_damage_source":
            scene = context.state_bridge.get_entity("state")["spatial_scene"]
            actual_object = next(row for row in scene["entities"] if row["id"] == "wooden-target")
            events.append(
                (
                    context.current_tool,
                    context.vars["_caster_state_id"],
                    context.vars["_object_damage_applied"],
                    actual_object["metadata"]["srd_spell_object"]["hp"],
                )
            )
        return actual_call(body, effect, context, executor)

    monkeypatch.setitem(_effect_executor._EFFECT_HANDLERS, "call", observe)
    monkeypatch.setattr(dice, "_rng", ActualDiceSequence(d8=[1, 1, 1, 1]))
    budget._run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert events == [("resolve_srd_sorcerous_burst_object_attack", "hero", 4, 14)]
    current = state[f"{SRD}.state"]["illusion_spell_sources"][source["id"]]
    assert current["installed"] and not current["invisible_active"]
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == "mislead"


def test_public_object_burst_reopens_and_exact_retry_preserves_one_attack_and_hp(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as public

    state, data, params = _object_case()
    params.update(sorcerous_burst_extra_dice=True, sorcerous_burst_extra_dice_limit=1)
    monkeypatch.setattr(public, "_spell_case", lambda _spell: (state, data, params))
    actual_dice = ActualDiceSequence(d20=10, d8=[8, 1, 1, 1, 2])
    monkeypatch.setattr(dice, "_rng", actual_dice)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    rolls = _record_burst_rolls(monkeypatch)
    database = tmp_path / "object-burst.sqlite3"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public._create_spell_campaign(owner, "sorcerous_burst")
        command = ActionCommand(
            campaign_id="casting",
            action_id="object-burst",
            idempotency_key="object-burst",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=params,
        )
        resources = deepcopy(
            owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]["resources"]
        )
        mechanics = StatelessMcpMechanics(owner)
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        scene = saved.state[f"{SRD}.state"]["spatial_scene"]
        target = next(entry for entry in scene["entities"] if entry["id"] == "wooden-target")
        assert target["metadata"]["srd_spell_object"]["hp"] == 5
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"] == resources
        assert [roll.sides for roll in rolls] == [20, 8]
        assert len(rolls[1].results) == 5
        assert mechanics.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)
        assert mechanics.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
        assert [roll.sides for roll in rolls] == [20, 8]
        miss = replace(
            command,
            action_id="object-miss",
            idempotency_key="object-miss",
            expected_revision=saved.revision,
        )
        monkeypatch.setattr(dice, "_rng", ActualDiceSequence(d20=1))
        result = mechanics.execute_action(miss)
        assert result.status is ActionStatus.COMMITTED, result.result
        after = owner.snapshot("casting")
        target = next(
            entry
            for entry in after.state[f"{SRD}.state"]["spatial_scene"]["entities"]
            if entry["id"] == "wooden-target"
        )
        assert target["metadata"]["srd_spell_object"]["hp"] == 5
        assert [roll.sides for roll in rolls] == [20, 8, 20]
        assert mechanics.execute_action(miss) == result
        assert owner.snapshot("casting").state == after.state


def test_saved_cast_retains_object_burst_stopping_intent_through_paid_completion(monkeypatch):
    state, data, params = _object_case()
    params.update(sorcerous_burst_extra_dice=True, sorcerous_burst_extra_dice_limit=1)
    monkeypatch.setattr(dice, "_rng", ActualDiceSequence(d20=10, d8=[8, 1, 1, 1, 2]))
    rolls = _record_burst_rolls(monkeypatch)
    begun = budget._run("begin_srd_spell_action", state, participant_data=data, **params)
    identity = begun.result["context"]["_pending_action_id"]
    pending = state[f"{SRD}.state"]["pending_srd_actions"][identity]
    assert pending["parameters"]["sorcerous_burst_extra_dice_limit"] == 1
    assert not rolls
    budget._run(
        "complete_srd_pending_action", state, participant_data=data, pending_action_id=identity
    )
    assert [roll.sides for roll in rolls] == [20, 8]
    assert len(rolls[1].results) == 5
    target = next(
        row
        for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if row["id"] == "wooden-target"
    )
    assert target["metadata"]["srd_spell_object"]["hp"] == 5
    assert state[f"{SRD}.state"]["pending_srd_action_stack"] == []
