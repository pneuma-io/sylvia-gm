"""Chapter announcements come from durable play, without exposing private preparation."""

from pathlib import Path

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ProseChannel
from sylvia_gm.domain.session import SessionMessageKind
from sylvia_gm.infrastructure.launch_handshake import derive_owner_response
from sylvia_gm.interfaces.local_api import LocalCommandBus
from sylvia_gm.interfaces.local_owner import LocalOwnerSupervisor
from tests.test_alpha_05_rift_journey import _construct_rift
from tests.test_alpha_f4_generic_sessions import _prepare_handoff


def test_saved_act_boundaries_are_private_safe_paged_and_reopen_without_duplicates(
    tmp_path: Path,
) -> None:
    database = tmp_path / "chapters.sqlite3"
    supervisor = LocalOwnerSupervisor()
    issued = supervisor.acquire_owner(database, "chapter-test")
    supervisor.complete_handshake(derive_owner_response(issued.secret_hex))
    owner = supervisor.application_owner()
    campaign, session = _construct_rift(
        LocalCommandBus(supervisor), owner, tmp_path, lane="quick_start", companion_ids=(),
    )
    turn = owner.current_open_turn(campaign)
    assert turn is not None

    def input_message(number: int) -> str:
        identity = f"chapter-input-{number}"
        owner.append_agent_message(
            session_id=session, message_id=identity, kind=SessionMessageKind.USER,
            content={"text": "Continue the selected synthetic scene."},
        )
        return identity

    def progress(number: int, item: str, status: str, basis: str) -> None:
        owner.record_campaign_plan_progress(
            session_id=session, update_id=f"chapter-progress-{number}", plan_item_id=item,
            progress_status=status, summary="PRIVATE consequence details.",
            basis_message_ids=(basis,), turn_id=turn,
        )

    def narration(number: int) -> str:
        attempt = f"chapter-narration-{number}"
        owner.start_narration_attempt(
            campaign_id=campaign, attempt_id=attempt, turn_id=turn, cancel=lambda: None,
        )
        owner.emit_narration_token(campaign, attempt, ProseChannel.TALK, "Public aftermath.")
        events = owner.complete_narration_attempt(campaign, attempt)
        return owner.append_session_canonical_references(session, events)[-1].message_id

    def scene(number: int, act: str, basis: str) -> None:
        owner.record_campaign_scene(
            session_id=session, scene_id=f"chapter-scene-{number}", act_id=act,
            summary="PRIVATE scene notes.", location_id=None, basis_message_ids=(basis,),
            turn_id=turn,
        )

    try:
        assert owner.session_history_page(session).act_markers == ()
        first = input_message(1)
        progress(1, "arrival-and-waystation", "resolved", first)
        progress(2, "act-2", "introduced", first)
        narration(1)
        # An objective ending and an offered future lead cannot declare an act transition.
        assert owner.session_history_page(session).act_markers == ()

        second = input_message(2)
        progress(3, "act-1", "resolved", second)
        assert owner.session_history_page(session).act_markers == ()  # narration still held
        completed_at = narration(2)
        progress(3, "act-1", "resolved", second)  # exact idempotent replay
        assert [dict(x) for x in owner.session_history_page(session).act_markers] == [{
            "marker_id": "act-1-complete", "after_message_id": completed_at,
            "act_number": 1, "status": "complete",
        }]

        third = input_message(3)
        scene(1, "act-2", third)
        progress(4, "act-1", "resolved", third)  # a refreshed summary is not another ending
        began_at = narration(3)
        fourth = input_message(4)
        scene(2, "act-1", fourth)  # revisiting a place in a previous act
        progress(5, "act-3", "retired", fourth)
        narration(4)
        fifth = input_message(5)
        progress(6, "act-6", "resolved", fifth)
        finale_at = narration(5)
        expected = [
            {"marker_id": "act-1-complete", "after_message_id": completed_at,
             "act_number": 1, "status": "complete"},
            {"marker_id": "act-2-begins", "after_message_id": began_at,
             "act_number": 2, "status": "begins"},
            {"marker_id": "act-6-complete", "after_message_id": finale_at,
             "act_number": 6, "status": "complete"},
        ]
        revision = owner.snapshot(campaign).revision
        page = owner.session_history_page(session, limit=1)
        assert len(page.messages) == 1
        assert [dict(x) for x in page.act_markers] == expected
        older = owner.session_history_page(session, before_sequence=page.next_before_sequence)
        assert [dict(x) for x in older.act_markers] == expected
        assert owner.snapshot(campaign).revision == revision
    finally:
        supervisor.shutdown()

    with AuthorityOwner.open(database, "chapter-reopen") as reopened:
        assert [dict(x) for x in reopened.session_history_page(session).act_markers] == expected
        assert reopened.snapshot(campaign).revision == revision


def test_markdown_only_plans_and_builder_chats_have_no_invented_acts(tmp_path: Path) -> None:
    with AuthorityOwner.open(tmp_path / "unstructured.sqlite3", "chapter-test") as owner:
        request = _prepare_handoff(
            owner, suffix="markdown", plan_content={"campaign_plan_markdown": "A freeform game."},
        )
        owner.complete_turn(str(request["campaign_id"]), str(request["completed_turn_id"]))
        owner.handoff_builder_to_dm(**request)
        assert owner.session_history_page(str(request["dm_session_id"])).act_markers == ()
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="builder")
        assert owner.session_history_page("builder").act_markers == ()
