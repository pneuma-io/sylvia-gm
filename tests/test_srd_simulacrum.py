"""Simulacrum uses the actual native body, casting clock, resources and recovery owners."""

import json
import os
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_awaken as awaken
from tests import test_srd_magic_jar as jar
from tests.test_srd_extended_casting import _begin
from tests.test_srd_spell_wave3_information import _entity, _scene

SRD = jar.SRD


@pytest.fixture(autouse=True)
def _compiled(monkeypatch):
    root = os.environ.get("FC14_SIMULACRUM_RUNTIME_ROOT")
    assert root, "Select the actual composed Simulacrum owner closure"
    monkeypatch.setenv("FC14_JAR_RUNTIME_ROOT", root)
    # Existing isolated declaration constants predate the genuine Beacon zero-roll signature.
    # Private receiver proof uses its actual runtime range; Root owns the declaration correction.
    from scripts.build_srd_5_2_1_mechanics import MANIFEST_PARAMETER_CONSTRAINTS

    monkeypatch.setitem(
        MANIFEST_PARAMETER_CONSTRAINTS["restore_hit_points"]["amount"], "minimum", 0
    )
    yield from jar._compiled.__wrapped__(monkeypatch)


def _run(tool_name, state, data, **params):
    return jar._run(tool_name, state, data, **params)


def _case(*, native_type="beast"):
    state, data, params = jar.cards._spell_case("simulacrum")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{SRD}.state"]["spatial_scene"] = _scene(
        _entity("hero", [0, 0, 0], participant_ref="hero"),
        _entity("beast", [3, 0, 0], participant_ref="beast"),
        _entity("snow", [-3, 0, 0], material="snow", is_object=True),
        _entity("snow2", [0, 3, 0], material="snow", is_object=True),
    )
    # Native original is admitted by the ordinary actor owner, not copied fixture state.
    _run(
        "prepare_srd_creature",
        state,
        data,
        **(
            awaken._native_params(creature_type=native_type)
            | {
                "spellcasting_ability": "wisdom",
                "spellcasting_modifier": 0,
                "spell_attack_bonus": 2,
                "spell_save_dc": 10,
                "spells": [
                    {"spell": "cure_wounds", "uses_per_day": 2},
                    {"spell": "chill_touch", "uses_per_day": 2},
                ],
            }
        ),
    )
    params.update(target="beast", simulacrum_pile="snow", simulacrum_friends=[])
    return state, data, params


def _target(state, name="snow"):
    return state[f"{SRD}.entities"][name]


def _begin_cast(state, data, params):
    return _run("begin_srd_spell_cast", state, data, **(_begin(params) | {"target": "beast"}))


def _cast(state, data, params):
    _begin_cast(state, data, params)
    _run(
        "continue_srd_spell_cast",
        state,
        data,
        caster_state="hero",
        elapsed_rounds=7200,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    return _run("cast_spell_outside_combat", state, data, **params)


def test_real_twelve_hour_cast_copies_native_statistics_and_spent_resources_without_equipment():
    state, data, params = _case()
    native_cast = {
        k: v
        for k, v in params.items()
        if k
        not in {
            "caster",
            "slot_resource",
            "simulacrum_pile",
            "simulacrum_friends",
        }
    }
    native_cast.update(
        spell="cure_wounds",
        caster_state="beast",
        target="beast",
        target_distance=0,
        material_item_ids=[],
    )
    _run("cast_spell_outside_combat", state, data, **native_cast)
    original = deepcopy(_target(state, "beast"))
    assert original["spell_uses_remaining"]["cure_wounds"] == 1
    started = state[f"{SRD}.state"].get("lasting_tick", 0)
    stock = _target(state, "hero")["spell_materials"][0]["quantity"]
    slots = _target(state, "hero")["resources"]["spell_slot_7"]
    _cast(state, data, params)
    duplicate = _target(state)
    assert duplicate["creature_type"] == "construct"
    assert duplicate["max_hp"] == original["max_hp"] // 2
    assert duplicate["hp"] == min(original["hp"], duplicate["max_hp"])
    for field in (
        "ability_scores",
        "native_hit_dice",
        "resources",
        "saving_throw_modifiers",
        "spell_uses_remaining",
    ):
        assert duplicate[field] == original[field]
    assert duplicate["inventory"] == [] and duplicate["held_items"] == []
    assert duplicate["attitudes"]["hero"] == "friendly"
    assert _target(state, "hero")["resources"]["spell_slot_7"] == slots - 1
    assert _target(state, "hero")["spell_materials"][0]["quantity"] == stock - 1
    assert state[f"{SRD}.state"]["lasting_tick"] - started == 14400
    scene = state[f"{SRD}.state"]["spatial_scene"]
    body = next(e for e in scene["entities"] if e["id"] == "snow")
    assert body["metadata"]["participant_ref"] == "snow"
    assert body["metadata"]["is_object"] is False


def test_ordinary_healing_is_resolved_without_restoring_simulacrum_and_rest_rejects():
    state, data, params = _case()
    _cast(state, data, params)
    _run(
        "apply_srd_damage",
        state,
        data,
        target="snow",
        amount=4,
        source_state_id="hero",
        source_damage_kind="spell",
        adjustment=0,
        damage_type="force",
    )
    hp = _target(state)["hp"]
    _run("restore_hit_points", state, data, target="snow", amount=6)
    assert _target(state)["hp"] == hp
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Simulacrum"):
        _run("begin_srd_rest", state, data, target_state="snow", kind="long", activity="sleep")
    assert state == before


def test_actual_zero_hit_points_melts_only_the_source_owned_duplicate():
    state, data, params = _case()
    _cast(state, data, params)
    _run(
        "apply_srd_damage",
        state,
        data,
        target="snow",
        amount=20,
        source_state_id="hero",
        source_damage_kind="spell",
        adjustment=0,
        damage_type="force",
    )
    assert _target(state)["simulacrum_melted"] is True
    assert _target(state)["created_active"] is False
    assert _target(state, "beast")["is_alive"] is True
    assert state[f"{SRD}.state"]["created_actor_records"]["snow"]["active"] is False


@pytest.mark.parametrize("completed_rounds", [0, 37])
def test_original_leaving_then_returning_does_not_erase_twelve_hour_proximity_failure(
    completed_rounds,
):
    state, data, params = _case()
    _begin_cast(state, data, params)
    if completed_rounds:
        _run(
            "continue_srd_spell_cast",
            state,
            data,
            caster_state="hero",
            elapsed_rounds=completed_rounds,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
    for destination in ([11, 0, 0], [3, 0, 0]):
        _run(
            "move_srd_scene_creature",
            state,
            data,
            actor_state="beast",
            destination=destination,
            mode="walk",
            difficult_terrain=False,
        )
    assert _target(state, "hero")["extended_casting"]["simulacrum_proximity_broken"] is True
    slot = _target(state, "hero")["resources"]["spell_slot_7"]
    materials = deepcopy(_target(state, "hero")["spell_materials"])
    channel = deepcopy(_target(state, "hero")["extended_casting"])
    receipt = _run(
        "continue_srd_spell_cast",
        state,
        data,
        caster_state="hero",
        elapsed_rounds=7200,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    assert _target(state, "hero")["extended_casting"] is None
    assert _target(state, "hero")["resources"]["spell_slot_7"] == slot
    assert "snow" not in state[f"{SRD}.entities"]
    assert _target(state, "hero")["spell_materials"] == materials
    assert receipt.result["status"] == "committed"
    assert channel["progress_rounds"] == completed_rounds
    _assert_interrupted_card(receipt.result, completed_rounds, channel["required_rounds"])
    saved = deepcopy(receipt.result)
    _target(state, "hero")["name"] = "Later caster"
    _run("advance_srd_lasting_time", state, data, elapsed_rounds=1)
    assert receipt.result == saved


def _assert_interrupted_card(result, completed_rounds, required_rounds):
    card = result.get("result_card")
    assert isinstance(card, dict), "Committed interruption needs its own saved continuation receipt"
    assert card["title"] == "Simulacrum"
    assert card["summary"] == [
        {"label": "Casting", "value": "Interrupted", "emphasis": True},
        {"label": "Rounds completed", "value": completed_rounds, "emphasis": False},
        {"label": "Rounds required", "value": required_rounds, "emphasis": False},
    ]
    assert card["details"] == [] and card["actions"] == []
    assert result["context"]["caster_state"]["extended_casting"] is None
    assert "_continued_cast_requirements" not in result["context"]
    source = Path(__file__).parents[1] / "src-tauri/frontend/app.js"
    script = """
const fs = require('fs'), assert = require('node:assert/strict');
global.window = {}; global.document = {addEventListener(){}};
eval(fs.readFileSync(process.argv[1], 'utf8'));
const html = window.SylviaGMFrontend.toolResultMarkup({
  tool_name: 'continue_srd_spell_cast', result: JSON.parse(process.argv[2])
}, '');
assert(html.includes('Simulacrum') && html.includes('Interrupted'));
assert(html.includes('result-card-compact'));
assert(!html.includes('Operation completed') && !html.includes('Ready to finish'));
assert(!html.includes('Later caster') && !html.includes('Actual host'));
"""
    subprocess.run(
        ["node", "-e", script, str(source), json.dumps(result)],
        check=True,
        capture_output=True,
        text=True,
    )


def test_public_interrupted_continuation_survives_reopen_and_replay(tmp_path, monkeypatch):
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from sylvia_gm.domain.errors import MechanicsRejected
    from tests import test_srd_public_spell_casting as public
    from tests.test_srd_npc_spell_casting import _act

    # Public admission and the direct fixture must bind the same genuine composed runtime.
    assert (jar.budget.ROOT / "runtime/ruleset.yaml").read_bytes() == (
        public.ROOT / "rulesets/srd-lite/runtime/ruleset.yaml"
    ).read_bytes()
    state, data, params = _case()
    monkeypatch.setattr(public, "_spell_case", lambda _spell: (state, data, params))
    database = tmp_path / "interrupted-continuation.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public._create_spell_campaign(owner, "simulacrum")
        begun, _ = _act(owner, "begin_srd_spell_cast", **(_begin(params) | {"target": "beast"}))
        assert begun.status is ActionStatus.COMMITTED
        for destination in ([11, 0, 0], [3, 0, 0]):
            moved, _ = _act(
                owner,
                "move_srd_scene_creature",
                actor_state="beast",
                destination=destination,
                mode="walk",
                difficult_terrain=False,
            )
            assert moved.status is ActionStatus.COMMITTED
        before = owner.snapshot("casting")
        channel = before.state[f"{SRD}.entities"]["hero"]["extended_casting"]
        assert channel["simulacrum_proximity_broken"] is True
        receipt, command = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=7200,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert receipt.status is ActionStatus.COMMITTED
        _assert_interrupted_card(
            receipt.result, channel["progress_rounds"], channel["required_rounds"]
        )
        after = owner.snapshot("casting")
        for field in ("resources", "spell_materials"):
            assert (
                after.state[f"{SRD}.entities"]["hero"][field]
                == before.state[f"{SRD}.entities"]["hero"][field]
            )
        assert "snow" not in after.state[f"{SRD}.entities"]
        saved_result = deepcopy(receipt.result)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.saved_action_receipt("casting", command.action_id) == receipt
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting") == after
        elapsed, _ = _act(owner, "advance_srd_lasting_time", elapsed_rounds=1)
        assert elapsed.status is ActionStatus.COMMITTED
        later = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="no uninterrupted"):
            _act(owner, "continue_srd_spell_cast", caster_state="hero", uninterrupted=True)
        assert owner.snapshot("casting") == later
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting") == later
        assert owner.saved_action_receipt("casting", command.action_id).result == saved_result
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == later
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting") == later
        assert owner.saved_action_receipt("casting", command.action_id).result == saved_result


def test_real_recast_retires_only_prior_owned_body_and_consumes_new_paid_cost():
    state, data, params = _case()
    _cast(state, data, params)
    _cast(state, data, params | {"simulacrum_pile": "snow2"})
    assert _target(state)["simulacrum_melted"] is True
    assert _target(state, "snow2")["created_active"] is True
    assert _target(state, "beast")["hp"] == 20
    assert _target(state, "hero")["resources"]["spell_slot_7"] == 1


def test_repair_spends_real_components_and_only_actual_creator_long_rest_restores_hp():
    state, data, params = _case()
    _target(state, "hero")["spell_materials"].append(
        {
            "id": "repair",
            "inventory_item": "repair_components",
            "kind": "specific",
            "components": [],
            "quantity": 5,
            "value_cp": 10000,
        }
    )
    _target(state, "hero")["inventory"].append("repair_components")
    _cast(state, data, params)
    _run(
        "apply_srd_damage",
        state,
        data,
        target="snow",
        amount=4,
        adjustment=0,
        damage_type="force",
        source_state_id="hero",
        source_damage_kind="spell",
    )
    _run("begin_srd_rest", state, data, target_state="hero", kind="long", activity="light")
    _run(
        "begin_srd_simulacrum_repair",
        state,
        data,
        caster_state="hero",
        duplicate_state="snow",
        hit_points=3,
        material_id="repair",
    )
    assert _target(state)["hp"] == 6
    assert _target(state, "hero")["spell_materials"][-1]["quantity"] == 2
    _run("advance_srd_lasting_time", state, data, elapsed_rounds=1200)
    _run("set_srd_rest_activity", state, data, target_state="hero", activity="sleep")
    _run("advance_srd_lasting_time", state, data, elapsed_rounds=3600)
    _run("complete_srd_rest", state, data, target_state="hero")
    assert _target(state)["hp"] == 9
    assert _target(state, "hero")["simulacrum_repair"] is None


def test_duplicate_acts_once_on_actual_creator_turn_without_refreshing_spent_creator_action():
    state, data, params = _case()
    _cast(state, data, params)
    combat = state[f"{SRD}.combat"]
    combat.update(encounter_active=True, turn_active=False, current_actor="hero")
    _run("begin_combat_turn", state, data)
    _run("take_dodge_action", state, data, actor="hero", actor_state="hero")
    caster_turn = deepcopy(state[f"{SRD}.combat"])
    lasting_tick = state[f"{SRD}.state"]["lasting_tick"]
    _run(
        "begin_srd_simulacrum_turn",
        state,
        data,
        caster_state="hero",
        duplicate_state="snow",
        command="Cast Cure Wounds on the creator",
    )
    assert state[f"{SRD}.combat"]["active_actor"] == "snow"
    native_cast = {
        k: v
        for k, v in params.items()
        if k
        not in {
            "caster",
            "slot_resource",
            "simulacrum_pile",
            "simulacrum_friends",
        }
    }
    native_cast.update(
        spell="cure_wounds",
        caster_state="snow",
        target="hero",
        target_distance=3,
        material_item_ids=[],
    )
    _run("cast_spell", state, data, **native_cast)
    _run("end_srd_simulacrum_turn", state, data, caster_state="hero", duplicate_state="snow")
    resumed = state[f"{SRD}.combat"]
    for field in (
        "active_actor",
        "current_actor",
        "normal_action_spent",
        "action_available",
        "movement_remaining",
        "movement_spent",
        "turn_sequence",
    ):
        assert resumed.get(field) == caster_turn.get(field)
    assert _target(state)["spell_uses_remaining"]["cure_wounds"] == 1
    assert state[f"{SRD}.state"]["lasting_tick"] == lasting_tick
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="already acted"):
        _run(
            "begin_srd_simulacrum_turn",
            state,
            data,
            caster_state="hero",
            duplicate_state="snow",
            command="Dodge again",
        )
    assert state == before


@pytest.mark.parametrize("change", ["pc", "replaced", "size", "material"])
def test_unsupported_original_or_pile_rejects_atomically_without_paid_completion(change):
    state, data, params = _case()
    if change == "pc":
        params["target"] = "hero"
    elif change == "replaced":
        _target(state, "beast")["form_statistics_replaced"] = True
    else:
        snow = next(
            e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "snow"
        )
        if change == "size":
            snow["height"] += 1
        else:
            snow["metadata"]["material"] = "stone"
        _begin_cast(state, data, params)
        _run(
            "continue_srd_spell_cast",
            state,
            data,
            caster_state="hero",
            elapsed_rounds=7200,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        if change in ("pc", "replaced"):
            _run(
                "begin_srd_spell_cast",
                state,
                data,
                **(_begin(params) | {"target": params["target"]}),
            )
        else:
            _run("cast_spell_outside_combat", state, data, **params)
    assert state == before


def _prepare_dispel(state, data, params, *, slot=7):
    materials = jar._prepare(state, data, "dispel_magic")
    source = state[f"{SRD}.state"]["created_actor_records"]["snow"]
    return params | {
        "spell": "dispel_magic",
        "target": "snow",
        "target_distance": 3,
        "slot_resource": f"spell_slot_{slot}",
        "material_item_ids": materials,
        "recovery_choice": "simulacrum",
        "recovery_source_id": source["cast_id"],
    }


def test_actual_failed_dispel_spends_its_slot_then_automatic_source_check_melts_exact_body():
    state, data, params = _case()
    data["abilities"]["intelligence"] = 10
    _cast(state, data, params)
    source = deepcopy(state[f"{SRD}.state"]["created_actor_records"]["snow"])
    assert source["cast_id"] and source["source_caster_id"] == "hero"
    assert source["ongoing_duration"] is False and source["dispel_ongoing"] is True
    _run("grant_temporary_hit_points", state, data, target="beast", amount=8, accept_new=True)
    slot3 = _target(state, "hero")["resources"]["spell_slot_3"]
    failed = _run(
        "cast_spell_outside_combat", state, data, **_prepare_dispel(state, data, params, slot=3)
    )
    assert failed.result["context"]["_spell_summary_rows"][0]["value"] is False
    assert _target(state, "hero")["resources"]["spell_slot_3"] == slot3 - 1
    assert _target(state)["created_active"] is True
    _run("cast_spell_outside_combat", state, data, **_prepare_dispel(state, data, params))
    assert _target(state)["simulacrum_melted"] is True
    assert _target(state, "beast")["temp_hp"] == 8
    assert _target(state, "beast")["is_alive"] is True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="ongoing Simulacrum"):
        _run("cast_spell_outside_combat", state, data, **_prepare_dispel(state, data, params))
    assert state == before


def test_until_dispelled_body_does_not_gain_a_finite_free_dismissal_receipt():
    state, data, params = _case()
    _cast(state, data, params)
    source = state[f"{SRD}.state"]["created_actor_records"]["snow"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("dismiss_srd_spell", state, data, caster_state="hero", cast_id=source["cast_id"])
    assert state == before


def test_owner_emission_repeats_without_duplicate_cast_repair_damage_or_turn_hooks():
    from scripts.srd_simulacrum_consumers import _declare_interrupted_continuation_receipt

    text = (jar.budget.ROOT / "runtime/ruleset.yaml").read_text()
    native = yaml.load(text, Loader=yaml.CSafeLoader)
    without = deepcopy(native)
    tool = without["tools"]["continue_srd_spell_cast"]

    # Remove only this declaration's captures, retaining all existing mechanics.
    def remove_captures(effects):
        for effect in effects:
            for branch in ("on_true", "on_false"):
                if branch in effect:
                    effect[branch] = [
                        child
                        for child in effect[branch]
                        if not child.get("set", {}).get("field", "").startswith("_continued_")
                    ]
                    remove_captures(effect[branch])

    remove_captures(tool["effects"])

    def compose(value):
        parsed = yaml.load(value, Loader=yaml.CSafeLoader)
        _declare_interrupted_continuation_receipt(
            parsed["tools"]["continue_srd_spell_cast"],
            parsed["definitions"]["spell_casting"]["simulacrum"]["name"],
        )
        return yaml.dump(parsed, Dumper=yaml.CSafeDumper, sort_keys=False)

    first = compose(yaml.dump(without, Dumper=yaml.CSafeDumper, sort_keys=False))
    second = compose(first)
    assert yaml.load(first, Loader=yaml.CSafeLoader) == native
    assert yaml.load(first, Loader=yaml.CSafeLoader) == yaml.load(second, Loader=yaml.CSafeLoader)


def test_public_real_twelve_hour_cast_reopens_for_actual_dispel_and_all_retries(
    tmp_path, monkeypatch
):
    from scripts.srd_simulacrum_consumers import PUBLIC_TOOLS as REPAIR_TOOLS
    from scripts.srd_simulacrum_turns import PUBLIC_TOOLS as TURN_TOOLS

    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from sylvia_gm.domain.package_library import canonical_json_bytes
    from tests import test_srd_plant_growth as growth
    from tests.test_srd_npc_spell_casting import _act

    monkeypatch.setattr(growth, "PUBLIC_TOOLS", REPAIR_TOOLS | TURN_TOOLS)
    growth._public_fixture(monkeypatch)
    record = deepcopy(growth.bundled_packages._srd_drafts()[0].record)
    root = tmp_path / "admission"
    record_path = root / "rulesets/srd-lite/package-record.json"
    record_path.parent.mkdir(parents=True)
    record_path.write_bytes(canonical_json_bytes(record))
    monkeypatch.setattr(jar.public_casting, "ROOT", root)

    def public_case(*_a, **_k):
        state, data, params = _case()
        _target(state, "hero")["prepared_spells"].append("dispel_magic")
        data["spellcasting_grants"][0]["spells"].append("dispel_magic")
        data["wizard_spellbook"].append("dispel_magic")
        return state, data, params

    monkeypatch.setattr(jar.public_casting, "_spell_case", public_case)
    database = tmp_path / "simulacrum.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = jar.public_casting._create_spell_campaign(owner, "simulacrum")
        initial = owner.snapshot("casting")
        began, beginning = _act(
            owner, "begin_srd_spell_cast", **(_begin(params) | {"target": "beast"})
        )
        assert began.status is ActionStatus.COMMITTED, began.result
        channel = owner.snapshot("casting")
        assert (
            channel.state[f"{SRD}.entities"]["hero"]["extended_casting"]["required_rounds"] == 7200
        )
        assert owner.execute_action(beginning) == began and owner.snapshot("casting") == channel
    with AuthorityOwner.open(database, "casting-owner") as owner:
        continued, continuation = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=7200,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert continued.status is ActionStatus.COMMITTED, continued.result
        cast, casting = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        saved = owner.snapshot("casting")
        hero = saved.state[f"{SRD}.entities"]["hero"]
        duplicate = saved.state[f"{SRD}.entities"]["snow"]
        source = saved.state[f"{SRD}.state"]["created_actor_records"]["snow"]
        assert duplicate["creature_type"] == "construct" and duplicate["max_hp"] == 10
        assert source["cast_id"] == hero["last_spell_source_cast_id"]
        assert source["source_caster_id"] == "hero"
        assert (
            hero["resources"]["spell_slot_7"]
            == initial.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] - 1
        )
        assert (
            hero["spell_materials"][0]["quantity"]
            == initial.state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["quantity"] - 1
        )
        for action, receipt in ((continuation, continued), (casting, cast)):
            assert owner.execute_action(action) == receipt and owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        dispelled, dispelling = _act(
            owner,
            "cast_spell_outside_combat",
            **(
                params
                | {
                    "spell": "dispel_magic",
                    "target": "snow",
                    "target_distance": 3,
                    "slot_resource": "spell_slot_7",
                    "material_item_ids": [],
                    "recovery_choice": "simulacrum",
                    "recovery_source_id": source["cast_id"],
                }
            ),
        )
        assert dispelled.status is ActionStatus.COMMITTED, dispelled.result
        final = owner.snapshot("casting")
        assert final.state[f"{SRD}.entities"]["snow"]["simulacrum_melted"] is True
        assert final.state[f"{SRD}.entities"]["beast"]["is_alive"] is True
        for action, receipt in (
            (dispelling, dispelled),
            (casting, cast),
            (continuation, continued),
        ):
            assert owner.execute_action(action) == receipt and owner.snapshot("casting") == final
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == final


def test_actual_ineligible_native_type_spends_committed_cast_cost_without_creating_a_body():
    state, data, params = _case(native_type="construct")
    slots = _target(state, "hero")["resources"]["spell_slot_7"]
    stock = _target(state, "hero")["spell_materials"][0]["quantity"]
    _cast(state, data, params)
    assert _target(state, "hero")["resources"]["spell_slot_7"] == slots - 1
    assert _target(state, "hero")["spell_materials"][0]["quantity"] == stock - 1
    assert "snow" not in state[f"{SRD}.entities"]


@pytest.mark.parametrize("interruption", ["range", "melted", "hp_block"])
def test_real_repair_never_erases_its_proximity_failure_or_independent_hp_prohibition(interruption):
    state, data, params = _case()
    _target(state, "hero")["spell_materials"].append(
        {
            "id": "repair",
            "inventory_item": "repair_components",
            "kind": "specific",
            "components": [],
            "quantity": 5,
            "value_cp": 10000,
        }
    )
    _target(state, "hero")["inventory"].append("repair_components")
    _cast(state, data, params)
    _run(
        "apply_srd_damage",
        state,
        data,
        target="snow",
        amount=4,
        adjustment=0,
        damage_type="force",
        source_state_id="hero",
        source_damage_kind="spell",
    )
    _run("begin_srd_rest", state, data, target_state="hero", kind="long", activity="light")
    _run(
        "begin_srd_simulacrum_repair",
        state,
        data,
        caster_state="hero",
        duplicate_state="snow",
        hit_points=3,
        material_id="repair",
    )
    if interruption == "range":
        for destination in ([-10, 0, 0], [-3, 0, 0]):
            _run(
                "move_srd_scene_creature",
                state,
                data,
                actor_state="snow",
                destination=destination,
                mode="walk",
                difficult_terrain=False,
            )
        assert _target(state, "hero")["simulacrum_repair"]["valid"] is False
    elif interruption == "melted":
        _run(
            "apply_srd_damage",
            state,
            data,
            target="snow",
            amount=20,
            adjustment=0,
            damage_type="force",
            source_state_id="hero",
            source_damage_kind="spell",
        )
    else:
        # A real independent Chill Touch producer blocks healing on this actual body.
        _run("grant_temporary_hit_points", state, data, target="snow", amount=20, accept_new=True)
        _run(
            "move_srd_scene_creature",
            state,
            data,
            actor_state="beast",
            destination=[1, 0, 0],
            mode="walk",
            difficult_terrain=False,
        )
        native_cast = {
            k: v
            for k, v in params.items()
            if k
            not in {
                "caster",
                "slot_resource",
                "simulacrum_pile",
                "simulacrum_friends",
            }
        }
        native_cast.update(
            spell="chill_touch",
            caster_state="beast",
            target="snow",
            target_distance=4,
            material_item_ids=[],
        )
        _run("cast_spell_outside_combat", state, data, **native_cast)
        assert _target(state)["hit_point_recovery_blocked"] is True
    hp = _target(state)["hp"]
    _run("advance_srd_lasting_time", state, data, elapsed_rounds=1200)
    _run("set_srd_rest_activity", state, data, target_state="hero", activity="sleep")
    _run("advance_srd_lasting_time", state, data, elapsed_rounds=3600)
    _run("complete_srd_rest", state, data, target_state="hero")
    if interruption != "hp_block":
        assert _target(state)["hp"] == hp
    else:
        # This one-turn spell has really expired during the actual eight-hour rest.
        assert _target(state)["hp"] == min(hp + 3, _target(state)["max_hp"])
    assert _target(state, "hero")["spell_materials"][-1]["quantity"] == 2
    assert state[f"{SRD}.state"]["has_simulacrum_repairs"] is False


@pytest.mark.parametrize("bad", ["identity", "owner", "level"])
def test_private_installer_rejects_nonproducer_identity_before_recasting_any_body(bad):
    state, data, params = _case()
    _cast(state, data, params)
    source = state[f"{SRD}.state"]["created_actor_records"]["snow"]
    supplied = {
        "caster_state": "hero",
        "original_state": "beast",
        "pile": "snow2",
        "slot_level": 7,
        "friends": [],
        "spell_cast_id": source["cast_id"],
        "spell_cast_owner": "hero",
        "actual_spell_level": 7,
    }
    supplied.update(
        {
            "identity": {"spell_cast_id": "invented"},
            "owner": {"spell_cast_owner": "guard"},
            "level": {"actual_spell_level": 6},
        }[bad]
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="genuine committed"):
        _run("install_srd_simulacrum", state, data, **supplied)
    assert state == before


def test_designated_actual_friend_and_no_simulacrum_recast_are_source_owned():
    state, data, params = _case()
    _cast(state, data, params | {"simulacrum_friends": ["guard"]})
    assert _target(state)["attitudes"]["guard"] == "friendly"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="cannot cast Simulacrum"):
        _run(
            "begin_srd_spell_cast",
            state,
            data,
            **(
                _begin(params)
                | {
                    "caster_state": "snow",
                    "target": "beast",
                }
            ),
        )
    assert state == before
