"""Real saved-source transaction proofs, plus a controlled-provider owner transport journey."""

import copy
import hashlib
import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.campaign_plan_sources import campaign_plan_sources
from sylvia_gm.application.reusable_plans import (
    reusable_campaign_plan_catalog,
    save_reusable_campaign_plan,
)
from sylvia_gm.domain._content_schema_plan import EXAMPLE
from sylvia_gm.domain.authority import (
    Participant,
    ParticipantValidationStatus,
    ProseChannel,
    StateValue,
    WriterRole,
)
from sylvia_gm.domain.campaign_ending import required_story_items
from sylvia_gm.domain.campaign_sequel import digest, prepare_story
from sylvia_gm.domain.errors import CommandRejected, IdempotencyConflict, StaleRevision
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.domain.session import SessionMessageKind
from tests.test_builder_library import install_character_packages

REVIEW = {
    "connection": "The relay crew investigates the second signal after the saved future.",
    "timeline": "after",
    "reviewed": True,
}


def _finish(owner, campaign, session, plan, tag, *, resolve_story=True):
    """Earn an ending through actual continuity and narration commits."""
    turn = owner.current_open_turn(campaign)
    if turn is None:
        turn = f"{tag}-turn"
        owner.open_turn(campaign, turn)
    user_id = f"{tag}-input"
    owner.append_agent_message(
        session_id=session,
        message_id=user_id,
        kind=SessionMessageKind.USER,
        content={"text": "Return the report."},
    )
    ending = "ending:" + plan["ending"]["outcomes"][0]["id"]
    owner.update_campaign_file(
        session_id=session,
        update_id=f"{tag}-progress",
        updates=tuple(
            {
                "operation": "update_plan_progress",
                "plan_item_id": identity,
                "progress_status": "resolved",
                "progress_summary": "The report was delivered.",
            }
            for identity in [*(sorted(required_story_items(plan)) if resolve_story else []), ending]
        ),
        basis_message_ids=(user_id,),
        turn_id=turn,
    )
    owner.start_narration_attempt(
        campaign_id=campaign, attempt_id=f"{tag}-reply", turn_id=turn, cancel=lambda: None
    )
    owner.emit_narration_token(
        campaign,
        f"{tag}-reply",
        ProseChannel.TALK,
        "The report is delivered. The crew retains its promise to the operator.",
    )
    events = owner.complete_narration_attempt(campaign, f"{tag}-reply")
    owner.append_session_canonical_references(session, events)


@pytest.fixture
def sequel(tmp_path, monkeypatch, request):
    database = tmp_path / "campaigns.sqlite3"
    with AuthorityOwner.open(database, "sequel-test", disconnect_grace_seconds=1.0) as owner:
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="init")
        candidate, _setting_id = install_character_packages(owner)
        with io.BytesIO() as exported:
            owner.export_package_candidate(candidate.candidate_id, exported)
            archive = exported.getvalue()
        campaign, session, builder, turn = (
            "campaign-sequel",
            "session-sequel",
            "builder-sequel",
            "setup",
        )
        primary = candidate.package_lock.entries[0]
        declaration = owner.exact_participant_declarations(
            primary.package_id,
            primary.version,
            primary.sha256,
        )[0]
        owner.create_campaign(
            CampaignCreate(
                campaign_id=campaign,
                display_name="Relay crew",
                writer=WriterRole.BUILDER,
                package_lock=candidate.package_lock,
                turn_id=turn,
                participants=(
                    Participant(
                        "iona",
                        "operator",
                        primary.package_id,
                        primary.version,
                        {"name": "Iona", "callsign": "Relay"},
                        package_sha256=primary.sha256,
                        schema_id=declaration.schema_id,
                        schema_sha256=declaration.schema_sha256,
                        validation_status=ParticipantValidationStatus.VALIDATED,
                    ),
                ),
                initial_state=(
                    StateValue(primary.package_id, "state", {"stress": 3, "charge": 2}),
                ),
            )
        )
        authority = campaign_plan_sources(owner, candidate.package_lock)
        original = copy.deepcopy(EXAMPLE)
        original["secrets"].append(
            {
                "id": "keeper-promise",
                "fact": "The keeper is still owed a recorder.",
                "known_by": [],
                "reveal": {"condition": "Iona explains her promise.", "from_act": "act-1"},
            }
        )
        original["objectives"].append(
            {
                "id": "return-recorder",
                "title": "An unresolved promise",
                "description": "Iona still owes the keeper a recorder.",
                "completion_conditions": ["The keeper receives the recorder."],
            }
        )
        if getattr(request, "param", None) == "early":
            original["ending"]["outcomes"][0]["requires_all_acts"] = False
            later = copy.deepcopy(original["structure"][0])
            later["id"] = "act-2"
            original["structure"].append(later)
        if getattr(request, "param", None) == "afterstory":
            original["ending"]["outcomes"][0]["after_story"] = (
                "Iona spends five years keeping the relay operational before the crew reunites."
            )
        original["bindings"] = {
            "ruleset": authority.ruleset.as_payload(),
            "campaign_setting": authority.setting.as_payload(),
            "package_closure_sha256": authority.closure_sha256,
        }
        authority.validate(original, "# Signal Relay")
        source_payload = copy.deepcopy(original)
        source_payload.update(id="second-signal", title="Second Signal")
        source_payload["secrets"] = []
        source_payload["objectives"] = source_payload["objectives"][:1]
        source_payload["ending"]["outcomes"][0]["requires_all_acts"] = True
        saved = save_reusable_campaign_plan(
            tmp_path,
            operation_id="save-second",
            source_payload=source_payload,
            source_markdown="# Second Signal\nThe crew returns for another investigation.",
            validate_plan=authority.validate,
            package_snapshot=archive,
        )
        source = SimpleNamespace(
            plan=saved.plan,
            title=saved.title,
            summary="A second signal.",
            content_sha256=saved.content_sha256,
            artifact_id=saved.artifact_id,
        )
        owner.create_agent_session(
            campaign_id=campaign,
            session_id=builder,
            role=WriterRole.BUILDER,
            provider_id="test",
            model_id="test",
            selected_skill="BUILD CAMPAIGN PLAN",
        )
        owner.record_builder_evidence(
            session_id=builder,
            evidence_id="source-evidence",
            source_kind="user_provided",
            content_sha256=digest(original),
            source_locator=None,
            safe_excerpt="Relay investigation.",
            claims=("relay",),
        )
        content = {"campaign_plan": original, "campaign_plan_markdown": "# Signal Relay"}
        suffix = hashlib.sha256(campaign.encode()).hexdigest()[:24]
        plan = owner.admit_campaign_plan(
            campaign_id=campaign,
            plan_id=f"campaign-plan-{suffix}",
            builder_session_id=builder,
            expected_revision=0,
            expected_package_lock_id=owner.current_package_lock(campaign).lock_id,
            content=content,
            content_sha256=digest(content),
            provenance_evidence_ids=("source-evidence",),
            validation_outcome="valid",
            acceptance_status="accepted",
        )
        owner.complete_turn(campaign, turn)
        owner.handoff_builder_to_dm(
            campaign_id=campaign,
            handoff_id=f"builder-dm-handoff-{suffix}",
            builder_session_id=builder,
            dm_session_id=session,
            dm_provider_id="test",
            dm_model_id="test",
            expected_writer=WriterRole.BUILDER,
            expected_revision=1,
            expected_package_lock_id=plan.package_lock_id,
            expected_package_candidate_id=candidate.candidate_id,
            plan_id=plan.plan_id,
            expected_plan_revision=1,
            expected_plan_content_sha256=plan.content_sha256,
            completed_turn_id=turn,
            model_guidance_override=True,
        )
        _finish(
            owner,
            campaign,
            session,
            original,
            "first",
            resolve_story=getattr(request, "param", None) != "early",
        )
        yield owner, campaign, session, database, source


def _review(owner, campaign, session):
    source_id = next(
        row.artifact_id
        for row in reusable_campaign_plan_catalog(owner._content_storage_root)
        if row.title == "Second Signal"
    )
    return owner.preview_campaign_sequel(
        campaign_id=campaign, session_id=session, source_id=source_id
    )


def _request(preview, operation="append-second"):
    return {
        "campaign_id": preview["basis"]["campaign_id"],
        "session_id": preview["basis"]["session_id"],
        "operation_id": operation,
        "source_id": preview["basis"]["source_id"],
        "expected_revision": preview["basis"]["expected_revision"],
        "preview_sha256": preview["preview_sha256"],
        "review": REVIEW,
    }


def test_append_keeps_original_state_history_sources_and_new_order_on_reopen(sequel):
    owner, campaign, session, database, source = sequel
    original = owner.accepted_campaign_plan(campaign)
    before = owner.snapshot(campaign)
    old_messages = owner.session_context(session).messages
    old_ending = owner.session_history_page(session).campaign_ending
    source_hash = digest(source.plan.payload)
    preview = _review(owner, campaign, session)
    assert owner.snapshot(campaign) == before  # Preview/cancel has no effects.
    assert preview["identity_mapping"]["act-1"] != "act-1"
    receipt = owner.append_campaign_sequel(**_request(preview))
    after = owner.snapshot(campaign)
    assert after.revision == before.revision + 1
    assert (after.state, after.participants, after.continuity, after.actions, after.events) == (
        before.state,
        before.participants,
        before.continuity,
        before.actions,
        before.events,
    )
    assert owner.campaign_plan(original.plan_id) == original
    assert owner.session_context(session).messages[: len(old_messages)] == old_messages
    assert digest(source.plan.payload) == source_hash
    from sylvia_gm.interfaces.owner_process import _admitted_dm_campaign_plan

    text, structured = _admitted_dm_campaign_plan(owner, campaign_id=campaign, session_id=session)
    assert structured == owner.accepted_campaign_plan(campaign).content["campaign_plan"]
    assert "SAME campaign" in text and "Do not apply" in text
    assert owner.session_history_page(session).campaign_ending is None
    assert owner.session_history_page(session).campaign_endings == (
        {**old_ending, "historical": True},
    )
    active = owner.accepted_campaign_plan(campaign)
    _finish(owner, campaign, session, active.content["campaign_plan"], "second")
    page = owner.session_history_page(session)
    assert len(page.campaign_endings) == 2
    assert page.campaign_ending["act_number"] == 2
    expected = owner.snapshot(campaign)
    owner.close()
    with AuthorityOwner.open(database, "sequel-reopen") as reopened:
        assert reopened.snapshot(campaign) == expected
        assert reopened.session_history_page(session).campaign_endings == page.campaign_endings
        sequence = json.loads(
            reopened.read_campaign_reference(campaign, source="campaign_plan", path="adventures")[
                "content"
            ]
        )
        assert [item["number"] for item in sequence] == [1, 2]
        assert sequence[0]["structure"][0]["id"] == "act-1"
        assert sequence[1]["structure"][0]["id"] == preview["identity_mapping"]["act-1"]
        assert reopened.append_campaign_sequel(**_request(preview)) == receipt


def test_stale_narration_is_rejected_even_without_a_state_revision(sequel):
    owner, campaign, session, _, _source = sequel
    preview = _review(owner, campaign, session)
    before = owner.snapshot(campaign)
    owner.append_agent_message(
        session_id=session,
        message_id="future",
        kind=SessionMessageKind.TALK,
        content={"text": "Years later the crew settles on the relay moon."},
    )
    assert owner.snapshot(campaign).revision == before.revision
    with pytest.raises(StaleRevision):
        owner.append_campaign_sequel(**_request(preview))
    assert owner.snapshot(campaign) == before


def test_failure_rolls_back_evidence_plan_revision_and_retry(sequel):
    owner, campaign, session, database, _source = sequel
    preview = _review(owner, campaign, session)
    before = owner.snapshot(campaign)
    messages = owner.session_context(session).messages
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TRIGGER fail_sequel BEFORE INSERT ON campaign_revisions "
            "WHEN NEW.plan_id LIKE 'campaign-sequel-%' BEGIN "
            "SELECT RAISE(ABORT, 'controlled sequel failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="controlled sequel failure"):
        owner.append_campaign_sequel(**_request(preview))
    assert owner.snapshot(campaign) == before
    assert owner.session_context(session).messages == messages
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER fail_sequel")
    receipt = owner.append_campaign_sequel(**_request(preview))
    assert owner.append_campaign_sequel(**_request(preview)) == receipt
    changed = _request(preview)
    changed["review"] = {**REVIEW, "connection": "Changed request."}
    with pytest.raises(IdempotencyConflict):
        owner.append_campaign_sequel(**changed)


def test_two_concurrent_requests_admit_one_adventure(sequel):
    owner, campaign, session, _, _source = sequel
    preview = _review(owner, campaign, session)

    def append(operation):
        try:
            return owner.append_campaign_sequel(**_request(preview, operation))
        except CommandRejected:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(append, ["one", "two"]))
    assert sum(item is not None for item in results) == 1
    assert len(owner._store.campaign_adventures(campaign)) == 2


@pytest.mark.parametrize("fault", ["binding", "fixed_character", "missing_source", "unreviewed"])
def test_incompatible_or_unreviewed_additions_change_nothing(sequel, monkeypatch, fault):
    owner, campaign, session, _, source = sequel
    before = owner.snapshot(campaign)
    messages = owner.session_context(session).messages
    if fault == "unreviewed":
        request = _request(_review(owner, campaign, session))
        request["review"] = {**REVIEW, "reviewed": False}
        with pytest.raises(CommandRejected):
            owner.append_campaign_sequel(**request)
    else:
        if fault == "missing_source":

            def unavailable(*_args):
                raise ValueError("missing closure")

            monkeypatch.setattr(
                "sylvia_gm.application.campaign_sequels.selected_campaign_plan", unavailable
            )
        else:
            raw = json.loads(canonical_json_bytes(source.plan.payload))
            if fault == "binding":
                raw["bindings"]["ruleset"]["content_sha256"] = "9" * 64
            else:
                raw["starting_party"]["player_character"] = {
                    "mode": "fixed",
                    "character": {"id": "other", "path": "other.json"},
                }
            source.plan = SimpleNamespace(payload=raw)
            monkeypatch.setattr(
                "sylvia_gm.application.campaign_sequels.selected_campaign_plan",
                lambda *_args: source,
            )
        with pytest.raises(CommandRejected):
            _review(owner, campaign, session)
    assert owner.snapshot(campaign) == before
    assert owner.session_context(session).messages == messages


def test_source_adventure_ids_are_mapped_without_changing_world_or_prose():
    prepared, mapping = prepare_story(EXAMPLE, 2)
    assert prepared["structure"][0]["objectives"][0] == mapping["understand-signal"]
    assert prepared["bindings"] == EXAMPLE["bindings"]
    assert prepared["initial_state"] == EXAMPLE["initial_state"]
    assert prepared["starting_party"] == EXAMPLE["starting_party"]
    assert prepared["premise"] == EXAMPLE["premise"]


def test_export_recovery_and_backup_retain_both_adventures_without_library(sequel):
    owner, campaign, session, database, _source = sequel
    preview = _review(owner, campaign, session)
    receipt = owner.append_campaign_sequel(**_request(preview))
    _finish(
        owner,
        campaign,
        session,
        owner.accepted_campaign_plan(campaign).content["campaign_plan"],
        "next",
    )
    expected = owner.snapshot(campaign)
    history = owner.session_history_page(session)
    original_plans = owner._store.campaign_adventures(campaign)
    archive = database.parent / "expanded.sylvia-gm-campaign"
    backup = database.parent / "backup.sqlite3"
    owner.export_campaign("export-expanded", campaign, archive)
    owner.backup_database(backup)
    owner.close()
    clean = database.parent / "clean"
    clean.mkdir()
    with AuthorityOwner.open(clean / "recovered.sqlite3", "sequel-recovery") as recovered:
        recovered.recover_campaign(recovery_id="recover-expanded", source=archive)
        assert recovered.snapshot(campaign) == expected
        assert recovered._store.campaign_adventures(campaign) == original_plans
        assert recovered.session_history_page(session).campaign_endings == history.campaign_endings
        # A lost acknowledgement remains replayable without the original author's library.
        assert recovered.append_campaign_sequel(**_request(preview)) == receipt
    with AuthorityOwner.open(backup, "sequel-backup") as restored:
        assert restored.snapshot(campaign) == expected
        assert restored._store.campaign_adventures(campaign) == original_plans


def test_other_campaign_and_original_library_bytes_are_unchanged(sequel):
    owner, campaign, session, database, _source = sequel
    snapshot = owner.snapshot(campaign)
    owner.create_campaign(
        CampaignCreate(
            campaign_id="other",
            display_name="Independent relay",
            writer=WriterRole.DM,
            package_lock=snapshot.package_lock,
            turn_id="other-turn",
            participants=snapshot.participants,
            initial_state=(
                StateValue(snapshot.package_lock.entries[0].package_id, "state", {"stress": 9}),
            ),
        )
    )
    other = owner.snapshot("other")
    library = database.parent / "campaign-plans"
    before = {
        path.relative_to(library).as_posix(): path.read_bytes()
        for path in library.rglob("*")
        if path.is_file()
    }
    owner.append_campaign_sequel(**_request(_review(owner, campaign, session)))
    assert owner.snapshot("other") == other
    assert {
        path.relative_to(library).as_posix(): path.read_bytes()
        for path in library.rglob("*")
        if path.is_file()
    } == before


def test_active_gm_work_blocks_append_without_interrupting_it(sequel):
    owner, campaign, session, _database, _source = sequel
    preview = _review(owner, campaign, session)
    before = owner.snapshot(campaign)
    with (
        owner.agent_session_work(session),
        pytest.raises(CommandRejected, match="work is running"),
    ):
        owner.append_campaign_sequel(**_request(preview))
    assert owner.snapshot(campaign) == before
    owner.append_campaign_sequel(**_request(preview))


def test_missing_contained_source_after_review_is_atomic(sequel):
    owner, campaign, session, database, _source = sequel
    preview = _review(owner, campaign, session)
    before = owner.snapshot(campaign)
    files = list((database.parent / "campaign-plans").rglob("setting.sylvia-gm-package"))
    assert len(files) == 1
    files[0].unlink()
    with pytest.raises(CommandRejected, match="source closure"):
        owner.append_campaign_sequel(**_request(preview))
    assert owner.snapshot(campaign) == before


def test_unresolved_preparation_remains_usable_in_the_next_adventure(sequel):
    owner, campaign, session, _database, _source = sequel
    owner.append_campaign_sequel(**_request(_review(owner, campaign, session)))
    assert "return-recorder" in owner.campaign_narrative_options(session)["plan_item_ids"]
    assert "keeper-promise" in {
        item["id"] for item in owner.campaign_knowledge_catalog(session)["secrets"]
    }
    owner.append_agent_message(
        session_id=session,
        message_id="keep-promise",
        kind=SessionMessageKind.USER,
        content={"text": "I return the recorder."},
    )
    owner.update_campaign_file(
        session_id=session,
        update_id="kept-promise",
        basis_message_ids=("keep-promise",),
        updates=(
            {
                "operation": "update_plan_progress",
                "plan_item_id": "return-recorder",
                "progress_status": "resolved",
                "progress_summary": "The keeper received it.",
            },
            {"operation": "reveal_secret", "secret_id": "keeper-promise", "audience": ["player"]},
        ),
    )
    assert owner.snapshot(campaign).continuity.reveals["keeper-promise"] == ["player"]


@pytest.mark.parametrize("sequel", ["early"], indirect=True)
def test_early_old_ending_does_not_complete_or_gate_unplayed_acts(sequel):
    owner, campaign, session, _database, _source = sequel
    owner.append_campaign_sequel(**_request(_review(owner, campaign, session)))
    plan = owner.accepted_campaign_plan(campaign).content["campaign_plan"]
    _finish(owner, campaign, session, plan, "later")
    progress = {
        item["plan_item_id"]
        for item in owner.snapshot(campaign).continuity.plan_progress
        if item["status"] == "resolved"
    }
    assert "act-1" not in progress and "act-2" not in progress
    assert owner.session_history_page(session).campaign_ending is not None


def test_interrupted_reply_must_be_resolved_before_append(sequel):
    owner, campaign, session, _database, _source = sequel
    preview = _review(owner, campaign, session)
    owner.start_narration_attempt(
        campaign_id=campaign,
        attempt_id="interrupted",
        turn_id=owner.current_open_turn(campaign),
        cancel=lambda: None,
    )
    owner.interrupt_narration(campaign, "interrupted")
    before = owner.snapshot(campaign)
    with pytest.raises(CommandRejected, match="interrupted GM reply"):
        owner.append_campaign_sequel(**_request(preview))
    assert owner.snapshot(campaign) == before


def test_duplicate_private_fact_is_not_reintroduced_as_unknown(sequel, monkeypatch):
    owner, campaign, session, _database, source = sequel
    raw = json.loads(canonical_json_bytes(source.plan.payload))
    raw["secrets"] = [
        {
            "id": "new-unknown-promise",
            "fact": "The keeper is still owed a recorder.",
            "known_by": [],
            "reveal": {"condition": "Rediscover the promise.", "from_act": "act-1"},
        }
    ]
    source.plan = SimpleNamespace(payload=raw)
    monkeypatch.setattr(
        "sylvia_gm.application.campaign_sequels.selected_campaign_plan", lambda *_args: source
    )
    before = owner.snapshot(campaign)
    with pytest.raises(CommandRejected, match="retained private fact"):
        _review(owner, campaign, session)
    assert owner.snapshot(campaign) == before


def test_overlapping_act_and_objective_ids_require_author_repair():
    raw = copy.deepcopy(EXAMPLE)
    raw["objectives"][0]["id"] = "act-1"
    raw["structure"][0]["objectives"] = ["act-1"]
    with pytest.raises(ValueError, match="identities overlap"):
        prepare_story(raw, 2)


@pytest.mark.parametrize("sequel", ["afterstory"], indirect=True)
def test_app_transport_preserves_narrated_future_and_plays_the_new_act(sequel):
    from tests.qa_attached_request import request_attached
    from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
    from tests.test_fc_campaign_progress import _play, _ProgressProvider, _write

    owner, campaign, session, database, source = sequel
    before = owner.snapshot(campaign)
    turn = owner.current_open_turn(campaign) or "after-story-turn"
    owner.close()
    future = "Five years pass. Iona keeps the relay operational and the crew finally reunites."
    future_step = _write(future)
    future_step[1]["revised_player_facing_response"].insert(
        0, {"kind": "ooc", "text": "The story is finished; here is the requested after-story."}
    )
    provider = _ProgressProvider([future_step])
    harness = _OwnerHarness(database, provider)
    try:
        reply = request_attached(
            harness, "finish-before-sequel", "submit_session_input",
            {"session_id": session, "attempt_id": "after-story-attempt",
             "message_id": "after-story-choice", "turn_id": turn,
             "expected_revision": before.revision, "user_text": "",
             "campaign_ending_choice": {"ending_id": "signal-settled", "choice": "finish"}},
            campaign_id=campaign, attempt_id="after-story-attempt",
        )
        assert reply["interruption"] is None, reply
        context = harness.request("context", "campaign_sequel_context", {
            "campaign_id": campaign, "session_id": session,
        })
        assert context["ending"]["choice"] == "finish"
        assert context["ending"]["has_after_story"] is True
        selection = {"campaign_id": campaign, "session_id": session,
                     "source_id": source.artifact_id}
        preview = harness.request("review", "preview_campaign_sequel", selection)
        receipt = harness.request("append", "append_campaign_sequel", _request(preview))
        assert harness.request("retry", "append_campaign_sequel", _request(preview)) == receipt
        act = preview["identity_mapping"]["act-1"]
    finally:
        harness.close()
        assert provider.failure is None, (provider.failure, provider.receipts)
    with AuthorityOwner.open(database, "after-story-retention") as reopened:
        old_messages = reopened.session_context(session).messages
        assert future in json.dumps([dict(item.content) for item in old_messages])
        assert reopened.snapshot(campaign).state == before.state
        assert reopened.snapshot(campaign).participants == before.participants
    next_scene = "After the five saved years, Iona follows the second signal with the same crew."
    _play(database, campaign, session, 99, [
        ("read_campaign_reference", {"source": "campaign_plan",
                                     "path": f"structured/structure/{act}"}),
        ("update_campaign_file", {
            "state_change": "changed", "state_change_summary": next_scene,
            "scenes": [{"act_id": act, "summary": next_scene}],
            "revised_player_facing_response": [{"kind": "narration", "text": next_scene}],
        }),
    ])
    with AuthorityOwner.open(database, "new-adventure-reopen") as reopened:
        snapshot = reopened.snapshot(campaign)
        assert snapshot.continuity.current_scene["act_id"] == act
        assert (snapshot.state, snapshot.participants) == (before.state, before.participants)
        assert reopened.session_context(session).messages[:len(old_messages)] == old_messages
        page = reopened.session_history_page(session)
        assert page.campaign_endings[0]["historical"] is True
        assert page.campaign_endings[0]["choice"] == "finish"
        assert page.act_markers[-1]["act_number"] == 2
