"""A paid hour leaves a distinct original body and retains one logical Astral actor."""

import json
from copy import deepcopy
from functools import lru_cache
from unittest.mock import patch

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_astral_projection import PROFILE, PUBLIC_TOOLS, bind_runtime_consumers, schema
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
    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    runtime["definitions"]["spells"]["astral_projection"] = deepcopy(PROFILE)
    runtime["definition_row_schemas"]["definitions.spells"]["variants"]["astral_projection"] = (
        schema()
    )
    for tool in runtime["tools"].values():
        for p in tool["parameters"]:
            if p["name"] == "spell" and p.get("enum_values") and "foresight" in p["enum_values"]:
                p["enum_values"] = sorted(set(p["enum_values"]) | {"astral_projection"})
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


def _case(*, astral=False):
    state, data, params = scene_case("astral_projection")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(name="Hero", hp=40, max_hp=40, temp_hp=2, conditions=[], exhaustion_level=0)
    hero["spell_materials"] = [
        {
            "id": "jacinth",
            "inventory_item": "jacinth_item",
            "kind": "specific",
            "quantity": 1,
            "value_cp": 100000,
            "components": ["astral_projection.1"],
        },
        {
            "id": "silver",
            "inventory_item": "silver_item",
            "kind": "specific",
            "quantity": 1,
            "value_cp": 10000,
            "components": ["astral_projection.2"],
        },
    ]
    hero["inventory"] = ["jacinth_item", "silver_item"]
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"] = [r for r in scene["entities"] if r["id"] in {"hero", "guard"}]
    origin = next(r for r in scene["entities"] if r["id"] == "hero")
    origin["id"] = "hero-original-body"
    origin["metadata"].update(plane="astral" if astral else "material", opaque_marker="keep")
    scene["entities"].append(
        _entity("astral-anchor", [100, 100, 0], is_object=True, plane="astral", movement="passable")
    )
    params.update(
        target="hero",
        slot_resource="spell_slot_9",
        material_item_ids=["jacinth", "silver"],
        material_component_available=True,
        astral_anchor="astral-anchor",
    )
    return state, data, params


def _begin_values(params):
    names = {p["name"] for p in _runtime()[0]["tools"]["begin_srd_spell_cast"]["parameters"]}
    return {k: v for k, v in params.items() if k in names}


def _paid_hour(state, data, params):
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin_values(params))
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=600,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    return _run("cast_spell_outside_combat", state, participant_data=data, **params).result[
        "context"
    ]


def test_actual_paid_hour_consumes_both_parts_and_leaves_distinct_body_with_logical_stats():
    state, data, params = _case()
    stats = deepcopy(state[f"{SRD}.entities"]["hero"])
    origin = deepcopy(state[f"{SRD}.state"]["spatial_scene"]["entities"][0])
    context = _paid_hour(state, data, params)
    hero = state[f"{SRD}.entities"]["hero"]
    source = state[f"{SRD}.state"]["astral_projection_sources"][hero["astral_projection_source_id"]]
    body = state[f"{SRD}.entities"][source["body_id"]]
    assert hero["hp"] == stats["hp"] and hero["max_hp"] == stats["max_hp"]
    assert hero["temp_hp"] == 2 and hero["conditions"] == []
    assert hero["inventory"] == [] and [m["quantity"] for m in hero["spell_materials"]] == [0, 0]
    assert hero["resources"]["spell_slot_9"] == stats["resources"]["spell_slot_9"] - 1
    assert body["hp"] == hero["hp"] and body["conditions"] == ["unconscious"]
    assert body["logical_actor_id"] == "hero" and body["nonautonomous_body"] is True
    read = _run("read_srd_astral_projection", state, caster_state="hero").result["context"]
    assert read["_astral_body_scene"]["id"] == origin["id"]
    assert read["_astral_body_scene"]["position"] == origin["position"]
    assert read["_astral_body_scene"]["metadata"]["opaque_marker"] == "keep"
    assert read["_astral_form_scene"]["metadata"]["plane"] == "astral"
    assert read["_astral_form_scene"]["metadata"]["participant_ref"] == "hero"
    actual = _run("srd_information_scene_entity", state, subject="hero").result["context"]
    assert actual["_info_scene_entity"]["id"] == source["form_scene_id"]
    assert context["_spell_summary_rows"] and source["expires_tick"] is None
    assert state[f"{SRD}.state"]["lasting_tick"] >= 1200


def test_living_magic_dismissal_reopens_and_restores_original_identity_without_second_stat_owner():
    state, data, params = _case()
    _paid_hour(state, data, params)
    source_id = state[f"{SRD}.entities"]["hero"]["astral_projection_source_id"]
    source = deepcopy(state[f"{SRD}.state"]["astral_projection_sources"][source_id])
    state = json.loads(json.dumps(state))
    _run("dismiss_srd_astral_projection", state, caster_state="hero")
    assert state[f"{SRD}.state"]["astral_projection_sources"][source_id]["installed"] is False
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 40
    assert state[f"{SRD}.entities"]["hero"]["astral_projection_role"] is None
    assert state[f"{SRD}.entities"][source["body_id"]]["astral_projection_role"] == "retired_body"
    body = next(
        r
        for r in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if r["id"] == "hero-original-body"
    )
    assert (
        body["metadata"]["participant_ref"] == "hero"
        and body["metadata"]["opaque_marker"] == "keep"
    )
    form = next(
        r
        for r in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if r["id"] == source["form_scene_id"]
    )
    assert form["active"] is False


def test_already_on_astral_plane_ends_after_actual_hour_and_payment_without_body_copy():
    state, data, params = _case(astral=True)
    before_ids = {row["id"] for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]}
    _paid_hour(state, data, params)
    assert state[f"{SRD}.entities"]["hero"].get("astral_projection_role") is None
    assert {row["id"] for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]} == before_ids
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"] == 2
    assert [m["quantity"] for m in state[f"{SRD}.entities"]["hero"]["spell_materials"]] == [0, 0]


def test_unsupported_possessions_and_genuine_receiving_reject_atomically():
    state, data, params = _case()
    state[f"{SRD}.entities"]["hero"]["inventory"].append("remaining_weapon")
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin_values(params))
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=600,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="physical possessions"):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before
    state, data, params = _case()
    _paid_hour(state, data, params)
    source = state[f"{SRD}.state"]["astral_projection_sources"][
        state[f"{SRD}.entities"]["hero"]["astral_projection_source_id"]
    ]
    for target in ("hero", source["body_id"]):
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected, match="No receiving"):
            _run(
                "apply_srd_damage", state, target=target, amount=5, damage_type="fire", adjustment=0
            )
        assert state == before
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Astral departure"):
        _run(
            "move_srd_travel_subject",
            state,
            subject=source["form_scene_id"],
            destination=[110, 100, 0],
            plane="material",
        )
    assert state == before


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
            "id": "astral-projection-native",
            "kind": "ruleset_runtime",
            "family": "astral_projection",
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
            "evidence": {"tests": ["paid-hour-original-body-reopen-dismiss-retry"]},
        }
    )
    content["inclusions"].sort(key=lambda r: r["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return bundled_packages._archive((NormalizedPackageDraft(record, payloads, 4), reference))


def test_public_real_hour_reopens_then_paid_projection_and_magic_dismissal_retry_once(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _spell: _case())
    database = tmp_path / "astral.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "astral_projection")
        began, begin_input = _act(owner, "begin_srd_spell_cast", **_begin_values(params))
        assert began.status is ActionStatus.COMMITTED
        assert owner.execute_action(begin_input) == began
        progress, progress_input = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=600,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert progress.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        projected, projection_input = _act(owner, "cast_spell_outside_combat", **params)
        assert projected.status is ActionStatus.COMMITTED
        source_id = owner.snapshot("casting").state[f"{SRD}.entities"]["hero"][
            "astral_projection_source_id"
        ]
        assert owner.execute_action(projection_input) == projected
        projected_state = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == projected_state
        dismissed, dismissal_input = _act(
            owner, "dismiss_srd_astral_projection", caster_state="hero"
        )
        assert dismissed.status is ActionStatus.COMMITTED
        final = owner.snapshot("casting")
        assert (
            final.state[f"{SRD}.state"]["astral_projection_sources"][source_id]["installed"]
            is False
        )
        assert owner.execute_action(dismissal_input) == dismissed
        assert owner.execute_action(projection_input) == projected
        assert owner.execute_action(progress_input) == progress
        assert owner.snapshot("casting") == final
