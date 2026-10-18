"""Builder saves and exports customized inherited files without changing their sources."""

import io
import json

import pytest

from sylvia_gm.agent._builder_session import _campaign_plan_example
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.campaign_plan_sources import campaign_plan_sources
from sylvia_gm.application.plan_bundle import export_plan_bundle, import_plan_bundle
from sylvia_gm.application.reusable_plans import load_reusable_campaign_plan
from sylvia_gm.domain.package_library import campaign_setting_entries, canonical_json_bytes
from sylvia_gm.provider._base import ProviderResponse, ToolCall
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_builder_library import (
    LibraryProvider,
    install_character_packages,
    launch_library_builder,
)


def write_call(identity, path, content):
    return ToolCall(
        id=identity,
        name="write_file",
        arguments=json.dumps({"path": path, "content": content}),
    )


@pytest.mark.parametrize(
    ("mistake", "path", "field_details"),
    [
        ("companion", "$.starting_party.required_companions[0]", ("missing: id",)),
        ("bindings", "$.bindings", ("ruleset", "campaign_setting", "package_closure_sha256")),
        ("top", "$", ("missing: structure", "party_placements", "structures", "title_short")),
        ("entity", "$.initial_state.entities[0]", ("missing: source_ref",)),
        (
            "entity-source",
            "$.initial_state.entities[0].source_ref",
            ("source_ref", "must be an object", "id", "path"),
        ),
        ("entity-source-fields", "$.initial_state.entities[0].source_ref", ("missing: id",)),
    ],
)
def test_plan_publication_reports_fields_to_the_builder(tmp_path, mistake, path, field_details):
    """A rejected draft identifies corrections in the actual next provider request."""
    database = tmp_path / "plan.db"
    with AuthorityOwner.open(database, "source") as owner:
        source, setting_id = install_character_packages(owner)
        authority = campaign_plan_sources(owner, source.package_lock, setting_id=setting_id)
        payload = json.loads(_campaign_plan_example(authority))
    if mistake == "companion":
        payload["starting_party"]["required_companions"] = [{"path": "characters/operator.md"}]
    elif mistake == "bindings":
        del payload["bindings"]
    elif mistake.startswith("entity"):
        entity = {
            "id": "operator",
            "kind": "npc",
            "source_ref": {"id": "operator", "path": "characters/operator.md"},
            "state": {},
        }
        payload["initial_state"]["entities"] = [entity]
        if mistake == "entity":
            del entity["source_ref"]
        elif mistake == "entity-source":
            entity["source_ref"] = "PRIVATE_VALUE_MUST_NOT_APPEAR"
        else:
            del entity["source_ref"]["id"]
    else:
        payload["structures"] = payload.pop("structure")
        payload["party_placements"] = []
        payload["title_short"] = "PRIVATE_VALUE_MUST_NOT_APPEAR"
    provider = LibraryProvider(
        responses=[
            ProviderResponse(
                tool_calls=[
                    write_call("json", "campaign-plan-draft/plan.json", json.dumps(payload)),
                    write_call(
                        "markdown", "campaign-plan-draft/plan.md", "# Relay\n\nReach the relay."
                    ),
                ]
            ),
            ProviderResponse(
                tool_calls=[
                    ToolCall(id="publish", name="publish_campaign_plan", arguments="{}"),
                ]
            ),
            ProviderResponse(content="The rejection identifies the draft fields to correct."),
        ]
    )
    harness = _OwnerHarness(database, provider)
    try:
        _, session_id = launch_library_builder(harness, source.candidate_id, setting_id, "plan")
        result = harness.request(
            "publish-turn",
            "submit_session_input",
            {
                "session_id": session_id,
                "attempt_id": "publish-attempt",
                "message_id": "publish",
                "user_text": "",
                "builder_start": True,
            },
        )
        assert result["interruption"] is None
        rejected = json.loads(
            next(
                item["content"]
                for item in provider.calls[-1]["messages"]
                if item.get("tool_call_id") == "publish"
            )
        )
        assert rejected["success"] is False
        assert rejected["code"] == "campaign_plan_invalid"
        assert rejected["validation_errors"], rejected
        diagnostic = rejected["validation_errors"][0]
        assert diagnostic["file"] == "campaign-plan-draft/plan.json"
        assert diagnostic["path"] == path
        for detail in field_details:
            assert detail in diagnostic["message"]
        assert diagnostic["guidance"]
        assert "PRIVATE_VALUE_MUST_NOT_APPEAR" not in json.dumps(rejected)
        assert not tuple((tmp_path / "campaign-plans").glob("*"))
    finally:
        harness.close()


@pytest.mark.parametrize("mistake", ["unlinked", "unknown", "noncanonical"])
def test_plan_added_content_rejection_identifies_the_declaration_and_link(tmp_path, mistake):
    """Real archive rejection tells Builder which owned file to repair without losing its draft."""
    database = tmp_path / "plan.db"
    with AuthorityOwner.open(database, "source") as owner:
        source, setting_id = install_character_packages(owner)
        authority = campaign_plan_sources(owner, source.package_lock, setting_id=setting_id)
        payload = json.loads(_campaign_plan_example(authority))
        declaration = json.loads(authority.read_setting("content/content-declarations.json"))
    added_path = "locations/roof.md"
    if mistake != "unlinked":
        declaration["inclusions"][0]["links"]["artifacts"].append(added_path)
    if mistake == "unknown":
        declaration["inclusions"][0]["links"]["artifacts"].append("locations/absent.md")
        declaration["inclusions"][0]["links"]["artifacts"].sort()
    declaration_text = canonical_json_bytes(declaration).decode()
    if mistake == "noncanonical":
        declaration_text += "\n"
    files = {
        "plan.json": json.dumps(payload),
        "plan.md": "# Relay\n\nReach the relay.",
        f"setting-files/{added_path}": "# Roof\n\nThe roof overlooks the relay.",
        "setting-files/content/content-declarations.json": declaration_text,
    }
    provider = LibraryProvider(
        responses=[
            ProviderResponse(
                tool_calls=[
                    write_call(f"write-{index}", f"campaign-plan-draft/{path}", content)
                    for index, (path, content) in enumerate(files.items())
                ]
            ),
            ProviderResponse(
                tool_calls=[
                    ToolCall(id="publish", name="publish_campaign_plan", arguments="{}"),
                ]
            ),
            ProviderResponse(content="The publication rejection identifies the content to repair."),
        ]
    )
    harness = _OwnerHarness(database, provider)
    try:
        _, session_id = launch_library_builder(harness, source.candidate_id, setting_id, "plan")
        result = harness.request(
            "publish-turn",
            "submit_session_input",
            {
                "session_id": session_id,
                "attempt_id": "publish-attempt",
                "message_id": "publish",
                "user_text": "",
                "builder_start": True,
            },
        )
        assert result["interruption"] is None
        rejected = json.loads(
            next(
                item["content"]
                for item in provider.calls[-1]["messages"]
                if item.get("tool_call_id") == "publish"
            )
        )
        assert rejected["success"] is False
        diagnostic = rejected["validation_errors"][0]
        assert diagnostic["file"] == (
            "campaign-plan-draft/setting-files/content/content-declarations.json"
        )
        assert diagnostic["package_id"] == authority.setting.package_id
        assert diagnostic["guidance"]
        if mistake == "noncanonical":
            assert "content declarations" in diagnostic["message"]
            assert "canonical" in diagnostic["message"]
            assert "manifest" not in diagnostic["message"]
        else:
            assert (added_path if mistake == "unlinked" else "locations/absent.md") in (
                diagnostic["message"]
            )
        assert not tuple((tmp_path / "campaign-plans").glob("*"))
        draft = tmp_path / "builder-chats" / session_id / "campaign-plan-draft"
        assert (draft / "setting-files" / added_path).exists()
    finally:
        harness.close()


@pytest.mark.parametrize("invalid_rules", [False, True])
def test_plan_builder_publishes_owned_world_and_rules_and_rejects_broken_runtime(
    tmp_path,
    invalid_rules,
):
    database = tmp_path / "plan.db"
    with AuthorityOwner.open(database, "source") as owner:
        source, setting_id = install_character_packages(owner)
        authority = campaign_plan_sources(owner, source.package_lock, setting_id=setting_id)
        payload = json.loads(_campaign_plan_example(authority))
        original_world = authority.read_setting("locations/relay.md")
        rules = authority.ruleset
        original_rules = owner.package_text_file(
            rules.package_id,
            rules.version,
            rules.content_sha256,
            "runtime/ruleset.yaml",
        )
        declaration = json.loads(authority.read_setting("content/content-declarations.json"))
    payload.update(id="relay-rooftop", title="Relay Rooftop", premise="Reach the rooftop antenna.")
    payload["starting_location"] = {"id": "rooftop", "path": "locations/roof.md"}
    payload["structure"][0]["setting_refs"] = ["locations/roof.md"]
    declaration["inclusions"][0]["links"]["artifacts"].append("locations/roof.md")
    declaration["inclusions"][0]["links"]["artifacts"].remove("characters/operator.md")
    changed_world = "# Relay\n\nThe lower relay is flooded.\n"
    new_world = "# Rooftop\n\nAn antenna overlooks the flooded relay.\n"
    changed_rules = (
        original_rules.replace("- dice:", "- deliberately_unknown_effect:", 1)
        if invalid_rules
        else original_rules.replace(
            "expression: 2d6",
            "expression: 4d6",
        )
    )
    assert changed_rules != original_rules
    files = {
        "plan.json": json.dumps(payload),
        "plan.md": "# Relay Rooftop\n\nReach the rooftop antenna.",
        "setting-files/locations/relay.md": changed_world,
        "setting-files/locations/roof.md": new_world,
        "setting-files/content/content-declarations.json": canonical_json_bytes(
            declaration
        ).decode(),
        "ruleset-files/runtime/ruleset.yaml": changed_rules,
        "removed-files.json": json.dumps(["setting/characters/operator.md"]),
    }
    provider = LibraryProvider(
        responses=[
            ProviderResponse(
                tool_calls=[
                    write_call(f"write-{index}", f"campaign-plan-draft/{path}", content)
                    for index, (path, content) in enumerate(files.items())
                ]
            ),
            ProviderResponse(
                tool_calls=[
                    ToolCall(id="publish", name="publish_campaign_plan", arguments="{}"),
                ]
            ),
            ProviderResponse(
                tool_calls=[
                    ToolCall(id="repeat", name="publish_campaign_plan", arguments="{}"),
                ]
            ),
            ProviderResponse(content="The Plan publication result has been checked."),
        ]
    )
    harness = _OwnerHarness(database, provider)
    try:
        _, session_id = launch_library_builder(harness, source.candidate_id, setting_id, "plan")
        result = harness.request(
            "publish-turn",
            "submit_session_input",
            {
                "session_id": session_id,
                "attempt_id": "publish-attempt",
                "message_id": "publish",
                "user_text": "",
                "builder_start": True,
            },
        )
        assert result["interruption"] is None
        messages = provider.calls[-1]["messages"]
        results = {
            message["tool_call_id"]: json.loads(message["content"])
            for message in messages
            if message.get("role") == "tool"
        }
        for index in range(len(files)):
            assert results[f"write-{index}"]["success"] is True
        published = results["publish"]
        assert results["repeat"] == published
        if invalid_rules:
            assert published["success"] is False
            assert published["code"] == "campaign_plan_invalid"
            assert published["validation_errors"][0]["code"] == "package_runtime_invalid"
            assert "library_artifact" not in published
        else:
            assert published["success"] is True, json.dumps(published)
            assert published["package_closure_sha256"] != source.closure_sha256
            assert published["ruleset"]["content_sha256"] != rules.content_sha256
            assert (
                published["campaign_setting"]["content_sha256"] != authority.setting.content_sha256
            )
    finally:
        harness.close()

    with AuthorityOwner.open(database, "reopen") as owner:
        assert owner.package_candidate(source.candidate_id) == source
        assert (
            campaign_plan_sources(owner, source.package_lock).read_setting("locations/relay.md")
            == original_world
        )
        assert (
            owner.package_text_file(
                rules.package_id,
                rules.version,
                rules.content_sha256,
                "runtime/ruleset.yaml",
            )
            == original_rules
        )
        plan_dirs = tuple((tmp_path / "campaign-plans").glob("*"))
        if invalid_rules:
            assert plan_dirs == ()
            assert not any(
                item.candidate_id.startswith("candidate-builder-plan-snapshot-")
                for item in owner.package_candidates()
            )
            return
        saved = load_reusable_campaign_plan(tmp_path, published["library_artifact"]["id"])
        assert len(plan_dirs) == 1
        assert saved.package_snapshot is not None
        assert saved.plan.payload["starting_location"]["path"] == "locations/roof.md"
        snapshot = next(
            item
            for item in owner.package_candidates()
            if (item.closure_sha256 == published["package_closure_sha256"])
        )
        sources = campaign_plan_sources(owner, snapshot.package_lock)
        assert sources.read_setting("locations/relay.md") == changed_world
        assert sources.read_setting("locations/roof.md") == new_world
        assert "characters/operator.md" not in sources.setting_paths
        assert (
            owner.package_text_file(
                sources.ruleset.package_id,
                sources.ruleset.version,
                sources.ruleset.content_sha256,
                "runtime/ruleset.yaml",
            )
            == changed_rules
        )
        archive = io.BytesIO()
        export_plan_bundle(
            tmp_path,
            saved.artifact_id,
            expected_content_sha256=saved.content_sha256,
            target=archive,
        )

    destination = tmp_path / "imported"
    destination.mkdir()
    with AuthorityOwner.open(destination / "plan.db", "imported") as owner:
        imported = import_plan_bundle(
            destination,
            io.BytesIO(archive.getvalue()),
            install_snapshot=lambda data, _closure: owner.install_package_archive(
                io.BytesIO(data),
                install_id="imported-copy",
            ),
        )
        assert imported.plan == saved.plan
        assert imported.package_snapshot == saved.package_snapshot
        inherited = owner.package_candidates()[0]
        assert inherited.package_lock == snapshot.package_lock
        assert len(campaign_setting_entries(inherited.package_lock)) == 1
        assert (
            campaign_plan_sources(owner, inherited.package_lock).read_setting("locations/roof.md")
            == new_world
        )
