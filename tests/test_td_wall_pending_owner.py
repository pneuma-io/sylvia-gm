"""Private wall inputs stay internal while admitted actions are still in flight."""

from asyncio import CancelledError
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest

from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CatalogTool
from sylvia_gm.domain.authority import MechanicOutcome, PlayerProjectionKind, thaw_json_value
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.test_td_grid_edge_owner import NS, candidate  # noqa: F401
from tests.test_td_wall_authoring import edit, segment, setup_owner, state


@pytest.mark.parametrize("outcome", ["commit", "cancel", "refuse"])
def test_inflight_wall_inputs_are_private_across_player_and_replay_readers(
    candidate,  # noqa: F811
    tmp_path,
    monkeypatch,
    outcome,  # noqa: F811
):
    database = setup_owner(tmp_path, candidate)
    entered, release = Event(), Event()
    secret = "SYNTHETIC-INFLIGHT-PRIVATE-WALL"
    captured = []
    with AuthorityOwner.open(database, "identity-owner") as owner:
        anchor_piece = segment(visible=False)
        anchor_piece["metadata"]["private"] = "SYNTHETIC-PRIVATE-ANCHOR"
        edit(owner, "draw", segments=[anchor_piece], key="anchor")
        before = owner.snapshot("identity")
        scene = state(owner)["spatial_scene"]
        anchor = scene["geometry"][0]
        piece = segment(3, 4, visible=False)
        piece["metadata"]["nested"] = {"private": [{"value": secret, "selected": anchor["id"]}]}
        command = ActionCommand(
            campaign_id="identity",
            action_id="inflight",
            idempotency_key="inflight",
            owner_id="identity-owner",
            writer=before.writer,
            expected_revision=before.revision,
            turn_id="turn",
            package_id=NS,
            tool_name="author_wall",
            parameters={
                "map_id": scene["map"]["id"],
                "operation": "extend",
                "expected_revision": 0 if outcome == "refuse" else scene["map"]["revision"],
                "geometry_ids": [anchor["id"]],
                "segments": [piece],
            },
        )
        admitted = owner._admitted_tool

        def held_tool(*args, **kwargs):
            tool = admitted(*args, **kwargs)

            def execute(context):
                captured.append(context)
                # Cancellation occurs after the actual tool staged its full mutation.
                if outcome == "cancel":
                    tool.execute(context)
                entered.set()
                assert release.wait(timeout=30)
                if outcome == "cancel":
                    raise CancelledError("synthetic in-flight cancellation")
                return tool.execute(context)

            return replace(tool, execute=execute)

        with monkeypatch.context() as patch:
            patch.setattr(owner, "_admitted_tool", held_tool)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(owner.execute_action, command)
                try:
                    assert entered.wait(timeout=30)
                    views = (
                        owner.player_projection("identity"),
                        owner.player_projection_page("identity", after_sequence=0),
                        owner._provisional_projection("identity"),
                        owner.canonical_replay("identity", acknowledged_sequence=0).provisional,
                    )
                    pending_store = owner._pending_tools[("identity", "inflight")]
                    during = owner.snapshot("identity")
                finally:
                    release.set()
                if outcome == "cancel":
                    with pytest.raises(CancelledError):
                        future.result(timeout=30)
                elif outcome == "refuse":
                    with pytest.raises(RulesetPreconditionRejected, match="map changed"):
                        future.result(timeout=30)
                else:
                    receipt = future.result(timeout=30)
        after = owner.snapshot("identity")
        assert during == before  # No uncommitted geometry/resource/revision reaches storage.
        assert thaw_json_value(captured[0].parameters) == command.parameters
        for view in views:
            pending = [item for item in view if item.kind is PlayerProjectionKind.PENDING_TOOL]
            assert len(pending) == 1
            payload = pending[0].safe_payload
            assert secret not in str(payload)
            assert anchor["id"] not in str(payload)
            assert anchor["edge_layer_id"] not in str(payload)
            assert payload["parameters"] == {}
            assert payload["tool_name"] == "author_wall"
            assert pending[0].provisional
        assert pending_store.safe_payload["parameters"] == {}
        assert not owner._pending_tools
        assert not any(
            item.kind is PlayerProjectionKind.PENDING_TOOL
            for item in owner.player_projection("identity")
        )
        assert not owner.canonical_replay("identity", acknowledged_sequence=0).provisional
        assert secret not in str(owner.canonical_replay("identity", acknowledged_sequence=0))
        if outcome == "commit":
            assert after.revision == before.revision + 1
            assert secret in str(after.actions[-1].parameters)
            assert secret in str(receipt.result)
            assert owner.execute_action(command) == receipt
        else:
            assert after == before
            assert secret not in str(after)
    with AuthorityOwner.open(database, "identity-owner") as owner:
        assert owner.snapshot("identity") == after
        assert not owner.canonical_replay("identity", acknowledged_sequence=0).provisional
        assert secret not in str(owner.canonical_replay("identity", acknowledged_sequence=0))
        assert secret not in str(owner.player_projection("identity"))
        if outcome == "commit":
            assert owner.execute_action(command) == receipt
            assert owner.snapshot("identity") == after


def test_pending_public_package_parameters_remain_available(tmp_path):
    from tests import test_alpha_f2_event_protocol as fixture

    entered, release = Event(), Event()

    def blocking(context):
        entered.set()
        assert release.wait(timeout=30)
        return MechanicOutcome(
            result={"status": "committed", "amount": context.parameters["amount"]}
        )

    with fixture._open_owner(tmp_path / "public.sqlite3") as owner:
        owner.register_tool(
            CatalogTool(
                package_id="synthetic.clockwork",
                package_version="1.0.0",
                package_sha256=fixture.SYNTHETIC_SHA,
                name="blocking",
                capability="synthetic.counter",
                input_schema=fixture._schema(),
                execute=blocking,
            )
        )
        campaign = fixture._campaign(owner)
        command = fixture._action(campaign, action_id="public-pending", tool_name="blocking")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(owner.execute_action, command)
            try:
                assert entered.wait(timeout=30)
                views = (
                    owner.player_projection(campaign),
                    owner.player_projection_page(campaign, after_sequence=0),
                    owner._provisional_projection(campaign),
                    owner.canonical_replay(campaign, acknowledged_sequence=0).provisional,
                )
            finally:
                release.set()
            future.result(timeout=30)
        for view in views:
            pending = [item for item in view if item.kind is PlayerProjectionKind.PENDING_TOOL]
            assert len(pending) == 1
            assert pending[0].safe_payload["parameters"] == {"amount": 1}
