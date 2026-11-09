"""Owning re-arm and GM-consumer coverage for the coordinator's technical reference.

The activity and scene facts are coordinator-supplied technical inputs within
the owner-approved BREAK CONTACT scope, not owner-selected encounter defaults.
"""

import hashlib
import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from sylvia_gm import dice
from sylvia_gm.agent._provider_session import ProviderSessionAdapter
from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.provider._base import ProviderStreamChunk
from sylvia_gm.spatial import SpatialEngine
from tests.admitted_library_fixture import (
    CANDIDATE_ID,
    admitted_fixture_owner,
    prepare_library_database,
)
from tests.test_alpha_f4_provider_session_adapter import _ScriptedProvider
from tests.test_td_break_contact import NS, attempt, move_out, record, scene_for, start
from tests.test_td_ordinary_movement import SESSION
from tests.test_td_rifleman_entry_boundaries import replay_all, step
from tests.td_source_fixture import candidate as candidate_fixture

candidate = candidate_fixture


def call(name, parameters, identity):
    return [
        ProviderStreamChunk(
            tool_call_delta={
                "index": 0,
                "id": identity,
                "function": {"name": name, "arguments": json.dumps(parameters)},
            },
            finish_reason="tool_calls",
        )
    ]


def state_record(owner, game):
    snapshot = owner.snapshot(game.campaign)
    return {
        "revision": snapshot.revision,
        "actor": deepcopy(game.actors[game.shooter]),
        "observers": {
            identity: deepcopy(game.actors[identity]) for identity in record(game)["observer_ids"]
        },
        "contacts": deepcopy(record(game)),
        "map_revision": game.world["spatial_scene"]["map"]["revision"],
        "state_sha256": hashlib.sha256(
            json.dumps(
                thaw_json_value(snapshot.state),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
    }


def refused_repeat(owner, game, activity):
    before = owner.snapshot(game.campaign)
    with patch.object(dice._rng, "randint") as draws, pytest.raises(CommandRejected):
        step(
            owner,
            game,
            "attempt_break_contact",
            {
                "actor": game.shooter,
                "activity_id": activity,
            },
        )
    assert not draws.called and owner.snapshot(game.campaign) == before


@pytest.mark.parametrize("mirrored", [False, True])
def test_real_loss_reacquisition_second_loss_rearms_and_restores(candidate, tmp_path, mirrored):
    scene = scene_for(candidate, renamed=True, mirrored=mirrored)
    activities = record(scene)["activities"]
    activities["second-quiet-metal"]["map_revision"] = 4
    third = deepcopy(activities["second-quiet-metal"])
    third["id"] = "after-rearmed-attempt"
    activities[third["id"]] = third
    initial_actor = deepcopy(scene.actors[scene.shooter])
    initial_observers = {
        identity: deepcopy(scene.actors[identity]) for identity in record(scene)["observer_ids"]
    }
    turn_roster = set(scene.world["turn_roster"])
    exhausted_observers = {
        identity: {**initial, "ap": 0} if identity in turn_roster else initial
        for identity, initial in initial_observers.items()
    }
    database = tmp_path / "authority.sqlite3"
    export = tmp_path / "rearmed.sylvia-gm-campaign"
    phases = []
    with admitted_fixture_owner(database, "aim-owner", candidate.read_bytes()) as (owner, admitted):
        game = start(owner, admitted, scene)
        move_out(owner, game)
        assert record(game)["epoch"] == 1 and record(game)["fully_lost"]
        attempt(owner, game, 1)
        assert record(game)["used_epoch"] == 1
        assert game.actors[game.shooter]["ap"] == 3
        assert record(game)["last_attempt"]["difficulty"] == 12
        refused_repeat(owner, game, "quiet-metal")
        first_evidence = {
            key: deepcopy(value["evidence"]) for key, value in record(game)["contacts"].items()
        }
        phases.append(state_record(owner, game))
        # Ordinary movement, not adopted state, genuinely restores direct sight.
        assert (
            step(
                owner,
                game,
                "move_actor",
                {
                    "actor": game.shooter,
                    "destination": [4, 4],
                },
            )
            == 0
        )
        assert game.entry_calls[-1][1].status.value == "committed"
        assert game.actors[game.shooter]["ap"] == 1
        assert game.actors[game.shooter]["center"] == initial_actor["center"]
        assert game.actors[game.shooter]["muzzle"] == initial_actor["muzzle"]
        assert not record(game)["fully_lost"] and not record(game)["all_blocked"]
        assert record(game)["epoch"] == record(game)["used_epoch"] == 1
        for identity, contact in record(game)["contacts"].items():
            assert contact["knowledge"] == "CONFIRMED"
            assert contact["last_exact"] == initial_actor["center"]
            assert contact["last_exact_revision"] == 3 and contact["region"] is None
            assert contact["evidence"] == first_evidence[identity]
        phases.append(state_record(owner, game))
        retained = deepcopy(record(game))
        first, second = (
            ("end_hostiles_turn", "end_allies_turn")
            if mirrored
            else ("end_allies_turn", "end_hostiles_turn")
        )
        step(owner, game, first, {})
        assert all(
            game.actors[identity]["ap"] == initial["max_ap"]
            for identity, initial in initial_observers.items()
        )
        step(owner, game, second, {})
        assert {
            identity: game.actors[identity] for identity in initial_observers
        } == exhausted_observers
        assert record(game) == retained
        assert game.actors[game.shooter]["ap"] == initial_actor["max_ap"] == 6
        assert (
            step(
                owner,
                game,
                "move_actor",
                {
                    "actor": game.shooter,
                    "destination": [4, 6],
                },
            )
            == 0
        )
        assert game.entry_calls[-1][1].status.value == "committed"
        assert game.actors[game.shooter]["ap"] == 4
        assert game.world["spatial_scene"]["map"]["revision"] == 4
        assert record(game)["epoch"] == 2 and record(game)["used_epoch"] == 1
        assert record(game)["fully_lost"] and record(game)["all_blocked"]
        for identity, contact in record(game)["contacts"].items():
            assert contact["knowledge"] == "LAST_KNOWN"
            assert contact["last_exact"] == initial_actor["center"]
            assert contact["evidence"] == first_evidence[identity]
        rearmed = owner.snapshot(game.campaign)
        phases.append(state_record(owner, game))
        owner.export_campaign("rearmed", game.campaign, export)
    with AuthorityOwner.open(database, "aim-owner") as owner:
        replay_all(owner, game, rearmed)
    restored_database = tmp_path / "restored.sqlite3"
    with AuthorityOwner.open(restored_database, "aim-owner") as owner:
        recovery = owner.recover_campaign(recovery_id="rearmed", source=export)
        assert owner.recover_campaign(recovery_id="rearmed", source=export) == recovery
        replay_all(owner, game, rearmed)
        game.owner = owner
        attempt(owner, game, 20, "second-quiet-metal")
        assert game.actors[game.shooter]["ap"] == 3
        assert record(game)["last_attempt"]["ap_spent"] == 1
        assert record(game)["last_attempt"]["difficulty"] == 12
        assert record(game)["epoch"] == record(game)["used_epoch"] == 2
        assert record(game)["activities"]["quiet-metal"]["consumed"]
        refused_repeat(owner, game, "second-quiet-metal")
        phases.append(state_record(owner, game))
        attempt(owner, game, 20, third["id"])
        assert game.actors[game.shooter]["ap"] == 1
        assert record(game)["last_attempt"]["ap_spent"] == 2
        assert record(game)["last_attempt"]["difficulty"] == 14
        actor = game.actors[game.shooter]
        assert actor["position_cell"] == [4, 6]
        assert actor["center"] == [4.5, 6.5, initial_actor["center"][2]]
        for field in ("weapon", "hp", "res", "stance"):
            assert actor[field] == initial_actor[field]
        # Only roster participants refresh/exhaust; other observers are unchanged.
        assert {
            identity: game.actors[identity] for identity in initial_observers
        } == exhausted_observers
        final = owner.snapshot(game.campaign)
        phases.append(state_record(owner, game))
        final_export = tmp_path / "spent-rearm.sylvia-gm-campaign"
        owner.export_campaign("spent-rearm", game.campaign, final_export)
    with AuthorityOwner.open(restored_database, "aim-owner") as owner:
        replay_all(owner, game, final)
    with AuthorityOwner.open(tmp_path / "fresh-final.sqlite3", "aim-owner") as owner:
        owner.recover_campaign(recovery_id="spent-rearm", source=final_export)
        replay_all(owner, game, final)
    (tmp_path / "rearm-proof.json").write_text(
        json.dumps(
            {
                "mirrored": mirrored,
                "phases": phases,
                "action_ids": [receipt.action_id for _, receipt in game.entry_calls],
                "rearmed_export_sha256": hashlib.sha256(export.read_bytes()).hexdigest(),
                "final_export_sha256": hashlib.sha256(final_export.read_bytes()).hexdigest(),
                "package_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
            },
            indent=2,
        )
        + "\n"
    )


def test_sampled_zero_split_occluder_cannot_arm_the_entry(candidate, tmp_path):
    scene = scene_for(candidate)
    physical = scene.world["spatial_scene"]
    lower = next(row for row in physical["geometry"] if row["id"] == "full-wall")
    upper = deepcopy(lower)
    lower["volume"]["max"][1] = 5.65
    upper["id"] = "upper-continuous-occluder"
    upper["volume"]["min"][1] = 5.65
    physical["geometry"].append(upper)
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene)
        move_out(owner, game)
        probes = {}
        for identity, contact in record(game)["contacts"].items():
            probes[identity] = (
                SpatialEngine(game.world["spatial_scene"])
                .query(
                    {
                        "kind": "exposure",
                        "map_id": physical["map"]["id"],
                        "observer": {"point": contact["origin"]},
                        "target": {"entity_id": game.shooter},
                        "samples": {"horizontal": 3, "vertical": 3},
                        "include": ["geometry"],
                    }
                )
                .as_payload()
            )
            assert probes[identity]["exposure"] == 0
        assert not record(game)["proof_supported"] and not record(game)["all_blocked"]
        assert record(game)["epoch"] == 0 and not record(game)["fully_lost"]
        refused_repeat(owner, game, "quiet-metal")
        (tmp_path / "unsupported-proof.json").write_text(
            json.dumps(
                {
                    "component_samples": probes,
                    "private_record": record(game),
                    "qualification": (
                        "Shared-engine-valid boxes, but no single full-body occlusion certificate. "
                        "Zero samples do not grant the Entry; this configuration is unsupported."
                    ),
                },
                indent=2,
            )
            + "\n"
        )


class CapturedProvider(_ScriptedProvider):
    """Serialize real adapter inputs at consumption time; no capabilities are overridden."""

    def __init__(self, label, stop_after_reads=None):
        super().__init__(
            [
                call(
                    "read_campaign_reference",
                    {"source": "campaign_state", "path": "current"},
                    label + "-read-current",
                ),
                call("read_action_sequence_controls", {}, label + "-read-controls"),
                call(
                    "update_campaign_file",
                    {
                        "state_change": "unchanged",
                        "state_change_summary": "Read-only public inspection.",
                        "revised_player_facing_response": [
                            {
                                "kind": "narration",
                                "text": "The public tactical controls are available.",
                            },
                        ],
                    },
                    label + "-finish-readonly",
                ),
            ]
        )
        if stop_after_reads:
            self.rounds.pop()
        self.serialized_requests = []
        self.stop_after_reads = stop_after_reads
        self.stop_result = None

    async def generate_stream(self, messages, tools=None, **kwargs):
        self.serialized_requests.append(json.dumps(messages, ensure_ascii=False))
        if len(self.serialized_requests) == 3 and self.stop_after_reads:
            # Keep the real GM decision boundary: stop after consuming both reads,
            # rather than inventing an NPC action to permit terminal narration.
            self.stop_result = self.stop_after_reads()
            return
        async for chunk in super().generate_stream(messages, tools, **kwargs):
            yield chunk


def assert_private_facts_absent(value, game):
    text = json.dumps(thaw_json_value(value), ensure_ascii=False)
    for marker in (
        "private-visual-",
        "private-observation-",
        "LOCALIZABLE_NOISE",
        "QUIET_HELD_METAL_SEARCH",
        "PRIVATE-PLAYER-OBSERVATION",
    ):
        assert marker not in text

    forbidden = {tuple(contact["origin"]) for contact in record(game)["contacts"].values()}
    failure_region = record(game)["activities"]["quiet-metal"]["failure"]["region"]
    forbidden.update((tuple(failure_region["min"]), tuple(failure_region["max"])))

    def visit(item):
        if isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            if len(item) == 3 and all(type(v) in (int, float) for v in item):
                assert tuple(item) not in forbidden
            for child in item:
                visit(child)
        elif isinstance(item, str):
            try:
                parsed = json.loads(item)
            except (ValueError, TypeError):
                return
            if isinstance(parsed, (dict, list)):
                visit(parsed)

    visit(thaw_json_value(value))


def consume_public_context(owner, game, label):
    session_id = f"{SESSION}-{game.campaign}"
    if owner.session_context(session_id).session.status.value == "stopped":
        owner.resume_agent_session(session_id)
    mcp_reference = StatelessMcpMechanics(owner).read_reference(
        game.campaign,
        source="campaign_state",
        path="current",
        session_id=session_id,
    )
    before = owner.snapshot(game.campaign)
    loop = CampaignSessionLoop(owner)
    gm_read_only = game.world["decision_owner"] == "gm"
    provider = CapturedProvider(label, (lambda: loop.stop(session_id)) if gm_read_only else None)
    adapter = ProviderSessionAdapter(owner, provider, owner_id="aim-owner")
    result = loop.run(
        SessionRunRequest(
            session_id=session_id,
            turn_id="turn",
            attempt_id="inspect-" + label,
            message_id="inspect-message-" + label,
            user_text="Read the current public campaign reference and tactical controls. "
            "Do not perform any game action or change game state.",
        ),
        adapter.for_turn("turn"),
    )
    assert (result.interruption is not None) == gm_read_only
    assert len(provider.serialized_requests) == 3 and not provider.rounds
    assert len(provider.calls) == (2 if gm_read_only else 3)
    if gm_read_only:
        assert provider.stop_result is not None
        assert provider.stop_result.interruption == result.interruption
    assert thaw_json_value(owner.snapshot(game.campaign).state) == thaw_json_value(before.state)
    for serialized in provider.serialized_requests:
        assert_private_facts_absent(json.loads(serialized), game)
    first_messages, first_tools = provider.calls[0]
    names = {row["function"]["name"] for row in first_tools}
    for operation in ("read_campaign_reference", "read_action_sequence_controls"):
        assert any(name.endswith("_" + operation) for name in names)
    fresh_text = next(
        row["content"]
        for row in first_messages
        if str(row.get("content", "")).startswith("SYLVIA_GM_FRESH_CAMPAIGN_STATE")
    )
    fresh = json.loads(fresh_text.split("\n", 1)[1])
    assert NS + ".state" not in fresh["campaign_state"]["ruleset_owned_values"]
    assert fresh["campaign_state"]["ruleset_owned_values"][NS + ".entities"] == game.actors
    assert fresh["campaign"]["revision"] == before.revision
    scope = fresh["ruleset_decision"]
    assert scope["owner"] == game.world["decision_owner"]
    assert scope["active_side_id"] == game.world["active_side"]
    assert [row["actor_id"] for row in scope["eligible_actors"]] == game.world["eligible_actor_ids"]
    for actor in scope["eligible_actors"]:
        assert actor["actor_state"] == game.actors[actor["actor_id"]]
        assert actor["actor_state"]["side"] == scope["active_side_id"]
        if scope["owner"] == "gm":
            assert actor["actor_state"]["ap"] == actor["actor_state"]["max_ap"]
    replies = {
        row["tool_call_id"]: json.loads(row["content"])
        for row in json.loads(provider.serialized_requests[-1])
        if row.get("role") == "tool"
    }
    controls = replies[label + "-read-controls"]
    assert controls["kind"] == "action_sequence_controls"
    reference_reply = replies[label + "-read-current"]
    assert reference_reply["status"] == "reference_read"
    reference = reference_reply["result"]
    reference_state = json.loads(reference["content"])
    assert NS + ".state" not in reference_state["ruleset_owned_values"]
    assert reference_state["ruleset_owned_values"][NS + ".entities"] == game.actors
    surfaces = {row["id"]: row for row in controls["surfaces"]}
    tactical = surfaces["operations.map"]["data"]
    assert tactical["map_revision"] == game.world["spatial_scene"]["map"]["revision"]
    assert {row["id"] for row in tactical["objectives"]} == {
        row["id"] for row in game.world["objectives"] if row["visible"]
    }
    assert game.shooter in {row["id"] for row in tactical["entities"]}
    assert "hidden_hostile" not in {row["id"] for row in tactical["entities"]}
    status = surfaces["operations.status"]["data"]
    assert status["Mission status"] == game.world["mission"]["status"]
    assert status["Active side"] == game.world["active_side"]
    assert status["Decision owner"] == game.world["decision_owner"]
    network = surfaces["operations.network"]["data"]
    assert {row["id"] for row in network["nodes"]} == {"lobby_terminal"}
    assert "camera_gateway" not in json.dumps(network)
    assert "security_controller" not in json.dumps(network)
    quiet = next(
        row for row in controls["controls"] if row["source"].get("action_id") == "keep-metal-quiet"
    )
    assert quiet["declaration"]["intent"] == "attempt_break_contact"
    assert set(quiet["selection_contract"]["required_keys"]) == {"actor", "map_revision"}
    assert quiet["selection_contract"]["context"] == {"map_revision": tactical["map_revision"]}
    # Discovery during this actual GM operation must not manufacture a manual
    # control grant. NPC choices instead use the ordinary ruleset decision scope.
    assert controls["direct_actions_available"] is False
    assert_private_facts_absent(controls, game)
    assert (
        json.loads(mcp_reference["content"])["ruleset_owned_values"]
        == reference_state["ruleset_owned_values"]
    )
    assert_private_facts_absent(mcp_reference, game)
    assert_private_facts_absent(thaw_json_value(owner.desktop_snapshot(game.campaign).state), game)
    assert_private_facts_absent(owner.campaign_panels(game.campaign), game)
    for _, receipt in game.entry_calls:
        assert_private_facts_absent(receipt.result, game)
    return {
        "label": label,
        "fresh_context_serialization": fresh_text,
        "tool_reply_serializations": [
            row["content"]
            for row in json.loads(provider.serialized_requests[-1])
            if row.get("role") == "tool"
        ],
        "real_discovered_tool_names": sorted(names),
        "serialized_request_sha256": [
            hashlib.sha256(value.encode()).hexdigest() for value in provider.serialized_requests
        ],
        "scripted_provider_calls": len(provider.calls),
        "serialized_requests": len(provider.serialized_requests),
        "stopped_after_real_reads": gm_read_only,
        "genuine_inference": False,
        "state_unchanged": True,
        "capability_overrides": False,
    }


def test_actual_provider_and_gm_reads_keep_public_play_and_private_evidence_separate(
    candidate, tmp_path
):
    scene = scene_for(candidate, renamed=True)
    scene.world["private_observation_note"] = "PRIVATE-PLAYER-OBSERVATION"
    database = tmp_path / "authority.sqlite3"
    projections = []
    prepare_library_database(database, candidate.read_bytes())
    with AuthorityOwner.open(database, "aim-owner", disconnect_grace_seconds=1.0) as owner:
        admitted = owner.package_candidate(CANDIDATE_ID)
        game = start(owner, admitted, scene)
        move_out(owner, game)
        attempt(owner, game, 1)
        assert record(game)["contacts"][game.mover]["knowledge"] == "APPROXIMATE"
        projections.append(consume_public_context(owner, game, "player-turn"))
        step(owner, game, "end_allies_turn", {})
        assert game.world["decision_owner"] == "gm"
        assert game.mover in game.world["eligible_actor_ids"]
        projections.append(consume_public_context(owner, game, "gm-turn"))
        expected = owner.snapshot(game.campaign)
    with AuthorityOwner.open(database, "aim-owner", disconnect_grace_seconds=1.0) as owner:
        game.owner = owner
        assert owner.snapshot(game.campaign) == expected
        projections.append(consume_public_context(owner, game, "reopened-gm-turn"))
    (tmp_path / "provider-projection-proof.json").write_text(
        json.dumps(
            {
                "package_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                "projections": projections,
                "qualification": (
                    "Actual ProviderSessionAdapter/CampaignSessionLoop, real owner and "
                    "reference/control tools, local scripted provider only. No inference, "
                    "registry/capability override, GUI, listener or native acceptance. "
                    "Captures are projected synthetic data, not raw Full Audit records."
                ),
            },
            indent=2,
        )
        + "\n"
    )
