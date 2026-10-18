"""Admitted transports keep safe saved-session projections in both consumers."""

from dataclasses import replace

import pytest

from sylvia_gm.config.provider_settings import _TRANSPORTS
from sylvia_gm.domain.authority import WriterRole
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.session import SessionRecord
from sylvia_gm.interfaces._session_reasoning import reasoning_state, validate_model_preferences
from sylvia_gm.provider._base import FakeProvider
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness


def choices(transport):
    return ({
        "provider_id": "test.transport",
        "model_id": "test.transport/model0",
        "model_name": "Future model",
        "transport": transport,
        "supported_reasoning_efforts": None,
        "default_reasoning_effort": None,
        "reasoning_source": "unknown",
        "reasoning_control": None,
    },)


@pytest.mark.parametrize("transport", sorted(_TRANSPORTS))
@pytest.mark.parametrize("role", [WriterRole.BUILDER, WriterRole.DM])
def test_admitted_transport_has_safe_saved_session_projection(transport, role):
    session = SessionRecord(
        "projection", None if role is WriterRole.BUILDER else "campaign",
        role, "test.transport", "test.transport/model0",
    )
    validate_model_preferences(session, choices(transport), once=False)
    result = reasoning_state(session, choices(transport))
    assert result["valid"] is True
    assert result["supported"] is None
    assert result["effective_request"] is None
    assert result["native_options"] == {}
    assert result["provider_reported_effective"] is None
    assert result["current"] is None and result["once"] is None


def test_unregistered_transport_still_fails_closed():
    session = SessionRecord("projection", "campaign", WriterRole.DM,
                            "test.transport", "test.transport/model0")
    with pytest.raises(ValueError, match="Configured model transport is unsupported"):
        reasoning_state(session, choices("unregistered_protocol"))


def test_native_gemini_does_not_invent_reasoning_capabilities():
    session = SessionRecord("projection", "campaign", WriterRole.DM,
                            "test.transport", "test.transport/model0")
    explicit = replace(session, reasoning_effort="high")
    with pytest.raises(CommandRejected, match="destination model"):
        validate_model_preferences(explicit, choices("gemini_generate_content"), once=False)
    state = reasoning_state(explicit, choices("gemini_generate_content"))
    assert state["valid"] is False
    assert state["supported"] is None
    assert state["effective_request"] is None


def test_native_gemini_builder_history_survives_real_owner_reopen(tmp_path):
    database = tmp_path / "owner.sqlite3"
    session_id = None
    before = None
    for index in range(2):
        harness = _OwnerHarness(database, FakeProvider())
        try:
            harness.resolver.model_choices = lambda: choices("gemini_generate_content")
            if index == 0:
                created = harness.request("new", "create_builder_chat", {
                    "operation_id": "native-gemini-builder",
                    "provider_id": "test.transport",
                    "model_id": "test.transport/model0",
                })
                session_id = created["session"]["session_id"]
            params = {"session_id": session_id}
            context = harness.request("context", "session_context", params)
            history = harness.request("history", "session_history_page", {**params, "limit": 20})
            for state in [context["reasoning_state"], history["reasoning_state"]]:
                assert state["valid"] is True
                assert state["supported"] is None
                assert state["native_options"] == {}
            if index == 0:
                before = history
            else:
                assert history == before
        finally:
            harness.close()
