"""Collect opaque Chat Completions continuation metadata outside public history."""

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from sylvia_gm.provider._exceptions import InvalidResponseError
from sylvia_gm.provider._tool_delta import _delta_value


class ChatCompletionMetadata:
    """Reassemble reasoning blocks and tool signatures without interpreting their contents."""

    def __init__(self, *, preserve_reasoning: bool = False, preserve_content: bool = False) -> None:
        self._preserve_reasoning = preserve_reasoning
        self._preserve_content = preserve_content
        self._reasoning: dict[str, str] = {}
        self._content: list[dict[str, Any]] = []
        self._details: dict[object, dict[str, Any]] = {}
        self._calls: dict[int, dict[str, Any]] = {}

    def add(self, message: object) -> None:
        """Accept one SDK message or delta, retaining block and tool identities."""
        if self._preserve_reasoning:
            for field in ("reasoning", "reasoning_content"):
                value = _delta_value(message, field, None)
                if value is not None:
                    if not isinstance(value, str):
                        raise InvalidResponseError("Provider reasoning channel must be text")
                    self._reasoning[field] = self._reasoning.get(field, "") + value
        if self._preserve_content:
            content = _delta_value(message, "content", None)
            if isinstance(content, list):
                self._content.extend(deepcopy(content))
            elif isinstance(content, str) and content:
                self._content.append({"type": "text", "text": content})
        for position, detail in enumerate(_delta_value(message, "reasoning_details", None) or []):
            if not isinstance(detail, Mapping):
                raise InvalidResponseError("Provider reasoning detail must be an object")
            key = detail.get("index")
            if key is None:
                key = detail.get("id") or position
            if not isinstance(key, (str, int)):
                raise InvalidResponseError("Provider reasoning detail identity is invalid")
            existing = self._details.setdefault(key, {})
            for field, value in detail.items():
                if value is None:
                    existing.setdefault(field, None)
                elif field in {"text", "summary", "data", "signature"}:
                    if not isinstance(value, str):
                        raise InvalidResponseError("Provider reasoning fragment must be text")
                    existing[field] = (existing.get(field) or "") + value
                elif field not in existing or existing[field] is None:
                    existing[field] = deepcopy(value)
                elif existing[field] != value:
                    raise InvalidResponseError("Provider reasoning detail identity changed")

        for position, call in enumerate(_delta_value(message, "tool_calls", None) or []):
            index = _delta_value(call, "index", position)
            if not isinstance(index, int) or index < 0:
                raise InvalidResponseError("Provider tool metadata index is invalid")
            current = self._calls.setdefault(index, {"id": ""})
            call_id = _delta_value(call, "id")
            if isinstance(call_id, str) and call_id:
                if current["id"] and current["id"] != call_id:
                    raise InvalidResponseError("Provider tool metadata identity changed")
                current["id"] = call_id
            extra = _delta_value(call, "extra_content")
            if extra is not None:
                if not isinstance(extra, Mapping):
                    raise InvalidResponseError("Provider tool signature metadata is invalid")
                if "extra" in current and current["extra"] != extra:
                    raise InvalidResponseError("Provider tool signature metadata changed")
                current["extra"] = deepcopy(dict(extra))

    def payload(self) -> dict[str, Any] | None:
        """Return only private extensions; canonical arguments remain with their ordinary calls."""
        result: dict[str, Any] = {}
        if self._reasoning:
            result["reasoning_fields"] = deepcopy(self._reasoning)
        if self._content and any(block.get("type") == "thinking" for block in self._content):
            result["chat_content"] = deepcopy(self._content)
        if self._details:
            result["reasoning_details"] = deepcopy(list(self._details.values()))
        extras = {}
        for call in self._calls.values():
            if "extra" in call:
                if not call["id"]:
                    raise InvalidResponseError("Provider tool signature has no call identity")
                extras[call["id"]] = deepcopy(call["extra"])
        if extras:
            result["tool_call_extras"] = extras
        return result or None
