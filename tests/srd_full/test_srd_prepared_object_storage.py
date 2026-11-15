"""Canonical object storage preserves real paid reversion and the original Scene packet."""

import importlib.util
import json
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

import pytest
import yaml
from scripts import srd_prepared_object_storage as storage
from scripts import srd_spell_object_damage as damage
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus, thaw_json_value
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_telekinesis_fall as scope
from tests import test_srd_spell_travel_teleport_objects as objects
from tests import test_srd_spell_wave6_transformations as forms
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_orders import _admit_scene

SOURCE = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-prepared-object-storage/srd_true_polymorph_objects.py")
)
SOURCE_SHA = "0e1df48d704ddc77c6a3dbd11c852feebe6b4afd46628b184bea9245e8fbc9c5"
HELPER_SHA = "561d4e2ba20b5b8a483b3a4b8f193376032ee9d707b469199cef32feaa85d343"


@lru_cache
def _source():
    assert sha256(SOURCE.read_bytes()).hexdigest() == SOURCE_SHA
    spec = importlib.util.spec_from_file_location("scripts._canonical_true_polymorph", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache
def _runtime():
    assert sha256(Path(damage.__file__).read_bytes()).hexdigest() == HELPER_SHA
    _, base = objects._runtime()
    source = _source().extend_runtime(base.decode())
    encoded = storage.bind_runtime_consumers(source)
    runtime = yaml.load(encoded, Loader=yaml.CSafeLoader)
    assert yaml.load(storage.bind_runtime_consumers(encoded), Loader=yaml.CSafeLoader) == runtime
    return runtime, encoded.encode()


@pytest.fixture
def canonical_scope(monkeypatch):
    monkeypatch.setattr(scope, "_runtime", _runtime)
    drafts = bundled_packages._srd_drafts

    def declared():
        primary, reference = drafts()
        record = deepcopy(primary.record)
        payload = dict(primary.payloads)
        names = _source().PUBLIC_TOOLS
        for name in sorted(names - {row["name"] for row in record["tools"]}):
            record["tools"].append(
                {
                    "name": name,
                    "capability": "dnd.srd.5.2.1.spells",
                    "input_schema": _tool_schema(_runtime()[0]["tools"][name], tool_name=name),
                    "writers": ["dm"],
                    "turn_statuses": ["open"],
                    "state_predicates": [],
                }
            )
        declarations = json.loads(payload["content/content-declarations.json"])
        for inclusion in declarations["inclusions"]:
            if "cast_spell" in inclusion.get("links", {}).get("tools", []):
                inclusion["links"]["tools"] = sorted(set(inclusion["links"]["tools"]) | names)
        payload["content/content-declarations.json"] = canonical_json_bytes(declarations)
        return NormalizedPackageDraft(record, payload, primary.format_version), reference

    monkeypatch.setattr(bundled_packages, "_srd_drafts", declared)
    yield from scope.actual_fall_scope.__wrapped__(monkeypatch)


def _case(_spell):
    state, data, params = forms._setup("true_polymorph", true_polymorph_size="tiny")
    state[f"{SRD}.state"]["spatial_scene"] = None
    params.update(
        true_polymorph_mode="creature_to_object",
        target_willing=True,
        true_polymorph_description="A wooden statuette",
    )
    return state, data, params


def test_default_scene_helpers_and_nonmirror_storage_are_exact():
    _, base = objects._runtime()
    source = _source().extend_runtime(base.decode())
    before = yaml.load(source, Loader=yaml.CSafeLoader)["tools"]
    after = yaml.load(storage.bind_runtime_consumers(source), Loader=yaml.CSafeLoader)["tools"]
    assert sha256(Path(damage.__file__).read_bytes()).hexdigest() == HELPER_SHA
    for name in (
        damage.QUALIFY,
        "prepare_srd_spell_object",
        "prepare_srd_spell_object_node",
        "resolve_srd_spell_object_relation",
    ):
        assert after[name] == before[name]
    for name in ("resolve_srd_spell_object_attack", damage.PACKET, damage.TELEPORT_PACKET):
        old = next(
            e["on_true"]
            for body in storage._lists(before[name]["effects"])
            for e in body
            if e.get("condition") == "_object_damage_applied > 0"
        )
        new = next(
            e["on_true"][0]["on_false"]
            for body in storage._lists(after[name]["effects"])
            for e in body
            if e.get("condition") == "_object_damage_applied > 0"
        )
        assert new == old
    assert damage._packet(before) == damage._packet(
        after
        | {
            "resolve_srd_spell_object_attack": before["resolve_srd_spell_object_attack"],
        }
    )


def test_paid_map_free_object_damage_restores_original_and_retries_once(
    canonical_scope,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(public, "_spell_case", _case)
    database = tmp_path / "canonical-object.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "true_polymorph")
        original = deepcopy(owner.snapshot("casting").state[f"{SRD}.entities"]["guard"])
        paid, command = _act(owner, "cast_spell_outside_combat", **params)
        assert paid.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        target = saved.state[f"{SRD}.entities"]["guard"]
        source_id = target["true_polymorph_object_source_id"]
        assert target["is_object"] is True and target["hp"] == 5
        assert saved.state[f"{SRD}.state"]["spatial_scene"] is None
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert StatelessMcpMechanics(owner).execute_action(command) == paid
        assert owner.snapshot("casting").state == saved.state
        # Actual final typed receiving is private, as in the existing shared object owner.
        actual = thaw_json_value(saved.state)
        _run(
            storage.PACKET,
            actual,
            caster_state="hero",
            object_state="guard",
            component_1_amount=20,
            component_1_damage_type="psychic",
            component_2_amount=1,
            component_2_damage_type="bludgeoning",
        )
        assert actual[f"{SRD}.entities"]["guard"]["hp"] == 4
        assert actual[f"{SRD}.entities"]["guard"]["srd_spell_object"]["hp"] == 4
        result = _run(
            storage.PACKET,
            actual,
            caster_state="hero",
            object_state="guard",
            component_1_amount=6,
            component_1_damage_type="force",
        )
        context = result.result["context"]
        target = actual[f"{SRD}.entities"]["guard"]
        assert context["_object_hp_before"] == 4 and context["_object_hp_after"] == 0
        assert context["_object_reverted"] is True
        assert target["is_alive"] is True and target["hp"] == original["hp"]
        assert target["true_polymorph_object_source_id"] is None
        assert actual[f"{SRD}.state"]["true_polymorph_object_sources"][source_id]["active"] is False
        before = deepcopy(actual)
        with pytest.raises(RulesetPreconditionRejected, match="actual admitted object"):
            _run(
                storage.PACKET,
                actual,
                caster_state="hero",
                object_state="guard",
                component_1_amount=6,
                component_1_damage_type="force",
            )
        assert actual == before
        assert actual[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"] == 2


def test_scene_mirror_uses_canonical_hp_and_reverts_before_object_destruction(canonical_scope):
    state, data, params = _case("true_polymorph")
    _admit_scene(state)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    for body in scene["entities"]:
        body["metadata"]["plane"] = "material"
    original = deepcopy(next(e for e in scene["entities"] if e["id"] == "guard"))
    original_hp = state[f"{SRD}.entities"]["guard"]["hp"]
    parcel = deepcopy(original)
    parcel.update(id="parcel", position=[5, 5, 0], height=1)
    parcel["metadata"] = {"is_object": True, "plane": "material"}
    scene["entities"].append(parcel)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _run(
        "prepare_srd_true_polymorph_object",
        state,
        object_id="parcel",
        name="Actual box",
        source="Plan: box beside the caster",
        material="wood",
        size="small",
        durability="resilient",
        magical=False,
        scene_object=True,
    )
    _run(
        damage.PACKET,
        state,
        caster_state="hero",
        object_id="parcel",
        component_1_amount=2,
        component_1_damage_type="force",
    )
    target = state[f"{SRD}.entities"]["parcel"]
    body = next(
        e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "parcel"
    )
    assert target["hp"] == target["srd_spell_object"]["hp"] == 8
    assert body["metadata"]["object_entity_ref"] == "parcel"
    assert body["metadata"]["srd_spell_object"] == target["srd_spell_object"]
    params["true_polymorph_body"] = [
        {"footprint": {"kind": "box", "half_extents": [0.25, 0.25]}, "height": 1},
    ]
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    source_id = state[f"{SRD}.entities"]["guard"]["true_polymorph_object_source_id"]
    result = _run(
        damage.PACKET,
        state,
        caster_state="hero",
        object_id="guard",
        component_1_amount=6,
        component_1_damage_type="force",
    )
    assert result.result["context"]["_object_reverted"] is True
    assert state[f"{SRD}.entities"]["guard"]["hp"] == original_hp
    body = next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "guard")
    assert body["footprint"] == original["footprint"] and body["height"] == original["height"]
    assert body["metadata"]["participant_ref"] == "guard"
    assert body["metadata"]["srd_spell_object"] is None
    assert state[f"{SRD}.state"]["true_polymorph_object_sources"][source_id]["active"] is False
