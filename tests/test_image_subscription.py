"""Subscription image protocol mapping without opening a runtime or accessing credentials."""

import base64

import pytest

from sylvia_gm.application.image_hooks import ImageHookError, ImageRequest, ImageSession
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


@pytest.mark.parametrize("account", [None, {}, {"type": "apiKey"}])
def test_signed_out_or_api_account_is_rejected_before_any_image_turn(tmp_path, account):
    connection = Connection(tmp_path, image_generation=True)

    def signed_out(method, params):
        connection.calls.append((method, params))
        return {"account": account}

    connection.call = signed_out
    adapter = SubscriptionImageAdapter(
        tmp_path, "fixture-model", connection_factory=lambda *args, **kwargs: connection
    )
    with pytest.raises(ImageHookError, match="credentials_refused"):
        adapter.submit(ImageRequest(uid(1), uid(2), "gpt.subscription", "create", 1, 1, "Draw"))
    assert connection.closed
    assert [method for method, _ in connection.calls] == ["account/read"]


@pytest.mark.parametrize(
    ("requested", "returned", "status"),
    [
        ((1024, 1024), (1254, 1254), "succeeded"),
        ((1024, 1536), (836, 1254), "succeeded"),
        ((1536, 1024), (1254, 836), "succeeded"),
        ((1024, 1536), (835, 1254), "succeeded"),
        ((1024, 1536), (900, 1254), "failed"),
        ((1024, 1024), (1254, 836), "failed"),
        ((1024, 1024), (256, 256), "failed"),
    ],
)
def test_subscription_output_keeps_requested_shape_without_requiring_exact_pixels(
    tmp_path, requested, returned, status
):
    connection = Connection(tmp_path, image_generation=True)
    events = list(connection.events)
    output = png(*returned)
    events[0]["params"]["item"]["result"] = base64.b64encode(output.data).decode()
    connection.events = iter(events)
    adapter = SubscriptionImageAdapter(
        tmp_path, "fixture-model", connection_factory=lambda *a, **k: connection
    )
    session = ImageSession(uid(1), [adapter])
    result = session.submit(
        ImageRequest(uid(1), uid(2), "gpt.subscription", "create", *requested, "Draw")
    )
    assert result.status == status
    if status == "succeeded":
        assert session.result_for_owner(uid(2)).dimensions == returned
    else:
        assert result.error == "invalid_response"
    assert [method for method, _ in connection.calls].count("turn/start") == 1
    assert connection.closed
