"""Completed paid Mansion expiry returns actual saved occupants through final owners."""

from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_mansion_returns import bind_runtime_consumers
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime, _RuntimeDumper

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_dwelling as dwelling
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_spell_travel import SRD, _point

BASE = Path(__file__).resolve().parents[3]
MANSION = BASE / "fc14-spells-20260930-wave8/portals/.scratch/dwelling-composition/runtime.yaml"
MANSION_SHA = "77e0ef1cf54bab32719223c83ec9c78f386cea6ac35f61272279c8e540a9b22c"
ACTUAL = BASE / "fc14-spells-20260930-wave27/build/rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
ACTUAL_SHA = "1f18ab6bfdf306ed1730403dbd09097b8c5788ddb4877a0caa2416846b59e529"


def _calls(value):
    if isinstance(value, dict):
        if isinstance(value.get("call"), str):
            yield value["call"]
        for child in value.values():
            yield from _calls(child)
    elif isinstance(value, list):
        for child in value:
            yield from _calls(child)


def _closure(tools, roots):
    selected, pending = set(), list(roots)
    while pending:
        name = pending.pop()
        if name not in selected:
            assert name in tools, name
            selected.add(name)
            pending.extend(_calls(tools[name]))
    return selected


@lru_cache
def _runtime():
    raw, actual_raw = MANSION.read_bytes(), ACTUAL.read_bytes()
    assert sha256(raw).hexdigest() == MANSION_SHA
    assert sha256(actual_raw).hexdigest() == ACTUAL_SHA
    runtime, actual = _load_runtime(raw.decode()), _load_runtime(actual_raw.decode())
    # Actual generated body/callback closure is borrowed, never re-emitted from newer source.
    roots = {
        "move_srd_travel_subject",
        "check_srd_secure_area_movement",
        "check_srd_hallow_movement",
        "resolve_srd_forbiddance_event",
    }
    preserved = {
        name: deepcopy(tool)
        for name, tool in runtime["tools"].items()
        if name
        in {
            "advance_srd_lasting_clock",
            "resolve_srd_travel_placement",
            "resolve_srd_travel_geometry_face_placement",
            "resolve_srd_travel_entity_face_placement",
            "resolve_srd_object_content_geometry",
        }
        or "dwelling" in name
        or "extradimensional" in name
        or "mansion" in name
    }
    room_guard = next(
        e
        for e in runtime["tools"]["move_srd_travel_subject_node"]["effects"]
        if e.get("call") == "check_srd_dwelling_room_bounds"
    )
    runtime["tools"].update(
        {name: deepcopy(actual["tools"][name]) for name in _closure(actual["tools"], roots)}
    )
    runtime["tools"].update(preserved)
    node = runtime["tools"]["move_srd_travel_subject_node"]
    if "check_srd_dwelling_room_bounds" not in str(node["effects"]):
        boundary = next(i for i, e in enumerate(node["effects"]) if "spatial_mutation" in e)
        node["effects"].insert(boundary, deepcopy(room_guard))
    originals = {
        name: deepcopy(runtime["tools"][name])
        for name in (
            "move_srd_travel_subject",
            "move_srd_travel_subject_node",
            "end_srd_extradimensional_source",
        )
    }
    text = bind_runtime_consumers(yaml.dump(runtime, Dumper=_RuntimeDumper, sort_keys=False))
    assert bind_runtime_consumers(text) == text
    runtime = _load_runtime(text)
    for name, original in originals.items():
        assert runtime["tools"][name] == original
    roots = set(runtime["tools"]) - {
        "qualify_srd_mansion_return",
        "return_srd_mansion_subject",
        "return_srd_mansion_subject_node",
    }
    selected = _closure(runtime["tools"], roots)
    runtime["tools"] = {name: tool for name, tool in runtime["tools"].items() if name in selected}
    content = yaml.dump(runtime, Dumper=_RuntimeDumper, sort_keys=False).encode()
    return runtime, content


@pytest.fixture
def returns_scope(monkeypatch):
    monkeypatch.setattr(fall, "_runtime", _runtime)
    monkeypatch.setattr(
        fall,
        "EXTRA_PUBLIC_TOOLS",
        frozenset({"traverse_srd_dwelling_door", "operate_srd_mansion_door"}),
    )
    from sylvia_gm.application import package_runtime

    original_execute = package_runtime.execute_tool

    def observed(*args, **kwargs):
        result = original_execute(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", observed)
    yield from fall.actual_fall_scope.__wrapped__(monkeypatch)


def _paid_inside(owner):
    _, _, parameters = public._create_spell_campaign(owner, "magnificent_mansion")
    mechanics = StatelessMcpMechanics(owner)
    for tool, params, key in (
        ("begin_srd_spell_cast", _begin(parameters), "begin"),
        (
            "continue_srd_spell_cast",
            {
                "caster_state": "hero",
                "elapsed_rounds": 10,
                "uninterrupted": True,
                "verbal_component_available": True,
                "somatic_component_available": True,
                "material_component_available": True,
            },
            "minute",
        ),
        ("cast_spell_outside_combat", parameters, "paid"),
        ("move_srd_scene_creature", {"actor_state": "hero", "destination": [5, 9, 0]}, "approach"),
    ):
        result = mechanics.execute_action(
            dwelling._command(params, owner.snapshot("casting").revision, tool, key)
        )
        assert result.status is ActionStatus.COMMITTED, result.result
    saved = owner.snapshot("casting")
    source, room = dwelling._source(saved.state), dwelling._room(saved.state)
    destination = [room["shape"]["min"][0] + 1.5, 0, room["shape"]["min"][2]]
    command = dwelling._command(
        {
            "actor_state": "hero",
            "source_id": source["id"],
            "door_subject": source["outer_id"],
            "destination": destination,
        },
        saved.revision,
        "traverse_srd_dwelling_door",
        "enter",
    )
    result = mechanics.execute_action(command)
    assert result.status is ActionStatus.COMMITTED, result.result
    assert _point(owner.snapshot("casting").state, "hero") == destination
    return deepcopy(source)


def test_paid_expiry_returns_real_body_at_outer_clock_then_reopens_and_retries(
    returns_scope, tmp_path, monkeypatch
):
    monkeypatch.setattr(public, "_spell_case", dwelling._mansion_case)
    database = tmp_path / "mansion-return.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        source = _paid_inside(owner)
        before = owner.snapshot("casting")
        assert before.state[f"{SRD}.state"]["lasting_tick"] == 20
        mechanics = StatelessMcpMechanics(owner)
        command = dwelling._command(
            {"elapsed_rounds": 14400}, before.revision, "advance_srd_lasting_time", "expiry"
        )
        result = mechanics.execute_action(command)
        assert result.status is ActionStatus.COMMITTED, result.result
        saved = owner.snapshot("casting")
        current = dwelling._source(saved.state)
        assert current["active"] is False and current["return_pending"] is False
        assert saved.state[f"{SRD}.state"]["has_extradimensional_pending_returns"] is False
        assert saved.state[f"{SRD}.state"]["mansion_return_receipt"] is None
        body = next(
            row
            for row in saved.state[f"{SRD}.state"]["spatial_scene"]["entities"]
            if row["id"] == "hero"
        )
        assert body["metadata"]["plane"] == source["origin_plane"]
        assert body["position"][0] == pytest.approx(source["origin"][0] - source["door_depth"] / 2)
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
        assert mechanics.execute_action(command) == result
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting") == saved
        assert mechanics.execute_action(command) == result
        assert owner.snapshot("casting") == saved
        later = mechanics.execute_action(
            dwelling._command(
                {"elapsed_rounds": 1}, saved.revision, "advance_srd_lasting_time", "later"
            )
        )
        assert later.status is ActionStatus.COMMITTED
        assert _point(owner.snapshot("casting").state, "hero") == body["position"]


def test_return_leaf_rejects_an_ordinary_caller_without_the_paid_receipt(returns_scope):
    state, _ = dwelling._tk_case()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="source-derived"):
        budget._run(
            "return_srd_mansion_subject",
            state,
            subject="hero",
            destination=[5, 9, 0],
            plane="material",
        )
    assert state == before


def test_raw_retirement_stays_queue_only_until_real_completed_outer_boundary(returns_scope):
    state, _ = dwelling._tk_case()
    dwelling._install(state)
    dwelling._enter(state)
    budget._run("apply_srd_condition", state, target="hero", condition="stunned")
    source = deepcopy(dwelling._source(state))
    inside = _point(state, "hero")
    budget._run("end_srd_extradimensional_source", state, source_id=source["id"])
    assert _point(state, "hero") == inside and dwelling._source(state)["return_pending"] is True
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert dwelling._source(state)["return_pending"] is False
    assert state[f"{SRD}.state"]["mansion_return_receipt"] is None
    assert _point(state, "hero")[0] == pytest.approx(4.875)
    assert "stunned" in state[f"{SRD}.entities"]["hero"]["conditions"]


def test_real_planar_ward_rejects_settlement_atomically_and_source_removal_releases_it(
    returns_scope,
):
    from tests import test_srd_spell_secure_areas as secure

    state, _ = dwelling._tk_case()
    dwelling._install(state)
    dwelling._enter(state)
    source_id = dwelling._source(state)["id"]
    ward_id, _ = secure._cast(
        state,
        "private_sanctum",
        caster_state="guard",
        origin=[0, 5, 0],
        side=20,
        security=["planar"],
    )
    budget._run("end_srd_extradimensional_source", state, source_id=source_id)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="actual ward"):
        budget._run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert state == before
    budget._run("end_srd_secure_area_source", state, source_id=ward_id)
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert dwelling._source(state)["return_pending"] is False
    assert _point(state, "hero")[0] == pytest.approx(4.875)
