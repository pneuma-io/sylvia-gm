"""Authored FIELD WORK reorientation through admitted owner and player contracts."""

import hashlib
import json
import zipfile
from copy import deepcopy
from dataclasses import replace

import pytest

from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.application.plan_state_admission import validate_plan_state
from sylvia_gm.domain.authority import WriterRole, thaw_json_value
from sylvia_gm.domain.content_validation import ContentShapeError
from sylvia_gm.domain.errors import CommandRejected, IdempotencyConflict, StaleRevision
from sylvia_gm.domain.package_library import manifest_from_record
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.test_td_ordinary_movement import SESSION
from tests.test_td_rifleman_entry_boundaries import scene_for, start, step
from tests.td_source_fixture import candidate as candidate_fixture

candidate = candidate_fixture
NS = "pneuma-io.trigger-discipline"
ENTRY = "specialty.field_engineer.entry.field_work"
DEVICE = "FIELD-SENSOR-RELAY-NODE-1"
TOOL = "sensor-tool"
MATERIAL = "sensor-mount"


def field_scene(candidate, enabled=True, mirrored=False):
    scene = scene_for(candidate, mirrored, (ENTRY,) if enabled else ())
    actor = scene.actors[scene.shooter]
    actor.update(class_id="TECH")
    map_id = scene.world["spatial_scene"]["map"]["id"]
    scene.actors[DEVICE] = {
        "id": DEVICE,
        "name": "Field sensor / relay node",
        "kind": "FIELD_SENSOR_RELAY_NODE",
        "active": True,
        "already_deployed": True,
        "map_id": map_id,
        "position_cell": deepcopy(actor["position_cell"]),
        "physical_configuration": {"orientation": "NORTH", "mounting": "original mount"},
        "capabilities": {
            "sensor_range": 10,
            "power": "original",
            "privilege": 0,
            "software": ["original sensor function"],
        },
        "field_work": {
            "compatible": True,
            "qualified_actors": [scene.shooter],
            "physical_access": {
                scene.shooter: {
                    "actor_cell": deepcopy(actor["position_cell"]),
                    "device_cell": deepcopy(actor["position_cell"]),
                }
            },
            "tool_id": TOOL,
            "material_id": MATERIAL,
        },
    }
    actor["engineering_tool"] = {
        "id": TOOL,
        "kind": "ENGINEERING_TOOL",
        "active": True,
        "carrier_id": scene.shooter,
        "location": "READY",
        "compatible_devices": [DEVICE],
    }
    scene.actors[DEVICE]["mounting_hardware"] = {
        "id": MATERIAL,
        "kind": "MOUNTING_HARDWARE",
        "active": True,
        "installed_on": DEVICE,
    }
    # Existing map device selection requires a visible physical entity, not a fabricated target.
    scene.world["spatial_scene"]["entities"].append(
        {
            "id": DEVICE,
            "map_id": map_id,
            "position": [actor["position_cell"][0] + 0.5, actor["position_cell"][1] + 0.5, 0],
            "footprint": {"kind": "box", "half_extents": [0.1, 0.1]},
            "height": 0.3,
            "facing": [0, -1],
            "active": True,
            "movement": "passable",
            "visibility": "clear",
            "metadata": {"player": {"label": "Field sensor / relay node", "kind": "device"}},
        }
    )
    return scene


def parameters(game, **changes):
    return {
        "actor": game.shooter,
        "device": DEVICE,
        "expected_orientation": "NORTH",
        "orientation": "EAST",
        **changes,
    }


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("mirrored", [False, True])
def test_admitted_field_work_changes_only_orientation_and_exact_authored_ap(
    candidate,
    tmp_path,
    enabled,
    mirrored,
):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3",
        "aim-owner",
        candidate.read_bytes(),
    ) as (owner, admitted):
        game = start(owner, admitted, field_scene(candidate, enabled, mirrored))
        expected = deepcopy(game.actors)
        expected[game.shooter]["ap"] -= 2 if enabled else 3
        expected[DEVICE]["physical_configuration"]["orientation"] = "EAST"
        assert step(owner, game, "reorient_field_node", parameters(game)) == 0
        receipt = game.entry_calls[-1][1]
        assert receipt.status.value == "committed", thaw_json_value(receipt.result)
        assert game.actors == expected
        assert game.world["last_field_work"]["status"] == "REORIENTED"
        assert game.world["last_field_work"]["ap_spent"] == (2 if enabled else 3)


def request(owner, game, identity="reorient"):
    panel = next(
        row
        for row in owner.campaign_panels(game.campaign)["surfaces"]
        if row["id"] == "operations.map"
    )
    action = next(row for row in panel["actions"] if row["id"] == "field-reorient-east")
    assert action["intent"] == "reorient_field_node"
    assert DEVICE in {row["id"] for row in panel["data"]["entities"]}
    return {
        "campaign_id": game.campaign,
        "session_id": f"{SESSION}-{game.campaign}",
        "surface_id": panel["id"],
        "panel_action_id": action["id"],
        "operation_id": identity,
        "expected_revision": owner.snapshot(game.campaign).revision,
        "selection": {
            "actor": game.shooter,
            "device": DEVICE,
            "map_revision": panel["data"]["map_revision"],
        },
    }


@pytest.mark.parametrize("enabled", [False, True])
def test_player_preview_queue_reopen_export_recovery_and_exact_once(candidate, tmp_path, enabled):
    database = tmp_path / "authority.sqlite3"
    export = tmp_path / "field-work.sylvia-gm-campaign"
    with admitted_fixture_owner(database, "aim-owner", candidate.read_bytes()) as (owner, admitted):
        scene = field_scene(candidate, enabled)
        scene.actors[scene.shooter]["ap"] = 2 if enabled else 3
        game = start(owner, admitted, scene)
        before = owner.snapshot(game.campaign)
        proposed = request(owner, game)
        source = {
            "kind": "panel",
            "surface_id": "operations.map",
            "action_id": "field-reorient-east",
        }
        available = owner.player_action_availability(
            campaign_id=game.campaign,
            expected_revision=before.revision,
            requests=[{"source": source, "selection": proposed["selection"]}],
        )["results"][0]
        assert available["available"], available
        preview = owner.preview_panel_action(**proposed)
        cost = next(
            row["value"] for row in preview["preview"]["summary"] if row["label"] == "AP cost"
        )
        assert cost == (2 if enabled else 3)
        assert owner.snapshot(game.campaign) == before
        sequence = owner.compose_action_sequence(
            campaign_id=game.campaign,
            sequence_id="field-work-queue",
            session_id=proposed["session_id"],
            expected_revision=before.revision,
            steps=[{"step_id": "node-east", "source": source, "selection": proposed["selection"]}],
        )
        assert owner.snapshot(game.campaign) == before
    with AuthorityOwner.open(database, "aim-owner") as owner:
        assert owner.snapshot(game.campaign) == before
        result = owner.execute_action_sequence(
            campaign_id=game.campaign,
            sequence_id="field-work-queue",
            session_id=proposed["session_id"],
            expected_version=sequence["version"],
            confirmation=sequence["confirmation"],
        )
        assert result["steps"][0]["status"] == "committed", result
        final = owner.snapshot(game.campaign)
        entities = final.state[NS + ".entities"]
        assert entities[game.shooter]["ap"] == 0
        assert entities[DEVICE]["physical_configuration"]["orientation"] == "EAST"
        assert final.state[NS + ".state"]["last_field_work"]["ap_spent"] == cost
        owner.export_campaign("export-field-work", game.campaign, export)
    with AuthorityOwner.open(tmp_path / "restored.sqlite3", "aim-owner") as owner:
        recovered = owner.recover_campaign(recovery_id="recover-field-work", source=export)
        assert owner.recover_campaign(recovery_id="recover-field-work", source=export) == recovered
        assert owner.snapshot(game.campaign) == final
        assert (
            owner.execute_action_sequence(
                campaign_id=game.campaign,
                sequence_id="field-work-queue",
                session_id=proposed["session_id"],
                expected_version=sequence["version"],
                confirmation=sequence["confirmation"],
            )
            == result
        )
        assert owner.snapshot(game.campaign) == final
        with pytest.raises(StaleRevision):
            owner.preview_panel_action(**{**proposed, "operation_id": "stale"})
        with pytest.raises(CommandRejected):
            owner.preview_panel_action(**request(owner, game, "already-east"))
        assert owner.snapshot(game.campaign) == final
    with AuthorityOwner.open(tmp_path / "restored.sqlite3", "aim-owner") as owner:
        assert owner.snapshot(game.campaign) == final
    (tmp_path / "proof.json").write_text(
        json.dumps(
            {
                "enabled": enabled,
                "ap": cost,
                "revision": final.revision,
                "package_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                "state_sha256": hashlib.sha256(
                    json.dumps(thaw_json_value(final.state), sort_keys=True).encode()
                ).hexdigest(),
                "export_sha256": hashlib.sha256(export.read_bytes()).hexdigest(),
                "result": thaw_json_value(result),
            },
            indent=2,
        )
        + "\n"
    )


def mutate(scene, case):
    actor, device = scene.actors[scene.shooter], scene.actors[DEVICE]
    args = {}
    if case == "wrong-role":
        actor["class_id"] = "RIFLEMAN"
    elif case == "inactive":
        actor["active"] = False
    elif case == "downed":
        actor["downed"] = True
    elif case == "ap":
        actor["ap"] = 1
    elif case == "undeployed":
        device["already_deployed"] = False
    elif case == "inactive-device":
        device["active"] = False
    elif case == "wrong-device":
        device["kind"] = "OTHER_DEVICE"
    elif case == "incompatible":
        device["field_work"]["compatible"] = False
    elif case == "no-work-contract":
        device.pop("field_work")
    elif case == "other-map":
        device["map_id"] = "another-map"
    elif case == "remote":
        device["position_cell"][0] += 1
    elif case == "no-access":
        device["field_work"]["physical_access"] = {}
    elif case == "skill":
        device["field_work"]["qualified_actors"] = []
    elif case == "no-tool":
        actor.pop("engineering_tool")
    elif case == "wrong-tool":
        actor["engineering_tool"]["id"] = "different-tool"
    elif case == "tool-inactive":
        actor["engineering_tool"]["active"] = False
    elif case == "wrong-carrier":
        actor["engineering_tool"]["carrier_id"] = scene.mover
    elif case in {"tool-stowed", "tool-quick"}:
        actor["engineering_tool"]["location"] = (
            "STOWED" if case.endswith("stowed") else "QUICK_ACCESS"
        )
    elif case == "wrong-tool-compatibility":
        actor["engineering_tool"]["compatible_devices"] = []
    elif case == "no-material":
        device.pop("mounting_hardware")
    elif case == "wrong-material":
        device["mounting_hardware"]["id"] = "different-mount"
    elif case == "material-inactive":
        device["mounting_hardware"]["active"] = False
    elif case == "uninstalled-material":
        device["mounting_hardware"]["installed_on"] = "other-device"
    elif case == "stale-config":
        device["physical_configuration"]["orientation"] = "WEST"
    elif case == "unchanged-config":
        args["orientation"] = "NORTH"
    elif case == "unsupported-config":
        args["orientation"] = "SOUTH"
    else:
        raise AssertionError(case)
    return args


@pytest.mark.parametrize("mirrored", [False, True])
def test_prerequisites_are_real_atomic_and_shared_with_player_checks(candidate, tmp_path, mirrored):
    cases = [
        "wrong-role",
        "inactive",
        "downed",
        "ap",
        "undeployed",
        "inactive-device",
        "wrong-device",
        "incompatible",
        "no-work-contract",
        "other-map",
        "remote",
        "no-access",
        "skill",
        "no-tool",
        "wrong-tool",
        "tool-inactive",
        "wrong-carrier",
        "tool-stowed",
        "tool-quick",
        "wrong-tool-compatibility",
        "no-material",
        "wrong-material",
        "material-inactive",
        "uninstalled-material",
        "stale-config",
        "unchanged-config",
        "unsupported-config",
    ]
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        for case in cases:
            scene = field_scene(candidate, mirrored=mirrored)
            changed = mutate(scene, case)
            game = start(owner, admitted, scene, campaign=case)
            before = owner.snapshot(game.campaign)
            # No request may manufacture physical access or prerequisite objects.
            if not mirrored and not changed:
                with pytest.raises(CommandRejected):
                    owner.preview_panel_action(**request(owner, game, case))
                assert owner.snapshot(game.campaign) == before
            with pytest.raises(CommandRejected):
                step(owner, game, "reorient_field_node", parameters(game, **changed))
            assert owner.snapshot(game.campaign) == before, case


def test_control_cost_override_duplicate_and_fresh_unchanged_rejection(candidate, tmp_path):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, field_scene(candidate))
        before = owner.snapshot(game.campaign)
        proposed = request(owner, game)
        with pytest.raises(CommandRejected):
            owner.preview_panel_action(
                **{
                    **proposed,
                    "selection": {
                        **proposed["selection"],
                        "actor": game.mover,
                    },
                }
            )
        with pytest.raises(CommandRejected):
            step(owner, game, "reorient_field_node", parameters(game, ap_cost=0))
        assert owner.snapshot(game.campaign) == before
        receipt = owner.execute_panel_action(**proposed)
        final = owner.snapshot(game.campaign)
        assert owner.execute_panel_action(**proposed) == receipt
        assert owner.snapshot(game.campaign) == final
        with pytest.raises(CommandRejected):
            step(owner, game, "reorient_field_node", parameters(game))
        assert owner.snapshot(game.campaign) == final


@pytest.mark.parametrize("mirrored", [False, True])
def test_chat_receipt_identity_survives_recovery_without_second_effect(
    candidate, tmp_path, mirrored
):
    database, exported = tmp_path / "authority.sqlite3", tmp_path / "chat.sylvia-gm-campaign"
    with admitted_fixture_owner(database, "aim-owner", candidate.read_bytes()) as (owner, admitted):
        game = start(owner, admitted, field_scene(candidate, mirrored=mirrored))
        command = ActionCommand(
            campaign_id=game.campaign,
            action_id="chat-field-work",
            idempotency_key="chat-field-work",
            owner_id="aim-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="turn",
            package_id=NS,
            tool_name="reorient_field_node",
            parameters=parameters(game),
        )
        receipt = owner.execute_action(command)
        assert receipt.status.value == "committed", receipt
        final = owner.snapshot(game.campaign)
        assert final.state[NS + ".entities"][game.shooter]["ap"] == 4
        assert (
            final.state[NS + ".entities"][DEVICE]["physical_configuration"]["orientation"] == "EAST"
        )
        owner.export_campaign("chat-field-work", game.campaign, exported)
    with AuthorityOwner.open(database, "aim-owner") as owner:
        assert owner.execute_action(command) == receipt
        assert owner.snapshot(game.campaign) == final
    with AuthorityOwner.open(tmp_path / "restored.sqlite3", "aim-owner") as owner:
        recovery = owner.recover_campaign(recovery_id="chat-field-work", source=exported)
        assert owner.recover_campaign(recovery_id="chat-field-work", source=exported) == recovery
        assert owner.execute_action(command) == receipt
        with pytest.raises(IdempotencyConflict):
            owner.execute_action(replace(command, parameters=parameters(game, orientation="SOUTH")))
        assert owner.snapshot(game.campaign) == final


def test_typed_equipment_admission_rejects_bad_fields(candidate, tmp_path):
    with zipfile.ZipFile(candidate) as package:
        manifest = manifest_from_record(
            next(
                row
                for row in json.loads(
                    package.read("manifest.json"),
                )["packages"]
                if row["id"] == NS
            )
        )
    for _label, change in [
        ("capability", lambda s: s.actors[DEVICE]["field_work"].update(qualified_actors=True)),
        ("orientation", lambda s: s.actors[DEVICE]["physical_configuration"].update(orientation=7)),
        ("material", lambda s: s.actors[DEVICE]["mounting_hardware"].update(active="yes")),
        ("tool", lambda s: s.actors[s.shooter]["engineering_tool"].update(location="INVENTED")),
    ]:
        scene = field_scene(candidate)
        payload = {
            "initial_state": {
                "mechanics": [
                    {
                        "namespace": NS,
                        "key": "entities",
                        "value": scene.actors,
                    }
                ]
            }
        }
        validate_plan_state(payload, manifest)
        change(scene)
        with pytest.raises(ContentShapeError):
            validate_plan_state(payload, manifest)


def test_package_keeps_exact_canon_and_other_cost_contracts(candidate):
    with zipfile.ZipFile(candidate) as package:
        assert hashlib.sha256(
            package.read(f"packages/{NS}/references/canon/Specialties/FIELD ENGINEER.md")
        ).hexdigest() == ("a30d257a0b71d74c86b7ef3e805c9231f4953060d43910344728f65fa37cdf8f")
        runtime = json.loads(package.read(f"packages/{NS}/runtime/ruleset.yaml"))
        tool = runtime["tools"]["reorient_field_node"]
        assert "ap_cost" not in {p["name"] for p in tool["parameters"]}
        assert "physical_configuration.orientation" in json.dumps(tool)
        assert package.read(f"packages/{NS}/references/field-work.md")
