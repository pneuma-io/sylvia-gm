"""A genuine common cast reference owns dismissal, paid Dispel and queued exit falls."""

import hashlib
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts import srd_spell_removal_adoption, srd_spell_removals, srd_spell_source_instances
from scripts.srd_full.srd_courier_chronology import _emit
from scripts.srd_full.srd_divination_spells import _normalize
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_tsunami_source_adoption import bind_runtime

from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_telekinesis_fall as scope
from tests import test_srd_tsunami as tsunami
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_spell_travel import SRD, _point, _TravelDice, dice

DEPENDENCIES = {
    "scripts/srd_spell_source_instances.py": (
        "44207161d717dc63846490b38bb4b1a4ccf8c2ef7de92de220ddbc0a9e4d5b66"
    ),
    "scripts/srd_spell_removal_adoption.py": (
        "b21333a295d2260ed85f64f9a924bf512eac54975c4a0e7d7cda2eed3d52ce42"
    ),
    "scripts/srd_delayed_blast_completion.py": (
        "8f13af2a193e37a3eb7ad219577f34e72ad81b0aa7741afc7a03eed54b82fa08"
    ),
    "scripts/srd_delayed_blast_fireball.py": (
        "ac2818ccd388d29e7a4dfd0c75c5f6fc79c759d250edf30d1db41f35a04cd585"
    ),
    "scripts/srd_sunbeam.py": "2e7511b69b99652fa7125d6cc45dd55fc841e65618ed8e0c71bedbd57ba7efda",
}


@lru_cache
def _runtime():
    root = Path(__file__).resolve().parents[2]
    for relative, expected in DEPENDENCIES.items():
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == expected
    _, encoded = tsunami._runtime()
    text = srd_spell_source_instances.extend_runtime(encoded.decode())
    # This selected diagnostic excludes Etherealness's public wrapper. Compose
    # the genuine common factories needed here without regenerating that owner.
    tools = _load_runtime(text)["tools"]
    changed = srd_spell_removals._tools(tools)
    for name in ("cast_srd_recovery", "dispel_srd_target_sources"):
        changed[name] = deepcopy(tools[name])
    srd_spell_removal_adoption.extend_tools(tools, changed)
    _normalize(changed)
    text = _emit(text, changed)
    text = bind_runtime(text)
    runtime = _load_runtime(text)
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


@pytest.fixture
def adoption_scope(monkeypatch):
    monkeypatch.setattr(scope, "_runtime", _runtime)
    monkeypatch.setattr(
        scope, "EXTRA_PUBLIC_TOOLS", frozenset(tsunami.PUBLIC_TOOLS | {"dismiss_srd_spell"})
    )
    yield from scope.actual_fall_scope.__wrapped__(monkeypatch)


def _case(_spell="tsunami"):
    state, data, params = tsunami._case()
    data["spellcasting_grants"][0]["spells"].append("dispel_magic")
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("dispel_magic")
    return state, data, params


def _dispel(source_id, *, point=None):
    _, _, params = tsunami.spell_case("dispel_magic")
    return params | {
        "recovery_choice": "tsunami",
        "recovery_source_id": source_id,
        "recovery_position": point or [25, 0, 20],
    }


def test_actual_cast_reference_dismissal_preserves_action_budget_and_rejects_forgery(
    adoption_scope, monkeypatch
):
    state, data, params = _case()
    actual = _TravelDice([(10, 1)] * 6 + [(20, 20)])
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    tsunami._complete(state, data, params)
    source = deepcopy(tsunami._source(state))
    hero = state[f"{SRD}.entities"]["hero"]
    assert source["cast_id"] == hero["last_spell_source_cast_id"] == hero["concentration_cast_id"]
    assert source["cast_id"].startswith("srd_cast:") and source["source_caster_id"] == "hero"
    assert source["spell_level"] == 8 and source["ongoing_duration"] is True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("dismiss_srd_spell", state, caster_state="guard", cast_id=source["cast_id"])
    assert state == before
    with pytest.raises(RulesetPreconditionRejected):
        _run("dismiss_srd_spell", state, caster_state="hero", cast_id="srd_cast:forged")
    assert state == before
    _run("dismiss_srd_spell", state, caster_state="hero", cast_id=source["cast_id"])
    assert not tsunami._source(state)["active"]
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    assert state[f"{SRD}.entities"]["hero"]["resources"] == hero["resources"]
    assert state[f"{SRD}.state"]["has_tsunami_pending_exits"]
    _run("settle_srd_tsunami_exits", state)
    assert not state[f"{SRD}.state"]["has_tsunami_pending_exits"]
    assert _point(state, "guard") == [5, 0, 0]


def test_paid_selected_dispel_reopen_retry_preserves_failure_then_real_exit_fall(
    adoption_scope, monkeypatch, tmp_path
):
    def elevated_case(_spell="tsunami"):
        state, data, params = _case()
        scene = state[f"{SRD}.state"]["spatial_scene"]
        next(b for b in scene["entities"] if b["id"] == "guard")["position"] = [5, 0, 20]
        scene["geometry"].append(
            {
                "id": "guard-platform",
                "map_id": "information",
                "volume": {"kind": "box", "min": [4.4, -1, 19], "max": [5.6, 1, 20]},
                "active": True,
                "movement": "blocking",
                "occupies_cells": True,
                "visibility": "opaque",
                "metadata": {"plane": "material", "ground": True},
                "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
            }
        )
        return state, data, params

    monkeypatch.setattr(public, "_spell_case", elevated_case)
    actual = _TravelDice([(10, 1)] * 6 + [(20, 20), (20, 20), (20, 1), (20, 14), (6, 2), (6, 3)])
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "tsunami-removal.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner, "tsunami", target_overrides={"hp": 500, "max_hp": 500}
        )
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
            "minute",
            "continue_srd_spell_cast",
            {
                "caster_state": "hero",
                "elapsed_rounds": 10,
                "uninterrupted": True,
                "verbal_component_available": True,
                "somatic_component_available": True,
                "material_component_available": True,
            },
        )
        _, cast = act("cast", "cast_spell_outside_combat", params)
        source = deepcopy(tsunami._source(cast.state))
        assert source["cast_id"].startswith("srd_cast:")
        act(
            "swim-off-platform",
            "swim_srd_tsunami",
            {"actor_state": "guard", "source_id": source["id"], "destination": [10, 0, 20]},
        )
        failed, failure = act("failed-dispel", "cast_spell_outside_combat", _dispel(source["id"]))
        assert tsunami._source(failure.state)["active"]
        assert failure.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
        assert "False" in str(failed.result)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == failure
        succeeded, ended = act(
            "successful-dispel", "cast_spell_outside_combat", _dispel(source["id"])
        )
        assert not tsunami._source(ended.state)["active"]
        assert ended.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 1
        assert source["id"] in str(succeeded.result)
        assert ended.state[f"{SRD}.state"]["has_tsunami_pending_exits"]
        assert _point(ended.state, "guard") == [10, 0, 20]
        opened, _ = act("actual-exit", "settle_srd_tsunami_exits", {})
        pending_id = opened.result["context"]["_pending_action_id"]
        _, landed = act(
            "complete-fall", "complete_srd_pending_action", {"pending_action_id": pending_id}
        )
        assert _point(landed.state, "guard") == [10, 0, 0]
        assert landed.state[f"{SRD}.entities"]["guard"]["hp"] == 492
        assert "prone" in landed.state[f"{SRD}.entities"]["guard"]["conditions"]
        assert actual.consumed == [(10, 1)] * 6 + [
            (20, 20),
            (20, 20),
            (20, 1),
            (20, 14),
            (6, 2),
            (6, 3),
        ]
