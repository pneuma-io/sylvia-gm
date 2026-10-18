"""Existing content subflows become saved chats while retaining their original context and files."""

import json
import sqlite3

import pytest

from sylvia_gm.agent._builder_session import _builder_prompt, _provider_history
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.builder_references import reference_reader
from sylvia_gm.application.builder_session_migration import migrate_legacy_builder_chats
from sylvia_gm.domain.authority import PackageLock
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.session import SessionMessageKind
from sylvia_gm.infrastructure import builder_workspace_migration
from sylvia_gm.interfaces.owner_process import _campaign_creation_catalog
from sylvia_gm.provider._base import ProviderResponse, ToolCall
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_builder_library import LibraryProvider, install_character_packages
from tests.test_builder_references import open_character_interview


def test_migration_discovery_on_fresh_database_does_not_create_session_tables(tmp_path):
    database = tmp_path / "fresh.db"
    with AuthorityOwner.open(database, "fresh-owner") as owner:
        with sqlite3.connect(database) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
        assert owner.legacy_builder_contexts() == ()
        with sqlite3.connect(database) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == version
            assert connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'agent_sessions'"
            ).fetchone() is None


def seed_legacy_character(owner, root, *, returned=False, logical_ruleset=False):
    candidate, setting_id = install_character_packages(owner)
    ruleset_id = "signal" if logical_ruleset else candidate.candidate_id
    draft = open_character_interview(owner, ruleset_id, setting_id, None)
    child = owner.start_guided_creation_subflow(
        operation_id="old-character-launch",
        campaign_id=draft.campaign_id,
        parent_session_id=draft.builder_session_id,
        expected_construction_revision=4,
        purpose="player_character",
        visible_answer="New Character",
        child_session_id="old-character",
    )
    owner.append_agent_message(
        session_id=child.child_session_id,
        message_id="old-player-description",
        kind=SessionMessageKind.USER,
        content={"text": "Make a relay courier."},
    )
    owner.append_agent_message(
        session_id=child.child_session_id,
        message_id="old-builder-reply",
        kind=SessionMessageKind.TALK,
        content={"text": "I saved a first draft."},
    )
    if returned:
        owner.return_guided_creation_subflow(
            operation_id="return-old-child",
            campaign_id=draft.campaign_id,
            parent_session_id=draft.builder_session_id,
            child_session_id=child.child_session_id,
            expected_construction_revision=5,
            use_result=False,
        )
        owner.advance_guided_campaign_draft(
            operation_id="change-old-ruleset",
            campaign_id=draft.campaign_id,
            builder_session_id=draft.builder_session_id,
            expected_construction_revision=6,
            current_step="ruleset",
            visible_answer="No Ruleset",
            answer={"candidate_id": None},
        )
    workspace = root / "campaigns" / draft.campaign_id
    (workspace / "drafts").mkdir(parents=True)
    (workspace / "drafts/character.md").write_bytes(b"# Vela\r\n\r\nA relay courier.")
    return draft, child, candidate, workspace


@pytest.mark.parametrize("returned", [False, True])
def test_old_chat_migration_retains_history_sources_and_independent_files(tmp_path, returned):
    database = tmp_path / "legacy.db"
    with AuthorityOwner.open(database, "seed-legacy") as owner:
        draft, child, candidate, original_workspace = seed_legacy_character(
            owner,
            tmp_path,
            returned=returned,
        )
        before_parent = owner.session_context(draft.builder_session_id)
        with sqlite3.connect(database) as connection:
            original_rows = connection.execute(
                "SELECT sequence, message_id, kind, content_json FROM agent_session_messages "
                "WHERE session_id = ? ORDER BY sequence",
                (child.child_session_id,),
            ).fetchall()
        migrated = migrate_legacy_builder_chats(
            owner, tmp_path, _campaign_creation_catalog(tmp_path, owner)
        )
        assert migrated == ("old-character",)
        assert (
            migrate_legacy_builder_chats(
                owner, tmp_path, _campaign_creation_catalog(tmp_path, owner)
            )
            == ()
        )
        context = owner.session_context(child.child_session_id)
        assert context.session.campaign_id is None
        assert context.session.parent_session_id is None
        assert context.session.provider_id == "test"
        assert context.messages[-1].content["references"]["candidate_id"] == candidate.candidate_id
        assert (
            context.messages[-1].content["selections"]["ruleset"]["value"]["candidate_id"]
            == candidate.candidate_id
        )
        assert "complete_creation_subflow" not in _builder_prompt(context)
        assert all(
            "creation_subflow_link" not in item["content"] for item in _provider_history(context)
        )
        assert reference_reader(owner, context).read("setting/characters/operator.md")
        with sqlite3.connect(database) as connection:
            rows = connection.execute(
                "SELECT sequence, message_id, kind, content_json "
                "FROM standalone_builder_chat_messages "
                "WHERE session_id = ? AND sequence <= ? ORDER BY sequence",
                (child.child_session_id, len(original_rows)),
            ).fetchall()
        assert rows == original_rows
        after_parent = owner.session_context(draft.builder_session_id)
        old_state = next(
            message.content
            for message in reversed(before_parent.messages)
            if message.content.get("kind") == "campaign_interview_state"
        )
        state = next(
            message.content
            for message in reversed(after_parent.messages)
            if message.content.get("kind") == "campaign_interview_state"
        )
        assert state["answers"] == old_state["answers"]
        assert state["active_subflow"] is None
        assert state["current_step"] == old_state["current_step"]
        independent = tmp_path / "builder-chats" / child.child_session_id / "drafts/character.md"
        assert independent.read_bytes() == (original_workspace / "drafts/character.md").read_bytes()
        independent.write_bytes(b"# Continued independent draft")
        assert b"relay courier" in (original_workspace / "drafts/character.md").read_bytes()

        # Bind only to exercise the existing permanent-deletion boundary. The independent
        # chat and its retained context must not consult the originating Interview afterward.
        for revision, (step, answer) in enumerate(
            (
                ("ruleset", {"candidate_id": None}),
                ("setting", {"setting_id": None}),
                ("plan", {"plan_id": None}),
                ("name", {"display_name": "Legacy origin"}),
                ("participants", {"player_name": "Investigator"}),
                ("boundaries_preferences", {"boundaries": "", "preferences": ""}),
                ("specific_requests", {"no_additional_requests": True}),
            ),
            start=state["construction_revision"],
        ):
            owner.advance_guided_campaign_draft(
                operation_id=f"finish-{step}",
                campaign_id=draft.campaign_id,
                builder_session_id=draft.builder_session_id,
                expected_construction_revision=revision,
                current_step=step,
                visible_answer=step,
                answer=answer,
            )
        owner.append_agent_message(
            session_id=draft.builder_session_id,
            message_id="ready-for-delete-test",
            kind=SessionMessageKind.USER,
            content={
                "text": "Ready to Begin",
                "interview_step": "ready",
                "interview_value": {"action": "ready_to_begin"},
            },
        )
        owner.bind_guided_campaign_basis(
            operation_id="bind-before-delete",
            campaign_id=draft.campaign_id,
            display_name="Legacy origin",
            package_lock=PackageLock(entries=()),
            turn_id="legacy-construction",
            builder_session_id=draft.builder_session_id,
        )
        owner.trash_campaign(
            operation_id="trash-origin",
            campaign_id=draft.campaign_id,
            expected_revision=owner.snapshot(draft.campaign_id).revision,
            designated_session_id=draft.builder_session_id,
        )
        owner.purge_campaign(
            operation_id="purge-origin",
            campaign_id=draft.campaign_id,
            expected_revision=owner.snapshot(draft.campaign_id).revision,
            confirmation=f"permanently purge {draft.campaign_id}",
        )
        assert owner.session_context(child.child_session_id) == context
    with AuthorityOwner.open(database, "reopen-legacy") as owner:
        assert owner.session_context(child.child_session_id) == context
        assert independent.read_bytes() == b"# Continued independent draft"
        assert reference_reader(owner, context).read("setting/characters/operator.md")


def test_old_name_only_reference_is_retained_without_claiming_an_exact_version(tmp_path):
    with AuthorityOwner.open(tmp_path / "name-only.db", "legacy-owner") as owner:
        _draft, child, _candidate, _workspace = seed_legacy_character(
            owner, tmp_path, logical_ruleset=True
        )
        migrate_legacy_builder_chats(owner, tmp_path, _campaign_creation_catalog(tmp_path, owner))
        context = owner.session_context(child.child_session_id)
        launch = context.messages[-1].content
        assert context.session.campaign_id is None
        assert launch["selections"]["ruleset"]["value"]["candidate_id"] == "signal"
        assert launch["references"] == {"candidate_id": None, "packages": [], "plan": None}
        assert "no recoverable exact source versions" in launch["reference_error"]
        assert "no recoverable exact source versions" in json.dumps(_provider_history(context))
        assert (tmp_path / "builder-chats/old-character/drafts/character.md").is_file()


def test_interrupted_workspace_copy_is_retried_before_detachment(tmp_path, monkeypatch):
    with AuthorityOwner.open(tmp_path / "copy.db", "copy-owner") as owner:
        draft, child, _candidate, source = seed_legacy_character(owner, tmp_path)
        original_copytree = builder_workspace_migration.shutil.copytree
        with monkeypatch.context() as patch:
            patch.setattr(
                builder_workspace_migration.shutil,
                "copytree",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("synthetic copy failure")),
            )
            with pytest.raises(OSError, match="synthetic copy failure"):
                migrate_legacy_builder_chats(
                    owner, tmp_path, _campaign_creation_catalog(tmp_path, owner)
                )
        assert (
            owner.session_context(child.child_session_id).session.campaign_id == draft.campaign_id
        )
        assert not (tmp_path / "builder-chats" / child.child_session_id).exists()
        assert (source / "drafts/character.md").exists()
        assert builder_workspace_migration.shutil.copytree is original_copytree
        # A crash after the complete copy but before the SQLite commit must reuse the copy.
        retained = builder_workspace_migration.retain_legacy_workspace(
            tmp_path,
            campaign_id=draft.campaign_id,
            session_id=child.child_session_id,
        )
        (source / "drafts/character.md").write_bytes(b"# Later origin change")
        migrate_legacy_builder_chats(owner, tmp_path, _campaign_creation_catalog(tmp_path, owner))
        assert b"relay courier" in (retained / "drafts/character.md").read_bytes()


def test_owner_startup_migrates_and_continues_using_saved_workspace(tmp_path):
    database = tmp_path / "startup.db"
    with AuthorityOwner.open(database, "startup-seed") as owner:
        draft, child, _candidate, _source = seed_legacy_character(owner, tmp_path)
    provider = LibraryProvider(
        responses=[
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="read-old-draft",
                        name="read_file",
                        arguments=json.dumps({"path": "drafts/character.md"}),
                    )
                ]
            ),
            ProviderResponse(content="The saved courier draft is available."),
        ]
    )
    harness = _OwnerHarness(database, provider)
    try:
        context = harness.request(
            "migrated", "session_context", {"session_id": child.child_session_id}
        )
        assert context["session"]["campaign_id"] is None
        result = harness.request(
            "continue",
            "submit_session_input",
            {
                "session_id": child.child_session_id,
                "attempt_id": "continue-legacy",
                "message_id": "continue-legacy",
                "user_text": "Read the draft we saved earlier.",
            },
        )
        assert result["interruption"] is None
        assert "complete_creation_subflow" not in json.dumps(provider.calls[0]["tools"])
        assert "relay courier" in json.dumps(provider.calls[-1]["messages"])
    finally:
        harness.close()


def test_workspace_migration_refuses_existing_unrelated_target_and_path_escape(tmp_path):
    target = tmp_path / "builder-chats/chat"
    target.mkdir(parents=True)
    (target / "my-file.md").write_text("Keep this file.")
    with pytest.raises(CommandRejected, match="existing independent workspace"):
        builder_workspace_migration.retain_legacy_workspace(
            tmp_path, campaign_id="campaign", session_id="chat"
        )
    assert (target / "my-file.md").read_text() == "Keep this file."
    with pytest.raises(CommandRejected, match="directory name"):
        builder_workspace_migration.retain_legacy_workspace(
            tmp_path, campaign_id="../elsewhere", session_id="chat"
        )


def test_campaign_customization_keeps_its_existing_link_and_completion_prompt(tmp_path):
    with AuthorityOwner.open(tmp_path / "customization.db", "customization-owner") as owner:
        draft = open_character_interview(owner, None, None, None)
        for revision, (step, answer) in enumerate(
            (
                ("participants", {"player_name": "Investigator"}),
                ("boundaries_preferences", {"boundaries": "", "preferences": ""}),
            ),
            start=4,
        ):
            owner.advance_guided_campaign_draft(
                operation_id=f"choose-{step}",
                campaign_id=draft.campaign_id,
                builder_session_id=draft.builder_session_id,
                expected_construction_revision=revision,
                current_step=step,
                visible_answer=step,
                answer=answer,
            )
        child = owner.start_guided_creation_subflow(
            operation_id="customize",
            campaign_id=draft.campaign_id,
            parent_session_id=draft.builder_session_id,
            expected_construction_revision=6,
            purpose="campaign_customization",
            visible_answer="Move the starting town",
            child_session_id="customize-chat",
        )
        context = owner.session_context(child.child_session_id)
        assert migrate_legacy_builder_chats(owner, tmp_path, ()) == ()
        assert owner.session_context(child.child_session_id) == context
        assert "complete_creation_subflow" in _builder_prompt(context)
        assert owner.builder_chat_index() == ()
