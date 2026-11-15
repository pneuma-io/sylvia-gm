"""Real source supplies retain quantities, holders, healing, contamination and deadlines."""

import json
from copy import deepcopy
from functools import lru_cache
from random import Random
from unittest.mock import patch

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.build_srd_5_2_1_spells import (
    SPELL_DEFINITIONS_END,
    SPELL_DEFINITIONS_START,
    _replace_generated_block,
    refresh_exploration_runtime,
)
from scripts.srd_full.srd_spell_supplies import PROFILES, PUBLIC_TOOLS, SOURCE_PAGES, extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_timed_senses import _replace_tools

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_packages import _archive, _srd_drafts
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import PackageRuntimeError, materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, MechanicContext, owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from tests import test_srd_spell_object_content as objects
from tests import test_srd_spell_result_cards as spell_cards
from tests.srd_full.test_srd_npc_spell_casting import _act, _prepare_mage

SRD = objects.SRD


@lru_cache
def _runtime_bytes():
    text = extend_runtime((objects.ROOT / "runtime/ruleset.yaml").read_text())
    runtime = _load_runtime(text)
    definitions = runtime["definitions"]["spells"] | PROFILES
    text = _replace_generated_block(
        text,
        SPELL_DEFINITIONS_START,
        SPELL_DEFINITIONS_END,
        [
            "    " + spell + ": " + json.dumps(profile)
            for spell, profile in sorted(definitions.items())
        ],
    )
    changed = {}
    for name, tool in _load_runtime(text)["tools"].items():
        for p in tool["parameters"]:
            if p["name"] == "spell" and "enum_values" in p:
                p["enum_values"] = sorted(set(p["enum_values"]) | set(PROFILES))
                changed[name] = tool
    return refresh_exploration_runtime(_replace_tools(text, changed)).encode()


@lru_cache
def _mechanic(name):
    runtime = _load_runtime(_runtime_bytes().decode())
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.1.0",
        package_sha256="a" * 64,
        runtime_bytes=_runtime_bytes(),
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


def _run(name, state, **parameters):
    result = _mechanic(name)(
        MechanicContext(
            campaign_id="supply",
            turn_id="turn",
            action_id=name,
            state=owned_payload(state),
            parameters=owned_payload(parameters),
            participants=owned_payload({}),
        )
    )
    for mutation in result.mutations:
        state[f"{mutation.namespace}.{mutation.key}"] = deepcopy(mutation.value)
    return result.result["context"]


def _row(state, identity):
    return next(
        e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == identity
    )


def _berries(state):
    _run(
        "record_srd_hand_sized_form",
        state,
        caster_state="hero",
        form_id="ten-berries",
        fiction="Ten berries fitting the synthetic caster's hand",
        kind="trinket",
        extent=[0.4, 0.4, 0.3],
    )
    _run(
        "cast_spell_supply_spell",
        state,
        caster_state="hero",
        spell="goodberry",
        slot_level=1,
        supply_output_id="berries",
        supply_hand_form_id="ten-berries",
    )


def test_supply_source_pages_and_fixed_quantities():
    corpus = json.loads((objects.ROOT / "content/corpus/spells.json").read_text())
    pages = {p["page"]: p["text_sha256"] for p in corpus["page_records"]}
    assert {p: pages[p] for p in SOURCE_PAGES} == SOURCE_PAGES
    assert PROFILES["goodberry"]["berries"] == 10
    assert PROFILES["goodberry"]["healing_per_berry"] == 1
    assert PROFILES["purify_food_and_drink"]["radius_feet"] == 5


def test_repeat_supply_composition_preserves_one_dispatch_and_lifetime_callback():
    parent = (objects.ROOT / "runtime/ruleset.yaml").read_text()
    once = _load_runtime(extend_runtime(parent))["tools"]
    twice = _load_runtime(extend_runtime(extend_runtime(parent)))["tools"]
    for name in ("cast_spell", "process_srd_turn_effects", "refresh_srd_object_content_one"):
        assert twice[name] == once[name]


def test_exact_berry_retirement_preserves_consumed_hp_and_nourishment():
    state = objects._state()
    _berries(state)
    state[f"{SRD}.entities"]["hero"].update(hp=1, max_hp=12)
    _run("eat_srd_goodberry", state, actor_state="hero", object_id="berries")
    actor = deepcopy(state[f"{SRD}.entities"]["hero"])
    batch = _row(state, "berries")["metadata"]["srd_goodberry_batch"]
    before = deepcopy(state)
    with pytest.raises((PackageRuntimeError, RulesetPreconditionRejected)):
        _run("retire_srd_supply_source", state, object_id="berries", source_id="wrong-source")
    assert state == before
    source = _run(
        "read_srd_supply_source", state, object_id="berries", source_id=batch["source_id"]
    )
    assert source["_supply_source"]["spell_level"] == 1
    _run("retire_srd_supply_source", state, object_id="berries", source_id=batch["source_id"])
    assert _row(state, "berries")["active"] is False
    assert state[f"{SRD}.entities"]["hero"] == actor


def test_two_actual_goodberry_casts_keep_distinct_saved_batch_sources():
    state = objects._state()
    _berries(state)
    first = deepcopy(_row(state, "berries")["metadata"]["srd_goodberry_batch"])
    _run(
        "cast_spell_supply_spell",
        state,
        caster_state="hero",
        spell="goodberry",
        slot_level=1,
        supply_output_id="second-berries",
        supply_hand_form_id="ten-berries",
    )
    second = deepcopy(_row(state, "second-berries")["metadata"]["srd_goodberry_batch"])
    assert first["source_id"] == "hero:goodberry:1"
    assert second["source_id"] == "hero:goodberry:2"
    restored = json.loads(json.dumps(state))
    _run("retire_srd_supply_source", restored, object_id="berries", source_id=first["source_id"])
    assert _row(restored, "berries")["active"] is False
    assert _row(restored, "second-berries")["metadata"]["srd_goodberry_batch"] == second
    assert _row(restored, "second-berries")["active"] is True


def test_ten_real_berries_transfer_keep_source_and_restore_one_actual_hp():
    state = objects._state()
    _berries(state)
    batch = _row(state, "berries")["metadata"]["srd_goodberry_batch"]
    assert batch["remaining"] == 10 and batch["expires_tick"] == 28800
    _run(
        "transfer_srd_goodberries",
        state,
        actor_state="hero",
        recipient_state="guard",
        object_id="berries",
    )
    assert _row(state, "berries")["metadata"]["carried_by"] == "guard"
    assert _row(state, "berries")["metadata"]["srd_goodberry_batch"] == batch
    state[f"{SRD}.entities"]["guard"].update(hp=1, max_hp=12)
    _run("eat_srd_goodberry", state, actor_state="guard", object_id="berries")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 2
    assert _row(state, "berries")["metadata"]["srd_goodberry_batch"]["remaining"] == 9
    nourishment = state[f"{SRD}.entities"]["guard"]["srd_goodberry_nourishment"]
    assert nourishment["through_tick"] == 28800 and nourishment["source_id"] == batch["source_id"]


def test_goodberry_dead_consumer_and_expiry_cannot_consume_or_revive():
    state = objects._state()
    _berries(state)
    state[f"{SRD}.entities"]["hero"].update(is_alive=False, hp=0)
    before = deepcopy(state)
    with pytest.raises((PackageRuntimeError, RulesetPreconditionRejected)):
        _run("eat_srd_goodberry", state, actor_state="hero", object_id="berries")
    assert state == before
    state[f"{SRD}.state"]["lasting_tick"] = 28800
    _run("refresh_srd_goodberries", state)
    assert _row(state, "berries")["active"] is False


def test_purify_cleans_actual_nonmagical_sphere_supply_and_preserves_other_objects():
    state = objects._state()
    food = objects._object(state)
    food.update(height=0.5, footprint={"kind": "box", "half_extents": [0.4, 0.4]})
    for sid, position in (("outside", [10, 0, 0]), ("magical", [3, 1, 0])):
        row = deepcopy(food)
        row.update(id=sid, position=position)
        state[f"{SRD}.state"]["spatial_scene"]["entities"].append(row)
    for sid, nonmagical in (("door", True), ("outside", True), ("magical", False)):
        _run(
            "record_srd_food_and_drink",
            state,
            object_id=sid,
            food=True,
            drink=False,
            nonmagical=nonmagical,
            poisoned=True,
            rotten=True,
        )
    _run(
        "cast_spell_supply_spell",
        state,
        caster_state="hero",
        spell="purify_food_and_drink",
        slot_level=1,
        supply_point=[3, 0, 0],
    )
    assert _row(state, "door")["metadata"]["srd_food_and_drink"]["poisoned"] is False
    assert _row(state, "door")["metadata"]["srd_food_and_drink"]["rotten"] is False
    for sid in ("outside", "magical"):
        assert _row(state, sid)["metadata"]["srd_food_and_drink"]["poisoned"] is True


def test_partial_sphere_supply_rejects_instead_of_purifying_unaffected_portions():
    state = objects._state()
    objects._object(state).update(
        position=[7.8, 0, 0], height=1, footprint={"kind": "box", "half_extents": [1, 1]}
    )
    _run(
        "record_srd_food_and_drink",
        state,
        object_id="door",
        food=True,
        drink=True,
        nonmagical=True,
        poisoned=True,
        rotten=True,
    )
    before = deepcopy(state)
    with pytest.raises((PackageRuntimeError, RulesetPreconditionRejected)):
        _run(
            "cast_spell_supply_spell",
            state,
            caster_state="hero",
            spell="purify_food_and_drink",
            slot_level=1,
            supply_point=[3, 0, 0],
        )
    assert state == before


def test_purify_removes_real_created_food_rot_without_repeating_the_original_spoil_event():
    state = objects._state()
    _run(
        "cast_spell_object_content_spell",
        state,
        caster_state="hero",
        object_id="",
        spell="create_food_and_water",
        slot_level=3,
        object_output_id="food",
        object_point=[3, 0, 0],
        object_extent=[1, 1, 1],
    )
    state[f"{SRD}.state"]["lasting_tick"] = 28800
    _run("refresh_srd_object_content_one", state, object_id="food")
    assert _row(state, "food")["metadata"]["srd_created_supply"]["spoiled_food_pounds"] == 45
    _run(
        "cast_spell_supply_spell",
        state,
        caster_state="hero",
        spell="purify_food_and_drink",
        slot_level=1,
        supply_point=[3, 0, 0],
    )
    food = _row(state, "food")["metadata"]["srd_created_supply"]
    assert food["food_pounds"] == 45 and food["spoiled_food_pounds"] == 0
    _run("refresh_srd_object_content_one", state, object_id="food")
    assert _row(state, "food")["metadata"]["srd_created_supply"] == food


@lru_cache
def _test_archive():
    """Declare this test-local public closure over the admitted immutable package."""
    primary, reference = _srd_drafts()
    record = deepcopy(primary.record)
    runtime = _load_runtime(_runtime_bytes().decode())
    existing = {t["name"] for t in record["tools"]}
    for name in existing | PUBLIC_TOOLS:
        if name not in runtime["tools"]:
            continue
        schema = _tool_schema(runtime["tools"][name], tool_name=name)
        if name in existing:
            next(t for t in record["tools"] if t["name"] == name)["input_schema"] = schema
        else:
            record["tools"].append(
                {
                    "name": name,
                    "capability": "dnd.srd.5.2.1.spells",
                    "input_schema": schema,
                    "writers": ["dm"],
                    "turn_statuses": ["open"],
                    "state_predicates": [],
                }
            )
    payload = dict(primary.payloads) | {"runtime/ruleset.yaml": _runtime_bytes()}
    content = json.loads(payload["content/content-declarations.json"])
    content["inclusions"].append(
        {
            "id": "synthetic-supply-fixture",
            "kind": "ruleset_runtime",
            "family": "synthetic-supply-fixture",
            "disposition": "included_mechanical",
            "classifications": ["sylvia_gm_implementation"],
            "sources": [],
            "links": {
                "artifacts": ["runtime/ruleset.yaml"],
                "capabilities": ["dnd.srd.5.2.1.spells"],
                "mechanics": [],
                "procedures": [],
                "prompt_fragments": [],
                "schemas": [],
                "state_namespaces": [],
                "tools": sorted(PUBLIC_TOOLS),
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["public-supply-use-and-retry"]},
        }
    )
    payload["content/content-declarations.json"] = canonical_json_bytes(content)
    return _archive((NormalizedPackageDraft(record, payload, primary.format_version), reference))


def _create_supply_campaign(owner, monkeypatch):
    definitions = spell_cards._spell_definitions() | PROFILES
    source_state = objects._state

    def supply_scene():
        state = source_state()
        objects._object(state).update(
            height=0.5, footprint={"kind": "box", "half_extents": [0.4, 0.4]}
        )
        return state

    with (
        patch.object(objects, "_test_archive", _test_archive),
        patch.object(objects, "_state", supply_scene),
        patch.object(spell_cards, "_spell_definitions", lambda: definitions),
    ):
        return objects._create_object_campaign(owner, monkeypatch)


def test_paid_public_goodberry_transfer_bonus_action_and_reopen_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "goodberry.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _create_supply_campaign(owner, monkeypatch)
        receipt, _ = _prepare_mage(
            owner,
            [{"spell": "goodberry", "uses_per_day": 1}],
            inventory=["mistletoe"],
            spell_materials=[
                {
                    "id": "mistletoe",
                    "inventory_item": "mistletoe",
                    "kind": "specific",
                    "components": ["goodberry.1"],
                    "quantity": 1,
                    "value_cp": 0,
                }
            ],
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        classified, _ = _act(
            owner,
            "record_srd_hand_sized_form",
            caster_state="mage",
            form_id="berries",
            fiction="Ten berries fitting this caster's hand",
            kind="trinket",
            extent=[0.4, 0.4, 0.3],
        )
        assert classified.status is ActionStatus.COMMITTED, classified.result
        cast, cast_command = _act(
            owner,
            "cast_spell_outside_combat",
            caster_state="mage",
            spell="goodberry",
            supply_output_id="berries",
            supply_hand_form_id="berries",
            material_item_ids=["mistletoe"],
            material_component_available=True,
            verbal_component_available=True,
            somatic_component_available=True,
        )
        assert cast.status is ActionStatus.COMMITTED, cast.result
        assert cast.result["result_card"]["title"] == "Goodberry"
        assert (
            owner.snapshot("casting").state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"][
                "goodberry"
            ]
            == 0
        )
        started, _ = _act(
            owner,
            "start_combat_encounter_entities",
            first_actor="mage",
            second_actor="guard",
            first_surprised=False,
            second_surprised=False,
            tie_winner="first",
        )
        assert started.status is ActionStatus.COMMITTED, started.result
        begun, _ = _act(owner, "begin_combat_turn")
        assert begun.status is ActionStatus.COMMITTED, begun.result
        assert owner.snapshot("casting").state[f"{SRD}.combat"]["active_actor"] == "mage"
        eaten, eat_command = _act(
            owner, "eat_srd_goodberry", actor_state="mage", object_id="berries"
        )
        assert eaten.status is ActionStatus.COMMITTED, eaten.result
        after_eat = owner.snapshot("casting")
        assert after_eat.state[f"{SRD}.entities"]["mage"]["bonus_action_available"] is False
        assert after_eat.state[f"{SRD}.combat"]["action_available"] is True
        assert _row(after_eat.state, "berries")["metadata"]["srd_goodberry_batch"]["remaining"] == 9
        transferred, _ = _act(
            owner,
            "transfer_srd_goodberries",
            actor_state="mage",
            recipient_state="guard",
            object_id="berries",
        )
        assert transferred.status is ActionStatus.COMMITTED, transferred.result
        saved = owner.snapshot("casting")
        assert (
            _row(saved.state, "berries")["metadata"]["srd_goodberry_batch"]["source_id"]
            == "mage:goodberry:1"
        )
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting") == saved
        assert reopened.execute_action(cast_command) == cast
        assert reopened.execute_action(eat_command) == eaten
        assert reopened.snapshot("casting") == saved
        for _ in range(3):
            if reopened.snapshot("casting").state[f"{SRD}.combat"]["active_actor"] == "guard":
                break
            ended, _ = _act(reopened, "end_combat_turn")
            assert ended.status is ActionStatus.COMMITTED, ended.result
            advanced, _ = _act(reopened, "advance_combat_encounter_turn")
            assert advanced.status is ActionStatus.COMMITTED, advanced.result
            if (
                reopened.snapshot("casting").state[f"{SRD}.combat"]["phase"]
                == "awaiting_turn_start"
            ):
                begun, _ = _act(reopened, "begin_combat_turn")
                assert begun.status is ActionStatus.COMMITTED, begun.result
        guard_before = reopened.snapshot("casting").state[f"{SRD}.entities"]["guard"]["hp"]
        guard_eat, command = _act(
            reopened, "eat_srd_goodberry", actor_state="guard", object_id="berries"
        )
        assert guard_eat.status is ActionStatus.COMMITTED, guard_eat.result
        final = reopened.snapshot("casting")
        assert final.state[f"{SRD}.entities"]["guard"]["hp"] == guard_before + 1
        assert _row(final.state, "berries")["metadata"]["srd_goodberry_batch"]["remaining"] == 8
        assert reopened.execute_action(command) == guard_eat
        assert reopened.snapshot("casting") == final


def test_paid_public_purify_real_supplies_reopens_without_repaying(tmp_path, monkeypatch):
    database = tmp_path / "purify.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _create_supply_campaign(owner, monkeypatch)
        prepared, _ = _prepare_mage(owner, [{"spell": "purify_food_and_drink", "uses_per_day": 1}])
        assert prepared.status is ActionStatus.COMMITTED, prepared.result
        classified, _ = _act(
            owner,
            "record_srd_food_and_drink",
            object_id="door",
            food=True,
            drink=True,
            nonmagical=True,
            poisoned=True,
            rotten=True,
        )
        assert classified.status is ActionStatus.COMMITTED, classified.result
        purified, command = _act(
            owner,
            "cast_spell_outside_combat",
            caster_state="mage",
            spell="purify_food_and_drink",
            supply_point=[3, 0, 0],
            verbal_component_available=True,
            somatic_component_available=True,
        )
        assert purified.status is ActionStatus.COMMITTED, purified.result
        assert purified.result["result_card"]["title"] == "Purify Food and Drink"
        saved = owner.snapshot("casting")
        food = _row(saved.state, "door")["metadata"]["srd_food_and_drink"]
        assert food["poisoned"] is False and food["rotten"] is False
        assert (
            saved.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["purify_food_and_drink"]
            == 0
        )
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting") == saved
        assert reopened.execute_action(command) == purified
        assert reopened.snapshot("casting") == saved
