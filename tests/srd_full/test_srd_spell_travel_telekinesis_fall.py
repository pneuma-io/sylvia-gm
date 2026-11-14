"""Exact Telekinesis release hands off once to the genuine saved scene-fall owner."""

import json
import os
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, WriterRole, owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from sylvia_gm.runtime._types import ExecutionBudget
from sylvia_gm.spatial import admit_spatial_scene, mutate_spatial_scene
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_spell_travel import SRD, _case, _point, _TravelDice, dice
from tests.srd_full.test_srd_spell_travel_telekinesis import _tk_case

RUNTIME_PATH = Path(
    os.environ.get(
        "FC14_TK_FALL_RUNTIME",
        str(Path(__file__).resolve().parents[2] / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"),
    )
)
EXTRA_PUBLIC_TOOLS = frozenset()


@lru_cache
def _runtime():
    encoded = RUNTIME_PATH.read_bytes()
    return _load_runtime(encoded), encoded


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


@lru_cache
def _archive():
    runtime, encoded = _runtime()
    primary, reference = bundled_packages._srd_drafts()
    record = deepcopy(primary.record)
    record["tools"] = [row for row in record["tools"] if row["name"] in runtime["tools"]]
    declared = {row["name"] for row in record["tools"]}
    for name in sorted(EXTRA_PUBLIC_TOOLS - declared):
        record["tools"].append(
            {
                "name": name,
                "capability": "dnd.srd.5.2.1.spells",
                "writers": ["dm"],
                "turn_statuses": ["open"],
                "state_predicates": [],
                "input_schema": _tool_schema(runtime["tools"][name], tool_name=name),
            }
        )
    for row in record["tools"]:
        row["input_schema"] = _tool_schema(runtime["tools"][row["name"]], tool_name=row["name"])
    record["ui_surfaces"] = []
    payload = dict(primary.payloads) | {"runtime/ruleset.yaml": encoded}
    declarations = json.loads(payload["content/content-declarations.json"])
    declared = {row["name"] for row in record["tools"]}
    package_root = bundled_packages._bundled_srd_root()
    for inclusion in declarations["inclusions"]:
        links = inclusion.get("links", {})
        for path in links.get("artifacts", []):
            if path not in payload:
                payload[path] = (package_root / path).read_bytes().replace(b"\r\n", b"\n")
        if "tools" in links:
            links["tools"] = [name for name in links["tools"] if name in declared]
            if "cast_spell_outside_combat" in links["tools"]:
                links["tools"] = sorted(set(links["tools"]) | EXTRA_PUBLIC_TOOLS)
        if "ui_surfaces" in links:
            links["ui_surfaces"] = []
    payload["content/content-declarations.json"] = canonical_json_bytes(declarations)
    return bundled_packages._archive(
        (
            NormalizedPackageDraft(record, payload, primary.format_version),
            reference,
        )
    )


@pytest.fixture
def actual_fall_scope(monkeypatch):
    _runtime.cache_clear()
    _mechanic.cache_clear()
    _archive.cache_clear()
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(public, "bundled_srd_archive", _archive)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    original = ExecutionBudget.consume_operation
    budgets = {}

    def observe(budget):
        budgets[id(budget)] = budget
        return original(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", observe)
    yield
    assert all(b.max_operations == 10000 for b in budgets.values())
    print("actual maximum operations", max((b.operations for b in budgets.values()), default=0))
    _mechanic.cache_clear()
    _archive.cache_clear()
    _runtime.cache_clear()


def _cast_case(_spell):
    state, data = _tk_case("telekinesis")
    _, _, parameters = _case("telekinesis")
    for grant in data["spellcasting_grants"]:
        if grant["source"] == "wizard":
            grant["spells"].append("feather_fall")
    data["wizard_spellbook"].append("feather_fall")
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("feather_fall")
    feather_state, _, _ = _case("feather_fall")
    feather_actor = feather_state[f"{SRD}.entities"]["hero"]
    state[f"{SRD}.entities"]["hero"]["spell_materials"] = deepcopy(feather_actor["spell_materials"])
    state[f"{SRD}.entities"]["hero"]["inventory"] = deepcopy(feather_actor["inventory"])
    parameters.pop("targets", None)
    parameters.update(telekinesis_subject="guard", telekinesis_destination=[5, 0, 30])
    return state, data, parameters


@pytest.mark.parametrize("feather", [False, True], ids=["unprotected", "paid-feather"])
def test_paid_telekinesis_release_real_fall_reopens_retries_and_lands(
    actual_fall_scope,
    tmp_path,
    monkeypatch,
    feather,
):
    monkeypatch.setattr(public, "_spell_case", _cast_case)
    actual = _TravelDice([(20, 1)] + ([] if feather else [(6, 3), (6, 4), (6, 5)]))
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "telekinesis-fall.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner,
            "telekinesis",
            target_overrides={"hp": 1000, "max_hp": 1000},
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="lift",
            idempotency_key="lift",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=params,
        )
        mechanics = StatelessMcpMechanics(owner)
        paid = mechanics.execute_action(command)
        assert paid.status is ActionStatus.COMMITTED, paid.result
        saved = owner.snapshot("casting")
        assert _point(saved.state, "guard") == [5, 0, 30]
        source = saved.state[f"{SRD}.state"]["telekinesis_sources"][-1]
        assert source["suspended"] is True
        assert "restrained" in saved.state[f"{SRD}.entities"]["guard"]["conditions"]
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
        assert mechanics.execute_action(command) == paid
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        ending = replace(
            command,
            action_id="release",
            idempotency_key="release",
            expected_revision=saved.revision,
            tool_name="end_concentration",
            parameters={"caster_state": "hero"},
        )
        release = owner.execute_action(ending)
        assert release.status is ActionStatus.COMMITTED, release.result
        released = owner.snapshot("casting")
        state = released.state[f"{SRD}.state"]
        trigger = state["pending_srd_action_stack"][-1]
        event = state["pending_srd_actions"][trigger]
        assert event["kind"] == "scene_fall"
        receipt = event["context"]["telekinesis_release_receipts"][0]
        assert receipt["source_id"] == source["id"]
        assert receipt["source_sequence"] == source["sequence"]
        assert receipt["cast_id"] == source["cast_id"]
        assert receipt["source_caster_id"] == "hero" and receipt["creature_id"] == "guard"
        assert receipt["pending_action_id"] == trigger and receipt["handed_off"] is True
        assert state["telekinesis_releases"] == []
        assert state["has_telekinesis_pending_releases"] is False
        assert _point(released.state, "guard") == [5, 0, 30]
        assert released.state[f"{SRD}.entities"]["guard"]["hp"] == 1000
        assert "restrained" not in released.state[f"{SRD}.entities"]["guard"]["conditions"]
        assert owner.execute_action(ending) == release
        assert owner.snapshot("casting") == released
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == released
        if feather:
            _, _, feather_parameters = _case("feather_fall")
            feather_parameters.update(targets=["guard"], reaction_trigger_id=trigger)
            reaction = replace(
                command,
                action_id="feather",
                idempotency_key="feather",
                expected_revision=released.revision,
                tool_name="cast_srd_reaction_spell",
                parameters=feather_parameters,
            )
            protected = owner.execute_action(reaction)
            assert protected.status is ActionStatus.COMMITTED, protected.result
            current = owner.snapshot("casting")
            assert current.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
            assert current.state[f"{SRD}.entities"]["hero"]["reaction_available"] is False
            assert owner.execute_action(reaction) == protected
            assert owner.snapshot("casting") == current
        current = owner.snapshot("casting")
        completion = replace(
            command,
            action_id="fall",
            idempotency_key="fall",
            expected_revision=current.revision,
            tool_name="complete_srd_pending_action",
            parameters={"pending_action_id": trigger},
        )
        finished = owner.execute_action(completion)
        assert finished.status is ActionStatus.COMMITTED, finished.result
        landed = owner.snapshot("casting")
        if feather:
            assert landed.state[f"{SRD}.entities"]["guard"]["scene_fall"]["status"] == "descending"
            assert _point(landed.state, "guard")[2] == 30
        else:
            assert _point(landed.state, "guard")[2] == 0
            assert landed.state[f"{SRD}.entities"]["guard"]["hp"] == 988
            assert "prone" in landed.state[f"{SRD}.entities"]["guard"]["conditions"]
        assert owner.execute_action(completion) == finished
        assert owner.snapshot("casting") == landed
        if feather:
            clock = replace(
                command,
                action_id="land",
                idempotency_key="land",
                expected_revision=landed.revision,
                tool_name="advance_srd_lasting_time",
                parameters={"elapsed_rounds": 1},
            )
            elapsed = owner.execute_action(clock)
            assert elapsed.status is ActionStatus.COMMITTED, elapsed.result
            landed = owner.snapshot("casting")
            assert _point(landed.state, "guard")[2] == 0
            assert landed.state[f"{SRD}.entities"]["guard"]["hp"] == 1000
            assert owner.execute_action(clock) == elapsed
            assert owner.snapshot("casting") == landed
        assert landed.state[f"{SRD}.state"]["pending_srd_action_stack"] == []
        assert landed.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
        assert actual.consumed == ([(20, 1)] if feather else [(20, 1), (6, 3), (6, 4), (6, 5)])
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == landed


def _released_case(monkeypatch, *, alias=False):
    state, _ = _tk_case()
    actual = _TravelDice([(20, 1), (6, 3), (6, 4), (6, 5)])
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    subject = "spatial-guard" if alias else "guard"
    if alias:
        next(
            row
            for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]
            if row["id"] == "guard"
        )["id"] = subject
    budget._run(
        "install_srd_telekinesis",
        state,
        caster_state="hero",
        subject=subject,
        destination=[5, 0, 30],
        slot_level=5,
        save_dc=15,
        concentration_modifier=3,
    )
    source = state[f"{SRD}.state"]["telekinesis_sources"][-1]
    budget._run("release_srd_telekinesis_grip", state, source_id=source["id"])
    return state, source, actual


def test_handoff_retains_actual_participant_id_and_never_reallocates(
    actual_fall_scope,
    monkeypatch,
):
    state, source, actual = _released_case(monkeypatch, alias=True)
    result = budget._run("handoff_srd_telekinesis_releases", state)
    trigger = result.result["context"]["_tk_handoff_action_id"]
    event = state[f"{SRD}.state"]["pending_srd_actions"][trigger]
    assert event["parameters"]["falling_events"][0]["actor_id"] == "guard"
    receipt = event["context"]["telekinesis_release_receipts"][0]
    assert receipt["subject"] == "spatial-guard" and receipt["creature_id"] == "guard"
    assert receipt["source_id"] == source["id"] and receipt["release_serial"] == 1
    after = deepcopy(state)
    again = budget._run("handoff_srd_telekinesis_releases", state)
    assert again.result["context"]["_tk_handoff_action_id"] is None
    assert state == after
    assert actual.consumed == [(20, 1)]


def test_handoff_waits_for_actual_pending_window_then_requalifies_landed_body(
    actual_fall_scope,
    monkeypatch,
):
    state, _, actual = _released_case(monkeypatch)
    budget._run("begin_srd_scene_fall", state, target_states=["guard"], fall_cause="support_lost")
    stack = state[f"{SRD}.state"]["pending_srd_action_stack"][:]
    result = budget._run("handoff_srd_telekinesis_releases", state)
    assert result.result["context"]["_tk_handoff_action_id"] is None
    assert state[f"{SRD}.state"]["pending_srd_action_stack"] == stack
    assert len(state[f"{SRD}.state"]["telekinesis_releases"]) == 1
    budget._run("complete_srd_pending_action", state, pending_action_id=stack[-1])
    assert _point(state, "guard")[2] == 0
    sequence = state[f"{SRD}.state"]["pending_srd_action_sequence"]
    budget._run("handoff_srd_telekinesis_releases", state)
    assert state[f"{SRD}.state"]["telekinesis_releases"] == []
    assert state[f"{SRD}.state"]["has_telekinesis_pending_releases"] is False
    assert state[f"{SRD}.state"]["pending_srd_action_sequence"] == sequence
    assert actual.consumed == [(20, 1), (6, 3), (6, 4), (6, 5)]


def test_handoff_new_real_support_closes_without_fall_allocation(
    actual_fall_scope,
    monkeypatch,
):
    state, _, actual = _released_case(monkeypatch)
    scene = admit_spatial_scene(state[f"{SRD}.state"]["spatial_scene"])
    changed = mutate_spatial_scene(
        scene,
        {
            "map_id": scene.map_id,
            "expected_revision": scene.geometry_revision,
            "target": {"kind": "geometry", "id": "ground"},
            "patch": {"volume": {"kind": "box", "min": [4, -1, 29], "max": [6, 1, 30]}},
        },
    )
    state[f"{SRD}.state"]["spatial_scene"] = changed.scene.as_payload()
    budget._run("handoff_srd_telekinesis_releases", state)
    assert state[f"{SRD}.state"]["telekinesis_releases"] == []
    assert state[f"{SRD}.state"].get("pending_srd_action_stack", []) == []
    assert _point(state, "guard")[2] == 30
    assert actual.consumed == [(20, 1)]
