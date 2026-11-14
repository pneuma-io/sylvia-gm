"""Celestial retains the genuine Cylinder branch after the source-qualified fire-ring adapter."""

import hashlib
import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest
from scripts import srd_conjured_presences as owner

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_conjured_presences as presence
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests.srd_full.test_srd_spell_travel import SRD, _TravelDice, dice

actual_fall_scope = presence.actual_fall_scope
conjured_scope = presence.conjured_scope
ROOT = Path(".scratch/conjured-cylinder-adapter-composition")


def _import(relative, digest, name):
    path = ROOT / relative
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    spec = importlib.util.spec_from_file_location("scripts." + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _actual_owners():
    environment = _import(
        "excluded/scripts/srd_spell_environment.py",
        "cf4812f6153e45b19d1a147952c0c2d91821e10b9a6fa0a1ae8b9e5758166d73",
        "_conjured_adapter_environment",
    )
    ring = _import(
        "excluded/scripts/srd_environment_wall_fire_ring.py",
        "59e6c4b61cb7a96657a42b6360444b8e9ce40f1b1ff708862a443547dd6de6b0",
        "_conjured_adapter_ring",
    )
    plain = environment._sphere_geometry_tools()
    wrapped = {"resolve_srd_environment_cylinder_body_segment": ring._cylinder_body(plain)}
    return plain, wrapped


def test_actual_fire_ring_adapter_reproduces_old_failure_and_preserves_exact_ordinary_math():
    plain, wrapped = _actual_owners()
    original = deepcopy(wrapped)
    previous_path = Path(
        ".scratch/conjured-cylinder-origin-frozen-v1/files/scripts/srd_conjured_presences.py"
    )
    assert hashlib.sha256(previous_path.read_bytes()).hexdigest() == (
        "f36eeeecc40b5cdf5e7d0bd129536e45d55dc827ccfc95cc0058f997ebfa6d7c"
    )
    spec = importlib.util.spec_from_file_location(
        "scripts._conjured_adapter_previous", previous_path
    )
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    with pytest.raises(ValueError, match="one existing cylinder source qualification"):
        previous._geometry(wrapped)
    assert owner._geometry(wrapped) == owner._geometry(plain)
    assert owner._intent_geometry(wrapped) == owner._intent_geometry(plain)
    assert wrapped == original


@pytest.mark.parametrize("defect", ["duplicate", "extra_fallback", "unknown_selector"])
def test_changed_or_ambiguous_actual_source_adapter_fails_honestly(defect):
    _, wrapped = _actual_owners()
    tool = wrapped["resolve_srd_environment_cylinder_body_segment"]
    branch = next(effect for effect in tool["effects"] if effect.get("on_false"))
    if defect == "duplicate":
        tool["effects"].append(deepcopy(branch))
    elif defect == "extra_fallback":
        branch["on_false"].append(deepcopy(branch["on_false"][0]))
    else:
        branch["condition"] += " and unknown_projection == true"
    with pytest.raises(ValueError):
        owner._geometry(wrapped)


@pytest.fixture
def adapter_scope(conjured_scope, monkeypatch):
    monkeypatch.setattr(fall, "RUNTIME_PATH", ROOT / "runtime.yaml")


@pytest.mark.parametrize("face,height", [("bottom", 0), ("top", 40)])
def test_actual_wrapped_cylinder_keeps_paid_face_origin_and_native_recipients(
    adapter_scope, actual_fall_scope, monkeypatch, face, height
):
    state, data, params = presence._case(celestial_origin_face=face, conjured_point=[5, 0, height])
    state[f"{SRD}.combat"].update(
        encounter_active=True, turn_active=True, active_actor="hero", phase="turn_active"
    )
    game = _TravelDice([(12, 2)] * 6 + [(12, 3)] * 4 + [(20, 10)])
    monkeypatch.setattr(dice, "_rng", game)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    budget._run("cast_spell", state, participant_data=data, **params)
    source = presence._source(state)
    assert source["origin"] == [5, 0, height] and source["cylinder_origin_face"] == face
    assert source["cylinder_min"] == 0 and source["cylinder_max"] == 40
    assert source["cast_id"] == "srd_cast:1"
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 494
    assert state[f"{SRD}.combat"]["action_available"] is False and not game.rolls


def test_actual_fey_source_cannot_borrow_celestial_body_math(adapter_scope, actual_fall_scope):
    state, data, params = presence._case("conjure_fey", fey_attack_target="")
    presence._cast(state, data, params)
    assert presence._source(state)["mode"] == "fey"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Celestial membership"):
        budget._run(
            "resolve_srd_celestial_body_segment",
            state,
            moving_subject="hero",
            area_id="presence",
            start=[0, 0, 0],
            end=[5, 0, 0],
        )
    assert state == before
