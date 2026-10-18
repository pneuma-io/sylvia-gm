"""Committed shot presentation excludes historical playback and raw query copies."""

import io
from copy import deepcopy

import pytest
import yaml

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.application.package_runtime import PackageRuntimeError
from sylvia_gm.domain.authority import ActionStatus, StateValue, WriterRole, thaw_json_value
from tests.test_spatial_runtime import (
    _PACKAGE_ID,
    _archive,
    _context,
    _effect,
    _entity,
    _mechanic,
    _mutation_effect,
    _runtime,
    _scene,
)


def _visible_scene():
    scene = _scene(entities=[_entity("source", [1, 3, 0]), _entity("subject", [8, 3, 0])])
    for item in scene["entities"] + scene["geometry"]:
        item["metadata"]["player"] = {"label": item["id"], "kind": "test"}
    return scene


def _shot(*, delay=0, contacts=None):
    return {"shot_effect": {
        "scene": "state.spatial_scene", "snapshot": "{_shot_snapshot}",
        "source_id": "source", "target_id": "subject",
        "origin": [1, 3, 1], "destination": [8, 3, 1], "delay_ms": delay, "miss": False,
        "contacts": contacts if contacts is not None else [
            {"kind": "geometry", "id": "near-surface", "point": [3, 3, 1], "cue": "penetration"},
            {"kind": "geometry", "id": "far-surface", "point": [6, 3, 1], "cue": "stop"},
        ],
    }}


def test_presentation_uses_firing_snapshot_and_does_not_undo_mechanics_when_invalid():
    mechanic = _mechanic(_runtime([
        _effect("snapshot", store_as="_shot_snapshot"),
        _mutation_effect(),
        _shot(),
        _shot(delay=None),
        {"set": {"field": "state.first", "value": "'resolved'"}},
    ]))
    result = mechanic(_context(_visible_scene()))
    presentation = result.result["shot_effects"]
    assert len(presentation["shots"]) == 1
    shot = presentation["shots"][0]
    assert [contact["cue"] for contact in shot["contacts"]] == ["penetration", "stop"]
    assert list(shot["destination"]) == [6, 3, 1]
    assert presentation["unavailable"] is True
    state = thaw_json_value(result.mutations[0].value)
    assert state["first"] == "resolved"
    assert next(item for item in state["spatial_scene"]["geometry"]
                if item["id"] == "near-surface")["active"] is False
    assert "source_id" not in shot and "snapshot_id" not in shot


def test_copied_spatial_contact_lists_stay_private_but_scalar_card_values_remain():
    runtime = yaml.safe_load(_runtime([
        {"set": {"field": "_scene_zones", "value": "scene_zones"}},
        {"set": {"field": "_early_copy", "value": "_scene_zones"}},
        _effect("snapshot", store_as="_shot_snapshot"),
        _effect("ray", store_as="_ray", snapshot="{_shot_snapshot}"),
        {"set": {"field": "_contacts", "value": []}},
        {"set": {"field": "_intersections", "value": "_ray.intersections"}},
        {"for_each": {"collection": "_intersections", "as": "_contact", "do": [
            {"set": {"field": "_display_distance", "value": "_contact.distance"}},
            {"set": {"field": "_copy", "value": {
                "kind": "_contact.kind", "id": "_contact.id", "point": "_contact.point",
                "cue": "'stop'",
            }}},
            {"list_push": {"list": "_contacts", "value": "_copy"}},
        ]}},
        _shot(contacts="{_contacts}"),
        {"list_pop": {"list": "_contacts"}, "store_as": "_popped_contact"},
    ]))
    runtime["tools"]["inspect_scene"]["parameters"] = [{
        "name": "scene_zones", "type": "array", "from_state": "state.spatial_scene.zones",
        "items": {"type": "object"},
    }]
    mechanic = _mechanic(yaml.safe_dump(runtime, sort_keys=False).encode())
    result = mechanic(_context(_visible_scene()))
    context = result.result["context"]
    assert isinstance(context["_display_distance"], (int, float))
    assert not {"_ray", "_intersections", "_contact", "_copy", "_contacts",
                "_scene_zones", "_early_copy", "_popped_contact"}.intersection(context)


def test_failed_action_never_returns_partial_effects():
    stale = _mutation_effect()
    stale["spatial_mutation"]["expected_revision"] = 999
    mechanic = _mechanic(_runtime([
        _effect("snapshot", store_as="_shot_snapshot"), _shot(),
        stale,
    ]))
    with pytest.raises(PackageRuntimeError, match="execution failed"):
        mechanic(_context(_visible_scene()))


def test_saved_effects_reopen_exactly_and_panel_feed_requires_an_explicit_current_cursor(tmp_path):
    runtime = _runtime([
        _effect("snapshot", store_as="_shot_snapshot"), _shot(), _shot(delay=100),
        {"set": {"field": "state.first", "value": "'resolved'"}},
    ])
    database = tmp_path / "shots.db"
    command = ActionCommand(
        campaign_id="shots", action_id="fire", idempotency_key="fire", owner_id="shots",
        writer=WriterRole.DM, expected_revision=0, turn_id="turn-shots",
        package_id=_PACKAGE_ID, tool_name="inspect_scene", parameters={},
    )
    with AuthorityOwner.open(database, "shots") as owner:
        candidate = owner.install_package_archive(io.BytesIO(_archive(runtime)), install_id="shots")
        owner.create_campaign(CampaignCreate(
            campaign_id="shots", display_name="Shots", writer=WriterRole.DM,
            package_lock=candidate.package_lock, turn_id=command.turn_id,
            initial_state=(StateValue(_PACKAGE_ID, "state", {
                "spatial_scene": _visible_scene(), "first": None, "second": None,
            }),),
        ))
        assert not owner.campaign_panels("shots")["shot_effects"]
        result = StatelessMcpMechanics(owner).execute_action(command)
        assert result.status is ActionStatus.COMMITTED
        assert not owner.campaign_panels("shots")["shot_effects"]
        feed = thaw_json_value(owner.campaign_panels("shots", after_revision=0)["shot_effects"])
        assert len(feed) == 1 and len(feed[0]["shots"]) == 2
        assert feed[0]["action_id"] == "fire" and feed[0]["revision"] == 1
        saved = deepcopy(thaw_json_value(result.result["shot_effects"]))
    with AuthorityOwner.open(database, "shots") as owner:
        retry = StatelessMcpMechanics(owner).execute_action(command)
        assert thaw_json_value(retry.result["shot_effects"]) == saved
        assert not owner.campaign_panels("shots")["shot_effects"]
        assert not owner.campaign_panels("shots", after_revision=1)["shot_effects"]
        current_feed = owner.campaign_panels("shots", after_revision=0)["shot_effects"]
        assert thaw_json_value(current_feed) == feed
