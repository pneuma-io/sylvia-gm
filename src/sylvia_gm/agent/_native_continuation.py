"""Selected-provider native continuation requirements, separate from inspection policy."""

from sylvia_gm.provider._base import ModelInfo


def requires_reasoning_with_tools(model: ModelInfo | None, *, has_tools: bool = True) -> bool:
    """Keep provider-required reasoning for DeepSeek, Kimi and GLM tool continuations."""
    if model is None or model.transport != "chat_completions":
        return False
    identity = model.wire_model_id.casefold().split("/")[-1]
    if not has_tools:
        return identity.startswith(("kimi-k3", "kimi-k2.7-code"))
    return identity.startswith(("deepseek", "kimi", "glm"))


def attach_chat_metadata(
    message: dict[str, object],
    metadata: object,
    *,
    required: bool = False,
) -> dict[str, object]:
    """Attach private chat extensions to the same saved tool identities only."""
    from collections.abc import Mapping
    from copy import deepcopy

    from sylvia_gm.domain.errors import CommandRejected

    if metadata is None:
        if required:
            raise CommandRejected(
                "Required provider continuation is unavailable; restore a backup or an "
                "export including Thinking.",
                code="provider_native_continuation_missing",
            )
        return message
    if not isinstance(metadata, Mapping):
        raise CommandRejected(
            "Native chat metadata is invalid", code="provider_native_continuation_mismatch"
        )
    result = dict(message)
    fields = metadata.get("reasoning_fields")
    if fields is not None:
        if (
            not isinstance(fields, Mapping)
            or not set(fields).issubset({"reasoning", "reasoning_content"})
            or any(not isinstance(value, str) for value in fields.values())
        ):
            raise CommandRejected(
                "Native reasoning fields are invalid", code="provider_native_continuation_mismatch"
            )
        result.pop("reasoning_content", None)
        result.update(fields)
    content = metadata.get("chat_content")
    if content is not None:
        if not isinstance(content, list) or any(
            not isinstance(block, Mapping) for block in content
        ):
            raise CommandRejected(
                "Native chat content is invalid", code="provider_native_continuation_mismatch"
            )
        result["content"] = deepcopy(content)
    output = metadata.get("responses_output")
    if output is not None:
        import json

        calls = message.get("tool_calls", [])
        if (
            not isinstance(output, list)
            or any(not isinstance(item, Mapping) for item in output)
            or not isinstance(calls, list)
        ):
            raise CommandRejected(
                "Native Responses metadata is invalid", code="provider_native_continuation_mismatch"
            )
        native_calls = [item for item in output if item.get("type") == "function_call"]
        try:
            matches = len(native_calls) == len(calls) and all(
                native.get("call_id") == call.get("id")
                and native.get("name") == call.get("function", {}).get("name")
                and json.loads(native.get("arguments", "{}"))
                == json.loads(call.get("function", {}).get("arguments", "{}"))
                for native, call in zip(native_calls, calls, strict=True)
            )
        except (TypeError, ValueError, AttributeError):
            matches = False
        if not matches:
            raise CommandRejected(
                "Native Responses calls do not match this exchange",
                code="provider_native_continuation_mismatch",
            )
        result["responses_output"] = deepcopy(output)
    parts = metadata.get("gemini_parts")
    if parts is not None:
        ids = metadata.get("gemini_call_ids")
        calls = message.get("tool_calls", [])
        if (
            not isinstance(parts, list)
            or any(not isinstance(part, Mapping) for part in parts)
            or not isinstance(ids, list)
            or not isinstance(calls, list)
            or ids != [call.get("id") for call in calls]
        ):
            raise CommandRejected(
                "Native Gemini metadata does not match this exchange",
                code="provider_native_continuation_mismatch",
            )
        import json

        native_calls = [part["functionCall"] for part in parts if "functionCall" in part]
        if len(native_calls) != len(calls) or any(
            native.get("name") != call.get("function", {}).get("name")
            or native.get("args", {}) != json.loads(call.get("function", {}).get("arguments", "{}"))
            for native, call in zip(native_calls, calls, strict=True)
        ):
            raise CommandRejected(
                "Native Gemini arguments do not match this exchange",
                code="provider_native_continuation_mismatch",
            )
        result["gemini_parts"] = deepcopy(parts)
    details = metadata.get("reasoning_details")
    if details is not None:
        if not isinstance(details, list):
            raise CommandRejected(
                "Native reasoning details are invalid", code="provider_native_continuation_mismatch"
            )
        result["reasoning_details"] = deepcopy(details)
    extras = metadata.get("tool_call_extras")
    if extras is not None:
        calls = message.get("tool_calls", [])
        if not isinstance(extras, Mapping) or not isinstance(calls, list):
            raise CommandRejected(
                "Native tool metadata is invalid", code="provider_native_continuation_mismatch"
            )
        ids = {call.get("id") for call in calls if isinstance(call, Mapping)}
        if not set(extras).issubset(ids):
            raise CommandRejected(
                "Native tool metadata does not match this exchange",
                code="provider_native_continuation_mismatch",
            )
        result["tool_calls"] = [
            {
                **call,
                **(
                    {"extra_content": deepcopy(extras[call["id"]])}
                    if call.get("id") in extras
                    else {}
                ),
            }
            for call in calls
        ]
    return result
