"""Object spell effects cross actual geometry, access, casting costs and durable replay."""

import json
import sys
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
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
from scripts.srd_full.srd_extended_casting import extend_runtime as extend_casting
from scripts.srd_full.srd_spell_object_content import PROFILES, PUBLIC_TOOLS, SOURCE_PAGES, extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_timed_senses import _replace_tools

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_packages import _archive, _srd_drafts
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import PackageRuntimeError, materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, MechanicContext, StateValue, owned_payload
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from tests import test_srd_public_spell_casting as public_casting
from tests.srd_full.test_srd_npc_spell_casting import _act, _prepare_mage
from tests.srd_full.test_srd_scene_relations import _state as _scene_state

SRD = "wotc.srd.5.2.1"
ROOT = Path(__file__).resolve().parents[2] / "rulesets/dnd-srd-5.2.1"


@lru_cache
def _runtime_bytes():
    """Use a test-owned source closure, leaving the immutable package files unchanged."""
    text = extend_runtime((ROOT / "runtime/ruleset.yaml").read_text())
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
    runtime = _load_runtime(text)
    changed = {}
    for name, tool in runtime["tools"].items():
        for parameter in tool["parameters"]:
            if parameter["name"] == "spell" and "enum_values" in parameter:
                parameter["enum_values"] = sorted(set(parameter["enum_values"]) | set(PROFILES))
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


def _state():
    state = _scene_state()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["position"] = [3, 2, 0]
    crate = deepcopy(scene["entities"][1])
    crate.update(id="door", position=[3, 0, 0])
    crate["metadata"] = {
        "plane": "material",
        "lighting": "bright",
        "fiction": "Synthetic wooden door",
    }
    scene["entities"].append(crate)
    other = deepcopy(scene["entities"][1])
    other["id"] = "other"
    other["metadata"]["participant_ref"] = "other"
    other["position"] = [3, -2, 0]
    scene["entities"].append(other)
    state[f"{SRD}.entities"]["other"] = deepcopy(state[f"{SRD}.entities"]["guard"])
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False, pending_action=None)
    return state


def _object(state):
    return next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "door")


def _run(name, state, **parameters):
    result = _mechanic(name)(
        MechanicContext(
            campaign_id="objects",
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


def _access(state, locks=()):
    return _run(
        "record_srd_object_access",
        state,
        object_id="door",
        object_kind="door",
        closed=True,
        locks=list(locks),
        stuck=False,
        barred=False,
    )


def _cast(state, spell, **changes):
    return _run(
        "cast_spell_object_content_spell",
        state,
        **{
            "caster_state": "hero",
            "object_id": "door",
            "spell": spell,
            "slot_level": PROFILES[spell]["base_level"],
            **changes,
        },
    )


def _reject(name, state, **parameters):
    before = deepcopy(state)
    with pytest.raises((RulesetPreconditionRejected, PackageRuntimeError)):
        _run(name, state, **parameters)
    assert state == before


def test_profiles_and_headers_remain_exact_source_owned_constants():
    corpus = json.loads((ROOT / "content/corpus/spells.json").read_text())
    pages = {p["page"]: p["text_sha256"] for p in corpus["page_records"]}
    assert {p: pages[p] for p in SOURCE_PAGES} == SOURCE_PAGES
    assert PROFILES["arcane_lock"]["password_rounds"] == 10
    assert PROFILES["knock"]["suppression_rounds"] == 100
    assert PROFILES["knock"]["audible_feet"] == 300
    assert PROFILES["mending"]["maximum_break_feet"] == 1


def test_reemission_preserves_parent_guards_and_does_not_duplicate_object_consumers():
    text = _runtime_bytes().decode()
    first = _load_runtime(extend_runtime(text))
    second = _load_runtime(extend_runtime(extend_runtime(text)))
    for name in ("cast_spell", "cast_srd_recovery", "advance_srd_lasting_time"):
        assert first["tools"][name] == second["tools"][name]


def test_regeneration_keeps_created_component_failure_out_of_long_cast_start():
    emitted = extend_runtime(extend_casting(_runtime_bytes().decode()))
    runtime = _load_runtime(emitted)
    begin = runtime["tools"]["begin_srd_spell_cast_internal"]
    assert "object_component_ids" not in {p["name"] for p in begin["parameters"]}
    assert "object_component_ids" not in json.dumps(begin["effects"])
    assert "check_srd_created_object_components" in json.dumps(runtime["tools"]["cast_spell"])


@pytest.mark.parametrize("spell", ["creation", "fabricate"])
def test_public_reemitted_object_sources_resolve_once_and_reopen(tmp_path, monkeypatch, spell):
    # Use the real admitted source closure twice, including any parent reaction/loss nesting.
    emitted = extend_runtime(extend_casting(_runtime_bytes().decode())).encode()
    monkeypatch.setattr(sys.modules[__name__], "_runtime_bytes", lambda: emitted)
    _test_archive.cache_clear()
    try:
        if spell == "creation":
            test_public_creation_long_cast_then_component_failure_spends_once_and_reopens(
                tmp_path, monkeypatch
            )
        else:
            test_remaining_public_object_spells_pay_actual_sources_and_replay(
                tmp_path, monkeypatch, spell
            )
    finally:
        _test_archive.cache_clear()


def test_arcane_lock_actual_password_designation_knock_suppression_and_expiry():
    state = _state()
    _access(state)
    _cast(state, "arcane_lock", object_password="moon", object_designated_creatures=["guard"])
    _reject(
        "operate_srd_locked_object", state, actor_state="other", object_id="door", operation="open"
    )
    _run(
        "operate_srd_locked_object", state, actor_state="guard", object_id="door", operation="open"
    )
    assert _object(state)["movement"] == "passable"
    _run(
        "operate_srd_locked_object", state, actor_state="hero", object_id="door", operation="close"
    )
    # The source caster and a designated actual creature bypass only the magical lock.
    lock = _object(state)["metadata"]["srd_arcane_locks"][0]
    assert lock["caster"] == "hero"
    _run(
        "operate_srd_locked_object",
        state,
        actor_state="other",
        object_id="door",
        operation="speak_password",
        password="moon",
    )
    _run(
        "operate_srd_locked_object", state, actor_state="other", object_id="door", operation="open"
    )
    _run(
        "operate_srd_locked_object", state, actor_state="hero", object_id="door", operation="close"
    )
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    _reject(
        "operate_srd_locked_object", state, actor_state="other", object_id="door", operation="open"
    )
    _cast(state, "knock")
    assert _object(state)["metadata"]["srd_arcane_locks"][0]["suppressed_until_tick"] == 220
    assert _object(state)["metadata"]["srd_last_knock"]["origin"] == [3, 0, 0]
    _run(
        "operate_srd_locked_object", state, actor_state="other", object_id="door", operation="open"
    )
    _run(
        "operate_srd_locked_object", state, actor_state="hero", object_id="door", operation="close"
    )
    _run("advance_srd_lasting_time", state, elapsed_rounds=100)
    _reject(
        "operate_srd_locked_object", state, actor_state="other", object_id="door", operation="open"
    )


def test_knock_unlocks_only_one_mundane_lock_and_does_not_open_the_object():
    state = _state()
    _access(state, ["iron", "brass"])
    _cast(state, "knock")
    access = _object(state)["metadata"]["srd_object_access"]
    assert access["locks"] == ["brass"] and access["closed"] is True
    _reject(
        "operate_srd_locked_object", state, actor_state="hero", object_id="door", operation="open"
    )
    _cast(state, "knock")
    _run("operate_srd_locked_object", state, actor_state="hero", object_id="door", operation="open")
    assert _object(state)["movement"] == "passable"


def test_mending_repairs_one_actual_break_and_retains_magic_damage_and_other_breaks():
    state = _state()
    _object(state)["metadata"]["srd_spell_object"] = {"hp": 0, "destroyed": True}
    _object(state)["metadata"]["magic"] = "Synthetic broken enchantment"
    for break_id, extent in [("tear", 1), ("split", 2)]:
        _run(
            "record_srd_object_break",
            state,
            object_id="door",
            break_id=break_id,
            break_min=[3, 0, 0],
            break_max=[3, 0, extent],
        )
    _cast(state, "mending", object_break_id="tear")
    metadata = _object(state)["metadata"]
    assert [b["id"] for b in metadata["srd_object_breaks"]] == ["split"]
    assert metadata["srd_spell_object"] == {"hp": 0, "destroyed": True}
    assert metadata["magic"] == "Synthetic broken enchantment"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="one foot"):
        _cast(state, "mending", object_break_id="split")
    assert state == before


def test_water_uses_actual_open_capacity_and_source_upcast_gallons():
    state = _state()
    _run(
        "record_srd_object_container",
        state,
        object_id="door",
        capacity_gallons=25,
        water_gallons=3,
        is_open=True,
    )
    _cast(state, "create_or_destroy_water", slot_level=2)
    assert _object(state)["metadata"]["srd_object_container"]["water_gallons"] == 23
    _cast(
        state,
        "create_or_destroy_water",
        slot_level=2,
        object_mode="destroy_water",
        object_quantity=5,
    )
    assert _object(state)["metadata"]["srd_object_container"]["water_gallons"] == 18
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="that many gallons"):
        _cast(state, "create_or_destroy_water", object_quantity=11)
    assert state == before


def test_food_and_water_fixed_supply_consumption_and_actual_elapsed_spoilage():
    state = _state()
    _run(
        "cast_spell_object_content_spell",
        state,
        caster_state="hero",
        object_id="",
        spell="create_food_and_water",
        slot_level=3,
        object_output_id="supplies",
        object_point=[2, 0, 0],
        object_form="Bland nourishing bread",
        object_extent=[3, 3, 3],
    )

    def supplies():
        return next(
            e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "supplies"
        )

    assert supplies()["metadata"]["srd_created_supply"]["food_pounds"] == 45
    assert supplies()["metadata"]["srd_created_supply"]["water_gallons"] == 30
    _run("consume_srd_created_supply", state, object_id="supplies", food_pounds=5, water_gallons=2)
    _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    source = supplies()["metadata"]["srd_created_supply"]
    assert source["food_pounds"] == 0 and source["spoiled_food_pounds"] == 40
    assert source["water_gallons"] == 28


def test_creation_seen_mixture_shortest_source_lifetime_and_real_scene_expiry():
    state = _state()
    _run(
        "record_srd_object_material",
        state,
        object_id="door",
        material="vegetable",
        raw_stock=False,
        materials=["vegetable", "adamantine_or_mithral"],
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="has seen"):
        _cast(state, "creation", object_output_id="created", object_point=[2, 0, 0])
    assert state == before
    _run("observe_srd_object_pattern", state, caster_state="hero", object_id="door")
    _cast(
        state,
        "creation",
        object_output_id="created",
        object_point=[2, 0, 0],
        object_materials=["vegetable", "adamantine_or_mithral"],
    )

    def created():
        return next(
            e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "created"
        )

    source = created()["metadata"]["srd_created_object"]
    assert source["expires_tick"] == 20 and source["component_forbidden"] is True
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert created()["active"] is False


def test_fabricate_preserves_real_stock_and_rejects_unproficient_high_skill():
    state = _state()
    _run(
        "record_srd_object_material",
        state,
        object_id="door",
        material="stone_or_crystal",
        raw_stock=True,
    )
    extent = [0.8, 0.8, 6]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="size limit"):
        _cast(state, "fabricate", object_form="Synthetic mineral box", object_extent=extent)
    assert state == before
    # The initial synthetic box is six feet high; admit a real smaller stock section.
    _object(state)["height"] = 4
    _cast(state, "fabricate", object_form="Synthetic stone block", object_extent=[0.8, 0.8, 4])
    assert _object(state)["metadata"]["srd_object_material"]["raw_stock"] is False
    assert _object(state)["metadata"]["srd_fabricated"]["input_volume"] == pytest.approx(2.56)


def test_stone_shape_changes_actual_section_geometry_and_retains_object_hp():
    state = _state()
    _object(state)["height"] = 4
    _object(state)["metadata"]["material"] = "stone"
    _object(state)["metadata"]["srd_spell_object"] = {"hp": 3}
    _run(
        "record_srd_object_material",
        state,
        object_id="door",
        material="stone_or_crystal",
        raw_stock=False,
    )
    _cast(
        state,
        "stone_shape",
        object_form="A narrow passage",
        object_extent=[0.8, 0.8, 4],
        object_shape="passage",
    )
    assert _object(state)["movement"] == "passable" and _object(state)["visibility"] == "clear"
    assert _object(state)["metadata"]["srd_spell_object"] == {"hp": 3}


def test_actual_dispel_check_retires_exact_ongoing_source_and_reveals_older_lock(monkeypatch):
    state = _state()
    original_guard = deepcopy(state[f"{SRD}.entities"]["guard"])
    _access(state)
    _cast(state, "arcane_lock", slot_level=2)
    _cast(state, "arcane_lock", slot_level=5)
    locks = _object(state)["metadata"]["srd_arcane_locks"]
    older, latest = [record["source_id"] for record in locks]
    assert locks[1]["spell_level"] == 5 and locks[1]["dispel_ongoing"] is True
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Random(2))
    parameters = {
        "target_state": "guard",
        "caster_state": "hero",
        "spell": "dispel_magic",
        "slot_level": 3,
        "casting_modifier": 3,
        "casting_ability": "intelligence",
        "save_dc": 15,
        "concentration_save_modifier": 3,
        "target_distance": 0,
        "can_touch": False,
        "recovery_choice": "object_content",
        "recovery_item": "door",
        "recovery_source_id": latest,
    }
    failed = _run("cast_srd_recovery", state, **parameters)
    assert failed["_dispel_succeeded"] is False
    assert [record["source_id"] for record in _object(state)["metadata"]["srd_arcane_locks"]] == [
        older,
        latest,
    ]
    succeeded = _run("cast_srd_recovery", state, **(parameters | {"slot_level": 5}))
    assert succeeded["_dispel_succeeded"] is True
    assert [record["source_id"] for record in _object(state)["metadata"]["srd_arcane_locks"]] == [
        older
    ]
    assert state[f"{SRD}.entities"]["guard"] == original_guard
    _reject("retire_srd_object_spell_source", state, object_id="door", source_id=latest)


@lru_cache
def _test_archive():
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
            "id": "synthetic-object-content-fixture",
            "kind": "ruleset_runtime",
            "family": "synthetic-object-fixture",
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
                "tools": sorted(PUBLIC_TOOLS & set(runtime["tools"])),
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["synthetic-object-public-cast"]},
        }
    )
    payload["content/content-declarations.json"] = canonical_json_bytes(content)
    return _archive((NormalizedPackageDraft(record, payload, primary.format_version), reference))


def _create_object_campaign(owner, monkeypatch, *, stone=False, stock=False):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _test_archive)
    scene = _state()[f"{SRD}.state"]["spatial_scene"]
    if stone:
        door = next(row for row in scene["entities"] if row["id"] == "door")
        door["height"] = 4
        door["metadata"]["material"] = "stone"
    if stock:
        door = next(row for row in scene["entities"] if row["id"] == "door")
        door["footprint"]["half_extents"] = [5, 5]
        door["height"] = 10
    mage = deepcopy(scene["entities"][0])
    mage.update(id="mage", position=[0, 1, 0])
    mage["metadata"]["participant_ref"] = "mage"
    scene["entities"].append(mage)
    create = owner.create_campaign

    def admit_scene(command):
        initial = list(command.initial_state)
        for index, item in enumerate(initial):
            if item.namespace == SRD and item.key == "state":
                initial[index] = replace(item, value=item.value | {"spatial_scene": scene})
                break
        else:
            initial.append(StateValue(SRD, "state", {"spatial_scene": scene}))
        return create(replace(command, initial_state=tuple(initial)))

    with patch.object(owner, "create_campaign", side_effect=admit_scene):
        return public_casting._create_spell_campaign(owner, "fire_bolt")


def test_public_knock_pays_actual_npc_use_and_retains_object_on_reopen_retry(tmp_path, monkeypatch):
    database = tmp_path / "object-content.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _create_object_campaign(owner, monkeypatch)
        receipt, _ = _prepare_mage(owner, [{"spell": "knock", "uses_per_day": 1}])
        assert receipt.status is ActionStatus.COMMITTED
        receipt, _ = _act(
            owner,
            "record_srd_object_access",
            object_id="door",
            object_kind="door",
            closed=True,
            locks=["iron", "brass"],
            stuck=False,
            barred=False,
        )
        assert receipt.status is ActionStatus.COMMITTED
        receipt, command = _act(
            owner,
            "cast_spell_outside_combat",
            caster_state="mage",
            spell="knock",
            object_target_id="door",
            verbal_component_available=True,
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["knock"] == 0
        assert _object(saved.state)["metadata"]["srd_object_access"]["locks"] == ["brass"]
        assert receipt.result["result_card"]["title"] == "Knock"
        assert any(
            r["value"] == "One mundane lock unlocked; unstuck and unbarred"
            for r in receipt.result["result_card"]["summary"]
        )
        with pytest.raises(MechanicsRejected):
            _act(
                owner,
                "cast_spell_outside_combat",
                caster_state="mage",
                spell="knock",
                object_target_id="door",
                verbal_component_available=True,
                damage=999,
            )
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting") == saved
        assert reopened.execute_action(command) == receipt
        assert reopened.snapshot("casting") == saved


def test_public_arcane_lock_consumes_source_gold_then_exact_object_dispel_persists(
    tmp_path, monkeypatch
):
    database = tmp_path / "object-dispel.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _create_object_campaign(owner, monkeypatch)
        receipt, _ = _prepare_mage(
            owner,
            [
                {"spell": "arcane_lock", "uses_per_day": 1, "casting_level": 5},
                {"spell": "dispel_magic", "uses_per_day": 1, "casting_level": 5},
            ],
            inventory=["gold-dust"],
            spell_materials=[
                {
                    "id": "gold-dust",
                    "inventory_item": "gold-dust",
                    "kind": "specific",
                    "components": ["arcane_lock.1"],
                    "quantity": 1,
                    "value_cp": 2500,
                }
            ],
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        _act(
            owner,
            "record_srd_object_access",
            object_id="door",
            object_kind="door",
            closed=True,
            locks=[],
            stuck=False,
            barred=False,
        )
        locked, lock_command = _act(
            owner,
            "cast_spell_outside_combat",
            caster_state="mage",
            spell="arcane_lock",
            object_target_id="door",
            material_item_ids=["gold-dust"],
            material_component_available=True,
            verbal_component_available=True,
            somatic_component_available=True,
        )
        assert locked.status is ActionStatus.COMMITTED, locked.result
        lock_state = owner.snapshot("casting")
        source = _object(lock_state.state)["metadata"]["srd_arcane_locks"][0]
        assert source["spell_level"] == 5
        mage = lock_state.state[f"{SRD}.entities"]["mage"]
        assert mage["spell_materials"][0]["quantity"] == 0
        assert mage["spell_uses_remaining"]["arcane_lock"] == 0
        dispelled, command = _act(
            owner,
            "cast_spell_outside_combat",
            caster_state="mage",
            target="mage",
            spell="dispel_magic",
            recovery_choice="object_content",
            recovery_item="door",
            recovery_source_id=source["source_id"],
            target_distance=0,
            verbal_component_available=True,
            somatic_component_available=True,
        )
        assert dispelled.status is ActionStatus.COMMITTED, dispelled.result
        saved = owner.snapshot("casting")
        assert _object(saved.state)["metadata"]["srd_arcane_locks"] == []
        assert saved.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["dispel_magic"] == 0
        assert dispelled.result["result_card"]["title"] == "Dispel Magic"
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.execute_action(command) == dispelled
        assert reopened.execute_action(lock_command) == locked
        assert reopened.snapshot("casting") == saved


def test_public_creation_long_cast_then_component_failure_spends_once_and_reopens(
    tmp_path, monkeypatch
):
    database = tmp_path / "creation.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _create_object_campaign(owner, monkeypatch)
        prepared, _ = _prepare_mage(
            owner,
            [{"spell": "creation", "uses_per_day": 1}, {"spell": "darkvision", "uses_per_day": 1}],
            inventory=["brush", "carrot"],
            spell_materials=[
                {
                    "id": "brush",
                    "inventory_item": "brush",
                    "kind": "specific",
                    "components": ["creation.1"],
                    "quantity": 1,
                    "value_cp": 0,
                },
                {
                    "id": "carrot",
                    "inventory_item": "carrot",
                    "kind": "specific",
                    "components": ["darkvision.1"],
                    "quantity": 1,
                    "value_cp": 0,
                },
            ],
        )
        assert prepared.status is ActionStatus.COMMITTED
        _act(
            owner,
            "record_srd_object_material",
            object_id="door",
            material="vegetable",
            raw_stock=False,
        )
        _act(owner, "observe_srd_object_pattern", caster_state="mage", object_id="door")
        begin, _ = _act(
            owner,
            "begin_srd_spell_cast",
            caster_state="mage",
            spell="creation",
            material_item_ids=["brush"],
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert begin.status is ActionStatus.COMMITTED
        progress, _ = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="mage",
            elapsed_rounds=10,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert progress.status is ActionStatus.COMMITTED
        created, creation_command = _act(
            owner,
            "cast_spell_outside_combat",
            caster_state="mage",
            spell="creation",
            object_target_id="door",
            object_output_id="created",
            object_point=[2, 1, 0],
            material_item_ids=["brush"],
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert created.status is ActionStatus.COMMITTED, created.result
        after_creation = owner.snapshot("casting")
        mage = after_creation.state[f"{SRD}.entities"]["mage"]
        assert mage["spell_uses_remaining"]["creation"] == 0 and mage["extended_casting"] is None
        failed, failure_command = _act(
            owner,
            "cast_spell_outside_combat",
            caster_state="mage",
            target="mage",
            spell="darkvision",
            object_component_ids=["created"],
            material_item_ids=["carrot"],
            caster_can_touch_target=True,
            target_distance=0,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert failed.status is ActionStatus.COMMITTED, failed.result
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["darkvision"] == 0
        assert "Creation material used as a spell component; casting costs spent" in str(
            failed.result["result_card"]
        )
        assert not any(
            r.get("spell") == "darkvision"
            for r in saved.state[f"{SRD}.state"].get("turn_effects", [])
        )
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.execute_action(creation_command) == created
        assert reopened.execute_action(failure_command) == failed
        assert reopened.snapshot("casting") == saved


@pytest.mark.parametrize(
    "spell",
    ["mending", "create_food_and_water", "create_or_destroy_water", "fabricate", "stone_shape"],
)
def test_remaining_public_object_spells_pay_actual_sources_and_replay(tmp_path, monkeypatch, spell):
    database = tmp_path / (spell + ".db")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _create_object_campaign(owner, monkeypatch, stone=spell == "stone_shape")
        headers = _load_runtime(_runtime_bytes().decode())["definitions"]["spell_casting"][spell]
        materials = [
            {
                "id": part["id"],
                "inventory_item": part["id"],
                "kind": "specific",
                "components": [part["id"]],
                "quantity": part["quantity"],
                "value_cp": part["minimum_value_cp"],
            }
            for part in headers["material_parts"]
        ]
        prepared, _ = _prepare_mage(
            owner,
            [{"spell": spell, "uses_per_day": 0 if spell == "mending" else 1}],
            inventory=[item["id"] for item in materials],
            spell_materials=materials,
        )
        assert prepared.status is ActionStatus.COMMITTED
        parameters = {
            "caster_state": "mage",
            "spell": spell,
            "material_item_ids": [item["id"] for item in materials],
            "verbal_component_available": True,
            "somatic_component_available": True,
            "material_component_available": True,
        }
        choices = {"object_target_id": "door"}
        if spell == "mending":
            _act(
                owner,
                "record_srd_object_break",
                object_id="door",
                break_id="tear",
                break_min=[2.7, -0.2, 0],
                break_max=[3.3, 0.2, 1],
            )
            choices["object_break_id"] = "tear"
        elif spell == "create_food_and_water":
            choices = {
                "object_output_id": "supply",
                "object_point": [2, 1, 0],
                "object_form": "Nourishing bread",
            }
        elif spell == "create_or_destroy_water":
            _act(
                owner,
                "record_srd_object_container",
                object_id="door",
                capacity_gallons=25,
                water_gallons=0,
                is_open=True,
            )
        else:
            _act(
                owner,
                "record_srd_object_material",
                object_id="door",
                material="stone_or_crystal" if spell == "stone_shape" else "vegetable",
                raw_stock=spell == "fabricate",
            )
            choices.update(
                object_extent=[0.8, 0.8, 4 if spell == "stone_shape" else 6],
                object_form="Simple coffer",
            )
        if PROFILES[spell]["casting_time"] != "action":
            started, _ = _act(owner, "begin_srd_spell_cast", **parameters)
            assert started.status is ActionStatus.COMMITTED
            continued, _ = _act(
                owner,
                "continue_srd_spell_cast",
                caster_state="mage",
                elapsed_rounds=10 if spell == "mending" else 100,
                uninterrupted=True,
                verbal_component_available=True,
                somatic_component_available=True,
                material_component_available=True,
            )
            assert continued.status is ActionStatus.COMMITTED
        receipt, command = _act(owner, "cast_spell_outside_combat", **parameters, **choices)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"][spell] == 0
        assert receipt.result["result_card"]["title"] == headers["name"]
        if spell == "mending":
            assert _object(saved.state)["metadata"]["srd_object_breaks"] == []
        elif spell == "create_food_and_water":
            supply = next(
                row
                for row in saved.state[f"{SRD}.state"]["spatial_scene"]["entities"]
                if row["id"] == "supply"
            )
            assert supply["metadata"]["srd_created_supply"]["food_pounds"] == 45
        elif spell == "create_or_destroy_water":
            assert _object(saved.state)["metadata"]["srd_object_container"]["water_gallons"] == 10
        elif spell == "fabricate":
            assert _object(saved.state)["metadata"]["srd_object_material"]["raw_stock"] is False
        else:
            assert _object(saved.state)["metadata"]["srd_stone_shape"]["shape"] == "solid"
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.execute_action(command) == receipt
        assert reopened.snapshot("casting") == saved


def test_source_rain_cube_extinguishes_only_actual_exposed_same_plane_flames():
    state = _state()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    door = _object(state)
    door.update(position=[10, 0, 0], height=1)
    door["footprint"]["half_extents"] = [0.4, 0.4]
    for identity, position, plane in [
        ("covered", [12, 0, 0], "material"),
        ("outside", [-5, 0, 0], "material"),
        ("other_plane", [14, 0, 0], "ethereal"),
    ]:
        row = deepcopy(door)
        row.update(id=identity, position=position)
        row["metadata"]["plane"] = plane
        scene["entities"].append(row)
    for identity in ["door", "covered", "outside", "other_plane"]:
        _run("record_srd_ordinary_flame", state, object_id=identity, exposed=identity != "covered")
    _cast(
        state,
        "create_or_destroy_water",
        object_id="",
        slot_level=3,
        object_mode="rain",
        object_point=[0, 0, 0],
        object_cube_face="east",
    )
    rows = {r["id"]: r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"]}
    assert rows["door"]["metadata"]["srd_ordinary_flame"]["burning"] is False
    assert all(
        rows[i]["metadata"]["srd_ordinary_flame"]["burning"] is True
        for i in ["covered", "outside", "other_plane"]
    )
    record = state[f"{SRD}.state"]["turn_effects"][-1]
    assert record["volume"] == {"kind": "box", "min": [0, -20, -20], "max": [40, 20, 20]}
    assert record["slot_level"] == 3
    assert record["dispel_ongoing"] is False
    assert record["source_sha256"] == SOURCE_PAGES[121]
    _reject("record_srd_ordinary_flame", state, object_id="door", exposed=True)


def test_created_supply_fills_real_container_capacity_then_preserves_ground_remainder():
    state = _state()
    _run(
        "record_srd_object_container",
        state,
        object_id="door",
        capacity_gallons=20,
        water_gallons=5,
        is_open=True,
        food_capacity_pounds=20,
        food_pounds=3,
    )
    _cast(
        state,
        "create_food_and_water",
        object_container_ids=["door"],
        object_id="",
        object_output_id="remaining",
        object_point=[2, 1, 0],
    )
    container_supply = _object(state)["metadata"]["srd_created_supply"]
    remainder = next(
        row
        for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if row["id"] == "remaining"
    )["metadata"]["srd_created_supply"]
    assert (container_supply["food_pounds"], container_supply["water_gallons"]) == (17, 15)
    assert (remainder["food_pounds"], remainder["water_gallons"]) == (28, 15)
    _run("consume_srd_created_supply", state, object_id="door", food_pounds=7, water_gallons=5)
    _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    current = _object(state)["metadata"]["srd_created_supply"]
    assert current["food_pounds"] == 0 and current["spoiled_food_pounds"] == 10
    assert current["water_gallons"] == 10
    assert _object(state)["metadata"]["srd_object_container"]["water_gallons"] == 5


def test_partial_flame_and_unowned_fog_reject_without_casting_fake_success():
    state = _state()
    _object(state).update(position=[0, 0, 0], height=1)
    _run("record_srd_ordinary_flame", state, object_id="door", exposed=True)
    for mode in ["rain", "destroy_fog"]:
        _reject(
            "cast_spell_object_content_spell",
            state,
            caster_state="hero",
            object_id="",
            spell="create_or_destroy_water",
            slot_level=1,
            object_mode=mode,
            object_point=[0, 0, 0],
            object_cube_face="east",
        )


def test_created_water_shares_real_vessel_capacity_and_destroy_consumes_both_sources():
    state = _state()
    _run(
        "record_srd_object_container",
        state,
        object_id="door",
        capacity_gallons=30,
        water_gallons=5,
        is_open=True,
        food_capacity_pounds=45,
    )
    _cast(
        state,
        "create_food_and_water",
        object_id="door",
        object_output_id="overflow",
        object_point=[2, 1, 0],
    )
    assert _object(state)["metadata"]["srd_created_supply"]["water_gallons"] == 25
    _cast(state, "create_or_destroy_water")
    assert _object(state)["metadata"]["srd_object_container"]["water_gallons"] == 5
    _cast(state, "create_or_destroy_water", slot_level=3, object_mode="destroy_water")
    assert _object(state)["metadata"]["srd_created_supply"]["water_gallons"] == 0
    assert _object(state)["metadata"]["srd_object_container"]["water_gallons"] == 0


def test_actual_held_object_geometry_and_source_invisibility_are_consumed_by_knock():
    state = _state()
    _access(state, locks=["iron"])
    _object(state)["metadata"]["carried_by"] = "guard"
    guard = next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "guard"
    )
    guard["position"] = [20, 0, 0]
    state[f"{SRD}.entities"]["guard"]["conditions"] = ["invisible"]
    _reject(
        "cast_spell_object_content_spell",
        state,
        caster_state="hero",
        object_id="door",
        spell="knock",
        slot_level=2,
    )
    assert _object(state)["position"] == [3, 0, 0]
    state[f"{SRD}.entities"]["hero"]["truesight_feet"] = 60
    _cast(state, "knock")
    assert _object(state)["position"] == [20, 0, 0]
    assert _object(state)["metadata"]["srd_object_access"]["locks"] == []


def test_creation_retains_authentic_seen_pattern_after_original_leaves_the_scene():
    state = _state()
    _run(
        "record_srd_object_material", state, object_id="door", material="vegetable", raw_stock=False
    )
    _run("observe_srd_object_pattern", state, caster_state="hero", object_id="door")
    _object(state)["active"] = False
    _cast(state, "creation", object_output_id="remembered", object_point=[2, 1, 0])
    created = next(
        row
        for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if row["id"] == "remembered"
    )
    assert created["metadata"]["fiction"] == "Synthetic wooden door"
    assert created["movement"] == "blocking" and created["visibility"] == "opaque"
    assert created["metadata"]["srd_created_object"]["material"] == "vegetable"


def test_public_source_rain_retains_paid_cube_and_extinguished_flame_on_reopen_retry(
    tmp_path, monkeypatch
):
    database = tmp_path / "rain.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _create_object_campaign(owner, monkeypatch)
        prepared, _ = _prepare_mage(
            owner,
            [{"spell": "create_or_destroy_water", "uses_per_day": 1, "casting_level": 3}],
            inventory=["mix"],
            spell_materials=[
                {
                    "id": "mix",
                    "inventory_item": "mix",
                    "kind": "specific",
                    "components": ["create_or_destroy_water.1"],
                    "quantity": 1,
                    "value_cp": 0,
                }
            ],
        )
        assert prepared.status is ActionStatus.COMMITTED
        admitted, _ = _act(owner, "record_srd_ordinary_flame", object_id="door", exposed=True)
        assert admitted.status is ActionStatus.COMMITTED
        receipt, command = _act(
            owner,
            "cast_spell_outside_combat",
            caster_state="mage",
            spell="create_or_destroy_water",
            object_mode="rain",
            object_point=[0, 0, 0],
            object_cube_face="east",
            material_item_ids=["mix"],
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        assert receipt.result["result_card"]["title"] == "Create or Destroy Water"
        saved = owner.snapshot("casting")
        assert (
            saved.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"][
                "create_or_destroy_water"
            ]
            == 0
        )
        assert _object(saved.state)["metadata"]["srd_ordinary_flame"]["burning"] is False
        cube = next(
            r
            for r in saved.state[f"{SRD}.state"]["turn_effects"]
            if r.get("spell") == "create_or_destroy_water"
        )
        assert cube["slot_level"] == 3 and cube["volume"]["max"][0] == 40
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.execute_action(command) == receipt
        assert reopened.snapshot("casting").state == saved.state


def test_partial_fabricate_retains_actual_unconsumed_stock_and_instant_output():
    state = _state()
    stock = _object(state)
    stock["footprint"]["half_extents"] = [1, 1]
    stock["height"] = 4
    _run(
        "record_srd_object_material", state, object_id="door", material="vegetable", raw_stock=True
    )
    _cast(
        state,
        "fabricate",
        object_extent=[2, 2, 2],
        object_output_id="coffer",
        object_point=[10, 0, 0],
        object_form="Plain wooden coffer",
    )
    remaining = _object(state)
    assert remaining["height"] == 2 and remaining["position"] == [3, 0, 2]
    assert remaining["metadata"]["srd_object_material"]["raw_stock"] is True
    output = next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "coffer"
    )
    assert output["height"] == 2 and output["position"] == [10, 0, 0]
    assert output["metadata"]["srd_object_material"]["raw_stock"] is False
    assert output["metadata"]["srd_fabricated"]["input_volume"] == 8
    assert output["metadata"]["srd_fabricated"]["dispel_ongoing"] is False


def test_eight_actual_connected_fabricate_cells_consume_exact_stock_without_new_volume():
    state = _state()
    stock = _object(state)
    stock["footprint"]["half_extents"] = [5, 5]
    stock["height"] = 10
    _run(
        "record_srd_object_material", state, object_id="door", material="vegetable", raw_stock=True
    )
    cells = [[10 + index * 5, 0, 0] for index in range(8)]
    _reject(
        "cast_spell_object_content_spell",
        state,
        caster_state="hero",
        object_id="door",
        spell="fabricate",
        slot_level=4,
        object_fabricate_layout="connected_cubes",
        object_cells=[[10, 0, 0], [20, 0, 0]],
        object_output_id="bad",
        object_form="Disconnected",
    )
    _cast(
        state,
        "fabricate",
        object_fabricate_layout="connected_cubes",
        object_cells=cells,
        object_output_id="timbers",
        object_form="Long joined timber form",
    )
    assert _object(state)["active"] is False
    rows = [
        row
        for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if row["id"] == "timbers" or row["id"].startswith("timbers:section:")
    ]
    assert len(rows) == 8 and [row["position"] for row in rows] == cells
    assert all(
        row["footprint"]["half_extents"] == [2.5, 2.5] and row["height"] == 5 for row in rows
    )
    assert all(row["metadata"]["srd_object_material"]["raw_stock"] is False for row in rows)
    assert sum(row["metadata"]["srd_fabricated"]["output_volume"] for row in rows) == 1000
    assert all(row["metadata"]["srd_fabricated"]["output_group_id"] == "timbers" for row in rows)
    assert all(row["movement"] == "blocking" and row["visibility"] == "opaque" for row in rows)
    assert state[f"{SRD}.state"]["spatial_scene"]["map"]["revision"] == 10


def test_public_eight_connected_fabricate_cells_pay_long_cast_and_reopen_retry(
    tmp_path, monkeypatch
):
    database = tmp_path / "connected-fabricate.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _create_object_campaign(owner, monkeypatch, stock=True)
        prepared, _ = _prepare_mage(owner, [{"spell": "fabricate", "uses_per_day": 1}])
        assert prepared.status is ActionStatus.COMMITTED
        admitted, _ = _act(
            owner,
            "record_srd_object_material",
            object_id="door",
            material="vegetable",
            raw_stock=True,
        )
        assert admitted.status is ActionStatus.COMMITTED
        started, _ = _act(
            owner,
            "begin_srd_spell_cast",
            caster_state="mage",
            spell="fabricate",
            verbal_component_available=True,
            somatic_component_available=True,
        )
        assert started.status is ActionStatus.COMMITTED
        continued, _ = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="mage",
            elapsed_rounds=100,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
        )
        assert continued.status is ActionStatus.COMMITTED
        receipt, command = _act(
            owner,
            "cast_spell_outside_combat",
            caster_state="mage",
            spell="fabricate",
            object_target_id="door",
            object_fabricate_layout="connected_cubes",
            object_cells=[[10 + i * 5, 0, 0] for i in range(8)],
            object_output_id="timbers",
            object_form="Joined timber form",
            verbal_component_available=True,
            somatic_component_available=True,
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["fabricate"] == 0
        assert saved.state[f"{SRD}.entities"]["mage"]["extended_casting"] is None
        assert _object(saved.state)["active"] is False
        assert (
            len(
                [
                    r
                    for r in saved.state[f"{SRD}.state"]["spatial_scene"]["entities"]
                    if r["id"] == "timbers" or r["id"].startswith("timbers:section:")
                ]
            )
            == 8
        )
        assert receipt.result["result_card"]["title"] == "Fabricate"
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.execute_action(command) == receipt
        assert reopened.snapshot("casting").state == saved.state


def test_rain_consumes_current_carried_flame_origin_before_source_cube_membership():
    state = _state()
    flame = _object(state)
    flame.update(position=[10, 0, 0], height=1)
    _run("record_srd_ordinary_flame", state, object_id="door", exposed=True)
    flame = _object(state)
    flame["metadata"]["carried_by"] = "guard"
    guard = next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "guard"
    )
    guard["position"] = [50, 0, 0]
    _cast(
        state,
        "create_or_destroy_water",
        object_id="",
        object_mode="rain",
        object_point=[0, 0, 0],
        object_cube_face="east",
    )
    assert _object(state)["position"] == [50, 0, 0]
    assert _object(state)["metadata"]["srd_ordinary_flame"]["burning"] is True
