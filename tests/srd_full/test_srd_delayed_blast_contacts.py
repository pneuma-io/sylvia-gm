"""Native touch saves, durable held occupancy and genuine forty-foot impact paths."""

from copy import deepcopy
from functools import lru_cache
from unittest.mock import patch

import pytest
import yaml
from scripts import srd_delayed_blast_contacts as contacts
from scripts import srd_reaction_spells as reactions
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm import dice
from sylvia_gm.application import package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._types import ExecutionBudget
from tests import test_srd_delayed_blast_fireball as base
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_information import _wall
from tests.srd_full.test_srd_sunbeam import Rolls

_BASE_RUNTIME = base._runtime


@lru_cache
def _runtime():
    parent, encoded = _BASE_RUNTIME()
    encoded = contacts.bind_runtime_consumers(encoded.decode())
    runtime = yaml.load(encoded, Loader=yaml.CSafeLoader)
    runtime["tools"]["read_srd_delayed_blast_position_probe"] = contacts._tool(
        "Read the actual saved bead row through its private current-position owner.",
        [contacts._parameter("source_id")],
        [
            contacts._set("_probe_rows", "state.turn_effects"),
            contacts._set("_probe_record", None),
            contacts._loop(
                "_probe_rows",
                "_probe_row",
                [
                    contacts._when(
                        "_probe_row.kind == 'delayed_blast_fireball' and "
                        "_probe_row.source_id == source_id",
                        [contacts._set("_probe_record", "_probe_row")],
                    )
                ],
            ),
            contacts._require("_probe_record != null", "The actual paid bead must exist."),
            contacts._call(
                "resolve_srd_delayed_blast_position",
                {"record": "{_probe_record}"},
                contacts.POSITION_EXPORTS_BEAD,
            ),
        ],
    )
    changed = {}
    with patch.object(reactions, "ORDINARY_SUPPORT_TOOLS", contacts.PUBLIC_TOOLS):
        reactions._ordinary_support_fences(runtime["tools"], changed)
    runtime["tools"].update(changed)
    assert runtime["tools"]["d20_test"] == parent["tools"]["d20_test"]
    assert (
        runtime["tools"]["resolve_spell_area_save_damage_target"]
        == parent["tools"]["resolve_spell_area_save_damage_target"]
    )
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
            input_schema=_tool_schema(runtime["tools"][name], tool_name=name),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    runtime, _ = _runtime()
    monkeypatch.setattr(base, "_runtime", _runtime)
    monkeypatch.setattr(base, "PUBLIC_TOOLS", base.PUBLIC_TOOLS | contacts.PUBLIC_TOOLS)
    monkeypatch.setattr(base.budget, "_mechanic", _mechanic)
    monkeypatch.setattr(base.cards, "_spell_definitions", lambda: runtime["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Rolls())
    actual = package_runtime.execute_tool

    def diagnose(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnose)


def _case(*, success=True):
    state, data, params = base._case()
    for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]:
        if row["id"] == "guard":
            row["position"] = [30, 5, 0]
        elif row["id"] != "hero":
            row["position"] = [80, 20 if row["id"] == "other" else -20, 0]
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["dexterity"] = (
        30 if success else -30
    )
    return state, data, params


def _touch(state, source):
    return _run(
        "touch_srd_delayed_blast_fireball", state, actor_state="guard", source_id=source
    ).result


def _throw(state, source, destination):
    return _run(
        "throw_srd_delayed_blast_fireball",
        state,
        actor_state="guard",
        source_id=source,
        throw_destination=destination,
    ).result


def test_actual_touch_success_move_and_forty_foot_throw_reuses_original_source(monkeypatch):
    state, data, params = _case()
    base._cast(state, data, params)
    original = deepcopy(base._sources(state)[0])
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    context = _touch(state, original["source_id"])["context"]
    assert context["_bead_touch_outcome"] == "success" and rolls.sides == [20]
    assert base._sources(state)[0]["held"]["actor"] == "guard"
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[35, 5, 0])
    before = deepcopy(state)
    point = _run(
        "read_srd_delayed_blast_position_probe", state, source_id=original["source_id"]
    ).result["context"]
    assert point["_bead_origin"] == [35, 5, 2] and point["_bead_plane"] == "material"
    assert state == before  # selected Dispel can reuse this pure current-occupancy query
    context = _throw(state, original["source_id"], [75, 5, 2])["context"]
    assert context["_bead_collided"] is False and rolls.sides == [20]
    source = base._sources(state)[0]
    assert source["origin"] == [75, 5, 2] and source["held"] is None
    for field in ("source_id", "cast_id", "caster", "slot_level", "save_dc", "expires_tick"):
        assert source[field] == original[field]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000


def test_actual_touch_failure_ends_and_detonates_once_with_native_saved_roll(monkeypatch):
    state, data, params = _case(success=False)
    base._cast(state, data, params)
    source = base._sources(state)[0]["source_id"]
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    context = _touch(state, source)["context"]
    assert context["_bead_touch_outcome"] == "failure"
    assert rolls.sides == [20] + [6] * 12 + [20]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 964
    assert base._event(state, source)["status"] == "resolved" and base._sources(state) == []
    before, faces = deepcopy(state), deepcopy(rolls.sides)
    with pytest.raises(RulesetPreconditionRejected, match="No current paid bead"):
        _touch(state, source)
    assert state == before and rolls.sides == faces


@pytest.mark.parametrize("collision", ["creature", "solid", "prepared_object"])
def test_actual_first_creature_or_solid_collision_ends_at_impact(monkeypatch, collision):
    state, data, params = _case()
    base._cast(state, data, params)
    source = base._sources(state)[0]["source_id"]
    _touch(state, source)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    if collision == "creature":
        row = next(row for row in scene["entities"] if row["id"] == "other")
        row["position"] = [50, 5, 0]
        x = 50 - row["footprint"]["half_extents"][0]
    elif collision == "prepared_object":
        row = next(row for row in scene["entities"] if row["id"] == "orb")
        row["position"], row["height"] = [45, 5, 0], 4
        x = 45 - row["footprint"]["half_extents"][0]
        _run(
            "prepare_srd_spell_object",
            state,
            object_id="orb",
            material="wood",
            size="large",
            durability="resilient",
        )
    else:
        panel = _wall(1, "glass", 4, 6)
        panel["volume"] = {"kind": "box", "min": [45, 4, 0], "max": [46, 6, 5]}
        panel["visibility"] = "clear"
        panel["metadata"]["plane"] = "material"
        scene["geometry"] = [panel]
        x = 45
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    context = _throw(state, source, [70, 5, 2])["context"]
    assert context["_bead_collided"] is True
    assert context["_bead_impact_point"] == [x, 5, 2]
    assert base._event(state, source)["record"]["origin"] == [x, 5, 2]
    assert base._event(state, source)["status"] == "resolved"
    assert rolls.sides.count(6) == 12 and base._sources(state) == []


def test_held_expiry_queues_current_actual_point_after_real_movement_without_damage(monkeypatch):
    state, data, params = _case()
    base._cast(state, data, params)
    source = base._sources(state)[0]["source_id"]
    _touch(state, source)
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[35, 5, 0])
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert rolls.sides == [] and base._sources(state) == []
    event = base._event(state, source)
    assert event["status"] == "queued" and event["record"]["origin"] == [35, 5, 2]
    _run(base.DRAIN, state)
    assert base._event(state, source)["status"] == "resolved"


def test_native_unconscious_contact_uses_automatic_dex_failure_and_truthful_no_roll(monkeypatch):
    state, data, params = _case()
    base._cast(state, data, params)
    source = base._sources(state)[0]["source_id"]
    state[f"{SRD}.entities"]["guard"]["conditions"] = ["unconscious"]
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _touch(state, source)
    assert result["context"]["_bead_touch_roll"] is None
    assert result["context"]["_bead_touch_faces"] == "No roll"
    assert result["context"]["_bead_touch_outcome"] == "failure"
    assert rolls.sides == [6] * 12
    assert base._event(state, source)["status"] == "resolved"


def test_unearned_throw_range_changed_body_and_remote_touch_reject_atomically(monkeypatch):
    state, data, params = _case()
    base._cast(state, data, params)
    source = base._sources(state)[0]["source_id"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Touch needs"):
        _run("touch_srd_delayed_blast_fireball", state, actor_state="hero", source_id=source)
    assert state == before
    with pytest.raises(RulesetPreconditionRejected, match="successful toucher"):
        _throw(state, source, [70, 5, 2])
    assert state == before
    _touch(state, source)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="beyond forty"):
        _throw(state, source, [70.01, 5, 2])
    assert state == before
    row = next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "guard"
    )
    row["height"] += 1
    before = deepcopy(state)
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    with pytest.raises(RulesetPreconditionRejected, match="changed anatomy"):
        _throw(state, source, [70, 5, 2])
    assert state == before and rolls.sides == []


def test_contact_final_binder_preserves_native_roll_owner_and_repeats():
    runtime, encoded = _runtime()
    rebound = yaml.load(contacts.bind_runtime_consumers(encoded.decode()), Loader=yaml.CSafeLoader)
    changed = {}
    with patch.object(reactions, "ORDINARY_SUPPORT_TOOLS", contacts.PUBLIC_TOOLS):
        reactions._ordinary_support_fences(rebound["tools"], changed)
    rebound["tools"].update(changed)
    assert rebound == runtime
    assert base.DRAIN not in str(runtime["tools"][base.QUEUE]["effects"])


def test_paid_archived_successful_touch_reopen_throw_detonation_and_exact_retry(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(base.public_casting, "bundled_srd_archive", base._archive)
    monkeypatch.setattr(base.public_casting, "_spell_case", lambda _spell: _case())
    rolls, measured = Rolls(), []
    monkeypatch.setattr(dice, "_rng", rolls)
    actual = ExecutionBudget.consume_operation

    def count(budget):
        if not measured or measured[-1] is not budget:
            measured.append(budget)
        actual(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", count)
    database, receipts = tmp_path / "dbf-contact.db", []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = base.public_casting._create_spell_campaign(owner, base.SPELL)
        receipts.append(_act(owner, "cast_spell_outside_combat", **params))
        source = base._sources(owner.snapshot("casting").state)[0]["source_id"]
        receipts.append(
            _act(owner, "touch_srd_delayed_blast_fireball", actor_state="guard", source_id=source)
        )
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        receipts.append(
            _act(
                owner,
                "throw_srd_delayed_blast_fireball",
                actor_state="guard",
                source_id=source,
                throw_destination=[70, 5, 2],
            )
        )
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        receipts.append(_act(owner, "detonate_srd_delayed_blast_fireball", caster_state="hero"))
        saved, faces = owner.snapshot("casting"), deepcopy(rolls.sides)
        assert base._event(saved.state, source)["status"] == "resolved"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        for result, command in receipts:
            assert (
                result.status is ActionStatus.COMMITTED and owner.execute_action(command) == result
            )
        assert owner.snapshot("casting") == saved and rolls.sides == faces
    assert all(b.max_operations == 10000 for b in measured)
    print("DBF paid/contact/throw max operations:", max(b.operations for b in measured))
