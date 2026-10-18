# This standalone QA CLI binds imports to its own isolated checkout before admission.
# ruff: noqa: E402
import hashlib
import json
import os
import sys
import zipfile
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
from sylvia_gm.domain.errors import CommandRejected

archive = options.archive
out = options.output
out.mkdir(exist_ok=False)
db = out / "campaign.sqlite3"
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
prepare_library_database(db, archive.read_bytes())
proof = {
    "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    "primary_sha256": primary["content_sha256"],
    "provider_inference": False,
    "physical_input": "not run",
    "steps": [],
}
with AuthorityOwner.open(db, "owned-td-native") as owner:
    candidate = owner.package_candidate(CANDIDATE_ID)
    owner.create_campaign(
        CampaignCreate(
            campaign_id="td-native",
            display_name="Synthetic authored TD edge and equipment QA",
            writer=WriterRole.DM,
            package_lock=candidate.package_lock,
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
                StateValue(ns, key.removeprefix(ns + "."), v)
                for key, v in scenario["initial_state"].items()
            ),
        )
    )
    owner.create_agent_session(
        campaign_id="td-native",
        session_id="panel-gm",
        role=WriterRole.DM,
        provider_id="synthetic",
        model_id="synthetic",
    )
    panels = owner.campaign_panels("td-native")
    (out / "initial-panels.json").write_text(json.dumps(thaw_json_value(panels), indent=2))

    def preview(surface, action, actor):
        before = owner.snapshot("td-native")
        command = {
            "campaign_id": "td-native",
            "surface_id": surface,
            "panel_action_id": action,
            "operation_id": f"qa-{len(proof['steps'])}-{action}",
            "expected_revision": before.revision,
            "selection": {"actor": actor},
        }
        value = owner.preview_panel_action(**command)
        assert owner.snapshot("td-native") == before
        cost = next(
            row["value"] for row in value["preview"]["summary"] if row["label"] == "AP cost"
        )
        receipt = owner.execute_panel_action(**command, confirmation=value.get("confirmation"))
        assert receipt.status.value == "committed", (action, thaw_json_value(receipt.result))
        after = owner.snapshot("td-native")
        delta = (
            before.state[ns + ".entities"][actor]["ap"] - after.state[ns + ".entities"][actor]["ap"]
        )
        assert delta == cost, (action, cost, delta)
        assert owner.execute_panel_action(**command) == receipt
        assert owner.snapshot("td-native") == after
        proof["steps"].append(
            {
                "action": action,
                "actor": actor,
                "preview": thaw_json_value(value),
                "cost": cost,
                "actual_ap_delta": delta,
                "exact_retry": True,
                "receipt": thaw_json_value(receipt.result),
            }
        )

    preview("operations.character", "stance-crouched", "vesper")
    preview("operations.character", "stance-standing", "vesper")
    preview("operations.character", "reload-retain", "hex")
    before = owner.snapshot("td-native")
    try:
        owner.preview_panel_action(
            campaign_id="td-native",
            surface_id="operations.character",
            panel_action_id="stance-standing",
            operation_id="hostile-rejected",
            expected_revision=before.revision,
            selection={"actor": "hostile_alpha"},
        )
    except CommandRejected as e:
        proof["enemy_rejection"] = str(e)
        assert "not currently controlled" in str(e)
    else:
        raise AssertionError("Enemy selected through unit_list must remain rejected")
    assert owner.snapshot("td-native") == before
    saved = owner.snapshot("td-native")
    proof["saved_revision"] = saved.revision
    (out / "after-panels.json").write_text(
        json.dumps(thaw_json_value(owner.campaign_panels("td-native")), indent=2)
    )
with AuthorityOwner.open(db, "owned-reopen") as owner:
    assert owner.snapshot("td-native") == saved
proof["reopen_exact"] = True
(out / "proof.json").write_text(json.dumps(proof, indent=2))
print(
    json.dumps(
        {
            "passed": True,
            "steps": [(s["action"], s["cost"]) for s in proof["steps"]],
            "enemy_atomic_rejection": True,
            "reopen_exact": True,
        }
    )
)
