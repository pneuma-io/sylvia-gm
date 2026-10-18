"""Coordinate intent admission and the actual panel-to-owner dispatch boundary."""

import hashlib
import io
import json
import zipfile
from copy import deepcopy

import pytest
import yaml
from tests.test_tactical_grid_ui import _installed_archive, _record, _scene, _surface

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
from sylvia_gm.domain.package_library import (
    canonical_json_bytes,
    content_root_for_record,
    manifest_from_record,
)


def coordinate_surface():
    surface = _surface("coordinate_map")
    surface["actions"][0]["bindings"] = {"actor": "actor", "destination": "aim_point"}
    return surface


def test_manifest_admits_coordinate_intent_without_grid_binding():
    record = _record(coordinate_surface())
    assert (
        manifest_from_record(record, format_version=1).ui_surfaces[0].renderer == "coordinate_map"
    )
    record = _record(coordinate_surface())
    record["ui_surfaces"][0]["actions"][0]["bindings"]["destination"] = "destination"
    record["content_sha256"] = content_root_for_record(record)
    with pytest.raises(ValueError, match="binding|Content fields do not match"):
        manifest_from_record(record, format_version=1)


def test_projection_uses_actual_positions_and_omits_hidden_and_inactive_bodies():
    scene = _scene()
    inactive = deepcopy(scene["entities"][0])
    inactive.update(id="retired", active=False)
    scene["entities"].append(inactive)
    projected = thaw_json_value(project_coordinate_map({"scene": scene}))
    assert [item["id"] for item in projected["entities"]] == ["operator"]
    assert projected["entities"][0]["position"] == scene["entities"][0]["position"]
    assert "concealed-hostile" not in json.dumps(projected)
    assert "gm_secret" not in json.dumps(projected)


def test_binding_keeps_unsnapped_3d_point():
    projection = project_coordinate_map({"scene": _scene()})
    selection = {"actor": "operator", "aim_point": [2.125, 1.375, 0.75], "map_revision": 7}
    params = thaw_json_value(
        resolve_tactical_action_parameters(
            coordinate_surface()["actions"][0], selection, projection
        )
    )
    assert params == {"actor": "operator", "destination": [2.125, 1.375, 0.75], "mode": "careful"}


@pytest.mark.parametrize(
    "patch",
    [
        {"map_revision": 6},
        {"actor": "ambusher"},
        {"aim_point": [2, 2, 4]},
        {"aim_point": [2, 2]},
        {"path": [[0, 0], [1, 1]]},
        {"aim_point": [float("nan"), 2, 0]},
    ],
)
def test_binding_rejects_stale_hidden_invalid_or_derived_intent(patch):
    projection = project_coordinate_map({"scene": _scene()})
    selection = {"actor": "operator", "aim_point": [2.125, 1.375, 0.75], "map_revision": 7, **patch}
    with pytest.raises(TacticalGridError):
        resolve_tactical_action_parameters(
            coordinate_surface()["actions"][0], selection, projection
        )


def coordinate_archive(*, gated=False):
    with zipfile.ZipFile(io.BytesIO(_installed_archive())) as archive:
        record = json.loads(archive.read("manifest.json"))["packages"][0]
        runtime = yaml.safe_load(archive.read("packages/synthetic.coordinate/runtime/ruleset.yaml"))
    record["ui_surfaces"][0] = coordinate_surface()
    if gated:
        record["ui_surfaces"][0]["source"]["fields"]["enabled"] = {
            "kind": "state",
            "namespace": "synthetic.coordinate",
            "key": "state.combat_active",
        }
    schema = record["tools"][0]["input_schema"]["properties"]["destination"]
    schema.update(items={"type": "number"}, minItems=3, maxItems=3)
    destination = runtime["tools"]["move_unit"]["parameters"][1]
    destination.update(items={"type": "number"}, min_items=3, max_items=3)
    payload = yaml.safe_dump(runtime, sort_keys=False).encode()
    record["files"][0].update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    record["content_sha256"] = content_root_for_record(record)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", canonical_json_bytes({"format": 1, "packages": [record]}))
        archive.writestr("packages/synthetic.coordinate/runtime/ruleset.yaml", payload)
    return output.getvalue()


def test_actual_panel_dispatch_commits_exact_coordinate_intent(tmp_path):
    with AuthorityOwner.open(tmp_path / "coordinate-map.sqlite3", "coordinate-owner") as owner:
        owner.set_admin_full_audit(True)
        installed = owner.install_package_archive(
            io.BytesIO(coordinate_archive()), install_id="coordinate-install"
        )
        owner.create_campaign(
            CampaignCreate(
                campaign_id="coordinate-campaign",
                display_name="Synthetic coordinate test",
                writer=WriterRole.DM,
                package_lock=installed.package_lock,
                turn_id="coordinate-turn",
                initial_state=(
                    StateValue(
                        "synthetic.coordinate",
                        "state",
                        {
                            "spatial_scene": _scene(),
                            "last_actor": None,
                            "last_destination": None,
                        },
                    ),
                ),
            )
        )
        panel = owner.campaign_panels("coordinate-campaign")["surfaces"][0]
        assert panel["renderer"] == "coordinate_map"
        point = [2.125, 1.375, 0.75]
        receipt = owner.execute_panel_action(
            campaign_id="coordinate-campaign",
            surface_id="operations.map",
            panel_action_id="move",
            operation_id="coordinate-move",
            expected_revision=0,
            selection=owned_payload({"actor": "operator", "aim_point": point, "map_revision": 7}),
        )
        assert receipt.status is ActionStatus.COMMITTED
        state = owner.snapshot("coordinate-campaign").state["synthetic.coordinate.state"]
        assert tuple(point) == state["last_destination"]
        assert state["last_actor"] == "operator"
        assert owner.admin_full_audit_enabled


@pytest.mark.parametrize("active", [False, None])
def test_noncombat_map_is_absent_and_cannot_commit_coordinate_movement(tmp_path, active):
    from sylvia_gm.domain.errors import CommandRejected

    with AuthorityOwner.open(tmp_path / "noncombat.sqlite3", "noncombat-owner") as owner:
        owner.set_admin_full_audit(True)
        installed = owner.install_package_archive(
            io.BytesIO(coordinate_archive(gated=True)), install_id="noncombat-install"
        )
        owner.create_campaign(
            CampaignCreate(
                campaign_id="noncombat",
                display_name="Synthetic exploration",
                writer=WriterRole.DM,
                package_lock=installed.package_lock,
                turn_id="noncombat-turn",
                initial_state=(
                    StateValue(
                        "synthetic.coordinate",
                        "state",
                        {
                            "spatial_scene": _scene(),
                            "combat_active": active,
                        },
                    ),
                ),
            )
        )
        assert not any(
            surface["renderer"] == "coordinate_map"
            for surface in owner.campaign_panels("noncombat")["surfaces"]
        )
        with pytest.raises(CommandRejected):
            owner.execute_panel_action(
                campaign_id="noncombat",
                surface_id="operations.map",
                panel_action_id="move",
                operation_id="invalid-noncombat-map-move",
                expected_revision=0,
                selection=owned_payload(
                    {"actor": "operator", "aim_point": [2.125, 1.375, 0.75], "map_revision": 7}
                ),
            )
        assert owner.snapshot("noncombat").revision == 0
