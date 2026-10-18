"""The Lite launch migration retains strict shared bounds and archival isolation."""

from dataclasses import replace
from copy import deepcopy

import pytest
import yaml

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import PackageRuntimeError, materialize_package_mechanic
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.domain.authority import owned_payload, ActionStatus
from sylvia_gm.runtime._types import ExecutionBudget
from tests.test_alpha_f3_package_execution import _archive, _command, _create, _runtime_yaml, _tool


def test_archived_full_binding_cannot_reactivate_while_other_rulesets_remain_supported():
    declaration = ToolDeclaration(
        name="advance", capability="clockwork.counter", input_schema=owned_payload(_tool()["input_schema"]),
        writers=("dm",), turn_statuses=("open",), state_predicates=(),
    )
    arguments = dict(package_version="1.0.0", package_sha256="a" * 64,
                     runtime_bytes=_runtime_yaml(), declaration=declaration)
    with pytest.raises(PackageRuntimeError, match="Full is archived"):
        materialize_package_mechanic(package_id="wotc.srd.5.2.1", **arguments)
    assert callable(materialize_package_mechanic(package_id="synthetic.clockwork", **arguments))


def test_default_twenty_thousand_operations_are_shared_and_overrun_rolls_back_and_reopens(tmp_path):
    assert ExecutionBudget().max_operations == 20_000
    assert ExecutionBudget().max_call_depth == 32
    runtime = yaml.safe_load(_runtime_yaml())
    runtime["procedures"] = {}
    # One collection snapshot, one loop effect, and 6,666 nested calls with
    # two child effects each: exactly 20,000 shared operations. Each child is well below
    # the cap; resetting accounting at nested calls would allow the overrun.
    runtime["tools"]["advance"]["effects"] = [
        {"set": {"field": "_items", "value": "combat.items"}},
        {"for_each": {"collection": "_items", "as": "_item",
                      "do": [{"call": "child"}]}},
    ]
    runtime["tools"]["child"] = {"description": "Nested accounting probe.", "parameters": [],
        "effects": [{"increment": {"field": "state.counter", "amount": 1}},
                    {"increment": {"field": "state.bonus", "amount": 1}}]}
    database = tmp_path / "budget.sqlite3"
    with AuthorityOwner.open(database, "owner-runtime") as owner:
        _create(owner, _archive(runtime_bytes=yaml.safe_dump(runtime, sort_keys=False).encode()))
        # Persist the probe collection through the campaign's normal initial state.
        from sylvia_gm.application.authority import CampaignCreate
        from sylvia_gm.domain.authority import StateValue, WriterRole
        lock = owner.snapshot("campaign-runtime").package_lock
        for campaign_id, count in [("within", 6666), ("over", 6667)]:
            owner.create_campaign(CampaignCreate(campaign_id=campaign_id, display_name=campaign_id,
                writer=WriterRole.DM, package_lock=lock, turn_id="turn-runtime",
                initial_state=(StateValue("synthetic.clockwork", "state",
                    {"counter": 0, "bonus": 0}),
                    StateValue("synthetic.clockwork", "combat", {"items": [0] * count}))))
        command = replace(_command(revision=0, suffix="within", steps=1), campaign_id="within")
        receipt = owner.execute_action(command)
        saved = owner.snapshot("within")
        assert saved.state["synthetic.clockwork.state"]["counter"] == 6666
        assert saved.state["synthetic.clockwork.state"]["bonus"] == 6666
        before = owner.snapshot("over")
        over = replace(command, campaign_id="over", action_id="over", idempotency_key="over")
        rejected = owner.execute_action(over)
        assert rejected.status is ActionStatus.FAILED
        assert owner.execute_action(over) == rejected
        assert owner.snapshot("over").state == before.state
        assert owner.snapshot("over").revision == before.revision + 1
        # The durable failed action advances the event revision once; mechanic
        # state remains byte-for-byte unchanged and retries append no new event.
        failed_revision = owner.snapshot("over").revision
        assert owner.execute_action(over) == rejected
        assert owner.snapshot("over").revision == failed_revision
        assert owner.execute_action(command) == receipt
        failed_state = deepcopy(before.state)
    with AuthorityOwner.open(database, "owner-runtime") as owner:
        assert owner.snapshot("over").state == failed_state
        assert owner.snapshot("within").state == saved.state
        assert owner.execute_action(command) == receipt


def test_exact_final_roster_and_strict_rejection_preserve_state():
    from scripts.srd_lite_catalogue import SELECTED, RETAINED_FULL_AUTHORED_SPELLS, build_catalogue
    from tests.test_srd_spell_result_cards import _spell_case, _spell_definitions
    from tests.test_srd_action_budget import _run
    from sylvia_gm.domain.errors import RulesetPreconditionRejected
    assert len(SELECTED) == 117 and len(RETAINED_FULL_AUTHORED_SPELLS) == 21
    assert set(_spell_definitions()) == SELECTED
    assert {row["identity"] for row in build_catalogue()["profiles"]} == SELECTED
    state, data, parameters = _spell_case("ice_knife")
    before = deepcopy(state)
    for spell in ("barkskin", "resistance", "mass_heal", "protection_from_poison"):
        with pytest.raises(RulesetPreconditionRejected, match="SRD Lite"):
            _run("cast_spell", state, participant_data=data, **(parameters | {"spell": spell}))
        assert state == before


def test_stale_full_library_record_is_preserved_but_absent_from_launch_discovery(tmp_path, monkeypatch):
    from sylvia_gm.interfaces.owner_process import _campaign_creation_catalog
    from sylvia_gm.application.interview_content import resolve_interview_content
    from sylvia_gm.domain.authority import PackageLock
    from sylvia_gm.domain.errors import CommandRejected
    with AuthorityOwner.open(tmp_path / "catalog.sqlite3", "owner-runtime") as owner:
        _create(owner, _archive())
        original = owner.package_candidates()
        candidate = original[0]
        entry = replace(candidate.package_lock.entries[0], package_id="wotc.srd.5.2.1")
        stale = replace(candidate, candidate_id="archived-full", package_lock=PackageLock((entry,)))
        monkeypatch.setattr(owner, "package_candidates", lambda: (stale, *original))
        rows = _campaign_creation_catalog(tmp_path, owner)
        assert not any(row.get("id") in {"wotc.srd.5.2.1", "archived-full"} for row in rows)
        assert stale in owner.package_candidates()
        assert any(row.get("id") == "synthetic.clockwork" for row in rows)
        with pytest.raises(CommandRejected, match="Full is archived"):
            resolve_interview_content(owner, tmp_path, {"ruleset": {"candidate_id": "wotc.srd.5.2.1"}})
