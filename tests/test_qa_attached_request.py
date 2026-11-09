"""QA attachments use the same role-specific lease identity as the renderer."""

from queue import Queue

import pytest

from tests.qa_attached_request import request_attached


class AttachmentHarness:
    def __init__(self, provider_work, initial_registration_race):
        self.provider_work = provider_work
        self.initial_registration_race = initial_registration_race
        self._pending_replies = {}
        self.replies = Queue()
        self.heartbeats = []

    def send(self, identity, payload):
        if identity == "work":
            return
        self.heartbeats.append(payload)
        expected_method = "provider_stream_heartbeat" if self.provider_work else "stream_heartbeat"
        expected_owner = "builder-child-session" if self.provider_work else "campaign"
        assert payload["method"] == expected_method
        assert payload["params"]["campaign_id"] == expected_owner
        assert payload["params"]["attempt_id"] == "attempt"
        if len(self.heartbeats) == 1 and self.initial_registration_race:
            message = (
                "Provider stream attachment is unavailable"
                if self.provider_work else "Narration attachment is unavailable"
            )
            self.replies.put({"id": identity, "ok": False, "error": {"message": message}})
        else:
            if len(self.heartbeats) > 1 and not self.initial_registration_race:
                assert payload["params"]["attachment_generation"] == 7
            self.replies.put({"id": identity, "ok": True, "result": {
                "attachment_deadline": 100, "attachment_generation": 7,
            }})
        if len(self.heartbeats) == 2:
            self.replies.put({"id": "work", "ok": True, "result": {"completed": True}})

    def _read_stream(self):
        return self.replies.get(timeout=5)


@pytest.mark.parametrize("provider_work", [False, True])
@pytest.mark.parametrize("initial_registration_race", [False, True])
def test_role_specific_attachment_keeps_generation_and_handles_registration_race(
    provider_work, initial_registration_race,
):
    harness = AttachmentHarness(provider_work, initial_registration_race)
    options = {"provider_session_id": "builder-child-session"} if provider_work else {}
    result = request_attached(
        harness, "work", "submit_session_input", {},
        campaign_id="campaign", attempt_id="attempt", **options,
    )
    assert result == {"completed": True}
    assert len(harness.heartbeats) == 2
