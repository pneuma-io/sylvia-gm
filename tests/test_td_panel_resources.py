"""Authored panel maxima and physical magazine metadata survive normal reload and reopen."""

import json
import zipfile

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import StateValue, WriterRole
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.td_source_fixture import candidate as candidate

NS = "pneuma-io.trigger-discipline"


def test_resource_projection_and_reload_keep_authored_maxima_and_capacity(candidate, tmp_path):
    database = tmp_path / "resources.sqlite3"
    with zipfile.ZipFile(candidate) as archive:
        state = json.loads(archive.read(f"packages/{NS}/conformance/scenario.json"))[
            "initial_state"
        ]
    actor = state[NS + ".entities"]["vesper"]
    actor.update(hp=3, res=2)
    actor["weapon"]["magazine"]["rounds"] = 7
    with admitted_fixture_owner(database, "owner", candidate.read_bytes()) as (owner, admitted):
        owner.create_campaign(CampaignCreate(
            campaign_id="resources", display_name="Synthetic resource projection",
            writer=WriterRole.DM, package_lock=admitted.package_lock, turn_id="turn",
            initial_state=tuple(
                StateValue(NS, key.removeprefix(NS + "."), value) for key, value in state.items()
            ),
        ))
        owner.create_agent_session(campaign_id="resources", session_id="dm", role=WriterRole.DM,
                                   provider_id="synthetic", model_id="synthetic")
        before = owner.snapshot("resources")
        panels = owner.campaign_panels("resources")
        for surface in panels["surfaces"]:
            if surface["id"] in {"operations.party", "operations.character", "operations.loadout"}:
                visible = surface["data"]["entities"]["vesper"]
                assert (visible["hp"], visible["max_hp"], visible["res"], visible["max_res"]) == (
                    3, 11, 2, 10,
                )
                assert visible["weapon"]["label"] == "Service rifle"
                assert visible["weapon"]["magazine"]["capacity"] == 30
        assert owner.snapshot("resources") == before
        command = {
            "campaign_id": "resources", "session_id": "dm", "surface_id": "operations.character",
            "panel_action_id": "reload-retain", "operation_id": "reload", "expected_revision": 0,
            "selection": {"actor": "vesper"},
        }
        receipt = owner.execute_panel_action(**command)
        after = owner.snapshot("resources")
        weapon = after.state[NS + ".entities"]["vesper"]["weapon"]
        assert weapon["magazine"]["rounds"] == weapon["magazine"]["capacity"] == 30
        assert weapon["retained_magazines"][0]["rounds"] == 7
        assert weapon["retained_magazines"][0]["capacity"] == 30
        assert owner.execute_panel_action(**command) == receipt
        assert owner.snapshot("resources") == after
    with AuthorityOwner.open(database, "reopened") as owner:
        assert owner.snapshot("resources") == after
