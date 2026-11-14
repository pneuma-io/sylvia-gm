"""Heat Metal uses actual held metal, Fire defenses, later turns and earned D20 penalties."""

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
from scripts.srd_full.srd_spell_damage_consumers import extend_runtime as extend_damage
from scripts.srd_full.srd_spell_heat_metal import (
    PROFILES,
    PUBLIC_TOOLS,
    SOURCE_PAGES,
    bind_d20_runtime,
    extend_runtime,
)
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_timed_senses import _replace_tools

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_packages import _archive, _srd_drafts
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, MechanicContext, owned_payload
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from tests import test_srd_spell_object_content as objects
from tests import test_srd_spell_object_utilities as utilities
from tests import test_srd_spell_result_cards as spell_cards
from tests import test_srd_spell_supplies as supplies
from tests.srd_full.test_srd_npc_spell_casting import _act, _prepare_mage
from tests.srd_full.test_srd_spell_attack_public import _RecordedRandom

SRD = objects.SRD


@lru_cache
def _runtime_bytes():
    text = bind_d20_runtime(extend_damage(extend_runtime(utilities._runtime_bytes().decode())))
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
        package_sha256="c" * 64,
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
            campaign_id="heated-metal",
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
    state = utilities._state()
    target = state[f"{SRD}.entities"]["guard"]
    target.update(
        hp=1000,
        max_hp=1000,
        temp_hp=0,
        inventory=["metal-item", "other-item"],
        held_items=["metal-item", "other-item"],
    )
    target["saving_throw_modifiers"]["constitution"] = -100
    return state


def _admit(state, held_as="worn"):
    _run(
        "record_srd_object_utility_facts",
        state,
        object_id="door",
        weight_pounds=7,
        manufactured=True,
        metal=True,
        object_kind="armor" if held_as == "worn" else "weapon",
        inventory_item="metal-item",
    )
    _run("record_srd_object_holder", state, actor_state="guard", object_id="door", held_as=held_as)


def _cast(state, level=2):
    result = _run(
        "cast_spell_heat_metal_spell",
        state,
        caster_state="hero",
        slot_level=level,
        save_dc=15,
        concentration_modifier=6,
        object_id="door",
    )
    source = next(
        r for r in state[f"{SRD}.state"]["turn_effects"] if r["kind"] == "heat_metal_spell"
    )
    return result, source


def _d20(state, kind):
    return _run(
        "d20_test",
        state,
        actor_state="guard",
        kind=kind,
        ability="strength",
        ability_modifier=0,
        proficiency_bonus=0,
        proficiency_factor="none",
        circumstantial_modifier=0,
        target_number=15,
        advantage=False,
        disadvantage=False,
        forego_save=False,
    )


def test_heat_source_pages_and_fixed_damage_scaling():
    corpus = json.loads((objects.ROOT / "content/corpus/spells.json").read_text())
    pages = {p["page"]: p["text_sha256"] for p in corpus["page_records"]}
    assert {p: pages[p] for p in SOURCE_PAGES} == SOURCE_PAGES
    assert PROFILES["heat_metal"]["duration_rounds"] == 10
    assert PROFILES["heat_metal"]["base_dice_count"] == 2


def test_actual_fire_immunity_prevents_grip_save_and_penalty(monkeypatch):
    rng = _RecordedRandom(0)
    monkeypatch.setattr(dice, "_rng", rng)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    state = _state()
    state[f"{SRD}.entities"]["guard"]["damage_immunities"] = ["fire"]
    _admit(state, "carried")
    _cast(state)
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] == 1000
    assert guard.get("srd_heat_metal_grips") is None
    assert supplies._row(state, "door")["metadata"]["carried_by"] == "guard"
    assert [draw for draw in rng.draws if draw[:2] == (1, 20)] == []
    assert len([draw for draw in rng.draws if draw[:2] == (1, 8)]) == 2


def test_failed_real_save_drops_only_heated_held_item_and_preserves_other_inventory(monkeypatch):
    monkeypatch.setattr(dice, "_rng", _RecordedRandom(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    state = _state()
    _admit(state, "carried")
    _cast(state, 3)
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] < 1000
    assert guard["held_items"] == ["other-item"]
    assert guard["inventory"] == ["other-item"]
    assert guard["dropped_items"] == ["metal-item"]
    assert supplies._row(state, "door")["metadata"]["carried_by"] is None
    assert guard.get("srd_heat_metal_grips") is None


def test_worn_metal_fire_against_temp_hp_earns_real_check_penalty_until_expiry(monkeypatch):
    monkeypatch.setattr(dice, "_rng", _RecordedRandom(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    state = _state()
    state[f"{SRD}.entities"]["guard"]["temp_hp"] = 1000
    _admit(state)
    _cast(state)
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] == 1000 and guard["temp_hp"] < 1000
    assert supplies._row(state, "door")["metadata"]["worn_by"] == "guard"
    assert len(guard["srd_heat_metal_grips"]) == 1
    check = _d20(state, "ability_check")
    assert check["_effective_disadvantage"] is True
    assert len(check["_d20_test"]["results"]) == 2
    save = _d20(state, "saving_throw")
    assert save["_effective_disadvantage"] is False
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _d20(state, "ability_check")["_effective_disadvantage"] is False


def test_exact_concentration_cleanup_and_deadline_retire_source_without_fake_object_conditions(
    monkeypatch,
):
    monkeypatch.setattr(dice, "_rng", _RecordedRandom(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    state = _state()
    _admit(state)
    _, source = _cast(state)
    before = deepcopy(supplies._row(state, "door"))
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert source not in state[f"{SRD}.state"]["turn_effects"]
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    assert supplies._row(state, "door") == before
    assert "conditions" not in before["metadata"]


def test_exact_object_source_callback_preserves_earned_penalty_and_other_scene(monkeypatch):
    monkeypatch.setattr(dice, "_rng", _RecordedRandom(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    state = _state()
    _admit(state)
    _, source = _cast(state)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Select the actual heated object"):
        _run(
            "read_srd_heat_metal_source",
            state,
            object_id="different-object",
            source_id=source["source_id"],
        )
    assert state == before
    read = _run(
        "read_srd_heat_metal_source",
        state,
        object_id="door",
        source_id=source["source_id"],
    )
    assert read["_heat_metal_source"] == source
    _run(
        "end_srd_heat_metal_source",
        state,
        caster_state="hero",
        source_id=source["source_id"],
    )
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    assert source not in state[f"{SRD}.state"]["turn_effects"]
    assert supplies._row(state, "door") == supplies._row(before, "door")
    assert _d20(state, "ability_check")["_effective_disadvantage"] is True


def test_heat_emitter_and_d20_bind_repeat_preserve_final_composition():
    text = utilities._runtime_bytes().decode()
    once = bind_d20_runtime(extend_runtime(text))
    twice = bind_d20_runtime(extend_runtime(once))
    assert _load_runtime(twice) == _load_runtime(once)


def test_independent_heat_casters_keep_exact_sources_and_actual_damage_details(monkeypatch):
    monkeypatch.setattr(dice, "_rng", _RecordedRandom(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    state = _state()
    _admit(state)
    result, first = _cast(state)
    assert first["source_id"] == "hero:heat_metal:1"
    assert isinstance(result["_heat_details"][0]["value"], int)
    assert result["_heat_details"][1]["value"] == result["_heat_roll"]["results"]
    _run(
        "cast_spell_heat_metal_spell",
        state,
        caster_state="other",
        slot_level=2,
        save_dc=15,
        concentration_modifier=6,
        object_id="door",
    )
    records = [r for r in state[f"{SRD}.state"]["turn_effects"] if r["kind"] == "heat_metal_spell"]
    assert [r["source_id"] for r in records] == ["hero:heat_metal:1", "other:heat_metal:1"]
    second = deepcopy(records[1])
    restored = json.loads(json.dumps(state))
    _run("end_srd_heat_metal_source", restored, caster_state="hero", source_id=first["source_id"])
    read = _run(
        "read_srd_heat_metal_source", restored, object_id="door", source_id=second["source_id"]
    )
    assert read["_heat_metal_source"] == second
    assert restored[f"{SRD}.entities"]["other"]["concentration_spell_id"] == "heat_metal"


@lru_cache
def _test_archive():
    primary, reference = _srd_drafts()
    record = deepcopy(primary.record)
    runtime = _load_runtime(_runtime_bytes().decode())
    existing = {tool["name"] for tool in record["tools"]}
    public = PUBLIC_TOOLS | utilities.PUBLIC_TOOLS | supplies.PUBLIC_TOOLS
    for name in existing | public:
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
    added = sorted(public - existing)
    content["inclusions"].append(
        {
            "id": "synthetic-heat-metal-fixture",
            "kind": "ruleset_runtime",
            "family": "synthetic-heat-metal-fixture",
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
                "tools": added,
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["public-heat-metal-turn-and-retry"]},
        }
    )
    payload["content/content-declarations.json"] = canonical_json_bytes(content)
    return _archive((NormalizedPackageDraft(record, payload, primary.format_version), reference))


def _campaign(owner, monkeypatch):
    scene_state = utilities._state

    def with_wearer():
        state = scene_state()
        wearer = deepcopy(state[f"{SRD}.state"]["spatial_scene"]["entities"][1])
        wearer.update(id="wearer", position=[3, 2, 0])
        wearer["metadata"]["participant_ref"] = "wearer"
        state[f"{SRD}.state"]["spatial_scene"]["entities"].append(wearer)
        return state

    definitions = spell_cards._spell_definitions() | utilities.PROFILES | PROFILES
    with (
        patch.object(objects, "_state", with_wearer),
        patch.object(objects, "_test_archive", _test_archive),
        patch.object(spell_cards, "_spell_definitions", lambda: definitions),
    ):
        return objects._create_object_campaign(owner, monkeypatch)


def test_paid_public_heat_later_bonus_action_uses_actual_retained_metal_and_reopens(
    tmp_path, monkeypatch
):
    rng = _RecordedRandom(0)
    monkeypatch.setattr(dice, "_rng", rng)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    database = tmp_path / "heat.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _campaign(owner, monkeypatch)
        mage, _ = _prepare_mage(
            owner,
            [{"spell": "heat_metal", "uses_per_day": 1, "casting_level": 3}],
            inventory=["heat-component"],
            spell_materials=[
                {
                    "id": "heat-component",
                    "inventory_item": "heat-component",
                    "kind": "specific",
                    "components": ["heat_metal.1"],
                    "quantity": 1,
                    "value_cp": 0,
                }
            ],
        )
        assert mage.status is ActionStatus.COMMITTED, mage.result
        wearer, _ = _act(
            owner,
            "prepare_srd_creature",
            creature_id="wearer",
            name="Metal Wearer",
            source="Synthetic stat block: AC12,1000HP, CON-10, manufactured metal armor.",
            armor_class=12,
            hit_points=1000,
            speed=30,
            initiative_modifier=-10,
            size_rank=2,
            constitution_save=-10,
            inventory=["metal-armor"],
        )
        assert wearer.status is ActionStatus.COMMITTED, wearer.result
        fact, _ = _act(
            owner,
            "record_srd_object_utility_facts",
            object_id="door",
            weight_pounds=7,
            manufactured=True,
            metal=True,
            object_kind="armor",
            inventory_item="metal-armor",
        )
        assert fact.status is ActionStatus.COMMITTED, fact.result
        held, _ = _act(
            owner,
            "record_srd_object_holder",
            actor_state="wearer",
            object_id="door",
            held_as="worn",
        )
        assert held.status is ActionStatus.COMMITTED, held.result
        started, _ = _act(
            owner,
            "start_combat_encounter_entities",
            first_actor="mage",
            second_actor="wearer",
            first_surprised=False,
            second_surprised=False,
            tie_winner="first",
        )
        assert started.status is ActionStatus.COMMITTED, started.result
        begun, _ = _act(owner, "begin_combat_turn")
        assert begun.status is ActionStatus.COMMITTED, begun.result
        cast, cast_command = _act(
            owner,
            "cast_spell",
            caster_state="mage",
            spell="heat_metal",
            heated_object_id="door",
            material_item_ids=["heat-component"],
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert cast.status is ActionStatus.COMMITTED, cast.result
        saved = owner.snapshot("casting")
        source = next(
            r
            for r in saved.state[f"{SRD}.state"]["turn_effects"]
            if r["kind"] == "heat_metal_spell"
        )
        assert source["spell_level"] == 3 and source["save_dc"] == 15
        assert source["source_id"] == "mage:heat_metal:1"
        details = {r["label"]: r["value"] for r in cast.result["result_card"]["details"]}
        assert isinstance(details["Heat Metal"], (int, float))
        assert len(details["Actual dice"]) == 3
        assert saved.state[f"{SRD}.combat"]["action_available"] is False
        assert saved.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["heat_metal"] == 0
        assert supplies._row(saved.state, "door")["metadata"]["worn_by"] == "wearer"
        with pytest.raises(MechanicsRejected):
            _act(
                owner,
                "reheat_srd_metal",
                caster_state="mage",
                source_id=source["source_id"],
                damage=999,
            )
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting") == saved
        draw_count = len(rng.draws)
        assert reopened.execute_action(cast_command) == cast
        assert len(rng.draws) == draw_count
        for _ in range(2):
            ended, _ = _act(reopened, "end_combat_turn")
            assert ended.status is ActionStatus.COMMITTED, ended.result
            advanced, _ = _act(reopened, "advance_combat_encounter_turn")
            assert advanced.status is ActionStatus.COMMITTED, advanced.result
            begun, _ = _act(reopened, "begin_combat_turn")
            assert begun.status is ActionStatus.COMMITTED, begun.result
        assert reopened.snapshot("casting").state[f"{SRD}.combat"]["active_actor"] == "mage"
        before = reopened.snapshot("casting")
        heated, command = _act(
            reopened, "reheat_srd_metal", caster_state="mage", source_id=source["source_id"]
        )
        assert heated.status is ActionStatus.COMMITTED, heated.result
        final = reopened.snapshot("casting")
        assert (
            final.state[f"{SRD}.entities"]["wearer"]["hp"]
            < before.state[f"{SRD}.entities"]["wearer"]["hp"]
        )
        assert final.state[f"{SRD}.entities"]["mage"]["bonus_action_available"] is False
        assert final.state[f"{SRD}.combat"]["action_available"] is True
        assert final.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["heat_metal"] == 0
        draw_count = len(rng.draws)
        assert reopened.execute_action(command) == heated
        assert len(rng.draws) == draw_count
        assert reopened.snapshot("casting") == final
