"""The real Lite owner exposes existing values through neutral package fields."""

import json
from copy import deepcopy
from pathlib import Path

from scripts.srd_lite_frontend_projections import bind_frontend_projections

from sylvia_gm.application.bundled_packages import bundled_srd_archive

NS = "sylvia.srd-lite.bg3"
ROOT = Path(__file__).resolve().parents[1]


def test_package_fields_follow_catalogue_and_omit_spells_without_a_school():
    record = json.loads((ROOT / "rulesets/srd-lite/package-record.json").read_text("utf-8"))
    catalogue = json.loads(
        (ROOT / "rulesets/srd-lite/content/spell-catalogue.json").read_text("utf-8")
    )
    expected = {
        profile["identity"]: profile["school"]
        for profile in catalogue["profiles"]
        if profile.get("school")
    }
    catalogue["profiles"].extend(
        [
            {"identity": "missing"},
            {"identity": "blank", "school": ""},
            {"identity": "null", "school": None},
            {"identity": "whitespace", "school": " "},
        ]
    )
    before = deepcopy(record)
    bind_frontend_projections(record, catalogue)
    assert record == before, "Checked-in surfaces must match their reproducible declaration"
    for surface in record["ui_surfaces"]:
        if surface["id"] in {"dnd-party", "dnd-character", "dnd-encounter"}:
            assert surface["source"]["fields"]["spell_categories"]["value"] == expected


def test_actual_owner_projects_committed_totals_and_caster_categories_without_mutation(
    tmp_path,
    monkeypatch,
):
    archive = bundled_srd_archive()
    candidate = tmp_path / "lite.sylvia-package"
    candidate.write_bytes(archive)
    monkeypatch.setenv("SRD_LITE_CANDIDATE", str(candidate))
    from tests.lite_imported_fixture import ImportedCase

    value = ImportedCase(
        tmp_path / "projection.sqlite3",
        "fire_bolt",
        archive_bytes=archive,
        participant_changes={"party_role": "player_character"},
    )
    try:
        before = value.snapshot()
        initial = value.owner.campaign_panels("lite")
        encounter = next(s for s in initial["surfaces"] if s["id"] == "dnd-encounter")
        assert encounter["data"]["order_values"] == {}
        assert value.snapshot() == before

        value.act(
            "start_combat_encounter_entities",
            first_actor="hero",
            second_actor="guard",
            first_surprised=False,
            second_surprised=False,
            tie_winner="first",
        )
        value.act("begin_combat_turn")
        committed = value.snapshot()
        combat = committed.state[f"{NS}.combat"]
        expected = {
            combat[f"initiative_{place}_actor"]: combat[f"initiative_{place}_total"]
            for place in ("first", "second")
        }
        for reopen in (False, True):
            if reopen:
                value.reopen()
            projection = value.owner.campaign_panels("lite")
            surfaces = {s["id"]: s["data"] for s in projection["surfaces"]}
            assert surfaces["dnd-encounter"]["order_values"] == expected
            assert "initiative" not in json.dumps(dict(expected))
            assert surfaces["dnd-encounter"]["combat"] == combat
            assert set(expected) == {combat["order_first"], combat["order_second"]}
            for identity in ("dnd-party", "dnd-character", "dnd-encounter"):
                data = surfaces[identity]
                assert data["spell_categories"]["fire_bolt"] == "evocation"
                assert data["entities"] == committed.state[f"{NS}.entities"]
                assert data["entities"]["hero"]["prepared_spells"] == ("fire_bolt",)
            assert value.snapshot() == committed
    finally:
        value.close()
