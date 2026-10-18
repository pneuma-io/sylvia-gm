"""A real paid hand has a source-owned object body, attacks and later Bonus Actions."""

import json
from copy import deepcopy
from functools import lru_cache
from unittest.mock import patch

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_arcane_hand import PROFILE, bind_runtime_consumers, schema
from scripts.srd_full.srd_parameter_declarations import compact_runtime

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages, package_runtime
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
from tests.srd_full.test_srd_produce_flame_reactions import SOURCE as PACKAGE_SOURCE
from tests.srd_full.test_srd_spell_damage_riders import _runtime as source_runtime
from tests.srd_full.test_srd_spell_wave3_information import _case as scene_case


@lru_cache
def _runtime():
    parent = source_runtime()[0]
    text = bind_runtime_consumers(yaml.dump(parent, Dumper=yaml.CSafeDumper, sort_keys=False))
    assert bind_runtime_consumers(text) == text
    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    runtime["definitions"]["spells"]["arcane_hand"] = deepcopy(PROFILE)
    runtime["definition_row_schemas"]["definitions.spells"]["variants"]["arcane_hand"] = schema()
    for name in (
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_spell_action",
        "begin_srd_spell_cast_internal",
    ):
        spell = next(p for p in runtime["tools"][name]["parameters"] if p["name"] == "spell")
        spell["enum_values"] = sorted(set(spell["enum_values"]) | {"arcane_hand"})
    assert parent["tools"]["d20_test"] == runtime["tools"]["d20_test"]
    return runtime, compact_runtime(
        yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
    ).encode()


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


class Rolls:
    def __init__(self, attack=10):
        self.attack = attack
        self.sides = []

    def randint(self, _a, b):
        self.sides.append(b)
        return self.attack if b == 20 else 1


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Rolls())
    original = package_runtime.execute_tool

    def diagnose(*args, **kwargs):
        result = original(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnose)


def _case(slot=5, option="clenched_fist"):
    state, data, params = scene_case("arcane_hand")
    entities = state[f"{SRD}.entities"]
    entities["hero"].update(hp=20, max_hp=100)
    entities["guard"].update(hp=100, max_hp=100, armor_class=1, temp_hp=0)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"] = [r for r in scene["entities"] if r["id"] in {"hero", "guard"}]
    scene["zones"] = [
        {
            "id": "ambient",
            "map_id": "information",
            "shape": {"kind": "box", "min": [-1000, -1000, -1000], "max": [1000, 1000, 1000]},
            "metadata": {"plane": "material", "lighting": "bright"},
        }
    ]
    params.update(
        slot_resource=f"spell_slot_{slot}",
        hand_id="hand",
        hand_position=[12, 0, 7],
        hand_height=5,
        hand_option=option,
    )
    if option == "clenched_fist":
        params["hand_target"] = "guard"
    _next(state)
    return state, data, params


def _cast(state, data, params):
    return _run("cast_spell", state, participant_data=data, **params).result["context"]


def _next(state):
    _run("end_combat_turn", state, actor="hero")
    _run("begin_combat_turn", state, actor="hero")


@pytest.mark.parametrize("slot,attack,count", [(5, 10, 5), (7, 20, 18)])
def test_paid_large_nonoccupying_object_uses_real_body_edge_and_upcast_critical_packet(
    slot, attack, count, monkeypatch
):
    state, data, params = _case(slot)
    rolls = Rolls(attack)
    monkeypatch.setattr(dice, "_rng", rolls)
    context = _cast(state, data, params)
    lasting = state[f"{SRD}.state"]
    hand = next(r for r in lasting["spatial_scene"]["entities"] if r["id"] == "hand")
    assert hand["footprint"] == {"kind": "box", "half_extents": [5, 5]}
    assert hand["movement"] == "passable" and hand["visibility"] == "clear"
    assert hand["metadata"]["participant_ref"] is None
    assert hand["metadata"]["arcane_hand"]["armor_class"] == 20
    assert hand["metadata"]["arcane_hand"]["hp"] == 100
    assert "hand" not in state[f"{SRD}.entities"]
    assert rolls.sides.count(8) == count
    assert context["_spell_damage_amount"] == count
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 100 - count
    assert state[f"{SRD}.entities"]["hero"]["resources"][f"spell_slot_{slot}"] == 2
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert (
        lasting["spell_force_records"]["hand"]["cast_id"]
        == state[f"{SRD}.entities"]["hero"]["concentration_cast_id"]
    )


def test_later_bonus_move_uses_saved_slot_and_current_body_then_expiry_retires_exact_proxy():
    state, data, params = _case(7)
    _cast(state, data, params)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="later turn"):
        _run(
            "command_srd_arcane_hand",
            state,
            caster_state="hero",
            force_id="hand",
            destination=[12, 0, 7],
            force_target="guard",
            hand_option="clenched_fist",
        )
    assert state == before
    state = json.loads(json.dumps(state))
    _next(state)
    _run(
        "command_srd_arcane_hand",
        state,
        caster_state="hero",
        force_id="hand",
        destination=[13, 0, 7],
        force_target="guard",
        hand_option="clenched_fist",
    )
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 82
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
    assert state[f"{SRD}.entities"]["hero"]["bonus_action_available"] is False
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=20)
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    assert state[f"{SRD}.state"]["spell_force_records"]["hand"]["active"] is False
    assert (
        next(r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "hand")[
            "active"
        ]
        is False
    )


def test_unsupported_option_and_occupied_creation_fail_atomically_without_payment():
    state, data, params = _case(option="forceful_hand")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="unsupported"):
        _cast(state, data, params)
    assert state == before
    state, data, params = _case(option="none")
    params["hand_position"] = [20, 0, 0]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="unoccupied"):
        _cast(state, data, params)
    assert state == before


@lru_cache
def _archive():
    """Use the native strict archive and one genuinely declared new public command."""
    runtime, encoded = _runtime()
    with patch.object(bundled_packages, "_bundled_srd_root", return_value=PACKAGE_SOURCE):
        primary, reference = bundled_packages._srd_drafts()
    record = deepcopy(primary.record)
    for declaration in record["tools"]:
        if declaration["name"] in runtime["tools"]:
            declaration["input_schema"] = _tool_schema(runtime["tools"][declaration["name"]])
    record["tools"].append(
        {
            "name": "command_srd_arcane_hand",
            "capability": "dnd.srd.5.2.1.spells",
            "input_schema": _tool_schema(runtime["tools"]["command_srd_arcane_hand"]),
            "writers": ["dm"],
            "turn_statuses": ["open"],
            "state_predicates": [],
        }
    )
    payloads = dict(primary.payloads) | {"runtime/ruleset.yaml": encoded}
    content = json.loads(payloads["content/content-declarations.json"])
    content["inclusions"].append(
        {
            "id": "arcane-hand-command",
            "kind": "ruleset_runtime",
            "family": "arcane_hand",
            "disposition": "included_mechanical",
            "classifications": ["sylvia_gm_implementation"],
            "sources": [],
            "links": {
                "artifacts": ["runtime/ruleset.yaml"],
                "capabilities": ["dnd.srd.5.2.1.spells"],
                "tools": ["command_srd_arcane_hand"],
                "mechanics": [],
                "procedures": [],
                "prompt_fragments": [],
                "schemas": [],
                "state_namespaces": [],
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["paid-hand-later-command-reopen-retry"]},
        }
    )
    content["inclusions"].sort(key=lambda row: row["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return bundled_packages._archive((NormalizedPackageDraft(record, payloads, 4), reference))


def test_public_paid_hand_reopens_then_actual_bonus_command_retries_once(tmp_path, monkeypatch):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _spell: _case())
    database = tmp_path / "hand.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(
            owner, "arcane_hand", target_overrides={"hp": 100, "max_hp": 100}
        )
        paid, paid_command = _act(owner, "cast_spell_outside_combat", **params)
        assert paid.status is ActionStatus.COMMITTED
        retained = owner.snapshot("casting")
        assert owner.execute_action(paid_command) == paid
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == retained
        _act(owner, "start_group_combat_encounter", combatants=["hero", "guard", "other"])
        _act(owner, "begin_combat_turn")
        command, command_input = _act(
            owner,
            "command_srd_arcane_hand",
            caster_state="hero",
            force_id="hand",
            destination=[13, 0, 7],
            force_target="guard",
            hand_option="clenched_fist",
        )
        assert command.status is ActionStatus.COMMITTED
        final = owner.snapshot("casting")
        assert final.state[f"{SRD}.entities"]["guard"]["hp"] == 90
        assert final.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
        assert owner.execute_action(command_input) == command
        assert owner.execute_action(paid_command) == paid
        assert owner.snapshot("casting") == final
