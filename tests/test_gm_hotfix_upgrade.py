"""Upgrade the actual published Alpha4 Lite closure through explicit review and adoption.

The retained fixture is byte-for-byte cb8bd3f:campaign-plans/a-debt-in-iron/
setting.sylvia-gm-package. It preserves the published 0.3.15 negative baseline.
The integration fixture preserves main6526904's exact primary record and normalized
declared payloads, with the same immutable reference package. Each variant must retain
its panel contracts; the other predecessor remains blocked rather than bypassing them.
"""

import io
import json
import zipfile
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.bundled_packages import bundled_srd_archive, bundled_srd_version
from sylvia_gm.application.campaign_rules_update import _tool_input_change
from sylvia_gm.application.package_materialization import (
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.application.package_runtime import PACKAGE_RUNTIME_PATH
from sylvia_gm.domain.authority import StateValue, WriterRole, owned_payload
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import ToolDeclaration, content_root_for_record
from sylvia_gm.domain.session import SessionMessageKind
from tests.test_alpha_f3_package_execution import _archive, _runtime_yaml
from tests.test_alpha_f3_package_publication import _file, _install
from tests.test_campaign_rules_update import adopt
from tests.test_campaign_rules_update import prepared as prepared
from tests.test_gm_memory_casting_hotfix import HealingGM, create_cleric_save, play

SRD = "sylvia.srd-lite.bg3"


def test_reviewed_input_changes_keep_authority_and_executable_schema_fences():
    schema = {
        "type": "object",
        "properties": {"choice": {"type": "string"}},
        "required": ["choice"],
        "additionalProperties": False,
    }
    prior = ToolDeclaration(
        name="choose",
        capability="choice",
        input_schema=owned_payload(schema),
        state_predicates=(),
        turn_statuses=("open",),
        writers=("dm",),
    )
    choices = deepcopy(schema)
    choices["properties"]["choice"].update(
        enum=["red", "blue"], **{"x-sylvia-gm-model-required": True}
    )
    incoming = replace(prior, input_schema=owned_payload(choices))
    assert _tool_input_change(prior, incoming) == "changed_input_choices"
    assert _tool_input_change(incoming, prior) == "changed_input_choices"
    guidance = deepcopy(schema)
    guidance["properties"]["choice"]["description"] = "Choose the actual scene fact."
    described = replace(prior, input_schema=owned_payload(guidance))
    assert _tool_input_change(prior, described) == "changed_model_guidance"
    assert _tool_input_change(described, prior) == "changed_model_guidance"
    guidance["properties"]["choice"]["description"] = False
    assert _tool_input_change(prior, replace(prior, input_schema=owned_payload(guidance))) is None
    assert _tool_input_change(prior, replace(incoming, writers=("player",))) is None
    assert _tool_input_change(prior, replace(incoming, capability="another.capability")) is None
    for key, value in [
        ("type", "integer"),
        ("x-sylvia-gm-reference", "participant"),
        ("maxLength", 2),
        ("default", True),
        ("x-sylvia-gm-model-required", "invalid"),
    ]:
        changed = deepcopy(choices)
        changed["properties"]["choice"][key] = value
        assert (
            _tool_input_change(prior, replace(incoming, input_schema=owned_payload(changed)))
            is None
        )
    changed = deepcopy(choices)
    changed["required"] = []
    assert _tool_input_change(prior, replace(incoming, input_schema=owned_payload(changed))) is None
    changed = deepcopy(choices)
    changed["properties"]["choice"]["enum"] = ["green"]
    assert (
        _tool_input_change(incoming, replace(incoming, input_schema=owned_payload(changed))) is None
    )


@pytest.mark.parametrize("refusal", ["literal", "conditional", "later"])
def test_only_an_initial_literal_false_can_retire_an_admitted_contract(tmp_path, refusal):
    runtime = yaml.safe_load(_runtime_yaml())
    original = runtime["tools"]["advance"]["effects"]
    check = {
        "require": {
            "condition": "false" if refusal != "conditional" else "steps == 2",
            "error": "This action is unavailable.",
        }
    }
    runtime["tools"]["advance"]["effects"] = (
        [original[0], check] if refusal == "later" else [check, *original]
    )
    raw = _archive(runtime_bytes=yaml.safe_dump(runtime).encode())
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        record = json.loads(archive.read("manifest.json"))["packages"][0]
        files = {
            f["path"]: archive.read(f"packages/{record['id']}/{f['path']}") for f in record["files"]
        }
    runtime["tools"].pop("advance")
    files[PACKAGE_RUNTIME_PATH] = yaml.safe_dump(runtime).encode()
    record.update(version="1.0.1", tools=[], files=[_file(p, v) for p, v in sorted(files.items())])
    record["content_sha256"] = content_root_for_record(record)
    with AuthorityOwner.open(tmp_path / "retirement.db", "retirement") as owner:
        old = owner.install_package_archive(io.BytesIO(raw), install_id="before")
        new = _install(owner, ((record, files),), "after")
        owner.create_campaign(
            CampaignCreate(
                campaign_id="retained",
                display_name="Retained counter",
                writer=WriterRole.DM,
                package_lock=old.package_lock,
                turn_id="checkpoint",
                initial_state=(
                    StateValue("synthetic.clockwork", "state", {"counter": 9, "bonus": 2}),
                ),
            )
        )
        owner.complete_turn("retained", "checkpoint")
        before = owner.snapshot("retained")
        pin = owner.current_package_lock("retained")
        preview = owner.preview_campaign_rules_update("retained", new.candidate_id)
        assert preview["compatible"] is (refusal == "literal"), preview
        if refusal == "literal":
            assert preview["tool_changes"][0]["retired_unavailable_actions"] == 1
            adopt(owner, preview)
            assert owner.snapshot("retained").state == before.state
            assert owner.package_lock_lineage("retained")[0] == pin
        else:
            with pytest.raises(CommandRejected, match="conflicts"):
                adopt(owner, preview)
            assert owner.snapshot("retained") == before
            assert owner.current_package_lock("retained") == pin


def test_saved_pending_queue_invalidates_review_without_rewriting_it(prepared):
    owner, _old, new = prepared
    owner.create_agent_session(
        campaign_id="running",
        session_id="queue-gm",
        role=WriterRole.DM,
        provider_id="fixture",
        model_id="fixture",
    )
    reviewed = owner.preview_campaign_rules_update("running", new.candidate_id)
    assert reviewed["compatible"]
    queue = {
        "kind": "action_sequence",
        "sequence_id": "retained-queue",
        "steps": [{"step_id": "future", "status": "pending"}],
    }
    owner.append_agent_message(
        session_id="queue-gm",
        message_id="queue-before-update",
        kind=SessionMessageKind.EVIDENCE,
        content=queue,
    )
    before = owner.snapshot("running")
    pin = owner.current_package_lock("running")
    blocked = owner.preview_campaign_rules_update("running", new.candidate_id)
    assert not blocked["compatible"]
    assert "action sequence" in str(blocked["conflicts"])
    with pytest.raises(CommandRejected, match="conflicts"):
        adopt(owner, reviewed)
    assert owner.snapshot("running") == before
    assert owner.current_package_lock("running") == pin
    assert owner.session_context("queue-gm").messages[-1].content == queue
    owner.append_agent_message(
        session_id="queue-gm",
        message_id="queue-cleared",
        kind=SessionMessageKind.EVIDENCE,
        content={**queue, "steps": []},
    )
    assert owner.preview_campaign_rules_update("running", new.candidate_id)["compatible"]


def test_upgrade_keeps_custom_setting_files_and_records(tmp_path):
    fixture = Path(__file__).parent / "fixtures"
    origin = "alpha4" if bundled_srd_version() == "0.3.21" else "integration"
    with zipfile.ZipFile(fixture / "alpha4-lite.sylvia-gm-package") as published:
        world_record = next(
            r
            for r in json.loads(published.read("manifest.json"))["packages"]
            if r["content_kind"] == "campaign_setting"
        )
        world_files = {
            f["path"]: published.read(f"packages/{world_record['id']}/{f['path']}")
            for f in world_record["files"]
        }
    with zipfile.ZipFile(fixture / f"{origin}-lite.sylvia-gm-package") as inherited:
        records = [
            r
            for r in json.loads(inherited.read("manifest.json"))["packages"]
            if r["content_kind"] != "campaign_setting"
        ]
        drafts = [
            NormalizedPackageDraft(
                r,
                {f["path"]: inherited.read(f"packages/{r['id']}/{f['path']}") for f in r["files"]},
                4,
            )
            for r in records
        ]
    world_record = deepcopy(world_record)
    world_record.pop("content_sha256")
    world_record.pop("files")
    world_record.update(version="1.0.0-hotfix-fixture")
    world_record["dependencies"] = [
        {k: v for k, v in d.items() if k != "content_sha256"} for d in world_record["dependencies"]
    ]
    world_files["campaigns/the-rift-awakening/act-1.md"] += (
        b"\nA green bell records a synthetic private garden clue.\n"
    )
    drafts.append(NormalizedPackageDraft(world_record, world_files, 4))
    database = tmp_path / "custom-world.sqlite3"
    with materialize_package_archive(tuple(drafts)) as archive:
        create_cleric_save(database, archive.archive.read())
    with AuthorityOwner.open(database, "hotfix-owner") as owner:
        owner.complete_turn("casting", "casting-turn")
        before = owner.snapshot("casting")
        pin = owner.current_package_lock("casting")
        options = owner.campaign_rules_update_options("casting")
        assert options["candidates"], options
        count = len(owner.package_candidates())
        assert owner.campaign_rules_update_options("casting") == options
        assert len(owner.package_candidates()) == count
        candidate = options["candidates"][0]["candidate_id"]
        preview = owner.preview_campaign_rules_update("casting", candidate)
        assert preview["compatible"], preview["conflicts"]
        assert owner.current_package_lock("casting") == pin
        assert owner.snapshot("casting") == before
        adopt(owner, preview)
        after = owner.snapshot("casting")
        assert after.state == before.state and after.participants == before.participants
        assert after.continuity == before.continuity
        world_pin = next(
            p for p in after.package_lock.entries if p.package_id == world_record["id"]
        )
        world = owner._store.exact_package_manifest(
            *(world_pin.package_id, world_pin.version, world_pin.sha256)
        )
        assert world.version == world_record["version"]
        assert world.metadata == world_record["metadata"]
        for descriptor in world.files:
            assert (
                owner._store.package_file_bytes(
                    *world.identity, descriptor.path, maximum_bytes=descriptor.byte_count
                )
                == world_files[descriptor.path]
            )
        assert owner.package_lock_lineage("casting")[0] == pin
    with AuthorityOwner.open(database, "hotfix-owner") as reopened:
        assert reopened.snapshot("casting") == after


@pytest.mark.parametrize("origin", ["alpha4", "integration"])
def test_retained_campaign_reviews_adopts_heals_and_reopens_without_losing_state(tmp_path, origin):
    database = tmp_path / "alpha4.sqlite3"
    archive = (Path(__file__).parent / f"fixtures/{origin}-lite.sylvia-gm-package").read_bytes()
    current_archive = bundled_srd_archive()
    with zipfile.ZipFile(io.BytesIO(current_archive)) as bundle:
        current_version = next(
            r["version"]
            for r in json.loads(bundle.read("manifest.json"))["packages"]
            if r["role"] == "primary"
        )
    assert current_version == bundled_srd_version()
    if current_version == "0.3.21" and origin == "integration":
        # Alpha4's schema refuses newer main-only panel metadata before it can be installed.
        with pytest.raises(CommandRejected, match="manifest fields are invalid"):
            create_cleric_save(database, archive)
        with AuthorityOwner.open(database, "hotfix-owner") as owner:
            assert not owner.campaign_index()
        return
    create_cleric_save(database, archive)
    with AuthorityOwner.open(database, "hotfix-owner", disconnect_grace_seconds=1.0) as owner:
        owner.append_agent_message(
            session_id="gm",
            message_id="upgrade-basis",
            kind=SessionMessageKind.USER,
            content={"text": "Remember the established scene."},
        )
        owner.update_campaign_file(
            session_id="gm",
            update_id="saved-before-upgrade",
            turn_id="casting-turn",
            basis_message_ids=("upgrade-basis",),
            updates=(
                {
                    "operation": "record_memory",
                    "fact_id": "retained",
                    "category": "continuity",
                    "text": "The north bell rings in pairs.",
                    "visibility": "player_known",
                    "known_by": ["hero"],
                },
                {
                    "operation": "update_scene",
                    "location_id": "north-belfry",
                    "summary": "Waiting under the bronze bell.",
                },
            ),
        )
        owner.complete_turn("casting", "casting-turn")
        before = owner.snapshot("casting")
        old_lock = owner.current_package_lock("casting")
        primary = next(p for p in old_lock.package_lock.entries if p.package_id == SRD)
        assert primary.version == "0.3.15"
        expected = (
            "495f41b1d630893ac8b3cdcc1945a332c6db004dfabe212d661fa2e8b5fd497a"
            if origin == "alpha4"
            else "dd42e6239820558385c316b7e5aacefa2e9b26b457175815570b1faeec2c04eb"
        )
        assert primary.sha256 == expected
        if origin == "integration":
            # Packaged startup installs bare Rulesets; Setting descendants are offered lazily.
            owner.install_package_archive(io.BytesIO(current_archive), install_id="startup-current")
        options = owner.campaign_rules_update_options("casting")
        assert options["candidates"], options
        candidate = options["candidates"][0]["candidate_id"]
        preview = owner.preview_campaign_rules_update("casting", candidate)
        if origin == "alpha4" and current_version != "0.3.21":
            assert not preview["compatible"]
            assert any(".panels." in c["path"] for c in preview["conflicts"])
            with pytest.raises(CommandRejected, match="conflicts"):
                adopt(owner, preview)
            assert owner.snapshot("casting") == before
            assert owner.current_package_lock("casting") == old_lock
            return
        assert preview["compatible"], preview["conflicts"]
        assert preview["tool_changes"]
        assert owner.current_package_lock("casting") == old_lock
        assert owner.snapshot("casting") == before
        receipt = adopt(owner, preview, "adopt-hotfix")
        after = owner.snapshot("casting")
        assert after.state == before.state
        assert after.participants == before.participants
        assert after.continuity == before.continuity
        assert after.revision == before.revision + 1
        assert owner.package_lock_lineage("casting")[0] == old_lock
        assert adopt(owner, preview, "adopt-hotfix").replayed
        owner.open_turn("casting", "after-upgrade")
        provider = HealingGM()
        play(
            owner,
            provider,
            campaign="casting",
            turn="after-upgrade",
            user_text="I cast Cure Wounds on myself.",
        )
        assert provider.cast_calls == 2
        saved = owner.snapshot("casting")
        hero = saved.state[f"{SRD}.entities"]["hero"]
        assert hero["hp"] == 9 and hero["resources"]["spell_slot_1"] == 1
        assert saved.continuity == before.continuity
        assert receipt.resulting_revision == after.revision
    with AuthorityOwner.open(database, "hotfix-owner") as owner:
        assert owner.snapshot("casting") == saved
