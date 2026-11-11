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










def _assert_continuation_card(card, stage, completed_rounds):
    assert card["title"] == "Water Walk"
    assert card["summary"] == [
        {"label": "Casting", "value": stage, "emphasis": True},
        {"label": "Rounds completed", "value": completed_rounds, "emphasis": False},
        {"label": "Rounds required", "value": 100, "emphasis": False},
    ]
    assert card["details"] == []
    assert card["actions"] == []






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
