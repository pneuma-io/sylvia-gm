"""Resolved-model GM style selection without changing persisted authority or Builder."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from sylvia_gm.agent._provider_session import _GM_MODEL_NARRATION_BLOCK, ProviderSessionAdapter
from sylvia_gm.agent._working_context import WorkingContextProvider
from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import PackageLock, WriterRole
from sylvia_gm.domain.session import SessionMessageKind
from sylvia_gm.provider._base import ModelInfo, ProviderResponse
from tests.test_alpha_f4_provider_session_adapter import (
    _OwnerProjection,
    _provider_input,
    _ScriptedProvider,
)
from tests.test_working_context import Model


def model(identity: str, wire: str, name: str = "GPT-6.1 Sol") -> ModelInfo:
    return ModelInfo(
        id=identity,
        name=name,
        provider_name="ChatGPT",
        provider_key="chatgpt_subscription",
        provider_mode="chatgpt_subscription",
        provider_model_id=wire,
    )


def assembled(info: ModelInfo | None, *, source: Any = None) -> list[dict[str, object]]:
    provider = _ScriptedProvider([])
    if info is not None:
        provider.model_info = info
    return ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()), provider, owner_id="model-narration-owner"
    )._messages(source or _provider_input())


@pytest.mark.parametrize("wire", ["gpt-6.1-sol", "gpt-6-sol", "gpt-5.6-sol"])
def test_exact_resolved_identity_and_wire_add_separate_block(wire: str) -> None:
    messages = assembled(model(f"chatgpt-subscription/{wire}", wire, "Arbitrary display label"))
    assert messages[1] == {"role": "system", "content": _GM_MODEL_NARRATION_BLOCK}
    assert sum(item.get("content") == _GM_MODEL_NARRATION_BLOCK for item in messages) == 1
    assert "Prepared events are not facts until played" in str(messages[0]["content"])
    assert "player may refuse or abandon the story" in str(messages[0]["content"])


@pytest.mark.parametrize(
    "identity,wire",
    [
        ("chatgpt-subscription/gpt-6-luna", "gpt-6-luna"),
        ("opencode-go/minimax-m3", "minimax-m3"),
        ("chatgpt-subscription/gpt-6.1-sol", "gpt-6-luna"),
        ("chatgpt-subscription/gpt-6.1-sol-preview", "gpt-6.1-sol"),
        ("chatgpt-subscription/alias", "gpt-6.1-sol"),
        ("custom/gpt-6.1-sol", "gpt-6.1-sol"),
        ("gpt-6.1-sol", "gpt-6.1-sol"),
        ("chatgpt-subscription/gpt-6.1-sol", "vendor/gpt-6.1-sol"),
        ("chatgpt-subscription/GPT-6.1-SOL", "gpt-6.1-sol"),
        ("chatgpt-subscription/gpt-6.1-sol", "gpt-6.1-sol-preview"),
    ],
)
def test_unknown_alias_mismatch_or_misleading_display_name_gets_no_block(
    identity: str, wire: str
) -> None:
    assert all(
        item.get("content") != _GM_MODEL_NARRATION_BLOCK
        for item in assembled(model(identity, wire))
    )


def test_missing_runtime_metadata_gets_no_block() -> None:
    assert all(item.get("content") != _GM_MODEL_NARRATION_BLOCK for item in assembled(None))


@pytest.mark.parametrize(
    "saved,actual", [("gpt-6-luna", "gpt-6.1-sol"), ("gpt-6.1-sol", "gpt-6-luna")]
)
def test_once_effective_provider_overrides_saved_session_in_both_directions(
    saved: str, actual: str
) -> None:
    source = _provider_input()
    source = replace(
        source, session=replace(source.session, model_id=f"chatgpt-subscription/{saved}")
    )
    result = assembled(model(f"chatgpt-subscription/{actual}", actual), source=source)
    assert (result[1].get("content") == _GM_MODEL_NARRATION_BLOCK) is (actual == "gpt-6.1-sol")
    assert source.session.model_id == f"chatgpt-subscription/{saved}"


def test_current_switches_reassemble_without_stale_or_duplicate_block() -> None:
    provider = _ScriptedProvider([])
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()), provider, owner_id="switch-owner"
    )
    for wire in ("gpt-6.1-sol", "gpt-6-luna", "gpt-5.6-sol", "gpt-6-luna", "gpt-6-sol"):
        provider.model_info = model(f"chatgpt-subscription/{wire}", wire)
        prepared = adapter._messages(_provider_input())
        assert sum(item.get("content") == _GM_MODEL_NARRATION_BLOCK for item in prepared) == (
            wire != "gpt-6-luna"
        )


def test_saved_contract_reopens_exactly_and_style_stays_request_owned(tmp_path: Path) -> None:
    path = tmp_path / "contract.sqlite3"
    source = _provider_input()
    with AuthorityOwner.open(path, "saved-style-owner") as owner:
        owner.create_campaign(
            CampaignCreate(
                display_name="Style checkpoint",
                writer=WriterRole.DM,
                package_lock=PackageLock(entries=()),
                turn_id="turn",
                campaign_id="campaign-provider",
            )
        )
        owner.create_agent_session(
            campaign_id="campaign-provider",
            role=WriterRole.DM,
            provider_id="chatgpt_subscription",
            model_id="chatgpt-subscription/gpt-6.1-sol",
            session_id=source.session.session_id,
        )
        owner.append_agent_message(
            session_id=source.session.session_id,
            message_id="immutable-contract",
            kind=SessionMessageKind.EVIDENCE,
            content={
                "kind": "gm_system_contract",
                "version": 1,
                "contract": "UNCHANGED SAVED GM CONTRACT",
            },
        )
    with AuthorityOwner.open(path, "reopened-style-owner") as owner:
        history = owner.session_context(source.session.session_id)
        persisted_before = history.messages
        for wire in ("gpt-6.1-sol", "gpt-6-luna"):
            messages = assembled(
                model(f"chatgpt-subscription/{wire}", wire),
                source=replace(source, session=history.session, history=history),
            )
            assert messages[0] == {"role": "system", "content": "UNCHANGED SAVED GM CONTRACT"}
            assert sum(item.get("content") == _GM_MODEL_NARRATION_BLOCK for item in messages) == (
                wire == "gpt-6.1-sol"
            )
        assert owner.session_context(source.session.session_id).messages == persisted_before


def test_compaction_preserves_style_once_and_current_authority_contract() -> None:
    provider = Model(responses=[ProviderResponse(content="Earlier events summarized.")])
    provider.model_info = model("chatgpt-subscription/gpt-6.1-sol", "gpt-6.1-sol")
    source = _provider_input()
    old = source.history.messages[0]
    history = replace(
        source.history,
        messages=tuple(
            replace(
                old,
                sequence=i + 1,
                message_id=f"turn-{i}",
                content={"text": f"Inspect the lantern {i}."},
            )
            for i in range(28)
        ),
    )
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()), provider, owner_id="compact-style"
    )
    original = adapter._messages(replace(source, history=history))
    pinned = [item for item in original if item["role"] == "system"]
    original = (
        pinned
        + [
            item
            for i in range(28)
            for item in (
                {"role": "user", "content": f"Inspect the lantern {i}."},
                {"role": "assistant", "content": f"The lantern remains lit at step {i}."},
            )
        ]
        + [{"role": "user", "content": "Inspect the current lantern."}]
    )

    async def save(_record: dict[str, Any]) -> None:
        pass

    working = WorkingContextProvider(provider, save=save)
    projected = asyncio.run(working.prepare(original, force=True))
    assert projected[0] == original[0]
    assert sum(item.get("content") == _GM_MODEL_NARRATION_BLOCK for item in projected) == 1
    assert projected[1] == original[1]
    # A reopened working context uses the new request's current system messages.
    unmatched = model("chatgpt-subscription/gpt-6-luna", "gpt-6-luna")
    provider.model_info = unmatched
    fresh = assembled(unmatched)
    changed = [item for item in fresh if item["role"] == "system"] + [
        item for item in original if item["role"] != "system"
    ]
    restored = WorkingContextProvider(provider, records=working.records, save=save)
    switched = restored.project(changed)
    assert switched[0] == original[0]
    assert all(item.get("content") != _GM_MODEL_NARRATION_BLOCK for item in switched)
    assert provider.calls
    assert all(
        item.get("content") != _GM_MODEL_NARRATION_BLOCK
        for call in provider.calls
        for item in call["messages"]
    )


def test_builder_role_does_not_receive_gm_style_block() -> None:
    source = _provider_input()
    session = replace(source.session, role=WriterRole.BUILDER, campaign_id=None)
    source = replace(source, session=session, history=replace(source.history, session=session))
    assert all(
        item.get("content") != _GM_MODEL_NARRATION_BLOCK
        for item in assembled(
            model("chatgpt-subscription/gpt-6.1-sol", "gpt-6.1-sol"), source=source
        )
    )


def test_response_correction_retry_keeps_block_once_and_old_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests import test_alpha_f4_provider_session_adapter as existing

    captured = []
    original = existing._ScriptedProvider

    def configured(rounds):
        provider = original(rounds)
        provider.model_info = model("chatgpt-subscription/gpt-6.1-sol", "gpt-6.1-sol")
        captured.append(provider)
        return provider

    monkeypatch.setattr(existing, "_ScriptedProvider", configured)
    existing.test_old_dialogue_shape_is_rejected_before_write_then_same_gm_corrects_it(
        [{"kind": "npc_dialogue", "speaker_id": "anonymous", "text": 'Rowan nods. "Proceed."'}]
    )
    assert len(captured[0].calls) == 2
    for messages, _ in captured[0].calls:
        assert messages[0] == {"role": "system", "content": "UNCHANGED OLD GM"}
        assert messages[1] == {"role": "system", "content": _GM_MODEL_NARRATION_BLOCK}
        assert sum(item.get("content") == _GM_MODEL_NARRATION_BLOCK for item in messages) == 1


def test_mismatched_provider_key_does_not_infer_sol_identity() -> None:
    info = model("chatgpt-subscription/gpt-6.1-sol", "gpt-6.1-sol")
    info.provider_key = "unrecognized_proxy"
    assert all(item.get("content") != _GM_MODEL_NARRATION_BLOCK for item in assembled(info))


@pytest.mark.parametrize("wire", ["gpt-6.1-sol", "gpt-6-luna"])
def test_narration_only_retry_follows_actual_provider_without_exposing_tools(
    monkeypatch: pytest.MonkeyPatch, wire: str
) -> None:
    from tests import test_alpha_f4_provider_session_adapter as existing

    captured = []
    original = existing._ScriptedProvider

    def configured(rounds):
        provider = original(rounds)
        provider.model_info = model(f"chatgpt-subscription/{wire}", wire)
        captured.append(provider)
        return provider

    monkeypatch.setattr(existing, "_ScriptedProvider", configured)
    existing.test_retry_adapter_reconstructs_authoritative_context_and_never_exposes_tools()
    messages, tools = captured[0].calls[0]
    assert tools is None
    assert sum(item.get("content") == _GM_MODEL_NARRATION_BLOCK for item in messages) == (
        wire == "gpt-6.1-sol"
    )
