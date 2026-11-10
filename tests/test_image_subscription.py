"""Subscription image protocol mapping without opening a runtime or accessing credentials."""

import base64

import pytest

from sylvia_gm.application.image_hooks import ImageHookError, ImageRequest
from sylvia_gm.provider._image_subscription import SubscriptionImageAdapter
from tests.test_image_hooks import png, uid


class Connection:
    def __init__(self, home, *, image_generation=False):
        assert image_generation
        self.workspace = home / "workspace"
        self.calls = []
        self.closed = False
        self.events = iter(
            [
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread",
                        "turnId": "turn",
                        "item": {
                            "type": "imageGeneration",
                            "status": "completed",
                            "result": base64.b64encode(png(1, 1).data).decode(),
                        },
                    },
                },
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread",
                        "turnId": "turn",
                        "turn": {"status": "completed"},
                    },
                },
            ]
        )

    def start(self):
        pass

    def close(self):
        self.closed = True

    def call(self, method, params):
        self.calls.append((method, params))
        return {
            "account/read": {"account": {"type": "chatgpt"}},
            "modelProvider/capabilities/read": {"imageGeneration": True},
            "thread/start": {"thread": {"id": "thread"}, "model": "fixture-model"},
            "turn/start": {"turn": {"id": "turn"}},
        }[method]

    def next_event(self):
        return next(self.events)


def test_subscription_has_one_isolated_turn_with_both_reference_images(tmp_path):
    connection = Connection(tmp_path, image_generation=True)
    adapter = SubscriptionImageAdapter(
        tmp_path, "fixture-model", connection_factory=lambda *a, **k: connection
    )
    request = ImageRequest(
        uid(1),
        uid(2),
        "gpt.subscription",
        "create",
        1,
        1,
        "Draw a portrait",
        reference_images=(png(1, 1, 1), png(1, 1, 2)),
    )
    result = adapter.submit(request)
    assert result.image == png(1, 1)
    methods = [method for method, _ in connection.calls]
    assert methods.count("turn/start") == 1 and connection.closed
    start = dict(connection.calls)["thread/start"]
    assert start["environments"] == [] and start["dynamicTools"] == []
    assert start["allowProviderModelFallback"] is False
    assert len(dict(connection.calls)["turn/start"]["input"]) == 3


def test_unavailable_image_capability_never_starts_a_turn_or_falls_back(tmp_path):
    connection = Connection(tmp_path, image_generation=True)
    original = connection.call
    connection.call = lambda method, params: (
        {"imageGeneration": False}
        if method == "modelProvider/capabilities/read"
        else original(method, params)
    )
    adapter = SubscriptionImageAdapter(
        tmp_path, "fixture-model", connection_factory=lambda *a, **k: connection
    )
    with pytest.raises(ImageHookError, match="unsupported_capability"):
        adapter.submit(ImageRequest(uid(1), uid(2), "gpt.subscription", "create", 1, 1, "Draw"))
    assert all(method != "turn/start" for method, _ in connection.calls)
    assert connection.closed


def test_result_path_cannot_escape_the_isolated_workspace(tmp_path):
    connection = Connection(tmp_path, image_generation=True)
    foreign = tmp_path / "auth.png"
    foreign.write_bytes(png().data)
    with pytest.raises(ImageHookError, match="invalid_response"):
        SubscriptionImageAdapter._image({"savedPath": str(foreign)}, connection)
