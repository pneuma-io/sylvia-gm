"""Paid Dispel uses actual source level and retains the Steed's exact minute grace."""

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_phantom_steed_dispel import bind_target_dispel, extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.application import package_runtime
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from tests import test_srd_phantom_steed as steed
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_telekinesis_fall as scope
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_spell_travel import SRD, _TravelDice, dice
from tests.srd_full.test_srd_spell_travel import _case as spell_case

DEPENDENCY = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-phantom-steed-dispel/input-1")
)


@lru_cache
def _runtime():
    """Use the immutable corrected source and a narrow isolated common-removal adapter."""
    manifest = DEPENDENCY / "manifest.json"
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == (
        "cbdd2b8d8165776d49a196b963a4666bc73cc4db772500825d90a326ad8d57b9"
    )
    files = json.loads(manifest.read_text())["files"]
    root = Path(__file__).resolve().parents[2]
    for relative in (
        "scripts/srd_phantom_steed.py",
        "tests/test_srd_phantom_steed.py",
        "scripts/srd_spell_source_instances.py",
        "scripts/srd_spell_removal_adoption.py",
    ):
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == files[relative]
    _, encoded = steed._runtime()
    text = bind_target_dispel(extend_runtime(encoded.decode()))
    runtime = _load_runtime(text)
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


@pytest.fixture
def dispel_scope(monkeypatch):
    monkeypatch.setattr(scope, "_runtime", _runtime)
    monkeypatch.setattr(scope, "EXTRA_PUBLIC_TOOLS", frozenset(steed.PUBLIC_TOOLS))
    checks = []
    execute = package_runtime.execute_tool

    def observe(*args, **kwargs):
        result = execute(*args, **kwargs)
        if not result.success:
            print("Actual Dispel fixture failure:", result.error)
        if result.success and result.tool_name == "dispel_srd_phantom_steed_target":
            checks.append(deepcopy(result.context["_phantom_dispel_checks"]))
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", observe)
    fixture = scope.actual_fall_scope.__wrapped__(monkeypatch)
    next(fixture)
    yield checks
    with pytest.raises(StopIteration):
        next(fixture)


def _case(*, level=3):
    state, data, parameters = steed._paid_case("phantom_steed")
    for grant in data["spellcasting_grants"]:
        if grant["source"] == "wizard":
            grant["spells"].append("dispel_magic")
    data["wizard_spellbook"].append("dispel_magic")
    actor = state[f"{SRD}.entities"]["hero"]
    actor["prepared_spells"].append("dispel_magic")
    actor["resources"]["spell_slot_4"] = 2
    if level != 3:
        parameters.update(slot_resource=f"spell_slot_{level}")
    return state, data, parameters


def _dispel_parameters():
    _, _, params = spell_case("dispel_magic")
    params.pop("targets", None)
    return params | {"target": "horse", "target_distance": 10}


def test_actual_lower_level_source_dispels_without_roll_and_cannot_restart_fade(dispel_scope):
    state, data, parameters = _case()
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=10,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    source = deepcopy(steed._source(state))
    assert source["spell_level"] == 3
    ordinary = _run(
        "dispel_srd_phantom_steed_target",
        state,
        caster="hero",
        caster_state="hero",
        target_state="hero",
        slot_level=3,
        casting_modifier=4,
        casting_ability="intelligence",
        participant_data=data,
    )
    assert ordinary.result["context"]["_phantom_dispel_checks"] == []
    paid = _run("cast_spell_outside_combat", state, participant_data=data, **_dispel_parameters())
    assert source["id"] in str(paid.result["result_card"]["details"])
    check = dispel_scope[-1][-1]
    assert check == {"source": source["id"], "roll": None, "ended": True}
    assert steed._source(state)["status"] == "fading"
    assert steed._source(state)["end_reason"] == "dispel"
    assert steed._source(state)["fade_until"] == 40
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    _run("cast_spell_outside_combat", state, participant_data=data, **_dispel_parameters())
    assert steed._source(state)["fade_until"] == 40


def test_paid_higher_level_check_grace_reopen_retry_and_exact_retirement(
    dispel_scope, monkeypatch, tmp_path
):
    monkeypatch.setattr(public, "_spell_case", lambda _spell: _case(level=4))
    actual = _TravelDice([(20, 1), (20, 20)])
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "steed-dispel.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "phantom_steed")
        base = ActionCommand(
            campaign_id="casting",
            action_id="begin",
            idempotency_key="begin",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="begin_srd_spell_cast",
            parameters=_begin(params),
        )

        def act(identity, name, selected):
            command = replace(
                base,
                action_id=identity,
                idempotency_key=identity,
                expected_revision=owner.snapshot("casting").revision,
                tool_name=name,
                parameters=selected,
            )
            result = owner.execute_action(command)
            assert result.status is ActionStatus.COMMITTED, result.result
            saved = owner.snapshot("casting")
            assert owner.execute_action(command) == result and owner.snapshot("casting") == saved
            return result, saved

        act("begin", "begin_srd_spell_cast", _begin(params))
        act(
            "continue",
            "continue_srd_spell_cast",
            {
                "caster_state": "hero",
                "elapsed_rounds": 10,
                "uninterrupted": True,
                "verbal_component_available": True,
                "somatic_component_available": True,
            },
        )
        _, created = act("complete", "cast_spell_outside_combat", params)
        source = steed._source(created.state)
        assert source["spell_level"] == 4
        failed, intact = act("dispel-failed", "cast_spell_outside_combat", _dispel_parameters())
        failed_check = dispel_scope[-1][-1]
        assert failed_check["ended"] is False and failed_check["roll"].results == (1,)
        assert source["id"] in str(failed.result["result_card"]["details"])
        assert steed._source(intact.state)["status"] == "active"
        assert intact.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == intact
        succeeded, fading = act("dispel-success", "cast_spell_outside_combat", _dispel_parameters())
        check = dispel_scope[-1][-1]
        assert check["ended"] is True and check["roll"].results == (20,)
        assert source["id"] in str(succeeded.result["result_card"]["details"])
        assert check["source"] == source["id"]
        assert steed._source(fading.state)["fade_until"] == 40
        assert fading.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 1
        _, grace = act("grace", "advance_srd_lasting_time", {"elapsed_rounds": 9})
        assert steed._source(grace.state)["status"] == "fading"
        assert grace.state[f"{SRD}.entities"]["horse"]["body_available"] is True
        _, final = act("retire", "advance_srd_lasting_time", {"elapsed_rounds": 1})
        assert steed._source(final.state)["status"] == "retired"
        assert final.state[f"{SRD}.entities"]["horse"]["body_available"] is False
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
