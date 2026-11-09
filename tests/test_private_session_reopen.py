"""Private engine tool references remain resumable without exposing internal inputs."""

import json
import shutil
import sqlite3

import pytest

from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import CanonicalEventType, WriterRole, thaw_json_value
from sylvia_gm.domain.errors import SchemaIdentityError
from tests.test_alpha_f4_generic_sessions import _clockwork_campaign
from tests.test_riichi_mahjong import table as table


def test_private_tool_replay_session_reopens_and_still_rejects_tampering(table, tmp_path):
    owner = table.owner
    owner.create_agent_session(
        campaign_id="riichi", role=WriterRole.DM, provider_id="fixture",
        model_id="fixture", session_id="dm",
    )
    table.act("start_game", {"player": "you", "match_format": "east_only"})
    replay = owner.canonical_replay("riichi", acknowledged_sequence=0).canonical
    call = next(event for event in replay if event.event_type is CanonicalEventType.TOOL_CALL)
    assert call.safe_payload["parameters"] == {}
    owner.append_session_canonical_references("dm", replay)
    before = owner.snapshot("riichi")
    history = owner.session_context("dm").messages
    owner.close()

    database = tmp_path / "riichi.sqlite3"
    with AuthorityOwner.open(database, "reopened") as reopened:
        assert reopened.snapshot("riichi") == before
        assert reopened.session_context("dm").messages == history
        assert thaw_json_value(reopened.session_context("dm").messages[0].content)
        assert next(event for event in reopened.canonical_replay(
            "riichi", acknowledged_sequence=0,
        ).canonical if event.event_type is CanonicalEventType.TOOL_CALL).safe_payload[
            "parameters"
        ] == {}

    for field, value in [
        ("tool_name", "invented"), ("package_sha256", "0" * 64),
        ("visibility", "internal"), ("parameters", {"player": "someone-else"}),
    ]:
        damaged = tmp_path / f"damaged-{field}.sqlite3"
        shutil.copy2(database, damaged)
        with sqlite3.connect(damaged) as connection:
            sequence, content = connection.execute(
                "SELECT sequence, content_json FROM agent_session_messages "
                "WHERE session_id='dm' AND canonical_sequence=?", (call.sequence,),
            ).fetchone()
            content = json.loads(content)
            if field == "visibility":
                content[field] = value
            else:
                content["safe_payload"][field] = value
            connection.execute(
                "UPDATE agent_session_messages SET content_json=? "
                "WHERE session_id='dm' AND sequence=?", (json.dumps(content), sequence),
            )
        with pytest.raises(SchemaIdentityError):
            AuthorityOwner.open(damaged, "rejected")


def test_public_tool_reference_cannot_omit_its_parameters(tmp_path):
    database = tmp_path / "public.sqlite3"
    with AuthorityOwner.open(database, "owner") as owner:
        _clockwork_campaign(owner, campaign_id="campaign", writer=WriterRole.DM,
                            turn_id="turn", calls=[])
        owner.create_agent_session(campaign_id="campaign", role=WriterRole.DM,
                                   provider_id="fixture", model_id="fixture", session_id="dm")
        package = owner.current_package_lock("campaign").package_lock.entries[0]
        owner.execute_action(ActionCommand(
            campaign_id="campaign", action_id="advance", idempotency_key="advance",
            owner_id="owner", writer=WriterRole.DM, expected_revision=0, turn_id="turn",
            package_id=package.package_id, tool_name="advance", parameters={"steps": 4},
        ))
        replay = owner.canonical_replay("campaign", acknowledged_sequence=0).canonical
        call = next(event for event in replay if event.event_type is CanonicalEventType.TOOL_CALL)
        assert call.safe_payload["parameters"] == {"steps": 4}
        owner.append_session_canonical_references("dm", replay)
    with AuthorityOwner.open(database, "reopened"):
        pass
    with sqlite3.connect(database) as connection:
        sequence, content = connection.execute(
            "SELECT sequence, content_json FROM agent_session_messages "
            "WHERE session_id='dm' AND canonical_sequence=?", (call.sequence,),
        ).fetchone()
        content = json.loads(content)
        content["safe_payload"]["parameters"] = {}
        connection.execute("UPDATE agent_session_messages SET content_json=? "
                           "WHERE session_id='dm' AND sequence=?", (json.dumps(content), sequence))
    with pytest.raises(SchemaIdentityError):
        AuthorityOwner.open(database, "rejected")
