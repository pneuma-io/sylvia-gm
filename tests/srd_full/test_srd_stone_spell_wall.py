"""Printed Stone support, actual panel damage, Concentration ending and permanence."""

import json
import os
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.srd_solid_spell_walls import POOL, bind_runtime_consumers
from scripts.srd_full.srd_solid_spell_walls import PROFILES as FORCE_PROFILES
from scripts.srd_full.srd_solid_spell_walls import extend_runtime as extend_force
from scripts.srd_full.srd_stone_spell_wall import PROFILES, SOURCE_SHA256, SPELL, extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_solid_spell_walls as force

SRD = force.SRD
ROOT = force.ROOT


def _header():
    corpus = json.loads((ROOT / "rulesets/dnd-srd-5.2.1/content/corpus/spells.json").read_text())
    page = next(p for p in corpus["page_records"] if p["page"] == 173)
    assert page["text_sha256"] == SOURCE_SHA256
    lines = [(173, line) for line in page["text"].splitlines()]
    start = next(i for i, (_, line) in enumerate(lines) if line.strip() == "Wall of Stone")
    return {
        "name": "Wall of Stone",
        **force._casting_requirements(lines, start + 1, SPELL),
    }


@lru_cache
def _runtime():
    supplied = os.environ.get("FC14_STONE_WALL_OWNER_RUNTIME")
    if supplied:
        raw = Path(supplied).read_bytes()
        runtime = _load_runtime(raw.decode())
        assert runtime["definitions"]["spells"][SPELL] == PROFILES[SPELL]
        assert runtime["definitions"]["spell_casting"][SPELL] == _header()
        assert not (errors := validate_ruleset_data(runtime)), errors
        return raw, runtime
    runtime = _load_runtime(force.BASE.read_text())
    runtime["definitions"]["spells"].update(FORCE_PROFILES | PROFILES)
    runtime["definitions"]["spell_casting"].update(
        wall_of_force=force._header(), wall_of_stone=_header()
    )
    for tool in runtime["tools"].values():
        for p in tool["parameters"]:
            if p["name"] == "spell" and "enum_values" in p:
                p["enum_values"] = sorted(
                    set(p["enum_values"]) | set(PROFILES) | set(FORCE_PROFILES)
                )

    class NoAliases(getattr(yaml, "CSafeDumper", yaml.SafeDumper)):
        def ignore_aliases(self, data):
            return True

    text = extend_force(yaml.dump(runtime, Dumper=NoAliases, sort_keys=False))
    text = extend_runtime(text)
    assert extend_runtime(text) == text
    source = force._source_parent()
    source.INSTALLERS.add("cast_srd_stone_spell_wall")
    text = source.extend_runtime(text)
    text = bind_runtime_consumers(text)
    runtime = _load_runtime(text)
    runtime["tools"]["cast_spell_outside_combat"] = deepcopy(runtime["tools"]["cast_spell"])
    selected, pending = (
        {},
        [
            "cast_spell",
            "cast_spell_outside_combat",
            "cast_srd_stone_spell_wall",
            "advance_srd_lasting_time",
            "end_srd_solid_spell_wall_source",
            "resolve_srd_spell_object_attack",
            "move_srd_scene_creature_environment_segment",
            "dismiss_srd_spell",
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
    (ROOT / ".scratch/stone-wall-proof-runtime.yaml").write_bytes(raw)
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
    for actor, position in (
        ("hero", [-20, -20, 0]),
        ("guard", [5, -5, 0]),
        ("other", [20, -5, 0]),
        ("door", [0, -30, 0]),
    ):
        force.wall._position(state, actor)["position"] = position
    state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(
        {
            "id": "real_stone_foundation",
            "map_id": "relation",
            "volume": {"kind": "box", "min": [-60, -2, -1], "max": [60, 2, 0]},
            "active": True,
            "movement": "blocking",
            "visibility": "opaque",
            "metadata": {"material": "stone", "plane": "material", "ground": True},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    )
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["zones"] = [
        {
            "id": "real_ambient",
            "map_id": scene["map"]["id"],
            "shape": {"kind": "box", **deepcopy(scene["map"]["bounds"])},
            "metadata": {"plane": "material", "lighting": "bright"},
        }
    ]
    return state


def _case():
    state = _state()
    force.objects._run(
        "cast_srd_stone_spell_wall",
        state,
        caster_state="hero",
        spell=SPELL,
        slot_level=5,
        concentration_save_modifier=0,
        origin=[0, 0, 5],
    )
    sid = state[f"{SRD}.entities"]["hero"]["solid_wall_source_id"]
    return state, sid


def test_actual_stone_support_is_required_and_no_source_or_cost_is_faked():
    state = _state()
    state[f"{SRD}.state"]["spatial_scene"]["geometry"][0]["metadata"]["material"] = "wood"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="stone support"):
        force.objects._run(
            "cast_srd_stone_spell_wall",
            state,
            caster_state="hero",
            spell=SPELL,
            slot_level=5,
            concentration_save_modifier=0,
            origin=[0, 0, 5],
        )
    assert state == before


def test_actual_panel_attack_hp_breach_changes_only_its_solid_body(monkeypatch):
    state, sid = _case()
    source = state[f"{SRD}.state"][POOL][sid]
    panel = source["panel_ids"][5]
    assert force.wall._position(state, panel)["metadata"]["srd_spell_object"]["hp"] == 180
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Force panel"):
        force.objects._run(
            "move_srd_scene_creature_environment_segment",
            state,
            actor_state="guard",
            destination=[5, 5, 0],
        )
    assert state == before
    # The real private object consumer derives one D20 and actual rolled damage.
    monkeypatch.setattr(dice, "_rng", Random(5))
    hit = force.objects._run(
        "resolve_srd_spell_object_attack",
        state,
        caster_state="hero",
        object_id=panel,
        attack_modifier=100,
        damage_die="d12",
        dice_count=100,
        damage_type="bludgeoning",
        max_range_feet=120,
    )
    assert hit["_object_damage_roll"] is not None and hit["_object_damage_rolled"] >= 180
    assert hit["_object_damage_applied"] == 180 and hit["_object_hp_after"] == 0
    assert force.wall._position(state, panel)["active"] is False
    assert all(
        force.wall._position(state, other)["active"]
        for other in source["panel_ids"]
        if other != panel
    )
    force.objects._run(
        "move_srd_scene_creature_environment_segment",
        state,
        actor_state="guard",
        destination=[5, 5, 0],
    )
    assert force.wall._position(state, "guard")["position"] == [5, 5, 0]


def test_real_full_concentration_latches_permanent_but_early_end_vanishes():
    state, sid = _case()
    force.objects._run("advance_srd_lasting_time", state, elapsed_rounds=99)
    assert state[f"{SRD}.state"][POOL][sid]["permanent"] is False
    reopened = json.loads(json.dumps(state))
    force.objects._run("advance_srd_lasting_time", reopened, elapsed_rounds=1)
    source = reopened[f"{SRD}.state"][POOL][sid]
    assert source["permanent"] is True and source["installed"] is True
    assert source["expires_tick"] is None and source["dispel_ongoing"] is False
    assert reopened[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    reopened[f"{SRD}.entities"]["hero"].update(hp=0, is_alive=False)
    force.objects._run("advance_srd_lasting_time", reopened, elapsed_rounds=1000)
    assert all(force.wall._position(reopened, panel)["active"] for panel in source["panel_ids"])
    early, early_id = _case()
    force.objects._run("end_srd_solid_spell_wall_source", early, source_id=early_id)
    assert early[f"{SRD}.state"][POOL][early_id]["installed"] is False
    assert all(
        not force.wall._position(early, p)["active"]
        for p in early[f"{SRD}.state"][POOL][early_id]["panel_ids"]
    )


@pytest.mark.parametrize("damage_type", ["poison", "psychic"])
def test_real_object_hit_keeps_printed_stone_immunity_and_panel_hp(monkeypatch, damage_type):
    state, sid = _case()
    panel = state[f"{SRD}.state"][POOL][sid]["panel_ids"][5]
    monkeypatch.setattr(dice, "_rng", Random(5))
    hit = force.objects._run(
        "resolve_srd_spell_object_attack",
        state,
        caster_state="hero",
        object_id=panel,
        attack_modifier=100,
        damage_die="d8",
        dice_count=4,
        damage_type=damage_type,
        max_range_feet=120,
    )
    assert hit["_object_damage_roll"] is not None
    assert hit["_object_damage_applied"] == 0 and hit["_object_hp_after"] == 180
    assert force.wall._position(state, panel)["active"] is True


def test_paid_named_stone_cast_uses_its_actual_material_slot_and_finite_source(monkeypatch):
    scene = deepcopy(_state()[f"{SRD}.state"]["spatial_scene"])
    monkeypatch.setattr(force.cards, "_casting_headers", lambda: {SPELL: _header()})
    monkeypatch.setattr(force.cards, "_spell_definitions", lambda: PROFILES)
    state, data, parameters = force.cards._spell_case(SPELL)
    state[f"{SRD}.state"]["spatial_scene"] = scene
    result = force.actions._run(
        "cast_spell",
        state,
        participant_data=data,
        **(parameters | {"target": None, "solid_wall_origin": [0, 0, 5]}),
    )
    assert result.result["status"] == "committed"
    actor = state[f"{SRD}.entities"]["hero"]
    source = state[f"{SRD}.state"][POOL][actor["solid_wall_source_id"]]
    assert source["spell_id"] == SPELL and source["source_sha256"] == SOURCE_SHA256
    assert source["source_caster_id"] == "hero" and source["spell_level"] == 5
    assert source["cast_id"] == actor["last_spell_source_cast_id"]
    assert actor["resources"]["spell_slot_5"] == 2
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert actor["spell_materials"][0]["quantity"] == 3
    assert source["cast_id"] in str(result.result["result_card"])
