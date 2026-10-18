"""Paid terrain work waits for the real clock and preserves the one scene authority."""

import ast
import json
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_move_earth import (
    PROFILE,
    PUBLIC_TOOLS,
    _bind_dispel,
    bind_removal_consumers,
    bind_runtime_consumers,
    schema,
)
from scripts.srd_full.srd_parameter_declarations import compact_runtime
from scripts.srd_full.srd_timed_actor_defenses import _require, _set, _when

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
    text = bind_removal_consumers(text)
    assert bind_removal_consumers(text) == text
    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    runtime["definitions"]["spells"]["move_earth"] = deepcopy(PROFILE)
    runtime["definition_row_schemas"]["definitions.spells"]["variants"]["move_earth"] = schema()
    for tool in runtime["tools"].values():
        for parameter in tool["parameters"]:
            values = parameter.get("enum_values", [])
            if parameter["name"] == "spell" and "foresight" in values:
                parameter["enum_values"] = sorted(set(values) | {"move_earth"})
    assert runtime["tools"]["d20_test"] == parent["tools"]["d20_test"]
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


def _case():
    state, data, params = scene_case("move_earth")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"] = [row for row in scene["entities"] if row["id"] in {"hero", "guard"}]
    scene["geometry"] = [
        {
            "id": "soil",
            "map_id": "information",
            "active": True,
            "movement": "blocking",
            "visibility": "opaque",
            "volume": {"kind": "box", "min": [30, -10, -10], "max": [50, 10, 0]},
            "metadata": {"plane": "material", "opaque_marker": "keep"},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    ]
    _run("record_srd_earth_material", state, geometry_id="soil", material="clay")
    params.update(
        target="hero",
        earth_geometry_id="soil",
        earth_elevation_change=3,
        slot_resource="spell_slot_6",
    )
    return state, data, params


def _paid(state, data, params):
    return _run("cast_spell_outside_combat", state, participant_data=data, **params)


def _terrain(state):
    return next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["geometry"] if row["id"] == "soil"
    )


def _source(state):
    source_id = state[f"{SRD}.entities"]["hero"]["concentration_cast_id"]
    return state[f"{SRD}.state"]["move_earth_sources"][source_id]


def _clock(state, ticks):
    return _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=ticks)


def _dispel_case():
    state, data, params = _case()
    data["spellcasting_grants"][0]["spells"].append("dispel_magic")
    data["wizard_spellbook"].append("dispel_magic")
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("dispel_magic")
    return state, data, params


def _dispel_params(params, source_id, level=3):
    return params | {
        "spell": "dispel_magic",
        "slot_resource": f"spell_slot_{level}",
        "material_item_ids": [],
        "recovery_choice": "move_earth",
        "recovery_source_id": source_id,
    }


def test_selected_recovery_join_preserves_real_frozen_adoption_boundary_and_repeats():
    path = Path(
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-move-earth/srd_spell_removal_adoption.py")
    )
    encoded = path.read_bytes()
    assert sha256(encoded).hexdigest() == (
        "b21333a295d2260ed85f64f9a924bf512eac54975c4a0e7d7cda2eed3d52ce42"
    )
    tree = ast.parse(encoded.decode())
    statements = [
        node
        for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name == "_bind_selected_recovery")
        or (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "SELECTED_KINDS"
                for target in node.targets
            )
        )
    ]
    namespace = {"_require": _require, "_set": _set, "_when": _when}
    exec(compile(ast.Module(body=statements, type_ignores=[]), str(path), "exec"), namespace)
    native = yaml.load(
        (PACKAGE_SOURCE / "runtime/ruleset.yaml").read_text(), Loader=yaml.CSafeLoader
    )
    changed = {"cast_srd_recovery": deepcopy(native["tools"]["cast_srd_recovery"])}
    namespace["_bind_selected_recovery"](changed)
    parent = changed["cast_srd_recovery"]
    joined = _bind_dispel(parent)
    assert _bind_dispel(joined) == joined
    assert joined["parameters"] == parent["parameters"]
    assert joined["pre_conditions"] == parent["pre_conditions"]
    assert joined["metadata"] == parent["metadata"]
    stripped = deepcopy(joined)
    stripped["effects"] = stripped["effects"][:-1]
    for effect in stripped["effects"]:
        if effect.get("set", {}).get("field") in {
            "_recovery_source_intents",
            "_recovery_source_owners",
        }:
            effect["set"]["value"].remove("'move_earth'")
    assert stripped == parent


def test_exact_free_dismissal_cancels_only_pending_work_and_keeps_completed_ground():
    state, data, params = _case()
    _paid(state, data, params)
    source_id = state[f"{SRD}.entities"]["hero"]["concentration_cast_id"]
    _clock(state, 200)
    completed = deepcopy(_terrain(state))
    _run(
        "choose_srd_move_earth_area",
        state,
        caster_state="hero",
        geometry_id="soil",
        elevation_change=-3,
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("dismiss_srd_spell", state, caster_state="guard", cast_id=source_id)
    assert state == before
    state = json.loads(json.dumps(state))
    resources = deepcopy(state[f"{SRD}.entities"]["hero"]["resources"])
    _run("dismiss_srd_spell", state, caster_state="hero", cast_id=source_id)
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    source = state[f"{SRD}.state"]["move_earth_sources"][source_id]
    assert source["installed"] is False and source["pending"] is None
    assert source["completed_changes"] == 1
    assert source["anchor_geometry_id"] == "soil"
    assert state[f"{SRD}.entities"]["hero"]["resources"] == resources
    _clock(state, 200)
    assert _terrain(state) == completed


def test_paid_dispel_uses_actual_upcast_source_and_keeps_ground_after_failed_then_earned_check(
    monkeypatch,
):
    state, data, params = _dispel_case()
    params["slot_resource"] = "spell_slot_7"
    _paid(state, data, params)
    source_id = state[f"{SRD}.entities"]["hero"]["concentration_cast_id"]
    _clock(state, 200)
    completed = deepcopy(_terrain(state))
    assert _source(state)["spell_level"] == 7
    monkeypatch.setattr(dice, "_rng", Rolls(attack=1))
    failed_action = _paid(state, data, _dispel_params(params, source_id))
    failed = failed_action.result["result_card"]
    details = {row["label"]: row["value"] for row in failed["details"]}
    assert details["Dispel DC"] == 17
    assert "selected 1; modifier 4; DC 17; effect ended: False" in details["Dispel check"]
    assert {row["label"]: row["value"] for row in failed["summary"]}[
        "Selected source ended"
    ] is False
    assert _source(state)["installed"] is True
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
    state = json.loads(json.dumps(state))
    monkeypatch.setattr(dice, "_rng", Rolls(attack=20))
    success = _paid(state, data, _dispel_params(params, source_id)).result["result_card"]
    assert {row["label"]: row["value"] for row in success["summary"]}[
        "Selected source ended"
    ] is True
    assert (
        "selected 20; modifier 4; DC 17; effect ended: True"
        in {row["label"]: row["value"] for row in success["details"]}["Dispel check"]
    )
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 1
    assert state[f"{SRD}.state"]["move_earth_sources"][source_id]["installed"] is False
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    _clock(state, 200)
    assert _terrain(state) == completed


def test_selected_dispel_rejects_changed_source_plane_range_or_path_atomically():
    for change, error in (
        (lambda state: _terrain(state)["metadata"].update(plane="ethereal"), "recorded plane"),
        (
            lambda state: next(
                row
                for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]
                if row["id"] == "hero"
            ).update(position=[-200, 0, 0]),
            "beyond Dispel range",
        ),
        (
            lambda state: state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(
                {
                    **deepcopy(_terrain(state)),
                    "id": "actual-wall",
                    "volume": {"kind": "box", "min": [10, -10, -10], "max": [11, 10, 10]},
                }
            ),
            "solid barrier",
        ),
    ):
        state, data, params = _dispel_case()
        _paid(state, data, params)
        source_id = state[f"{SRD}.entities"]["hero"]["concentration_cast_id"]
        change(state)
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected, match=error):
            _paid(state, data, _dispel_params(params, source_id, 6))
        assert state == before


def test_real_paid_work_waits_100_rounds_then_next_area_and_expiry_preserve_terrain():
    state, data, params = _case()
    initial = deepcopy(_terrain(state))
    _paid(state, data, params)
    assert _terrain(state) == initial
    assert _source(state)["pending"]["due_tick"] == 200
    _clock(state, 198)
    assert _terrain(state) == initial
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="ten-minute boundary"):
        _run(
            "choose_srd_move_earth_area",
            state,
            caster_state="hero",
            geometry_id="soil",
            elevation_change=-3,
        )
    assert state == before
    state = json.loads(json.dumps(state))
    _clock(state, 2)
    assert _terrain(state)["volume"]["max"] == [50, 10, 3]
    assert _terrain(state)["metadata"] == initial["metadata"]
    assert _source(state)["pending"] is None
    assert _source(state)["completed_changes"] == 1
    _run(
        "choose_srd_move_earth_area",
        state,
        caster_state="hero",
        geometry_id="soil",
        elevation_change=-3,
    )
    _clock(state, 200)
    assert _terrain(state) == initial
    source_id = state[f"{SRD}.entities"]["hero"]["concentration_cast_id"]
    _clock(state, 2000)
    assert _terrain(state) == initial
    assert state[f"{SRD}.state"]["move_earth_sources"][source_id]["installed"] is False
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None


def test_source_eligibility_and_fresh_deadline_checks_reject_before_any_mutation():
    for mutation, error in (
        (
            lambda state, params: _terrain(state)["metadata"].update(material="stone"),
            "dirt, sand or clay",
        ),
        (lambda state, params: _terrain(state)["metadata"].update(plants=True), "Plant, rock"),
        (lambda state, params: params.update(earth_elevation_change=11), "half the area's"),
        (
            lambda state, params: _terrain(state)["volume"].update(
                min=[150, -10, -10], max=[170, 10, 0]
            ),
            "within 120",
        ),
        (
            lambda state, params: state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
                _entity("occupant", [40, 0, 0])
            ),
            "Occupied terrain",
        ),
    ):
        state, data, params = _case()
        mutation(state, params)
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected, match=error):
            _paid(state, data, params)
        assert state == before
    state, data, params = _case()
    _paid(state, data, params)
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(_entity("occupant", [40, 0, 0]))
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Occupied terrain"):
        _clock(state, 200)
    assert state == before
    state, data, params = _case()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="never reclassify"):
        _run("record_srd_earth_material", state, geometry_id="soil", material="sand")
    assert state == before
    _paid(state, data, params)
    _run("end_srd_concentration_node", state, source_state="hero", target_state="hero")
    _clock(state, 200)
    assert _terrain(state)["volume"]["max"] == [50, 10, 0]


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
            "id": "move-earth-native",
            "kind": "ruleset_runtime",
            "family": "move_earth",
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
            "evidence": {"tests": ["paid-terrain-ten-minute-source-clock-reopen-retry"]},
        }
    )
    content["inclusions"].sort(key=lambda row: row["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return bundled_packages._archive((NormalizedPackageDraft(record, payloads, 4), reference))


def test_public_paid_work_reopens_then_real_clock_changes_terrain_and_retries_once(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _spell: _case())
    database = tmp_path / "earth.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "move_earth")
        paid, paid_input = _act(owner, "cast_spell_outside_combat", **params)
        assert paid.status is ActionStatus.COMMITTED
        assert owner.execute_action(paid_input) == paid
        saved = owner.snapshot("casting")
        assert _terrain(saved.state)["volume"]["max"] == [50, 10, 0]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        completed, clock_input = _act(owner, "advance_srd_lasting_time", elapsed_rounds=100)
        assert completed.status is ActionStatus.COMMITTED
        changed = owner.snapshot("casting")
        assert _terrain(changed.state)["volume"]["max"] == [50, 10, 3]
        assert _terrain(changed.state)["metadata"]["opaque_marker"] == "keep"
        assert _source(changed.state)["completed_changes"] == 1
        assert owner.execute_action(clock_input) == completed
        assert owner.execute_action(paid_input) == paid
        assert owner.snapshot("casting") == changed
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == changed
        read, read_input = _act(owner, "read_srd_move_earth", caster_state="hero")
        assert read.status is ActionStatus.COMMITTED
        assert owner.execute_action(read_input) == read
        assert _source(owner.snapshot("casting").state)["completed_changes"] == 1


def test_public_paid_dispel_reopens_and_retries_without_rewinding_terrain_or_paying_twice(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _spell: _dispel_case())
    database = tmp_path / "earth-dispel.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "move_earth")
        paid, paid_input = _act(owner, "cast_spell_outside_combat", **params)
        assert paid.status is ActionStatus.COMMITTED
        completed, clock_input = _act(owner, "advance_srd_lasting_time", elapsed_rounds=100)
        assert completed.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        source_id = saved.state[f"{SRD}.entities"]["hero"]["concentration_cast_id"]
        assert _source(saved.state)["anchor_geometry_id"] == "soil"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        dispelled, dispel_input = _act(
            owner, "cast_spell_outside_combat", **_dispel_params(params, source_id, 6)
        )
        assert dispelled.status is ActionStatus.COMMITTED
        ended = owner.snapshot("casting")
        assert ended.state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
        record = ended.state[f"{SRD}.state"]["move_earth_sources"][source_id]
        assert record["installed"] is False and record["pending"] is None
        assert _terrain(ended.state) == _terrain(saved.state)
        assert ended.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 1
        assert owner.execute_action(dispel_input) == dispelled
        assert owner.execute_action(clock_input) == completed
        assert owner.execute_action(paid_input) == paid
        assert owner.snapshot("casting") == ended
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == ended
        later, later_input = _act(owner, "advance_srd_lasting_time", elapsed_rounds=100)
        assert later.status is ActionStatus.COMMITTED
        final = owner.snapshot("casting")
        assert _terrain(final.state) == _terrain(saved.state)
        assert owner.execute_action(later_input) == later
        assert owner.execute_action(dispel_input) == dispelled
        assert owner.snapshot("casting") == final
