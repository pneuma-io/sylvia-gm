"""Retained Setting Character Files migrate on read without rewriting their source."""

import hashlib
import json
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest

from sylvia_gm.application.reusable_participants import parse_setting_character
from sylvia_gm.domain.authority import PackageLockEntry, PackageRole
from sylvia_gm.domain.campaign_plan import PackageBinding
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import ParticipantArtifactDeclaration

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    path = ROOT / "tests/fixtures/SRD-Lite-117-QA-Legacy-Contract-Clean.sylvia-gm-package"
    raw = path.read_bytes()
    with zipfile.ZipFile(path) as archive:
        packages = json.loads(archive.read("manifest.json"))["packages"]
        primary = next(p for p in packages if p["id"] == "sylvia.srd-lite.bg3")
    retained = json.loads((ROOT / "tests/fixtures/lite-tavin-retained-legacy.identity.json").read_bytes())
    setting = retained["campaign_setting"]
    source = (ROOT / "tests/fixtures/lite-tavin-retained-legacy.json").read_bytes()
    assert hashlib.sha256(source).hexdigest() == retained["source_sha256"]
    assert primary["version"] == "0.3.0"
    a = primary["participant_artifacts"][0]
    declaration = ParticipantArtifactDeclaration(
        kind=a["kind"],
        schema_id=a["schema_id"],
        schema_sha256=a["schema_sha256"],
        schema=a["schema"],
        entity_state_schema=a["entity_state_schema"],
        initial_state_calculations=tuple(a["initial_state_calculations"]),
        initial_state_validation=a["initial_state_validation"],
    )
    package = PackageLockEntry(
        package_id=primary["id"],
        version=primary["version"],
        sha256=primary["content_sha256"],
        role=PackageRole.PRIMARY,
        load_order=0,
        dependencies=(),
        capabilities=(),
    )
    binding = PackageBinding(setting["id"], setting["version"], setting["content_sha256"])
    return path, raw, source, package, declaration, binding


@pytest.mark.parametrize("role", ["player_character", "companion"])
def test_retained_exact_character_migrates_detached_with_source_provenance(role):
    path, archive, source, package, declaration, binding = fixture()
    original = json.loads(source)
    parsed = parse_setting_character(
        source.decode(),
        character_id=original["id"],
        role=role,
        package=package,
        declaration=declaration,
        campaign_setting=binding,
    )
    assert parsed.authored_profile == original["authored_profile"]
    assert parsed.summary == original["summary"]
    assert parsed.setting_data == original["setting_data"]
    assert {k: v for k, v in parsed.participant.items() if k != "party_role"} == {
        k: v for k, v in original["participant"].items() if k != "party_role"
    }
    assert parsed.participant["party_role"] == role
    assert all(parsed.entity_state[k] == v for k, v in original["entity_state"].items())
    assert set(parsed.entity_state) - set(original["entity_state"]) == {
        "weapon_sets",
        "light_attack_source_items",
    }
    assert parsed.entity_state["weapon_sets"]["active_set"] is None
    assert parsed.schema_sha256 == declaration.schema_sha256
    assert parsed.source_migration == {
        "migration_id": "srd-lite-saved-weapon-sets-v1",
        "source_content_sha256": hashlib.sha256(source).hexdigest(),
        "source_schema_sha256": original["schema"]["content_sha256"],
        "target_schema_sha256": declaration.schema_sha256,
    }
    assert path.read_bytes() == archive
    assert (ROOT / "tests/fixtures/lite-tavin-retained-legacy.json").read_bytes() == source


@pytest.mark.parametrize(
    "change", ["unknown_schema", "foreign_ruleset", "invalid_hp", "forged_armor"]
)
def test_compatibility_never_accepts_unknown_or_invalid_old_contract(change):
    _, _, source, package, declaration, binding = fixture()
    value = json.loads(source)
    original = deepcopy(value)
    if change == "unknown_schema":
        value["schema"]["content_sha256"] = "0" * 64
    elif change == "foreign_ruleset":
        from dataclasses import replace

        package = replace(package, package_id="example.foreign")
    elif change == "invalid_hp":
        value["entity_state"]["hp"] = True
    else:
        value["entity_state"]["armor_class"] += 1
    before = deepcopy(value)
    with pytest.raises((ValueError, CommandRejected)):
        parse_setting_character(
            json.dumps(value),
            character_id=original["id"],
            role="player_character",
            package=package,
            declaration=declaration,
            campaign_setting=binding,
        )
    assert value == before


@pytest.mark.parametrize("role", ["player_character", "companion"])
def test_current_setting_character_keeps_exact_state_without_legacy_migration(role):
    path = ROOT / "campaign-plans/a-debt-in-iron/setting.sylvia-gm-package"
    before = path.read_bytes()
    with zipfile.ZipFile(path) as archive:
        records = json.loads(archive.read("manifest.json"))["packages"]
        primary = next(p for p in records if p["id"] == "sylvia.srd-lite.bg3")
        setting = next(p for p in records if p["id"] == "pneuma.severed-lands")
        raw = archive.read("packages/pneuma.severed-lands/characters/tavin-bell.json")
    original = json.loads(raw)
    a = primary["participant_artifacts"][0]
    declaration = ParticipantArtifactDeclaration(kind=a["kind"],schema_id=a["schema_id"],schema_sha256=a["schema_sha256"],schema=a["schema"],entity_state_schema=a["entity_state_schema"],initial_state_calculations=tuple(a["initial_state_calculations"]),initial_state_validation=a["initial_state_validation"])
    package = PackageLockEntry(package_id=primary["id"],version=primary["version"],sha256=primary["content_sha256"],role=PackageRole.PRIMARY,load_order=0,dependencies=(),capabilities=())
    parsed = parse_setting_character(raw.decode(),character_id=original["id"],role=role,package=package,declaration=declaration,campaign_setting=PackageBinding(setting["id"],setting["version"],setting["content_sha256"]))
    assert parsed.entity_state == original["entity_state"]
    assert parsed.authored_profile == original["authored_profile"]
    assert parsed.source_migration is None
    assert path.read_bytes() == before
