"""Real owner queries filter irrelevant seams and retain atomic physical refusal."""

import io

import pytest

from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import StateValue, WriterRole, thaw_json_value
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.test_spatial_grid_edge_exposure import as_path, assert_open, exposure_seam
from tests.test_spatial_runtime import _PACKAGE_ID, _archive, _runtime


@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("eligibility", ["clear", "metadata", "opaque"])
@pytest.mark.parametrize("kind", ["exposure", "exposure_path", "ray"])
def test_owner_eligibility_and_physical_refusal_survive_reopen(
    tmp_path, horizontal, reverse, eligibility, kind,
):
    scene, query, center = exposure_seam(horizontal, reverse, eligibility)
    scene["map"]["id"] = query["map_id"] = "runtime-map"
    for row in [*scene["geometry"], *scene["entities"], *scene["zones"]]:
        row["map_id"] = "runtime-map"
    if kind == "exposure_path":
        query = as_path(query, center, horizontal)
    elif kind == "ray":
        query = {
            "kind": "ray", "map_id": "runtime-map", "origin": query["observer"]["point"],
            "destination": center, "include": ["geometry"], "max_intersections": 16,
        }
    query.pop("kind")
    # Local writes before the query must roll back with a genuine physical refusal.
    effects = [
        {"set": {"field": "state.ap", "value": "state.ap - 1"}},
        {"spatial_query": {
            "scene": "state.spatial_scene", "operation": kind, "query": query,
        }, "store_as": "_observed"},
        {"set": {"field": "state.observed", "value": "_observed"}},
        {"set": {"field": "state.ap", "value": "state.ap + 1"}},
    ]
    archive = _archive(_runtime(effects))
    database = tmp_path / "eligibility.sqlite3"
    initial = {"spatial_scene": scene, "ap": 7, "ammo": 5, "hp": 19}
    refuses = kind == "ray" or eligibility == "opaque"
    with AuthorityOwner.open(database, "eligibility-owner") as owner:
        package = owner.install_package_archive(io.BytesIO(archive), install_id="synthetic")
        owner.create_campaign(CampaignCreate(
            campaign_id="eligibility", display_name="Synthetic seam eligibility",
            writer=WriterRole.DM, package_lock=package.package_lock, turn_id="turn",
            initial_state=(StateValue(_PACKAGE_ID, "state", initial),),
        ))
        before = owner.snapshot("eligibility")
    # An old scene without continuity IDs is loaded normally before any action.
    with AuthorityOwner.open(database, "eligibility-owner") as owner:
        assert owner.snapshot("eligibility") == before
        assert thaw_json_value(before.state[_PACKAGE_ID + ".state"]) == initial
        command = ActionCommand(
            campaign_id="eligibility", action_id="query", idempotency_key="query",
            owner_id="eligibility-owner", writer=WriterRole.DM,
            expected_revision=before.revision, turn_id="turn", package_id=_PACKAGE_ID,
            tool_name="inspect_scene", parameters={},
        )
        if refuses:
            with pytest.raises(RulesetPreconditionRejected, match="map author"):
                owner.execute_action(command)
            assert owner.snapshot("eligibility") == before
        else:
            receipt = owner.execute_action(command)
            assert receipt.status.value == "committed"
            state = thaw_json_value(owner.snapshot("eligibility").state[_PACKAGE_ID + ".state"])
            observed = state.pop("observed")
            assert_open(observed, kind, center)
            assert state == initial  # Geometry, HP, AP, ammo and map revision unchanged.
            assert owner.execute_action(command) == receipt
        after = owner.snapshot("eligibility")
    with AuthorityOwner.open(database, "eligibility-owner") as owner:
        assert owner.snapshot("eligibility") == after
        if refuses:
            with pytest.raises(RulesetPreconditionRejected, match="map author"):
                owner.execute_action(command)
        else:
            assert owner.execute_action(command) == receipt
        assert owner.snapshot("eligibility") == after
