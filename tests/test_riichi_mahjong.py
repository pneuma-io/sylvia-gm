"""The declared Riichi package owns a complete physical table through normal authority."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from scripts.build_riichi_ruleset import NS, source

from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.application.piece_table import project_piece_table
from sylvia_gm.builder._ruleset_source import compile_ruleset_source
from sylvia_gm.domain.authority import (
    Participant,
    ParticipantValidationStatus,
    WriterRole,
    thaw_json_value,
)
from sylvia_gm.domain.errors import CommandRejected


class Table:
    def __init__(self, owner):
        self.owner = owner
        self.sequence = 0

    def snapshot(self):
        return self.owner.snapshot("riichi")

    def state(self):
        return self.snapshot().state[NS + ".state"]

    def entities(self):
        return self.snapshot().state[NS + ".entities"]

    def act(self, name, parameters):
        self.sequence += 1
        command = ActionCommand(
            campaign_id="riichi",
            action_id=f"action-{self.sequence}",
            idempotency_key=f"action-{self.sequence}",
            turn_id="turn",
            owner_id="riichi-test",
            writer=WriterRole.DM,
            expected_revision=self.snapshot().revision,
            package_id=NS,
            tool_name=name,
            parameters=parameters,
        )
        receipt = self.owner.execute_action(command)
        assert receipt.status.value == "committed", receipt.result
        return receipt


@pytest.fixture
def table(tmp_path):
    compiled = compile_ruleset_source(yaml.safe_dump(source()).encode())
    with AuthorityOwner.open(tmp_path / "riichi.sqlite3", "riichi-test") as owner:
        with materialize_package_archive((compiled.draft,)) as artifact:
            manifest = artifact.manifest
            schema = manifest.participant_artifacts[0]
            candidate = owner.install_package_archive(artifact.archive, install_id="riichi")
        owner.create_campaign(
            CampaignCreate(
                campaign_id="riichi",
                display_name="Riichi Mahjong",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="turn",
                participants=(
                    Participant(
                        participant_id="you",
                        kind="player",
                        package_id=NS,
                        package_version=manifest.version,
                        data={"name": "You", "control": "player"},
                        package_sha256=manifest.content_sha256,
                        schema_id=schema.schema_id,
                        schema_sha256=schema.schema_sha256,
                        validation_status=ParticipantValidationStatus.VALIDATED,
                    ),
                ),
            )
        )
        yield Table(owner)


def physical_tiles(table):
    state = table.state()
    values = list(state["live"]) + list(state["dead"])
    for seat in table.entities().values():
        values += list(seat["game"]["hand"])
        values += [row["tile"] for row in seat["game"]["discards"] if not row["claimed"]]
        for meld in seat["game"]["melds"]:
            values += list(meld["tiles"])
    return values


@pytest.mark.parametrize("match_format", ["east_only", "east_south"])
def test_normal_start_deals_four_seats_red_fives_and_private_wall(table, match_format):
    assert {tool.name: tool.model_behavior.value for tool in table.owner.catalog("riichi")} == {
        "start_game": "resolves_player_intent",
        "take_action": "resolves_player_intent",
    }
    receipt = table.act("start_game", {"player": "you", "match_format": match_format})
    assert {tool.name: tool.model_behavior.value for tool in table.owner.catalog("riichi")} == {
        "start_game": "resolves_player_intent",
        "take_action": "resolves_player_intent",
    }
    assert receipt.status.value == "committed"
    assert table.state()["match_format"] == match_format
    assert sorted(physical_tiles(table)) == list(range(136))
    assert [len(table.entities()[seat]["game"]["hand"]) for seat in table.state()["seats"]] == [
        14,
        13,
        13,
        13,
    ]
    assert len(table.state()["live"]) == 69
    assert len(table.state()["dead"]) == 14
    assert sum(row["red"] for row in source()["definitions"]["tiles"]) == 3
    projection = thaw_json_value(table.owner.campaign_panels("riichi"))
    panel = projection["surfaces"][0]
    assert panel["renderer"] == "piece_table" and "source" not in panel
    areas = {area["id"]: area for area in panel["data"]["areas"]}
    assert areas["you.right"]["position"] == "right"
    assert areas["you.left"]["position"] == "left"
    groups = [group for area in panel["data"]["areas"] for group in area["groups"]]
    assert sum(group.get("concealed", False) for group in groups) == 3
    assert [group["count"] for group in groups if group.get("concealed")] == [13, 13, 13]
    assert projection["player_control"]["available"] is True
    assert table.owner.desktop_snapshot("riichi").state == {}
    assert "live" not in json.dumps(thaw_json_value(receipt.result))


def test_discard_response_turn_cycle_preserves_tiles_and_rejects_stale_choice(table):
    table.act("start_game", {"player": "you", "match_format": "east_only"})
    first_choice = table.state()["choices"][0]["id"]
    table.act("take_action", {"choice": first_choice})
    assert table.state()["phase"] in {"response", "discard"}
    assert table.owner.campaign_panels("riichi")["player_control"]["available"] is False
    with pytest.raises(CommandRejected):
        table.act("take_action", {"choice": first_choice})
    for _ in range(3):
        if table.state()["phase"] != "response":
            break
        table.act("take_action", {"choice": table.state()["choices"][0]["id"]})
    assert table.state()["phase"] == "discard"
    assert table.state()["actor"] == "you.right"
    assert len(table.entities()["you.right"]["game"]["hand"]) == 14
    assert sorted(physical_tiles(table)) == list(range(136))


def test_concrete_seat_views_do_not_disclose_other_hands_and_client_cannot_choose_a_viewer(table):
    table.act("start_game", {"player": "you", "match_format": "east_only"})
    raw = table.state()["table"]
    for viewer in ("you", "you.right", "you.across", "you.left"):
        projected = project_piece_table(raw, viewer_ids={viewer}, action_ids={"play"})
        hands = [area["groups"][0] for area in projected["areas"] if area["id"] != "shared"]
        assert sum("pieces" in hand for hand in hands) == 1
        assert sum(hand.get("concealed", False) for hand in hands) == 3
    table.act("take_action", {"choice": table.state()["choices"][0]["id"]})
    with pytest.raises(CommandRejected):
        table.owner.execute_panel_action(
            campaign_id="riichi",
            surface_id="riichi-table",
            panel_action_id="play",
            operation_id="forged-viewer",
            expected_revision=table.snapshot().revision,
            selection={"piece": table.state()["choices"][0]["id"], "viewer": "you.right"},
        )
    before = table.snapshot()
    with pytest.raises(CommandRejected):
        table.owner.execute_panel_action(
            campaign_id="riichi",
            surface_id="riichi-table",
            panel_action_id="play",
            operation_id="valid-token-wrong-seat",
            expected_revision=before.revision,
            selection={"piece": table.state()["choices"][0]["id"]},
        )
    assert table.snapshot() == before


def test_action_tokens_do_not_encode_sorted_hand_position_or_private_option_count(
    table, monkeypatch
):
    sampled = []

    class Random:
        def shuffle(self, values):
            sampled.append(len(values))
            values.reverse()

    monkeypatch.setattr("sylvia_gm.runtime._effect_executor.secrets.SystemRandom", Random)
    table.act("start_game", {"player": "you", "match_format": "east_only"})
    assert sampled == [136, 256]
    assert table.state()["choices"][0]["id"] == "choice:1:255"
    assert len(set(table.state()["choice_ids"])) == len(table.state()["choices"])


def test_opponent_context_and_ordinary_reference_expose_only_permitted_information(table):
    from sylvia_gm.agent._provider_session import (
        ProviderSessionAdapter,
        _gm_decision_control_message,
    )
    from tests.test_alpha_f4_provider_session_adapter import _provider_input, _ScriptedProvider

    table.act("start_game", {"player": "you", "match_format": "east_south"})
    table.act("take_action", {"choice": table.state()["choices"][0]["id"]})
    boundary = table.owner.model_decision_boundary("riichi")
    assert boundary.owner.value == "gm" and boundary.actor_id != "you.seat"
    payload = json.loads(
        _gm_decision_control_message(boundary)["content"].split("\n", 1)[1].split("\n", 1)[0]
    )
    assert payload["actor_state"]["game"]["hand"] == list(
        table.entities()[boundary.actor_id]["game"]["hand"]
    )
    assert "hand" not in json.dumps(payload["actor_state"]["game"]["public_table"])
    assert len(payload["actor_state"]["game"]["public_table"]["seats"]) == 4
    assert (
        "live" not in payload["actor_state"]["game"]
        and "dead" not in payload["actor_state"]["game"]
    )
    provider_input = _provider_input()
    provider_input = replace(
        provider_input, session=replace(provider_input.session, campaign_id="riichi")
    )
    adapter = ProviderSessionAdapter(table.owner, _ScriptedProvider([]), owner_id="riichi-test")
    context = adapter._current_campaign_context_payload(provider_input)
    assert context["campaign_state"]["ruleset_owned_values"] == {}
    assert context["ruleset_decision"]["actor_id"] == boundary.actor_id
    assert context["ruleset_decision"]["actor_state"] == payload["actor_state"]
    fresh = json.loads(adapter._campaign_context_messages(provider_input)[1]["content"].split(
        "\n", 1,
    )[1])
    assert fresh["ruleset_decision"] == context["ruleset_decision"]
    reference = table.owner.read_campaign_reference(
        "riichi", source="campaign_state", path="current"
    )
    assert json.loads(reference["content"])["ruleset_owned_values"] == {}


def test_resumed_gm_seat_requires_real_action_before_campaign_narration(table):
    from sylvia_gm.agent._provider_session import ProviderSessionAdapter
    from sylvia_gm.agent._stateless_mcp import ModelMechanicsTool
    from sylvia_gm.domain.session import SessionContext
    from sylvia_gm.provider._base import ProviderStreamChunk
    from tests.test_alpha_f4_provider_session_adapter import (
        _campaign_file_tool,
        _next_action_event,
        _provider_input,
        _ScriptedProvider,
        _tool_operations,
    )

    table.act("start_game", {"player": "you", "match_format": "east_only"})
    table.act("take_action", {"choice": table.state()["choices"][0]["id"]})
    boundary = table.owner.model_decision_boundary("riichi")
    assert boundary.owner.value == "gm"
    choice = boundary.actor_state["game"]["choices"][0]["id"]
    session = table.owner.create_agent_session(
        campaign_id="riichi", session_id="riichi-gm", role=WriterRole.DM,
        provider_id="synthetic", model_id="synthetic",
    )
    base = _provider_input(user_text="Continue the table.")
    catalog = tuple(ModelMechanicsTool(
        package_id=tool.package_id, package_version=tool.package_version,
        package_sha256=tool.package_sha256, name=tool.name, capability=tool.capability,
        input_schema=tool.input_schema, description=tool.description,
        model_behavior=tool.model_behavior,
    ) for tool in table.owner.catalog("riichi"))
    provider_input = replace(
        base, session=session,
        history=SessionContext(session=session, messages=(
            replace(base.history.messages[0], session_id=session.session_id),
        )), catalog=(*catalog, _campaign_file_tool(allow_unchanged=True)),
    )
    provider = _ScriptedProvider([[ProviderStreamChunk(
        tool_call_delta={"index": 0, "id": "riichi-controlled-seat", "function": {
            "name": "take_action", "arguments": json.dumps({"choice": choice}),
        }}, finish_reason="tool_calls",
    )]])
    events = iter(ProviderSessionAdapter(
        table.owner, provider, owner_id="riichi-test",
    ).for_turn("turn")(provider_input))
    try:
        action = _next_action_event(events)
        assert action.action_command.tool_name == "take_action"
        assert provider.request_kwargs[0]["tool_choice"] == "required"
        messages, tools = provider.calls[0]
        assert "update_campaign_file" not in _tool_operations(tools)
        assert "SYLVIA_GM_DECISION_REQUEST" in str(messages)
        before = table.snapshot().revision
        receipt = table.owner.execute_action(action.action_command)
        assert receipt.status.value == "committed"
        assert receipt.resulting_revision == before + 1
    finally:
        events.close()


def test_real_match_reopens_without_reshuffle_or_action_replay(table, tmp_path):
    table.act("start_game", {"player": "you", "match_format": "east_only"})
    choice = table.state()["choices"][0]["id"]
    table.act("take_action", {"choice": choice})
    state = thaw_json_value(table.state())
    entities = thaw_json_value(table.entities())
    panel = thaw_json_value(table.owner.campaign_panels("riichi"))
    table.owner.close()
    with AuthorityOwner.open(tmp_path / "riichi.sqlite3", "reopened") as owner:
        reopened = Table(owner)
        assert thaw_json_value(reopened.state()) == state
        assert thaw_json_value(reopened.entities()) == entities
        assert thaw_json_value(owner.campaign_panels("riichi")) == panel
        with pytest.raises(CommandRejected):
            owner.execute_panel_action(
                campaign_id="riichi",
                surface_id="riichi-table",
                panel_action_id="play",
                operation_id="stale-reopened",
                expected_revision=reopened.snapshot().revision,
                selection={"piece": choice},
            )


@pytest.mark.parametrize("match_format,hand_count", [("east_only", 8), ("east_south", 12)])
def test_complete_match_from_normal_start_through_one_extension(
    table,
    monkeypatch,
    match_format,
    hand_count,
    tmp_path,
):
    from tests.test_riichi_scoring import tiles

    dealt = []

    class ControlledWall:
        def shuffle(self, wall):
            if len(wall) != 136:
                wall.reverse()
                return
            dealer = len(dealt) % 4
            winner = (dealer + 1) % 4
            pool = list(range(136))
            hand = []
            for example in tiles("23456m23466p678s"):
                candidates = [tile for tile in pool if tile // 4 == example // 4]
                tile = next(
                    (tile for tile in candidates if tile not in (16, 52, 88)), candidates[0]
                )
                pool.remove(tile)
                hand.append(tile)
            winning_tile = next(tile for tile in pool if tile // 4 == 6)
            pool.remove(winning_tile)
            indicator = 124
            pool.remove(indicator)
            hands = [[] for _ in range(4)]
            hands[winner] = hand
            hands[dealer] = [winning_tile, *pool[:12]]
            del pool[:12]
            for seat in range(4):
                if not hands[seat]:
                    hands[seat] = pool[:13]
                    del pool[:13]
            remainder = [*pool[:74], indicator, *pool[74:]]
            wall[:] = [tile for hand in hands for tile in hand] + remainder
            assert sorted(wall) == list(range(136)) and wall[126] == indicator
            dealt.append(winner)

    monkeypatch.setattr("sylvia_gm.runtime._effect_executor.secrets.SystemRandom", ControlledWall)
    table.act("start_game", {"player": "you", "match_format": match_format})
    first_generation_choice = table.state()["choices"][0]["id"]
    for hand in range(hand_count):
        assert table.state()["phase"] == "discard"
        discard = next(
            row
            for row in table.state()["choices"]
            if row["op"] == "discard" and row["tiles"][0] // 4 == 6
        )
        table.act("take_action", {"choice": discard["id"]})
        for _ in range(3):
            if table.state()["phase"] != "response":
                break
            winner = table.state()["seats"][dealt[-1]]
            op = "ron" if table.state()["actor"] == winner else "pass"
            choice = next(row for row in table.state()["choices"] if row["op"] == op)
            table.act("take_action", {"choice": choice["id"]})
        assert table.state()["result"]["kind"] == "win"
        assert sorted(physical_tiles(table)) == list(range(136))
        choice = next(row for row in table.state()["choices"] if row["op"] == "continue")
        table.act("take_action", {"choice": choice["id"]})
        assert table.state()["finished"] is (hand == hand_count - 1)
    assert len(dealt) == hand_count
    assert table.state()["phase"] == "finished"
    assert table.state()["dealer"] == 3
    assert table.state()["round_wind"] == (1 if match_format == "east_only" else 2)
    assert [table.entities()[seat]["score"] for seat in table.state()["seats"]] == [25000] * 4
    result = table.state()["match_result"]
    assert result["winner"] == 0
    assert [row["seat"] for row in result["standings"]] == [0, 1, 2, 3]
    assert [row["points"] for row in result["standings"]] == [25000] * 4
    saved = table.snapshot()
    sequence = table.sequence
    table.owner.close()
    with AuthorityOwner.open(tmp_path / "riichi.sqlite3", "riichi-test") as reopened:
        assert reopened.snapshot("riichi") == saved
        table = Table(reopened)
        table.sequence = sequence
        table.act("start_game", {"player": "you", "match_format": match_format})
        assert table.state()["match_result"] is None
        assert first_generation_choice not in table.state()["choice_ids"]
        with pytest.raises(CommandRejected):
            table.act("take_action", {"choice": first_generation_choice})


def test_original_setting_is_an_ordinary_campaign_choice_and_character_can_be_created(tmp_path):
    from sylvia_gm.application.reusable_participants import save_reusable_participant
    from sylvia_gm.builder._setting_materializer import _world_draft
    from sylvia_gm.domain.campaign_plan import PackageBinding
    from sylvia_gm.interfaces.owner_process import _campaign_creation_catalog

    compiled = compile_ruleset_source(yaml.safe_dump(source()).encode())
    world = _world_draft(
        package_id="pneuma.riichi-club",
        version="0.1.0",
        title="The Lantern Table",
        payloads={
            "world.md": (
                Path(__file__).parents[1] / "rulesets/riichi-mahjong/setting/world.md"
            ).read_bytes()
        },
        ruleset=compiled.draft,
    )
    with AuthorityOwner.open(tmp_path / "library.sqlite3", "library-test") as owner:
        with materialize_package_archive((compiled.draft, world)) as artifact:
            candidate = owner.install_package_archive(artifact.archive, install_id="riichi")
        primary = next(row for row in candidate.package_lock.entries if row.package_id == NS)
        setting = next(
            row for row in candidate.package_lock.entries if row.package_id == "pneuma.riichi-club"
        )
        declaration = owner.exact_participant_declarations(NS, primary.version, primary.sha256)[0]
        saved = save_reusable_participant(
            tmp_path,
            operation_id="riichi-player",
            role="player_character",
            summary="A friendly club player",
            authored_profile="You enjoy a friendly game and clear rules.",
            participant={"name": "You", "control": "player"},
            entity_state={},
            package=primary,
            declaration=declaration,
            campaign_setting=PackageBinding(setting.package_id, setting.version, setting.sha256),
        )
        assert saved.participant["control"] == "player" and saved.entity_state == {}
        catalog = _campaign_creation_catalog(tmp_path, owner)
        choice = next(row for row in catalog if row["kind"] == "ruleset" and row["id"] == NS)
        assert any(row["id"] == "pneuma.riichi-club" for row in choice["compatible_settings"])
