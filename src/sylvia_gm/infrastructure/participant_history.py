"""Recover former live character data from authority-owned committed consequences."""

import json
import sqlite3
from collections.abc import Mapping


def participant_data_history(
    connection: sqlite3.Connection,
    campaign_id: str,
    participant_id: str,
    current: Mapping,
) -> list[Mapping]:
    """Walk a contiguous reverse history; never accept a detached asserted before-value."""
    values = [current]
    for row in connection.execute(
        "SELECT e.safe_payload_json FROM canonical_events e JOIN actions a "
        "ON a.campaign_id = e.campaign_id AND a.action_id = e.action_id "
        "WHERE e.campaign_id = ? AND e.event_type = 'consequence' "
        "AND a.status = 'committed' ORDER BY e.sequence DESC",
        (campaign_id,),
    ):
        payload = json.loads(row[0])
        if (
            payload.get("kind") != "participant_updated"
            or payload.get("participant_id") != participant_id
        ):
            continue
        if payload.get("after") != values[-1] or not isinstance(payload.get("before"), Mapping):
            raise ValueError("Participant update history is discontinuous")
        values.append(payload["before"])
    return values
