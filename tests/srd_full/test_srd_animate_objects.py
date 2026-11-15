"""Paid animation preserves original objects and uses real native turns and saved hits."""

import json
import sys
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_animate_objects import PROFILE, PUBLIC_TOOLS, bind_runtime_consumers, schema
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages, package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from sylvia_gm.runtime import _effect_executor
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_arcane_hand import Rolls
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_produce_flame_reactions import SOURCE as PACKAGE_SOURCE
from tests.srd_full.test_srd_spell_damage_riders import _runtime as source_runtime
from tests.srd_full.test_srd_spell_wave3_information import _case as scene_case
from tests.srd_full.test_srd_spell_wave3_information import _entity


@lru_cache
def _runtime():
    parent = source_runtime()[0]
    text = bind_runtime_consumers(yaml.dump(parent, Dumper=yaml.CSafeDumper, sort_keys=False))
    assert bind_runtime_consumers(text) == text
    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    runtime["definitions"]["spells"]["animate_objects"] = deepcopy(PROFILE)
    runtime["definition_row_schemas"]["definitions.spells"]["variants"]["animate_objects"] = (
        schema()
    )
    for name in (
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_spell_action",
        "begin_srd_spell_cast_internal",
    ):
        spell = next(p for p in runtime["tools"][name]["parameters"] if p["name"] == "spell")
        spell["enum_values"] = sorted(set(spell["enum_values"]) | {"animate_objects"})
    assert runtime["tools"]["d20_test"] == parent["tools"]["d20_test"]
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


@lru_cache
def _mechanic(name):
    runtime, encoded = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=encoded,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Rolls(10))
    original = package_runtime.execute_tool

    def diagnose(*args, **kwargs):
        result = original(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnose)


def _case(slot=5, sizes=("tiny", "large")):
    state, data, params = scene_case("animate_objects")
    combat = state[f"{SRD}.combat"]
    combat.update(encounter_active=False, turn_active=False)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"] = [r for r in scene["entities"] if r["id"] in {"hero", "guard"}]
    state[f"{SRD}.entities"]["hero"].update(
        languages=["common", "elvish"],
        initiative_modifier=(data["abilities"]["dexterity"] - 10) // 2,
        name="Hero",
    )
    state[f"{SRD}.entities"]["guard"].update(
        hp=100, max_hp=100, armor_class=1, temp_hp=0, initiative_modifier=0
    )
    for index, size in enumerate(sizes):
        object_id = f"object-{index}"
        body = _entity(object_id, [15, 3 * index, 0], is_object=True, opaque_marker="keep")
        body["height"] = 1
        state[f"{SRD}.state"]["spatial_scene"]["entities"].append(body)
        _run(
            "prepare_srd_spell_object",
            state,
            object_id=object_id,
            material="wood",
            size=size,
            durability="resilient",
        )
        _run(
            "record_srd_object_material",
            state,
            object_id=object_id,
            material="vegetable",
            raw_stock=False,
            magical=False,
        )
        _run("record_srd_object_attachment", state, object_id=object_id, fixed_to_surface=False)
    params.update(
        animated_object_ids=[f"object-{i}" for i in range(len(sizes))],
        slot_resource=f"spell_slot_{slot}",
    )
    return state, data, params


def _paid(state, data, params):
    return _run("cast_spell_outside_combat", state, participant_data=data, **params).result[
        "context"
    ]


def _actors(state):
    source_id = state[f"{SRD}.entities"]["hero"]["concentration_cast_id"]
    return state[f"{SRD}.state"]["animated_object_sources"][source_id]["actors"]


def test_paid_weighted_targets_use_printed_construct_stats_and_same_original_scene_bodies():
    state, data, params = _case()
    original = {r["id"]: deepcopy(r) for r in state[f"{SRD}.state"]["spatial_scene"]["entities"]}
    context = _paid(state, data, params)
    actors = _actors(state)
    assert len(actors) == 2 and context["_spell_summary_rows"]
    for index, actor_id in enumerate(actors):
        actor = state[f"{SRD}.entities"][actor_id]
        body = next(
            r
            for r in state[f"{SRD}.state"]["spatial_scene"]["entities"]
            if r["id"] == f"object-{index}"
        )
        assert body["position"] == original[body["id"]]["position"]
        assert body["footprint"] == original[body["id"]]["footprint"]
        assert body["metadata"]["participant_ref"] == actor_id
        assert body["metadata"]["opaque_marker"] == "keep"
        assert (
            body["metadata"]["srd_spell_object"]
            == original[body["id"]]["metadata"]["srd_spell_object"]
        )
        assert actor["creature_type"] == "construct" and actor["armor_class"] == 15
        assert actor["max_hp"] == (10 if index == 0 else 20)
        assert actor["speed"] == 30 and actor["blindsight_feet"] == 30
        assert (
            actor["passive_perception"] == 6
            and actor["proficiency_bonus"] == context["_casting_profile"]["proficiency_bonus"]
        )
        assert actor["damage_immunities"] == ["poison", "psychic"]
        assert actor["condition_immunities"] == [
            "charmed",
            "exhaustion",
            "frightened",
            "paralyzed",
            "poisoned",
        ]
        assert actor["created_understood_languages"] == ["common", "elvish"]
    assert len(state[f"{SRD}.state"]["spatial_scene"]["entities"]) == len(original)
    state = json.loads(json.dumps(state))
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=20)
    for actor_id in actors:
        assert state[f"{SRD}.entities"][actor_id]["created_active"] is False
    for body in state[f"{SRD}.state"]["spatial_scene"]["entities"]:
        if body["id"].startswith("object-"):
            assert body["metadata"]["participant_ref"] is None
            assert (
                body["metadata"]["srd_spell_object"]["hp"]
                == original[body["id"]]["metadata"]["srd_spell_object"]["hp"]
            )


def test_ineligible_attachment_or_weighted_count_rolls_back_real_payment_and_objects():
    state, data, params = _case(sizes=("large", "large", "large"))
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="limit"):
        _paid(state, data, params)
    assert state == before
    state, data, params = _case(sizes=("tiny",))
    _run("record_srd_object_attachment", state, object_id="object-0", fixed_to_surface=True)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="not fixed"):
        _paid(state, data, params)
    assert state == before


def test_actual_zero_hp_damage_carries_only_remaining_packet_into_original_object():
    state, data, params = _case(sizes=("tiny",))
    _paid(state, data, params)
    actor_id = _actors(state)[0]
    state[f"{SRD}.entities"][actor_id]["temp_hp"] = 2
    _run("apply_srd_damage", state, target=actor_id, amount=15, damage_type="fire", adjustment=0)
    body = next(
        r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "object-0"
    )
    assert body["metadata"]["participant_ref"] is None
    assert body["metadata"]["srd_spell_object"]["hp"] == 2  # 15 - 2 TempHP - 10 ConstructHP.
    assert state[f"{SRD}.entities"][actor_id]["created_active"] is False


WAVE42 = Path(str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-animate-objects/input-1"))


def _excluded_module(name, expected):
    """Borrow only immutable actual Jar/factoring sources; no changing peer source or stubs."""
    path = WAVE42 / "scripts" / (name + ".py")
    assert sha256(path.read_bytes()).hexdigest() == expected
    spec = spec_from_file_location("scripts._animate_excluded_" + name, path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache
def _jar_factored_runtime(live_astral=False):
    parent = deepcopy(source_runtime()[0])
    body = _excluded_module(
        "srd_body_soul_spells",
        "7fc681b770e3adf719f54a6a825326234528633cba92ff5bc61222ec241c814c",
    )
    with patch.dict(sys.modules, {"scripts.srd_full.srd_body_soul_spells": body}):
        jar = _excluded_module(
            "srd_magic_jar",
            "e26634d36b6ec75d1d3e1a49e4aff1a6dde2aed849cef19864ed5cc0283548be",
        )
    consumers = _excluded_module(
        "srd_magic_jar_consumers",
        "354a9161c7574fc0e45186340c7286111066b91f5f26e9fecaa3146ae0ba319a",
    )
    factor = _excluded_module(
        "srd_qualified_packet_implementations",
        "872cf573e7a28dc80bf9e3722c454f9ff36a11c2dcd1f1cb513b92ad3ff7a76b",
    )
    text = body.extend_runtime(yaml.dump(parent, Dumper=yaml.CSafeDumper, sort_keys=False))
    text = jar.extend_runtime(text)
    text = consumers.bind_consumers(text)
    runtime = _load_runtime(text)
    # Astral's production receiver preserves the death owner behind its exact form guard.
    parent_root = (
        Path(__file__).resolve().parents[2]
        if live_astral
        else Path(str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-animate-objects/input-2"))
    )
    source = parent_root / "scripts/srd_astral_projection.py"
    expected = (
        "d9f817753ae73db745d1158bb9de8b12c02ce315834e0e1a662e020a3fd49a64"
        if live_astral
        else "df1addad62edcf50f822f22b57d2c2a5e4a4fd6db6d0e17452a53e953400e768"
    )
    assert sha256(source.read_bytes()).hexdigest() == expected
    spec = spec_from_file_location("scripts.animate_astral_parent", source)
    astral = module_from_spec(spec)
    spec.loader.exec_module(astral)
    text = astral.bind_runtime_consumers(
        yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
    )
    runtime = _load_runtime(text)
    # Native267 predates Jar/Clone. Keep every native owner and the exact transitive
    # consequence dependencies it now calls; unrelated new public Jar producers are
    # outside this receiver proof. No production runtime or native owner is pruned.
    needed = set(parent["tools"])
    pending = list(needed)

    def calls(value):
        if isinstance(value, dict):
            if isinstance(value.get("call"), str):
                yield value["call"]
            for child in value.values():
                yield from calls(child)
        elif isinstance(value, list):
            for child in value:
                yield from calls(child)

    while pending:
        name = pending.pop()
        assert name in runtime["tools"], name
        for target in calls(runtime["tools"][name]["effects"]):
            if target not in needed:
                needed.add(target)
                pending.append(target)
    runtime["tools"] = {name: tool for name, tool in runtime["tools"].items() if name in needed}
    for core, owner, *_ in factor.COHORTS:
        original = runtime["tools"][owner]
        fields = factor.PRESENTATION_FIELDS - ({"_hp_lost"} if "composite" in owner else set())
        runtime["tools"][core] = factor._canonical(original, fields)
        runtime["tools"][owner] = factor._delegate(original, core, True, [])
    before = deepcopy(runtime["tools"])
    text = bind_runtime_consumers(yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False))
    assert bind_runtime_consumers(text) == text
    runtime = _load_runtime(text)
    assert (
        runtime["tools"]["apply_srd_damage"]["effects"][0]["on_true"]
        == (before["apply_srd_damage"]["effects"][0]["on_true"])
    )
    for owner in ("apply_srd_qualifying_damage", "apply_srd_qualifying_composite_damage"):
        assert runtime["tools"][owner] == before[owner]
    for owner in (
        "apply_srd_damage",
        "apply_srd_composite_damage",
        "execute_srd_qualified_scalar_packet",
        "execute_srd_qualified_composite_packet",
    ):
        actual = deepcopy(runtime["tools"][owner])

        def remove_owned(effects):
            for effect in effects[:]:
                if effect.get("condition") == (
                    "target.hp == 0 and target.animated_object_active == true"
                ):
                    effects.remove(effect)
                else:
                    for key in ("on_true", "on_false"):
                        if isinstance(effect.get(key), list):
                            remove_owned(effect[key])

        remove_owned(actual["effects"])
        assert actual == before[owner]
    runtime["definitions"]["spells"]["animate_objects"] = deepcopy(PROFILE)
    runtime["definition_row_schemas"]["definitions.spells"]["variants"]["animate_objects"] = (
        schema()
    )
    for tool in runtime["tools"].values():
        for p in tool["parameters"]:
            if p["name"] == "spell" and "foresight" in p.get("enum_values", []):
                p["enum_values"] = sorted(set(p["enum_values"]) | {"animate_objects"})
    assert runtime["tools"]["d20_test"] == before["d20_test"]
    needed = {
        "prepare_srd_spell_object",
        "record_srd_object_material",
        "record_srd_object_attachment",
        "cast_spell_outside_combat",
        "apply_srd_damage",
        "apply_srd_composite_damage",
        "apply_srd_qualifying_damage",
        "apply_srd_qualifying_composite_damage",
    }
    pending = list(needed)
    while pending:
        name = pending.pop()
        assert name in runtime["tools"], name
        for target in calls(runtime["tools"][name]["effects"]):
            if target not in needed:
                needed.add(target)
                pending.append(target)
    runtime["tools"] = {name: tool for name, tool in runtime["tools"].items() if name in needed}
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


@lru_cache
def _jar_factored_mechanic(name, live_astral=False):
    runtime, encoded = _jar_factored_runtime(live_astral)
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=encoded,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.mark.parametrize("live_astral", (False, True), ids=("wave53", "live"))
@pytest.mark.parametrize(
    "packet",
    [
        "apply_srd_damage",
        "apply_srd_qualifying_damage",
        "apply_srd_qualifying_composite_damage",
    ],
)
def test_genuine_jar_and_factored_packets_revert_once_with_exact_temp_hp_overflow(
    packet,
    live_astral,
    monkeypatch,
):
    monkeypatch.setattr(budget, "_mechanic", lambda name: _jar_factored_mechanic(name, live_astral))
    state, data, params = _case(sizes=("tiny",))
    _paid(state, data, params)
    state = json.loads(json.dumps(state))
    actor_id = _actors(state)[0]
    state[f"{SRD}.entities"][actor_id]["temp_hp"] = 2
    calls = []
    original = _effect_executor._EFFECT_HANDLERS["call"]

    def observe(body, effect, *args, **kwargs):
        if body == "revert_srd_animated_object_actor":
            calls.append(effect)
        return original(body, effect, *args, **kwargs)

    monkeypatch.setitem(_effect_executor._EFFECT_HANDLERS, "call", observe)
    values = {"target": actor_id, "amount": 15, "damage_type": "fire", "adjustment": 0}
    if "composite" in packet:
        values = {
            "target": actor_id,
            "component_1_amount": 9,
            "component_1_damage_type": "fire",
            "component_2_amount": 6,
            "component_2_damage_type": "acid",
        }
    _run(packet, state, **values)
    body = next(
        r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "object-0"
    )
    assert len(calls) == 1
    assert body["metadata"]["participant_ref"] is None
    assert body["metadata"]["srd_spell_object"]["hp"] == 2
    assert state[f"{SRD}.entities"][actor_id]["created_active"] is False
    with pytest.raises(RulesetPreconditionRejected, match="dead creature"):
        _run(packet, state, **values)
    assert len(calls) == 1
    assert (
        next(
            r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "object-0"
        )["metadata"]["srd_spell_object"]["hp"]
        == 2
    )


def test_real_after_caster_initiative_command_and_saved_native_slam_reopen():
    state, data, params = _case(slot=6, sizes=("tiny",))
    _paid(state, data, params)
    actor_id = _actors(state)[0]
    _run(
        "start_group_combat_encounter",
        state,
        participant_data=data,
        combatants=["hero", "guard", actor_id],
    )
    order = [entry["entity_id"] for entry in state[f"{SRD}.combat"]["order"]]
    assert order.index(actor_id) == order.index("hero") + 1
    _run("begin_combat_turn", state, actor="hero")
    _run(
        "command_srd_animated_objects",
        state,
        caster_state="hero",
        actor_ids=[actor_id],
        command="attack",
    )
    _run("end_combat_turn", state, actor="hero")
    _run("advance_group_combat_encounter_turn", state, completed_actor="hero")
    _run("begin_combat_turn", state, actor=actor_id)
    hit = _run(
        "begin_srd_animated_object_slam", state, actor_state=actor_id, target_state="guard"
    ).result["context"]["_pending_action_id"]
    state = json.loads(json.dumps(state))
    _run("complete_srd_pending_action", state, pending_action_id=hit)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 95  # Real 2d4 + 3 Force.
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2


def test_uncommanded_real_object_turn_dodges_and_concentration_end_restores_same_body():
    state, data, params = _case(sizes=("tiny",))
    _paid(state, data, params)
    actor_id = _actors(state)[0]
    source_id = state[f"{SRD}.entities"]["hero"]["concentration_cast_id"]
    _run(
        "start_group_combat_encounter",
        state,
        participant_data=data,
        combatants=["hero", "guard", actor_id],
    )
    _run("begin_combat_turn", state, actor="hero")
    _run("end_combat_turn", state, actor="hero")
    _run("advance_group_combat_encounter_turn", state, completed_actor="hero")
    _run("begin_combat_turn", state, actor=actor_id)
    assert state[f"{SRD}.entities"][actor_id]["dodging"] is True
    assert state[f"{SRD}.combat"]["action_taken"] == "dodge"
    with pytest.raises(RulesetPreconditionRejected, match="attack command"):
        _run("begin_srd_animated_object_slam", state, actor_state=actor_id, target_state="guard")
    _run("end_srd_concentration_node", state, source_state="hero", target_state="hero")
    assert state[f"{SRD}.state"]["animated_object_sources"][source_id]["installed"] is False
    body = next(
        r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "object-0"
    )
    assert body["metadata"]["participant_ref"] is None
    assert body["metadata"]["opaque_marker"] == "keep"
    assert body["metadata"]["srd_spell_object"]["hp"] == 5
    assert state[f"{SRD}.entities"][actor_id]["created_active"] is False


@lru_cache
def _archive():
    runtime, encoded = _runtime()
    with patch.object(bundled_packages, "_bundled_srd_root", return_value=PACKAGE_SOURCE):
        primary, reference = bundled_packages._srd_drafts()
    record = deepcopy(primary.record)
    for declaration in record["tools"]:
        if declaration["name"] in runtime["tools"]:
            declaration["input_schema"] = _tool_schema(runtime["tools"][declaration["name"]])
    for name in sorted(PUBLIC_TOOLS):
        record["tools"].append(
            {
                "name": name,
                "capability": "dnd.srd.5.2.1.spells",
                "input_schema": _tool_schema(runtime["tools"][name]),
                "writers": ["dm"],
                "turn_statuses": ["open"],
                "state_predicates": [],
            }
        )
    payloads = dict(primary.payloads) | {"runtime/ruleset.yaml": encoded}
    content = json.loads(payloads["content/content-declarations.json"])
    content["inclusions"].append(
        {
            "id": "animate-objects-native",
            "kind": "ruleset_runtime",
            "family": "animate_objects",
            "disposition": "included_mechanical",
            "classifications": ["sylvia_gm_implementation"],
            "sources": [],
            "links": {
                "artifacts": ["runtime/ruleset.yaml"],
                "capabilities": ["dnd.srd.5.2.1.spells"],
                "tools": sorted(PUBLIC_TOOLS),
                "mechanics": [],
                "procedures": [],
                "prompt_fragments": [],
                "schemas": [],
                "state_namespaces": [],
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["paid-original-object-reopen-command-saved-slam"]},
        }
    )
    content["inclusions"].sort(key=lambda row: row["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return bundled_packages._archive((NormalizedPackageDraft(record, payloads, 4), reference))


def test_public_paid_animation_reopens_then_real_command_and_saved_slam_retry_once(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _spell: _case(sizes=("tiny",)))
    database = tmp_path / "objects.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(
            owner, "animate_objects", target_overrides={"hp": 100, "max_hp": 100}
        )
        paid, paid_input = _act(owner, "cast_spell_outside_combat", **params)
        assert paid.status is ActionStatus.COMMITTED
        retained = owner.snapshot("casting")
        actor_id = _actors(retained.state)[0]
        assert owner.execute_action(paid_input) == paid
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == retained
        _act(owner, "start_group_combat_encounter", combatants=["hero", "guard", "other", actor_id])
        _act(owner, "begin_combat_turn")
        command, command_input = _act(
            owner,
            "command_srd_animated_objects",
            caster_state="hero",
            actor_ids=[actor_id],
            command="attack",
        )
        assert command.status is ActionStatus.COMMITTED
        _act(owner, "end_combat_turn")
        _act(owner, "advance_group_combat_encounter_turn")
        _act(owner, "begin_combat_turn")
        begin, begin_input = _act(
            owner, "begin_srd_animated_object_slam", actor_state=actor_id, target_state="guard"
        )
        assert begin.status is ActionStatus.COMMITTED
        hit = begin.result["context"]["_pending_action_id"]
        assert owner.execute_action(begin_input) == begin
    with AuthorityOwner.open(database, "casting-owner") as owner:
        completed, complete_input = _act(
            owner, "complete_srd_pending_action", pending_action_id=hit
        )
        assert completed.status is ActionStatus.COMMITTED
        final = owner.snapshot("casting")
        assert final.state[f"{SRD}.entities"]["guard"]["hp"] == 96
        assert owner.execute_action(complete_input) == completed
        assert owner.execute_action(command_input) == command
        assert owner.execute_action(paid_input) == paid
        assert owner.snapshot("casting") == final
