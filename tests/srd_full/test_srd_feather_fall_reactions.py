"""Actual public Feather Fall uses saved geometry, paid Reactions and one completion."""

import json
from copy import deepcopy
from functools import lru_cache

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_reaction_spells import extend_runtime, normalize_pending_spell_guards
from scripts.srd_full.srd_spell_source_instances import extend_runtime as extend_sources
from scripts.srd_full.srd_timed_actor_defenses import _RuntimeDumper

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_feather_fall as falling
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run


@lru_cache
def _runtime():
    runtime, content = falling._runtime()
    text = extend_sources(
        normalize_pending_spell_guards(content.decode()),
        requirements=runtime["definitions"]["spell_casting"],
        definitions=runtime["definitions"]["spells"],
    )
    text = extend_runtime(text)
    runtime = yaml.safe_load(text)
    return runtime, yaml.dump(runtime, Dumper=_RuntimeDumper, sort_keys=False).encode()


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


def _begin(state, targets=("guard",)):
    return _run(
        "begin_srd_scene_fall", state, target_states=list(targets), fall_cause="departure"
    ).result["context"]["_pending_action_id"]


def test_paid_public_fall_reaction_saved_reopened_and_completed_once():
    state, data, parameters = falling._case(height=120)
    trigger = _begin(state)
    state = json.loads(json.dumps(state))
    hero = state[f"{SRD}.entities"]["hero"]
    slots = hero["resources"]["spell_slot_1"]
    parameters.update(targets=["guard"], reaction_trigger_id=trigger)
    _run("cast_srd_reaction_spell", state, participant_data=data, **parameters)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["resources"]["spell_slot_1"] == slots - 1
    assert hero["reaction_available"] is False
    sources = [x for x in state[f"{SRD}.state"]["turn_effects"] if x.get("spell") == "feather_fall"]
    assert len(sources) == 1 and sources[0]["cast_id"]
    assert sources[0]["source_caster_id"] == "hero"
    assert falling._height(state) == 120
    state = json.loads(json.dumps(state))
    result = _run("complete_srd_pending_action", state, pending_action_id=trigger).result
    assert result["result_card"]["title"] == "Falling creatures"
    assert falling._height(state) == 120 and falling._actor(state)["hp"] == 1000
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("complete_srd_pending_action", state, pending_action_id=trigger)
    assert state == before
    _run("advance_srd_lasting_time", state, elapsed_rounds=2)
    assert falling._height(state) == 0 and falling._actor(state)["hp"] == 1000
    assert falling._actor(state)["scene_fall"]["status"] == "landed"


def test_unprotected_public_fall_completes_real_damage_once():
    state, _, _ = falling._case(height=30)
    trigger = _begin(state)
    _run("complete_srd_pending_action", state, pending_action_id=trigger)
    assert falling._height(state) == 0 and falling._actor(state)["hp"] < 1000
    assert falling._actor(state)["scene_fall"]["status"] == "landed"


@pytest.mark.parametrize("invalid", ["not_falling", "six", "blind_trigger"])
def test_invalid_public_reaction_retains_slot_reaction_and_exact_event(invalid):
    targets = ("guard",) if invalid != "six" else tuple(f"fall-{i}" for i in range(6))
    state, data, parameters = falling._case(height=120, targets=targets)
    trigger = _begin(state, targets)
    parameters.update(targets=["guard"], reaction_trigger_id=trigger)
    if invalid == "not_falling":
        parameters["targets"] = ["other"]
    elif invalid == "six":
        parameters["targets"] = list(targets)
    else:
        state[f"{SRD}.entities"]["hero"]["conditions"] = ["blinded"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_srd_reaction_spell", state, participant_data=data, **parameters)
    assert state == before
