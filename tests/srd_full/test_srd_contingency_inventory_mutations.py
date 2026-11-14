"""Actual ordinary inventory bookkeeping ends a paid parent before reacquisition."""

import hashlib
import os
import re
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from scripts.srd_full.srd_contingency_inventory_mutations import (
    CHECK,
    OWNERS,
    _callback,
    bind_tools,
)
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import WriterRole, thaw_json_value
from sylvia_gm.domain.session import SessionMessageKind
from tests import test_srd_contingency_combined_public as combined
from tests import test_srd_public_spell_casting as public_casting

ROOT = Path(__file__).resolve().parents[2]
NATIVE = Path(
    os.environ.get(
        "FC14_CONTINGENCY_INVENTORY_NATIVE",
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/fc14-contingency-inventory-native/ruleset.yaml"),
    )
)
PROOF = Path(
    os.environ.get(
        "FC14_CONTINGENCY_INVENTORY_MUTATION_RUNTIME",
        ROOT / ".scratch/contingency-inventory-mutations-v1/selected-runtime.yaml",
    )
)


def _actual_fragments():
    """Use the exact retained normal runtime's genuine ordinary inventory owners."""
    content = NATIVE.read_bytes()
    assert hashlib.sha256(content).hexdigest() == (
        "64bc7b418f7918e7e7bf4e8bb46079b48fd19e72fbda5199ed6453fa9c011305"
    )
    text = NATIVE.read_text()
    tools = {CHECK: {}}
    for name in OWNERS:
        match = re.search(
            rf"(?ms)^  {name}:\n.*?(?=^  [A-Za-z_][A-Za-z0-9_]*:|^definitions:|^procedures:)",
            text,
        )
        assert match is not None, name
        tools[name] = _load_runtime("tools:\n" + match[0])["tools"][name]
    return tools


def _sequences(sequence):
    yield sequence
    for node in sequence:
        for branch in ("on_true", "on_false"):
            if branch in node:
                yield from _sequences(node[branch])
        if "for_each" in node:
            yield from _sequences(node["for_each"]["do"])


@pytest.mark.parametrize("name", OWNERS)
def test_real_inventory_write_retains_siblings_and_repeats_once(name):
    tools = _actual_fragments()
    original = deepcopy(tools)
    changed = bind_tools(tools)
    assert tools == original
    subject, value = OWNERS[name]
    tool = changed[name]
    matches = [
        (sequence, index)
        for sequence in _sequences(tool["effects"])
        for index, node in enumerate(sequence)
        if node.get("set") == {"field": subject + ".inventory", "value": value}
    ]
    assert len(matches) == 1
    sequence, index = matches[0]
    assert sequence[index + 1] == _callback(subject)
    assert bind_tools(tools | changed) == changed
    stripped = deepcopy(tool)
    for sequence in _sequences(stripped["effects"]):
        sequence[:] = [node for node in sequence if node != _callback(subject)]
    assert stripped == original[name]


@pytest.fixture(scope="module")
def actual_archive():
    original = combined.PROOF, combined.PUBLIC
    combined.PROOF = PROOF
    combined.PUBLIC = combined.PUBLIC | {"update_srd_carried_inventory"}
    combined._runtime.cache_clear()
    combined._selected_archive.cache_clear()
    try:
        archive = combined._archive
        primary, _ = combined._srd_drafts()
        declaration = next(
            row for row in primary.record["tools"] if row["name"] == "update_srd_carried_inventory"
        )
        assert "x-sylvia-gm-character-update" in declaration["input_schema"]

        def preserve_ordinary_bookkeeping(drafts):
            selected, reference = drafts
            record = deepcopy(selected.record)
            record["tools"] = [
                deepcopy(declaration) if row["name"] == declaration["name"] else row
                for row in record["tools"]
            ]
            return archive(
                (
                    NormalizedPackageDraft(record, selected.payloads, selected.format_version),
                    reference,
                )
            )

        with patch.object(combined, "_archive", preserve_ordinary_bookkeeping):
            yield combined._selected_archive()
    finally:
        combined.PROOF, combined.PUBLIC = original
        combined._runtime.cache_clear()
        combined._selected_archive.cache_clear()


def _bookkeep(owner, session_id, message_id, updates):
    """Use the shared public campaign-file route; its inventory runtime stays private."""
    owner.append_agent_message(
        session_id=session_id,
        message_id=message_id,
        kind=SessionMessageKind.USER,
        content={"text": "Record the completed physical statuette transfer."},
    )
    parameters = {
        "state_change": "changed",
        "state_change_summary": "The carried statuette changed hands.",
        "character_updates": [
            row | {"expected_revision": owner.snapshot("casting").revision} for row in updates
        ],
    }
    result = StatelessMcpMechanics(owner).commit_campaign_file(
        "casting", session_id=session_id, basis_message_id=message_id, parameters=parameters
    )
    assert result["status"] == "campaign_file_updated"
    return parameters, result


def test_paid_parent_ends_on_real_inventory_transfer_and_stays_ended_after_saved_return(
    tmp_path, monkeypatch, actual_archive
):
    state, data, parameters, _ = combined._case(monkeypatch)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _: (state, data, parameters))
    monkeypatch.setattr(public_casting, "bundled_srd_archive", lambda: actual_archive)
    database = tmp_path / "contingency-inventory-loss.sqlite3"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        create = owner.create_campaign
        monkeypatch.setattr(
            owner,
            "create_campaign",
            lambda request: create(
                replace(
                    request,
                    participants=(
                        *request.participants,
                        replace(request.participants[0], participant_id="guard"),
                    ),
                )
            ),
        )
        public_casting._create_spell_campaign(owner, "contingency")
        parent_receipt, parent_command = combined._cast(owner, parameters, 100)
        session_id = owner.create_agent_session(
            campaign_id="casting", role=WriterRole.DM, provider_id="scripted", model_id="scripted"
        ).session_id
        armed = owner.snapshot("casting")
        hero = thaw_json_value(armed.state[f"{combined.SRD}.entities"]["hero"])
        guard = thaw_json_value(armed.state[f"{combined.SRD}.entities"]["guard"])
        source_id = hero["contingency_source_id"]
        source = armed.state[f"{combined.SRD}.state"]["contingency_sources"][source_id]
        statuette = source["statuette_item"]
        assert source["installed"] is True
        lot = next(row for row in hero["spell_materials"] if row["inventory_item"] == statuette)
        kept = [item for item in hero["inventory"] if item != statuette]
        kept_materials = [row for row in hero["spell_materials"] if row != lot]
        guard_inventory = guard.get("inventory", []) + [statuette]
        guard_materials = guard.get("spell_materials", []) + [lot]
        transfer_parameters, transfer_result = _bookkeep(
            owner,
            session_id,
            "statuette-given",
            [
                {
                    "participant_id": "hero",
                    "fields": {"inventory": kept, "spell_materials": kept_materials},
                },
                {
                    "participant_id": "guard",
                    "fields": {"inventory": guard_inventory, "spell_materials": guard_materials},
                },
            ],
        )
        ended = owner.snapshot("casting")
        record = ended.state[f"{combined.SRD}.state"]["contingency_sources"][source_id]
        assert record["installed"] is False and record["status"] == "ended"
        assert ended.state[f"{combined.SRD}.entities"]["hero"]["has_contingency"] is False
        assert statuette not in ended.state[f"{combined.SRD}.entities"]["hero"]["inventory"]
        assert statuette in ended.state[f"{combined.SRD}.entities"]["guard"]["inventory"]
        assert ended.state[f"{combined.SRD}.entities"]["hero"]["resources"] == hero["resources"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == ended
        return_parameters, return_result = _bookkeep(
            owner,
            session_id,
            "statuette-returned",
            [
                {
                    "participant_id": "guard",
                    "fields": {
                        "inventory": guard.get("inventory", []),
                        "spell_materials": guard.get("spell_materials", []),
                    },
                },
                {
                    "participant_id": "hero",
                    "fields": {
                        "inventory": hero["inventory"],
                        "spell_materials": hero["spell_materials"],
                    },
                },
            ],
        )
        restored = owner.snapshot("casting")
        assert restored.state[f"{combined.SRD}.state"]["contingency_sources"][source_id] == record
        assert restored.state[f"{combined.SRD}.entities"]["hero"]["has_contingency"] is False
        assert "water_breathing" not in str(
            restored.state[f"{combined.SRD}.state"].get("turn_effects", {})
        )
        monkeypatch.setattr(dice, "roll", lambda *_: pytest.fail("A saved retry must not reroll."))
        assert owner.execute_action(parent_command) == parent_receipt
        for message, parameters, result in (
            ("statuette-given", transfer_parameters, transfer_result),
            ("statuette-returned", return_parameters, return_result),
        ):
            assert (
                StatelessMcpMechanics(owner).commit_campaign_file(
                    "casting",
                    session_id=session_id,
                    basis_message_id=message,
                    parameters=parameters,
                )
                == result
            )
        assert owner.snapshot("casting") == restored
