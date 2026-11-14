"""Authentic receiving packets open durable Hellish Rebuke and ordinary Counterspell windows."""

import json
from copy import deepcopy
from functools import lru_cache
from random import Random
from unittest.mock import patch

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_hellish_rebuke import OPEN, PROFILE, _lists, _opener, bind_runtime_consumers
from scripts.srd_full.srd_received_damage_reactions import bind_receiving_events

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages, package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from sylvia_gm.runtime._expression_parser import MAX_EXPRESSION_LENGTH, parse_expression
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_produce_flame_reactions import SOURCE, _mage_spec
from tests.srd_full.test_srd_reaction_spells import _mage
from tests.srd_full.test_srd_reaction_windows import _parameters as _attack_parameters
from tests.srd_full.test_srd_received_damage_reactions import _parent
from tests.srd_full.test_srd_spell_wave3_information import _case as _scene_case
from tests.srd_full.test_srd_spell_wave3_information import _entity, _wall


@lru_cache
def _runtime():
    parent = deepcopy(_parent()[0])
    parent["definitions"]["spells"]["hellish_rebuke"] = deepcopy(PROFILE)
    for tool in parent["tools"].values():
        for parameter in tool["parameters"]:
            if (
                parameter["name"] == "spell"
                and "enum_values" in parameter
                and "shield" in parameter["enum_values"]
            ):
                parameter["enum_values"].append("hellish_rebuke")
    # Creature preparation uses the admitted profile union rather than a verdict fixture.
    text = yaml.dump(parent, Dumper=yaml.CSafeDumper, sort_keys=False)
    text = bind_runtime_consumers(bind_receiving_events(text))
    return yaml.load(text, Loader=yaml.CSafeLoader), text.encode()


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
    actual = package_runtime.execute_tool

    def diagnosed(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnosed)


class Rolls(Random):
    def __init__(self):
        super().__init__(0)
        self.sides = []

    def randint(self, low, high):
        self.sides.append(high)
        return 10 if high == 20 else min(4, high)


def _case(save_success=False):
    state, data, _ = _scene_case("shield")
    _mage(state, "hellish_rebuke")
    entities = state[f"{SRD}.entities"]
    entities["hero"].update(creature_kind="character", hp=1000, max_hp=1000, temp_hp=0)
    entities["hero"]["saving_throw_modifiers"] = dict.fromkeys(data["abilities"], 0)
    entities["hero"]["saving_throw_modifiers"]["dexterity"] = 30 if save_success else -30
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1].update(id="mage", position=[5, 0, 0])
    scene["entities"][1]["metadata"].update(participant_ref="mage", lighting="bright")
    scene["entities"][2]["position"] = [100, 100, 0]
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="hero",
        current_actor="hero",
        action_available=True,
        normal_action_spent=False,
        attacks_remaining=0,
    )
    return state, data


def _actual_hit(state, data):
    params = _attack_parameters() | {"target": "mage"}
    begun = _run("begin_srd_character_attack", state, participant_data=data, **params)
    original_id = begun.result["context"]["_pending_action_id"]
    _run("complete_srd_pending_action", state, participant_data=data, pending_action_id=original_id)
    owner = state[f"{SRD}.state"]
    event_id = owner["pending_srd_action_stack"][-1]
    assert event_id != original_id
    assert owner["pending_srd_actions"][event_id]["kind"] == "received_damage"
    assert owner["pending_srd_actions"][event_id]["parent_action_id"] == original_id
    return event_id


@pytest.mark.parametrize("save_success", [False, True])
def test_real_hit_receipt_then_paid_rebuke_survives_reopen_and_resolves_once(
    monkeypatch, save_success
):
    state, data = _case(save_success)
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    event_id = _actual_hit(state, data)
    original_hp = state[f"{SRD}.entities"]["mage"]["hp"]
    assert rolls.sides == [20, 6]
    event = deepcopy(state[f"{SRD}.state"]["pending_srd_actions"][event_id])
    begun = _run(
        "begin_srd_hellish_rebuke",
        state,
        caster_state="mage",
        reaction_trigger_id=event_id,
        verbal_component_available=True,
        somatic_component_available=True,
    )
    spell_id = begun.result["context"]["_pending_action_id"]
    assert spell_id != event_id
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 1000
    state = json.loads(json.dumps(state))
    out = _run(
        "complete_srd_pending_action", state, participant_data=data, pending_action_id=spell_id
    )
    expected = 4 if save_success else 8
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 1000 - expected
    assert state[f"{SRD}.entities"]["mage"]["hp"] == original_hp
    assert state[f"{SRD}.entities"]["mage"]["reaction_available"] is False
    assert state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["hellish_rebuke"] == 1
    assert rolls.sides == [20, 6, 10, 10, 20]
    assert "Hellish Rebuke" in str(out.result["result_card"])
    assert (
        state[f"{SRD}.state"]["pending_srd_actions"][event_id]["received_damage"]
        == event["received_damage"]
    )
    _run("complete_srd_pending_action", state, pending_action_id=event_id)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("complete_srd_pending_action", state, pending_action_id=spell_id)
    assert state == before


@pytest.mark.parametrize("reason", ["range", "blind", "incapacitated", "dead", "no_reaction"])
def test_opening_uses_current_real_receiver_and_scene_instead_of_damage_amount_verdict(reason):
    state, _ = _case()
    receiver = state[f"{SRD}.entities"]["mage"]
    if reason == "range":
        state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = [61, 0, 0]
    elif reason == "blind":
        receiver["conditions"] = ["blinded"]
    elif reason == "incapacitated":
        receiver["conditions"] = ["incapacitated"]
    elif reason == "dead":
        receiver.update(hp=1)
    else:
        receiver["reaction_available"] = False
    _run(
        "apply_srd_damage",
        state,
        target="mage",
        source_state_id="hero",
        amount=1,
        adjustment=0,
        damage_type="force",
    )
    queued = state[f"{SRD}.state"]["pending_srd_received_damage_ids"][0]
    _run(OPEN, state)
    assert not state[f"{SRD}.state"]["pending_srd_action_stack"]
    assert state[f"{SRD}.state"]["pending_srd_actions"][queued]["status"] == "ineligible"


def test_final_join_is_idempotent_and_never_puts_an_opening_edge_in_damage():
    runtime, encoded = _runtime()
    assert bind_runtime_consumers(encoded.decode()) == encoded.decode()
    for name in ("apply_srd_damage", "apply_srd_composite_damage", "apply_srd_qualifying_damage"):
        assert OPEN not in str(runtime["tools"][name]["effects"])


def test_newer_owning_reaction_card_shape_and_native_d20_are_preserved():
    from scripts.srd_full.srd_reaction_spells import _reaction_tools

    parent = deepcopy(_runtime()[0])
    actual_owner = _reaction_tools()["cast_spell_reaction"]
    parent["tools"]["cast_spell_reaction"] = actual_owner
    text = yaml.dump(parent, Dumper=yaml.CSafeDumper, sort_keys=False)
    rebound = yaml.load(bind_runtime_consumers(text), Loader=yaml.CSafeLoader)
    original_card = next(
        e
        for e in actual_owner["effects"]
        if e.get("condition", "").startswith("spell != 'feather_fall'")
    )
    new_card = next(
        e
        for e in rebound["tools"]["cast_spell_reaction"]["effects"]
        if e.get("condition", "").startswith("spell != 'feather_fall'")
    )
    assert new_card["on_true"] == original_card["on_true"]
    assert new_card["condition"] == original_card["condition"] + " and spell != 'hellish_rebuke'"
    assert rebound["tools"]["d20_test"] == parent["tools"]["d20_test"]


def test_visible_creature_behind_actual_transparent_total_cover_cannot_open_rebuke():
    state, _ = _case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["position"] = [20, 0, 0]
    wall = _wall(1)
    wall["visibility"] = "clear"
    scene["geometry"] = [wall]
    scene["entities"][0]["metadata"]["cover_from"] = {"mage": "total"}
    _run(
        "apply_srd_damage",
        state,
        target="mage",
        source_state_id="hero",
        amount=1,
        adjustment=0,
        damage_type="force",
    )
    queued = state[f"{SRD}.state"]["pending_srd_received_damage_ids"][0]
    out = _run(OPEN, state)
    assert not state[f"{SRD}.state"]["pending_srd_action_stack"]
    assert state[f"{SRD}.state"]["pending_srd_actions"][queued]["status"] == "ineligible"
    assert out.result["context"]["_opened_received_damage_id"] is None


def test_real_saved_hit_and_rebuke_keep_the_unchanged_operation_cap(monkeypatch):
    from sylvia_gm.runtime._types import ExecutionBudget

    state, data = _case()
    monkeypatch.setattr(dice, "_rng", Rolls())
    budgets = []
    actual = ExecutionBudget.consume_operation

    def count(current):
        if not budgets or budgets[-1] is not current:
            budgets.append(current)
        actual(current)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", count)
    event_id = _actual_hit(state, data)
    begun = _run(
        "begin_srd_hellish_rebuke",
        state,
        caster_state="mage",
        reaction_trigger_id=event_id,
        verbal_component_available=True,
        somatic_component_available=True,
    )
    _run(
        "complete_srd_pending_action",
        state,
        pending_action_id=begun.result["context"]["_pending_action_id"],
    )
    _run("complete_srd_pending_action", state, pending_action_id=event_id)
    assert all(b.max_operations == 10000 for b in budgets)
    print(
        "Authentic hit/received-window/Rebuke max operations:", max(b.operations for b in budgets)
    )

    def nodes(value):
        if isinstance(value, dict):
            return 1 + sum(nodes(k) + nodes(v) for k, v in value.items())
        if isinstance(value, list):
            return 1 + sum(map(nodes, value))
        return 1

    print(
        "Queue+Hellish native267 expanded-node delta:", nodes(_runtime()[0]) - nodes(_parent()[0])
    )


def test_genuine_counterspell_loses_rebuke_but_keeps_original_damage_and_one_reaction(monkeypatch):
    state, data = _case()
    data["spellcasting_grants"][0]["spells"].append("counterspell")
    data["wizard_spellbook"].append("counterspell")
    hero = state[f"{SRD}.entities"]["hero"]
    hero["prepared_spells"].append("counterspell")
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    event_id = _actual_hit(state, data)
    original_hp = state[f"{SRD}.entities"]["mage"]["hp"]
    begun = _run(
        "begin_srd_hellish_rebuke",
        state,
        caster_state="mage",
        reaction_trigger_id=event_id,
        verbal_component_available=True,
        somatic_component_available=True,
    )
    spell_id = begun.result["context"]["_pending_action_id"]
    _run(
        "cast_srd_reaction_spell",
        state,
        participant_data=data,
        spell="counterspell",
        caster="hero",
        caster_state="hero",
        slot_resource="spell_slot_3",
        reaction_trigger_id=spell_id,
        verbal_component_available=True,
        somatic_component_available=True,
    )
    assert state[f"{SRD}.state"]["pending_srd_actions"][spell_id]["status"] == "lost"
    _run("complete_srd_pending_action", state, pending_action_id=spell_id)
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 1000
    assert state[f"{SRD}.entities"]["mage"]["hp"] == original_hp
    assert state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["hellish_rebuke"] == 1
    assert state[f"{SRD}.entities"]["mage"]["reaction_available"] is False
    assert rolls.sides == [20, 6, 20]  # authentic countered Constitution save, no Fire dice
    _run("complete_srd_pending_action", state, pending_action_id=event_id)


def test_upcast_character_uses_actual_slot_and_fifo_completed_packets(monkeypatch):
    state, data, _ = _scene_case("hellish_rebuke")
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(creature_kind="character", hp=1000, max_hp=1000, temp_hp=5)
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    for _ in range(2):
        _run(
            "apply_srd_damage",
            state,
            target="hero",
            source_state_id="guard",
            amount=1,
            adjustment=0,
            damage_type="cold",
        )
    queued = list(state[f"{SRD}.state"]["pending_srd_received_damage_ids"])
    _run(OPEN, state)
    assert state[f"{SRD}.state"]["pending_srd_action_stack"] == [queued[0]]
    out = _run(
        "begin_srd_hellish_rebuke",
        state,
        participant_data=data,
        caster="hero",
        caster_state="hero",
        reaction_trigger_id=queued[0],
        slot_resource="spell_slot_3",
        verbal_component_available=True,
        somatic_component_available=True,
    )
    spell_id = out.result["context"]["_pending_action_id"]
    _run("complete_srd_pending_action", state, participant_data=data, pending_action_id=spell_id)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
    assert rolls.sides == [10] * 4 + [20]
    _run("complete_srd_pending_action", state, pending_action_id=queued[0])
    assert not state[f"{SRD}.state"]["pending_srd_action_stack"]
    assert state[f"{SRD}.state"]["pending_srd_actions"][queued[1]]["status"] == "ineligible"


@lru_cache
def _archive():
    runtime, encoded = _runtime()
    with patch.object(bundled_packages, "_bundled_srd_root", return_value=SOURCE):
        primary, reference = bundled_packages._srd_drafts()
    record = deepcopy(primary.record)
    names = {"begin_srd_hellish_rebuke", "cast_spell_outside_combat", "complete_srd_pending_action"}
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
            "id": "hellish-authentic-received-damage",
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
            "evidence": {"tests": ["public-hit-receipt-rebuke-reopen-retry"]},
        }
    )
    content["inclusions"].sort(key=lambda row: row["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return bundled_packages._archive((NormalizedPackageDraft(record, payloads, 4), reference))


def _public_case(_spell):
    state, data, params = _scene_case("hellish_rebuke")
    state[f"{SRD}.entities"]["hero"].update(
        creature_kind="character", hp=1000, max_hp=1000, temp_hp=0
    )
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("mage", [5, 0, 0], participant_ref="mage", creature_type="humanoid")
    )
    return state, data, params


def test_strict_package_public_receiving_cast_and_rebuke_reopen_without_repeating_damage(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public_casting, "_spell_case", _public_case)
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    database = tmp_path / "hellish.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "hellish_rebuke")
        spec = _mage_spec() | {"spells": [{"spell": "fire_bolt", "uses_per_day": 0}]}
        prepared, _ = _act(owner, "prepare_srd_creature", **spec)
        assert prepared.status is ActionStatus.COMMITTED
        params.pop("caster")
        params.pop("slot_resource")
        params.update(spell="fire_bolt", caster_state="mage", target="hero", target_distance=5)
        hit, hit_command = _act(owner, "cast_spell_outside_combat", **params)
        saved_hit = owner.snapshot("casting")
        event_id = saved_hit.state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
        assert saved_hit.state[f"{SRD}.entities"]["hero"]["hp"] == 996
        assert rolls.sides == [20, 10]
        assert owner.execute_action(hit_command) == hit
        assert owner.snapshot("casting") == saved_hit
        begun, begin_command = _act(
            owner,
            "begin_srd_hellish_rebuke",
            caster="hero",
            caster_state="hero",
            reaction_trigger_id=event_id,
            slot_resource="spell_slot_1",
            verbal_component_available=True,
            somatic_component_available=True,
        )
        assert begun.status is ActionStatus.COMMITTED
        spell_id = begun.result["context"]["_pending_action_id"]
        pending = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == pending
        assert owner.execute_action(begin_command) == begun
        completed, complete_command = _act(
            owner, "complete_srd_pending_action", pending_action_id=spell_id
        )
        assert completed.status is ActionStatus.COMMITTED
        assert "Hellish Rebuke" in str(completed.result["result_card"])
        rebuked = owner.snapshot("casting")
        assert rebuked.state[f"{SRD}.entities"]["hero"]["hp"] == 996
        assert rebuked.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
        assert rebuked.state[f"{SRD}.entities"]["mage"]["hp"] == 992
        assert rolls.sides == [20, 10, 10, 10, 20]
        assert owner.execute_action(complete_command) == completed
        assert owner.snapshot("casting") == rebuked
        settled, settle_command = _act(
            owner, "complete_srd_pending_action", pending_action_id=event_id
        )
        final = owner.snapshot("casting")
        assert not final.state[f"{SRD}.state"]["pending_srd_action_stack"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
        assert owner.execute_action(settle_command) == settled
        assert owner.snapshot("casting") == final
        assert rolls.sides == [20, 10, 10, 10, 20]


@pytest.mark.parametrize(
    "status,kind,owner_kind,active,opens",
    [
        ("completed", "weapon_attack", "weapon_attack", False, True),
        ("resolved", "freezing_sphere", "freezing_sphere", False, True),
        ("open", "freezing_sphere", "freezing_sphere", False, False),
        ("queued", "freezing_sphere", "freezing_sphere", False, False),
        ("cancelled", "freezing_sphere", "freezing_sphere", False, False),
        ("resolved", "weapon_attack", "weapon_attack", False, False),
        ("resolved", "freezing_sphere", "other_owner", False, False),
        ("resolved", "freezing_sphere", "freezing_sphere", True, False),
    ],
)
def test_real_queued_packet_waits_for_its_exact_settled_parent(
    status,
    kind,
    owner_kind,
    active,
    opens,
):
    state, _ = _case()
    _run(
        "apply_srd_damage",
        state,
        target="mage",
        source_state_id="hero",
        amount=1,
        adjustment=0,
        damage_type="force",
    )
    lasting = state[f"{SRD}.state"]
    event_id = lasting["pending_srd_received_damage_ids"][0]
    event = lasting["pending_srd_actions"][event_id]
    packet = deepcopy(event["received_damage"])
    parent = "readiness-parent"
    event["parent_action_id"] = parent
    lasting["pending_srd_actions"][parent] = {
        "kind": kind,
        "owner_kind": owner_kind,
        "status": status,
    }
    lasting["pending_srd_action_stack"] = [parent] if active else []
    entities = deepcopy(state[f"{SRD}.entities"])
    _run(OPEN, state)
    lasting = state[f"{SRD}.state"]
    assert lasting["pending_srd_actions"][event_id]["received_damage"] == packet
    assert state[f"{SRD}.entities"] == entities
    if opens:
        assert lasting["pending_srd_action_stack"] == [event_id]
        assert lasting["pending_srd_actions"][event_id]["status"] == "open"
    else:
        assert lasting["pending_srd_action_stack"] == ([parent] if active else [])
        assert lasting["pending_srd_actions"][event_id]["status"] == "queued"
        assert lasting["pending_srd_received_damage_ids"] == [event_id]


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "fabricated"),
        ("owner_kind", "fabricated"),
        ("received_damage", {"effective_damage": 0}),
    ],
)
def test_settled_sphere_parent_cannot_qualify_a_fabricated_receipt(field, value):
    state, _ = _case()
    _run(
        "apply_srd_damage",
        state,
        target="mage",
        source_state_id="hero",
        amount=1,
        adjustment=0,
        damage_type="force",
    )
    lasting = state[f"{SRD}.state"]
    event_id = lasting["pending_srd_received_damage_ids"][0]
    event = lasting["pending_srd_actions"][event_id]
    parent = "settled-sphere-parent"
    event["parent_action_id"] = parent
    lasting["pending_srd_actions"][parent] = {
        "kind": "freezing_sphere",
        "owner_kind": "freezing_sphere",
        "status": "resolved",
    }
    event[field] = value
    entities = deepcopy(state[f"{SRD}.entities"])
    _run(OPEN, state)
    lasting = state[f"{SRD}.state"]
    assert not lasting["pending_srd_action_stack"]
    assert lasting["pending_srd_actions"][event_id]["status"] == "ineligible"
    assert not lasting["pending_srd_received_damage_ids"]
    assert state[f"{SRD}.entities"] == entities


def test_readiness_expressions_fit_the_engine_after_lasting_state_binding():
    expressions = [
        effect["set"]["value"]
        for group in _lists(_opener()["effects"])
        for effect in group
        if effect.get("set", {}).get("field") == "_received_ready"
        and isinstance(effect["set"]["value"], str)
    ]
    assert MAX_EXPRESSION_LENGTH == 500
    assert expressions
    for expression in expressions:
        assert len(expression) <= MAX_EXPRESSION_LENGTH, expression
        parse_expression(expression)
