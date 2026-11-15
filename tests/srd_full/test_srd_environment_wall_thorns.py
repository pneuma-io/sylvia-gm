"""Actual Thorn appearance, later saves, literal movement cost and saved source lifetime."""

import hashlib
import json
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts import srd_spell_environment as environment
from scripts.srd_full.srd_actor_spell_modifiers import _emit
from scripts.srd_full.srd_environment_wall_thorns import PROFILE, SOURCE_SHA256, SPELL, extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_solid_spell_walls as force

ROOT, SRD = force.ROOT, force.SRD
BASE = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-environment-wall-thorns/ruleset.yaml")
)


def _header():
    corpus = json.loads((ROOT / "rulesets/dnd-srd-5.2.1/content/corpus/spells.json").read_text())
    page = next(p for p in corpus["page_records"] if p["page"] == 173)
    assert page["text_sha256"] == SOURCE_SHA256
    lines = [(173, line) for line in page["text"].splitlines()]
    start = next(i for i, (_, line) in enumerate(lines) if line.strip() == "Wall of Thorns")
    return {"name": "Wall of Thorns", **force._casting_requirements(lines, start + 1, SPELL)}


class NoAliases(getattr(yaml, "CSafeDumper", yaml.SafeDumper)):
    def ignore_aliases(self, data):
        return True


@lru_cache
def _runtime():
    raw = BASE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "8499f04a288bd35c92302c21cd01345c43111c9223b60f91a032af071ac62d07"
    )
    runtime = _load_runtime(raw.decode())
    runtime["definitions"]["spells"][SPELL] = PROFILE
    runtime["definitions"]["spell_casting"][SPELL] = _header()
    # Exact captured Environment supplies its real Fire constructor and body/save owners.
    assert hashlib.sha256((ROOT / "scripts/srd_spell_environment.py").read_bytes()).hexdigest() == (
        "60453c00f2830b57a49fa024616ac6349d09b4a764dbf4bc01ef65faf3753b48"
    )
    changed = environment._tools()
    changed.update(environment._dispel_tools(runtime["tools"]))
    changed["srd_information_movement_cost"] = deepcopy(
        runtime["tools"]["srd_information_movement_cost"]
    )
    changed["d20_test"] = deepcopy(runtime["tools"]["d20_test"])
    environment._bind_sphere_hazards(changed)
    environment._bind_blade_barrier(changed)
    environment._bind_wall_of_fire(changed)
    runtime["tools"].update(changed)
    for tool in runtime["tools"].values():
        for p in tool["parameters"]:
            if p["name"] == "spell" and "enum_values" in p:
                p["enum_values"] = sorted(set(p["enum_values"]) | {SPELL})
    text = _emit(yaml.dump(runtime, Dumper=NoAliases, sort_keys=False), changed)
    text = extend_runtime(text)
    assert extend_runtime(text) == text
    runtime = _load_runtime(text)
    runtime["definition_row_schemas"]["definitions.spells"]["variants"]["spell_environment"] = (
        environment._schema()
    )
    runtime["tools"]["cast_spell_outside_combat"] = deepcopy(runtime["tools"]["cast_spell"])
    selected, pending = (
        {},
        [
            "cast_spell",
            "cast_spell_outside_combat",
            "cast_spell_environment",
            "resolve_srd_wall_of_thorns_event",
            "srd_information_movement_cost",
            "resolve_srd_environment_segment",
            "resolve_srd_environment_hazard_entry",
            "resolve_srd_environment_hazard_event",
            "resolve_srd_environment_hazard_end_turn",
            "move_srd_scene_creature_environment_segment",
            "move_srd_scene_creature",
            "advance_srd_lasting_time",
        ],
    )
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        selected[name] = runtime["tools"][name]
        stack = [selected[name]]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if "call" in node:
                    pending.append(node["call"])
                for kind in ("spatial_query", "spatial_mutation"):
                    if kind in node:
                        node[kind]["scene"] = re.sub(
                            r"^lasting_state\.", "state.", node[kind]["scene"]
                        )
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    runtime["tools"] = selected
    assert not (errors := validate_ruleset_data(runtime)), errors
    raw = yaml.dump(runtime, Dumper=NoAliases, sort_keys=False).encode()
    (ROOT / ".scratch/thorn-wall-proof-runtime.yaml").write_bytes(raw)
    return raw, runtime


@lru_cache
def _mechanic(name):
    raw, runtime = _runtime()
    return force.materialize_package_mechanic(
        package_id=SRD,
        package_version="5.2.1",
        package_sha256="c" * 64,
        runtime_bytes=raw,
        declaration=force.ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=force.owned_payload(
                force._tool_schema(runtime["tools"][name], tool_name=name)
            ),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    monkeypatch.setattr(force.objects, "_mechanic", _mechanic)
    monkeypatch.setattr(force.actions, "_mechanic", _mechanic)


def _state():
    state = force.objects._state()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    for actor, pos in [
        ("hero", [-20, -20, 0]),
        ("guard", [0, 0, 0]),
        ("other", [20, -5, 0]),
        ("door", [50, 50, 0]),
    ]:
        force.wall._position(state, actor)["position"] = pos
    scene["geometry"].append(
        {
            "id": "real_ground",
            "map_id": "relation",
            "volume": {"kind": "box", "min": [-60, -60, -1], "max": [60, 60, 0]},
            "active": True,
            "movement": "blocking",
            "visibility": "opaque",
            "metadata": {"plane": "material", "ground": True},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    )
    scene["zones"] = [
        {
            "id": "ambient",
            "map_id": "relation",
            "shape": {"kind": "box", **deepcopy(scene["map"]["bounds"])},
            "metadata": {"plane": "material", "lighting": "bright"},
        }
    ]
    for actor in ("hero", "guard", "other"):
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"] = {"dexterity": 0}
        state[f"{SRD}.entities"][actor].update(hp=300, max_hp=300, temp_hp=0, is_alive=True)
    return state


def _cast(state, level=6):
    force.objects._run(
        "cast_spell_environment",
        state,
        caster_state="hero",
        spell=SPELL,
        slot_level=level,
        concentration_save_modifier=100,
        origin=[0, 0, 0],
        save_dc=30,
        wall_axis="east_west",
        thorn_wall_length=20,
    )
    sid = state[f"{SRD}.entities"]["hero"]["environment_area_id"]
    return sid


def test_real_appearance_dex_piercing_and_saved_box(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(8))
    state = _state()
    sid = _cast(state, 7)
    source = state[f"{SRD}.state"]["persistent_spell_areas"][sid]
    guard = state[f"{SRD}.entities"]["guard"]
    assert source["volume"] == {"kind": "box", "min": [-10, -2.5, 0], "max": [10, 2.5, 10]}
    assert source["source_sha256"] == SOURCE_SHA256
    assert source["initial_damage_roll"]["count"] == 8
    assert guard["hp"] == 300 - source["initial_damage_amount"]
    assert guard["last_wall_of_thorns_save"]["roll"] is not None
    assert guard["last_wall_of_thorns_save"]["timing"] == "initial"
    assert guard["last_wall_of_thorns_save"]["outcome"] == "failure"


def test_later_entry_and_end_share_one_save_and_slashing_packet_after_reopen(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(8))
    state = _state()
    state[f"{SRD}.entities"]["guard"]["damage_immunities"] = ["piercing"]
    sid = _cast(state)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 300
    reopened = json.loads(json.dumps(state))
    combat = reopened[f"{SRD}.combat"]
    combat.update(encounter_active=True, turn_active=True, active_actor="guard", turn_sequence=1)
    force.objects._run(
        "resolve_srd_environment_hazard_event", reopened, target_state="guard", area_id=sid
    )
    guard = reopened[f"{SRD}.entities"]["guard"]
    assert guard["hp"] < 300  # Piercing immunity does not erase later Slashing.
    assert guard["last_wall_of_thorns_save"]["timing"] == "entry"
    assert guard["last_wall_of_thorns_save"]["roll"] is not None
    before = deepcopy(reopened)
    monkeypatch.setattr(dice, "roll", lambda *a, **kw: pytest.fail("Repeated Thorn trigger rolled"))
    force.objects._run("resolve_srd_environment_hazard_end_turn", reopened, target_state="guard")
    force.objects._run(
        "resolve_srd_environment_hazard_event", reopened, target_state="guard", area_id=sid
    )
    assert reopened == before


@pytest.mark.parametrize("saved", [False, True])
def test_later_actual_dex_save_half_and_new_turn(monkeypatch, saved):
    state = _state()
    _cast(state)
    guard = state[f"{SRD}.entities"]["guard"]
    guard["saving_throw_modifiers"]["dexterity"] = 100 if saved else -100
    state[f"{SRD}.combat"].update(
        encounter_active=True, turn_active=True, active_actor="guard", turn_sequence=2
    )
    monkeypatch.setattr(dice, "_rng", Random(11))
    before = guard["hp"]
    force.objects._run("resolve_srd_environment_hazard_end_turn", state, target_state="guard")
    saved_result = state[f"{SRD}.entities"]["guard"]["last_wall_of_thorns_save"]
    assert saved_result["outcome"] == ("success" if saved else "failure")
    assert saved_result["timing"] == "end"
    amount = saved_result["raw_damage"]["total"]
    assert before - state[f"{SRD}.entities"]["guard"]["hp"] == (amount // 2 if saved else amount)


@pytest.mark.parametrize(
    "difficult,ignore_ground,expected",
    [
        (False, False, 4),
        (True, False, 5),
        (False, True, 4),
    ],
)
def test_literal_thorns_cost_is_not_difficult_terrain(
    monkeypatch, difficult, ignore_ground, expected
):
    state = _state()
    _cast(state)
    monkeypatch.setattr(dice, "roll", lambda *a, **kw: pytest.fail("Cost rolled dice"))
    cost = force.objects._run(
        "srd_information_movement_cost",
        state,
        start=[0, 0, 0],
        end=[1, 0, 0],
        plane="material",
        actor_subject="guard",
        base_difficult=difficult,
        ignore_ground_environments=ignore_ground,
    )
    assert cost["_route_cost"] == pytest.approx(expected)


def test_inside_movement_is_a_real_later_entry_and_expiry_restores_cost_and_sight():
    state = _state()
    sid = _cast(state)
    entry = force.objects._run(
        "resolve_srd_environment_hazard_entry", state, actor_state="guard", destination=[1, 0, 0]
    )
    assert entry["_hazard_entry_events"] == [{"area_id": sid, "target_id": "guard"}]
    assert 0 < entry["_hazard_entry_t"] < 1
    sight = force.objects._run(
        "resolve_srd_environment_segment", state, start=[0, -5, 3], end=[0, 5, 3], plane="material"
    )
    assert sight["_environment_sight_obscured"] is True
    force.objects._run("advance_srd_lasting_time", state, elapsed_rounds=100)
    sight = force.objects._run(
        "resolve_srd_environment_segment", state, start=[0, -5, 3], end=[0, 5, 3], plane="material"
    )
    assert sight["_environment_sight_obscured"] is False
    cost = force.objects._run(
        "srd_information_movement_cost",
        state,
        start=[0, 0, 0],
        end=[1, 0, 0],
        plane="material",
        actor_subject="guard",
        base_difficult=False,
    )
    assert cost["_route_cost"] == 1


def test_unsupported_ring_or_absent_solid_surface_rolls_back_source_and_concentration():
    state = _state()
    before = deepcopy(state)
    with pytest.raises(force.RulesetPreconditionRejected, match="ring"):
        force.objects._run(
            "cast_spell_environment",
            state,
            caster_state="hero",
            spell=SPELL,
            slot_level=6,
            concentration_save_modifier=0,
            origin=[0, 0, 0],
            save_dc=10,
            thorn_wall_shape="ring",
        )
    assert state == before


def test_paid_named_druid_thorns_spends_slot_action_and_retains_material_and_saved_damage(
    monkeypatch,
):
    scene = deepcopy(_state()[f"{SRD}.state"]["spatial_scene"])
    monkeypatch.setattr(force.cards, "_casting_headers", lambda: {SPELL: _header()})
    monkeypatch.setattr(force.cards, "_spell_definitions", lambda: {SPELL: PROFILE})
    state, data, parameters = force.cards._spell_case(SPELL)
    assert data["classes"][0]["id"] == "druid"
    state[f"{SRD}.state"]["spatial_scene"] = scene
    for actor in ("guard", "other"):
        state[f"{SRD}.entities"][actor].update(hp=300, max_hp=300, temp_hp=0)
    result = force.actions._run(
        "cast_spell",
        state,
        participant_data=data,
        **(
            parameters
            | {"target": None, "environment_origin": [0, 0, 0], "environment_thorn_wall_length": 20}
        ),
    )
    assert result.result["status"] == "committed"
    actor = state[f"{SRD}.entities"]["hero"]
    sid = actor["environment_area_id"]
    source = state[f"{SRD}.state"]["persistent_spell_areas"][sid]
    assert source["spell"] == SPELL and source["spell_level"] == 6
    assert source["save_dc"] == 18  # Actual level17Druid Wisdom18 and PB6.
    assert actor["resources"]["spell_slot_6"] == 2
    assert actor["spell_materials"][0]["quantity"] == 3
    assert state[f"{SRD}.combat"]["action_available"] is False
    reopened = json.loads(json.dumps(state))
    reopened[f"{SRD}.combat"].update(active_actor="guard", turn_sequence=2)
    force.objects._run("resolve_srd_environment_hazard_end_turn", reopened, target_state="guard")
    assert reopened[f"{SRD}.entities"]["guard"]["last_wall_of_thorns_save"]["timing"] == "end"
    before = deepcopy(reopened)
    monkeypatch.setattr(dice, "roll", lambda *a, **kw: pytest.fail("Retry rolled"))
    force.objects._run("resolve_srd_environment_hazard_end_turn", reopened, target_state="guard")
    assert reopened == before


def test_actual_committed_inside_movement_spends_four_and_applies_later_damage():
    state = _state()
    sid = _cast(state)
    before = state[f"{SRD}.entities"]["guard"]["hp"]
    force.objects._run(
        "move_srd_scene_creature", state, actor_state="guard", destination=[1, 0, 0], mode="walk"
    )
    assert force.wall._position(state, "guard")["position"] == [1, 0, 0]
    assert state[f"{SRD}.entities"]["guard"]["scene_movement_spent"] == pytest.approx(4)
    assert state[f"{SRD}.entities"]["guard"]["hp"] < before
    source = state[f"{SRD}.state"]["persistent_spell_areas"][sid]
    assert source["later_targets"] == ["guard"]


def test_two_real_overlapping_casts_union_literal_cost():
    state = _state()
    _cast(state)
    force.objects._run(
        "cast_spell_environment",
        state,
        caster_state="other",
        spell=SPELL,
        slot_level=6,
        concentration_save_modifier=100,
        origin=[0, 0, 0],
        save_dc=30,
        thorn_wall_length=20,
    )
    assert len(state[f"{SRD}.state"]["persistent_spell_area_ids"]) == 2
    cost = force.objects._run(
        "srd_information_movement_cost",
        state,
        start=[0, 0, 0],
        end=[1, 0, 0],
        plane="material",
        actor_subject="guard",
        base_difficult=False,
    )
    assert cost["_route_cost"] == pytest.approx(4)
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = []
    before = deepcopy(state)
    with pytest.raises(force.RulesetPreconditionRejected, match="ground"):
        _cast(state)
    assert state == before
