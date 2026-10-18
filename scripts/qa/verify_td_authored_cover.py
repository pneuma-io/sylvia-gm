# This standalone QA CLI binds imports to its own isolated checkout before admission.
# ruff: noqa: E402
import json
import math
import os
import sys
import zipfile
from copy import deepcopy
from pathlib import Path

sys.dont_write_bytecode = True
import argparse

r = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(r / "src"), str(r)]
parser = argparse.ArgumentParser(
    description="Verify authored TD controls with real owner admission, execution and persistence."
)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument(
    "--archive", type=Path, default=r / "tests/fixtures/td-native-panels-0.1.6.sylvia-gm-package"
)
options = parser.parse_args()
p = options.output.parent
(p / "temp").mkdir(exist_ok=True)
for key in ["TEMP", "TMP", "TMPDIR"]:
    os.environ[key] = str(p / "temp")
from tests.admitted_library_fixture import CANDIDATE_ID, prepare_library_database

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import (
    Participant,
    ParticipantValidationStatus,
    StateValue,
    WriterRole,
    thaw_json_value,
)
from sylvia_gm.spatial import SpatialEngine

archive = options.archive
out = options.output
out.mkdir(exist_ok=False)
with zipfile.ZipFile(archive) as z:
    primary = next(
        v
        for v in json.loads(z.read("manifest.json"))["packages"]
        if v["id"] == "pneuma-io.trigger-discipline"
    )
    ns = primary["id"]
    prefix = "packages/" + ns + "/"
    scenario = json.loads(z.read(prefix + "conformance/panel-edge-combat.json"))
    character = json.loads(z.read(prefix + "conformance/character.json"))
artifact = primary["participant_artifacts"][0]
proof = {
    "authored_artifact": "conformance/panel-edge-combat.json",
    "fixture_preserved": True,
    "cases": [],
    "provider_inference": False,
    "physical_input": "not run",
}
for orientation, stance, target, protected, openpoint, edge in [
    ("vertical", "PRONE", [6.5, 2.5, 0], [2.5, 2.5, 0], [8.5, 2.5, 0], "quarter-edge-vertical"),
    ("horizontal", "CROUCHED", [5.5, 6.5, 0], [5.5, 2.5, 0], [5.5, 9.5, 0], "half-edge-horizontal"),
]:
    for angle, origin in [("protected", protected), ("open", openpoint)]:
        case = deepcopy(scenario)
        state = case["initial_state"][ns + ".state"]
        scene = state["spatial_scene"]
        entities = case["initial_state"][ns + ".entities"]
        db = out / f"{orientation}-{angle}.sqlite3"
        heights = {"STANDING": 1.8, "CROUCHED": 1.15, "PRONE": 0.45}
        for ident, point, posture in [
            ("hex", origin, "STANDING"),
            ("hostile_alpha", target, stance),
        ]:
            e = entities[ident]
            h = heights[posture]
            e.update(
                position_cell=[int(point[0]), int(point[1])],
                stance=posture,
                center=[point[0], point[1], h * 0.5],
                muzzle=[point[0] + 0.41, point[1], h * 0.7777777777777778],
            )
            body = next(v for v in scene["entities"] if v["id"] == ident)
            body.update(position=point, height=h)
            body["metadata"]["player"]["stance"] = posture.lower()
            if ident == "hex":
                dx, dy = target[0] - point[0], target[1] - point[1]
                length = math.hypot(dx, dy)
                direction = [dx / length, dy / length]
                e["muzzle"] = [
                    point[0] + direction[0] * 0.41,
                    point[1] + direction[1] * 0.41,
                    h * 0.7777777777777778,
                ]
                body["facing"] = direction
        for body in scene["entities"]:
            if body["id"] not in ["hex", "hostile_alpha"]:
                body["active"] = False
        prepare_library_database(db, archive.read_bytes())
        with AuthorityOwner.open(db, "owned-cover") as owner:
            owner.create_campaign(
                CampaignCreate(
                    campaign_id="cover",
                    display_name=f"Synthetic {orientation} low-edge {angle}",
                    writer=WriterRole.DM,
                    package_lock=owner.package_candidate(CANDIDATE_ID).package_lock,
                    turn_id="turn",
                    participants=(
                        Participant(
                            participant_id="hex",
                            kind=artifact["kind"],
                            package_id=ns,
                            package_version=primary["version"],
                            package_sha256=primary["content_sha256"],
                            schema_id=artifact["schema_id"],
                            schema_sha256=artifact["schema_sha256"],
                            validation_status=ParticipantValidationStatus.VALIDATED,
                            data=character["artifact"],
                        ),
                    ),
                    initial_state=tuple(
                        StateValue(ns, k.removeprefix(ns + "."), v)
                        for k, v in case["initial_state"].items()
                    ),
                )
            )
            owner.create_agent_session(
                campaign_id="cover",
                session_id="panel-gm",
                role=WriterRole.DM,
                provider_id="synthetic",
                model_id="synthetic",
            )
            before = owner.snapshot("cover")
            native_scene = thaw_json_value(before.state[ns + ".state"]["spatial_scene"])
            e = before.state[ns + ".entities"]
            query = {
                "kind": "exposure",
                "map_id": native_scene["map"]["id"],
                "observer": {"point": list(e["hex"]["muzzle"])},
                "target": {"entity_id": "hostile_alpha"},
                "samples": {"horizontal": 3, "vertical": 3},
                "include": ["geometry", "entities"],
            }
            exposure = SpatialEngine(native_scene).query(query).as_payload()
            if angle == "protected":
                assert exposure["exposure"] < 1 and any(
                    v["id"] == edge for v in exposure["occluders"]
                ), exposure
            else:
                assert exposure["exposure"] == 1, exposure
            panels = owner.campaign_panels("cover")
            surface = next(v for v in panels["surfaces"] if v["id"] == "operations.map")
            command = {
                "campaign_id": "cover",
                "surface_id": "operations.map",
                "panel_action_id": "fire",
                "operation_id": "native-fire",
                "expected_revision": before.revision,
                "selection": {
                    "actor": "hex",
                    "target": "hostile_alpha",
                    "aim_point": list(e["hostile_alpha"]["center"]),
                    "map_revision": surface["data"]["map_revision"],
                },
            }
            preview = owner.preview_panel_action(**command)
            assert owner.snapshot("cover") == before
            receipt = owner.execute_panel_action(
                **command, confirmation=preview.get("confirmation")
            )
            assert receipt.status.value == "committed", thaw_json_value(receipt.result)
            after = owner.snapshot("cover")
            assert (
                before.state[ns + ".entities"]["hex"]["ap"]
                - after.state[ns + ".entities"]["hex"]["ap"]
                == 2
            )
            proof["cases"].append(
                {
                    "orientation": orientation,
                    "angle": angle,
                    "stance": stance,
                    "exposure": exposure,
                    "preview": thaw_json_value(preview),
                    "receipt": thaw_json_value(receipt.result),
                    "actual_ap_delta": 2,
                    "scene_geometry": native_scene["geometry"],
                    "source_bound": True,
                }
            )
            (out / f"{orientation}-{angle}-panels.json").write_text(
                json.dumps(thaw_json_value(owner.campaign_panels("cover")), indent=2)
            )
        with AuthorityOwner.open(db, "owned-cover-reopen") as owner:
            assert owner.snapshot("cover") == after
proof["reopen_exact"] = True
proof["passed"] = True
(out / "proof.json").write_text(json.dumps(proof, indent=2))
print(
    json.dumps(
        {
            "passed": True,
            "cases": [
                (c["orientation"], c["angle"], c["exposure"]["exposure"]) for c in proof["cases"]
            ],
            "native_shots": 4,
            "reopen_exact": True,
        }
    )
)
