"""Map bodies bind to declared actor references before player controls and mechanics."""

import io
import json
import zipfile
from copy import deepcopy

import pytest
from tests.test_coordinate_map_ui import coordinate_archive, coordinate_surface
from tests.test_tactical_grid_ui import _scene

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.tactical_grid import (
    TacticalGridError,
    project_coordinate_map,
    resolve_tactical_action_parameters,
)
from sylvia_gm.domain.authority import (
    ActionStatus,
    StateValue,
    WriterRole,
    owned_payload,
    thaw_json_value,
)
from sylvia_gm.domain.package_library import canonical_json_bytes, content_root_for_record


def scene_with_actor_reference():
    scene = _scene()
    scene["entities"][0]["metadata"]["participant_ref"] = "operator-character"
    return scene


def reference_action():
    action = coordinate_surface()["actions"][0]
    action["actor_reference"] = "participant_ref"
    return action


def test_map_binds_selected_body_to_declared_actor_and_keeps_target_point():
    scene = scene_with_actor_reference()
    projection = project_coordinate_map({"scene": scene}, actor_reference="participant_ref")
    assert projection["entities"][0]["id"] == "operator"
    assert projection["entities"][0]["actor_ref"] == "operator-character"
    params = resolve_tactical_action_parameters(
        reference_action(),
        {
            "actor": "operator",
            "aim_point": [2.125, 1.375, 0.75],
            "map_revision": 7,
        },
        projection,
    )
    assert params["actor"] == "operator-character"
    assert tuple(params["destination"]) == (2.125, 1.375, 0.75)
    assert scene == scene_with_actor_reference()


@pytest.mark.parametrize("bad_reference", [None, "", "   ", 3, {"id": "operator-character"}])
def test_invalid_or_missing_declared_reference_cannot_fall_back_to_body_identity(bad_reference):
    scene = scene_with_actor_reference()
    scene["entities"][0]["metadata"]["participant_ref"] = bad_reference
    projection = project_coordinate_map({"scene": scene}, actor_reference="participant_ref")
    assert projection["entities"][0]["actor_ref"] is None
    with pytest.raises(TacticalGridError):
        resolve_tactical_action_parameters(
            reference_action(),
            {
                "actor": "operator",
                "aim_point": [2, 2, 0],
                "map_revision": 7,
            },
            projection,
        )


def test_client_cannot_submit_actor_reference_or_select_hidden_body():
    projection = project_coordinate_map(
        {"scene": scene_with_actor_reference()}, actor_reference="participant_ref"
    )
    for patch in (
        {"actor": "operator-character"},
        {"actor_ref": "operator-character"},
        {"actor": "ambusher"},
    ):
        with pytest.raises(TacticalGridError):
            resolve_tactical_action_parameters(
                reference_action(),
                {
                    "actor": "operator",
                    "aim_point": [2, 2, 0],
                    "map_revision": 7,
                    **patch,
                },
                projection,
            )


def test_hidden_duplicate_active_body_does_not_make_an_actor_ambiguously_selectable():
    scene = scene_with_actor_reference()
    duplicate = deepcopy(scene["entities"][0])
    duplicate["id"] = "hidden-proxy"
    duplicate["metadata"].pop("player")
    scene["entities"].append(duplicate)
    projection = project_coordinate_map({"scene": scene}, actor_reference="participant_ref")
    assert len(projection["entities"]) == 1
    assert projection["entities"][0]["actor_ref"] is None
    assert "hidden-proxy" not in json.dumps(thaw_json_value(projection))


def reference_archive():
    with zipfile.ZipFile(io.BytesIO(coordinate_archive())) as archive:
        payloads = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(payloads["manifest.json"])
    record = manifest["packages"][0]
    record["ui_surfaces"][0]["actions"][0]["actor_reference"] = "participant_ref"
    record["content_sha256"] = content_root_for_record(record)
    payloads["manifest.json"] = canonical_json_bytes(manifest)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in payloads.items():
            archive.writestr(name, payload)
    return output.getvalue()


def test_actual_owner_panel_dispatch_resolves_mechanical_actor_before_execution(tmp_path):
    with AuthorityOwner.open(tmp_path / "refs.sqlite3", "refs-owner") as owner:
        owner.set_admin_full_audit(True)
        installed = owner.install_package_archive(
            io.BytesIO(reference_archive()), install_id="refs-install"
        )
        owner.create_campaign(
            CampaignCreate(
                campaign_id="refs",
                display_name="Synthetic coordinate actor references",
                writer=WriterRole.DM,
                package_lock=installed.package_lock,
                turn_id="refs-turn",
                initial_state=(
                    StateValue(
                        "synthetic.coordinate",
                        "state",
                        {
                            "spatial_scene": scene_with_actor_reference(),
                            "last_actor": None,
                            "last_destination": None,
                        },
                    ),
                ),
            )
        )
        panel = owner.campaign_panels("refs")["surfaces"][0]
        assert panel["data"]["entities"][0]["actor_ref"] == "operator-character"
        receipt = owner.execute_panel_action(
            campaign_id="refs",
            surface_id="operations.map",
            panel_action_id="move",
            operation_id="refs-move",
            expected_revision=0,
            selection=owned_payload(
                {"actor": "operator", "aim_point": [2.125, 1.375, 0.75], "map_revision": 7}
            ),
        )
        assert receipt.status is ActionStatus.COMMITTED
        state = owner.snapshot("refs").state["synthetic.coordinate.state"]
        assert state["last_actor"] == "operator-character"
        assert tuple(state["last_destination"]) == (2.125, 1.375, 0.75)
