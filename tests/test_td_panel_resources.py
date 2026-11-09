"""Authored panel maxima and physical magazine metadata survive normal reload and reopen."""

import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import StateValue, WriterRole
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.td_source_fixture import SOURCE, builder
from tests.td_source_fixture import candidate as candidate

NS = "pneuma-io.trigger-discipline"


def test_resource_projection_and_reload_keep_authored_maxima_and_capacity(candidate, tmp_path):
    database = tmp_path / "resources.sqlite3"
    with zipfile.ZipFile(candidate) as archive:
        package = json.loads(archive.read("manifest.json"))["packages"][0]
        map_surface = next(surface for surface in package["ui_surfaces"]
                           if surface["id"] == "operations.map")
        declared_actions = json.loads(
            (Path(__file__).parent / "fixtures/td-current-map-actions.json").read_bytes()
        )
        assert map_surface["actions"] == declared_actions
        state = json.loads(archive.read(f"packages/{NS}/conformance/scenario.json"))[
            "initial_state"
        ]
    actor = state[NS + ".entities"]["vesper"]
    target = state[NS + ".entities"]["drone_target"]
    assert (target["hp"], target["max_hp"], target["res"], target["max_res"]) == (50, 50, 20, 20)
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


PACKAGE_PREFIX = "packages/pneuma-io.trigger-discipline/"


def test_public_checkout_and_package_omit_vault_text(candidate):
    assert not (SOURCE / "AGENTS.md").exists()
    assert not (SOURCE / "README.md").exists()
    assert not list(SOURCE.rglob("*.canvas"))
    assert not any((SOURCE / name).exists() for name in builder.INDEXED_REFERENCE_ROOTS)
    with zipfile.ZipFile(candidate) as archive:
        assert not any("/references/canon/" in name for name in archive.namelist())
        index = json.loads(archive.read(PACKAGE_PREFIX + "provenance/source-index.json"))
        assert index["notes_distribution"] == "omitted"
        assert len(index["entries"]) > 200
        guide = archive.read(PACKAGE_PREFIX + "references/character-authoring.md").decode()
        assert "Complete GM/Builder session coverage is not established" in guide
        compiled = archive.read(PACKAGE_PREFIX + "conformance/specialty-counterfactuals.json")
        assert b'"source_excerpt"' not in compiled
        assert b'"canonical_span"' not in compiled


def test_private_input_keeps_existing_source_exclusions(tmp_path):
    (tmp_path / "Hacking").mkdir()
    (tmp_path / "Hacking/CREDENTIAL.md").write_text("Synthetic excluded note")
    (tmp_path / "Hacking/ACCESS.md").write_text("Synthetic permitted note")
    paths = {path.relative_to(tmp_path).as_posix() for path in builder._authority_paths(tmp_path)}
    assert "Hacking/CREDENTIAL.md" not in paths
    assert "Hacking/ACCESS.md" in paths


def test_private_input_and_output_must_be_explicit(tmp_path):
    with pytest.raises(ValueError, match="explicit notes directory"):
        builder.build_draft(SOURCE, draft_path=tmp_path / "draft", include_private_notes=True)
    with pytest.raises(ValueError, match="outside the public repository"):
        builder.build_draft(SOURCE, draft_path=SOURCE / "private-draft",
                            notes_directory=tmp_path, include_private_notes=True)
    contaminated = tmp_path / "contaminated"
    (contaminated / "files/references/canon").mkdir(parents=True)
    with pytest.raises(ValueError, match="containing private notes"):
        builder.build_draft(SOURCE, draft_path=contaminated)


@pytest.mark.parametrize("include_private_notes", [False, True])
def test_explicit_notes_input_preserves_mechanics_in_both_modes(
    candidate, tmp_path, include_private_notes,
):
    supplied = os.environ.get("TD_PRIVATE_NOTES_DIRECTORY")
    if not supplied:
        pytest.skip("Private vault is an optional local input, never a public test fixture")
    from scripts.build_trigger_discipline_package import build_package

    revision = subprocess.check_output(["git", "-C", str(SOURCE), "rev-parse", "HEAD"],
                                       text=True).strip()
    output = tmp_path / "provided-notes.sylvia-gm-package"
    identity = build_package(output, source_commit=revision, allow_worktree=True,
                             notes_directory=Path(supplied),
                             include_private_notes=include_private_notes)
    assert identity["distribution"] == ("private-local" if include_private_notes else "public")
    with zipfile.ZipFile(candidate) as public, zipfile.ZipFile(output) as private:
        index = json.loads(private.read(PACKAGE_PREFIX + "provenance/source-index.json"))
        assert index["notes_distribution"] == (
            "private-local" if include_private_notes else "omitted"
        )
        notes = {name for name in private.namelist() if "/references/canon/" in name}
        if include_private_notes:
            assert len(notes) > 200
        else:
            assert not notes
        for name in notes:
            source_note = Path(supplied) / name.split("/references/canon/", 1)[1]
            assert private.read(name) == source_note.read_bytes()
        for name in ("runtime/ruleset.yaml", "references/specialty-eligibility.json",
                     "provenance/parameter-manifest.json", "conformance/scenario.json"):
            assert public.read(PACKAGE_PREFIX + name) == private.read(PACKAGE_PREFIX + name), name
