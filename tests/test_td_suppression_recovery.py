"""Close the v23 source-owner persistence gap without claiming its unfinished native run.

The retained package is exact. Its conformance scene is used on fresh databases; this
is not the unavailable C24 saved campaign. Seven AP for the suppressor is an explicit
technical sequence budget, not a Siren starting profile or progression rule.
"""

import hashlib
import json
import os
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import StateValue, WriterRole, thaw_json_value
from sylvia_gm.domain.errors import IdempotencyConflict, StaleRevision
from tests.admitted_library_fixture import admitted_fixture_owner

ROOT = Path(__file__).resolve().parents[1]
NS = "pneuma-io.trigger-discipline"
RETAINED = ROOT / "tests/fixtures/td-native-source-v23.sylvia-gm-package"


def _current_archive(tmp_path):
    """Accept an exact candidate or build the source closure for ordinary standalone runs."""
    if selected := os.environ.get("SYLVIA_TD_CANDIDATE"):
        return Path(selected)
    from tests.td_source_fixture import build_archive

    return build_archive(tmp_path)


@pytest.mark.parametrize("generation", ["retained-v23", "current"])
def test_suppression_break_save_reopen_replay_and_portable_recovery(tmp_path, generation):
    archive = RETAINED if generation == "retained-v23" else _current_archive(tmp_path)
    raw = archive.read_bytes()
    if generation == "retained-v23":
        assert hashlib.sha256(raw).hexdigest() == (
            "53cad5edcd6002d106b499d0f30734404d9eafc35f80ab4bdc26e909f63d0bd4"
        )
    with zipfile.ZipFile(archive) as package:
        manifest = json.loads(package.read("manifest.json"))
        record = next(row for row in manifest["packages"] if row["id"] == NS)
        scene = json.loads(package.read(f"packages/{NS}/conformance/scenario.json"))[
            "initial_state"
        ]
        runtime_sha = hashlib.sha256(
            package.read(f"packages/{NS}/runtime/ruleset.yaml")
        ).hexdigest()
    # Exactly enough for suppress, one-cell move, suppress, retain-magazine reload.
    scene[NS + ".entities"]["hex"]["ap"] = 7
    database = tmp_path / "fresh-synthetic.sqlite3"
    commands, receipts, checkpoints = [], [], []
    campaign_id, owner_id = "v23-recovery-source", "suppression-recovery-owner"

    def verify(owner, expected):
        snapshot = owner.snapshot(campaign_id)
        assert snapshot == expected
        for command, receipt in zip(commands, receipts, strict=True):
            assert owner.execute_action(command) == receipt
            assert owner.snapshot(campaign_id) == expected
        if commands:
            with pytest.raises(IdempotencyConflict):
                owner.execute_action(replace(commands[-1], parameters={"actor": "other"}))
            assert owner.snapshot(campaign_id) == expected

    def execute(owner, tool, args):
        before = owner.snapshot(campaign_id)
        identity = f"suppression-step-{len(commands)}"
        command = ActionCommand(
            campaign_id=campaign_id, action_id=identity, idempotency_key=identity,
            owner_id=owner_id, writer=WriterRole.DM, expected_revision=before.revision,
            turn_id="turn", package_id=NS, tool_name=tool, parameters=args,
        )
        receipt = owner.execute_action(command)
        assert receipt.status.value == "committed"
        commands.append(command)
        receipts.append(receipt)
        after = owner.snapshot(campaign_id)
        assert after.revision == before.revision + 1
        assert owner.execute_action(command) == receipt
        assert owner.snapshot(campaign_id) == after
        checkpoints.append({"tool": tool, "revision": after.revision,
                            "state": thaw_json_value(after.state)})
        return after

    with admitted_fixture_owner(database, owner_id, raw) as (owner, candidate):
        owner.create_campaign(CampaignCreate(
            campaign_id=campaign_id, display_name="Synthetic v23 suppression recovery",
            writer=WriterRole.DM, package_lock=candidate.package_lock, turn_id="turn",
            initial_state=tuple(StateValue(NS, key.removeprefix(NS + "."), value)
                                for key, value in scene.items()),
        ))
        execute(owner, "prepare_overwatch", {"actor": "vesper", "zone_id": "overwatch-lane"})
        prepared = thaw_json_value(owner.snapshot(campaign_id).state[NS + ".state"])["prepared"]
        execute(owner, "establish_suppression", {
            "actor": "hex", "zone_id": "suppression-lane", "rounds": 10,
        })
        active = owner.snapshot(campaign_id)
        assert active.state[NS + ".state"]["prepared"]["suppression"]["active"] is True
        owner.export_campaign("active-export", campaign_id, tmp_path / "active.sylvia-gm-campaign")
    # Resume a durable, active preparation after releasing the only database owner.
    with AuthorityOwner.open(database, owner_id) as owner:
        verify(owner, active)
        moved = execute(owner, "move_actor", {"actor": "hex", "destination": [3, 3]})
        assert moved.state[NS + ".state"]["prepared"]["suppression"]["active"] is False
        assert moved.state[NS + ".state"]["prepared"]["suppression"]["capacity"] == 0
        assert moved.state[NS + ".state"]["prepared"]["overwatch"] == prepared["overwatch"]
        execute(owner, "establish_suppression", {
            "actor": "hex", "zone_id": "suppression-lane", "rounds": 10,
        })
        loaded_before = thaw_json_value(
            owner.snapshot(campaign_id).state[NS + ".entities"]["hex"]["weapon"]
        )
        final = execute(owner, "reload_weapon", {"actor": "hex", "disposition": "RETAIN"})
        actor = final.state[NS + ".entities"]["hex"]
        assert actor["ap"] == 0
        assert actor["weapon"]["magazine"] == loaded_before["spare_magazines"][-1]
        assert actor["weapon"]["retained_magazines"] == [loaded_before["magazine"]]
        assert actor["weapon"]["current_recoil"] == loaded_before["current_recoil"]
        assert actor["weapon"]["chamber"] == loaded_before["chamber"]
        assert final.state[NS + ".state"]["prepared"]["suppression"]["active"] is False
        assert final.state[NS + ".state"]["prepared"]["suppression"]["capacity"] == 0
        assert final.state[NS + ".state"]["prepared"]["overwatch"] == prepared["overwatch"]
        with pytest.raises(StaleRevision):
            owner.execute_action(replace(commands[-1], action_id="stale-new-command",
                                         idempotency_key="stale-new-command"))
        assert owner.snapshot(campaign_id) == final
        owner.backup_database(tmp_path / "final-backup.sqlite3")
    # The missing historical boundary: reopen final state, then export its saved bytes.
    with AuthorityOwner.open(database, owner_id) as owner:
        verify(owner, final)
        owner.export_campaign(
            "final-reopened-export", campaign_id, tmp_path / "final.sylvia-gm-campaign",
        )
    with AuthorityOwner.open(tmp_path / "final-backup.sqlite3", owner_id) as owner:
        verify(owner, final)
    for label, expected in (("active", active), ("final", final)):
        with AuthorityOwner.open(tmp_path / f"recovered-{label}.sqlite3", owner_id) as owner:
            source = tmp_path / f"{label}.sylvia-gm-campaign"
            result = owner.recover_campaign(recovery_id="selected-recovery", source=source)
            assert owner.recover_campaign(recovery_id="selected-recovery", source=source) == result
            # Active checkpoint has only its first two receipts; no later action is replayed.
            count = 2 if label == "active" else len(commands)
            saved_commands, saved_receipts = commands, receipts
            commands, receipts = commands[:count], receipts[:count]
            verify(owner, expected)
            commands, receipts = saved_commands, saved_receipts
        with AuthorityOwner.open(tmp_path / f"recovered-{label}.sqlite3", owner_id) as owner:
            assert owner.snapshot(campaign_id) == expected
    (tmp_path / "receipt.json").write_text(json.dumps({
        "generation": generation, "archive_sha256": hashlib.sha256(raw).hexdigest(),
        "td_content_sha256": record["content_sha256"], "runtime_sha256": runtime_sha,
        "synthetic_override": {"hex.ap": 7}, "checkpoints": checkpoints,
        "qualification": (
            "deterministic source owner only; historical C24 native export remains absent"
        ),
    }, indent=2), encoding="utf-8")
