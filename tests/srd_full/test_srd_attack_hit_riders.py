"""Actual saved weapon hits keep their paid Smite child and settle one original packet."""

import json
from copy import deepcopy
from functools import lru_cache
from random import Random
from unittest.mock import patch

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_attack_hit_riders import BEGIN, INTENT, bind_runtime, restore_runtime
from scripts.srd_full.srd_mirror_image_consumers import _weapon
from scripts.srd_full.srd_parameter_declarations import compact_runtime
from scripts.srd_full.srd_received_damage_reactions import bind_receiving_events

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_produce_flame_reactions import SOURCE
from tests.srd_full.test_srd_reaction_spells import _cast_parameters, _mage
from tests.srd_full.test_srd_reaction_windows import _parameters
from tests.srd_full.test_srd_spell_damage_riders import _case, _cast
from tests.srd_full.test_srd_spell_damage_riders import _runtime as _source_runtime
from tests.srd_full.test_srd_spell_wave3_information import _entity


@lru_cache
def _runtime():
    runtime = deepcopy(_source_runtime()[0])
    for kind, actor in (("character_attack", "actor_state"), ("creature_attack", "actor")):
        name = "complete_srd_" + kind
        runtime["tools"][name] = _weapon(runtime["tools"][name], actor, "target")
    original = bind_receiving_events(yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False))
    text = bind_runtime(original)
    assert bind_runtime(text) == text
    assert yaml.load(restore_runtime(text), Loader=yaml.CSafeLoader) == yaml.load(
        original, Loader=yaml.CSafeLoader
    )
    return yaml.load(text, Loader=yaml.CSafeLoader), compact_runtime(text).encode()


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


class Rolls(Random):
    def __init__(self, attack=20, damage=1):
        super().__init__(0)
        self.attack, self.damage, self.sides = attack, damage, []

    def randint(self, _a, b):
        self.sides.append(b)
        return self.attack if b == 20 else self.damage


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Rolls())


def _hit_case(spell="divine_smite", slot=1):
    state, data, params = _case(spell, slot)
    hero = state[f"{SRD}.entities"]["hero"]
    hero["inventory"] = [*hero.get("inventory", []), "spear"]
    data["weapon_proficiencies"] = ["simple_weapons"]
    params.update(target="hero", target_distance=0)
    for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]:
        if row["id"] == "guard":
            row["position"] = [5, 0, 0]
    return state, data, params


def _finish(state, data, event_id):
    return _run(
        "complete_srd_pending_action", state, participant_data=data, pending_action_id=event_id
    ).result["context"]


def _begin_hit(state, data, *, pause=True):
    result = _run(
        "begin_srd_character_attack",
        state,
        participant_data=data,
        **(_parameters() | {INTENT: pause}),
    )
    event_id = result.result["context"]["_pending_action_id"]
    return event_id


@pytest.mark.parametrize(
    "spell,slot,count,kind",
    [
        ("divine_smite", 1, 6, "radiant"),
        ("searing_smite", 2, 4, "fire"),
        ("shining_smite", 2, 4, "radiant"),
    ],
)
def test_real_critical_hit_paid_smite_child_reopens_then_one_packet(spell, slot, count, kind):
    state, data, params = _hit_case(spell, slot)
    hit = _begin_hit(state, data)
    roll = deepcopy(state[f"{SRD}.state"]["pending_srd_actions"][hit]["attack_roll"])
    paused = _finish(state, data, hit)
    pool = state[f"{SRD}.state"]["pending_srd_actions"]
    assert pool[hit]["stage"] == "attack_hit_riders"
    assert pool[hit]["rider_marks_captured"] is True
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 100
    assert paused["_pending_card_summary"][0]["value"] == "Not resolved yet"
    before_slot = state[f"{SRD}.entities"]["hero"]["resources"][f"spell_slot_{slot}"]
    child = _run(
        BEGIN, state, participant_data=data, **(params | {"rider_pending_action_id": hit})
    ).result["context"]["_pending_action_id"]
    assert state[f"{SRD}.state"]["pending_srd_action_stack"] == [hit, child]
    assert state[f"{SRD}.entities"]["hero"]["bonus_action_available"] is False
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 100
    state = json.loads(json.dumps(state))
    _finish(state, data, child)
    pool = state[f"{SRD}.state"]["pending_srd_actions"]
    assert state[f"{SRD}.state"]["pending_srd_action_stack"] == [hit]
    component = pool[hit]["smite_component"]
    assert component["damage_type"] == kind and component["roll"]["count"] == count
    assert state[f"{SRD}.entities"]["hero"]["resources"][f"spell_slot_{slot}"] == before_slot - 1
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 100
    finished = _finish(state, data, hit)
    assert finished["_attack_roll"] == roll
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 100 - finished["_damage_incoming"]
    assert finished["_damage_incoming"] == 2 + data["abilities"]["strength"] // 2 - 5 + count
    assert dice._rng.sides.count(20) == 1
    assert state[f"{SRD}.state"]["pending_srd_actions"] == {}
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="does not exist"):
        _finish(state, data, hit)
    assert state == before


def test_real_hit_decline_preserves_default_cost_and_one_original_damage_roll():
    state, data, _ = _hit_case()
    hit = _begin_hit(state, data)
    _finish(state, data, hit)
    state = json.loads(json.dumps(state))
    result = _finish(state, data, hit)
    assert result["_damage_incoming"] > 0
    assert dice._rng.sides.count(20) == 1
    assert state[f"{SRD}.entities"]["hero"]["bonus_action_available"] is True
    assert state[f"{SRD}.state"]["pending_srd_actions"] == {}


def test_automatic_paid_hex_component_keeps_original_critical_without_smite_pause():
    state, data, mark = _case("hex")
    hero = state[f"{SRD}.entities"]["hero"]
    hero["inventory"].append("spear")
    data["weapon_proficiencies"] = ["simple_weapons"]
    source_id = _cast(state, data, mark)
    for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]:
        if row["id"] == "guard":
            row["position"] = [5, 0, 0]
    hit = _begin_hit(state, data, pause=False)
    result = _finish(state, data, hit)
    assert result["_hit_rider_receipts"][0]["source_id"] == source_id
    assert result["_hit_rider_receipts"][0]["roll"]["count"] == 2
    assert result["_hit_rider_receipts"][0]["effective"] == 2
    assert state[f"{SRD}.state"]["pending_srd_actions"] == {}


def test_defensive_window_is_not_a_smite_hit_and_shield_miss_never_earns_components():
    state, data, params = _hit_case()
    _mage(state, "shield")
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("mage", [5, 0, 0], participant_ref="mage", creature_type="humanoid")
    )
    dice._rng = Rolls(7)
    hit = _run(
        "begin_srd_character_attack",
        state,
        participant_data=data,
        **(_parameters() | {INTENT: True, "target": "mage"}),
    ).result["context"]["_pending_action_id"]
    assert 13 <= state[f"{SRD}.state"]["pending_srd_actions"][hit]["attack_total"] < 18
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="final accepted hit"):
        _run(BEGIN, state, participant_data=data, **(params | {"rider_pending_action_id": hit}))
    assert state == before
    _run("cast_spell", state, **_cast_parameters("shield", hit))
    result = _finish(state, data, hit)
    assert result["_attack_outcome"] == "miss"
    assert state[f"{SRD}.entities"]["mage"]["hp"] == 1000
    assert state[f"{SRD}.entities"]["hero"]["bonus_action_available"] is True
    assert state[f"{SRD}.state"]["pending_srd_actions"] == {}
    assert dice._rng.sides.count(20) == 1


def test_paid_shining_replaces_hex_only_after_its_earned_hit_dice_are_saved():
    state, data, mark = _case("hex")
    hero = state[f"{SRD}.entities"]["hero"]
    hero["inventory"].append("spear")
    hero["prepared_spells"].append("shining_smite")
    data["classes"] = [{"id": "warlock", "level": 12}, {"id": "paladin", "level": 5}]
    data["spellcasting_grants"].append(
        {"source": "paladin", "ability": "charisma", "spells": ["shining_smite"]}
    )
    data["weapon_proficiencies"] = ["simple_weapons"]
    hex_id = _cast(state, data, mark)
    _run("end_combat_turn", state, actor="hero")
    state[f"{SRD}.combat"]["current_actor"] = "hero"
    _run("begin_combat_turn", state, actor="hero")
    for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]:
        if row["id"] == "guard":
            row["position"] = [5, 0, 0]
    hit = _begin_hit(state, data)
    _finish(state, data, hit)
    event = state[f"{SRD}.state"]["pending_srd_actions"][hit]
    assert event["rider_mark_components"][0]["source_id"] == hex_id
    paid = mark | {
        "spell": "shining_smite",
        "target": "hero",
        "target_distance": 0,
        "slot_resource": "spell_slot_2",
        "spell_grant": "paladin",
        "rider_pending_action_id": hit,
    }
    child = _run(BEGIN, state, participant_data=data, **paid).result["context"][
        "_pending_action_id"
    ]
    _finish(state, data, child)
    assert state[f"{SRD}.state"]["attack_rider_sources"][hex_id]["installed"] is False
    result = _finish(state, data, hit)
    assert [(r["spell"], r["effective"]) for r in result["_hit_rider_receipts"]] == [
        ("hex", 2),
        ("shining_smite", 4),
    ]
    assert dice._rng.sides.count(20) == 1
    expected = 2 + data["abilities"]["strength"] // 2 - 5 + 2 + 4
    assert result["_damage_incoming"] == expected
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 100 - expected


def test_actual_mirror_redirect_never_earns_a_smite_window_or_mark_packet():
    state, data, _ = _hit_case()
    _mage(state, "mirror_image")
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("mage", [5, 0, 0], participant_ref="mage", creature_type="humanoid")
    )
    combat = deepcopy(state[f"{SRD}.combat"])
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _run(
        "cast_spell_outside_combat",
        state,
        spell="mirror_image",
        caster_state="mage",
        verbal_component_available=True,
        somatic_component_available=True,
    )
    state[f"{SRD}.combat"] = combat
    dice._rng = Rolls(damage=6)
    hit = _run(
        "begin_srd_character_attack",
        state,
        participant_data=data,
        **(_parameters() | {INTENT: True, "target": "mage"}),
    ).result["context"]["_pending_action_id"]
    result = _finish(state, data, hit)
    assert result["_attack_outcome"] == "critical_hit"
    record = next(
        r
        for r in state[f"{SRD}.state"]["illusion_spell_sources"].values()
        if r["spell"] == "mirror_image" and r["installed"]
    )
    assert record["remaining_duplicates"] == 2
    assert state[f"{SRD}.entities"]["mage"]["hp"] == 1000
    assert state[f"{SRD}.entities"]["hero"]["bonus_action_available"] is True
    assert state[f"{SRD}.state"]["pending_srd_actions"] == {}
    assert dice._rng.sides == [20, 6, 6, 6]


@lru_cache
def _archive():
    """Retain the real native package and declare only this new public paid bridge."""
    runtime, encoded = _runtime()
    with patch.object(bundled_packages, "_bundled_srd_root", return_value=SOURCE):
        primary, reference = bundled_packages._srd_drafts()
    record = deepcopy(primary.record)
    for declaration in record["tools"]:
        if declaration["name"] in runtime["tools"]:
            declaration["input_schema"] = _tool_schema(runtime["tools"][declaration["name"]])
    record["tools"].append(
        {
            "name": BEGIN,
            "capability": "dnd.srd.5.2.1.spells",
            "input_schema": _tool_schema(runtime["tools"][BEGIN]),
            "writers": ["dm"],
            "turn_statuses": ["open"],
            "state_predicates": [],
        }
    )
    payloads = dict(primary.payloads) | {"runtime/ruleset.yaml": encoded}
    content = json.loads(payloads["content/content-declarations.json"])
    content["inclusions"].append(
        {
            "id": "saved-hit-paid-smite",
            "kind": "ruleset_runtime",
            "family": "attack_rider_spell",
            "disposition": "included_mechanical",
            "classifications": ["sylvia_gm_implementation"],
            "sources": [],
            "links": {
                "artifacts": ["runtime/ruleset.yaml"],
                "capabilities": ["dnd.srd.5.2.1.spells"],
                "tools": [BEGIN],
                "mechanics": [],
                "procedures": [],
                "prompt_fragments": [],
                "schemas": [],
                "state_namespaces": [],
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["saved-weapon-hit-paid-smite-reopen-retry"]},
        }
    )
    content["inclusions"].sort(key=lambda row: row["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return bundled_packages._archive((NormalizedPackageDraft(record, payloads, 4), reference))


def test_real_public_smite_child_reopens_and_retries_without_repeating_original_hit(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _spell: _hit_case("searing_smite", 2))
    database = tmp_path / "saved-smite.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(
            owner, "searing_smite", target_overrides={"hp": 100, "max_hp": 100, "temp_hp": 0}
        )
        _act(owner, "start_group_combat_encounter", combatants=["hero", "guard", "other"])
        _act(owner, "begin_combat_turn")
        dice._rng = Rolls()
        public_attack = _parameters() | {INTENT: True}
        public_attack.pop(
            "actor_state"
        )  # This actual public owner binds its active actor from state.
        begun, command = _act(owner, "begin_srd_character_attack", **public_attack)
        assert begun.status is ActionStatus.COMMITTED
        hit = begun.result["context"]["_pending_action_id"]
        paused, paused_command = _act(owner, "complete_srd_pending_action", pending_action_id=hit)
        assert paused.status is ActionStatus.COMMITTED
        assert "Not resolved yet" in str(paused.result["result_card"])
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["guard"]["hp"] == 100
        assert owner.execute_action(command) == begun
        assert owner.execute_action(paused_command) == paused
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        public_smite = params | {"rider_pending_action_id": hit}
        public_smite.pop("rider_ability")  # The three Smites do not make Hex's ability choice.
        child, child_command = _act(owner, BEGIN, **public_smite)
        assert child.status is ActionStatus.COMMITTED
        child_id = child.result["context"]["_pending_action_id"]
        paid, paid_command = _act(owner, "complete_srd_pending_action", pending_action_id=child_id)
        assert paid.status is ActionStatus.COMMITTED
        child_saved = owner.snapshot("casting")
        assert child_saved.state[f"{SRD}.entities"]["guard"]["hp"] == 100
        assert owner.execute_action(child_command) == child
        assert owner.execute_action(paid_command) == paid
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == child_saved
        dice._rng = Rolls(1)
        finished, finished_command = _act(
            owner, "complete_srd_pending_action", pending_action_id=hit
        )
        assert finished.status is ActionStatus.COMMITTED
        assert "Added damage" in str(finished.result["result_card"])
        assert 20 not in dice._rng.sides
        final = owner.snapshot("casting")
        assert final.state[f"{SRD}.entities"]["guard"]["hp"] < 100
        assert not final.state[f"{SRD}.state"]["pending_srd_action_stack"]
        assert owner.execute_action(finished_command) == finished
        assert owner.snapshot("casting") == final


def test_actual_counterspell_child_loses_smite_but_keeps_original_hit_and_slot():
    state, data, params = _hit_case("searing_smite", 2)
    _mage(state, "counterspell")
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("mage", [5, 0, 0], participant_ref="mage", creature_type="humanoid")
    )
    hit = _begin_hit(state, data)
    _finish(state, data, hit)
    child = _run(
        BEGIN, state, participant_data=data, **(params | {"rider_pending_action_id": hit})
    ).result["context"]["_pending_action_id"]
    before_slot = state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"]
    dice._rng = Rolls(1)
    _run("cast_spell", state, **_cast_parameters("counterspell", child))
    _finish(state, data, child)
    event = state[f"{SRD}.state"]["pending_srd_actions"][hit]
    assert event.get("smite_component") is None
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == before_slot
    assert state[f"{SRD}.entities"]["hero"]["bonus_action_available"] is False
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 100
    state = json.loads(json.dumps(state))
    dice._rng = Rolls(20)
    result = _finish(state, data, hit)
    assert 20 not in dice._rng.sides
    assert result["_attack_outcome"] == "critical_hit"
    assert result["_hit_rider_receipts"] == []
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 100 - result["_damage_incoming"]
    assert state[f"{SRD}.state"]["pending_srd_actions"] == {}


def test_authenticated_saved_packet_queues_earned_damage_before_outer_cleanup():
    state, data, params = _hit_case("searing_smite", 2)
    # This is the frozen queue owner's saved-preparation gate, not a Hellish casting proof.
    state[f"{SRD}.entities"]["guard"].update(prepared_spells=["hellish_rebuke"], temp_hp=5)
    hit = _begin_hit(state, data)
    _finish(state, data, hit)
    child = _run(
        BEGIN, state, participant_data=data, **(params | {"rider_pending_action_id": hit})
    ).result["context"]["_pending_action_id"]
    _finish(state, data, child)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 100
    event = state[f"{SRD}.state"]["pending_srd_actions"][hit]
    allowed = {
        p["name"] for p in _runtime()[0]["tools"]["resume_srd_character_attack_hit"]["parameters"]
    }
    original_parameters = {k: v for k, v in event["parameters"].items() if k in allowed}
    # The authentic private receiving boundary precedes Light's separately owned FIFO opener.
    result = _run(
        "resume_srd_character_attack_hit",
        state,
        participant_data=data,
        **(original_parameters | {"pending_action_id": hit}),
    ).result["context"]
    lasting = state[f"{SRD}.state"]
    assert len(lasting["pending_srd_received_damage_ids"]) == 1
    receipt = lasting["pending_srd_actions"][lasting["pending_srd_received_damage_ids"][0]]
    assert receipt["status"] == "queued" and receipt["owner_kind"] == "hellish_received_damage"
    assert receipt["actor_state_id"] == "hero" and receipt["target_state_id"] == "guard"
    assert receipt["received_damage"]["effective_damage"] == result["_damage_incoming"]
    assert receipt["received_damage"]["hp_before"] == 100
    assert receipt["received_damage"]["temp_hp_before"] == 5
    assert receipt["received_damage"]["hp_after"] == state[f"{SRD}.entities"]["guard"]["hp"]
    assert (
        receipt["received_damage"]["effective_damage"]
        > 100 - receipt["received_damage"]["hp_after"]
    )
    assert lasting["pending_srd_action_stack"] == [hit]
    assert dice._rng.sides.count(20) == 1
