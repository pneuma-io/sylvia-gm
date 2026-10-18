"""Rituals use real casting time, concentration, components and saved public actions."""

import json
import subprocess
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from tests.test_srd_action_budget import _run
from tests.test_srd_public_spell_casting import SRD, _create_spell_campaign
from tests.test_srd_spell_result_cards import _spell_case


@pytest.fixture(autouse=True)
def _pending_completion_diagnostic(monkeypatch, request, tmp_path):
    """Observe the actual executor unchanged for the five selected synthetic reaction cases."""
    selected = {
        "test_counterspell_later_casting_round_consumes_its_actual_magic_action",
        "test_public_longer_cast_window_reopens_and_retries_before_actual_counterspell",
        "test_public_continued_action_receipt_survives_pending_completion_and_replay",
    }
    if request.node.originalname not in selected:
        return
    from sylvia_gm.application import package_runtime

    original = package_runtime.execute_tool
    diagnostic = tmp_path / "pending-completion-diagnostic.jsonl"

    def observed(*args, **kwargs):
        result = original(*args, **kwargs)
        if not result.success:
            failure = {
                "tool_name": result.tool_name,
                "failure_kind": result.failure_kind,
                "error": result.error,
            }
            encoded = json.dumps(failure, ensure_ascii=False)
            with diagnostic.open("a", encoding="utf-8") as stream:
                stream.write(encoded + "\n")
            print("PENDING_COMPLETION_DIAGNOSTIC " + encoded)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", observed)

    from sylvia_gm.runtime import _effect_executor

    original_condition = _effect_executor.evaluate_condition
    condition_diagnostic = tmp_path / "pending-condition-diagnostic.jsonl"

    def observed_condition(*args, **kwargs):
        try:
            return original_condition(*args, **kwargs)
        except Exception as failure:
            expression = args[0] if args else kwargs.get("expression")
            recorded = {"expression": expression, "exception": repr(failure)}
            encoded = json.dumps(recorded, ensure_ascii=False)
            try:
                with condition_diagnostic.open("a", encoding="utf-8") as stream:
                    stream.write(encoded + "\n")
                print("PENDING_CONDITION_DIAGNOSTIC " + encoded, flush=True)
            except OSError:
                pass
            raise

    monkeypatch.setattr(_effect_executor, "evaluate_condition", observed_condition)


@pytest.fixture(autouse=True)
def _admitted_casting_scene(monkeypatch):
    """Supply the actual scene required by the composed casting clock consumers."""
    from tests import test_srd_public_spell_casting as public_casting
    from tests.test_srd_spell_wave3_information import _entity, _scene

    original_case = _spell_case

    def case_with_scene(spell):
        state, data, parameters = original_case(spell)
        if "spatial_scene" not in state[f"{SRD}.state"]:
            state[f"{SRD}.state"]["spatial_scene"] = _scene(
                *[
                    _entity(subject, [index * 5, 0, 0], participant_ref=subject)
                    for index, subject in enumerate(state[f"{SRD}.entities"])
                ]
            )
        return state, data, parameters

    monkeypatch.setattr(__name__ + "._spell_case", case_with_scene)
    monkeypatch.setattr(public_casting, "_spell_case", case_with_scene)


def _ritual(*, combat=False):
    state, data, parameters = _spell_case("water_walk")
    parameters.update(casting_mode="ritual", slot_resource="ritual", targets=["guard"])
    state[f"{SRD}.entities"]["hero"]["turns_started"] = 1
    if not combat:
        state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    return state, data, parameters


def _begin(parameters):
    names = {
        "spell",
        "caster",
        "caster_state",
        "casting_mode",
        "slot_resource",
        "verbal_component_available",
        "somatic_component_available",
        "material_component_available",
        "material_item_ids",
        "spell_grant",
        "spellbook_readable",
    }
    return {name: value for name, value in parameters.items() if name in names}


def _external_slow(state, data):
    """Apply another creature's actual Slow without ending it when Hero begins casting."""
    from tests.test_srd_reaction_spells import _mage

    combat = deepcopy(state[f"{SRD}.combat"])
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _mage(state, "slow")
    slow_state, _, slow_parameters = _spell_case("slow")
    entities = state[f"{SRD}.entities"]
    entities["mage"]["spell_materials"] = deepcopy(
        slow_state[f"{SRD}.entities"]["hero"]["spell_materials"]
    )
    entities["mage"]["inventory"] = [
        m["inventory_item"] for m in entities["mage"]["spell_materials"]
    ]
    entities["hero"]["saving_throw_modifiers"] = dict.fromkeys(data["abilities"], 0)
    slow_parameters.pop("caster")
    slow_parameters.pop("slot_resource")
    slow_parameters.update(caster_state="mage", targets=["hero"], forego_save_targets=["hero"])
    _run("cast_spell_outside_combat", state, **slow_parameters)
    state[f"{SRD}.combat"] = combat
    assert state[f"{SRD}.entities"]["hero"]["spell_slow"] is True


@pytest.mark.parametrize("ritual,combat", [(False, True), (True, False)])
def test_slow_failed_actual_longer_attempt_spends_costs_once_and_creates_no_channel(
    monkeypatch,
    ritual,
    combat,
):
    state, data, parameters = _ritual(combat=combat)
    if not ritual:
        state, data, parameters = _spell_case("foresight")
        state[f"{SRD}.entities"]["hero"]["turns_started"] = 1
    resource = parameters["slot_resource"] if not ritual else "spell_slot_3"
    _external_slow(state, data)
    monkeypatch.setattr(dice, "_rng", Random(2))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    result = _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters)).result
    hero = state[f"{SRD}.entities"]["hero"]
    assert result["context"]["_slow_cast_failed"] is True
    assert result["context"]["_slow_cast_failure_roll"]["results"] == [1]
    assert hero.get("extended_casting") is None
    assert hero.get("concentration_spell_id") is None
    assert hero["resources"][resource] == (3 if ritual else 2)
    assert state[f"{SRD}.entities"]["guard"].get("spell_water_walk") is not True
    assert "Slow: Somatic gestures failed" in str(result["result_card"])
    if combat:
        assert state[f"{SRD}.combat"]["action_available"] is False
        assert state[f"{SRD}.combat"]["normal_action_spent"] is True


def test_started_slow_gesture_survives_expiry_and_cannot_reroll_at_ritual_completion(monkeypatch):
    state, data, parameters = _ritual()
    _external_slow(state, data)
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    hero = state[f"{SRD}.entities"]["hero"]
    saved_roll = deepcopy(hero["extended_casting"]["slow_cast"]["roll"])
    assert saved_roll["results"] == [4]
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
    assert state[f"{SRD}.entities"]["hero"]["spell_slow"] is False
    monkeypatch.setattr(dice, "_rng", Random(2))
    result = _run("cast_spell", state, participant_data=data, **parameters).result
    assert result["context"]["_slow_cast_failure_roll"] == saved_roll
    assert state[f"{SRD}.entities"]["guard"]["spell_water_walk"] is True
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 3


def _long_reaction_case():
    """Place the actual prepared reacting creature in the admitted casting scene."""
    from tests.test_srd_reaction_spells import _mage
    from tests.test_srd_scene_relations import _state

    state, data, parameters = _ritual(combat=True)
    _mage(state, "counterspell")
    scene = deepcopy(_state()[f"{SRD}.state"]["spatial_scene"])
    scene["entities"][1].update(id="mage", position=[10, 0, 0])
    scene["entities"][1]["metadata"]["participant_ref"] = "mage"
    state[f"{SRD}.state"]["spatial_scene"] = scene
    return state, data, parameters


@pytest.mark.parametrize("seed,countered", [(8, True), (5, False)])
def test_counterspell_longer_attempt_interrupts_only_failed_save_without_early_spell(
    monkeypatch,
    seed,
    countered,
):
    from tests.test_srd_reaction_spells import _cast_parameters

    state, data, parameters = _long_reaction_case()
    begun = _run(
        "begin_srd_extended_spell_action", state, participant_data=data, **_begin(parameters)
    ).result
    event_id = begun["context"]["_pending_action_id"]
    event = state[f"{SRD}.state"]["pending_srd_actions"][event_id]
    assert event["kind"] == "spell_cast" and event["owner_kind"] == "extended_casting"
    assert event["components"] == ["verbal", "somatic", "material"]
    monkeypatch.setattr(dice, "_rng", Random(seed))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    _run("cast_srd_reaction_spell", state, **_cast_parameters("counterspell", event_id))
    _run("complete_srd_pending_action", state, participant_data=data, pending_action_id=event_id)
    hero = state[f"{SRD}.entities"]["hero"]
    assert (hero["extended_casting"] is None) is countered
    if not countered:
        assert hero["extended_casting"]["progress_rounds"] == 1
        assert hero["extended_casting"]["window_action_id"] is None
    assert hero["resources"]["spell_slot_3"] == 3
    assert state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["counterspell"] == 1
    assert state[f"{SRD}.entities"]["guard"].get("spell_water_walk") is not True
    assert state[f"{SRD}.state"]["pending_srd_action_stack"] == []


@pytest.mark.parametrize("countered", [True, False])
def test_counterspell_later_casting_round_consumes_its_actual_magic_action(monkeypatch, countered):
    from tests.test_srd_reaction_spells import _cast_parameters

    state, data, parameters = _long_reaction_case()
    begun = _run(
        "begin_srd_extended_spell_action", state, participant_data=data, **_begin(parameters)
    ).result
    _run(
        "complete_srd_pending_action",
        state,
        pending_action_id=begun["context"]["_pending_action_id"],
    )
    _run("end_combat_turn", state, actor="hero")
    _run("begin_combat_turn", state, actor="hero")
    progress = _run(
        "continue_srd_extended_spell_action",
        state,
        caster_state="hero",
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    ).result
    event_id = progress["context"]["_pending_action_id"]
    _assert_continuation_card(progress["result_card"], "Progressing", 2)
    recorded_progress = deepcopy(progress)
    pending = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="pending reaction"):
        _run("continue_srd_extended_spell_action", state, caster_state="hero")
    assert state == pending
    assert state[f"{SRD}.entities"]["hero"]["extended_casting"]["progress_rounds"] == 2
    assert state[f"{SRD}.combat"]["action_available"] is False
    monkeypatch.setattr(dice, "_rng", Random(8))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    if countered:
        _run("cast_srd_reaction_spell", state, **_cast_parameters("counterspell", event_id))
    done = _run("complete_srd_pending_action", state, pending_action_id=event_id).result
    if countered:
        assert state[f"{SRD}.entities"]["hero"]["extended_casting"] is None
        assert state[f"{SRD}.state"]["extended_casting_sources"] == []
    else:
        assert state[f"{SRD}.entities"]["hero"]["extended_casting"]["progress_rounds"] == 2
        assert state[f"{SRD}.entities"]["hero"]["extended_casting"]["window_action_id"] is None
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 3
    assert state[f"{SRD}.entities"]["guard"].get("spell_water_walk") is not True
    assert progress == recorded_progress
    assert "casting window" in json.dumps(done["result_card"])
    assert (
        "lost" in json.dumps(done["result_card"])
        if countered
        else "open" in json.dumps(done["result_card"])
    )
    assert "Ready to finish" not in json.dumps(done["result_card"])


def test_public_longer_cast_window_reopens_and_retries_before_actual_counterspell(
    tmp_path,
    monkeypatch,
):
    from tests import test_srd_public_spell_casting as public
    from tests.test_srd_npc_spell_casting import _act
    from tests.test_srd_reaction_spells import _cast_parameters

    state, data, parameters = _long_reaction_case()
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    monkeypatch.setattr(public, "_spell_case", lambda _spell: (state, data, parameters))
    monkeypatch.setattr(dice, "_rng", Random(8))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "long-cast-window.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _create_spell_campaign(owner, "water_walk")
        begun, command = _act(owner, "begin_srd_extended_spell_action", **_begin(parameters))
        assert begun.status is ActionStatus.COMMITTED
        assert "Progressing" not in json.dumps(begun.result.get("result_card"))
        saved = owner.snapshot("casting")
        assert owner.execute_action(command) == begun
        assert owner.snapshot("casting").state == saved.state
        event_id = saved.state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        countered, counter_command = _act(
            owner, "cast_srd_reaction_spell", **_cast_parameters("counterspell", event_id)
        )
        assert countered.status is ActionStatus.COMMITTED
        assert owner.execute_action(counter_command) == countered
        done, finish_command = _act(
            owner, "complete_srd_pending_action", pending_action_id=event_id
        )
        assert done.status is ActionStatus.COMMITTED
        assert "casting window" in json.dumps(done.result["result_card"])
        assert "lost" in json.dumps(done.result["result_card"])
        assert "Ready to finish" not in json.dumps(done.result["result_card"])
        final = owner.snapshot("casting")
        assert final.state[f"{SRD}.entities"]["hero"]["extended_casting"] is None
        assert final.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 3
        assert owner.execute_action(finish_command) == done
        assert owner.snapshot("casting").state == final.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == final.state
        assert owner.saved_action_receipt("casting", command.action_id) == begun
        assert owner.saved_action_receipt("casting", finish_command.action_id) == done
        assert owner.execute_action(command) == begun
        assert owner.execute_action(finish_command) == done
        assert owner.snapshot("casting") == final


def test_continued_action_metadata_copy_has_the_actual_continuation_scalar_contract():
    from scripts.srd_reaction_spells import _extended_casting_windows

    from tests.test_srd_action_budget import ROOT, _runtime_definition

    tools = _runtime_definition((ROOT / "runtime/ruleset.yaml").read_bytes())["tools"]
    source = deepcopy(tools["continue_srd_spell_cast"])
    inherited = _extended_casting_windows(tools)["continue_srd_extended_spell_action"]
    assert inherited["metadata"] == source["metadata"]
    assert inherited["metadata"] is not tools["continue_srd_spell_cast"]["metadata"]
    assert source["metadata"]["result_card"]["summary"][1]["value"] == {
        "path": "context._continued_cast_rounds"
    }
    assert source["metadata"]["result_card"]["summary"][2]["value"] == {
        "path": "context._continued_cast_required_rounds"
    }
    # The actual continuation runs before open_srd_extended_spell_window can add a pending ID.
    offset = 2  # Existing pending-stack and one-round requirements.
    assert inherited["effects"][offset : offset + len(source["effects"])] == source["effects"]
    assert (
        inherited["effects"][offset + len(source["effects"])]["set"]["field"]
        == "_pending_action_id"
    )
    assert tools["continue_srd_spell_cast"] == source


@pytest.mark.parametrize("countered", [True, False])
def test_public_continued_action_receipt_survives_pending_completion_and_replay(
    tmp_path,
    monkeypatch,
    countered,
):
    from sylvia_gm.domain.errors import MechanicsRejected
    from tests import test_srd_public_spell_casting as public
    from tests.test_srd_npc_spell_casting import _act
    from tests.test_srd_reaction_spells import _cast_parameters

    state, data, parameters = _long_reaction_case()
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    monkeypatch.setattr(public, "_spell_case", lambda _spell: (state, data, parameters))
    monkeypatch.setattr(dice, "_rng", Random(8))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "continued-window.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _create_spell_campaign(owner, "water_walk")
        begun, _ = _act(owner, "begin_srd_extended_spell_action", **_begin(parameters))
        assert begun.status is ActionStatus.COMMITTED
        closed, _ = _act(
            owner,
            "complete_srd_pending_action",
            pending_action_id=begun.result["context"]["_pending_action_id"],
        )
        assert closed.status is ActionStatus.COMMITTED
        progress, command = _act(
            owner,
            "continue_srd_extended_spell_action",
            caster_state="hero",
            elapsed_rounds=1,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert progress.status is ActionStatus.COMMITTED
        _assert_continuation_card(progress.result["result_card"], "Progressing", 1)
        saved_result = deepcopy(progress.result)
        pending = owner.snapshot("casting")
        event_id = progress.result["context"]["_pending_action_id"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == pending
        assert owner.saved_action_receipt("casting", command.action_id) == progress
        assert owner.execute_action(command) == progress
        assert owner.snapshot("casting") == pending
        with pytest.raises(MechanicsRejected, match="pending reaction"):
            _act(owner, "continue_srd_extended_spell_action", caster_state="hero")
        assert owner.snapshot("casting") == pending
        if countered:
            counter, _ = _act(
                owner, "cast_srd_reaction_spell", **_cast_parameters("counterspell", event_id)
            )
            assert counter.status is ActionStatus.COMMITTED
        done, completion = _act(owner, "complete_srd_pending_action", pending_action_id=event_id)
        assert done.status is ActionStatus.COMMITTED
        assert "casting window" in json.dumps(done.result["result_card"])
        assert ("lost" if countered else "open") in json.dumps(done.result["result_card"])
        final = owner.snapshot("casting")
        channel = final.state[f"{SRD}.entities"]["hero"]["extended_casting"]
        assert (channel is None) is countered
        if not countered:
            assert channel["progress_rounds"] == 1 and channel["window_action_id"] is None
        assert final.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 3
        assert final.state[f"{SRD}.entities"]["guard"].get("spell_water_walk") is not True
        assert owner.saved_action_receipt("casting", command.action_id).result == saved_result
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
        assert owner.execute_action(command) == progress
        assert owner.execute_action(completion) == done
        assert owner.snapshot("casting") == final
        assert owner.saved_action_receipt("casting", command.action_id).result == saved_result


def test_ritual_requires_100_rounds_then_casts_at_base_level_without_slot_or_second_action():
    state, data, parameters = _ritual()
    before = deepcopy(state[f"{SRD}.entities"]["hero"]["resources"])
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["extended_casting"]["required_rounds"] == 100
    assert hero["extended_casting"]["progress_rounds"] == 0
    assert hero["concentration_spell_id"] == "water_walk"
    rejected = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Start and continue"):
        _run("cast_spell", state, participant_data=data, **parameters)
    assert state == rejected
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
        elapsed_rounds=99,
        uninterrupted=True,
    )
    with pytest.raises(RulesetPreconditionRejected, match="Start and continue"):
        _run("cast_spell", state, participant_data=data, **parameters)
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
        uninterrupted=True,
    )
    assert state[f"{SRD}.state"]["lasting_tick"] == 200
    result = _run("cast_spell", state, participant_data=data, **parameters)
    assert result.result["status"] == "committed"
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["resources"] == before
    assert hero["extended_casting"] is None
    assert hero["concentration_spell_id"] is None
    assert state[f"{SRD}.state"]["extended_casting_sources"] == []
    assert state[f"{SRD}.entities"]["guard"]["spell_water_walk"] is True
    assert "no spell slot spent" in str(result.result["result_card"])


@pytest.mark.parametrize(
    "changed",
    [
        {"casting_mode": "ordinary"},
        {"slot_resource": "spell_slot_3"},
        {"spell": "fly", "slot_resource": "ritual", "casting_mode": "ritual"},
    ],
)
def test_ritual_cannot_bypass_the_source_tag_or_resource_contract(changed):
    state, data, parameters = _ritual()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters | changed))
    assert state == before


def test_combat_casting_requires_each_turn_magic_action_and_cannot_accelerate():
    state, data, parameters = _ritual(combat=True)
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    assert state[f"{SRD}.combat"]["normal_action_spent"] is True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="same turn"):
        _run(
            "continue_srd_spell_cast",
            state,
            caster_state="hero",
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
    assert state == before
    _run("end_combat_turn", state, actor="hero")
    _run("begin_combat_turn", state, actor="hero")
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    assert state[f"{SRD}.entities"]["hero"]["extended_casting"]["progress_rounds"] == 2
    _run("end_combat_turn", state, actor="hero")
    _run("begin_combat_turn", state, actor="hero")
    _run("end_combat_turn", state, actor="hero")
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["concentration_spell_id"] is None
    assert hero["extended_casting"] is None
    assert state[f"{SRD}.state"]["extended_casting_sources"] == []
    assert hero["resources"]["spell_slot_3"] == 3


def test_casting_continuation_checks_current_components_without_exposing_package_clock():
    state, data, parameters = _ritual()
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Verbal component"):
        _run(
            "continue_srd_spell_cast",
            state,
            caster_state="hero",
            uninterrupted=True,
            somatic_component_available=True,
            material_component_available=True,
        )
    assert state == before
    # Public tool schemas cannot select an alternate package clock.
    record = json.loads(
        (Path(__file__).parents[1] / "rulesets/srd-lite/package-record.json").read_text()
    )
    declarations = {t["name"]: t["input_schema"] for t in record["tools"]}
    for name in ("begin_srd_spell_cast", "continue_srd_spell_cast"):
        assert "lasting_state" not in declarations[name]["properties"]


def test_starting_material_validation_retains_priced_consumed_components():
    state, data, parameters = _spell_case("stoneskin")
    hero = state[f"{SRD}.entities"]["hero"]
    before = deepcopy(hero)
    _run(
        "resolve_srd_spell_materials",
        state,
        caster_state="hero",
        spell="stoneskin",
        item_ids=parameters["material_item_ids"],
        materials_available=True,
        components_waived=False,
        target_count=1,
        permitted_focus_kinds=[],
        validate_only=True,
    )
    assert state[f"{SRD}.entities"]["hero"] == before
    _run("cast_spell", state, participant_data=data, **parameters)
    assert state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["quantity"] == 2


@pytest.mark.parametrize("book_present,ritual", [(True, True), (False, True), (True, False)])
def test_ritual_adept_grants_an_unprepared_book_spell_only_as_a_wizard_ritual(book_present, ritual):
    state, data, _ = _spell_case("mage_armor")
    if book_present:
        data["wizard_spellbook"].append("comprehend_languages")
    before = deepcopy(state)
    if book_present and ritual:
        result = _run(
            "validate_srd_spell_grants",
            state,
            participant_data=data,
            caster="hero",
            spell="comprehend_languages",
            ritual=ritual,
        )
        assert result.result["context"]["_grant_ability"] == "intelligence"
        assert result.result["context"]["_ritual_book_required"] is True
    else:
        with pytest.raises(RulesetPreconditionRejected, match="saved spellcasting grant"):
            _run(
                "validate_srd_spell_grants",
                state,
                participant_data=data,
                caster="hero",
                spell="comprehend_languages",
                ritual=ritual,
            )
    assert state == before


@pytest.mark.parametrize("spell", ["comprehend_languages", "water_breathing"])
def test_ritual_senses_reach_the_consumer_and_start_duration_after_casting(spell):
    state, data, parameters = _spell_case(spell)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    parameters.update(casting_mode="ritual", slot_resource="ritual", spellbook_readable=True)
    if spell == "comprehend_languages":
        data["spellcasting_grants"][0]["spells"] = ["mage_armor"]
        data["wizard_spellbook"].append("mage_armor")
        state[f"{SRD}.entities"]["hero"]["prepared_spells"] = ["mage_armor"]
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected, match="reading"):
            _run(
                "begin_srd_spell_cast",
                state,
                participant_data=data,
                **(_begin(parameters) | {"spellbook_readable": False}),
            )
        assert state == before
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=100,
        uninterrupted=True,
        spellbook_readable=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    effect = state[f"{SRD}.state"]["turn_effects"][-1]
    assert effect["expires_tick"] == 200 + (1200 if spell == "comprehend_languages" else 28800)
    assert state[f"{SRD}.entities"]["hero"]["resources"] == {
        f"spell_slot_{level}": 3 for level in range(1, 10)
    }
    target = "hero" if spell == "comprehend_languages" else "guard"
    senses = _run("resolve_srd_timed_senses", state, target_state=target)
    field = "_sense_comprehension" if spell == "comprehend_languages" else "_sense_water_breathing"
    assert senses.result["context"][field] is True


@pytest.mark.parametrize("interruption", ["damage", "condition", "dismiss", "elapsed"])
def test_interruption_ends_the_cast_without_spending_its_spell_slot(interruption):
    state, data, parameters = _ritual()
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    if interruption == "damage":
        state[f"{SRD}.entities"]["hero"]["concentration_save_modifier"] = -100
        _run("apply_srd_damage", state, target="hero", amount=1, adjustment=0, damage_type="force")
    elif interruption == "condition":
        _run("apply_srd_condition", state, target="hero", condition="stunned")
    elif interruption == "dismiss":
        _run("end_concentration", state, caster_state="hero")
    else:
        _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["concentration_spell_id"] is None
    assert hero["extended_casting"] is None
    assert hero["resources"]["spell_slot_3"] == 3
    with pytest.raises(RulesetPreconditionRejected, match="no uninterrupted"):
        _run(
            "continue_srd_spell_cast",
            state,
            caster_state="hero",
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
            uninterrupted=True,
        )


def test_public_ritual_survives_reopen_and_retries_without_repeating_time_or_effect(tmp_path):
    database = tmp_path / "ritual.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "water_walk")
        parameters.update(casting_mode="ritual", slot_resource="ritual", targets=["guard"])
        begin = ActionCommand(
            campaign_id="casting",
            action_id="begin",
            idempotency_key="begin",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="begin_srd_spell_cast",
            parameters=_begin(parameters),
        )
        mechanics = StatelessMcpMechanics(owner)
        receipt = mechanics.execute_action(begin)
        assert receipt.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(begin) == receipt
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        mechanics = StatelessMcpMechanics(reopened)
        assert reopened.snapshot("casting").state == saved.state
        continuation = replace(
            begin,
            action_id="continue",
            idempotency_key="continue",
            expected_revision=saved.revision,
            tool_name="continue_srd_spell_cast",
            parameters={
                "caster_state": "hero",
                "elapsed_rounds": 99,
                "uninterrupted": True,
                "verbal_component_available": True,
                "somatic_component_available": True,
                "material_component_available": True,
            },
        )
        progress = mechanics.execute_action(continuation)
        assert progress.status is ActionStatus.COMMITTED
        _assert_continuation_card(progress.result["result_card"], "Progressing", 99)
        saved_progress = deepcopy(progress.result)
        assert mechanics.execute_action(continuation) == progress
        intermediate = reopened.snapshot("casting")
        ready_command = replace(
            continuation,
            action_id="ready",
            idempotency_key="ready",
            expected_revision=intermediate.revision,
            parameters=continuation.parameters | {"elapsed_rounds": 1},
        )
        ready_receipt = mechanics.execute_action(ready_command)
        assert ready_receipt.status is ActionStatus.COMMITTED
        _assert_continuation_card(ready_receipt.result["result_card"], "Ready to finish", 100)
        saved_ready = deepcopy(ready_receipt.result)
        assert mechanics.execute_action(ready_command) == ready_receipt
        ready = reopened.snapshot("casting")
        assert ready.state[f"{SRD}.state"]["lasting_tick"] == 200
        assert ready.state[f"{SRD}.entities"]["guard"].get("spell_water_walk") is not True
        finish = replace(
            begin,
            action_id="finish",
            idempotency_key="finish",
            expected_revision=ready.revision,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        result = mechanics.execute_action(finish)
        assert result.status is ActionStatus.COMMITTED
        finished = reopened.snapshot("casting")
        assert mechanics.execute_action(finish) == result
        assert reopened.snapshot("casting").state == finished.state
        assert finished.state[f"{SRD}.entities"]["guard"]["spell_water_walk"] is True
        assert finished.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 3
        assert progress.result == saved_progress
        assert ready_receipt.result == saved_ready
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        mechanics = StatelessMcpMechanics(reopened)
        before_replay = reopened.snapshot("casting")
        assert reopened.saved_action_receipt("casting", "continue") == progress
        assert reopened.saved_action_receipt("casting", "ready") == ready_receipt
        assert mechanics.execute_action(continuation) == progress
        assert mechanics.execute_action(ready_command) == ready_receipt
        assert mechanics.execute_action(finish) == result
        assert reopened.snapshot("casting") == before_replay


def _assert_continuation_card(card, stage, completed_rounds):
    assert card["title"] == "Water Walk"
    assert card["summary"] == [
        {"label": "Casting", "value": stage, "emphasis": True},
        {"label": "Rounds completed", "value": completed_rounds, "emphasis": False},
        {"label": "Rounds required", "value": 100, "emphasis": False},
    ]
    assert card["details"] == []
    assert card["actions"] == []


def test_continuation_card_matches_scoped_source_emission_on_the_composed_input():
    from scripts.srd_extended_casting import (
        _declare_continuation_outer,
        _declare_continuation_progress,
    )
    from scripts.srd_simulacrum_consumers import _declare_interrupted_continuation_receipt
    from scripts.srd_timed_actor_defenses import _load_runtime

    from tests import test_srd_action_budget as budget

    text = (budget.ROOT / "runtime/ruleset.yaml").read_text(encoding="utf-8")
    native = _load_runtime(text)["tools"]
    # Exercise only the presentation emitters used by the real owners. Full builders
    # require earlier direct-cost/direct-summary stages, absent in composed B2 input.
    emitted = deepcopy(native)

    def remove_captures(effects):
        kept = []
        for effect in effects:
            if effect.get("set", {}).get("field", "").startswith("_continued_"):
                continue
            for branch in ("on_true", "on_false"):
                if branch in effect:
                    effect[branch] = remove_captures(effect[branch])
            if (
                effect.get("condition")
                == (
                    "caster_state.extended_casting.progress_rounds >= "
                    "caster_state.extended_casting.required_rounds"
                )
                and effect.get("on_true") == []
            ):
                continue
            kept.append(effect)
        return kept

    private = emitted["continue_srd_spell_cast_internal"]
    outer = emitted["continue_srd_spell_cast"]
    private["effects"] = remove_captures(private["effects"])
    outer["effects"] = remove_captures(outer["effects"])
    outer["metadata"].pop("result_card")
    _declare_continuation_progress(private)
    _declare_continuation_outer(outer)
    _declare_interrupted_continuation_receipt(
        outer,
        _load_runtime(text)["definitions"]["spell_casting"]["simulacrum"]["name"],
    )
    assert emitted == native  # Includes every unowned execution body and wrapper.
    _declare_continuation_progress(private)
    _declare_continuation_outer(outer)
    _declare_interrupted_continuation_receipt(
        outer,
        _load_runtime(text)["definitions"]["spell_casting"]["simulacrum"]["name"],
    )
    assert emitted == native  # Repetition must not duplicate any capture or hook.
    assert (
        emitted["continue_srd_spell_cast"]["metadata"]["result_card"]
        == (native["continue_srd_spell_cast"]["metadata"]["result_card"])
    )
    expected = {
        "_continued_spell_label",
        "_continued_cast_stage",
        "_continued_cast_rounds",
        "_continued_cast_required_rounds",
    }

    def effects(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from effects(child)
        elif isinstance(value, list):
            for child in value:
                yield from effects(child)

    for tools in (native, emitted):
        captures = {
            effect["set"]["field"]
            for effect in effects(tools["continue_srd_spell_cast_internal"]["effects"])
            if "set" in effect
        }
        assert expected <= captures
        delegate = next(
            effect
            for effect in effects(tools["continue_srd_spell_cast"]["effects"])
            if effect.get("call") == "continue_srd_spell_cast_internal"
        )
        assert delegate["export_context"] == {field: field for field in expected}
        interruption = next(
            effect
            for effect in effects(tools["continue_srd_spell_cast"]["effects"])
            if effect.get("condition") == "_sim_proximity_failed == true"
        )["on_true"]
        assert interruption[0] == {
            "set": {"field": "_continued_cast_stage", "value": "'interrupted'"}
        }


@pytest.mark.parametrize("elapsed_rounds", [99, 100])
def test_continuation_receipt_records_progress_without_resolving_the_spell(elapsed_rounds):
    state, data, parameters = _ritual()
    resources = deepcopy(state[f"{SRD}.entities"]["hero"]["resources"])
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    result = _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=elapsed_rounds,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    ).result

    assert result["status"] == "committed"
    channel = result["context"]["caster_state"]["extended_casting"]
    assert channel["spell"] == "water_walk"
    assert channel["progress_rounds"] == elapsed_rounds
    assert channel["required_rounds"] == 100
    assert state[f"{SRD}.entities"]["hero"]["resources"] == resources
    assert state[f"{SRD}.entities"]["guard"].get("spell_water_walk") is not True

    card = result.get("result_card")
    assert isinstance(card, dict), "Committed continuation needs its own saved progress receipt"
    rows = card["summary"] + card["details"]
    numeric_values = [row["value"] for row in rows if type(row["value"]) is int]
    assert elapsed_rounds in numeric_values
    assert 100 in numeric_values
    assert "Water Walk" in json.dumps(card)
    assert "Guard" not in json.dumps(card)
    stage = "Progressing" if elapsed_rounds < 100 else "Ready to finish"
    _assert_continuation_card(card, stage, elapsed_rounds)
    saved_result = deepcopy(result)

    if elapsed_rounds < 100:
        _run(
            "continue_srd_spell_cast",
            state,
            caster_state="hero",
            elapsed_rounds=100 - elapsed_rounds,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
    ready = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="casting is ready"):
        _run(
            "continue_srd_spell_cast",
            state,
            caster_state="hero",
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
    assert state == ready
    _run("cast_spell", state, participant_data=data, **parameters)
    assert state[f"{SRD}.entities"]["hero"]["extended_casting"] is None
    assert state[f"{SRD}.entities"]["guard"]["spell_water_walk"] is True
    assert state[f"{SRD}.entities"]["hero"]["resources"] == resources
    state[f"{SRD}.entities"]["hero"].update(name="Later name", extended_casting=None)
    assert result == saved_result
    source = Path(__file__).parents[1] / "src-tauri/frontend/app.js"
    script = """
const fs = require('fs'), assert = require('node:assert/strict');
global.window = {}; global.document = {addEventListener(){}};
eval(fs.readFileSync(process.argv[1], 'utf8'));
const result = JSON.parse(process.argv[2]);
const html = window.SylviaGMFrontend.toolResultMarkup({
  tool_name: 'continue_srd_spell_cast', result
}, '');
assert(html.includes('Water Walk'));
assert(html.includes(process.argv[3]));
assert(html.includes('result-card-compact'));
assert(!html.includes('Operation completed'));
assert(!html.includes('Guard') && !html.includes('Later name'));
"""
    subprocess.run(
        ["node", "-e", script, str(source), json.dumps(saved_result), stage],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("tool_name", ["begin_srd_spell_cast", "continue_srd_spell_cast"])
def test_public_casting_schema_matches_native_private_clock_contract(tool_name):
    from scripts.build_srd_5_2_1_mechanics import _tool_schema
    from scripts.srd_extended_casting import _bind_private_casting_clock

    from tests import test_srd_action_budget as budget

    tools = budget._runtime_definition((budget.ROOT / "runtime/ruleset.yaml").read_bytes())["tools"]
    tool = tools[tool_name]
    record = json.loads((budget.ROOT / "package-record.json").read_bytes())
    schema = next(row["input_schema"] for row in record["tools"] if row["name"] == tool_name)
    generated = _tool_schema(tool, schema, tool_name=tool_name)
    # B2 omits existing descriptive/model-display annotations from its manifest.
    # Preserve and compare every parameter validation constraint and reference.
    for name, child in generated["properties"].items():
        for annotation in ("description", "x-sylvia-gm-model-hidden"):
            if annotation not in schema["properties"][name]:
                child.pop(annotation, None)
    assert generated == schema
    assert "lasting_state" not in schema["properties"]
    assert all(parameter["name"] != "lasting_state" for parameter in tool["parameters"])
    assert "lasting_state." not in json.dumps(tool["effects"])
    assert "{_lasting_state_id}" not in json.dumps(tool["effects"])
    rebound = deepcopy(tool)
    _bind_private_casting_clock(rebound)
    assert rebound == tool


@pytest.mark.parametrize("continued", [False, True])
def test_public_casting_rejects_alternate_clock_without_state_or_spend(
    tmp_path,
    monkeypatch,
    continued,
):
    from tests import test_srd_public_spell_casting as public

    original_case = public._spell_case

    def with_alternate_clock(spell):
        state, data, parameters = original_case(spell)
        alternate = deepcopy(state[f"{SRD}.entities"]["guard"])
        alternate.update(deepcopy(state[f"{SRD}.state"]))
        alternate["lasting_tick"] = 987654
        state[f"{SRD}.entities"]["alternate-clock"] = alternate
        return state, data, parameters

    monkeypatch.setattr(public, "_spell_case", with_alternate_clock)
    with AuthorityOwner.open(tmp_path / "alternate-clock.sqlite3", "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "water_walk")
        parameters.update(casting_mode="ritual", slot_resource="ritual", targets=["guard"])
        command = ActionCommand(
            campaign_id="casting",
            action_id="begin",
            idempotency_key="begin",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="begin_srd_spell_cast",
            parameters=_begin(parameters),
        )
        mechanics = StatelessMcpMechanics(owner)
        if continued:
            receipt = mechanics.execute_action(command)
            assert receipt.status is ActionStatus.COMMITTED
            command = replace(
                command,
                action_id="continue",
                idempotency_key="continue",
                expected_revision=owner.snapshot("casting").revision,
                tool_name="continue_srd_spell_cast",
                parameters={
                    "caster_state": "hero",
                    "elapsed_rounds": 1,
                    "uninterrupted": True,
                    "verbal_component_available": True,
                    "somatic_component_available": True,
                    "material_component_available": True,
                },
            )
        before = owner.snapshot("casting")
        invalid = replace(
            command, parameters=command.parameters | {"lasting_state": "alternate-clock"}
        )
        for _ in range(2):
            with pytest.raises(MechanicsRejected, match="declared parameters") as rejected:
                mechanics.execute_action(invalid)
            assert "lasting_state" in rejected.value.field_paths
            after = owner.snapshot("casting")
            assert after.state == before.state
            assert after.revision == before.revision
        assert after.state[f"{SRD}.entities"]["alternate-clock"]["lasting_tick"] == 987654
        valid = mechanics.execute_action(command)
        assert valid.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert mechanics.execute_action(command) == valid
        assert owner.snapshot("casting").state == saved.state
        assert (
            saved.state[f"{SRD}.entities"]["alternate-clock"]
            == (before.state[f"{SRD}.entities"]["alternate-clock"])
        )
