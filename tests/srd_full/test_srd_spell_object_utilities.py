"""Object utilities select actual mass, holders, sapphire lots and canonical positions."""

import json
from copy import deepcopy
from functools import lru_cache
from unittest.mock import patch

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.build_srd_5_2_1_spells import (
    SPELL_DEFINITIONS_END,
    SPELL_DEFINITIONS_START,
    _replace_generated_block,
    refresh_exploration_runtime,
)
from scripts.srd_full.srd_spell_object_utilities import PROFILES, PUBLIC_TOOLS, SOURCE_PAGES, extend_runtime
from scripts.srd_full.srd_spell_travel import bind_runtime_consumers
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_timed_senses import _replace_tools

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_packages import _archive, _srd_drafts
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import PackageRuntimeError, materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, MechanicContext, owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from tests import test_srd_spell_object_content as objects
from tests import test_srd_spell_result_cards as spell_cards
from tests import test_srd_spell_supplies as supplies
from tests.srd_full.test_srd_npc_spell_casting import _act, _prepare_mage

SRD = objects.SRD
_SOURCE_STATE = objects._state


@lru_cache
def _runtime_bytes():
    text = bind_runtime_consumers(extend_runtime(supplies._runtime_bytes().decode()))
    definitions = _load_runtime(text)["definitions"]["spells"] | PROFILES
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
        package_sha256="b" * 64,
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
            campaign_id="utilities",
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


def _state():
    state = _SOURCE_STATE()
    row = objects._object(state)
    row.update(height=0.5, footprint={"kind": "box", "half_extents": [0.4, 0.4]})
    gem = deepcopy(row)
    gem.update(
        id="gem",
        position=[2, 0, 0],
        height=0.2,
        footprint={"kind": "box", "half_extents": [0.1, 0.1]},
    )
    gem["metadata"]["fiction"] = "Synthetic sapphire"
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(gem)
    return state


def _materials():
    return [
        {
            "id": "gem-lot",
            "inventory_item": "gem-token",
            "kind": "specific",
            "components": ["instant_summons.1"],
            "quantity": 1,
            "value_cp": 100000,
        }
    ]


def _admit(state, caster="hero"):
    state[f"{SRD}.entities"][caster].update(inventory=["gem-token"], spell_materials=_materials())
    for object_id, weight, kind, item in (
        ("door", 7, "object", ""),
        ("gem", 0.2, "sapphire", "gem-token"),
    ):
        _run(
            "record_srd_object_utility_facts",
            state,
            object_id=object_id,
            weight_pounds=weight,
            manufactured=True,
            metal=False,
            object_kind=kind,
            inventory_item=item,
        )
    _run("record_srd_object_holder", state, actor_state=caster, object_id="gem", held_as="carried")
    _run(
        "record_srd_spell_material_object",
        state,
        caster_state=caster,
        object_id="gem",
        material_item_id="gem-lot",
    )


def _mark(state, caster="hero"):
    _run(
        "cast_spell_object_utility_spell",
        state,
        caster_state=caster,
        spell="instant_summons",
        slot_level=6,
        utility_object_id="door",
        utility_sapphire_id="gem",
        material_item_ids=["gem-lot"],
    )
    return next(
        r for r in state[f"{SRD}.state"]["turn_effects"] if r["kind"] == "object_utility_spell"
    )


def test_object_utility_source_pages_and_fixed_instant_limits():
    corpus = json.loads((objects.ROOT / "content/corpus/spells.json").read_text())
    pages = {page["page"]: page["text_sha256"] for page in corpus["page_records"]}
    assert {page: pages[page] for page in SOURCE_PAGES} == SOURCE_PAGES
    assert PROFILES["instant_summons"]["casting_time"] == "1_minute"
    assert PROFILES["instant_summons"]["maximum_weight_pounds"] == 10
    assert PROFILES["instant_summons"]["sapphire_minimum_value_cp"] == 100000


def test_repeat_utility_dispatch_preserves_shared_cast_and_sole_relocation():
    parent = supplies._runtime_bytes().decode()
    once = _load_runtime(extend_runtime(parent))["tools"]
    twice = _load_runtime(extend_runtime(extend_runtime(parent)))["tools"]
    assert once["cast_spell"] == twice["cast_spell"]
    assert once["move_srd_travel_subject"] == twice["move_srd_travel_subject"]


def test_selected_instant_source_is_removed_only_on_its_actual_sapphire():
    state = _state()
    _admit(state)
    source = _mark(state)
    before = deepcopy(state)
    with pytest.raises((PackageRuntimeError, RulesetPreconditionRejected)):
        _run(
            "read_srd_object_utility_source", state, object_id="door", source_id=source["source_id"]
        )
    assert state == before
    read = _run(
        "read_srd_object_utility_source", state, object_id="gem", source_id=source["source_id"]
    )
    assert read["_object_utility_source"]["spell_level"] == 6
    _run(
        "retire_srd_object_utility_source",
        state,
        caster_state="hero",
        source_id=source["source_id"],
    )
    assert supplies._row(state, "gem")["active"] is True
    assert state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["quantity"] == 1
    assert source not in state[f"{SRD}.state"]["turn_effects"]


def test_two_actual_instant_casts_keep_distinct_sapphire_sources_after_saved_state_restore():
    state = _state()
    second_gem = deepcopy(supplies._row(state, "gem"))
    second_gem["id"] = "second-gem"
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(second_gem)
    _admit(state)
    caster = state[f"{SRD}.entities"]["hero"]
    caster["inventory"].append("second-gem-token")
    lot = deepcopy(_materials()[0])
    lot.update(id="second-gem-lot", inventory_item="second-gem-token")
    caster["spell_materials"].append(lot)
    _run(
        "record_srd_object_utility_facts",
        state,
        object_id="second-gem",
        weight_pounds=0.2,
        manufactured=True,
        metal=False,
        object_kind="sapphire",
        inventory_item="second-gem-token",
    )
    _run(
        "record_srd_object_holder",
        state,
        actor_state="hero",
        object_id="second-gem",
        held_as="carried",
    )
    _run(
        "record_srd_spell_material_object",
        state,
        caster_state="hero",
        object_id="second-gem",
        material_item_id="second-gem-lot",
    )
    first = deepcopy(_mark(state))
    _run(
        "cast_spell_object_utility_spell",
        state,
        caster_state="hero",
        spell="instant_summons",
        slot_level=6,
        utility_object_id="door",
        utility_sapphire_id="second-gem",
        material_item_ids=["second-gem-lot"],
    )
    records = [
        r for r in state[f"{SRD}.state"]["turn_effects"] if r["kind"] == "object_utility_spell"
    ]
    assert [r["source_id"] for r in records] == ["hero:instant_summons:1", "hero:instant_summons:2"]
    second = deepcopy(records[1])
    restored = json.loads(json.dumps(state))
    _run(
        "retire_srd_object_utility_source",
        restored,
        caster_state="hero",
        source_id=first["source_id"],
    )
    assert first not in restored[f"{SRD}.state"]["turn_effects"]
    read = _run(
        "read_srd_object_utility_source",
        restored,
        object_id="second-gem",
        source_id=second["source_id"],
    )
    assert read["_object_utility_source"] == second
    assert (
        supplies._row(restored, "second-gem")["metadata"]["srd_used_instant_summons"]
        == second["source_id"]
    )


def test_repreparation_cannot_change_real_mass_or_sapphire_source_identity():
    state = _state()
    _admit(state)
    before = deepcopy(state)
    with pytest.raises((PackageRuntimeError, RulesetPreconditionRejected)):
        _run(
            "record_srd_object_utility_facts",
            state,
            object_id="door",
            weight_pounds=0,
            manufactured=False,
            metal=True,
            object_kind="weapon",
            inventory_item="",
        )
    assert state == before
    record = _mark(state)
    after = deepcopy(state)
    with pytest.raises((PackageRuntimeError, RulesetPreconditionRejected)):
        _mark(state)
    assert state == after
    assert (
        supplies._row(state, "gem")["metadata"]["srd_used_instant_summons"] == record["source_id"]
    )


def test_actual_sapphire_crush_recalls_across_planes_through_sole_object_relocation():
    state = _state()
    _admit(state)
    source = _mark(state)
    _run(
        "move_srd_travel_subject",
        state,
        subject="door",
        destination=[50, 0, 0],
        plane="ethereal",
        teleport=True,
    )
    _run("use_srd_instant_summons", state, caster_state="hero", source_id=source["source_id"])
    recalled = supplies._row(state, "door")
    assert recalled["position"] == [0, 0, 0]
    assert recalled["metadata"]["plane"] == "material"
    assert recalled["metadata"]["carried_by"] == "hero"
    assert supplies._row(state, "gem")["active"] is False
    assert state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["quantity"] == 0
    assert "gem-token" not in state[f"{SRD}.entities"]["hero"]["inventory"]
    assert source not in state[f"{SRD}.state"]["turn_effects"]


def test_an_actual_other_holder_is_identified_at_current_planar_position_without_recall():
    state = _state()
    _admit(state)
    source = _mark(state)
    _run(
        "record_srd_object_holder", state, actor_state="guard", object_id="door", held_as="carried"
    )
    _run(
        "move_srd_travel_subject",
        state,
        subject="guard",
        destination=[75, 4, 0],
        plane="astral",
        teleport=True,
    )
    result = _run(
        "use_srd_instant_summons", state, caster_state="hero", source_id=source["source_id"]
    )
    assert result["_utility_other_holder"] is True
    assert result["_utility_holder_name"] == state[f"{SRD}.entities"]["guard"]["name"]
    assert result["_utility_holder_position"] == [75, 4, 0]
    assert result["_utility_holder_plane"] == "astral"
    assert supplies._row(state, "door")["metadata"]["carried_by"] == "guard"
    assert supplies._row(state, "door")["position"] == [75, 4, 0]
    assert supplies._row(state, "gem")["active"] is False


@lru_cache
def _test_archive():
    primary, reference = _srd_drafts()
    record = deepcopy(primary.record)
    runtime = _load_runtime(_runtime_bytes().decode())
    existing = {tool["name"] for tool in record["tools"]}
    new_public = PUBLIC_TOOLS | supplies.PUBLIC_TOOLS
    for name in existing | new_public:
        if name not in runtime["tools"]:
            continue
        schema = _tool_schema(runtime["tools"][name], tool_name=name)
        if name in existing:
            next(tool for tool in record["tools"] if tool["name"] == name)["input_schema"] = schema
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
    for artifact in (
        "content/help-action.source.json",
        "content/unarmed-strike.source.json",
        "content/weapon-catalog.source.json",
    ):
        payload[artifact] = (objects.ROOT / artifact).read_bytes()
    content = json.loads(payload["content/content-declarations.json"])
    content["inclusions"].append(
        {
            "id": "synthetic-object-utility-fixture",
            "kind": "ruleset_runtime",
            "family": "synthetic-object-utility-fixture",
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
                "tools": sorted(new_public),
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["public-object-utility-use-and-retry"]},
        }
    )
    payload["content/content-declarations.json"] = canonical_json_bytes(content)
    return _archive((NormalizedPackageDraft(record, payload, primary.format_version), reference))


def _campaign(owner, monkeypatch):
    definitions = spell_cards._spell_definitions() | supplies.PROFILES | PROFILES
    with (
        patch.object(objects, "_state", _state),
        patch.object(objects, "_test_archive", _test_archive),
        patch.object(spell_cards, "_spell_definitions", lambda: definitions),
    ):
        return objects._create_object_campaign(owner, monkeypatch)


def test_paid_public_instant_summons_long_cast_reopen_crush_and_retry(tmp_path, monkeypatch):
    database = tmp_path / "instant.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _campaign(owner, monkeypatch)
        prepared, _ = _prepare_mage(
            owner,
            [{"spell": "instant_summons", "uses_per_day": 1}],
            inventory=["gem-token"],
            spell_materials=_materials(),
        )
        assert prepared.status is ActionStatus.COMMITTED, prepared.result
        for object_id, weight, kind, item in (
            ("door", 7, "object", ""),
            ("gem", 0.2, "sapphire", "gem-token"),
        ):
            fact, _ = _act(
                owner,
                "record_srd_object_utility_facts",
                object_id=object_id,
                weight_pounds=weight,
                manufactured=True,
                metal=False,
                object_kind=kind,
                inventory_item=item,
            )
            assert fact.status is ActionStatus.COMMITTED, fact.result
        held, _ = _act(
            owner,
            "record_srd_object_holder",
            actor_state="mage",
            object_id="gem",
            held_as="carried",
        )
        assert held.status is ActionStatus.COMMITTED, held.result
        linked, _ = _act(
            owner,
            "record_srd_spell_material_object",
            caster_state="mage",
            object_id="gem",
            material_item_id="gem-lot",
        )
        assert linked.status is ActionStatus.COMMITTED, linked.result
        cast_args = {
            "caster_state": "mage",
            "spell": "instant_summons",
            "material_item_ids": ["gem-lot"],
            "verbal_component_available": True,
            "somatic_component_available": True,
            "material_component_available": True,
        }
        begun, _ = _act(owner, "begin_srd_spell_cast", **cast_args)
        assert begun.status is ActionStatus.COMMITTED, begun.result
        progressed, _ = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="mage",
            elapsed_rounds=10,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert progressed.status is ActionStatus.COMMITTED, progressed.result
        cast, cast_command = _act(
            owner,
            "cast_spell_outside_combat",
            **cast_args,
            utility_object_id="door",
            utility_sapphire_id="gem",
        )
        assert cast.status is ActionStatus.COMMITTED, cast.result
        assert cast.result["result_card"]["title"] == "Instant Summons"
        saved = owner.snapshot("casting")
        source = next(
            r
            for r in saved.state[f"{SRD}.state"]["turn_effects"]
            if r["kind"] == "object_utility_spell"
        )
        assert source["source_id"] == "mage:instant_summons:1"
        assert (
            saved.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["instant_summons"] == 0
        )
        assert saved.state[f"{SRD}.entities"]["mage"]["spell_materials"][0]["quantity"] == 1
        assert saved.state[f"{SRD}.state"]["lasting_tick"] == 20
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting") == saved
        assert reopened.execute_action(cast_command) == cast
        started, _ = _act(
            reopened,
            "start_combat_encounter_entities",
            first_actor="mage",
            second_actor="guard",
            first_surprised=False,
            second_surprised=False,
            tie_winner="first",
        )
        assert started.status is ActionStatus.COMMITTED, started.result
        begun, _ = _act(reopened, "begin_combat_turn")
        assert begun.status is ActionStatus.COMMITTED, begun.result
        used, use_command = _act(
            reopened, "use_srd_instant_summons", caster_state="mage", source_id=source["source_id"]
        )
        assert used.status is ActionStatus.COMMITTED, used.result
        final = reopened.snapshot("casting")
        assert supplies._row(final.state, "gem")["active"] is False
        assert supplies._row(final.state, "door")["metadata"]["carried_by"] == "mage"
        assert final.state[f"{SRD}.entities"]["mage"]["spell_materials"][0]["quantity"] == 0
        assert final.state[f"{SRD}.combat"]["action_available"] is False
        assert reopened.execute_action(use_command) == used
        assert reopened.snapshot("casting") == final
