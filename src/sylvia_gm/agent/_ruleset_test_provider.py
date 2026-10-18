"""Scripted model replies for scenarios using the ordinary campaign session adapter."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Iterable
from copy import deepcopy
from dataclasses import replace
from typing import Any, NoReturn

from sylvia_gm.agent._provider_session import _tool_alias
from sylvia_gm.agent._session_loop import ProviderEvent, SessionProvider, SessionProviderInput
from sylvia_gm.provider._base import Provider, ProviderResponse, ProviderStreamChunk


class ScenarioScriptError(ValueError):
    """An observed request does not match the saved scenario's expected continuation."""


class RulesetTestProvider(Provider):
    """Supply authored choices while leaving discovery and turn control to production code."""

    def __init__(
        self,
        replies: list[dict[str, Any]],
        *,
        decision_owner: Callable[[], str],
    ) -> None:
        super().__init__()
        self._replies = deepcopy(replies)
        self._decision_owner = decision_owner
        self._aliases: dict[str, str] = {}
        self.requests: list[dict[str, Any]] = []
        self.tool_results: list[dict[str, Any]] = []
        self.position = 0
        self.failure: str | None = None

    def observe(self, driver: SessionProvider) -> SessionProvider:
        """Observe the actual discovery result without changing its tools or admission policy."""

        def observed(provider_input: SessionProviderInput) -> Iterable[ProviderEvent]:
            def discover(discovery_id: str):
                catalog = provider_input.current_catalog(discovery_id)
                self._aliases = {_tool_alias(tool): tool.name for tool in catalog}
                return catalog

            for event in driver(replace(provider_input, catalog_supplier=discover)):
                evidence = event.evidence
                if evidence and evidence.get("kind") == "provider_tool_results":
                    for result in evidence["results"]:
                        self.tool_results.append(
                            {
                                "tool": self._aliases.get(result["name"], result["name"]),
                                "result": json.loads(result["content"]),
                            }
                        )
                yield event

        return observed

    def _fail(self, message: str) -> NoReturn:
        """Keep a concise failure even if the ordinary adapter converts the exception to a stop."""
        self.failure = message
        raise ScenarioScriptError(message)

    def assert_finished(self) -> None:
        """A normal turn ending before all saved replies are consumed is not a passing test."""
        if self.failure:
            raise ScenarioScriptError(self.failure)
        if self.position != len(self._replies):
            self._fail("The game loop finished with unused scripted replies.")

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        raise ScenarioScriptError("Scenario execution requires the ordinary streaming adapter.")

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        exposed = {
            str(tool["function"]["name"]): self._aliases.get(str(tool["function"]["name"]))
            for tool in tools or []
        }
        names = {name for name in exposed.values() if name is not None}
        owner = self._decision_owner()
        self.requests.append({"decision_owner": owner, "available_tools": sorted(names)})
        if self.position >= len(self._replies):
            self._fail("The game loop requested a reply after the saved script ended.")
        reply = self._replies[self.position]
        self.position += 1
        expected = reply.get("expect_request", {})
        if "decision_owner" in expected and expected["decision_owner"] != owner:
            self._fail("Decision owner differs from the saved expectation.")
        if not set(expected.get("available_tools", ())).issubset(names):
            self._fail("An expected operation is unavailable at this request.")
        if set(expected.get("unavailable_tools", ())) & names:
            self._fail("An operation expected to be withheld is available at this request.")

        if reply["kind"] == "tool":
            matches = [alias for alias, name in exposed.items() if name == reply["tool"]]
            if len(matches) != 1:
                self._fail("The scripted operation is not uniquely available at this request.")
            name = matches[0]
            arguments = reply["arguments"]
        else:
            # Complete narration through the exposed disposition tool when the normal loop uses it.
            disposition = [
                alias for alias, name in exposed.items() if name == "update_campaign_file"
            ]
            if not disposition:
                yield ProviderStreamChunk(content=reply["text"], finish_reason="stop")
                return
            if len(disposition) != 1:
                self._fail("The narration disposition operation is ambiguous.")
            name = disposition[0]
            arguments = {
                "state_change": "unchanged",
                "state_change_summary": "The scenario adds no durable narrative facts.",
                "revised_player_facing_response": [{"kind": "narration", "text": reply["text"]}],
            }
        yield ProviderStreamChunk(
            tool_call_delta={
                "index": 0,
                "id": f"scenario-call-{self.position}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            },
            finish_reason="tool_calls",
        )

    async def close(self) -> None:
        """No network connection or credential is used by the scripted provider."""
