"""A Plan must reject unusable party placements before committing campaign state."""

import json

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.campaign_plan_sources import campaign_plan_sources
from sylvia_gm.application.campaign_starting_world import materialize_starting_world
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests.test_campaign_library_selection import seed_campaign_library


@pytest.mark.parametrize(
    "damage, message",
    [
        ("too_few", "too few starting placements"),
        ("duplicate", "conflicting identities"),
        ("missing_list", "authored list"),
        ("foreign", "must belong"),
        ("reserved", "reserved canonical channel"),
        ("bad_reference", "participant reference is invalid"),
    ],
)
def test_unusable_party_placement_rejects_without_changing_the_plan(tmp_path, damage, message):
    with AuthorityOwner.open(tmp_path / "placement.db", "owner") as owner:
        candidate, _, saved, _ = seed_campaign_library(owner, tmp_path)
        sources = campaign_plan_sources(owner, candidate.package_lock)
        original = canonical_json_bytes(saved.plan.payload)
        payload = json.loads(original)
        slots = payload["initial_state"]["party_placements"]
        if damage == "too_few":
            del slots[1:]
        elif damage == "duplicate":
            slots[0]["append"][0]["value"]["id"] = "operator"
        elif damage == "missing_list":
            slots[0]["append"][0]["key"] = "state.missing.entities"
        elif damage == "foreign":
            slots[0]["append"][0]["namespace"] = "another.ruleset"
        elif damage == "reserved":
            slots[0]["append"][0]["key"] = "state.private_reasoning"
        else:
            slots[0]["entity_state"]["id"] = {"$participant": []}
        with pytest.raises((ValueError, CommandRejected), match=message):
            plan = sources.validate(payload, saved.plan.readable_markdown)
            materialize_starting_world(
                plan,
                entity_reference={"namespace": sources.ruleset.package_id, "key": "state.entities"},
                party_entities={
                    "selected-player": {"stress": 0},
                    "selected-companion": {"stress": 1},
                },
            )
        assert canonical_json_bytes(saved.plan.payload) == original
