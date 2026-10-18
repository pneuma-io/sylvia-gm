"""Native Gemini GenerateContent requests, including private signed continuations.

Protocol: https://ai.google.dev/api/generate-content (checked 2026-10-14).
Requests are sent once. Session recovery owns retries and committed tool mutations.
"""

from __future__ import annotations

import json
from copy import deepcopy
from urllib.parse import quote
from uuid import uuid4

import httpx

from sylvia_gm.provider._base import (
    ModelInfo,
    Provider,
    ProviderResponse,
    ProviderStreamChunk,
    TokenUsage,
    ToolCall,
)
from sylvia_gm.provider._credentials import CredentialPool
from sylvia_gm.provider._exceptions import (
    AuthenticationError,
    IncompleteProviderStreamError,
    InvalidRequestError,
    InvalidResponseError,
    ModelNotFoundError,
    ProviderConnectionError,
    ProviderContinuationRequiredError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
    TokenLimitError,
)
from sylvia_gm.provider._hooks import HookContext


class GeminiProvider(Provider):
    """GenerateContent client shared by direct Gemini and compatible gateways."""

    def __init__(
        self,
        *,
        base_url: str,
        model_info: ModelInfo,
        credential_pool: CredentialPool,
        http_client: httpx.AsyncClient | None = None,
        default_headers=None,
    ):
        super().__init__()
        self.model_info = model_info
        self._base_url = base_url.rstrip("/")
        self._credentials = credential_pool
        self._client = http_client or httpx.AsyncClient(follow_redirects=False)
        self._headers = dict(default_headers or {})

    def provider_request_representation(self, messages, tools, options):
        contents = []
        system = []
        calls = {}
        for message in messages:
            role = message.get("role")
            if role in {"system", "developer"}:
                system.append({"text": str(message.get("content") or "")})
                continue
            if role == "tool":
                identity = message.get("tool_call_id")
                if identity not in calls:
                    raise InvalidRequestError("Gemini tool result has no matching function call.")
                name, native_id = calls[identity]
                try:
                    result = json.loads(message.get("content") or "{}")
                except (ValueError, TypeError):
                    result = {"result": str(message.get("content") or "")}
                if not isinstance(result, dict):
                    result = {"result": result}
                response = {"name": name, "response": result}
                if native_id:
                    response["id"] = native_id
                parts = [{"functionResponse": response}]
            else:
                parts = deepcopy(message.get("gemini_parts"))
                neutral = message.get("tool_calls") or []
                if parts is None:
                    parts = [{"text": str(message["content"])}] if message.get("content") else []
                    for call in neutral:
                        fn = call["function"]
                        args = fn.get("arguments", "{}")
                        args = json.loads(args) if isinstance(args, str) else args
                        parts.append(
                            {"functionCall": {"id": call["id"], "name": fn["name"], "args": args}}
                        )
                native_calls = [part["functionCall"] for part in parts if "functionCall" in part]
                for call, native in zip(neutral, native_calls, strict=True):
                    calls[call["id"]] = (native["name"], native.get("id"))
            wire_role = "model" if role == "assistant" else "user"
            if not parts:
                continue
            if contents and contents[-1]["role"] == wire_role:
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": wire_role, "parts": parts})
        request = {"contents": contents}
        if system:
            request["systemInstruction"] = {"parts": system}
        if tools:
            request["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": tool["function"]["name"],
                            "description": tool["function"].get("description", ""),
                            "parametersJsonSchema": deepcopy(
                                tool["function"].get("parameters", {"type": "object"})
                            ),
                        }
                        for tool in tools
                    ]
                }
            ]
            choice = options.get("tool_choice")
            if isinstance(choice, str) and choice in {"required", "none", "auto"}:
                request["toolConfig"] = {
                    "functionCallingConfig": {
                        "mode": {"required": "ANY", "none": "NONE", "auto": "AUTO"}[choice]
                    }
                }
            elif isinstance(choice, dict):
                name = choice.get("function", {}).get("name")
                if name:
                    request["toolConfig"] = {
                        "functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [name]}
                    }
        config = {}
        for source, target in {
            "max_tokens": "maxOutputTokens",
            "max_output_tokens": "maxOutputTokens",
            "temperature": "temperature",
            "top_p": "topP",
            "top_k": "topK",
            "stop": "stopSequences",
        }.items():
            if options.get(source) is not None:
                config[target] = options[source]
        if config:
            request["generationConfig"] = config
        return request

    async def _request(self, messages, tools, stream, timeout, options):
        credential = await self._credentials.get_credential()
        headers = dict(self._headers)
        headers.update(options.get("extra_headers") or {})
        from sylvia_gm.provider._protocol_routes import is_opencode_inference

        if is_opencode_inference(self._base_url):
            headers = {
                key: value for key, value in headers.items() if key.lower() != "x-goog-api-key"
            }
            headers["Authorization"] = f"Bearer {credential.value}"
        else:
            headers.setdefault("x-goog-api-key", credential.value)
        headers.setdefault("Content-Type", "application/json")
        method = "streamGenerateContent" if stream else "generateContent"
        model = quote(self.model_info.wire_model_id.removeprefix("models/"), safe="")
        endpoint = f"{self._base_url}/models/{model}:{method}"
        request = self.provider_request_representation(messages, tools, options)
        await self.hooks.fire_on_request(
            HookContext(
                provider_key=self.model_info.provider_key,
                model_id=self.model_info.id,
                stream=stream,
                request_json=request,
            )
        )
        return endpoint, headers, request

    @staticmethod
    async def _check(response):
        if response.is_success:
            return
        status = response.status_code
        if status in {401, 403}:
            raise AuthenticationError("Gemini rejected the supplied credential.")
        if status == 429:
            try:
                retry = float(response.headers.get("retry-after", ""))
            except ValueError:
                retry = None
            raise RateLimitError("Gemini rate or quota limit reached.", retry_after=retry)
        if status == 404:
            raise ModelNotFoundError("Gemini model or endpoint was not found.")
        if status >= 500:
            raise ProviderUnavailableError(f"Gemini unavailable (HTTP {status}).")
        body = (await response.aread()).decode("utf-8", errors="replace").casefold()
        if status in {400, 413} and any(
            word in body for word in ("context", "token limit", "input token", "too long")
        ):
            raise TokenLimitError.provider_rejection()
        raise InvalidRequestError(f"Gemini rejected the request (HTTP {status}).")

    @staticmethod
    def _parse(document, offset=0):
        candidates = document.get("candidates") if isinstance(document, dict) else None
        if not isinstance(candidates, list) or not candidates:
            raise InvalidResponseError("Gemini returned no usable candidate.")
        candidate = candidates[0]
        parts = candidate.get("content", {}).get("parts", [])
        if not isinstance(parts, list) or any(not isinstance(part, dict) for part in parts):
            raise InvalidResponseError("Gemini returned invalid content parts.")
        text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
        reasoning = "".join(part.get("text", "") for part in parts if part.get("thought"))
        calls = []
        for part in parts:
            fn = part.get("functionCall")
            if fn is not None:
                if (
                    not isinstance(fn, dict)
                    or not isinstance(fn.get("name"), str)
                    or not isinstance(fn.get("args", {}), dict)
                ):
                    raise InvalidResponseError("Gemini returned invalid function arguments.")
                calls.append(
                    ToolCall(
                        id=fn.get("id") or f"gemini-{uuid4()}",
                        name=fn["name"],
                        arguments=json.dumps(fn.get("args", {})),
                    )
                )
        reason = candidate.get("finishReason")
        if reason == "CONTINUATION":
            token = candidate.get("continuationToken")
            if not isinstance(token, str) or not token:
                raise InvalidResponseError("Gemini continuation is missing its required token.")
            from copy import deepcopy

            raise ProviderContinuationRequiredError(
                "Gemini reached its per-request limit and requires a token continuation with "
                "the identical original request. Automatic native continuation is not supported; "
                "saved work is preserved. Choose a smaller step or another model.",
                continuation_token=token,
                native_parts=deepcopy(parts),
            )
        if reason and reason not in {"STOP", "MAX_TOKENS"}:
            raise InvalidResponseError(f"Gemini did not complete a usable response ({reason}).")
        return (
            text,
            reasoning,
            calls,
            parts,
            ("length" if reason == "MAX_TOKENS" else "tool_calls" if calls else "stop")
            if reason
            else None,
        )

    async def generate(self, messages, tools=None, stream=False, timeout=60.0, **options):
        if stream:
            chunks = [
                chunk async for chunk in self.generate_stream(messages, tools, timeout, **options)
            ]
            calls = [
                ToolCall(
                    id=chunk.tool_call_delta["id"],
                    name=chunk.tool_call_delta["function"]["name"],
                    arguments=chunk.tool_call_delta["function"]["arguments"],
                )
                for chunk in chunks
                if chunk.tool_call_delta
            ]
            return ProviderResponse(
                content="".join(chunk.content or "" for chunk in chunks),
                tool_calls=calls,
                reasoning_content="".join(chunk.reasoning_content or "" for chunk in chunks),
                finish_reason=chunks[-1].finish_reason,
                model=self.model_info.wire_model_id,
                chat_completion_metadata=chunks[-1].chat_completion_metadata,
            )
        endpoint, headers, request = await self._request(messages, tools, False, timeout, options)
        try:
            response = await self._client.post(
                endpoint, headers=headers, json=request, timeout=timeout
            )
            await self._check(response)
            document = response.json()
            text, reasoning, calls, parts, finish = self._parse(document)
            if finish is None:
                raise InvalidResponseError("Gemini response has no completion status.")
            result = ProviderResponse(
                content=text,
                reasoning_content=reasoning,
                tool_calls=calls,
                model=self.model_info.wire_model_id,
                finish_reason=finish,
                usage=TokenUsage(
                    prompt_tokens=document.get("usageMetadata", {}).get("promptTokenCount", 0),
                    completion_tokens=document.get("usageMetadata", {}).get(
                        "candidatesTokenCount", 0
                    ),
                ),
                chat_completion_metadata={
                    "gemini_parts": parts,
                    "gemini_call_ids": [call.id for call in calls],
                },
            )
            await self.hooks.fire_on_response(
                HookContext(
                    provider_key=self.model_info.provider_key,
                    model_id=self.model_info.id,
                    response=result,
                    http_status=response.status_code,
                )
            )
            return result
        except httpx.TimeoutException as error:
            raise ProviderTimeoutError("Gemini request timed out.") from error
        except httpx.RequestError as error:
            raise ProviderConnectionError("Gemini connection failed.") from error
        except (ValueError, TypeError, KeyError) as error:
            raise InvalidResponseError("Gemini returned malformed response data.") from error

    async def generate_stream(self, messages, tools=None, timeout=120.0, **options):
        endpoint, headers, request = await self._request(messages, tools, True, timeout, options)
        parts = []
        calls = []
        finish = None
        text_parts = []
        reasoning_parts = []
        usage = {}
        try:
            async with self._client.stream(
                "POST",
                endpoint,
                params={"alt": "sse"},
                headers=headers,
                json=request,
                timeout=timeout,
            ) as response:
                await self._check(response)
                data = []
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        data.append(line[5:].strip())
                    elif not line and data:
                        document = json.loads("\n".join(data))
                        data.clear()
                        text, reasoning, new_calls, new_parts, reason = self._parse(document)
                        parts.extend(new_parts)
                        text_parts.append(text)
                        reasoning_parts.append(reasoning)
                        usage = document.get("usageMetadata", usage)
                        if text or reasoning:
                            yield ProviderStreamChunk(
                                content=text or None, reasoning_content=reasoning or None
                            )
                        for call in new_calls:
                            index = len(calls)
                            calls.append(call)
                            yield ProviderStreamChunk(
                                tool_call_delta={
                                    "index": index,
                                    "id": call.id,
                                    "type": "function",
                                    "function": {"name": call.name, "arguments": call.arguments},
                                }
                            )
                        if reason:
                            finish = (
                                "length"
                                if reason == "length"
                                else "tool_calls"
                                if calls
                                else "stop"
                            )
                if data or finish is None:
                    raise IncompleteProviderStreamError(
                        "Gemini stream ended before a complete terminal event."
                    )
                result = ProviderResponse(
                    content="".join(text_parts),
                    reasoning_content="".join(reasoning_parts),
                    tool_calls=calls,
                    finish_reason=finish,
                    model=self.model_info.wire_model_id,
                    usage=TokenUsage(
                        prompt_tokens=usage.get("promptTokenCount", 0),
                        completion_tokens=usage.get("candidatesTokenCount", 0),
                    ),
                    chat_completion_metadata={
                        "gemini_parts": parts,
                        "gemini_call_ids": [call.id for call in calls],
                    },
                )
                await self.hooks.fire_on_response(
                    HookContext(
                        provider_key=self.model_info.provider_key,
                        model_id=self.model_info.id,
                        stream=True,
                        response=result,
                        http_status=response.status_code,
                    )
                )
                yield ProviderStreamChunk(
                    finish_reason=finish,
                    chat_completion_metadata={
                        "gemini_parts": parts,
                        "gemini_call_ids": [call.id for call in calls],
                    },
                )
        except httpx.TimeoutException as error:
            raise ProviderTimeoutError("Gemini stream timed out.") from error
        except httpx.RequestError as error:
            raise ProviderConnectionError("Gemini stream connection failed.") from error
        except (ValueError, TypeError, KeyError) as error:
            raise InvalidResponseError("Gemini returned malformed stream data.") from error

    async def close(self):
        await self._client.aclose()
