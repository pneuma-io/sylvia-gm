"""Actual Root outer drains settle earned Storm damage without a packet-to-Reaction edge."""

import importlib.util
import json
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from scripts import srd_received_damage_reactions as receiving
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages, package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from sylvia_gm.runtime._types import ExecutionBudget
from tests import test_srd_storm_lightning as storm
from tests.srd_full.test_srd_action_budget import SRD
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_sunbeam import Rolls

DEPENDENCY = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-storm-hellish-outer/srd_hellish_rebuke.py")
)
ROOT_SUCCESSOR = "5c8d3b8689b312db8ddafa31f53129d44e8f46ce2dc3ec424be5985dbaba0ceb"
QUEUE_PARENT = "e86ec89ab4af582863f7090738a2fd46d58d7d393b2819aa5d0b1d1dc88995b9"
QUEUE_SOURCE = "462073a3f5e740a0595b751712b9f0c65f1afa2a2b2f1068a55b42f2c64bb95c"
RECEIVERS = {"guard": "rain_guard", "other": "rain_other"}
TARGETS = [*RECEIVERS.values(), *storm.TARGETS[2:]]


@lru_cache
def _source():
    assert sha256(DEPENDENCY.read_bytes()).hexdigest() == ROOT_SUCCESSOR
    assert sha256(
        DEPENDENCY.with_name("srd_received_damage_reactions.py").read_bytes()
    ).hexdigest() == (QUEUE_PARENT)
    assert sha256(Path(receiving.__file__).read_bytes()).hexdigest() == QUEUE_SOURCE
    spec = importlib.util.spec_from_file_location("scripts._storm_hellish_root_parent", DEPENDENCY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache
def _runtime():
    source = _source()
    parent = deepcopy(storm._runtime()[0])
    parent["definitions"]["spells"]["hellish_rebuke"] = deepcopy(source.PROFILE)
    for tool in parent["tools"].values():
        for parameter in tool["parameters"]:
            values = parameter.get("enum_values", [])
            if (
                parameter["name"] == "spell"
                and "shield" in values
                and "hellish_rebuke" not in values
            ):
                values.append("hellish_rebuke")
    text = yaml.dump(parent, Dumper=yaml.CSafeDumper, sort_keys=False)
    queued = yaml.load(receiving.bind_receiving_events(text), Loader=yaml.CSafeLoader)
    encoded = source.bind_runtime_consumers(
        yaml.dump(queued, Dumper=yaml.CSafeDumper, sort_keys=False)
    )
    runtime = yaml.load(encoded, Loader=yaml.CSafeLoader)
    for name in ("begin_combat_turn", storm.lightning.CHOOSE):
        assert runtime["tools"][name]["effects"][:-1] == queued["tools"][name]["effects"]
        assert source.OPEN in str(runtime["tools"][name]["effects"][-1])
    for name in (
        "process_srd_turn_effects",
        "resolve_srd_storm_later_caster_start",
        storm.lightning.TARGET,
        storm.lightning.objects.PACKET,
        "apply_srd_damage",
        "apply_srd_composite_damage",
        "apply_srd_qualifying_damage",
        "end_combat_turn",
        "advance_srd_lasting_time",
    ):
        assert runtime["tools"][name] == queued["tools"][name]
        assert source.OPEN not in str(runtime["tools"][name]["effects"])
    return runtime, encoded.encode()


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
            input_schema=_tool_schema(runtime["tools"][name], tool_name=name),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@lru_cache
def _archive():
    runtime, encoded = _runtime()
    dbf = storm.base.base.base.base
    with patch.object(bundled_packages, "_bundled_srd_root", return_value=dbf.SOURCE):
        primary, reference = bundled_packages._srd_drafts()
    record = deepcopy(primary.record)
    names = dbf.PUBLIC_TOOLS | {"complete_srd_pending_action"}
    for declaration in record["tools"]:
        name = declaration["name"]
        actual_schema = _tool_schema(runtime["tools"][name])
        if declaration["input_schema"] != actual_schema:
            declaration["input_schema"] = actual_schema
            names.add(name)
    for name in names:
        record["tools"] = [row for row in record["tools"] if row["name"] != name]
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
            "id": "storm-native-outer-received-damage",
            "kind": "ruleset_runtime",
            "family": "spells",
            "disposition": "included_mechanical",
            "classifications": ["sylvia_gm_implementation"],
            "sources": [],
            "links": {
                "artifacts": ["runtime/ruleset.yaml"],
                "capabilities": ["dnd.srd.5.2.1.spells"],
                "tools": sorted(names),
                "mechanics": [],
                "procedures": [],
                "prompt_fragments": [],
                "schemas": [],
                "state_namespaces": [],
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["native-rain-lightning-fifo-rebuke-reopen-retry"]},
        }
    )
    content["inclusions"].sort(key=lambda row: row["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return bundled_packages._archive((NormalizedPackageDraft(record, payloads, 4), reference))


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    runtime, _ = _runtime()
    dbf = storm.base.base.base.base
    monkeypatch.setattr(dbf, "_runtime", _runtime)
    monkeypatch.setattr(dbf.budget, "_mechanic", _mechanic)
    monkeypatch.setattr(
        dbf,
        "PUBLIC_TOOLS",
        dbf.PUBLIC_TOOLS
        | storm.lightning.PUBLIC_TOOLS
        | {"prepare_srd_spell_object", "begin_srd_hellish_rebuke"},
    )
    dbf._archive.cache_clear()
    _archive.cache_clear()
    monkeypatch.setattr(dbf.cards, "_spell_definitions", lambda: runtime["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Rolls())
    actual = package_runtime.execute_tool

    def diagnose(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnose)


def _npc(name):
    return {
        "creature_id": name,
        "name": "Storm receiving caster",
        "source": "Synthetic humanoid with admitted saves and two Hellish Rebuke uses.",
        "armor_class": 12,
        "hit_points": 1000,
        "speed": 30,
        "size_rank": 2,
        "initiative_modifier": 0,
        "creature_type": "humanoid",
        "constitution_save": 20,
        "dexterity_save": -10 if name == RECEIVERS["guard"] else 20,
        "spellcasting_ability": "intelligence",
        "spellcasting_modifier": 4,
        "spell_attack_bonus": 7,
        "spell_save_dc": 15,
        "spells": [{"spell": "hellish_rebuke", "uses_per_day": 2}],
    }


def _public_case(_spell):
    state, data, parameters = storm._unpaid_case()
    for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]:
        if row["id"] in RECEIVERS:
            old_id = row["id"]
            row["id"] = RECEIVERS[old_id]
            row["metadata"]["participant_ref"] = RECEIVERS[old_id]
            if old_id == "other":
                row["position"] = [0, 40, 0]
        elif row["id"] == "orb":
            row["position"] = [100, 100, 0]
    parameters["storm_cloud_point"] = [220, 220, 50]
    return state, data, parameters


def _source_row(state):
    return storm.base.base.base._sources(state)[0]


def _pending(state):
    return state[f"{SRD}.state"]


def _receipts(state):
    return sorted(
        (
            (key, row)
            for key, row in _pending(state)["pending_srd_actions"].items()
            if row["kind"] == "received_damage"
        ),
        key=lambda pair: int(pair[0].rsplit("-", 1)[1]),
    )


def _rebuke(owner, receipts, receiver, event_id):
    begun = _act(
        owner,
        "begin_srd_hellish_rebuke",
        caster_state=receiver,
        reaction_trigger_id=event_id,
        verbal_component_available=True,
        somatic_component_available=True,
    )
    receipts.append(begun)
    assert begun[0].status is ActionStatus.COMMITTED
    spell_id = begun[0].result["context"]["_pending_action_id"]
    receipts.append(_act(owner, "complete_srd_pending_action", pending_action_id=spell_id))
    assert "Hellish Rebuke" in str(receipts[-1][0].result["result_card"])
    receipts.append(_act(owner, "complete_srd_pending_action", pending_action_id=event_id))


def test_actual_root_successor_only_drains_two_new_outer_owners_and_repeats():
    runtime, encoded = _runtime()
    assert (
        yaml.load(_source().bind_runtime_consumers(encoded.decode()), Loader=yaml.CSafeLoader)
        == runtime
    )


def test_paid_native_rain_then_lightning_outer_receipts_reopen_and_settle_fifo_once(
    tmp_path, monkeypatch
):
    dbf = storm.base.base.base.base
    public = dbf.public_casting
    monkeypatch.setattr(public, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public, "_spell_case", _public_case)
    rolls, budgets = Rolls(), []
    monkeypatch.setattr(dice, "_rng", rolls)
    consume = ExecutionBudget.consume_operation

    def count(budget):
        if not budgets or budgets[-1] is not budget:
            budgets.append(budget)
        consume(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", count)
    database, receipts = tmp_path / "storm-hellish.db", []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner,
            "storm_of_vengeance",
            target_overrides={"hp": 1000, "max_hp": 1000},
            entity_overrides={
                "hp": 1000,
                "max_hp": 1000,
                "temp_hp": 0,
                "saving_throw_modifiers": {"dexterity": -10, "constitution": 20},
            },
        )
        for receiver in RECEIVERS.values():
            receipts.append(_act(owner, "prepare_srd_creature", **_npc(receiver)))
        for name in storm.TARGETS[2:]:
            receipts.append(
                _act(
                    owner,
                    "prepare_srd_spell_object",
                    object_id=name,
                    material="wood",
                    size="large",
                    durability="resilient",
                )
            )
        slots_before = owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]["resources"][
            "spell_slot_9"
        ]
        receipts.append(_act(owner, "cast_spell_outside_combat", **params))
        source_id = _source_row(owner.snapshot("casting").state)["source_id"]
        receipts.append(
            _act(
                owner,
                "start_combat_encounter_entities",
                first_actor="hero",
                second_actor=RECEIVERS["guard"],
                first_surprised=False,
                second_surprised=False,
                tie_winner="first",
            )
        )
        faces = deepcopy(rolls.sides)
        receipts.append(_act(owner, "begin_combat_turn"))
        saved = owner.snapshot("casting")
        state, combat = saved.state, saved.state[f"{SRD}.combat"]
        assert rolls.sides[len(faces) :] == [6] * 4
        assert combat["turn_active"] is True and combat["action_available"] is True
        assert _source_row(state)["last_resolved_ordinal"] == 2
        assert "object damage" in str(receipts[-1][0].result["result_card"])
        rain = _receipts(state)
        assert [row["target_state_id"] for _, row in rain] == list(RECEIVERS.values())
        assert [row["status"] for _, row in rain] == ["open", "queued"]
        assert _pending(state)["pending_srd_action_stack"] == [rain[0][0]]
        assert _pending(state)["pending_srd_received_damage_ids"] == [rain[1][0]]
        for _, row in rain:
            assert row["actor_state_id"] == "hero" and row["parent_action_id"] is None
            assert row["turn_actor"] == "hero"
            assert row["received_damage"]["effective_damage"] == 12
            assert row["received_damage"]["hp_before"] == 1000
            assert row["received_damage"]["hp_after"] == 988
            assert row["received_damage"]["created_tick"] == _pending(state)["lasting_tick"]
        faces = deepcopy(rolls.sides)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        _rebuke(owner, receipts, RECEIVERS["guard"], rain[0][0])
        state = owner.snapshot("casting").state
        assert _pending(state)["pending_srd_action_stack"] == [rain[1][0]]
        assert state[f"{SRD}.entities"][RECEIVERS["guard"]]["reaction_available"] is False
        assert (
            state[f"{SRD}.entities"][RECEIVERS["guard"]]["spell_uses_remaining"]["hellish_rebuke"]
            == 1
        )
        _rebuke(owner, receipts, RECEIVERS["other"], rain[1][0])
        state = owner.snapshot("casting").state
        assert not _pending(state)["pending_srd_action_stack"]
        assert not _pending(state)["pending_srd_received_damage_ids"]
        assert _source_row(state)["source_id"] == source_id
        assert rolls.sides[len(faces) :] == ([10, 10, 20, 20] * 2)
        for name in (
            "end_combat_turn",
            "advance_combat_encounter_turn",
            "begin_combat_turn",
            "end_combat_turn",
            "advance_combat_encounter_turn",
            "begin_combat_turn",
        ):
            receipts.append(_act(owner, name))
        state = owner.snapshot("casting").state
        assert _source_row(state)["storm_ordinal"] == 3
        assert _source_row(state)["later_effect_pending"] is True
        assert state[f"{SRD}.entities"][RECEIVERS["guard"]]["reaction_available"] is True
        assert state[f"{SRD}.entities"][RECEIVERS["other"]]["reaction_available"] is False
        faces = deepcopy(rolls.sides)
        receipts.append(_act(owner, storm.lightning.CHOOSE, caster_state="hero", targets=TARGETS))
        saved = owner.snapshot("casting")
        state = saved.state
        assert rolls.sides[len(faces) :] == [6] * 10 + [20, 20]
        assert _source_row(state)["source_id"] == source_id
        assert _source_row(state)["last_resolved_ordinal"] == 3
        assert _source_row(state)["storm_last_stage_result"]["raw_roll"]["results"] == [3] * 10
        assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"] == slots_before - 1
        bolts = _receipts(state)
        assert [row["target_state_id"] for _, row in bolts] == list(RECEIVERS.values())
        assert all(row["turn_actor"] == "hero" for _, row in bolts)
        assert [row["received_damage"]["effective_damage"] for _, row in bolts] == [30, 15]
        assert _pending(state)["pending_srd_action_stack"] == [bolts[0][0]]
        assert _pending(state)["pending_srd_received_damage_ids"] == [bolts[1][0]]
        faces = deepcopy(rolls.sides)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        _rebuke(owner, receipts, RECEIVERS["guard"], bolts[0][0])
        saved = owner.snapshot("casting")
        state = saved.state
        assert rolls.sides[len(faces) :] == [10, 10, 20, 20]
        assert not _pending(state)["pending_srd_action_stack"]
        assert not _pending(state)["pending_srd_received_damage_ids"]
        assert _pending(state)["pending_srd_actions"][bolts[1][0]]["status"] == "ineligible"
        assert (
            state[f"{SRD}.entities"][RECEIVERS["guard"]]["spell_uses_remaining"]["hellish_rebuke"]
            == 0
        )
        assert _source_row(state)["source_id"] == source_id
        faces = deepcopy(rolls.sides)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        for result, command in receipts:
            assert result.status is ActionStatus.COMMITTED
            assert owner.execute_action(command) == result
        assert owner.snapshot("casting") == saved and rolls.sides == faces
    assert all(budget.max_operations == 10000 for budget in budgets)
    print(
        "Storm/Hellish native outer max operations:", max(budget.operations for budget in budgets)
    )
