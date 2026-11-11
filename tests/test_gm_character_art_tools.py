"""Ordinary GM image tools reach real Lite campaign targets through the provider adapter."""

import json

import pytest

from sylvia_gm.agent._image_art_tools import image_art_model_tools
from sylvia_gm.agent._provider_session import ProviderSessionAdapter, _tool_alias
from sylvia_gm.agent._session_loop import ProviderControl, ProviderEventKind, SessionProviderInput
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.image_art import ImageArt
from sylvia_gm.application.ready_campaigns import ready_campaign_catalog, start_ready_campaign
from sylvia_gm.domain.authority import PackageRole
from sylvia_gm.domain.session import SessionMessageKind
from sylvia_gm.provider._base import ProviderStreamChunk
from tests.test_alpha_f4_provider_session_adapter import _ScriptedProvider, _tool_operations
from tests.test_image_art import Images


def tool_round(name, arguments, identity):
    return [
        ProviderStreamChunk(
            tool_call_delta={
                "index": 0,
                "id": identity,
                "function": {"name": name, "arguments": json.dumps(arguments)},
            },
            finish_reason="tool_calls",
        )
    ]


@pytest.mark.parametrize("mode", ["review", "auto"])
def test_gm_discovers_new_enemy_and_requests_complete_art_without_format_juggling(
    tmp_path, monkeypatch, mode
):
    database = tmp_path / "gm-art.db"
    images = Images()
    with AuthorityOwner.open(database, "gm-art-owner") as owner:
        template = next(row for row in ready_campaign_catalog() if row["id"] == "last-cart-through")
        started = start_ready_campaign(
            owner,
            tmp_path,
            operation_id="start",
            template_id=template["id"],
            expected_content_sha256=template["content_sha256"],
            display_name="GM artwork",
            provider_id="scripted",
            model_id="scripted/model",
            model_guidance_override=True,
        )
        campaign_id, session_id = started["campaign_id"], started["dm_session_id"]
        primary = next(
            entry
            for entry in owner.snapshot(campaign_id).package_lock.entries
            if entry.role is PackageRole.PRIMARY
        )
        assert primary.package_id == "sylvia.srd-lite.bg3"
        owner.append_agent_message(
            session_id=session_id,
            message_id="encounter",
            kind=SessionMessageKind.USER,
            content={"text": "Prepare artwork for the glass sentinel we just encountered."},
        )
        owner.update_campaign_file(
            session_id=session_id,
            update_id="introduced-enemies",
            basis_message_ids=("encounter",),
            updates=tuple(
                {
                    "operation": "record_entity",
                    "entity_id": identity,
                    "entity_kind": "creature",
                    "name": name,
                    "attributes": {},
                    "visibility": visibility,
                    "known_by": known_by,
                }
                for identity, name, visibility, known_by in (
                    ("glass-sentinel", "Glass Sentinel", "player_known", ["player"]),
                    ("unseen-keeper", "Unseen Keeper", "gm_private", ["gm"]),
                )
            ),
        )
        art = ImageArt(owner, adapters=(images,), settings=lambda: {"mode": mode})
        monkeypatch.setattr(owner, "_image_art", art)
        mechanics = StatelessMcpMechanics(owner)
        discoveries = []

        def discover(identity):
            tools = mechanics.list_tools(
                campaign_id,
                purpose="provider_inference",
                discovery_id=identity,
                session_id=session_id,
            )
            discoveries.append(tools)
            return tools

        aliases = {
            tool.name: _tool_alias(tool)
            for tool in (*discover("fixture-catalog"), *image_art_model_tools(owner))
        }
        provider = _ScriptedProvider(
            [
                tool_round(aliases["get_image_art"], {}, "inspect-art"),
                tool_round(
                    aliases["request_image_art"],
                    {
                        "operation_id": "sentinel-art",
                        "target_id": "glass-sentinel",
                        "purpose": "character",
                        "name": "Glass Sentinel",
                        "appearance": (
                            "Tall glass construct, amber core, "
                            "chipped hands, no clothing"
                        ),
                        "brief": "Keep its established glass construction and amber core",
                    },
                    "prepare-art",
                ),
                tool_round(
                    aliases["update_campaign_file"],
                    {
                        "state_change": "unchanged",
                        "state_change_summary": (
                            "Only optional artwork was prepared; game state is unchanged."
                        ),
                        "revised_player_facing_response": [
                            {"kind": "narration", "text": "The artwork request is prepared."},
                        ],
                    },
                    "finish-art",
                ),
            ]
        )
        context = owner.session_context(session_id)
        state_before = owner.snapshot(campaign_id).state
        adapter = ProviderSessionAdapter(owner, provider, owner_id="gm-art-provider")
        events = tuple(
            adapter.for_turn(started["dm_turn_id"])(
                SessionProviderInput(
                    session=context.session,
                    history=context,
                    catalog=(),
                    control=ProviderControl(),
                    catalog_supplier=discover,
                    basis_message_id="encounter",
                    narrative_writer=lambda parameters: mechanics.commit_campaign_file(
                        campaign_id,
                        session_id=session_id,
                        basis_message_id="encounter",
                        turn_id=started["dm_turn_id"],
                        parameters=parameters,
                    ),
                )
            )
        )
        assert len(provider.calls) == 3
        assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
            "The artwork request is prepared.",
        ]
        assert owner.snapshot(campaign_id).state == state_before
        assert discoveries and any(tool.package_id == primary.package_id for tool in discoveries[0])
        tool_names = set(_tool_operations(provider.calls[0][1]))
        assert {"get_image_art", "request_image_art"} <= tool_names
        inspected = next(
            json.loads(message["content"])
            for message in provider.calls[1][0]
            if message.get("role") == "tool"
        )
        assert inspected["success"] is True
        targets = {target["id"] for target in inspected["result"]["targets"]}
        assert "glass-sentinel" in targets and "unseen-keeper" not in targets
        needed = {row["target_id"]: row["purposes"] for row in inspected["result"]["needed_art"]}
        assert set(needed["glass-sentinel"]) == {"full_body", "reference_sheet", "portrait"}
        proposed = next(
            json.loads(message["content"])
            for message in provider.calls[2][0]
            if message.get("tool_call_id") == "prepare-art"
        )
        assert proposed["success"] is True
        row = proposed["result"]
        if mode == "review":
            assert row["status"] == "proposed" and not images.calls
        for index, purpose in enumerate(("full_body", "reference_sheet", "portrait")):
            if mode == "review":
                art.generate(session_id, row["operation_id"])
            else:
                row = next(
                    item
                    for item in art.view(session_id)["requests"]
                    if item["target_id"] == "glass-sentinel" and item["purpose"] == purpose
                )
            assert art._futures[row["operation_id"]].result(timeout=20)["status"] == "succeeded"
            if mode == "review":
                adopted = art.adopt(session_id, row["operation_id"], expected_sha256=None)
                if index < 2:
                    row = adopted["next_request"]
        visuals = art.view(session_id)["character_visuals"]["glass-sentinel"]
        assert all(purpose in visuals for purpose in ("full_body", "reference_sheet", "portrait"))
        assert len(images.calls) == 3
        assert [len(request.reference_images) for request in images.calls] == [1, 2, 3]
        assert images.calls[1].reference_images[0].sha256 == visuals["full_body"]["sha256"]
        assert [image.sha256 for image in images.calls[2].reference_images[:2]] == [
            visuals["full_body"]["sha256"],
            visuals["reference_sheet"]["sha256"],
        ]
        assert len({request.reference_images[-1].sha256 for request in images.calls}) == 1
        assert (
            art.request(
                session_id,
                operation_id="reuse-sentinel",
                target_id="glass-sentinel",
                purpose="character",
                name="Glass Sentinel",
                brief="Reuse existing artwork",
            )["status"]
            == "already_available"
        )
        assert len(images.calls) == 3
        art.frame(
            session_id,
            "glass-sentinel",
            visuals["portrait"]["sha256"],
            {
                "x": 0.45,
                "y": 0.3,
                "zoom": 1.3,
            },
        )
        expected = art.view(session_id)["character_visuals"]["glass-sentinel"]
    with AuthorityOwner.open(database, "gm-art-reopened") as owner:
        art = ImageArt(owner)
        try:
            restored = art.view(session_id)["character_visuals"]["glass-sentinel"]
            assert restored == expected
            assert not art.adapters and not art._futures
        finally:
            art.close()
