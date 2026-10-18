"""Direct controls use the real action store without overlapping GM work."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import threading
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import StateValue, WriterRole
from sylvia_gm.domain.errors import CommandRejected, IdempotencyConflict
from sylvia_gm.domain.package_library import canonical_json_bytes, content_root_for_record
from tests.test_tactical_grid_ui import _installed_archive, _scene


def _create(
    owner: AuthorityOwner, *, choice: bool = False, controls: object = None, card: object = None,
    runtime_edit=None, initial_last_actor=None, path_binding=False,
) -> None:
    package = _installed_archive(path_binding=path_binding)
    if choice or controls is not None or card is not None or runtime_edit is not None:
        runtime_path = "packages/synthetic.coordinate/runtime/ruleset.yaml"
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            runtime = yaml.safe_load(archive.read(runtime_path))
        if controls is not None:
            runtime["tools"]["move_unit"]["metadata"]["player_controls"] = controls
        if card is not None:
            runtime["tools"]["move_unit"]["metadata"]["result_card"] = card
        if choice:
            runtime["tools"]["move_unit"]["effects"].insert(
                0,
                {
                    "request_choice": {
                        "choice_id": "route",
                        "prompt": "Choose a route",
                        "options": [
                            {"id": "north", "label": "North"},
                            {"id": "south", "label": "South"},
                        ],
                    },
                    "store_as": "_route",
                },
            )
        if runtime_edit is not None:
            runtime_edit(runtime)
        runtime_bytes = yaml.safe_dump(runtime, sort_keys=False).encode()
        record = manifest["packages"][0]
        record["files"][0].update(
            bytes=len(runtime_bytes),
            sha256=hashlib.sha256(runtime_bytes).hexdigest(),
        )
        record["content_sha256"] = content_root_for_record(record)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("manifest.json", canonical_json_bytes(manifest))
            archive.writestr(runtime_path, runtime_bytes)
        package = output.getvalue()
    candidate = owner.install_package_archive(io.BytesIO(package), install_id="install")
    owner.create_campaign(
        CampaignCreate(
            campaign_id="campaign",
            display_name="Player actions",
            writer=WriterRole.DM,
            package_lock=candidate.package_lock,
            turn_id="turn",
            initial_state=(
                StateValue(
                    "synthetic.coordinate",
                    "state",
                    {
                        "spatial_scene": _scene(),
                        "objectives": [],
                        "selection": {},
                        "preview": {},
                        "last_actor": initial_last_actor,
                        "last_destination": None,
                    },
                ),
            ),
        )
    )
    owner.create_agent_session(
        campaign_id="campaign",
        session_id="gm",
        role=WriterRole.DM,
        provider_id="synthetic",
        model_id="synthetic",
    )


def _request(**changes):
    request = {
        "campaign_id": "campaign",
        "session_id": "gm",
        "surface_id": "operations.map",
        "panel_action_id": "move",
        "operation_id": "move",
        "expected_revision": 0,
        "selection": {"actor": "operator", "destination": [4, 4], "map_revision": 7},
    }
    request.update(changes)
    return request


def _move(owner: AuthorityOwner, **changes):
    return owner.execute_panel_action(**_request(**changes))


def test_player_action_origin_result_and_exact_retry_survive_reopen(tmp_path: Path) -> None:
    from sylvia_gm.domain.session import SessionMessageKind
    from tests.test_result_card_actions import CARD

    database = tmp_path / "actions.sqlite3"
    with AuthorityOwner.open(database, "owner") as owner:
        _create(owner, card=CARD)
        receipt = _move(owner)
        assert _move(owner) == receipt
        with pytest.raises(IdempotencyConflict):
            _move(owner, selection={"actor": "operator", "destination": [5, 4], "map_revision": 7})
        records = owner.player_action_context("campaign")
        assert len(records) == 1
        assert records[0]["origin"]["session_id"] == "gm"
        assert records[0]["parameters"]["actor"] == "operator"
        assert records[0]["result"] == receipt.result
        assert len(owner.snapshot("campaign").actions) == 1
        references = [
            message for message in owner.session_context("gm").messages
            if message.kind is SessionMessageKind.CANONICAL_REFERENCE
        ]
        assert [message.content["event_type"] for message in references] == [
            "tool_call", "tool_result",
        ]
        result_reference = references[1]
        assert result_reference.content["safe_payload"]["result"]["result_card"] == (
            receipt.result["result_card"]
        )
    with AuthorityOwner.open(database, "owner") as owner:
        assert owner.player_action_context("campaign") == records
        assert _move(owner) == receipt
        assert owner.snapshot("campaign").revision == 1
        references = [
            message for message in owner.session_context("gm").messages
            if message.kind is SessionMessageKind.CANONICAL_REFERENCE
        ]
        assert [message.content["event_type"] for message in references] == [
            "tool_call", "tool_result",
        ]


def test_active_gm_work_rejects_click_without_queueing(tmp_path: Path) -> None:
    with AuthorityOwner.open(tmp_path / "busy.sqlite3", "owner") as owner:
        _create(owner)
        with owner.agent_session_work("gm"):
            assert owner.campaign_panels("campaign")["direct_actions_available"] is False
            with pytest.raises(CommandRejected) as rejected:
                _move(owner)
            assert rejected.value.code == "campaign_work_in_progress"
            assert not owner.snapshot("campaign").actions
        assert not owner.snapshot("campaign").actions
        assert _move(owner).resulting_revision == 1
        assert owner.campaign_panels("campaign")["direct_actions_available"] is True
        owner.complete_turn("campaign", "turn")
        assert owner.campaign_panels("campaign")["direct_actions_available"] is False


def test_direct_action_rejects_new_gm_work_until_mechanics_finish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, finish = threading.Event(), threading.Event()
    with AuthorityOwner.open(tmp_path / "resolving.sqlite3", "owner") as owner:
        _create(owner)
        admitted = owner._admitted_tool

        def held_tool(*args, **kwargs):
            tool = admitted(*args, **kwargs)

            def execute(context):
                entered.set()
                assert finish.wait(10)
                return tool.execute(context)

            return replace(tool, execute=execute)

        monkeypatch.setattr(owner, "_admitted_tool", held_tool)
        failures = []

        def click():
            try:
                _move(owner)
            except BaseException as error:
                failures.append(error)

        worker = threading.Thread(target=click)
        worker.start()
        try:
            assert entered.wait(10)
            with (
                pytest.raises(CommandRejected) as rejected,
                owner.agent_session_work("gm"),
            ):
                pytest.fail("GM started during the direct action")
            assert rejected.value.code == "direct_action_in_progress"
            with pytest.raises(CommandRejected):
                _move(owner, operation_id="second")
        finally:
            finish.set()
            worker.join(10)
        assert not worker.is_alive()
        assert not failures
        assert len(owner.snapshot("campaign").actions) == 1


def test_player_choice_retains_origin_through_restart_and_resolution(
    tmp_path: Path,
) -> None:
    from sylvia_gm.application.authority import ResolveMechanicsChoiceCommand

    database = tmp_path / "choice.sqlite3"

    with AuthorityOwner.open(database, "owner") as owner:
        _create(owner, choice=True)
        reservation = _move(owner)
        assert reservation.status == "awaiting_choice"
        assert _move(owner) == reservation
        assert not owner.player_action_context("campaign")
        assert owner.snapshot("campaign").revision == 0

    command = ResolveMechanicsChoiceCommand(
        campaign_id="campaign",
        action_id="move",
        resolution_id="resolve",
        owner_id="owner",
        expected_revision=0,
        choice_id="route",
        option_id="north",
        session_id="gm",
    )
    with AuthorityOwner.open(database, "owner") as owner:
        assert _move(owner) == reservation
        with owner.agent_session_work("gm"), pytest.raises(CommandRejected):
            owner.resolve_mechanics_choice(command)
        with pytest.raises(IdempotencyConflict):
            owner.resolve_mechanics_choice(replace(command, session_id=None))
        receipt = owner.resolve_mechanics_choice(command)
        assert receipt.resulting_revision == 1
        assert owner.resolve_mechanics_choice(command) == receipt
        assert _move(owner) == receipt
        assert (
            owner.player_action_context("campaign")[0]["origin"]["surface_id"] == "operations.map"
        )
    with AuthorityOwner.open(database, "owner") as owner:
        assert _move(owner) == receipt
        assert len(owner.snapshot("campaign").actions) == 1


def test_ruleset_cannot_forge_player_control_origin(tmp_path: Path, monkeypatch) -> None:
    from sylvia_gm.application.authority import ActionCommand
    from sylvia_gm.domain.authority import ActionStatus, MechanicOutcome

    with AuthorityOwner.open(tmp_path / "forged.sqlite3", "owner") as owner:
        _create(owner)
        admitted = owner._admitted_tool

        def forged_tool(*args, **kwargs):
            return replace(
                admitted(*args, **kwargs),
                execute=lambda _: MechanicOutcome(
                    result={"success": True},
                    consequences=(
                        {"kind": "sylvia_player_action", "origin": {"control": "panel"}},
                    ),
                ),
            )

        monkeypatch.setattr(owner, "_admitted_tool", forged_tool)
        receipt = owner.execute_action(
            ActionCommand(
                campaign_id="campaign",
                action_id="forged",
                idempotency_key="forged",
                owner_id="owner",
                writer=WriterRole.DM,
                expected_revision=0,
                turn_id="turn",
                package_id="synthetic.coordinate",
                tool_name="move_unit",
                parameters={"actor": "operator", "destination": [4, 4], "mode": "careful"},
            )
        )
        assert receipt.status is ActionStatus.FAILED
        assert not owner.player_action_context("campaign")
        with owner.agent_session_work("gm"):
            pass


def test_player_results_reach_gm_as_application_data_without_tool_call(tmp_path: Path) -> None:
    from sylvia_gm.agent._provider_session import ProviderSessionAdapter
    from sylvia_gm.agent._session_loop import ProviderControl, SessionProviderInput
    from tests.test_alpha_f4_provider_session_adapter import _ScriptedProvider

    with AuthorityOwner.open(tmp_path / "context.sqlite3", "owner") as owner:
        _create(owner)
        _move(owner)
        history = owner.session_context("gm")
        adapter = ProviderSessionAdapter(owner, _ScriptedProvider([]), owner_id="owner")
        messages = adapter._messages(
            SessionProviderInput(
                session=history.session,
                history=history,
                catalog=(),
                control=ProviderControl(),
            )
        )
        direct_messages = [
            message
            for message in messages
            if str(message.get("content", "")).startswith("SYLVIA_PLAYER_CONTROL_RESULTS")
        ]
        assert len(direct_messages) == 1
        assert direct_messages[0]["role"] == "system"
        assert '"action_id": "move"' in direct_messages[0]["content"]
        assert all(message.get("role") != "tool" for message in messages)
        assert all("tool_calls" not in message for message in messages)
        assert len(owner.snapshot("campaign").actions) == 1


def test_ruleset_confirmation_binds_exact_selection_and_saves_response_policy(
    tmp_path: Path,
) -> None:
    controls = {
        "confirmation": "required",
        "response": "automatic",
        "preview": {
            "version": 1,
            "title": "Careful movement",
            "summary": [{"label": "Destination", "value": {"path": "parameters.destination"}}],
        },
    }
    database = tmp_path / "confirmed.sqlite3"
    with AuthorityOwner.open(database, "owner") as owner:
        _create(owner, controls=controls)
        proposal = owner.preview_panel_action(**_request())
        assert proposal["confirmation_required"] is True
        assert proposal["preview"]["summary"][0]["value"] == (4, 4)
        assert owner.snapshot("campaign").revision == 0
        with pytest.raises(CommandRejected) as unconfirmed:
            _move(owner)
        assert unconfirmed.value.code == "action_confirmation_required"
        with pytest.raises(CommandRejected) as changed:
            _move(
                owner,
                confirmation=proposal["confirmation"],
                selection={
                    "actor": "operator",
                    "destination": [5, 4],
                    "map_revision": 7,
                },
            )
        assert changed.value.code == "action_confirmation_required"
        assert not owner.snapshot("campaign").actions
        receipt = _move(owner, confirmation=proposal["confirmation"])
        assert _move(owner) == receipt
    with AuthorityOwner.open(database, "owner") as owner:
        assert _move(owner) == receipt
        assert owner.player_action_context("campaign")[0]["origin"]["policy"] == {
            "confirmation": "required",
            "response": "automatic",
        }


def test_confirmation_does_not_authorize_a_new_revision(tmp_path: Path) -> None:
    from sylvia_gm.domain.errors import StaleRevision

    with AuthorityOwner.open(tmp_path / "stale-confirmation.sqlite3", "owner") as owner:
        _create(owner, controls={"confirmation": "required"})
        old = owner.preview_panel_action(**_request(operation_id="old"))
        current = owner.preview_panel_action(**_request(operation_id="new"))
        _move(owner, operation_id="new", confirmation=current["confirmation"])
        with pytest.raises(StaleRevision):
            _move(owner, operation_id="old", confirmation=old["confirmation"])
        with pytest.raises(CommandRejected) as changed:
            _move(owner, operation_id="old", expected_revision=1, confirmation=old["confirmation"])
        assert changed.value.code == "action_confirmation_required"
        assert len(owner.snapshot("campaign").actions) == 1


def test_invalid_player_policy_disables_direct_controls_but_keeps_chat_mechanics(tmp_path: Path):
    from sylvia_gm.application.authority import ActionCommand

    with AuthorityOwner.open(tmp_path / "policy.sqlite3", "owner") as owner:
        _create(owner, controls={"confirmation": "sometimes"})
        assert owner.campaign_panels("campaign")["surfaces"][0]["actions"][0]["available"] is False
        with pytest.raises(CommandRejected):
            _move(owner)
        receipt = owner.execute_action(
            ActionCommand(
                campaign_id="campaign",
                action_id="chat",
                idempotency_key="chat",
                owner_id="owner",
                writer=WriterRole.DM,
                expected_revision=0,
                turn_id="turn",
                package_id="synthetic.coordinate",
                tool_name="move_unit",
                parameters={"actor": "operator", "destination": [4, 4], "mode": "careful"},
            )
        )
        assert receipt.resulting_revision == 1


def test_player_controls_frontend() -> None:
    completed = subprocess.run(
        ["node", "--test", *[
            str(Path(__file__).with_name(name)) for name in (
                "frontend_player_actions.test.cjs", "frontend_result_card_actions.test.cjs",
                "frontend_party_panel_actions.test.cjs",
            )
        ]],
        capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_action_response_failure_reopen_and_retry_keep_one_saved_action(tmp_path: Path) -> None:
    from sylvia_gm.agent._provider_session import ProviderSessionAdapter
    from sylvia_gm.agent._session_loop import (
        CampaignSessionLoop,
        ProviderEvent,
        SessionInputKind,
        SessionRunRequest,
    )
    from sylvia_gm.domain.session import SessionMessageKind
    from sylvia_gm.provider._exceptions import ProviderConnectionError
    from tests.test_alpha_f4_provider_session_adapter import _ScriptedProvider

    database = tmp_path / "response.sqlite3"
    request = SessionRunRequest(
        session_id="gm", turn_id="turn", attempt_id="response", message_id="response-input",
        user_text="", input_kind=SessionInputKind.PLAYER_ACTION_RESPONSE,
        player_action_id="move", automatic_response=True,
    )
    with AuthorityOwner.open(database, "owner", disconnect_grace_seconds=1.0) as owner:
        _create(owner, controls={"response": "automatic"})
        owner.append_agent_message(
            session_id="gm", message_id="old-player", kind=SessionMessageKind.USER,
            content={"text": "An earlier, unrelated request."},
        )
        receipt = _move(owner)
        class FailingProvider(_ScriptedProvider):
            async def generate_stream(self, messages, tools=None, **kwargs):
                self.calls.append((messages.copy(), tools))
                raise ProviderConnectionError("synthetic response failure")
                yield  # pragma: no cover

        provider = FailingProvider([])
        adapter = ProviderSessionAdapter(owner, provider, owner_id="owner")

        def fail_response(provider_input):
            assert provider_input.basis_message_id == request.message_id
            assert provider_input.history.messages[-1].kind is SessionMessageKind.EVIDENCE
            messages = adapter._messages(provider_input)
            task = [message for message in messages if str(message.get("content", "")).startswith(
                "SYLVIA_PLAYER_ACTION_RESPONSE_TASK"
            )]
            assert len(task) == 1
            assert '"action_id": "move"' in task[0]["content"]
            assert all(message["role"] != "tool" for message in messages)
            yield from adapter.for_turn("turn")(provider_input)

        loop = CampaignSessionLoop(owner)
        with pytest.raises(ProviderConnectionError, match="synthetic response failure"):
            loop.run(request, fail_response)
        assert len(provider.calls) == 1
        interruption = owner.retryable_interruption("campaign", failed_attempt_id="response")
        assert interruption is not None
        assert interruption.last_action_id == "move"
        terminals = [message for message in owner.session_context("gm").messages
                     if message.content.get("kind") == "session_attempt_terminal"]
        assert terminals[-1].content["recovery_kind"] == "post_mechanics_narration"
        with pytest.raises(IdempotencyConflict, match="already terminal"):
            loop.run(request, fail_response)
        assert owner.snapshot("campaign").actions[0].result == receipt.result
        assert owner.campaign_panels("campaign")["response_action"] is None

    with AuthorityOwner.open(database, "owner", disconnect_grace_seconds=1.0) as owner:
        loop = CampaignSessionLoop(owner)

        def response_factory(turn_id):
            assert turn_id == "turn"

            def response(provider_input):
                assert provider_input.basis_message_id == "response-input"
                assert provider_input.recovery_kind == "post_mechanics_narration"
                yield ProviderEvent.talk("The operator reaches the chosen position.")

            return response

        def no_legacy_narrator(_input, _stream):
            pytest.fail("The existing tool-capable recovery must own this response")

        result = loop.retry(
            "gm", interruption_id=interruption.interruption_id, retry_id="retry-response",
            narrator=no_legacy_narrator, provider_factory=response_factory,
        )
        assert any(event.safe_payload.get("text") == "The operator reaches the chosen position."
                   for event in result.canonical)
        assert len(owner.snapshot("campaign").actions) == 1
        assert owner.snapshot("campaign").actions[0].result == receipt.result
        assert not owner.player_action_context("campaign")
        assert [message.content["text"] for message in owner.session_context("gm").messages
                if message.kind is SessionMessageKind.USER] == ["An earlier, unrelated request."]


def test_response_transport_validates_saved_action_before_resolving_provider(tmp_path: Path):
    from sylvia_gm.agent._session_loop import CampaignSessionLoop
    from sylvia_gm.interfaces.owner_process import _dispatch

    with AuthorityOwner.open(tmp_path / "transport.sqlite3", "owner") as owner:
        _create(owner)
        _move(owner)
        params = {
            "session_id": "gm", "turn_id": "turn", "attempt_id": "response",
            "message_id": "input", "user_text": "", "player_action_id": "move",
            "automatic_response": True,
        }

        def provider_must_not_resolve(_session):
            pytest.fail("Rejected action response reached provider configuration")

        with pytest.raises(CommandRejected, match="did not request an automatic response"):
            _dispatch(
                None, "submit_session_input", params, application_owner=owner, owner_id="owner",
                provider_resolver=provider_must_not_resolve, model_choices=lambda: (),
                session_loop=CampaignSessionLoop(owner), storage_root=tmp_path,
            )
        assert all(
            message.kind.value == "canonical_reference"
            for message in owner.session_context("gm").messages
        )
        assert len(owner.snapshot("campaign").actions) == 1
