"""Saved anatomical offsets follow actual bodies and reject stale forms before sight."""

import json
from copy import deepcopy
from functools import lru_cache
from random import Random
from unittest.mock import patch

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_scene_sight_origins import _consumers, _tools, bind_runtime_consumers

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages, package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from sylvia_gm.runtime import _effect_executor
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_illumination_body_consumers import NATIVE
from tests.srd_full.test_srd_illumination_body_consumers import _runtime as _native_runtime
from tests.srd_full.test_srd_illumination_point_sight import _with_ambient
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_illumination import _case
from tests.srd_full.test_srd_spell_wave3_information import _wall


@lru_cache
def _runtime():
    runtime = deepcopy(_native_runtime(False)[0])
    runtime["tools"].update(_tools() | _consumers(runtime))
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
            input_schema=owned_payload(_tool_schema(runtime["tools"][name])),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _owner(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)


def _state():
    state, _, _ = _case("light")
    _with_ambient(state)
    return state


def _hero(state):
    return next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "hero")


def _resolve(state):
    return _run("resolve_srd_scene_sight_origin", state, observer_state="hero").result["context"]


def _record(state, offset=(0, 0, 5)):
    return _run("record_srd_scene_sight_origin", state, observer_state="hero", offset=list(offset))


def test_absent_anatomy_preserves_explicit_scene_anchor_without_inferred_eye_height():
    state = _state()
    before = deepcopy(state)
    result = _resolve(state)
    assert result["_sight_origin"] == [0, 0, 0]
    assert result["_sight_origin_recorded"] is False and state == before


def test_factual_record_preserves_other_metadata_and_follows_actual_relocation():
    state = _state()
    before = deepcopy(_hero(state))
    _record(state, (0.2, -0.2, 5))
    row = _hero(state)
    assert row["position"] == before["position"]
    assert {k: v for k, v in row["metadata"].items() if k != "srd_sight_origin"} == before[
        "metadata"
    ]
    assert row["metadata"]["srd_sight_origin"] == {
        "offset": [0.2, -0.2, 5],
        "footprint": before["footprint"],
        "height": 6,
        "form_source_id": None,
    }
    row["position"] = [20, 5, 1]
    assert _resolve(state)["_sight_origin"] == [20.2, 4.8, 6]


@pytest.mark.parametrize("offset", [(1, 0, 5), (0, 1, 5), (0, 0, -1), (0, 0, 7)])
def test_record_rejects_offset_outside_actual_box_body_without_mutation(offset):
    state = _state()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _record(state, offset)
    assert state == before


@pytest.mark.parametrize("offset,accepted", [((0.3, 0.4, 6), True), ((0.4, 0.4, 5), False)])
def test_circle_record_uses_actual_radius_instead_of_its_bounding_rectangle(offset, accepted):
    state = _state()
    _hero(state)["footprint"] = {"kind": "circle", "radius": 0.5}
    if accepted:
        _record(state, offset)
        assert _resolve(state)["_sight_origin"] == list(offset)
    else:
        with pytest.raises(RulesetPreconditionRejected):
            _record(state, offset)


@pytest.mark.parametrize("change", ["height", "footprint", "form"])
def test_changed_body_or_same_sized_new_form_invalidates_saved_anatomy(change):
    state = _state()
    _record(state)
    if change == "height":
        _hero(state)["height"] = 7
    elif change == "footprint":
        _hero(state)["footprint"]["half_extents"][0] = 0.5
    else:
        state[f"{SRD}.entities"]["hero"]["form_current_source_id"] = "actual-new-body"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="body|footprint"):
        _resolve(state)
    assert state == before
    _record(state)
    assert _resolve(state)["_sight_origin_recorded"] is True


@pytest.mark.parametrize("transparent", [False, True])
def test_actual_recorded_eye_crosses_wall_above_floor_and_preserves_total_cover(transparent):
    state = _state()
    barrier = _wall(2)
    barrier["volume"] = {"kind": "box", "min": [10, -5, 0], "max": [12, 5, 10]}
    if transparent:
        barrier["visibility"] = "clear"
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [barrier]

    def point():
        return _run(
            "resolve_srd_illumination_point_sight", state, observer_state="hero", point=[30, 0, 0]
        ).result["context"]

    assert point()["_illumination_point_can_see"] is True
    _record(state)
    result = point()
    assert result["_illumination_point_origin"] == [0, 0, 5]
    assert result["_illumination_point_total_cover"] is True
    assert result["_illumination_point_opaque"] is (not transparent)
    assert result["_illumination_point_can_see"] is transparent


def test_object_exposure_samples_use_actual_recorded_eye_and_keep_actor_range(monkeypatch):
    state = _state()
    _record(state)
    _run(
        "prepare_srd_spell_object",
        state,
        object_id="orb",
        material="wood",
        size="large",
        durability="resilient",
    )
    queries = []
    actual = _effect_executor._execute_effect

    def observe(effect, context, tool_executor=None):
        if effect.get("store_as") == "_object_exposure":
            queries.append(deepcopy(effect["spatial_query"]["query"]))
            assert context.vars["_sight_origin"] == [0, 0, 5]
        actual(effect, context, tool_executor)

    monkeypatch.setattr(_effect_executor, "_execute_effect", observe)
    result = _run(
        "resolve_srd_spell_object_relation",
        state,
        caster_state="hero",
        object_id="orb",
        max_range_feet=60,
    ).result["context"]
    assert queries[0]["observer"]["point"] == "{_sight_origin}"
    assert result["_object_distance"] == 5


def test_sight_origin_binder_is_idempotent_and_public_input_is_factual_only():
    runtime, encoded = _runtime()
    assert bind_runtime_consumers(encoded.decode()) == encoded.decode()
    schema = _tool_schema(runtime["tools"]["record_srd_scene_sight_origin"])
    assert set(schema["properties"]) == {"observer_state", "offset", "lasting_state"}


@lru_cache
def _archive():
    runtime, encoded = _runtime()
    with patch.object(bundled_packages, "_bundled_srd_root", return_value=NATIVE.parent.parent):
        primary, reference = bundled_packages._srd_drafts()
    record = deepcopy(primary.record)
    name = "record_srd_scene_sight_origin"
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
            "id": "srd-factual-sight-origin",
            "kind": "ruleset_runtime",
            "family": "illumination_spell",
            "disposition": "included_mechanical",
            "classifications": ["sylvia_gm_implementation"],
            "sources": [],
            "links": {
                "artifacts": ["runtime/ruleset.yaml"],
                "capabilities": ["dnd.srd.5.2.1.spells"],
                "tools": [name],
                "mechanics": [],
                "procedures": [],
                "prompt_fragments": [],
                "schemas": [],
                "state_namespaces": [],
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["factual-eye-public-misty-reopen-retry"]},
        }
    )
    content["inclusions"].sort(key=lambda row: row["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return bundled_packages._archive((NormalizedPackageDraft(record, payloads, 4), reference))


def _public_case(_spell):
    state, data, params = _case("misty_step")
    _with_ambient(state)
    wall = _wall(2)
    wall["volume"] = {"kind": "box", "min": [10, -5, 0], "max": [12, 5, 10]}
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [wall]
    for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]:
        if row["id"] != "hero":
            row["position"] = [100, 100, 0]
    params["travel_destination"] = [5, 5, 0]
    return state, data, params


def test_actual_factual_record_and_paid_misty_sight_survive_public_reopen_retry(
    tmp_path, monkeypatch
):
    errors = []
    sights = []
    actual = package_runtime.execute_tool

    def observed(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not result.success:
            errors.append(result.error)
        if args[0] == "resolve_srd_illumination_point_sight" and result.success:
            sights.append(
                (
                    result.context["_illumination_point_origin"],
                    result.context["_illumination_point_can_see"],
                )
            )
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", observed)
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public_casting, "_spell_case", _public_case)
    database = tmp_path / "factual-eye.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "misty_step")
        recorded, command = _act(
            owner, "record_srd_scene_sight_origin", observer_state="hero", offset=[0, 0, 5]
        )
        assert recorded.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert owner.execute_action(command) == recorded
        with pytest.raises(MechanicsRejected):
            _act(
                owner, "cast_spell_outside_combat", **(params | {"travel_destination": [30, 0, 0]})
            )
        assert owner.snapshot("casting") == saved
        try:
            paid, paid_command = _act(owner, "cast_spell_outside_combat", **params)
        except MechanicsRejected:
            pytest.fail(str(errors))
        assert paid.status is ActionStatus.COMMITTED
        assert ([0, 0, 5], False) in sights and ([0, 0, 5], True) in sights
        after = owner.snapshot("casting")
        hero = _hero(after.state)
        assert hero["position"] == [5, 5, 0]
        assert hero["metadata"]["srd_sight_origin"]["offset"] == [0, 0, 5]
    with AuthorityOwner.open(database, "casting-owner") as owner:

        class NoRoll(Random):
            def randint(self, _a, _b):
                pytest.fail("A durable factual sight retry rolled again")

        monkeypatch.setattr(dice, "_rng", NoRoll())
        assert owner.execute_action(command) == recorded
        assert owner.execute_action(paid_command) == paid
        assert owner.snapshot("casting") == after
