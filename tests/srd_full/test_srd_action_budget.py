"""Shared SRD action-budget invariants; normal-play composition lives in the FC journey."""

from __future__ import annotations

import io
import json
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.application.bundled_packages import bundled_srd_archive
from sylvia_gm.application.package_runtime import PackageRuntimeError, materialize_package_mechanic
from sylvia_gm.domain.authority import (
    ActionStatus,
    MechanicContext,
    StateValue,
    WriterRole,
    owned_payload,
)
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests.srd_full.test_srd_second_wind import _installed_fighter

SRD = "wotc.srd.5.2.1"
ROOT = Path(__file__).resolve().parents[2] / "rulesets/dnd-srd-5.2.1"


@lru_cache
def _runtime_definition(runtime_bytes):
    """Reuse the unchanged package definition; every mechanic still owns its mutable state."""
    return yaml.load(runtime_bytes, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))


@lru_cache
def _mechanic(name):
    """Compile the actual package mechanic, including private shared budget consumers."""
    runtime_bytes = (ROOT / "runtime/ruleset.yaml").read_bytes()
    runtime = _runtime_definition(runtime_bytes)
    record = json.loads((ROOT / "package-record.json").read_text(encoding="utf-8"))
    schema = _tool_schema(runtime["tools"][name], tool_name=name)
    return materialize_package_mechanic(
        package_id=SRD, package_version=record["version"], package_sha256="a" * 64,
        runtime_bytes=runtime_bytes,
        declaration=ToolDeclaration(
            name=name, capability="dnd.srd.5.2.1.action-economy",
            input_schema=owned_payload(schema), writers=("dm",), turn_statuses=("open",),
            state_predicates=(),
        ),
    )


def _state():
    return {
        f"{SRD}.combat": {
            "encounter_active": True, "turn_active": True, "phase": "turn_active",
            "active_actor": "hero", "current_actor": "hero", "action_available": True,
            "normal_action_spent": False, "movement_remaining": 30,
        },
        f"{SRD}.entities": {
            "hero": {
                "hp": 20, "max_hp": 20, "is_alive": True, "fighter_level": 17,
                "speed": 30, "exhaustion_level": 0, "conditions": [],
                "bonus_action_available": True, "reaction_available": True,
                "resources": {"action_surge": 2},
            },
        },
    }


def _run(tool_name, state, *, participant_data=None, **parameters):
    """Apply only successful authoritative mutations; rejected effects cannot leak."""
    result = _mechanic(tool_name)(MechanicContext(
        campaign_id="budget", turn_id="turn", action_id=tool_name,
        state=owned_payload(state), parameters=owned_payload(parameters),
        participants=owned_payload({
            "hero": {
                "participant_id": "hero", "kind": "player_character",
                "validation_status": "validated", "package_id": SRD,
                "data": {"name": "Budget Hero", "speed": 30, **(participant_data or {})},
            },
        }),
    ))
    if result.result["status"] == "committed":
        for mutation in result.mutations:
            state[f"{mutation.namespace}.{mutation.key}"] = deepcopy(mutation.value)
    return result


def _surge(state):
    return _run("use_srd_action_surge", state, actor="hero", actor_state="hero")


def _reject(name, state, **parameters):
    before = deepcopy(state)
    with pytest.raises((RulesetPreconditionRejected, PackageRuntimeError)):
        _run(name, state, **parameters)
    assert state == before


@pytest.mark.parametrize("first", ["surge", "ordinary"])
def test_surge_grants_exactly_one_extra_action_before_or_after_ordinary_action(first):
    state = _state()
    if first == "ordinary":
        assert _run("spend_srd_action", state).result["status"] == "committed"
    assert _surge(state).result["status"] == "committed"
    assert state[f"{SRD}.entities"]["hero"]["resources"]["action_surge"] == 1
    for _ in range(2 if first == "surge" else 1):
        assert _run("spend_srd_action", state).result["status"] == "committed"
    before = deepcopy(state)
    _reject("spend_srd_action", state)
    _reject("use_srd_action_surge", state, actor="hero", actor_state="hero")
    assert state == before
    assert state[f"{SRD}.combat"]["movement_remaining"] == 30
    assert state[f"{SRD}.entities"]["hero"]["bonus_action_available"] is True
    assert state[f"{SRD}.entities"]["hero"]["reaction_available"] is True


@pytest.mark.parametrize("magic_first", [False, True])
def test_surge_never_pays_for_magic_but_preserves_the_ordinary_magic_action(magic_first):
    state = _state()
    if magic_first:
        assert _run("spend_spell_casting_budget", state, caster_state="hero",
                    casting_time="action", slot_resource="cantrip").result["status"] == "committed"
    assert _surge(state).result["status"] == "committed"
    if not magic_first:
        assert _run("spend_spell_casting_budget", state, caster_state="hero",
                    casting_time="action", slot_resource="cantrip").result["status"] == "committed"
    before = deepcopy(state)
    _reject("spend_spell_casting_budget", state, caster_state="hero",
            casting_time="action", slot_resource="cantrip")
    assert state == before
    assert _run("spend_srd_action", state).result["status"] == "committed"
    assert state[f"{SRD}.combat"]["action_available"] is False


@pytest.mark.parametrize("field,value", [
    ("fighter_level", 1), ("hp", 0), ("conditions", ["incapacitated"]),
    ("resources", {"action_surge": 0}),
])
def test_ineligible_surge_has_no_effect(field, value):
    state = _state()
    state[f"{SRD}.entities"]["hero"][field] = value
    before = deepcopy(state)
    _reject("use_srd_action_surge", state, actor="hero", actor_state="hero")
    assert state == before


def test_new_turn_discards_unused_extra_action_and_resets_once_per_turn_use():
    state = _state()
    assert _surge(state).result["status"] == "committed"
    assert _run("end_combat_turn", state, actor="hero").result["status"] == "committed"
    assert _run("begin_combat_turn", state, actor="hero").result["status"] == "committed"
    assert state[f"{SRD}.combat"]["action_surge_available"] is False
    assert state[f"{SRD}.combat"]["action_surge_used"] is False
    assert _surge(state).result["status"] == "committed"
    assert state[f"{SRD}.entities"]["hero"]["resources"]["action_surge"] == 0


def test_generic_resource_spend_cannot_consume_action_surge_without_granting_action():
    state = _state()
    before = deepcopy(state)
    _reject("spend_srd_resource", state, target="hero", resource="action_surge", amount=1)
    assert state == before


def test_committed_surge_exact_retry_after_owner_reopen_does_not_spend_again(tmp_path):
    database = tmp_path / "surge-retry.sqlite3"
    command = ActionCommand(
        campaign_id="budget", action_id="surge-once", idempotency_key="surge-once",
        owner_id="budget-owner", writer=WriterRole.DM, expected_revision=0,
        turn_id="budget-turn", package_id=SRD, tool_name="use_srd_action_surge",
        parameters={"actor": "hero", "actor_state": "hero"},
    )
    with AuthorityOwner.open(database, "budget-owner") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(bundled_srd_archive()), install_id="srd",
        )
        fighter = _installed_fighter(candidate.package_lock, fighter=True)
        fighter = replace(fighter, data={**fighter.data, "level": 17,
                                        "classes": [{"id": "fighter", "level": 17}]})
        initial = _state()
        initial[f"{SRD}.combat"].update(active_actor_kind="character", active_actor_can_act=True)
        owner.create_campaign(CampaignCreate(
            campaign_id="budget", display_name="Synthetic surge retry", writer=WriterRole.DM,
            package_lock=candidate.package_lock, turn_id="budget-turn", participants=(fighter,),
            initial_state=tuple(StateValue(SRD, key, initial[f"{SRD}.{key}"])
                                for key in ("combat", "entities")),
        ))
        receipt = StatelessMcpMechanics(owner).execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("budget")
    with AuthorityOwner.open(database, "budget-owner") as owner:
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("budget") == saved
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["action_surge"] == 1
