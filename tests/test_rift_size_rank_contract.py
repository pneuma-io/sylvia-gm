"""The retained Rift start uses its source-declared creature size without changing saved games."""

import json
from copy import deepcopy
from pathlib import Path

import yaml

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_campaign_plans import sources_for
from sylvia_gm.application.bundled_packages import bundled_rift_archive
from sylvia_gm.application.plan_snapshot import _admitted_snapshot_facts
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.infrastructure.launch_handshake import derive_owner_response
from sylvia_gm.interfaces.local_api import LocalCommandBus
from sylvia_gm.interfaces.local_owner import LocalOwnerSupervisor
from tests.admitted_library_fixture import prepare_library_database
from tests.test_alpha_05_rift_journey import _construct_rift

ROOT = Path(__file__).resolve().parents[1]
NS = "sylvia.srd-lite.bg3"
WOLF = "rift-opening-riftwolf"


def source_rank():
    source = (ROOT / "campaign-settings/severed-lands/content/monsters/rifthound.md").read_text(
        encoding="utf-8"
    )
    stat_block = yaml.safe_load(source.split("---", 2)[1])
    return {"Tiny": 0, "Small": 1, "Medium": 2, "Large": 3, "Huge": 4, "Gargantuan": 5}[
        stat_block["size"]
    ]


def test_rift_authored_encounter_and_plan_keep_the_source_size():
    template = json.loads(
        (ROOT / "src/sylvia_gm/campaign/the-rift-awakening.json").read_text(encoding="utf-8")
    )
    plan = json.loads(
        (ROOT / "campaign-plans/the-rift-awakening/plan.json").read_text(encoding="utf-8")
    )
    text = (ROOT / "campaign-plans/the-rift-awakening/plan.md").read_text(encoding="utf-8")
    opening = json.loads(
        text.split("## Starting facts and state", 1)[1].split("```json", 1)[1].split("```", 1)[0]
    )
    state = template["encounter_entities"][WOLF]
    assert state["size_rank"] == source_rank() == 2
    declared = next(row for row in plan["initial_state"]["entities"] if row["id"] == WOLF)
    readable = next(row for row in opening["entities"] if row["id"] == WOLF)
    assert declared["source_ref"]["path"] == "content/monsters/rifthound.md"
    assert declared["state"] == readable["state"] == state


def test_public_rift_start_saves_source_size_and_reopens_without_recalculating(tmp_path):
    database = tmp_path / "rift-size.sqlite3"
    prepare_library_database(database, bundled_rift_archive())
    supervisor = LocalOwnerSupervisor()
    issued = supervisor.acquire_owner(database, "owner-rift")
    supervisor.complete_handshake(derive_owner_response(issued.secret_hex))
    owner = supervisor.application_owner()
    try:
        owner.set_admin_full_audit(True)
        campaign_id, _ = _construct_rift(
            LocalCommandBus(supervisor),
            owner,
            tmp_path,
            lane="quick_start",
            companion_ids=(),
        )
        saved = owner.snapshot(campaign_id)
        for participant in saved.participants:
            assert (
                saved.state[f"{NS}.entities"][participant.participant_id]["size_rank"]
                == (participant.data["size_rank"])
            )
    finally:
        supervisor.shutdown()
    with AuthorityOwner.open(database, "rift-size-reopened") as reopened:
        assert reopened.snapshot(campaign_id) == saved
        assert reopened.snapshot(campaign_id).state[f"{NS}.entities"][WOLF]["size_rank"] == (
            source_rank()
        )


def test_retained_legacy_rift_snapshot_keeps_its_exact_bindings_and_missing_size(tmp_path):
    root = ROOT / "campaign-plans/the-rift-awakening"
    snapshot = (root / "setting.sylvia-gm-package").read_bytes()
    primary, settings, closure = _admitted_snapshot_facts(snapshot)
    setting = next(row[0] for row in settings if row[0].package_id == "pneuma.severed-lands")
    payload = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    payload["bindings"] = {
        "ruleset": {
            "id": primary.package_id,
            "version": primary.version,
            "content_sha256": primary.content_sha256,
        },
        "campaign_setting": {
            "id": setting.package_id,
            "version": setting.version,
            "content_sha256": setting.sha256,
        },
        "package_closure_sha256": closure,
    }
    declared = next(row for row in payload["initial_state"]["entities"] if row["id"] == WOLF)
    declared["state"].pop("size_rank", None)
    text = (root / "plan.md").read_text(encoding="utf-8")
    text = text.replace('        "size_rank": 2,\n', "")
    before = deepcopy(payload)
    (tmp_path / "plan.json").write_bytes(canonical_json_bytes(payload))
    (tmp_path / "plan.md").write_text(text, encoding="utf-8")
    (tmp_path / "setting.sylvia-gm-package").write_bytes(snapshot)
    retained = sources_for(tmp_path)
    assert retained.snapshot == snapshot
    assert retained.validated_plan.payload == before
    assert (
        "size_rank"
        not in next(
            row
            for row in retained.validated_plan.payload["initial_state"]["entities"]
            if row["id"] == WOLF
        )["state"]
    )
    assert json.loads((tmp_path / "plan.json").read_bytes()) == before
    assert (tmp_path / "setting.sylvia-gm-package").read_bytes() == snapshot
