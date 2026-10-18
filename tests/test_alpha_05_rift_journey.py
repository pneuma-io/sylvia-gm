"""The Rift Awakening over the desktop composition and MCP authority paths."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from sylvia_gm.agent._provider_session import ProviderSessionAdapter
from sylvia_gm.agent._session_loop import (
    CampaignSessionLoop,
    ProviderControl,
    SessionInputKind,
    SessionProviderInput,
    SessionRunRequest,
)
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.application.bundled_packages import (
    bundled_rift_archive,
    bundled_rift_campaign_plan,
)
from sylvia_gm.application.reusable_plans import save_reusable_rift_plan
from sylvia_gm.campaign.rift import materialize_rift_selection, rift_public_template
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.session import SessionMessageKind
from sylvia_gm.infrastructure.launch_handshake import derive_owner_response
from sylvia_gm.interfaces.local_api import LocalCommandBus
from sylvia_gm.interfaces.local_owner import LocalOwnerSupervisor
from sylvia_gm.interfaces.owner_process import _dispatch
from sylvia_gm.provider._admin_audit import AdminAuditedProvider
from sylvia_gm.provider._base import Provider, ProviderResponse, ProviderStreamChunk
from sylvia_gm.provider._exceptions import ProviderConnectionError


class _RiftIntentProvider(Provider):
    """Prove the model-facing prompt/tool/result composition without bypassing the adapter."""

    def __init__(self, player_actor_id: str) -> None:
        super().__init__()
        self.player_actor_id = player_actor_id
        self.current_actor_id = player_actor_id
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        raise NotImplementedError

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        self.calls.append((messages.copy(), tools))
        required_ruleset_decision = any(
            str(item["function"]["name"]).endswith(
                (
                    "_start_combat_encounter_entities",
                    "_resolve_adjudicated_creature_attack",
                    "_resolve_combat_attack",
                )
            )
            for item in tools or []
        )
        if kwargs.get("tool_choice") == "required" and not required_ruleset_decision:
            update = next(
                item
                for item in tools or []
                if str(item["function"]["name"]).endswith("_update_campaign_file")
            )
            arguments: dict[str, object] = {
                "state_change": "unchanged",
                "state_change_summary": "The response adds no durable campaign truth.",
            }
            required = update["function"]["parameters"].get("required", [])
            if "revised_player_facing_response" in required:
                arguments["revised_player_facing_response"] = next(
                    (
                        str(message["content"])
                        for message in reversed(messages)
                        if message.get("role") == "assistant"
                        and isinstance(message.get("content"), str)
                        and str(message["content"]).strip()
                    ),
                    "The flame catches the hound across its scarred flank. What do you do?",
                )
            yield ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": f"call-rift-file-unchanged-{len(self.calls)}",
                    "function": {
                        "name": update["function"]["name"],
                        "arguments": json.dumps(arguments),
                    },
                },
                finish_reason="tool_calls",
            )
            return
        latest_non_system = next(
            (message for message in reversed(messages) if message.get("role") != "system"),
            {},
        )
        if latest_non_system.get("role") == "tool" and "campaign_file_unchanged" in str(
            latest_non_system.get("content", "")
        ):
            yield ProviderStreamChunk(
                content="The flame catches the hound across its scarred flank. What do you do?",
                finish_reason="stop",
            )
            return
        start = next(
            (
                item
                for item in tools or []
                if str(item["function"]["name"]).endswith(
                    "_start_combat_encounter_entities"
                )
            ),
            None,
        )
        if start is not None:
            rendered_messages = "\n".join(str(item.get("content", "")) for item in messages)
            assert "Verdant Margin" in rendered_messages
            assert "Adventurer" in rendered_messages
            assert "Riftwolf" in rendered_messages
            yield ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "call-rift-start-encounter",
                    "function": {
                        "name": start["function"]["name"],
                        "arguments": (
                            f'{{"first_actor":"{self.player_actor_id}",'
                            '"second_actor":"rift-opening-riftwolf",'
                            '"first_surprised":false,"second_surprised":false,'
                            '"tie_winner":"first"}'
                        ),
                    },
                },
                finish_reason="tool_calls",
            )
            return
        creature_attack = next(
            (
                item
                for item in tools or []
                if str(item["function"]["name"]).endswith(
                    "_resolve_adjudicated_creature_attack"
                )
            ),
            None,
        )
        if creature_attack is not None:
            self.current_actor_id = "rift-opening-riftwolf"
            yield ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": f"call-riftwolf-bite-{len(self.calls)}",
                    "function": {
                        "name": creature_attack["function"]["name"],
                        "arguments": (
                            f'{{"target":"{self.player_actor_id}",'
                            '"attack_name":"Riftwolf bite","attack_modifier":3,'
                            '"damage_expression":"d6","damage_modifier":1,'
                            '"damage_type":"piercing","advantage":false,'
                            '"disadvantage":false}'
                        ),
                    },
                },
                finish_reason="tool_calls",
            )
            return
        player_attack = next(
            (
                item
                for item in tools or []
                if str(item["function"]["name"]).endswith("_resolve_combat_attack")
            ),
            None,
        )
        if player_attack is not None:
            self.current_actor_id = self.player_actor_id
            yield ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": f"call-rift-longsword-attack-{len(self.calls)}",
                    "function": {
                        "name": player_attack["function"]["name"],
                        "arguments": (
                            f'{{"actor":"{self.player_actor_id}",'
                            '"target":"rift-opening-riftwolf","attack_sequence":"primary",'
                            '"use_graze_on_miss":false,"weapon":"longsword",'
                            '"attack_mode":"melee","attack_ability":"strength",'
                            '"wielding_two_handed":false,"mounted":false,'
                            '"circumstantial_modifier":0,"advantage":false,'
                            '"disadvantage":false,"actor_can_see_target":true,'
                            '"target_can_see_actor":true,"target_is_charmer":false,'
                            '"fear_source_in_sight":false,"attack_distance":5}'
                        ),
                    },
                },
                finish_reason="tool_calls",
            )
            return
        assert any(message["role"] == "tool" for message in messages)
        yield ProviderStreamChunk(
            content="The flame catches the hound across its scarred flank. What do you do?",
            finish_reason="stop",
        )

    async def close(self) -> None:
        return None


class _RiftOpeningProvider(Provider):
    """Query context, save a natural opening scene, then stop for the player."""

    def __init__(self, player_actor_id: str) -> None:
        super().__init__()
        self.player_actor_id = player_actor_id
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        raise NotImplementedError

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        self.calls.append((messages.copy(), tools))
        if len(self.calls) == 1:
            read_reference = next(
                item
                for item in tools or []
                if str(item["function"]["name"]).endswith("_read_campaign_reference")
            )
            yield ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "call-read-stern-reference",
                    "function": {
                        "name": read_reference["function"]["name"],
                        "arguments": (
                            '{"source":"campaign_setting",'
                            '"path":"npcs/patrol-veteran-stern.md"}'
                        ),
                    },
                },
                finish_reason="tool_calls",
            )
            return
        if len(self.calls) == 2:
            assert any(
                message.get("role") == "tool"
                and "lowest casualty rate and the highest artifact recovery rate"
                in str(message.get("content", ""))
                for message in messages
            )
            update = next(
                item
                for item in tools or []
                if str(item["function"]["name"]).endswith("_update_campaign_file")
            )
            narration = (
                "The Verdant Margin closes softly behind you. Ahead, a path divides around an "
                "old stone pillar: one branch opens toward lighter undergrowth while the other "
                "descends beneath a violet-shadowed canopy. What do you do?"
            )
            yield ProviderStreamChunk(content=narration)
            yield ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "call-rift-opening-file-update",
                    "function": {
                        "name": update["function"]["name"],
                        "arguments": json.dumps(
                            {
                                "state_change": "changed",
                                "state_change_summary": (
                                    "The opening establishes the current fork scene."
                                ),
                                "scenes": [
                                    {
                                        "act_id": "act-1",
                                        "location_id": "verdant-margin",
                                        "summary": (
                                            "The Adventurer stands at a fork around an old stone "
                                            "pillar in the Verdant Margin; the route behind is "
                                            "closing and no encounter has begun."
                                        ),
                                    }
                                ],
                                "plan_progress": [
                                    {
                                        "plan_item_id": "act-1",
                                        "progress_status": "introduced",
                                        "progress_summary": (
                                            "The Verdant Margin fork is available; the player "
                                            "response is unresolved."
                                        ),
                                    }
                                ],
                                "revised_player_facing_response": narration,
                            }
                        ),
                    },
                },
                finish_reason="tool_calls",
            )
            return
        raise AssertionError("Opening should finish after the held draft is committed")

    async def close(self) -> None:
        return None


class _RevealedSecretProvider(Provider):
    """Use a secret only after application context marks it player-known."""

    def __init__(self, expected_fact: str) -> None:
        super().__init__()
        self.expected_fact = expected_fact
        self.calls: list[list[dict[str, Any]]] = []
        self.request_surfaces: list[dict[str, object]] = []

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        raise NotImplementedError

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        self.calls.append(messages.copy())
        self.request_surfaces.append(
            {
                "tools": [
                    str(item["function"]["name"])
                    for item in tools or []
                ],
                "options": dict(kwargs),
            }
        )
        disposition_required = str(messages[-1].get("content", "")).startswith(
            "SYLVIA_GM_CAMPAIGN_FILE_DISPOSITION_REQUIRED"
        )
        if kwargs.get("tool_choice") == "required" and disposition_required:
            update = next(
                item
                for item in tools or []
                if str(item["function"]["name"]).endswith("_update_campaign_file")
            )
            held_response = next(
                (
                    str(message["content"])
                    for message in reversed(messages)
                    if message.get("role") == "assistant"
                    and isinstance(message.get("content"), str)
                    and str(message["content"]).strip()
                ),
                None,
            )
            if held_response is not None:
                yield ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": f"call-secret-file-unchanged-{len(self.calls)}",
                        "function": {
                            "name": update["function"]["name"],
                            "arguments": json.dumps(
                                {
                                    "state_change": "unchanged",
                                    "state_change_summary": (
                                        "The response repeats an already revealed truth."
                                    ),
                                    "revised_player_facing_response": held_response,
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
                return
        campaign_preparation = next(
            str(message["content"])
            for message in messages
            if str(message.get("content", "")).startswith(
                "SYLVIA_GM_CAMPAIGN_PREPARATION"
            )
        )
        fresh_authority = next(
            str(message["content"])
            for message in messages
            if str(message.get("content", "")).startswith(
                "SYLVIA_GM_FRESH_CAMPAIGN_STATE"
            )
        )
        preparation = json.loads(campaign_preparation.split("\n", 1)[1])
        data = json.loads(fresh_authority.split("\n", 1)[1])
        assert self.expected_fact in {
            item["fact"] for item in data["PLAYER_KNOWN"]["revealed_facts"]
        }
        assert "rift-is-one-way" in data["PLAYER_KNOWN"]["revealed_secret_ids"]
        memory_texts = {
            item["text"] for item in data["PLAYER_KNOWN"]["current_facts"]
        }
        assert memory_texts >= {
            "The party agreed to meet V privately beneath Waystation.",
            "V trusted Mara with the truth that the Rift is one-way.",
        }
        anchor_fact = (
            "The Anchor Doors are life support for the Severed Lands rather than portals home."
        )
        assert anchor_fact not in {
            item["fact"] for item in data["PLAYER_KNOWN"]["revealed_facts"]
        }
        rendered = json.dumps(messages, ensure_ascii=False)
        assert preparation["private_preparation"][
            "additional_material_available_via"
        ] == {"source": "campaign_plan", "path": "structured"}
        assert anchor_fact not in rendered
        assert "old-history-0" not in rendered
        assert "old-history-49" in rendered
        narration = f"V has told you the truth: {self.expected_fact}"
        if kwargs.get("tool_choice") == "required":
            update = next(
                item
                for item in tools or []
                if str(item["function"]["name"]).endswith("_update_campaign_file")
            )
            yield ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": f"call-secret-file-unchanged-{len(self.calls)}",
                    "function": {
                        "name": update["function"]["name"],
                        "arguments": json.dumps(
                            {
                                "state_change": "unchanged",
                                "state_change_summary": (
                                    "The response repeats an already revealed truth."
                                ),
                                "revised_player_facing_response": narration,
                            }
                        ),
                    },
                },
                finish_reason="tool_calls",
            )
            return
        yield ProviderStreamChunk(content=narration, finish_reason="stop")

    async def close(self) -> None:
        return None


def _construct_rift(
    bus: LocalCommandBus,
    owner: AuthorityOwner,
    storage_root: Path,
    *,
    lane: str,
    companion_ids: tuple[str, ...],
    plan_id: str = "the-rift-awakening",
    plan_title: str = "The Rift Awakening",
) -> tuple[str, str]:
    scenario_id = lane if plan_id == "the-rift-awakening" else f"{lane}-reusable"
    draft = _dispatch(
        bus,
        "create_guided_campaign_draft",
        {
            "operation_id": f"create-rift-{scenario_id}",
            "display_name": f"The Rift Awakening - {scenario_id}",
            "provider_id": "scripted-builder",
            "model_id": "scripted-builder-model",
        },
        application_owner=owner,
        owner_id="owner-rift",
        provider_resolver=None,  # type: ignore[arg-type]
        model_choices=lambda: (),
        session_loop=CampaignSessionLoop(owner),
        storage_root=storage_root,
    )
    campaign_id = str(draft["campaign_id"])
    builder_session_id = str(draft["builder_session_id"])
    construction_revision = int(draft["construction_revision"])
    customization_notes = (
        "Make Captain Orin a reluctant ally and move the observatory reveal into Act II."
        if lane == "customize"
        else ""
    )
    answers = [
        ("ruleset", "D&D 5e SRD 5.2.1", {"candidate_id": "sylvia.srd-lite.bg3"}),
        ("setting", "Severed Lands", {"setting_id": "pneuma.severed-lands"}),
        ("plan", plan_title, {"plan_id": plan_id}),
    ]
    answers.extend(
        [
            (
                "name",
                f"The Rift Awakening - {scenario_id}",
                {"display_name": f"The Rift Awakening - {scenario_id}"},
            ),
            (
                "participants",
                f"Mara; {len(companion_ids)} companions",
                {
                    "archetype_id": "mara",
                    "companion_ids": list(companion_ids),
                },
            ),
            (
                "boundaries_preferences",
                "Boundaries: None; Preferences: None",
                {"boundaries": "", "preferences": ""},
            ),
            (
                "specific_requests",
                customization_notes or "No additional requests",
                {
                    "no_additional_requests": not bool(customization_notes),
                    "requests": customization_notes,
                },
            ),
        ]
    )
    for step, visible_answer, answer in answers:
        advanced = _dispatch(
            bus,
            "advance_guided_campaign_draft",
            {
                "operation_id": f"answer-rift-{scenario_id}-{step}",
                "campaign_id": campaign_id,
                "builder_session_id": builder_session_id,
                "expected_construction_revision": construction_revision,
                "current_step": step,
                "visible_answer": visible_answer,
                "answer": answer,
            },
            application_owner=owner,
            owner_id="owner-rift",
            provider_resolver=None,  # type: ignore[arg-type]
            model_choices=lambda: (),
            session_loop=CampaignSessionLoop(owner),
            storage_root=storage_root,
        )
        construction_revision = int(advanced["construction_revision"])
    started = _dispatch(
        bus,
        "start_rift_campaign",
        {
            "operation_id": f"start-rift-{scenario_id}",
            "campaign_id": campaign_id,
            "builder_session_id": builder_session_id,
            "ruleset_selection": "sylvia.srd-lite.bg3",
            "setting_id": "pneuma.severed-lands",
            "plan_id": plan_id,
            "display_name": f"The Rift Awakening - {scenario_id}",
            "lane": lane,
            "player_name": "Mara Venn",
            "archetype_id": "open-adventurer",
            "companion_ids": list(companion_ids),
            "customization_notes": customization_notes,
            "provider_id": "scripted-builder",
            "model_id": "scripted-builder-model",
            "construction_intent": {
                "boundaries": "",
                "preferences": "",
                "specific_requests": customization_notes,
                "contributions": {},
            },
        },
        application_owner=owner,
        owner_id="owner-rift",
        provider_resolver=None,  # type: ignore[arg-type]
        model_choices=lambda: (),
        session_loop=CampaignSessionLoop(owner),
        storage_root=storage_root,
    )
    assert started["resulting_revision"] == 1
    assert started["campaign_id"] == campaign_id
    resumed_start = _dispatch(
        bus,
        "start_rift_campaign",
        {
            "operation_id": f"resume-start-rift-{scenario_id}",
            "campaign_id": campaign_id,
            "builder_session_id": builder_session_id,
            "ruleset_selection": "sylvia.srd-lite.bg3",
            "setting_id": "pneuma.severed-lands",
            "plan_id": plan_id,
            "display_name": f"The Rift Awakening - {scenario_id}",
            "lane": lane,
            "archetype_id": "mara",
            "companion_ids": list(companion_ids),
            "customization_notes": customization_notes,
            "provider_id": "scripted-builder",
            "model_id": "scripted-builder-model",
            "construction_intent": {
                "boundaries": "",
                "preferences": "",
                "specific_requests": customization_notes,
                "contributions": {},
            },
        },
        application_owner=owner,
        owner_id="owner-rift",
        provider_resolver=None,  # type: ignore[arg-type]
        model_choices=lambda: (),
        session_loop=CampaignSessionLoop(owner),
        storage_root=storage_root,
    )
    assert resumed_start == started
    ready_messages = [
        message
        for message in owner.session_context(builder_session_id).messages
        if message.kind is SessionMessageKind.USER
        and message.content.get("interview_step") == "ready"
    ]
    assert len(ready_messages) == 1
    plan_text = (storage_root / "campaigns" / campaign_id / "campaign-plan.md").read_text(
        encoding="utf-8"
    )
    structured_plan = json.loads(
        (storage_root / "campaigns" / campaign_id / "campaign-plan.json").read_text(
            encoding="utf-8"
        )
    )
    _completed, plan, handoff = owner.finalize_campaign_construction(
        campaign_id=campaign_id,
        dm_provider_id="scripted-dm",
        dm_model_id="scripted-dm-model",
        plan_artifact_text=plan_text,
        structured_plan=structured_plan,
        model_guidance_override=True,
    )
    assert handoff.dm_session_id
    assert plan.content["campaign_plan_markdown"] == plan_text
    return campaign_id, handoff.dm_session_id


def test_committed_mechanic_reference_survives_provider_failure_retry_and_reopen(
    tmp_path: Path,
) -> None:
    """Keep one committed result visible while Retry finishes the interrupted turn."""

    recovered_narration = "You finish checking the moss and remain beside the old stones."

    class _CommitThenDisconnectProvider(Provider):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0
            self.d20_emissions = 0
            self.disconnected = False

        async def generate(
            self,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
            stream: bool = False,
            timeout: float = 60.0,
            **kwargs: object,
        ) -> ProviderResponse:
            raise NotImplementedError

        async def generate_stream(
            self,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
            timeout: float = 120.0,
            **kwargs: object,
        ) -> AsyncIterator[ProviderStreamChunk]:
            self.calls += 1
            if self.d20_emissions == 0:
                d20 = next(
                    item
                    for item in tools or []
                    if str(item["function"]["name"]).endswith("_d20_test")
                    and not str(item["function"]["name"]).endswith(
                        "_unbound_d20_test"
                    )
                )
                self.d20_emissions += 1
                yield ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-perception-before-disconnect",
                        "function": {
                            "name": d20["function"]["name"],
                            "arguments": json.dumps(
                                {
                                    "actor": player_id,
                                    "skill": "perception",
                                    "circumstantial_modifier": 0,
                                    "target_number": 12,
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
                return
            if not self.disconnected:
                self.disconnected = True
                raise ProviderConnectionError("Synthetic disconnect after committed mechanic")
            if kwargs.get("tool_choice") == "required":
                update = next(
                    item
                    for item in tools or []
                    if str(item["function"]["name"]).endswith(
                        "_update_campaign_file"
                    )
                )
                arguments: dict[str, object] = {
                    "state_change": "unchanged",
                    "state_change_summary": (
                        "The perception result adds no durable campaign truth."
                    ),
                    "revised_player_facing_response": [
                        {"kind": "narration", "text": recovered_narration}
                    ],
                }
                yield ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-settle-perception-retry",
                        "function": {
                            "name": update["function"]["name"],
                            "arguments": json.dumps(arguments),
                        },
                    },
                    finish_reason="tool_calls",
                )
                return
            yield ProviderStreamChunk(
                content=recovered_narration,
                finish_reason="stop",
            )

        async def close(self) -> None:
            return None

    database = tmp_path / "committed-mechanic-retry.db"
    supervisor = LocalOwnerSupervisor()
    issued = supervisor.acquire_owner(database, "owner-rift")
    supervisor.complete_handshake(derive_owner_response(issued.secret_hex))
    bus = LocalCommandBus(supervisor)
    owner = supervisor.application_owner()
    campaign_id = ""
    session_id = ""
    result_sequence = 0
    try:
        campaign_id, session_id = _construct_rift(
            bus,
            owner,
            tmp_path,
            lane="quick_start",
            companion_ids=(),
        )
        snapshot = owner.snapshot(campaign_id)
        player_id = snapshot.participants[0].participant_id
        turn_id = owner.current_open_turn(campaign_id)
        assert turn_id is not None
        provider = _CommitThenDisconnectProvider()
        session_loop = CampaignSessionLoop(owner)

        def dispatch(method: str, params: Mapping[str, Any]) -> Any:
            return _dispatch(
                bus,
                method,
                params,
                application_owner=owner,
                owner_id="owner-rift",
                provider_resolver=lambda _session: provider,
                model_choices=lambda: (),
                session_loop=session_loop,
                storage_root=tmp_path,
            )

        with pytest.raises(ProviderConnectionError, match="Synthetic disconnect"):
            dispatch(
                "submit_session_input",
                {
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "attempt_id": "attempt-perception-disconnect",
                    "message_id": "message-perception-disconnect",
                    "user_text": "I study the scuffed moss for a hidden trail.",
                },
            )

        failed_snapshot = owner.snapshot(campaign_id)
        d20_calls = [
            event
            for event in failed_snapshot.events
            if event.event_type.value == "tool_call"
            and event.safe_payload.get("tool_name") == "d20_test"
        ]
        assert len(d20_calls) == 1
        result_event = next(
            event
            for event in failed_snapshot.events
            if event.event_type.value == "tool_result"
            and event.action_id == d20_calls[0].action_id
        )
        result = result_event.safe_payload.get("result")
        assert isinstance(result, Mapping)
        result_card = result.get("result_card")
        assert isinstance(result_card, Mapping)
        assert result_card.get("title") == "perception"
        result_sequence = result_event.sequence

        failed_references = [
            message
            for message in owner.session_context(session_id).messages
            if message.kind is SessionMessageKind.CANONICAL_REFERENCE
            and message.canonical_sequence == result_sequence
        ]
        assert len(failed_references) == 1

        interruption = owner.retryable_interruption(
            campaign_id,
            failed_attempt_id="attempt-perception-disconnect",
        )
        assert interruption is not None
        dispatch(
            "manual_retry_session",
            {
                "session_id": session_id,
                "interruption_id": interruption.interruption_id,
                "retry_id": "attempt-perception-retry",
            },
        )

        assert provider.d20_emissions == 1
        recovered_references = [
            message
            for message in owner.session_context(session_id).messages
            if message.kind is SessionMessageKind.CANONICAL_REFERENCE
            and message.canonical_sequence == result_sequence
        ]
        assert len(recovered_references) == 1
        assert len(
            [
                event
                for event in owner.snapshot(campaign_id).events
                if event.event_type.value == "tool_call"
                and event.safe_payload.get("tool_name") == "d20_test"
            ]
        ) == 1
    finally:
        supervisor.shutdown()

    with AuthorityOwner.open(database, "owner-rift-reopen") as reopened:
        reopened_references = [
            message
            for message in reopened.session_context(session_id).messages
            if message.kind is SessionMessageKind.CANONICAL_REFERENCE
            and message.canonical_sequence == result_sequence
        ]
        assert len(reopened_references) == 1
        assert len(
            [
                event
                for event in reopened.snapshot(campaign_id).events
                if event.event_type.value == "tool_call"
                and event.safe_payload.get("tool_name") == "d20_test"
            ]
        ) == 1


def test_rift_quick_start_customize_mcp_and_reopen(tmp_path: Path) -> None:
    database = tmp_path / "rift.db"
    supervisor = LocalOwnerSupervisor()
    issued = supervisor.acquire_owner(database, "owner-rift")
    supervisor.complete_handshake(derive_owner_response(issued.secret_hex))
    bus = LocalCommandBus(supervisor)
    owner = supervisor.application_owner()
    try:
        archive = bundled_rift_archive()
        assert archive == bundled_rift_archive()
        public = _dispatch(
            bus,
            "rift_template",
            {},
            application_owner=owner,
            owner_id="owner-rift",
            provider_resolver=None,  # type: ignore[arg-type]
            model_choices=lambda: (),
            session_loop=CampaignSessionLoop(owner),
            storage_root=tmp_path,
        )
        assert public == rift_public_template()
        assert public["template_id"] == "the-rift-awakening"
        assert [item["id"] for item in public["player_archetypes"]] == [
            "open-adventurer"
        ]
        assert public["companions"] == []

        invalid_start = {
            "operation_id": "reject-cross-type-rift-selection",
            "campaign_id": "not-created",
            "builder_session_id": "not-created",
            "ruleset_selection": "the-rift-awakening",
            "setting_id": "pneuma.severed-lands",
            "plan_id": "the-rift-awakening",
            "display_name": "Rejected cross-type selection",
            "lane": "quick_start",
            "archetype_id": "mara",
            "companion_ids": [],
            "customization_notes": "",
            "provider_id": "scripted-builder",
            "model_id": "scripted-builder-model",
        }
        with pytest.raises(CommandRejected, match="explicitly selected compatible"):
            _dispatch(
                bus,
                "start_rift_campaign",
                invalid_start,
                application_owner=owner,
                owner_id="owner-rift",
                provider_resolver=None,  # type: ignore[arg-type]
                model_choices=lambda: (),
                session_loop=CampaignSessionLoop(owner),
                storage_root=tmp_path,
            )
        invalid_start["operation_id"] = "reject-cross-type-rift-setting"
        invalid_start["ruleset_selection"] = "sylvia.srd-lite.bg3"
        invalid_start["setting_id"] = "the-rift-awakening"
        with pytest.raises(CommandRejected, match="explicitly selected compatible"):
            _dispatch(
                bus,
                "start_rift_campaign",
                invalid_start,
                application_owner=owner,
                owner_id="owner-rift",
                provider_resolver=None,  # type: ignore[arg-type]
                model_choices=lambda: (),
                session_loop=CampaignSessionLoop(owner),
                storage_root=tmp_path,
            )

        quick_id, quick_dm_session = _construct_rift(
            bus,
            owner,
            tmp_path,
            lane="quick_start",
            companion_ids=(),
        )
        custom_id, _custom_dm_session = _construct_rift(
            bus,
            owner,
            tmp_path,
            lane="customize",
            companion_ids=(),
        )
        source_plan = bundled_rift_campaign_plan()
        source_guidance = source_plan.payload["gm_guidance"]["instructions"]
        source_opening = source_plan.payload["structure"][0]["planned_encounters"][0]
        assert "Run The Rift Awakening as a D&D 5e campaign" in source_guidance
        assert "private story preparation and desired direction" in source_guidance
        assert "never as a scene queue or script" in source_guidance
        assert "Interpret how to get there freely" in source_guidance
        assert "Reorder, combine, defer, replace, or retire" in source_guidance
        assert "do not introduce an encounter merely because it is prepared" in source_guidance
        assert "three or four short tabletop-DM paragraphs" not in source_guidance
        assert "damp soil ripples" not in source_guidance
        assert "one available way to express" in source_opening
        assert "be delayed, or be replaced" in source_opening
        assert "not present until the GM establishes it" in source_opening
        reusable_plan = save_reusable_rift_plan(
            tmp_path,
            operation_id="save-rift-reusable-selection",
            title="Reusable Rift Route",
            source_payload=source_plan.payload,
            source_markdown=source_plan.readable_markdown,
        )
        reusable_id, _reusable_dm_session = _construct_rift(
            bus,
            owner,
            tmp_path,
            lane="quick_start",
            companion_ids=(),
            plan_id=reusable_plan.artifact_id,
            plan_title=reusable_plan.title,
        )

        quick = owner.snapshot(quick_id)
        custom = owner.snapshot(custom_id)
        reusable = owner.snapshot(reusable_id)
        assert quick.writer is WriterRole.DM
        assert custom.writer is WriterRole.DM
        assert reusable.writer is WriterRole.DM
        assert (
            owner.accepted_campaign_plan(reusable_id).content["campaign_plan"]["title"]
            == "Reusable Rift Route"
        )
        assert quick.participants[0].participant_id != custom.participants[0].participant_id
        quick_player_id = quick.participants[0].participant_id
        assert [item.data["party_role"] for item in quick.participants] == ["player_character"]
        assert (
            sum(item.data["party_role"] == "player_character" for item in custom.participants) == 1
        )
        assert sum(item.data["party_role"] == "companion" for item in custom.participants) == 0
        quick_panels = _dispatch(
            bus,
            "campaign_panels",
            {"campaign_id": quick_id},
            application_owner=owner,
            owner_id="owner-rift",
            provider_resolver=None,  # type: ignore[arg-type]
            model_choices=lambda: (),
            session_loop=CampaignSessionLoop(owner),
            storage_root=tmp_path,
        )
        assert [surface["id"] for surface in quick_panels["surfaces"]] == [
            "dnd-party",
            "dnd-encounter",
            "dnd-inventory",
        ]
        panels_by_id = {surface["id"]: surface for surface in quick_panels["surfaces"]}
        participant_name = panels_by_id["dnd-party"]["data"]["participants"][0]["data"][
            "name"
        ]
        assert participant_name == "Adventurer"
        assert panels_by_id["dnd-party"]["data"]["entities"][quick_player_id]["hp"] == 11
        assert (
            "longsword"
            in panels_by_id["dnd-inventory"]["data"]["entities"][quick_player_id][
                "inventory"
            ]
        )
        assert "dnd-objectives" not in panels_by_id
        assert "truth-of-the-doors" not in repr(quick_panels)
        assert "final-choice" not in repr(quick_panels)
        assert panels_by_id["dnd-encounter"]["actions"][0]["intent"] == "end_combat_encounter"

        turn_id = owner.current_open_turn(quick_id)
        assert turn_id is not None
        plan_text = (tmp_path / "campaigns" / quick_id / "campaign-plan.md").read_text(
            encoding="utf-8"
        )
        structured_plan = owner.accepted_campaign_plan(quick_id).content["campaign_plan"]
        opening_provider = _RiftOpeningProvider(quick_player_id)
        opening_result = CampaignSessionLoop(owner).run(
            SessionRunRequest(
                session_id=quick_dm_session,
                turn_id=turn_id,
                attempt_id="attempt-rift-campaign-start",
                message_id="message-rift-campaign-start",
                user_text="",
                input_kind=SessionInputKind.CAMPAIGN_START,
            ),
            ProviderSessionAdapter(
                owner,
                opening_provider,
                owner_id="owner-rift",
                campaign_plan_text=plan_text,
                campaign_plan=structured_plan,
            ).for_turn(turn_id),
        )
        assert opening_result.receipts == ()
        opening_activity = next(
            message
            for message in owner.session_context(quick_dm_session).messages
            if message.content.get("kind") == "gm_turn_activity"
        )
        assert opening_activity.content["activity"] == "campaign_opened"
        assert opening_activity.content["basis_message_id"] == "message-rift-campaign-start"
        assert len(opening_provider.calls) == 2
        opening_surface = {
            re.sub(r"^tool_\d+_", "", str(tool["function"]["name"]))
            for tool in (opening_provider.calls[0][1] or [])
        }
        assert opening_surface == {
            "d20_test",
            "search_campaign_knowledge",
            "read_campaign_reference",
            "start_combat_encounter_entities",
            "start_group_combat_encounter",
            "take_srd_short_rest_hit_die",
            "unbound_d20_test",
            "update_campaign_file",
        }
        assert {
            re.sub(r"^tool_\d+_", "", str(tool["function"]["name"]))
            for tool in (opening_provider.calls[1][1] or [])
        } == opening_surface
        assert "CAMPAIGN_FILE_DISPOSITION_REQUIRED" not in str(opening_provider.calls)
        first_opening_messages = opening_provider.calls[0][0]
        first_ruleset_guide = next(
            str(message["content"])
            for message in first_opening_messages
            if str(message.get("content", "")).startswith(
                "SYLVIA_GM_RULESET_OPERATING_GUIDE"
            )
        )
        first_campaign_explainers = next(
            str(message["content"])
            for message in first_opening_messages
            if str(message.get("content", "")).startswith(
                "SYLVIA_GM_CAMPAIGN_PREPARATION"
            )
        )
        first_fresh_authority = next(
            str(message["content"])
            for message in first_opening_messages
            if str(message.get("content", "")).startswith(
                "SYLVIA_GM_FRESH_CAMPAIGN_STATE"
            )
        )
        first_campaign_data = json.loads(first_campaign_explainers.split("\n", 1)[1])
        first_fresh_data = json.loads(first_fresh_authority.split("\n", 1)[1])
        assert "two distinct modes: out-of-combat and in-combat" in first_ruleset_guide
        assert "start_combat_encounter_entities" in first_ruleset_guide
        preparation = first_campaign_data["campaign_plan_preparation"]
        guidance = preparation["gm_guidance"]["instructions"]
        assert "Run The Rift Awakening as a D&D 5e campaign" in guidance
        assert "Campaign Plan as private story preparation" in guidance
        assert "never as a scene queue or script" in guidance
        assert "reorder, combine, defer, replace, or retire" in guidance.lower()
        act_one = next(act for act in preparation["prepared_acts"] if act["id"] == "act-1")
        admitted_sources = {
            source["path"]: source["content"]
            for source in first_campaign_data["campaign_setting_references"]
        }
        assert preparation["starting_location"] == {
            "id": "verdant-margin",
            "path": "locations/verdant-margin.md",
        }
        assert "campaigns/the-rift-awakening/act-1.md" not in admitted_sources
        assert "situations" not in act_one
        assert "planned_encounters" not in act_one
        assert "Read the structured Campaign Plan" in act_one["detail_handling"]
        assert set(admitted_sources) == {"locations/verdant-margin.md"}
        opening_location = admitted_sources["locations/verdant-margin.md"]
        assert "forest where the Rift's energy has accelerated growth" in opening_location
        assert "## Hooks" not in opening_location
        assert "supply cache" not in opening_location.casefold()
        assert "content/monsters/rifthound.md" not in admitted_sources
        one_way_fact = "There is no return crossing: the Rift is one-way."
        assert one_way_fact not in {
            item["text"] for item in first_fresh_data["PLAYER_KNOWN"]["current_facts"]
        }
        assert "prepared_secrets" not in first_campaign_data["private_preparation"]
        assert first_campaign_data["private_preparation"][
            "additional_material_available_via"
        ] == {"source": "campaign_plan", "path": "structured"}
        assert one_way_fact not in str(first_campaign_data)
        assert first_campaign_data["private_preparation"][
            "private_setting_references"
        ] == ["DM-SECRETS.md"]
        assert one_way_fact not in first_fresh_data["GM_PRIVATE_CURRENT"][
            "character_knowledge"
        ].get("stern", [])
        opening_talk = " ".join(
            str(event.safe_payload)
            for event in opening_result.canonical
            if event.event_type.value == "talk"
        )
        assert one_way_fact not in opening_talk
        assert (
            "committed game state" in opening_talk
            or "The Verdant Margin closes softly behind you" in opening_talk
        )
        assert any(
            "application-owned, not player speech" in str(message.get("content", ""))
            and "campaign_start" in str(message.get("content", ""))
            for message in first_opening_messages
        )
        assert not any(
            "immediately call the currently exposed start-combat mechanic"
            in str(message.get("content", "")).casefold()
            for message in first_opening_messages
        )
        assert not any(
            message.get("role") == "user"
            and str(message.get("content", "")).strip() in {"Begin the story", "Begin."}
            for message in first_opening_messages
        )
        start_context = owner.session_context(quick_dm_session)
        assert any(
            message.kind.value == "evidence"
            and message.content.get("kind") == "campaign_start"
            for message in start_context.messages
        )
        opening_snapshot = owner.snapshot(quick_id)
        assert "sylvia.srd-lite.bg3.combat" not in opening_snapshot.state
        assert opening_snapshot.continuity.current_scene == {
            "act_id": "act-1",
            "location_id": "verdant-margin",
            "origin": "gm_commit",
            "summary": (
                "The Adventurer stands at a fork around an old stone pillar in the Verdant "
                "Margin; the route behind is closing and no encounter has begun."
            ),
        }

        provider = _RiftIntentProvider(quick_player_id)
        owner.set_admin_full_audit(True)
        audited_provider = AdminAuditedProvider(
            provider,
            sink=owner.record_admin_full_audit,
            session_id=quick_dm_session,
            role="dm",
            provider_id="scripted-dm",
            model_id="scripted-dm-model",
        )
        adapter = ProviderSessionAdapter(
            owner,
            audited_provider,
            owner_id="owner-rift",
            campaign_plan_text=plan_text,
            campaign_plan=structured_plan,
        )
        start_combat_result = CampaignSessionLoop(owner).run(
            SessionRunRequest(
                session_id=quick_dm_session,
                turn_id=turn_id,
                attempt_id="attempt-rift-longsword-start-combat",
                message_id="message-rift-longsword-start-combat",
                user_text=(
                    "I close the distance and strike the rift-scarred hound "
                    "with my longsword."
                ),
            ),
            adapter.for_turn(turn_id),
        )
        assert len(start_combat_result.receipts) >= 2
        assert all(
            receipt.status is ActionStatus.COMMITTED
            for receipt in start_combat_result.receipts
        )
        start_receipt = next(
            receipt
            for receipt in start_combat_result.receipts
            if receipt.result.get("tool") == "start_combat_encounter_entities"
        )
        initiative_directives = start_receipt.result["narrative_directives"]
        assert len(initiative_directives) == 1
        initiative_directive = initiative_directives[0]
        assert initiative_directive["priority"] == "high"
        assert initiative_directive["style"] == "system"
        assert "Initiative had to be rolled before an attack could be resolved" in (
            initiative_directive["text"]
        )
        combat_after_initiative = owner.snapshot(quick_id).state[
            "sylvia.srd-lite.bg3.combat"
        ]
        ordered_actor_ids = [
            combat_after_initiative[key] for key in ("order_first", "order_second")
        ]
        assert f"{ordered_actor_ids[0]}, then {ordered_actor_ids[1]}" in (
            initiative_directive["text"]
        )
        assert "state those actors' player-facing names" in initiative_directive["text"]
        assert "never the internal IDs" in initiative_directive["text"]
        assert "{" not in initiative_directive["text"]
        assert "Follow the resulting Player or GM decision boundary" in (
            initiative_directive["text"]
        )
        player_attack_receipt = next(
            receipt
            for receipt in start_combat_result.receipts
            if receipt.result.get("tool") == "resolve_combat_attack"
        )
        assert player_attack_receipt.status is ActionStatus.COMMITTED
        assert len(provider.calls) >= 3
        audit_traces = owner.admin_full_audit_projection()["traces"]
        discoveries = {
            trace["discovery_id"]: trace
            for trace in audit_traces
            if trace["kind"] == "mcp_list_tools"
            and trace["purpose"] == "provider_inference"
        }
        requests = [trace for trace in audit_traces if trace["kind"] == "provider_request"]
        assert len(requests) == len(provider.calls)
        assert len(discoveries) >= len(requests)
        assert all(
            request["correlation"]["mcp_discovery_id"] in discoveries
            for request in requests
        )
        required_requests = [
            request
            for request in requests
            if request["options"].get("tool_choice") == "required"
        ]
        assert required_requests
        required_tool_names = [
            {
                re.sub(r"^tool_\d+_", "", str(tool["function"]["name"]))
                for tool in request["tools"]
            }
            for request in required_requests
        ]
        assert any("update_campaign_file" in tool_names for tool_names in required_tool_names)
        committed_tool_names = [
            trace["tool_name"]
            for trace in audit_traces
            if trace["kind"] == "mcp_call_tool"
        ]
        assert committed_tool_names[0:2] == [
            "start_combat_encounter_entities",
            "begin_combat_turn",
        ]
        assert committed_tool_names[-1] == "resolve_combat_attack"
        assert "advance_combat_encounter_turn" in committed_tool_names or len(
            committed_tool_names
        ) == 3
        tool_surfaces = [
            {
                re.sub(r"^tool_\d+_", "", str(tool["function"]["name"]))
                for tool in (tools or [])
            }
            for _messages, tools in provider.calls
        ]
        assert tool_surfaces[0] == {
            "d20_test",
            "take_srd_short_rest_hit_die",
            "unbound_d20_test",
            "search_campaign_knowledge",
            "read_campaign_reference",
            "update_campaign_file",
            "start_combat_encounter_entities",
            "start_group_combat_encounter",
        }
        assert all("begin_combat_turn" not in surface for surface in tool_surfaces)
        assert all("advance_combat_encounter_turn" not in surface for surface in tool_surfaces)
        assert any("resolve_combat_attack" in surface for surface in tool_surfaces)
        attack_surface = next(
            surface for surface in tool_surfaces if "resolve_combat_attack" in surface
        )
        assert "resolve_adjudicated_creature_attack" not in attack_surface
        assert any(
            {
                "search_campaign_knowledge",
                "read_campaign_reference",
                "update_campaign_file",
            }
            <= surface
            for surface in tool_surfaces
        )
        wolf_hp = owner.snapshot(quick_id).state["sylvia.srd-lite.bg3.entities"][
            "rift-opening-riftwolf"
        ]["hp"]
        assert isinstance(wolf_hp, int)
        after_action_surface = {tool.name for tool in owner.catalog(quick_id)}
        assert "start_combat_encounter_entities" not in after_action_surface
        assert "begin_combat_turn" not in after_action_surface
        assert "end_combat_turn" in after_action_surface
        assert "resolve_combat_attack" not in after_action_surface
        assert "resolve_adjudicated_creature_attack" not in after_action_surface
        assert any(
            str(message.get("content", "")).startswith(
                "SYLVIA_GM_EXACT_NARRATION_FACTS"
            )
            for messages, _tools in provider.calls
            for message in messages
        )
        mcp = StatelessMcpMechanics(owner)
        assert '"id":"the-rift-awakening"' in str(
            mcp.read_reference(
                quick_id,
                source="campaign_plan",
                path="structured",
            )["content"]
        )
        assert "The Game Master Describes a Scene" in str(
            mcp.read_reference(
                quick_id,
                source="ruleset",
                path="content/corpus/playing_the_game.json",
            )["content"]
        )
        character_file = json.loads(
            str(
                mcp.read_reference(
                    quick_id,
                    source="participant",
                    path=quick_player_id,
                )["content"]
            )
        )
        assert character_file["ruleset"]["starting_data"]["name"] == "Adventurer"
        assert character_file["profile"] == {"status": "sparse", "authored_text": None}
        assert character_file["selector"]["summary"] == (
            "A generic level-one Fighter with no authored identity or backstory."
        )
        assert "The crossing is one-way" in str(
            mcp.read_reference(
                quick_id,
                source="campaign_setting",
                path="DM-SECRETS.md",
            )["content"]
        )
        after_action_mcp_surface = {
            tool.name
            for tool in mcp.list_tools(
                quick_id,
                purpose="acceptance_after_action",
                discovery_id="rift-after-action",
            )
        }
        assert "read_campaign_reference" in after_action_mcp_surface
        assert "search_campaign_knowledge" in after_action_mcp_surface
        assert after_action_mcp_surface - {
            "search_campaign_knowledge",
            "read_campaign_reference",
        } == after_action_surface
        updated_panels = {
            surface["id"]: surface for surface in owner.campaign_panels(quick_id)["surfaces"]
        }
        assert (
            updated_panels["dnd-encounter"]["data"]["entities"]["rift-opening-riftwolf"]["hp"]
            == wolf_hp
        )
        combat_panel = updated_panels["dnd-encounter"]["data"]["combat"]
        assert combat_panel["encounter_active"] is True
        assert combat_panel["current_actor"] in {
            quick_player_id,
            "rift-opening-riftwolf",
        }
        end_turn_tool = next(
            tool for tool in owner.catalog(quick_id) if tool.name == "end_combat_turn"
        )
        end_turn_basis = owner.snapshot(quick_id)
        end_turn = owner.execute_action(
            ActionCommand(
                campaign_id=quick_id,
                action_id="action-end-rift-player-turn",
                idempotency_key="end-rift-player-turn",
                owner_id="owner-rift",
                writer=WriterRole.DM,
                expected_revision=end_turn_basis.revision,
                turn_id=turn_id,
                package_id=end_turn_tool.package_id,
                tool_name=end_turn_tool.name,
                parameters={},
                initiating_session_id=quick_dm_session,
            )
        )
        assert end_turn.status is ActionStatus.COMMITTED
        assert {tool.name for tool in owner.catalog(quick_id)} == {
            "advance_combat_encounter_turn",
            "end_combat_encounter",
        }
        after_turn_mcp_surface = {
            tool.name
            for tool in mcp.list_tools(
                quick_id,
                purpose="acceptance_after_turn_end",
                discovery_id="rift-after-turn-end",
            )
        }
        assert "read_campaign_reference" in after_turn_mcp_surface
        assert "search_campaign_knowledge" in after_turn_mcp_surface
        assert after_turn_mcp_surface - {
            "search_campaign_knowledge",
            "read_campaign_reference",
        } == {
            "advance_combat_encounter_turn",
            "end_combat_encounter",
        }
        panel_basis = owner.snapshot(quick_id)
        panel_receipt = owner.execute_panel_action(
            campaign_id=quick_id,
            surface_id="dnd-encounter",
            panel_action_id="end-combat",
            operation_id="panel-end-rift-combat",
            expected_revision=panel_basis.revision,
        )
        assert panel_receipt.status is ActionStatus.COMMITTED
        after_combat_mcp_surface = {
            tool.name
            for tool in mcp.list_tools(
                quick_id,
                purpose="acceptance_after_combat_end",
                discovery_id="rift-after-combat-end",
            )
        }
        assert "read_campaign_reference" in after_combat_mcp_surface
        assert "search_campaign_knowledge" in after_combat_mcp_surface
        assert after_combat_mcp_surface - {
            "search_campaign_knowledge",
            "read_campaign_reference",
        } == {
            "d20_test",
            "take_srd_short_rest_hit_die",
            "unbound_d20_test",
            "start_combat_encounter_entities",
            "start_group_combat_encounter",
        }
        ended_panels = {
            surface["id"]: surface for surface in owner.campaign_panels(quick_id)["surfaces"]
        }
        assert ended_panels["dnd-encounter"]["data"]["combat"]["encounter_active"] is False
        assert "player" not in owner.snapshot(quick_id).continuity.reveals.get(
            "rift-is-one-way", []
        )
        with pytest.raises(CommandRejected, match="protected secret"):
            owner.record_campaign_memory(
                session_id=quick_dm_session,
                memory_id="memory-rift-illegal-secret",
                fact_id="illegal-secret-memory",
                category="discovery",
                text=one_way_fact,
                visibility="player_known",
                known_by=("player",),
                basis_message_ids=("message-rift-campaign-start",),
            )
        scene_result: Mapping[str, object] = {}
        for act_id, summary, location_id in (
            ("act-2", "The party has reached the Margin road.", "verdant-margin"),
            ("act-3", "The party has reached Waystation.", "waystation"),
            (
                "act-4",
                "V has begun the private meeting beneath Waystation.",
                "waystation",
            ),
        ):
            scene_result = mcp.commit_campaign_file(
                quick_id,
                session_id=quick_dm_session,
                basis_message_id="message-rift-longsword-start-combat",
                parameters={
                    "state_change": "changed",
                    "state_change_summary": f"The current scene advances to {act_id}.",
                    "scenes": [
                        {
                            "act_id": act_id,
                            "summary": summary,
                            "location_id": location_id,
                        }
                    ]
                },
            )
            assert scene_result["status"] == "campaign_file_updated"
        narrative_surface = mcp.list_tools(
            quick_id,
            purpose="acceptance_reveal_available",
            discovery_id="rift-reveal-available",
            session_id=quick_dm_session,
        )
        narrative_tools = {
            tool.name: tool
            for tool in narrative_surface
            if tool.category == "narrative_state"
        }
        assert set(narrative_tools) == {"update_campaign_file"}
        assert all("oneOf" not in tool.input_schema for tool in narrative_tools.values())
        campaign_file_tool = narrative_tools["update_campaign_file"]
        reveal_schema = campaign_file_tool.input_schema["properties"]["reveals"]["items"]
        assert reveal_schema["required"] == ["audience", "secret_id"]
        assert reveal_schema["properties"]["secret_id"]["enum"] == [
            "rift-is-one-way",
            "anchor-doors-are-life-support",
            "v-preserved-the-lie",
        ]
        continuity_result = mcp.commit_campaign_file(
            quick_id,
            session_id=quick_dm_session,
            basis_message_id="message-rift-longsword-start-combat",
            parameters={
                "state_change": "changed",
                "state_change_summary": "The truth was revealed and the relationship changed.",
                "reveals": [
                    {
                        "secret_id": "rift-is-one-way",
                        "audience": ["player"],
                    }
                ],
                "facts": [
                    {
                        "fact_id": "decision-private-meeting",
                        "category": "decision",
                        "text": "The party agreed to meet V privately beneath Waystation.",
                        "visibility": "player_known",
                        "known_by": ["player", "v"],
                    },
                    {
                        "fact_id": "relationship-v-trust",
                        "category": "relationship",
                        "text": "V trusted Mara with the truth that the Rift is one-way.",
                        "visibility": "player_known",
                        "known_by": ["player", "v"],
                    },
                ],
            },
        )
        assert continuity_result["status"] == "campaign_file_updated"
        assert continuity_result["change_count"] == 3
        for index in range(50):
            owner.append_agent_message(
                session_id=quick_dm_session,
                message_id=f"old-history-user-{index}",
                kind=SessionMessageKind.USER,
                content={"text": f"old-history-{index} user detail"},
            )
            owner.append_agent_message(
                session_id=quick_dm_session,
                message_id=f"old-history-talk-{index}",
                kind=SessionMessageKind.TALK,
                content={"text": f"old-history-{index} narrator detail"},
            )
        reveal_provider = _RevealedSecretProvider(one_way_fact)
        reveal_result = CampaignSessionLoop(owner).run(
            SessionRunRequest(
                session_id=quick_dm_session,
                turn_id=turn_id,
                attempt_id="attempt-rift-revealed-secret",
                message_id="message-rift-ask-v",
                user_text="What truth did V just tell me?",
            ),
            ProviderSessionAdapter(
                owner,
                reveal_provider,
                owner_id="owner-rift",
                campaign_plan_text=plan_text,
                campaign_plan=structured_plan,
            ).for_turn(turn_id),
        )
        assert any(
            one_way_fact in str(event.safe_payload)
            for event in reveal_result.canonical
            if event.event_type.value == "talk"
        ), (
            [
                (event.event_type.value, event.safe_payload)
                for event in reveal_result.canonical
            ],
            reveal_provider.request_surfaces,
        )
        assert any(
            term in str(event.safe_payload).lower()
            for event in start_combat_result.canonical
            for term in ("longsword", "riftwolf bite")
        )
        print(
            "RIFT MODEL-FACING PLAY",
            "plan=visible",
            "tools=start_combat->begin_turn->attack",
            f"hp=9->{wolf_hp}",
            "narration=committed",
        )
        owner.complete_turn(quick_id, turn_id)
        export_path = tmp_path / "rift-portable-backup.zip"
        exported = owner.export_campaign(
            operation_id="export-rift-quick",
            campaign_id=quick_id,
            target=export_path,
        )
        assert exported.byte_count > 0

        candidate_id = next(
            item.candidate_id
            for item in owner.package_candidates()
            if item.package_lock.entries[0].package_id == "sylvia.srd-lite.bg3"
        )
        candidate = owner.package_candidate(candidate_id)
        with pytest.raises(CommandRejected):
            materialize_rift_selection(
                participant_namespace="0" * 24,
                lane="quick_start",
                archetype_id="open-adventurer",
                companion_ids=tuple(f"not-bundled-{index}" for index in range(6)),
                customization_notes="",
                package=candidate.package_lock.entries[0],
                participant_declaration=owner.package_participant_declarations(candidate_id)[0],
            )
    finally:
        supervisor.shutdown()

    with AuthorityOwner.open(database, "owner-rift-reopened") as reopened:
        quick = reopened.snapshot(quick_id)
        custom = reopened.snapshot(custom_id)
        assert quick.writer is WriterRole.DM
        assert quick.state["sylvia.srd-lite.bg3.entities"]["rift-opening-riftwolf"]["hp"] == wolf_hp
        assert len(custom.participants) == 1
        assert quick.continuity.current_scene["act_id"] == "act-4"
        assert "player" in quick.continuity.reveals["rift-is-one-way"]
        assert {
            item["record_id"] for item in quick.continuity.facts
        } >= {"decision-private-meeting", "relationship-v-trust"}
        reopened_context = reopened.session_context(quick_dm_session)
        reopened_messages = ProviderSessionAdapter(
            reopened,
            _RevealedSecretProvider(one_way_fact),
            owner_id="owner-rift-reopened",
            campaign_plan_text=plan_text,
            campaign_plan=structured_plan,
        )._messages(  # noqa: SLF001 - exact restart projection under test
            SessionProviderInput(
                session=reopened_context.session,
                history=reopened_context,
                catalog=(),
                control=ProviderControl(),
            )
        )
        reopened_campaign_preparation = next(
            str(message["content"])
            for message in reopened_messages
            if str(message.get("content", "")).startswith(
                "SYLVIA_GM_CAMPAIGN_PREPARATION"
            )
        )
        reopened_fresh_campaign_state = next(
            str(message["content"])
            for message in reopened_messages
            if str(message.get("content", "")).startswith(
                "SYLVIA_GM_FRESH_CAMPAIGN_STATE"
            )
        )
        reopened_data = json.loads(reopened_campaign_preparation.split("\n", 1)[1])
        reopened_fresh = json.loads(reopened_fresh_campaign_state.split("\n", 1)[1])
        reopened_sources = {
            source["path"]: source["content"]
            for source in reopened_data["campaign_setting_references"]
        }
        assert "campaigns/the-rift-awakening/act-4.md" not in reopened_sources
        act_four = next(
            act
            for act in reopened_data["campaign_plan_preparation"]["prepared_acts"]
            if act["id"] == "act-4"
        )
        assert "situations" not in act_four
        assert "planned_encounters" not in act_four
        assert "Read the structured Campaign Plan" in act_four["detail_handling"]
        assert reopened_sources == {}
        assert one_way_fact in {
            item["fact"] for item in reopened_fresh["PLAYER_KNOWN"]["revealed_facts"]
        }
        assert {
            item["record_id"]
            for item in reopened_fresh["PLAYER_KNOWN"]["current_facts"]
        } >= {"decision-private-meeting", "relationship-v-trust"}
        assert reopened_fresh["campaign_state"]["current_scene"]["act_id"] == "act-4"
        assert (
            "sylvia.srd-lite.bg3.entities"
            in reopened_fresh["campaign_state"]["ruleset_owned_values"]
        )

    recovery_database = tmp_path / "rift-recovery.db"
    with AuthorityOwner.open(recovery_database, "owner-rift-recovery") as recovered:
        result = recovered.recover_campaign(
            recovery_id="recover-rift-quick",
            source=export_path,
        )
        assert result.campaign_id == quick_id
        restored = recovered.snapshot(quick_id)
        assert restored.state["sylvia.srd-lite.bg3.entities"]["rift-opening-riftwolf"]["hp"] == wolf_hp
        assert [item.data["party_role"] for item in restored.participants] == ["player_character"]

    print(
        "ALPHA-05 RIFT",
        "quick=1pc+0 companions",
        "customize=1pc+0 companions",
        "mcp=committed reopen=exact recovery=exact",
    )


def test_campaign_state_continuity_survives_restart_and_portable_recovery(
    tmp_path: Path,
) -> None:
    """Campaign-specific truth is durable authority, not reconstructed transcript evidence."""

    database = tmp_path / "continuity.db"
    supervisor = LocalOwnerSupervisor()
    issued = supervisor.acquire_owner(database, "owner-continuity")
    supervisor.complete_handshake(derive_owner_response(issued.secret_hex))
    owner = supervisor.application_owner()
    bus = LocalCommandBus(supervisor)
    campaign_id, dm_session_id = _construct_rift(
        bus,
        owner,
        tmp_path,
        lane="quick_start",
        companion_ids=(),
    )
    initial = owner.snapshot(campaign_id)
    assert initial.continuity.current_scene is not None
    assert initial.continuity.current_scene["origin"] == "accepted_initial_state"
    assert initial.continuity.current_scene["summary"] is None

    turn_id = owner.current_open_turn(campaign_id)
    assert turn_id is not None
    basis_message_id = "message-continuity-memory"
    owner.append_agent_message(
        session_id=dm_session_id,
        message_id=basis_message_id,
        kind=SessionMessageKind.USER,
        content={"text": "I ask where Alrya is now."},
    )
    mcp = StatelessMcpMechanics(owner)
    player = initial.participants[0]
    progress_result = mcp.commit_campaign_file(
        campaign_id,
        session_id=dm_session_id,
        turn_id=turn_id,
        basis_message_id=basis_message_id,
        parameters={
            "state_change": "changed",
            "state_change_summary": "Alrya's location and Plan status changed.",
            "entities": [
                {
                    "entity_id": "alrya",
                    "entity_kind": "person",
                    "name": "Alrya",
                    "attributes": {
                        "current_location": "capital-hospital",
                        "condition": "confined-to-hospital",
                    },
                    "visibility": "player_known",
                    "known_by": ["player", "alrya"],
                },
                {
                    "entity_id": player.participant_id,
                    "entity_kind": "person",
                    "name": "Mara Venn",
                    "attributes": {"current_location": "capital-hospital"},
                    "visibility": "player_known",
                    "known_by": ["player"],
                    "source_ref": player.participant_id,
                }
            ],
            "plan_progress": [
                {
                    "plan_item_id": "arrival-and-waystation",
                    "progress_status": "deferred",
                    "progress_summary": "The party followed the hospital lead instead.",
                }
            ],
        },
    )
    assert progress_result["change_count"] == 3

    # Push Alrya beyond the 128-record automatic provider projection. Long campaigns must still
    # let the GM discover her without already knowing her Campaign State record identity.
    for batch_start in range(0, 130, 64):
        batch_end = min(batch_start + 64, 130)
        owner.update_campaign_file(
            session_id=dm_session_id,
            update_id=f"fill-campaign-file-{batch_start}",
            updates=tuple(
                {
                    "operation": "record_memory",
                    "fact_id": f"000-archive-{index:03d}",
                    "category": "continuity",
                    "text": f"Archive ledger entry {index} remains unchanged.",
                    "visibility": "gm_private",
                    "known_by": [],
                }
                for index in range(batch_start, batch_end)
            ),
            basis_message_ids=(basis_message_id,),
            turn_id=turn_id,
        )
    projected_records = owner.snapshot(campaign_id).continuity.facts[:128]
    assert not any(item.get("record_id") == "alrya" for item in projected_records)

    discovered = owner.search_campaign_knowledge(
        campaign_id,
        query="Where is Alrya?",
        sources=("campaign_state",),
        limit=5,
    )
    assert discovered["query"] == "Where is Alrya?"
    assert discovered["truncated"] is False
    assert discovered["matches"] == [
        {
            "source": "campaign_state",
            "path": "alrya",
            "kind": "entity",
            "label": "Alrya",
            "visibility": "player_known",
            "provenance": {
                "campaign_id": campaign_id,
                "campaign_revision": owner.snapshot(campaign_id).revision,
                "record_id": "alrya",
            },
        }
    ]
    owner.set_admin_full_audit(True)
    mcp_discovered = mcp.search_knowledge(
        campaign_id,
        query="Alrya",
        sources=("campaign_state",),
        limit=2,
    )
    assert mcp_discovered["matches"][0]["path"] == "alrya"
    assert any(
        item.get("kind") == "mcp_search_campaign_knowledge"
        and item.get("campaign_id") == campaign_id
        for item in owner.admin_full_audit_projection()["traces"]
    )
    exact_alrya = owner.read_campaign_reference(
        campaign_id,
        source="campaign_state",
        path="alrya",
    )
    assert "capital-hospital" in str(exact_alrya["content"])
    assert "confined-to-hospital" in str(exact_alrya["content"])

    character_baseline = owner.read_campaign_reference(
        campaign_id,
        source="participant",
        path=player.participant_id,
    )
    campaign_character = owner.read_campaign_reference(
        campaign_id,
        source="campaign_state",
        path=player.participant_id,
    )
    assert character_baseline["path"] == player.participant_id
    assert "Mara Venn" not in character_baseline["content"]
    assert "Mara Venn" in campaign_character["content"]
    assert "Campaign State is current campaign truth" in campaign_character["precedence"]

    bounded = owner.search_campaign_knowledge(
        campaign_id,
        query="archive ledger",
        sources=("campaign_state",),
        limit=3,
    )
    assert len(bounded["matches"]) == 3
    assert bounded["truncated"] is True
    assert all(item["visibility"] == "gm_private" for item in bounded["matches"])
    assert all("content" not in item for item in bounded["matches"])

    missing = owner.search_campaign_knowledge(
        campaign_id,
        query="person who definitely does not exist",
        limit=3,
    )
    assert missing["matches"] == []
    assert missing["truncated"] is False

    overview = owner.read_campaign_reference(
        campaign_id,
        source="campaign_setting",
        path="locations/verdant-margin.md#overview",
    )
    assert overview["selection"] == {
        "kind": "markdown_section",
        "heading": "Overview",
    }
    assert "forest where the Rift's energy has accelerated growth" in overview["content"]
    assert "Returner supply cache" not in overview["content"]
    assert "## Hooks" not in overview["content"]
    assert overview["interpretation"] == (
        "Reusable Setting baseline, not current Campaign State. Examples describe what can "
        "occur; they do not establish a current object, route, event, or prior action."
    )

    default_location = owner.read_campaign_reference(
        campaign_id,
        source="campaign_setting",
        path="locations/verdant-margin.md",
    )
    assert default_location["selection"] == {
        "kind": "markdown_section",
        "heading": "Overview",
        "default": True,
    }
    assert "forest where the Rift's energy has accelerated growth" in default_location["content"]
    assert "Returner supply cache" not in default_location["content"]
    assert "## Hooks" not in default_location["content"]
    assert {
        "heading": "Hooks",
        "path": "locations/verdant-margin.md#hooks",
        "visibility": "gm_private_preparation",
    } in default_location["available_sections"]

    hooks = owner.read_campaign_reference(
        campaign_id,
        source="campaign_setting",
        path="locations/verdant-margin.md#hooks",
    )
    assert hooks["selection"] == {
        "kind": "markdown_section",
        "heading": "Hooks",
    }
    assert "Returner supply cache" in hooks["content"]
    assert hooks["visibility"] == "gm_private_preparation"

    location_matches = owner.search_campaign_knowledge(
        campaign_id,
        query="Verdant Margin",
        sources=("campaign_setting",),
        limit=8,
    )
    assert any(
        item["path"] == "locations/verdant-margin.md#overview"
        for item in location_matches["matches"]
    )
    assert not any(item["path"].endswith("#hooks") for item in location_matches["matches"])
    hook_matches = owner.search_campaign_knowledge(
        campaign_id,
        query="humanoid barefoot Heartwood",
        sources=("campaign_setting",),
        limit=5,
    )
    assert any(
        item["path"] == "locations/verdant-margin.md#hooks"
        for item in hook_matches["matches"]
    )
    assert mcp.commit_campaign_file(
        campaign_id,
        session_id=dm_session_id,
        turn_id=turn_id,
        basis_message_id=basis_message_id,
        parameters={
            "state_change": "changed",
            "state_change_summary": "Alrya's location and Plan status changed.",
            "entities": [
                {
                    "entity_id": "alrya",
                    "entity_kind": "person",
                    "name": "Alrya",
                    "attributes": {
                        "current_location": "capital-hospital",
                        "condition": "confined-to-hospital",
                    },
                    "visibility": "player_known",
                    "known_by": ["player", "alrya"],
                },
                {
                    "entity_id": player.participant_id,
                    "entity_kind": "person",
                    "name": "Mara Venn",
                    "attributes": {"current_location": "capital-hospital"},
                    "visibility": "player_known",
                    "known_by": ["player"],
                    "source_ref": player.participant_id,
                }
            ],
            "plan_progress": [
                {
                    "plan_item_id": "arrival-and-waystation",
                    "progress_status": "deferred",
                    "progress_summary": "The party followed the hospital lead instead.",
                }
            ],
        },
    ) == progress_result
    duplicate_basis_message_id = "message-continuity-memory-restated"
    owner.append_agent_message(
        session_id=dm_session_id,
        message_id=duplicate_basis_message_id,
        kind=SessionMessageKind.USER,
        content={"text": "I restate what we already know."},
    )
    revision_before_restatement = owner.snapshot(campaign_id).revision
    duplicate_result = mcp.commit_campaign_file(
        campaign_id,
        session_id=dm_session_id,
        turn_id=turn_id,
        basis_message_id=duplicate_basis_message_id,
        parameters={
            "state_change": "unchanged",
            "state_change_summary": "The response only restates existing campaign truth.",
        },
    )
    assert duplicate_result["status"] == "campaign_file_unchanged"
    assert duplicate_result["state_changed"] is False
    assert owner.snapshot(campaign_id).revision == revision_before_restatement
    owner.complete_turn(campaign_id, turn_id)

    current_reference = owner.read_campaign_reference(
        campaign_id,
        source="campaign_state",
        path="alrya",
    )
    assert "capital-hospital" in str(current_reference["content"])
    assert not any(
        message.content.get("kind")
        in {"campaign_scene", "campaign_secret_reveal", "campaign_memory"}
        for message in owner.session_context(dm_session_id).messages
    )
    export_path = tmp_path / "continuity.zip"
    owner.export_campaign(
        operation_id="export-continuity",
        campaign_id=campaign_id,
        target=export_path,
    )
    supervisor.shutdown()

    with AuthorityOwner.open(database, "owner-continuity-reopen") as reopened:
        facts = {
            str(item["record_id"]): item for item in reopened.snapshot(campaign_id).continuity.facts
        }
        assert facts["alrya"]["attributes"]["current_location"] == "capital-hospital"
        assert reopened.snapshot(campaign_id).continuity.plan_progress[0]["status"] == "deferred"

    recovery_database = tmp_path / "continuity-recovered.db"
    with AuthorityOwner.open(recovery_database, "owner-continuity-recovery") as recovered:
        recovered.recover_campaign(
            recovery_id="recover-continuity",
            source=export_path,
        )
        recovered_facts = {
            str(item["record_id"]): item
            for item in recovered.snapshot(campaign_id).continuity.facts
        }
        assert recovered_facts["alrya"]["attributes"]["condition"] == (
            "confined-to-hospital"
        )


def test_campaign_file_batch_is_validated_atomic_and_replay_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "campaign-file-batch.db"
    supervisor = LocalOwnerSupervisor()
    issued = supervisor.acquire_owner(database, "owner-campaign-file-batch")
    supervisor.complete_handshake(derive_owner_response(issued.secret_hex))
    owner = supervisor.application_owner()
    bus = LocalCommandBus(supervisor)
    campaign_id, dm_session_id = _construct_rift(
        bus,
        owner,
        tmp_path,
        lane="quick_start",
        companion_ids=(),
    )
    turn_id = owner.current_open_turn(campaign_id)
    assert turn_id is not None
    basis_message_id = "message-campaign-file-batch"
    owner.append_agent_message(
        session_id=dm_session_id,
        message_id=basis_message_id,
        kind=SessionMessageKind.USER,
        content={"text": "I ask after Alrya and accept Mara's promise."},
    )
    before = owner.snapshot(campaign_id)
    valid_updates = (
        {
            "operation": "record_entity",
            "entity_id": "alrya",
            "entity_kind": "person",
            "name": "Alrya",
            "attributes": {
                "current_location": "capital-hospital",
                "condition": "confined-to-hospital",
            },
            "visibility": "player_known",
            "known_by": ["player", "alrya"],
        },
        {
            "operation": "record_memory",
            "fact_id": "mara-hospital-promise",
            "category": "promise",
            "text": "Mara promised to send word from the capital hospital.",
            "visibility": "player_known",
            "known_by": ["player", "mara"],
        },
    )

    with pytest.raises(CommandRejected):
        owner.update_campaign_file(
            session_id=dm_session_id,
            update_id="campaign-file-invalid-batch",
            updates=(*valid_updates[:1], {"operation": "record_memory"}),
            basis_message_ids=(basis_message_id,),
            turn_id=turn_id,
        )
    assert owner.snapshot(campaign_id).revision == before.revision
    assert "alrya" not in {
        item["record_id"] for item in owner.snapshot(campaign_id).continuity.facts
    }

    def fail_before_commit() -> None:
        raise RuntimeError("synthetic Campaign File transaction interruption")

    monkeypatch.setattr(owner._store, "_before_commit", fail_before_commit)  # noqa: SLF001
    with pytest.raises(RuntimeError, match="transaction interruption"):
        owner.update_campaign_file(
            session_id=dm_session_id,
            update_id="campaign-file-atomic-batch",
            updates=valid_updates,
            basis_message_ids=(basis_message_id,),
            turn_id=turn_id,
        )
    rolled_back = owner.snapshot(campaign_id)
    assert rolled_back.revision == before.revision
    assert {item["record_id"] for item in rolled_back.continuity.facts}.isdisjoint(
        {"alrya", "mara-hospital-promise"}
    )

    monkeypatch.setattr(owner._store, "_before_commit", lambda: None)  # noqa: SLF001
    admitted = owner.update_campaign_file(
        session_id=dm_session_id,
        update_id="campaign-file-atomic-batch",
        updates=valid_updates,
        basis_message_ids=(basis_message_id,),
        turn_id=turn_id,
    )
    assert admitted["status"] == "campaign_file_updated"
    assert admitted["change_count"] == 2
    assert admitted["resulting_revision"] == before.revision + 2
    assert owner.update_campaign_file(
        session_id=dm_session_id,
        update_id="campaign-file-atomic-batch",
        updates=valid_updates,
        basis_message_ids=(basis_message_id,),
        turn_id=turn_id,
    ) == admitted
    final = owner.snapshot(campaign_id)
    assert final.revision == before.revision + 2
    assert {item["record_id"] for item in final.continuity.facts} >= {
        "alrya",
        "mara-hospital-promise",
    }
    supervisor.shutdown()
