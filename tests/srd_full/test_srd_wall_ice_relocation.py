"""Paid Ice formation authenticates its forced receiver without changing ordinary Travel."""

import json
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_environment_wall_ice import bind_movement, bind_objects
from scripts.srd_full.srd_spell_environment import extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime, _RuntimeDumper
from scripts.srd_full.srd_timed_senses import _replace_tools
from scripts.srd_full.srd_wall_ice_relocation import bind_runtime_consumers

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_spell_environment import _position
from tests.srd_full.test_srd_wall_of_ice import _case, _panels

BASE = Path(__file__).resolve().parents[3]
ACTUAL = BASE / "fc14-spells-20260930-wave27/build/rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
ACTUAL_SHA = "1f18ab6bfdf306ed1730403dbd09097b8c5788ddb4877a0caa2416846b59e529"
PARENT = BASE / "fc14-spells-20260930-wave30/baseline"
PARENT_MANIFEST = "b1a3e085f3892be89a433222e321214963c6a22c16da76d3d96d96469d5d3e78"
ROOTS = {
    "cast_spell_outside_combat",
    "push_srd_wall_ice_subject",
    "qualify_srd_wall_ice_push",
    "move_srd_travel_subject",
    "move_srd_scene_creature",
    "apply_srd_condition",
    "advance_srd_lasting_time",
    "cast_srd_spell_secure_area",
    "cast_srd_hallow",
}


def _calls(value):
    if isinstance(value, dict):
        if isinstance(value.get("call"), str):
            yield value["call"]
        for child in value.values():
            yield from _calls(child)
    elif isinstance(value, list):
        for child in value:
            yield from _calls(child)


@lru_cache
def _runtime():
    """Compose the frozen owning source and retain the exact transitive actual tool bodies."""
    raw = ACTUAL.read_bytes()
    assert sha256(raw).hexdigest() == ACTUAL_SHA
    manifest_bytes = (PARENT.parent / "baseline-manifest.json").read_bytes()
    assert sha256(manifest_bytes).hexdigest() == PARENT_MANIFEST
    manifest = json.loads(manifest_bytes)["files"]
    source = PARENT / "rulesets/dnd-srd-5.2.1/content/spell-mechanics.yaml"
    assert (
        sha256(source.read_bytes()).hexdigest()
        == manifest[str(source.relative_to(PARENT)).replace("\\", "/")]
    )
    declarations = _load_runtime(source.read_text())["spells"]
    # Cache source composition only; strict materialization and execution still run normally.
    scripts = Path(__file__).parents[1] / "scripts"
    code = {
        str(p.relative_to(scripts)): sha256(p.read_bytes()).hexdigest()
        for p in scripts.glob("*.py")
    }
    key = sha256(
        json.dumps(
            [
                ACTUAL_SHA,
                PARENT_MANIFEST,
                manifest[str(source.relative_to(PARENT)).replace("\\", "/")],
                sorted(ROOTS),
                "final-ice-consumers-v1",
                code,
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    cache = scripts.parent / ".scratch" / f"ice-receiver-{key}.yaml"
    if cache.exists():
        content = cache.read_bytes()
        return _load_runtime(content.decode()), content
    original = _load_runtime(raw.decode())
    original["definitions"]["spells"]["wall_of_ice"] = declarations["wall_of_ice"]["definition"]
    original["definitions"]["spells"]["wall_of_fire"] = declarations["wall_of_fire"]["definition"]
    selector = next(
        p for p in original["tools"]["cast_spell"]["parameters"] if p["name"] == "spell"
    )
    selector["enum_values"] = sorted(set(selector["enum_values"]) | {"wall_of_ice", "wall_of_fire"})
    text = yaml.dump(original, Dumper=_RuntimeDumper, sort_keys=False)
    text = extend_runtime(text)
    actual = _load_runtime(text)
    assert "check_srd_magic_zone_effect" in actual["tools"]
    # This is the existing final outside-cast delegate with the new real source choices.
    cast = actual["tools"]["cast_spell"]
    outside = deepcopy(actual["tools"]["cast_spell_outside_combat"])
    outside["parameters"] = deepcopy(cast["parameters"])
    outside["pre_conditions"] = deepcopy(cast["pre_conditions"])
    from scripts.srd_full.srd_exploration_cast_delegate import _forward

    outside["effects"] = [
        {
            "call": "cast_spell",
            "params": {p["name"]: _forward(p) for p in cast["parameters"]},
            "export_context": "*",
        }
    ]
    text = _replace_tools(text, {"cast_spell_outside_combat": outside})
    tools = _load_runtime(text)["tools"]
    source_consumers = {
        name: deepcopy(tools[name])
        for name in (
            "refresh_srd_environment_mode_indices",
            "resolve_srd_environment_point",
            "resolve_srd_environment_segment",
            "retire_srd_environment_ground_source",
            "srd_information_barriers",
        )
    }
    text = _replace_tools(text, {**bind_objects(tools), **bind_movement(tools)})
    before = _load_runtime(text)["tools"]
    text = bind_runtime_consumers(text)
    actual = _load_runtime(text)
    for name in ("move_srd_travel_subject", "move_srd_travel_subject_node"):
        assert actual["tools"][name] == before[name]
    for name, tool in source_consumers.items():
        assert actual["tools"][name] == tool
    assert bind_runtime_consumers(text) == text
    selected, pending = set(), list(ROOTS)
    while pending:
        name = pending.pop()
        if name not in selected:
            assert name in actual["tools"], name
            selected.add(name)
            pending.extend(_calls(actual["tools"][name]))
    scoped = deepcopy(actual)
    scoped["tools"] = {name: tool for name, tool in actual["tools"].items() if name in selected}
    assert all(scoped["tools"][name] == actual["tools"][name] for name in selected)
    content = yaml.dump(scoped, Dumper=_RuntimeDumper, sort_keys=False).encode()
    cache.parent.mkdir(exist_ok=True)
    cache.write_bytes(content)
    return scoped, content


@lru_cache
def _mechanic(name):
    runtime, content = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=content,
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
def _authored(monkeypatch):
    runtime, _ = _runtime()
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: runtime["definitions"]["spells"])
    from sylvia_gm.application import package_runtime

    execute = package_runtime.execute_tool

    def observed(*args, **kwargs):
        result = execute(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", observed)


def _occupied(*, side="positive", axis="north_south"):
    state, data, params = _case(axis=axis)
    state[f"{SRD}.state"].setdefault("lasting_tick", 0)
    _position(state, "guard")["position"] = [20, -5, 0] if axis == "north_south" else [15, 0, 0]
    params["environment_ice_push_side"] = side
    return state, data, params


def test_paid_occupied_formation_uses_actual_chosen_face_and_once_damage():
    state, data, params = _occupied()
    result = _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert result.result["status"] == "committed"
    position = _position(state, "guard")["position"]
    assert position[0] > 20.5 and position[1:] == [-5, 0]
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["appearance_completed"] and not source["appearance_in_progress"]
    assert len(source["formation_targets"]) == 1
    assert len(_panels(state)) == 10
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2
    assert state[f"{SRD}.state"]["wall_ice_pending_push"] is None


@pytest.mark.parametrize(
    "axis,side", [("north_south", "negative"), ("east_west", "positive"), ("east_west", "negative")]
)
def test_actual_full_body_clears_the_selected_source_face(axis, side):
    state, data, params = _occupied(axis=axis, side=side)
    body = _position(state, "guard")
    original = deepcopy(body["position"])
    extents = body["footprint"]["half_extents"]
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    current = _position(state, "guard")["position"]
    dimension = 0 if axis == "north_south" else 1
    center = 20 if dimension == 0 else 0
    if side == "positive":
        assert current[dimension] - extents[dimension] >= center + 0.5
    else:
        assert current[dimension] + extents[dimension] <= center - 0.5
    assert current[1 - dimension] == original[1 - dimension] and current[2] == original[2]


@pytest.mark.parametrize("condition", ["stunned", "restrained"])
def test_source_push_moves_restricted_receiver_without_spending_its_budget(condition):
    state, data, params = _occupied()
    _run("apply_srd_condition", state, target="guard", condition=condition)
    guard = state[f"{SRD}.entities"]["guard"]
    budget_before = {
        name: deepcopy(guard.get(name))
        for name in (
            "movement_spent",
            "movement_remaining",
            "action_available",
            "bonus_action_available",
            "reaction_available",
        )
    }
    tick = state[f"{SRD}.state"]["lasting_tick"]
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    guard = state[f"{SRD}.entities"]["guard"]
    assert condition in guard["conditions"]
    assert _position(state, "guard")["position"][0] >= 20.9
    assert {name: guard.get(name) for name in budget_before} == budget_before
    assert state[f"{SRD}.state"]["lasting_tick"] == tick


def test_blocked_actual_body_sweep_rejects_atomically_without_cost():
    state, data, params = _occupied()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["geometry"].append(
        {
            "id": "solid-east",
            "map_id": scene["map"]["id"],
            "volume": {"kind": "box", "min": [21, -1000, 0], "max": [22, 1000, 10]},
            "active": True,
            "movement": "blocking",
            "occupies_cells": False,
            "visibility": "opaque",
            "metadata": {"plane": "material", "material": "stone"},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before


def test_forced_leaf_does_not_admit_teleport_or_other_spell_source_bypass_ids():
    runtime, _ = _runtime()
    for name in ("push_srd_wall_ice_subject", "push_srd_wall_ice_subject_node"):
        parameters = {p["name"] for p in runtime["tools"][name]["parameters"]}
        assert not parameters.intersection(
            {
                "teleport",
                "levitate_source_id",
                "telekinesis_source_id",
                "sphere_source_id",
                "force",
                "magical",
                "voluntary",
            }
        )


def _fiend_receiver(state):
    state[f"{SRD}.entities"]["guard"]["creature_type"] = "fiend"
    _position(state, "guard")["metadata"]["creature_type"] = "fiend"


@pytest.mark.parametrize("ward", ["circle", "hallow"])
def test_source_owned_voluntary_ward_does_not_block_printed_involuntary_receiver(ward):
    from tests import test_srd_spell_secure_areas as secure

    state, data, params = _occupied()
    _fiend_receiver(state)
    _position(state, "other")["position"] = [30.8, -5, 0]
    if ward == "circle":
        secure._cast(state, "magic_circle", caster_state="other", origin=[30.8, -5, 0])
        query = "check_srd_secure_area_movement"
        exported = "_secure_area_movement_allowed"
    else:
        _run(
            "cast_srd_hallow",
            state,
            caster_state="other",
            slot_level=5,
            origin=[30.8, -5, 0],
            radius=10,
            ward_types=["fiend"],
            caster_can_touch_origin=True,
        )
        query, exported = "check_srd_hallow_movement", "_hallow_movement_allowed"
    blocked = _run(query, state, subject="guard", destination=[20.9, -5, 0], plane="material")
    assert blocked.result["context"][exported] is False
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert _position(state, "guard")["position"][0] >= 20.9
    assert state[f"{SRD}.state"]["wall_ice_pending_push"] is None


def test_real_forbiddance_entry_packet_follows_the_actual_forced_position_once(monkeypatch):
    from tests import test_srd_spell_secure_areas as secure
    from tests.srd_full.test_srd_spell_damage_consumers import _record_rolls

    state, data, params = _occupied()
    _fiend_receiver(state)
    _position(state, "other")["position"] = [20.5, -10, 0]
    sid, _ = secure._cast(
        state,
        "forbiddance",
        caster_state="other",
        origin=[20.5, -10, 0],
        floor_width=10,
        floor_length=10,
    )
    assert not _run(
        "qualify_srd_secure_area_subject", state, source_id=sid, subject="guard"
    ).result["context"]["_secure_area_subject_inside"]
    rolls = _record_rolls(monkeypatch)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    radiant = [roll for roll in rolls if roll.sides == 10]
    assert len(radiant) == 1 and radiant[0].count == 5
    receipt = state[f"{SRD}.state"]["spell_secure_area_sources"][sid]["entry_damage_turns"]["guard"]
    assert receipt["clock"] == "round"
    assert receipt["turn"] == state[f"{SRD}.state"]["lasting_tick"] // 2
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    hp = state[f"{SRD}.entities"]["guard"]["hp"]
    assert hp == 1000 - radiant[0].total - source["appearance_raw_amount"] // 2
    assert _position(state, "guard")["position"][0] >= 20.9
    repeated = _run(
        "resolve_srd_forbiddance_event",
        state,
        target_state="guard",
        event="entry",
        origin=[20, -5, 0],
        origin_plane="material",
    )
    assert repeated.result["context"]["_secure_forbiddance_packets"] == []
    assert state[f"{SRD}.entities"]["guard"]["hp"] == hp


def test_ordinary_call_cannot_forge_or_reuse_a_forced_receipt():
    state, data, params = _occupied()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "push_srd_wall_ice_subject",
            state,
            subject="guard",
            destination=[23, -5, 0],
            plane="material",
        )
    assert state == before
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    endpoint = deepcopy(_position(state, "guard")["position"])
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "push_srd_wall_ice_subject",
            state,
            subject="guard",
            destination=endpoint,
            plane="material",
        )
    assert state == before


@lru_cache
def _archive():
    """Strictly admit this selected real owner closure with its genuine public schema."""
    from sylvia_gm.application.bundled_packages import _archive as archive_bytes
    from sylvia_gm.application.package_materialization import NormalizedPackageDraft
    from sylvia_gm.domain.package_library import canonical_json_bytes
    from tests import test_srd_dream_spell as dream

    primary, reference = dream._linked_drafts()
    record = deepcopy(dict(primary.record))
    payloads = dict(primary.payloads)
    runtime, content = _runtime()
    record["tools"] = [row for row in record["tools"] if row["name"] in runtime["tools"]]
    names = {row["name"] for row in record["tools"]}
    for surface in record.get("ui_surfaces", []):
        surface["actions"] = [action for action in surface["actions"] if action["intent"] in names]
    for row in record["tools"]:
        row["input_schema"] = _tool_schema(runtime["tools"][row["name"]], tool_name=row["name"])
    payloads["runtime/ruleset.yaml"] = content
    declarations = json.loads(payloads["content/content-declarations.json"])
    for inclusion in declarations["inclusions"]:
        if "tools" in inclusion.get("links", {}):
            inclusion["links"]["tools"] = [
                name for name in inclusion["links"]["tools"] if name in names
            ]
    payloads["content/content-declarations.json"] = canonical_json_bytes(declarations)
    return archive_bytes((NormalizedPackageDraft(record, payloads, 4), reference))


def test_strict_paid_public_push_reopens_and_cast_retry_cannot_repeat_push_or_damage(
    tmp_path, monkeypatch
):
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from sylvia_gm.domain.errors import MechanicsRejected
    from tests import test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_npc_spell_casting import _act

    monkeypatch.setattr(public, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public, "_spell_case", lambda _spell: _occupied())
    database = tmp_path / "ice-receiver.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner, "wall_of_ice", target_overrides={"hp": 1000, "max_hp": 1000}
        )
        names = {tool.name for tool in StatelessMcpMechanics(owner).list_tools("casting")}
        assert "cast_spell_outside_combat" in names
        assert "push_srd_wall_ice_subject" not in names
        result, command = _act(owner, "cast_spell_outside_combat", **params)
        assert result.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        source = saved.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
        assert source["appearance_settled_ids"] == ["guard"]
        assert _position(saved.state, "guard")["position"][0] >= 20.9
        assert saved.state[f"{SRD}.entities"]["guard"]["hp"] < 1000
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2
        assert StatelessMcpMechanics(owner).execute_action(command) == result
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting").state == saved.state
        assert StatelessMcpMechanics(reopened).execute_action(command) == result
        assert reopened.snapshot("casting").state == saved.state
        assert saved.state[f"{SRD}.state"]["has_wall_of_ice"] is True
        with pytest.raises(MechanicsRejected, match="intact Ice section"):
            _act(
                reopened,
                "move_srd_scene_creature",
                actor_state="guard",
                destination=[15, -5, 0],
            )
        assert reopened.snapshot("casting").state == saved.state
        hp = saved.state[f"{SRD}.entities"]["guard"]["hp"]
        elapsed, elapsed_command = _act(reopened, "advance_srd_lasting_time", elapsed_rounds=1)
        assert elapsed.status is ActionStatus.COMMITTED
        later = reopened.snapshot("casting")
        assert later.state[f"{SRD}.entities"]["guard"]["hp"] == hp
        assert later.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"][
            "appearance_settled_ids"
        ] == ["guard"]
        assert StatelessMcpMechanics(reopened).execute_action(elapsed_command) == elapsed
        assert reopened.snapshot("casting").state == later.state
