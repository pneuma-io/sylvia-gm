"""Explicit source adoption retains custom world/rules and publishes new declarations."""

import io
import json
from dataclasses import replace

import pytest
import yaml

from sylvia_gm.agent._builder_session import _campaign_plan_example
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.application.campaign_plan_sources import campaign_plan_sources
from sylvia_gm.application.library_sources import LibrarySourceReader
from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.application.plan_bundle import export_plan_bundle, import_plan_bundle
from sylvia_gm.application.reusable_plans import load_reusable_campaign_plan
from sylvia_gm.builder._package_snapshots import (
    retained_package_drafts,
    ruleset_copy,
    select_snapshot_sources,
)
from sylvia_gm.builder._setting_materializer import _world_draft, materialize_setting_draft
from sylvia_gm.domain.authority import StateValue, WriterRole
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.provider._base import ProviderResponse, ToolCall
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_builder_library import (
    LibraryProvider,
    install_character_packages,
    launch_library_builder,
)
from tests.test_plan_snapshot_publication import write_call


@pytest.mark.parametrize("conflicting_support", [False, True])
def test_rules_update_retains_shared_world_dependency_once(tmp_path, conflicting_support):
    with AuthorityOwner.open(tmp_path / "shared.db", "owner") as owner:
        original, _ = install_character_packages(owner)
        updated, _, _, _ = newer_source(owner, original, setting=True)
        drafts = retained_package_drafts(owner, updated.candidate_id, include_setting=True)
        support = next(item for item in drafts if item.record["content_kind"] == "support")
        revised = []
        for item in drafts:
            if item.record["content_kind"] == "campaign_setting":
                item = replace(
                    item,
                    record={
                        **item.record,
                        "dependencies": [
                            *item.record["dependencies"],
                            {
                                "id": support.record["id"],
                                "version": support.record["version"],
                                "namespace": "world_manual",
                                "kind": "reference",
                            },
                        ],
                    },
                )
            elif item == support and conflicting_support:
                item = replace(item, payloads={"manual.md": b"A conflicting local world manual."})
            revised.append(item)
        (tmp_path / "ruleset-source.json").write_text(
            json.dumps(
                {
                    "candidate_id": updated.candidate_id,
                    "expected_closure_sha256": updated.closure_sha256,
                }
            )
        )
        if conflicting_support:
            with pytest.raises(CommandRejected, match="Setting-owned dependency"):
                select_snapshot_sources(owner, tmp_path, tmp_path, tuple(revised))
        else:
            selected = select_snapshot_sources(owner, tmp_path, tmp_path, tuple(revised))
            assert [item.record["id"] for item in selected].count(support.record["id"]) == 1
            with materialize_package_archive(selected) as materialized:
                owner.install_package_archive(materialized.archive, install_id="shared-update")


def test_new_setting_owns_updated_rules_declarations_and_support_files(tmp_path):
    workspace = tmp_path / "builder"
    root = workspace / "setting-draft"
    (root / "files").mkdir(parents=True)
    (root / "files" / "world.md").write_text("# Island\nA new world with updated signal rules.")
    with AuthorityOwner.open(tmp_path / "setting.db", "owner") as owner:
        original, _ = install_character_packages(owner)
        updated, record, _, _ = newer_source(owner, original, setting=False)
        (root / "ruleset-source.json").write_text(
            json.dumps(
                {
                    "candidate_id": updated.candidate_id,
                    "expected_closure_sha256": updated.closure_sha256,
                }
            )
        )
        record["version"] = "0.2.0-island"
        (root / "package-records").mkdir()
        (root / "package-records" / f"{record['id']}.json").write_text(json.dumps(record))
        with materialize_setting_draft(
            owner,
            workspace,
            candidate_id=original.candidate_id,
            package_id="local.island",
            version="1.0.0",
            title="Island",
        ) as materialized:
            saved = owner.install_package_archive(materialized.archive, install_id="island")
        sources = campaign_plan_sources(owner, saved.package_lock)
        assert sources.ruleset.version == "0.2.0-island"
        assert sources.read_setting("world.md").startswith("# Island")
        saved_record = owner.package_record(
            sources.ruleset.package_id,
            sources.ruleset.version,
            sources.ruleset.content_sha256,
        )
        assert "source_ping" in {tool["name"] for tool in saved_record["tools"]}
        assert {"local.signal-manual"} <= {entry.package_id for entry in saved.package_lock.entries}
        assert (
            "rank"
            in owner.package_participant_declarations(saved.candidate_id)[0].schema["properties"]
        )
        assert owner.package_candidate(original.candidate_id) == original
        assert owner.package_candidate(updated.candidate_id) == updated


def add_ping_tool(record, runtime, content, name, value):
    record["tools"].append(
        {
            "name": name,
            "capability": "signal-static.consequence",
            "writers": ["dm"],
            "turn_statuses": ["open"],
            "state_predicates": [],
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        }
    )
    runtime["tools"][name] = {
        "description": "Save a signal count.",
        "parameters": [],
        "effects": [{"set": {"field": "state.pings", "value": value}}],
        "metadata": {"costs_action": False},
    }
    content["inclusions"][0]["links"]["tools"].append(name)
    content["inclusions"][0]["links"]["tools"].sort()


def newer_source(owner, original, *, setting):
    drafts = ruleset_copy(owner, original.candidate_id)
    ruleset = drafts[0]
    record = json.loads(canonical_json_bytes(ruleset.record))
    record["version"] = "0.2.0"
    runtime = yaml.safe_load(ruleset.payloads["runtime/ruleset.yaml"])
    content = json.loads(ruleset.payloads["content/content-declarations.json"])
    add_ping_tool(record, runtime, content, "source_ping", 2)
    schema = record["participant_artifacts"][0]
    schema["schema"]["properties"]["rank"] = {"type": "string"}
    schema.pop("schema_sha256")
    reference = _world_draft(
        package_id="local.signal-manual",
        version="1.0.0",
        title="Signal Manual",
        payloads={"manual.md": b"The updated signal protocol has a new ping operation."},
        ruleset=ruleset,
    )
    reference = replace(
        reference,
        record={
            **reference.record,
            "content_kind": "support",
            "dependencies": [],
        },
    )
    record["dependencies"] = [
        {
            "id": reference.record["id"],
            "version": "1.0.0",
            "namespace": "manual",
            "kind": "reference",
        }
    ]
    updated = replace(
        ruleset,
        record=record,
        payloads={
            **ruleset.payloads,
            "runtime/ruleset.yaml": yaml.safe_dump(runtime, sort_keys=False).encode(),
            "content/content-declarations.json": canonical_json_bytes(content),
        },
    )
    packages = [updated, reference]
    if setting:
        world = next(
            item
            for item in retained_package_drafts(
                owner,
                original.candidate_id,
                include_setting=True,
            )
            if item.record["content_kind"] == "campaign_setting"
        )
        world_record = {
            **world.record,
            "version": "0.2.0",
            "dependencies": [
                {
                    "id": record["id"],
                    "version": "0.2.0",
                    "namespace": "ruleset_snapshot",
                    "kind": "ruleset_snapshot",
                }
            ],
        }
        packages.append(
            replace(
                world,
                record=world_record,
                payloads={
                    **world.payloads,
                    "locations/relay.md": b"The updated relay overlooks a new landing pad.",
                },
            )
        )
    with materialize_package_archive(packages) as materialized:
        candidate = owner.install_package_archive(materialized.archive, install_id="new-source")
    return candidate, record, runtime, content


@pytest.mark.parametrize("source_kind", ["ruleset", "setting"])
def test_plan_adopts_complete_source_and_keeps_explicit_custom_declarations(tmp_path, source_kind):
    database = tmp_path / "updates.db"
    with AuthorityOwner.open(database, "seed") as owner:
        original, setting_id = install_character_packages(owner)
        original_declaration = owner.package_participant_declarations(original.candidate_id)[0]
        original_schema_sha256 = original_declaration.schema_sha256
        authority = campaign_plan_sources(owner, original.package_lock)
        payload = json.loads(_campaign_plan_example(authority))
        updated, record, runtime, content = newer_source(
            owner,
            original,
            setting=source_kind == "setting",
        )
        original_record = owner.package_record(
            authority.ruleset.package_id,
            authority.ruleset.version,
            authority.ruleset.content_sha256,
        )
        reader = LibrarySourceReader(owner, authority.ruleset.package_id, setting_id)
        editable = json.loads(reader.record(updated.candidate_id, "ruleset")["content"])
        assert editable["content_kind"] == "ruleset" and "files" not in editable
        assert editable["participant_artifacts"][0]["schema"]["properties"]["rank"] == {
            "type": "string",
        }
    record["version"] = "0.2.0-house"
    add_ping_tool(record, runtime, content, "house_ping", 7)
    schema = record["participant_artifacts"][0]
    schema["schema"]["properties"]["badge"] = {"type": "string"}
    payload.update(
        id="house-relay", title="House Relay", premise="Follow the customized relay route."
    )
    world_text = "# Relay\n\nOur local house campaign keeps the old lighthouse.\n"
    files = {
        "plan.json": json.dumps(payload),
        "plan.md": "# House Relay\n\nFollow the relay.",
        f"{source_kind}-source.json": json.dumps(
            {
                "candidate_id": updated.candidate_id,
                "expected_closure_sha256": updated.closure_sha256,
            }
        ),
        f"package-records/{record['id']}.json": json.dumps(record),
        "ruleset-files/runtime/ruleset.yaml": yaml.safe_dump(runtime, sort_keys=False),
        "ruleset-files/content/content-declarations.json": canonical_json_bytes(content).decode(),
        "setting-files/locations/relay.md": world_text,
    }
    provider = LibraryProvider(
        responses=[
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="record",
                        name="inspect_library_sources",
                        arguments=json.dumps(
                            {
                                "action": "record",
                                "candidate_id": updated.candidate_id,
                                "path": "ruleset",
                                "limit": 32000,
                            }
                        ),
                    ),
                    *(
                        write_call(str(index), f"campaign-plan-draft/{path}", value)
                        for index, (path, value) in enumerate(files.items())
                    ),
                ]
            ),
            ProviderResponse(
                tool_calls=[
                    ToolCall(id="publish", name="publish_campaign_plan", arguments="{}"),
                ]
            ),
            ProviderResponse(content="The requested update and custom rules are saved together."),
        ]
    )
    harness = _OwnerHarness(database, provider)
    try:
        _, session_id = launch_library_builder(harness, original.candidate_id, setting_id, "plan")
        harness.request(
            "update",
            "submit_session_input",
            {
                "session_id": session_id,
                "attempt_id": "update",
                "message_id": "update",
                "user_text": "",
                "builder_start": True,
            },
        )
        results = {
            item["tool_call_id"]: json.loads(item["content"])
            for item in provider.calls[-1]["messages"]
            if item.get("role") == "tool"
        }
        assert results["record"]["success"] is True
        assert json.loads(results["record"]["content"])["version"] == "0.2.0"
        saved_result = results["publish"]
        assert saved_result["success"] is True, json.dumps(saved_result)
    finally:
        harness.close()
    with AuthorityOwner.open(database, "reopen") as owner:
        saved = load_reusable_campaign_plan(tmp_path, saved_result["library_artifact"]["id"])
        assert saved.plan.payload["bindings"]["ruleset"]["version"] == "0.2.0-house"
        effective = next(
            item
            for item in owner.package_candidates()
            if (item.closure_sha256 == saved_result["package_closure_sha256"])
        )
        assert "local.signal-manual" in {item.package_id for item in effective.package_lock.entries}
        sources = campaign_plan_sources(owner, effective.package_lock)
        assert sources.read_setting("locations/relay.md") == world_text
        declared = owner.package_participant_declarations(effective.candidate_id)[0]
        assert {"rank", "badge"} <= set(declared.schema["properties"])
        assert declared.schema_sha256 != original_schema_sha256
        assert (
            owner.package_record(
                authority.ruleset.package_id,
                authority.ruleset.version,
                authority.ruleset.content_sha256,
            )
            == original_record
        )
        assert owner.package_candidate(updated.candidate_id) == updated
        owner.create_campaign(
            CampaignCreate(
                campaign_id="house",
                display_name="House Relay",
                writer=WriterRole.DM,
                package_lock=effective.package_lock,
                turn_id="turn",
                initial_state=(StateValue(record["id"], "state", {"pings": 0}),),
            )
        )
        assert {"source_ping", "house_ping"} <= {tool.name for tool in owner.catalog("house")}
        for revision, (tool_name, expected) in enumerate((("source_ping", 2), ("house_ping", 7))):
            owner.execute_action(
                ActionCommand(
                    campaign_id="house",
                    action_id=tool_name,
                    idempotency_key=tool_name,
                    owner_id="reopen",
                    writer=WriterRole.DM,
                    expected_revision=revision,
                    turn_id="turn",
                    package_id=record["id"],
                    tool_name=tool_name,
                    parameters={},
                )
            )
            assert owner.snapshot("house").state[f"{record['id']}.state"]["pings"] == expected
        archive = io.BytesIO()
        export_plan_bundle(
            tmp_path,
            saved.artifact_id,
            expected_content_sha256=saved.content_sha256,
            target=archive,
        )
    destination = tmp_path / "fresh"
    destination.mkdir()
    with AuthorityOwner.open(destination / "fresh.db", "imported") as owner:
        imported = import_plan_bundle(
            destination,
            io.BytesIO(archive.getvalue()),
            install_snapshot=lambda data, _closure: owner.install_package_archive(
                io.BytesIO(data),
                install_id="imported-update",
            ),
        )
        assert imported.plan == saved.plan
        assert owner.package_candidates()[0].package_lock == effective.package_lock
