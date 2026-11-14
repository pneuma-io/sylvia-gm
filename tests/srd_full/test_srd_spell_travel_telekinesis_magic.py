"""Telekinesis fine control consumes real magic-zone source bodies before its action."""

import os
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_spell_travel_telekinesis_fine import _magic_qualification, emit_late_access
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests import test_srd_spell_travel_telekinesis_fine as fine
from tests.srd_full.test_srd_spell_travel import SRD, _point, _TravelDice, dice
from tests.srd_full.test_srd_spell_travel_telekinesis import _install, _tk_case


@pytest.fixture
def magic_scope(monkeypatch):
    monkeypatch.setattr(
        fall,
        "RUNTIME_PATH",
        Path(os.environ.get("FC14_TK_MAGIC_RUNTIME", str(fall.RUNTIME_PATH))),
    )
    yield from fall.actual_fall_scope.__wrapped__(monkeypatch)


def _ready(monkeypatch, field_origin, *, field_plane="material"):
    state, _ = _tk_case()
    guard = next(row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]
                 if row["id"] == "guard")
    guard["position"] = field_origin
    guard["metadata"]["plane"] = field_plane
    actual = _TravelDice([])
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    budget._run("record_srd_telekinesis_object_size", state, object_id="orb", size="small")
    budget._run("record_srd_object_access", state, object_id="orb", object_kind="door",
                closed=True, locks=[], stuck=False, barred=False)
    _install(state, subject="orb", destination=_point(state, "orb"))
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=1)
    source = state[f"{SRD}.state"]["telekinesis_sources"][-1]
    budget._run("cast_srd_spell_magic_zone", state, caster_state="guard",
                spell="antimagic_field", slot_level=8, concentration_save_modifier=0,
                save_dc=15)
    return state, source, actual


@pytest.mark.parametrize(
    "origin,suppressed",
    [([0, 5, 0], True), ([25, 5, 0], True), ([10.7, 0, 0], True),
     ([11, 0, 0], False), ([35.7, 0, 0], True), ([36, 0, 0], False),
     ([50, 0, 0], False)],
    ids=["caster", "target", "caster-partial-body", "caster-outside",
         "target-partial-body", "target-outside", "both-outside"],
)
def test_fine_magic_uses_actual_caster_and_object_body_overlap(
    magic_scope, monkeypatch, origin, suppressed
):
    state, source, actual = _ready(monkeypatch, origin)
    before = deepcopy(state)
    if suppressed:
        with pytest.raises(RulesetPreconditionRejected, match="suppresses this Telekinesis"):
            fine._operate(state, source)
        assert state == before
    else:
        fine._operate(state, source)
        assert fine._object(state)["metadata"]["srd_object_access"]["closed"] is False
    assert actual.consumed == []


def test_ordinary_access_remains_physical_and_exact_field_end_restores_magic(
    magic_scope, monkeypatch
):
    state, source, actual = _ready(monkeypatch, [25, 5, 0])
    budget._run("operate_srd_locked_object", state, actor_state="guard",
                object_id="orb", operation="open")
    budget._run("operate_srd_locked_object", state, actor_state="guard",
                object_id="orb", operation="close")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="suppresses this Telekinesis"):
        fine._operate(state, source)
    assert state == before
    budget._run("end_concentration", state, caster_state="guard")
    fine._operate(state, source)
    assert fine._object(state)["metadata"]["srd_object_access"]["closed"] is False
    assert actual.consumed == []


def test_field_on_another_actual_plane_does_not_suppress_fine_control(magic_scope, monkeypatch):
    state, source, actual = _ready(monkeypatch, [25, 5, 0], field_plane="ethereal")
    fine._operate(state, source)
    assert fine._object(state)["metadata"]["srd_object_access"]["closed"] is False
    assert actual.consumed == []


def test_actual_clock_expiry_removes_magic_suppression_without_caller_verdict(
    magic_scope, monkeypatch
):
    state, _, actual = _ready(monkeypatch, [25, 5, 0])
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=600)
    result = budget._run("check_srd_telekinesis_object_magic", state,
                         caster_state="hero", object_id="orb", slot_level=5)
    assert result.result["context"]["_magic_zone_effect_allowed"] is True
    assert state[f"{SRD}.state"]["has_spell_magic_zones"] is False
    assert actual.consumed == []


def test_final_definition_hook_uses_later_emitted_magic_owner_and_is_idempotent(magic_scope):
    runtime, encoded = fall._runtime()
    stale = deepcopy(runtime)
    stale["tools"]["check_srd_telekinesis_object_magic"] = _magic_qualification({})
    text = yaml.dump(stale, Dumper=yaml.CSafeDumper, sort_keys=False)
    final = emit_late_access(text)
    repaired = _load_runtime(final)["tools"]
    assert repaired["check_srd_telekinesis_object_magic"] == (
        runtime["tools"]["check_srd_telekinesis_object_magic"])
    assert repaired["operate_srd_telekinesis_object"] == (
        runtime["tools"]["operate_srd_telekinesis_object"])
    assert emit_late_access(final) == final
    assert emit_late_access(encoded.decode()) == encoded.decode()
