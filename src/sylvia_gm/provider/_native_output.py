"""Native continuation and terminal framing at the compatible HTTP boundary."""

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

import httpx

from sylvia_gm.provider._exceptions import InvalidResponseError, ProviderUnavailableError


def native_output(items: object) -> list[dict[str, Any]]:
    """Keep complete Responses output items, including opaque reasoning, in private storage."""
    result = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, Mapping):
            value = dict(item)
        elif callable(getattr(item, "model_dump", None)):
            value = item.model_dump(mode="json", exclude_none=True)
        else:
            value = vars(item)
        if not isinstance(value, dict) or not isinstance(value.get("type"), str):
            raise InvalidResponseError("Responses output item is malformed")
        result.append(deepcopy(value))
    return result


def responses_finish(response: object) -> str:
    """Only output-budget exhaustion is a length finish; failures never become success."""
    status = getattr(response, "status", None)
    if status == "completed":
        return "stop"
    if status == "incomplete":
        reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
        if reason == "max_output_tokens":
            return "length"
        raise InvalidResponseError(
            "Responses output is incomplete for a reason other than its output limit"
        )
    if status == "failed":
        raise ProviderUnavailableError("Provider returned a failed Responses result")
    raise InvalidResponseError("Responses result has no valid terminal status")


def split_chat_content(content: object) -> tuple[str, str]:
    """Separate Mistral ThinkChunks from visible text without discarding their native form."""
    if content is None:
        return "", ""
    if isinstance(content, str):
        return content, ""
    if not isinstance(content, list):
        raise InvalidResponseError("Provider content must be text or content blocks")
    visible, private = [], []
    for block in content:
        if not isinstance(block, Mapping):
            raise InvalidResponseError("Provider content block must be an object")
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            visible.append(block["text"])
        elif block.get("type") == "thinking" and isinstance(block.get("thinking"), list):
            for part in block["thinking"]:
                if isinstance(part, Mapping) and isinstance(part.get("text"), str):
                    private.append(part["text"])
    return "".join(visible), "".join(private)


class DoneTrackingStream(httpx.AsyncByteStream):
    """Observe the Chat SSE sentinel hidden by the SDK, keeping original bytes intact."""

    def __init__(self, source: httpx.AsyncByteStream) -> None:
        self.source = source
        self.done = False
        self._tail = b""

    async def __aiter__(self):
        async for data in self.source:
            self.observe(data)
            yield data

    def observe(self, data: bytes) -> None:
        combined = self._tail + data
        normalized = combined.replace(b"\r\n", b"\n")
        if b"data: [DONE]\n\n" in normalized or b"data:[DONE]\n\n" in normalized:
            self.done = True
        self._tail = combined[-32:]

    async def aclose(self) -> None:
        await self.source.aclose()
