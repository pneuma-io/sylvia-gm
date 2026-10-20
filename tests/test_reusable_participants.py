from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest

from sylvia_gm.agent._builder_session import _participant_artifact_tool
from sylvia_gm.agent._session_loop import CampaignSessionLoop
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_packages import bundled_rift_archive, bundled_rift_authority
from sylvia_gm.application.bundled_campaign_plans import bundled_plan
from sylvia_gm.application.initial_character_state import materialize_initial_character_state
from sylvia_gm.application.reusable_participants import (
    compatible_reusable_participants,
    load_reusable_participant,
    save_reusable_participant,
)
from sylvia_gm.campaign.rift import materialize_rift_selection, rift_public_template
from sylvia_gm.domain.authority import PackageLockEntry, PackageRole, SafePayload
from sylvia_gm.domain.campaign_plan import PackageBinding
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import (
    ParticipantArtifactDeclaration,
    canonical_json_bytes,
    participant_schema_sha256,
    manifest_from_record,
)
from sylvia_gm.domain.session import SessionMessageKind
from sylvia_gm.infrastructure.launch_handshake import derive_owner_response
from sylvia_gm.interfaces.local_api import LocalCommandBus
from sylvia_gm.interfaces.local_owner import LocalOwnerSupervisor
from sylvia_gm.interfaces.owner_process import _campaign_creation_catalog, _dispatch

ROOT = Path(__file__).resolve().parents[1]


def _starting_state(*, max_hp: int = 10) -> dict[str, object]:
    """Return the Rift example's authored SRD inputs without calculated fields."""

    template = json.loads(
        (ROOT / "src/sylvia_gm/campaign/the-rift-awakening.json").read_text(encoding="utf-8")
    )
    state = deepcopy(template["player_archetypes"][0]["entity_state"])
    contract = json.loads(
        (ROOT / "rulesets/srd-lite/content/character-creation-state.json").read_text(
            encoding="utf-8"
        )
    )
    for calculation in contract["initial_state_calculations"]:
        parts = calculation["set"]["field"].split(".")
        parent = state
        parents: list[tuple[dict[str, object], str]] = []
        for part in parts[:-1]:
            child = parent.get(part)
            if not isinstance(child, dict):
                break
            parents.append((parent, part))
            parent = child
        else:
            parent.pop(parts[-1], None)
            for owner, key in reversed(parents):
                if owner.get(key) == {}:
                    owner.pop(key)
    state.setdefault("resources", {})["hit_die"] = 1
    state.setdefault("resource_maxima", {})["hit_die"] = 1
    state["max_hp"] = max_hp
    return state


def _participant_authority() -> tuple[PackageLockEntry, ParticipantArtifactDeclaration]:
    authority = bundled_rift_authority()
    record = json.loads(
        (ROOT / "rulesets" / "srd-lite" / "package-record.json").read_text(encoding="utf-8")
    )
    artifact = record["participant_artifacts"][0]
    return (
        PackageLockEntry(
            package_id=authority.ruleset.package_id,
            version=authority.ruleset.version,
            sha256=authority.ruleset.content_sha256,
            role=PackageRole.PRIMARY,
            load_order=0,
            dependencies=(),
            capabilities=(),
        ),
        ParticipantArtifactDeclaration(
            kind=artifact["kind"],
            schema_id=artifact["schema_id"],
            schema_sha256=artifact["schema_sha256"],
            schema=artifact["schema"],
            entity_state_schema=artifact["entity_state_schema"],
            initial_state_calculations=tuple(artifact["initial_state_calculations"]),
            initial_state_validation=artifact.get("initial_state_validation"),
        ),
    )


def _legacy_participant_authority() -> tuple[PackageLockEntry, ParticipantArtifactDeclaration]:
    """Return a schema-only declaration for legacy-format preservation checks."""

    package, current = _participant_authority()
    return package, ParticipantArtifactDeclaration(
        kind=current.kind,
        schema_id=current.schema_id,
        schema_sha256=participant_schema_sha256(current.schema),
        schema=current.schema,
    )


def _plan_participant_authority(plan_id="last-cart-through"):
    """Author a test character against its selected Plan's preserved exact snapshot."""
    plan = bundled_plan(plan_id)
    assert plan is not None and plan.package_snapshot is not None
    bindings = plan.plan.payload["bindings"]
    rules = bindings["ruleset"]
    setting = bindings["campaign_setting"]
    with zipfile.ZipFile(io.BytesIO(plan.package_snapshot)) as archive:
        record = next(row for row in json.loads(archive.read("manifest.json"))["packages"]
                      if row["id"] == rules["id"])
    manifest = manifest_from_record(record)
    assert manifest.content_sha256 == rules["content_sha256"]
    package = PackageLockEntry(package_id=manifest.package_id, version=manifest.version,
                               sha256=manifest.content_sha256, role=PackageRole.PRIMARY,
                               load_order=0, dependencies=(), capabilities=())
    return (package, manifest.participant_artifacts[0],
            PackageBinding(setting["id"], setting["version"], setting["content_sha256"]))


@pytest.mark.parametrize("role", ["player_character", "companion"])
def test_rift_party_options_retain_saved_source_profile(tmp_path, role):
    """The Rift-specific catalog must retain the same safe provenance as ordinary options."""
    from sylvia_gm.application.character_profiles import save_character_profile
    from sylvia_gm.domain._content_schema_character_profile import EXAMPLE

    source = save_character_profile(
        tmp_path, operation_id="source", source=json.dumps(EXAMPLE).encode(),
    )
    package, declaration, setting = _plan_participant_authority()
    saved = save_reusable_participant(
        tmp_path, operation_id="adapted", role=role, summary="A synthetic archivist.",
        authored_profile="# Vela\n\nAn archivist.", participant=_participant("Vela"),
        entity_state=_starting_state(), package=package, declaration=declaration,
        campaign_setting=setting, source_profile=source.provenance(),
    )
    loaded = load_reusable_participant(
        tmp_path, saved.artifact_id, package=package, declaration=declaration,
        campaign_setting=setting,
    )
    options = rift_public_template(reusable_participants=(loaded,))
    rows = options["companions" if role == "companion" else "player_archetypes"]
    row = next(item for item in rows if item["id"] == saved.artifact_id)
    assert row["source_profile"] == source.provenance()
    assert "authored_profile" not in row and "entity_state" not in row

    from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness

    harness = _OwnerHarness(tmp_path / "owner.db", None)
    try:
        actual = harness.request("party", "campaign_party_options", {
            "ruleset_selection": "sylvia.srd-lite.bg3",
            "setting_id": "pneuma.severed-lands",
            "plan_id": "last-cart-through",
        })
        actual_rows = actual["companions" if role == "companion" else "player_archetypes"]
        actual_row = next(item for item in actual_rows if item["id"] == saved.artifact_id)
        assert actual_row["source_profile"] == source.provenance()
        assert "authored_profile" not in actual_row and "entity_state" not in actual_row
    finally:
        harness.close()


def _participant(name: str) -> dict[str, object]:
    return {
        "name": name,
        "level": 1,
        "species": "Human",
        "background": "Guide",
        "origin_feats": ["skilled"],
        "classes": [{"id": "fighter", "level": 1}],
        "fighting_styles": [{"source": "fighter", "style": "archery"}],
        "abilities": {
            "strength": 13,
            "dexterity": 14,
            "constitution": 12,
            "intelligence": 10,
            "wisdom": 15,
            "charisma": 8,
        },
        "saving_throw_proficiencies": ["strength", "constitution"],
        "size_rank": 1,
        "speed": 30,
    }


def test_version_three_authored_character_keeps_its_identity_and_profile(tmp_path: Path) -> None:
    package, declaration = _participant_authority()
    setting = bundled_rift_authority().setting
    participant = {**_participant("Vela"), "party_role": "companion"}
    state = materialize_initial_character_state(
        participant, _starting_state(), entity_state_schema=declaration.entity_state_schema,
        initial_state_calculations=declaration.initial_state_calculations,
        initial_state_validation=declaration.initial_state_validation,
    )
    profile = "# Vela\n\nAn existing version-three authored profile."
    payload = {"participant": participant, "entity_state": state}
    identity = {
        "role": "companion", "summary": "Signal keeper.", "authored_profile": profile,
        "package_id": package.package_id, "package_version": package.version,
        "package_sha256": package.sha256, "schema_id": declaration.schema_id,
        "schema_sha256": declaration.schema_sha256, "campaign_setting_id": setting.package_id,
        "campaign_setting_version": setting.version,
        "campaign_setting_sha256": setting.content_sha256, "payload": payload,
    }
    content_hash = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    artifact_id = f"participant-{'a' * 24}-{content_hash[:16]}"
    record = {key: value for key, value in identity.items() if key not in {
        "payload", "authored_profile"
    }}
    record.update({
        "format_id": "sylvia-gm-reusable-participant", "format_version": 3,
        "name": "Vela", "artifact_id": artifact_id, "content_sha256": content_hash,
    })
    path = tmp_path / "participants" / artifact_id
    path.mkdir(parents=True)
    (path / "record.json").write_bytes(canonical_json_bytes(record))
    (path / "payload.json").write_bytes(canonical_json_bytes(payload))
    (path / "profile.md").write_bytes(profile.encode("utf-8"))
    before = {file.name: file.read_bytes() for file in path.iterdir()}
    loaded = load_reusable_participant(
        tmp_path, artifact_id, package=package, declaration=declaration, campaign_setting=setting
    )
    assert loaded.authored_profile == profile
    assert loaded.profile_status == "authored"
    assert loaded.content_sha256 == content_hash
    assert loaded.setting_data == {}
    assert {file.name: file.read_bytes() for file in path.iterdir()} == before


def test_reusable_participant_honors_an_ordinary_ruleset_closed_schema(
    tmp_path: Path,
) -> None:
    schema = {
        "$id": "example.signal.participant.v1",
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "callsign": {"type": "string"},
        },
        "required": ["name", "callsign"],
        "additionalProperties": False,
    }
    package = PackageLockEntry(
        package_id="example.signal-rules",
        version="2.0.0",
        sha256="1" * 64,
        role=PackageRole.PRIMARY,
        load_order=0,
        dependencies=(),
        capabilities=(),
    )
    declaration = ParticipantArtifactDeclaration(
        kind="character",
        schema_id="example.signal.participant.v1",
        schema_sha256=participant_schema_sha256(
            schema,
            entity_state_schema={
                "type": "object",
                "properties": {"stress": {"type": "integer"}},
                "required": ["stress"],
                "additionalProperties": False,
            },
        ),
        schema=schema,
        entity_state_schema={
            "type": "object",
            "properties": {"stress": {"type": "integer"}},
            "required": ["stress"],
            "additionalProperties": False,
        },
    )
    campaign_setting = PackageBinding("example.signal-setting", "3.0.0", "2" * 64)
    participant = {"name": "Vela", "callsign": "Northstar"}

    saved = save_reusable_participant(
        tmp_path,
        operation_id="save-signal-companion",
        role="companion",
        summary="A synthetic participant for an ordinary Ruleset.",
        authored_profile="# Vela\n\nA synthetic closed-schema participant profile.",
        participant=participant,
        entity_state={"stress": 0},
        package=package,
        declaration=declaration,
        campaign_setting=campaign_setting,
    )

    assert saved.role == "companion"
    assert saved.participant == participant
    assert saved.catalog_payload()["kind"] == "companion"
    loaded = load_reusable_participant(
        tmp_path,
        saved.artifact_id,
        package=package,
        declaration=declaration,
        campaign_setting=campaign_setting,
    )
    assert loaded == saved
    assert compatible_reusable_participants(
        tmp_path,
        package=package,
        declaration=declaration,
        campaign_setting=campaign_setting,
    ) == (saved,)

    assert (
        compatible_reusable_participants(
            tmp_path,
            package=dataclasses.replace(package, sha256="3" * 64),
            declaration=declaration,
            campaign_setting=campaign_setting,
        )
        == ()
    )
    assert (
        compatible_reusable_participants(
            tmp_path,
            package=package,
            declaration=dataclasses.replace(declaration, schema_sha256="4" * 64),
            campaign_setting=campaign_setting,
        )
        == ()
    )
    assert (
        compatible_reusable_participants(
            tmp_path,
            package=package,
            declaration=declaration,
            campaign_setting=dataclasses.replace(campaign_setting, content_sha256="5" * 64),
        )
        == ()
    )


def test_owner_local_participants_survive_restart_and_materialize_exact_selection(
    tmp_path: Path,
) -> None:
    package, declaration, campaign_setting = _plan_participant_authority()
    player = save_reusable_participant(
        tmp_path,
        operation_id="save-player-one",
        role="player_character",
        summary="A synthetic player fixture.",
        authored_profile=(
            "# Mira\n\nMira was raised by lighthouse keepers and records every promise she makes."
        ),
        participant=_participant("Mira"),
        entity_state=_starting_state(max_hp=10),
        package=package,
        declaration=declaration,
        campaign_setting=campaign_setting,
    )
    companion = save_reusable_participant(
        tmp_path,
        operation_id="save-companion-one",
        role="companion",
        summary="A synthetic companion fixture.",
        authored_profile=(
            "# Tamsin\n\nTamsin left the capital after refusing a commission from the city watch."
        ),
        participant=_participant("Tamsin"),
        entity_state=_starting_state(max_hp=8),
        package=package,
        declaration=declaration,
        campaign_setting=campaign_setting,
    )
    assert player.participant["party_role"] == "player_character"
    assert companion.participant["party_role"] == "companion"
    save_reusable_participant(
        tmp_path,
        operation_id="save-incompatible-player",
        role="player_character",
        summary="An incompatible synthetic fixture.",
        authored_profile="# Wrong Package\n\nSynthetic incompatible fixture.",
        participant=_participant("Wrong Package"),
        entity_state=_starting_state(max_hp=1),
        package=dataclasses.replace(package, sha256="f" * 64),
        declaration=declaration,
        campaign_setting=campaign_setting,
    )
    save_reusable_participant(
        tmp_path,
        operation_id="save-incompatible-setting-player",
        role="player_character",
        summary="An incompatible Setting fixture.",
        authored_profile="# Wrong Setting\n\nSynthetic incompatible fixture.",
        participant=_participant("Wrong Setting"),
        entity_state=_starting_state(max_hp=1),
        package=package,
        declaration=declaration,
        campaign_setting=dataclasses.replace(campaign_setting, content_sha256="e" * 64),
    )

    reopened = compatible_reusable_participants(
        tmp_path,
        package=package,
        declaration=declaration,
        campaign_setting=campaign_setting,
    )
    template = rift_public_template(reusable_participants=reopened)
    assert [item["name"] for item in template["player_archetypes"]] == ["Adventurer", "Mira"]
    assert [item["name"] for item in template["companions"]] == ["Tamsin"]
    assert template["player_archetypes"][1]["profile_status"] == "authored"
    assert template["player_archetypes"][1]["campaign_ready"] is True
    assert template["companions"][0]["profile_status"] == "authored"
    assert template["companions"][0]["campaign_ready"] is True
    assert "participant" not in player.catalog_payload()
    assert "entity_state" not in companion.catalog_payload()
    assert "authored_profile" not in player.catalog_payload()
    assert player.authored_profile.startswith("# Mira")
    assert player.profile_status == "authored"
    assert player.catalog_payload()["campaign_ready"] is True
    assert (tmp_path / "participants" / player.artifact_id / "profile.md").read_text(
        encoding="utf-8"
    ) == player.authored_profile
    assert player.catalog_payload()["campaign_setting"] == campaign_setting.as_payload()

    selection = materialize_rift_selection(
        participant_namespace="0" * 24,
        lane="quick_start",
        archetype_id=player.artifact_id,
        companion_ids=(companion.artifact_id,),
        customization_notes="",
        package=package,
        participant_declaration=declaration,
        reusable_participants=reopened,
        selected_plan=bundled_plan("last-cart-through").plan,
    )
    assert [participant.data["name"] for participant in selection.participants] == [
        "Mira",
        "Tamsin",
    ]
    assert selection.canonical_input["player_character"]["archetype_id"] == player.artifact_id
    assert selection.canonical_input["companion_ids"] == [companion.artifact_id]
    assert selection.canonical_input["selected_party"] == [
        {
            "participant_id": selection.participants[0].participant_id,
            "party_role": "player_character",
            "name": "Mira",
            "summary": "A synthetic player fixture.",
            "character_file": {
                "source_id": player.artifact_id,
                "profile_status": "authored",
                "authored_profile": player.authored_profile,
                "setting_data": {},
            },
        },
        {
            "participant_id": selection.participants[1].participant_id,
            "party_role": "companion",
            "name": "Tamsin",
            "summary": "A synthetic companion fixture.",
            "character_file": {
                "source_id": companion.artifact_id,
                "profile_status": "authored",
                "authored_profile": companion.authored_profile,
                "setting_data": {},
            },
        },
    ]
    with pytest.raises(CommandRejected, match="selection is unavailable"):
        materialize_rift_selection(
            participant_namespace="1" * 24,
            lane="quick_start",
            archetype_id=player.artifact_id,
            companion_ids=(),
            customization_notes="",
            package=package,
            participant_declaration=declaration,
        )

    with pytest.raises(CommandRejected) as no_custom:
        materialize_rift_selection(
            participant_namespace="2" * 24,
            lane="quick_start",
            archetype_id=player.artifact_id,
            companion_ids=(companion.artifact_id,),
            customization_notes="",
            package=package,
            participant_declaration=declaration,
            reusable_participants=reopened,
            starting_party_policy={
                "player_character": {"mode": "compatible"},
                "required_companions": [],
                "custom_companions": {"minimum": 0, "maximum": 0},
            },
        )
    assert no_custom.value.code == "campaign_plan_custom_companion_count_invalid"

    with pytest.raises(CommandRejected) as custom_required:
        materialize_rift_selection(
            participant_namespace="3" * 24,
            lane="quick_start",
            archetype_id=player.artifact_id,
            companion_ids=(),
            customization_notes="",
            package=package,
            participant_declaration=declaration,
            reusable_participants=reopened,
            starting_party_policy={
                "player_character": {"mode": "compatible"},
                "required_companions": [],
                "custom_companions": {"minimum": 1, "maximum": 1},
            },
        )
    assert custom_required.value.code == "campaign_plan_custom_companion_count_invalid"

    with pytest.raises(CommandRejected) as fixed_player:
        materialize_rift_selection(
            participant_namespace="4" * 24,
            lane="quick_start",
            archetype_id=player.artifact_id,
            companion_ids=(),
            customization_notes="",
            package=package,
            participant_declaration=declaration,
            reusable_participants=reopened,
            starting_party_policy={
                "player_character": {
                    "mode": "fixed",
                    "character": {
                        "id": "open-adventurer",
                        "path": "characters/open-adventurer.md",
                    },
                },
                "required_companions": [],
                "custom_companions": {"minimum": 0, "maximum": 0},
            },
        )
    assert fixed_player.value.code == "campaign_plan_fixed_player_required"

    with pytest.raises(CommandRejected) as exceeds_ruleset:
        materialize_rift_selection(
            participant_namespace="5" * 24,
            lane="quick_start",
            archetype_id="open-adventurer",
            companion_ids=(),
            customization_notes="",
            package=package,
            participant_declaration=declaration,
            reusable_participants=reopened,
            starting_party_policy={
                "player_character": {"mode": "compatible"},
                "required_companions": [{"id": "any-companion", "path": "companions/any.md"}],
                "custom_companions": {"minimum": 0, "maximum": 5},
            },
        )
    assert exceeds_ruleset.value.code == "campaign_plan_party_exceeds_ruleset"


def test_original_unscoped_participants_are_grandfathered_only_to_severed_lands(
    tmp_path: Path,
) -> None:
    package, declaration = _legacy_participant_authority()
    campaign_setting = bundled_rift_authority().setting
    participant = {**_participant("Chloe"), "party_role": "companion"}
    payload = {"participant": participant, "entity_state": {"hp": 8, "max_hp": 8}}
    summary = "A legacy Severed Lands companion fixture."
    identity = {
        "role": "companion",
        "summary": summary,
        "package_id": package.package_id,
        "package_version": package.version,
        "package_sha256": package.sha256,
        "schema_id": declaration.schema_id,
        "schema_sha256": declaration.schema_sha256,
        "payload": payload,
    }
    content_sha256 = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    operation_suffix = hashlib.sha256(b"legacy-chloe").hexdigest()[:24]
    artifact_id = f"participant-{operation_suffix}-{content_sha256[:16]}"
    record = {
        "format_id": "sylvia-gm-reusable-participant",
        "format_version": 1,
        "artifact_id": artifact_id,
        "role": "companion",
        "name": "Chloe",
        "summary": summary,
        "content_sha256": content_sha256,
        "package_id": package.package_id,
        "package_version": package.version,
        "package_sha256": package.sha256,
        "schema_id": declaration.schema_id,
        "schema_sha256": declaration.schema_sha256,
    }
    root = tmp_path / "participants" / artifact_id
    root.mkdir(parents=True)
    (root / "payload.json").write_bytes(canonical_json_bytes(payload))
    (root / "record.json").write_bytes(canonical_json_bytes(record))

    assert (
        compatible_reusable_participants(
            tmp_path,
            package=package,
            declaration=declaration,
            campaign_setting=campaign_setting,
        )
        == ()
    )
    compatible = compatible_reusable_participants(
        tmp_path,
        package=package,
        declaration=declaration,
        campaign_setting=campaign_setting,
        legacy_campaign_setting=campaign_setting,
    )
    assert [item.name for item in compatible] == ["Chloe"]
    assert compatible[0].authored_profile is None
    assert compatible[0].profile_status == "sparse"
    assert compatible[0].rift_source()["profile_status"] == "sparse"
    assert compatible[0].rift_source()["authored_profile"] is None


def test_reusable_participant_profile_is_part_of_the_verified_identity(tmp_path: Path) -> None:
    package, declaration = _participant_authority()
    campaign_setting = bundled_rift_authority().setting
    saved = save_reusable_participant(
        tmp_path,
        operation_id="profile-integrity",
        role="companion",
        summary="A concise catalog description.",
        authored_profile="# Orin\n\nOrin grew up aboard the river barge *Dawn Bell*.",
        participant=_participant("Orin"),
        entity_state=_starting_state(max_hp=7),
        package=package,
        declaration=declaration,
        campaign_setting=campaign_setting,
    )
    profile_path = tmp_path / "participants" / saved.artifact_id / "profile.md"
    profile_path.write_text("# Orin\n\nA different history.", encoding="utf-8")

    with pytest.raises(ValueError, match="content hash"):
        load_reusable_participant(
            tmp_path,
            saved.artifact_id,
            package=package,
            declaration=declaration,
            campaign_setting=campaign_setting,
        )


def test_version_two_participant_remains_readable_but_cannot_enter_new_campaign(
    tmp_path: Path,
) -> None:
    package, declaration = _legacy_participant_authority()
    campaign_setting = bundled_rift_authority().setting
    participant = {**_participant("Legacy Chloe"), "party_role": "companion"}
    payload = {"participant": participant, "entity_state": {"hp": 8, "max_hp": 8}}
    summary = "A version-two companion with no authored profile body."
    identity = {
        "role": "companion",
        "summary": summary,
        "package_id": package.package_id,
        "package_version": package.version,
        "package_sha256": package.sha256,
        "schema_id": declaration.schema_id,
        "schema_sha256": declaration.schema_sha256,
        "campaign_setting_id": campaign_setting.package_id,
        "campaign_setting_version": campaign_setting.version,
        "campaign_setting_sha256": campaign_setting.content_sha256,
        "payload": payload,
    }
    content_sha256 = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    operation_suffix = hashlib.sha256(b"version-two-chloe").hexdigest()[:24]
    artifact_id = f"participant-{operation_suffix}-{content_sha256[:16]}"
    record = {
        "format_id": "sylvia-gm-reusable-participant",
        "format_version": 2,
        "artifact_id": artifact_id,
        "role": "companion",
        "name": "Legacy Chloe",
        "summary": summary,
        "content_sha256": content_sha256,
        "package_id": package.package_id,
        "package_version": package.version,
        "package_sha256": package.sha256,
        "schema_id": declaration.schema_id,
        "schema_sha256": declaration.schema_sha256,
        "campaign_setting_id": campaign_setting.package_id,
        "campaign_setting_version": campaign_setting.version,
        "campaign_setting_sha256": campaign_setting.content_sha256,
    }
    root = tmp_path / "participants" / artifact_id
    root.mkdir(parents=True)
    (root / "payload.json").write_bytes(canonical_json_bytes(payload))
    (root / "record.json").write_bytes(canonical_json_bytes(record))

    loaded = load_reusable_participant(
        tmp_path,
        artifact_id,
        package=package,
        declaration=declaration,
        campaign_setting=campaign_setting,
    )
    assert loaded.authored_profile is None
    assert loaded.profile_status == "sparse"
    assert loaded.catalog_payload()["campaign_ready"] is False
    public = rift_public_template(reusable_participants=(loaded,))
    legacy_choice = public["companions"][0]
    assert legacy_choice["profile_status"] == "sparse"
    assert legacy_choice["campaign_ready"] is False

    with pytest.raises(CommandRejected) as rejected:
        materialize_rift_selection(
            participant_namespace="9" * 24,
            lane="quick_start",
            archetype_id="open-adventurer",
            companion_ids=(artifact_id,),
            customization_notes="",
            package=package,
            participant_declaration=declaration,
            reusable_participants=(loaded,),
        )

    assert rejected.value.code == "character_file_profile_required"
    assert str(rejected.value) == (
        "Legacy Chloe's Character File needs an authored profile before it can join a new "
        "campaign. Complete the Character File or choose someone else."
    )


def test_reusable_participant_catalog_does_not_require_a_preinstalled_rift_candidate(
    tmp_path: Path,
) -> None:
    package, declaration = _participant_authority()
    campaign_setting = bundled_rift_authority().setting
    save_reusable_participant(
        tmp_path,
        operation_id="catalog-player-without-candidate",
        role="player_character",
        summary="A synthetic catalog fixture.",
        authored_profile="# Mira\n\nSynthetic catalog fixture profile.",
        participant=_participant("Mira"),
        entity_state=_starting_state(max_hp=10),
        package=package,
        declaration=declaration,
        campaign_setting=campaign_setting,
    )
    save_reusable_participant(
        tmp_path,
        operation_id="catalog-companion-without-candidate",
        role="companion",
        summary="A synthetic companion catalog fixture.",
        authored_profile="# Tamsin\n\nSynthetic catalog fixture profile.",
        participant=_participant("Tamsin"),
        entity_state=_starting_state(max_hp=8),
        package=package,
        declaration=declaration,
        campaign_setting=campaign_setting,
    )

    with AuthorityOwner.open(tmp_path / "catalog.sqlite3", "owner-catalog-no-candidate") as owner:
        assert owner.package_candidates() == ()
        catalog = _campaign_creation_catalog(tmp_path, owner)

    assert {item["id"] for item in catalog if item.get("kind") == "ruleset"} == {
        "sylvia.srd-lite.bg3", "pneuma.riichi-mahjong",
    }
    players = [item for item in catalog if item.get("kind") == "player_character"]
    assert sum(not item.get("builtin_starter", False) for item in players) == 1
    assert sum(item.get("builtin_starter", False) for item in players) == 12
    assert sum(item.get("kind") == "companion" for item in catalog) == 1


def test_created_participants_refresh_choices_without_selecting_until_player_answers(
    tmp_path: Path,
) -> None:
    from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness

    database = tmp_path / "participant-builder.sqlite3"
    harness = _OwnerHarness(database, None)
    selected_ids: dict[str, str] = {}
    try:
        draft = harness.request("draft", "create_guided_campaign_draft", {
            "operation_id": "draft", "display_name": "Participant Draft",
            "provider_id": "test.provider", "model_id": "test.model",
        })
        for revision, (step, answer) in enumerate((
            ("ruleset", {"candidate_id": "sylvia.srd-lite.bg3"}),
            ("setting", {"setting_id": "pneuma.severed-lands"}),
            ("plan", {"plan_id": "last-cart-through"}),
            ("name", {"display_name": "Participant Draft"}),
        )):
            advanced = harness.request(step, "advance_guided_campaign_draft", {
                "operation_id": step, "campaign_id": draft["campaign_id"],
                "builder_session_id": draft["builder_session_id"],
                "expected_construction_revision": revision, "current_step": step,
                "visible_answer": step, "answer": answer,
            })
        assert advanced["current_step"] == "participants"
        parent_params = {"session_id": draft["builder_session_id"]}
        parent_before = harness.request("before", "session_context", parent_params)
        package, declaration, setting = _plan_participant_authority()
        for role, name, collection in (
            ("player_character", "Vela", "player_archetypes"),
            ("companion", "Orin", "companions"),
        ):
            launched = harness.request(role, "start_guided_creation_subflow", {
                "operation_id": role, "campaign_id": draft["campaign_id"],
                "parent_session_id": draft["builder_session_id"],
                "expected_construction_revision": advanced["construction_revision"],
                "purpose": role, "visible_answer": f"Create New {role}",
            })
            assert launched["status"] == "independent"
            child = harness.request(f"child-{role}", "session_context", {
                "session_id": launched["child_session_id"],
            })
            assert child["session"]["campaign_id"] is None
            saved = save_reusable_participant(
                tmp_path, operation_id=f"save-{role}", role=role,
                summary="A synthetic traveler.", authored_profile=f"# {name}\n\nA traveler.",
                participant=_participant(name), entity_state=_starting_state(),
                package=package, declaration=declaration,
                campaign_setting=setting,
            )
            selected_ids[role] = saved.artifact_id
            options = harness.request(f"refresh-{role}", "campaign_party_options", {
                "ruleset_selection": "sylvia.srd-lite.bg3",
                "setting_id": "pneuma.severed-lands", "plan_id": "last-cart-through",
            })
            assert any(item["id"] == saved.artifact_id for item in options[collection])
            # Saving and refreshing have no return handshake and do not answer the Interview.
            assert (
                harness.request(f"parent-{role}", "session_context", parent_params) == parent_before
            )
    finally:
        harness.close()

    harness = _OwnerHarness(database, None)
    try:
        assert harness.request("reopened", "session_context", parent_params) == parent_before
        answer = {"archetype_id": selected_ids["player_character"],
                  "companion_ids": [selected_ids["companion"]]}
        chosen = harness.request("choose", "advance_guided_campaign_draft", {
            "operation_id": "choose", "campaign_id": draft["campaign_id"],
            "builder_session_id": draft["builder_session_id"],
            "expected_construction_revision": advanced["construction_revision"],
            "current_step": "participants", "visible_answer": "Vela and Orin", "answer": answer,
        })
        assert chosen["current_step"] == "boundaries_preferences"
    finally:
        harness.close()

    with AuthorityOwner.open(database, "verify-player-choice") as owner:
        context = owner.session_context(draft["builder_session_id"])
        selected = next(message for message in context.messages
                        if message.message_id == chosen["answer_message_id"])
        assert selected.kind is SessionMessageKind.USER
        assert selected.content["interview_value"] == answer


def test_participant_builder_tool_derives_role_and_setup_refreshes_local_choices() -> None:
    published: list[tuple[str, str, str]] = []

    def publish(
        *,
        role: str,
        summary: str,
        authored_profile: str,
        participant: SafePayload,
        entity_state: SafePayload,
    ) -> SafePayload:
        published.append((role, str(participant["name"]), authored_profile))
        return {
            "id": f"participant-{'c' * 24}-{'d' * 16}",
            "name": participant["name"],
        }

    tool = _participant_artifact_tool(role="companion", publisher=publish)
    result = tool.handler(
        {
            "summary": "Synthetic companion.",
            "authored_profile": "# Orin\n\nOrin keeps a careful journal of every road traveled.",
            "participant": {"name": "Orin"},
            "entity_state": json.dumps({"hp": 7}),
        },
        None,
    )
    assert result["success"] is True
    assert published == [
        (
            "companion",
            "Orin",
            "# Orin\n\nOrin keeps a careful journal of every road traveled.",
        )
    ]
    assert (
        tool.handler(
            {
                "summary": "Incomplete companion.",
                "participant": {"name": "Orin"},
                "entity_state": json.dumps({"hp": 7}),
            },
            None,
        )["code"]
        == "participant_artifact_invalid"
    )

    frontend = (ROOT / "src-tauri" / "frontend" / "app.js").read_text(encoding="utf-8")
    setup = frontend[frontend.index("async function showConstructionPanel") :]
    setup = setup[: setup.index("function restoreConstructionAnswer")]
    assert "const template = await state.bridge.rift_template();" in setup
    assert "renderRiftTemplate(template);" in setup
    assert "if (!stillSelected()) return;" in setup
    assert "!state.riftTemplate" not in setup

    operating_guide = (
        ROOT / "rulesets" / "srd-lite" / "prompts" / "gm-operating-guide.md"
    ).read_text(encoding="utf-8")
    assert "A Player Character and Companion face one hostile together." in operating_guide


def test_owner_dispatch_projects_only_compatible_local_participant_metadata(
    tmp_path: Path,
) -> None:
    supervisor = LocalOwnerSupervisor()
    issued = supervisor.acquire_owner(tmp_path / "owner.sqlite3", "owner-local-participant")
    supervisor.complete_handshake(derive_owner_response(issued.secret_hex))
    bus = LocalCommandBus(supervisor)
    owner = supervisor.application_owner()
    try:
        session_loop = CampaignSessionLoop(owner)

        def dispatch(method: str, params: SafePayload) -> object:
            return _dispatch(
                bus,
                method,
                params,
                application_owner=owner,
                owner_id="owner-local-participant",
                provider_resolver=None,  # type: ignore[arg-type]
                model_choices=lambda: (),
                session_loop=session_loop,
                storage_root=tmp_path,
            )

        archive = bundled_rift_archive()
        candidate = bus.install_package(
            io.BytesIO(archive),
            install_id="install-local-participant-fixture",
        )
        package = next(
            entry
            for entry in candidate.package_lock.entries
            if entry.package_id == bundled_rift_authority().ruleset.package_id
        )
        declaration = owner.package_participant_declarations(candidate.candidate_id)[0]
        saved = save_reusable_participant(
            tmp_path,
            operation_id="save-owner-dispatch-player",
            role="player_character",
            summary="A synthetic owner-dispatch fixture.",
            authored_profile=(
                "# Ilyra\n\nIlyra served as a courier between the capital and Harrowfield."
            ),
            participant=_participant("Ilyra"),
            entity_state=_starting_state(max_hp=9),
            package=package,
            declaration=declaration,
            campaign_setting=bundled_rift_authority().setting,
        )
        public = dispatch("rift_template", {})
        selected = next(
            item for item in public["player_archetypes"] if item["id"] == saved.artifact_id
        )
        assert selected == {
            "id": saved.artifact_id,
            "name": "Ilyra",
            "label": "Ilyra",
            "summary": "A synthetic owner-dispatch fixture.",
            "reusable": True,
            "profile_status": "authored",
            "campaign_ready": True,
        }
        legacy_saved = saved
        plan_package, plan_declaration, plan_setting = _plan_participant_authority()
        saved = save_reusable_participant(
            tmp_path, operation_id="save-plan-owner-dispatch-player", role="player_character",
            summary=legacy_saved.summary, authored_profile=legacy_saved.authored_profile,
            participant=_participant("Ilyra"), entity_state=_starting_state(max_hp=9),
            package=plan_package, declaration=plan_declaration, campaign_setting=plan_setting,
        )
        foreign = save_reusable_participant(
            tmp_path, operation_id="save-foreign-owner-dispatch-player", role="player_character",
            summary="A synthetic character from another Setting.",
            authored_profile="# Foreign fixture\n\nA controlled compatibility check.",
            participant=_participant("Foreign fixture"), entity_state=_starting_state(),
            package=plan_package, declaration=plan_declaration,
            campaign_setting=dataclasses.replace(plan_setting, package_id="example.foreign-setting"),
        )
        assert saved.artifact_id != legacy_saved.artifact_id
        options = dispatch("campaign_party_options", {
            "ruleset_selection": "sylvia.srd-lite.bg3", "setting_id": "pneuma.severed-lands",
            "plan_id": "last-cart-through",
        })
        ids = {item["id"] for item in options["player_archetypes"]}
        assert saved.artifact_id in ids and foreign.artifact_id not in ids
        same_binding = (
            legacy_saved.package_sha256 == saved.package_sha256
            and legacy_saved.schema_sha256 == saved.schema_sha256
            and legacy_saved.campaign_setting_id == saved.campaign_setting_id
            and legacy_saved.campaign_setting_version == saved.campaign_setting_version
            and legacy_saved.campaign_setting_sha256 == saved.campaign_setting_sha256
        )
        assert (legacy_saved.artifact_id in ids) == same_binding
        assert (tmp_path / "participants" / legacy_saved.artifact_id / "profile.md").is_file()
        catalog = dispatch("campaign_creation_catalog", {})
        ruleset = next(item for item in catalog if item["kind"] == "ruleset")
        ruleset_selection = str(ruleset["selection_id"])
        assert ruleset_selection == "sylvia.srd-lite.bg3"
        assert candidate.candidate_id in ruleset["selection_aliases"]

        draft = dispatch(
            "create_guided_campaign_draft",
            {
                "operation_id": "create-local-participant-campaign",
                "display_name": "Local Participant Campaign",
                "provider_id": "test.provider",
                "model_id": "test.model",
            },
        )
        revision = int(draft["construction_revision"])
        campaign_id = str(draft["campaign_id"])
        builder_session_id = str(draft["builder_session_id"])
        for step, visible, answer in (
            ("ruleset", "D&D SRD", {"candidate_id": ruleset_selection}),
            ("setting", "Severed Lands", {"setting_id": "pneuma.severed-lands"}),
            ("plan", "Last Cart Through", {"plan_id": "last-cart-through"}),
            (
                "name",
                "Local Participant Campaign",
                {"display_name": "Local Participant Campaign"},
            ),
            (
                "participants",
                "Ilyra",
                {"archetype_id": saved.artifact_id, "companion_ids": []},
            ),
            (
                "boundaries_preferences",
                "No boundaries or preferences",
                {"boundaries": "", "preferences": ""},
            ),
            (
                "specific_requests",
                "No additional requests",
                {"no_additional_requests": True, "requests": ""},
            ),
        ):
            advanced = dispatch(
                "advance_guided_campaign_draft",
                {
                    "operation_id": f"local-participant-answer-{step}",
                    "campaign_id": campaign_id,
                    "builder_session_id": builder_session_id,
                    "expected_construction_revision": revision,
                    "current_step": step,
                    "visible_answer": visible,
                    "answer": answer,
                },
            )
            revision = int(advanced["construction_revision"])
        dispatch(
            "start_guided_campaign",
            {
                "operation_id": "start-local-participant-campaign",
                "campaign_id": campaign_id,
                "builder_session_id": builder_session_id,
                "candidate_id": ruleset_selection,
                "setting_id": "pneuma.severed-lands",
                "plan_id": "last-cart-through",
                "display_name": "Local Participant Campaign",
                "lane": "quick_start",
                "player_character_id": saved.artifact_id,
                "companion_ids": [],
                "customization_notes": "",
                "provider_id": "test.provider",
                "model_id": "test.model",
                "construction_intent": {
                    "boundaries": "",
                    "preferences": "",
                    "specific_requests": "",
                    "contributions": {},
                },
            },
        )
        snapshot = owner.snapshot(campaign_id)
        assert len(snapshot.participants) == 1
        assert snapshot.participants[0].data["name"] == "Ilyra"
        assert snapshot.participants[0].data["party_role"] == "player_character"
        character_file = json.loads(
            str(
                owner.read_campaign_reference(
                    campaign_id,
                    source="participant",
                    path=snapshot.participants[0].participant_id,
                )["content"]
            )
        )
        assert character_file["source_character_file_id"] == saved.artifact_id
        assert character_file["party_role"] == "player_character"
        assert character_file["selector"] == {
            "summary": "A synthetic owner-dispatch fixture.",
            "meaning": "Short setup-catalog aid, not a substitute for authored biography.",
        }
        assert character_file["profile"] == {
            "status": "authored",
            "authored_text": saved.authored_profile,
        }
        assert character_file["ruleset"] == {
            "schema_kind": "player_character",
            "starting_data": saved.participant,
            "meaning": (
                "Ruleset-compatible starting data. Opaque identifiers establish only their "
                "exact stored values, not unwritten prose biography."
            ),
        }
        assert "authored_summary" not in character_file
        assert "data" not in character_file
    finally:
        supervisor.shutdown()
