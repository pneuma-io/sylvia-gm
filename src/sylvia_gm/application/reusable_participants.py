"""Small owner-local library for exact reusable Player Characters and Companions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from sylvia_gm.application.character_profiles import validate_source_profile
from sylvia_gm.application.initial_character_state import (
    materialize_initial_character_state,
    verify_initial_character_state,
)
from sylvia_gm.application.reusable_library_deletion import (
    delete_exact_library_entry,
    validate_owned_files,
    validated_library_entry,
)
from sylvia_gm.application.schema_admission import validate_json_schema, validate_parameters
from sylvia_gm.application.setting_character_requirements import validate_setting_character_data
from sylvia_gm.domain.authority import PackageLockEntry, SafePayload
from sylvia_gm.domain.campaign_plan import PackageBinding
from sylvia_gm.domain.content_validation import validate_content_shape
from sylvia_gm.domain.errors import (
    CommandRejected,
    MechanicsRejected,
    MechanicsValidationIssue,
    RulesetDiagnostic,
)
from sylvia_gm.domain.package_library import (
    ParticipantArtifactDeclaration,
    canonical_json_bytes,
)

REUSABLE_PARTICIPANT_FORMAT_ID = "sylvia-gm-reusable-participant"
REUSABLE_PARTICIPANT_FORMAT_VERSION = 5
SETTING_CHARACTER_FORMAT_ID = "sylvia-gm-character-file"
SETTING_CHARACTER_FORMAT_VERSION = 2
_MAX_LIBRARY_PARTICIPANTS = 256
_MAX_PARTICIPANT_BYTES = 256 * 1024
_ARTIFACT_ID = re.compile(r"participant-[0-9a-f]{24}-[0-9a-f]{16}")
_LIBRARY_WRITE_LOCK = threading.Lock()


class ParticipantValidationError(ValueError):
    """Retain field-level correction facts without exposing submitted character values."""

    def __init__(self, issues: tuple[MechanicsValidationIssue, ...]) -> None:
        super().__init__("Reusable participant does not match its exact schema")
        self.issues = issues


@dataclass(frozen=True)
class ReusableParticipant:
    """One validated local participant tied to an exact Setting and Ruleset schema."""

    artifact_id: str
    role: str
    name: str
    summary: str
    authored_profile: str | None
    content_sha256: str
    package_id: str
    package_version: str
    package_sha256: str
    schema_id: str
    schema_sha256: str
    campaign_setting_id: str
    campaign_setting_version: str
    campaign_setting_sha256: str
    participant: SafePayload
    entity_state: SafePayload
    setting_data: SafePayload = field(default_factory=dict)
    source_profile: SafePayload | None = None
    source_migration: SafePayload | None = None

    @property
    def profile_status(self) -> str:
        """Distinguish a real authored profile from an older sparse artifact."""

        return "authored" if self.authored_profile is not None else "sparse"

    def catalog_payload(self) -> SafePayload:
        """Project only the metadata needed to render a safe local choice."""

        return {
            "kind": self.role,
            "id": self.artifact_id,
            "name": self.name,
            "title": self.name,
            "summary": self.summary,
            "profile_status": self.profile_status,
            "campaign_ready": self.profile_status == "authored",
            "content_sha256": self.content_sha256,
            "reusable": True,
            "source_profile": self.source_profile,
            "ruleset": {
                "id": self.package_id,
                "version": self.package_version,
                "content_sha256": self.package_sha256,
            },
            "schema": {
                "id": self.schema_id,
                "content_sha256": self.schema_sha256,
            },
            "campaign_setting": {
                "id": self.campaign_setting_id,
                "version": self.campaign_setting_version,
                "content_sha256": self.campaign_setting_sha256,
            },
        }

    def rift_source(self) -> dict[str, object]:
        """Return the private materialization payload for the campaign owner only."""

        return {
            "kind": self.role,
            "id": self.artifact_id,
            "label": self.name,
            "name": self.name,
            "summary": self.summary,
            "content_sha256": self.content_sha256,
            "reusable": True,
            "authored_profile": self.authored_profile,
            "profile_status": self.profile_status,
            "participant": json.loads(canonical_json_bytes(self.participant)),
            "entity_state": json.loads(canonical_json_bytes(self.entity_state)),
            "setting_data": json.loads(canonical_json_bytes(self.setting_data)),
        }


def _library_root(storage_root: Path) -> Path:
    return storage_root / "participants"


def _atomic_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}-",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary.write(value)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
    except OSError:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def _validate_role(role: str) -> None:
    if role not in {"player_character", "companion"}:
        raise ValueError("Reusable participant role is invalid")


def _validated_payload(
    *,
    role: str,
    participant: SafePayload,
    entity_state: SafePayload,
    declaration: ParticipantArtifactDeclaration,
    creating: bool = False,
    setting_data: SafePayload | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    _validate_role(role)
    participant_value = json.loads(canonical_json_bytes(participant))
    state_value = json.loads(canonical_json_bytes(entity_state))
    if not isinstance(participant_value, dict) or not isinstance(state_value, dict):
        raise ValueError("Reusable participant content must use JSON objects")
    properties = declaration.schema.get("properties")
    if isinstance(properties, Mapping) and "party_role" in properties:
        participant_value["party_role"] = role
    try:
        validate_json_schema(declaration.schema, label="Reusable participant schema")
        validate_parameters(participant_value, declaration.schema)
    except MechanicsRejected as error:
        raise ParticipantValidationError(error.validation_issues) from error
    except Exception as error:
        raise ValueError("Reusable participant does not match its exact schema") from error
    if declaration.entity_state_schema is not None:
        validate_state = (
            materialize_initial_character_state if creating else verify_initial_character_state
        )
        state_value = dict(
            validate_state(
                participant_value,
                state_value,
                entity_state_schema=declaration.entity_state_schema,
                initial_state_calculations=declaration.initial_state_calculations,
                initial_state_validation=declaration.initial_state_validation,
                setting_data=setting_data,
            )
        )
    elif creating:
        raise CommandRejected(
            "This Ruleset has not declared complete character creation data",
            code="character_creation_contract_missing",
            diagnostics=(
                RulesetDiagnostic(
                    code="character_creation_contract_missing",
                    phase="character_initial_state",
                    message="The contained Ruleset needs a complete starting state declaration.",
                    path="participant_artifacts.entity_state_schema",
                    guidance=(
                        "Explicitly update the Setting's contained Ruleset before creation. "
                        "Declare authored inputs and calculated outputs; do not guess defaults."
                    ),
                ),
            ),
        )
    encoded = canonical_json_bytes({"participant": participant_value, "entity_state": state_value})
    if len(encoded) > _MAX_PARTICIPANT_BYTES:
        raise ValueError("Reusable participant content is oversized")
    return participant_value, state_value


def _validated_authored_profile(value: object) -> str:
    """Validate the authored Character File body without imposing a prose word limit."""

    if not isinstance(value, str) or not value:
        raise ValueError("authored_profile must be a non-empty text string; Markdown is allowed")
    if (
        value != value.strip()
        or "\x00" in value
        or len(value.encode("utf-8")) > _MAX_PARTICIPANT_BYTES
    ):
        raise ValueError("Reusable participant authored profile is invalid")
    return value


def parse_setting_character(
    source: str,
    *,
    character_id: str,
    role: str,
    package: PackageLockEntry,
    declaration: ParticipantArtifactDeclaration,
    campaign_setting: PackageBinding,
    setting_character_schema: SafePayload | None = None,
) -> ReusableParticipant:
    """Parse one exact playable Character File from the selected Setting package."""

    _validate_role(role)
    if (
        not isinstance(source, str)
        or not source
        or "\x00" in source
        or len(source.encode("utf-8")) > _MAX_PARTICIPANT_BYTES
        or not isinstance(character_id, str)
        or not character_id
        or len(character_id) > 160
    ):
        raise ValueError("Setting Character File input is invalid")

    def unique_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Character File members must be unique")
            result[key] = value
        return result

    try:
        envelope = json.loads(source, object_pairs_hook=unique_members)
        pending = [(envelope, 0)]
        while pending:
            value, depth = pending.pop()
            if depth > 32:
                raise ValueError("Character File nesting is too deep")
            if isinstance(value, dict):
                pending.extend((child, depth + 1) for child in value.values())
            elif isinstance(value, list):
                pending.extend((child, depth + 1) for child in value)
    except (ValueError, RecursionError) as error:
        raise ValueError("Setting Character File is invalid JSON") from error
    fields = {
        "format_id",
        "format_version",
        "id",
        "schema",
        "summary",
        "authored_profile",
        "participant",
        "entity_state",
    }
    format_version = envelope.get("format_version") if isinstance(envelope, Mapping) else None
    if format_version == SETTING_CHARACTER_FORMAT_VERSION:
        validate_content_shape("character_file", envelope)
        fields.add("setting_data")
    if (
        not isinstance(envelope, Mapping)
        or set(envelope) != fields
        or envelope.get("format_id") != SETTING_CHARACTER_FORMAT_ID
        or format_version not in {1, SETTING_CHARACTER_FORMAT_VERSION}
        or type(envelope.get("format_version")) is not int
        or envelope.get("id") != character_id
    ):
        raise ValueError("Setting Character File envelope is invalid")
    schema = envelope.get("schema")
    if (
        not isinstance(schema, Mapping)
        or set(schema) != {"id", "content_sha256"}
    ):
        raise ValueError("Setting Character File schema is incompatible")
    legacy_declaration = None
    if schema.get("id") != declaration.schema_id or schema.get("content_sha256") != declaration.schema_sha256:
        from sylvia_gm.application.srd_lite_character_compatibility import (
            retained_equipment_character_contract,
        )
        legacy_declaration = retained_equipment_character_contract(package, declaration, schema)
        if legacy_declaration is None:
            raise ValueError("Setting Character File schema is incompatible")
    summary = envelope.get("summary")
    if (
        not isinstance(summary, str)
        or not summary
        or summary != summary.strip()
        or len(summary) > 500
        or "\x00" in summary
    ):
        raise ValueError("Setting Character File summary is invalid")
    authored_profile = _validated_authored_profile(envelope.get("authored_profile"))
    setting_data = validate_setting_character_data(
        envelope.get("setting_data", {}), schema=setting_character_schema
    )
    source_state = cast(SafePayload, envelope.get("entity_state"))
    source_migration = None
    if legacy_declaration is not None:
        from sylvia_gm.application.srd_lite_weapon_set_migration import (
            MIGRATION_ID, PACKAGE_ID, migrate_state,
        )
        _, validated_old = _validated_payload(
            role=role, participant=cast(SafePayload, envelope.get("participant")),
            entity_state=source_state, declaration=legacy_declaration, setting_data=setting_data,
        )
        definitions = declaration.initial_state_validation.get("definitions", {})
        namespace = PACKAGE_ID + ".entities"
        migrated = migrate_state({namespace: {character_id: validated_old}}, definitions["saved_weapons"])
        source_state = migrated[namespace][character_id]
        source_migration = {
            "migration_id": MIGRATION_ID,
            "source_content_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "source_schema_sha256": schema["content_sha256"],
            "target_schema_sha256": declaration.schema_sha256,
        }
    participant, entity_state = _validated_payload(
        role=role,
        participant=cast(SafePayload, envelope.get("participant")),
        entity_state=source_state,
        declaration=declaration,
        setting_data=setting_data,
    )
    name = participant.get("name")
    if not isinstance(name, str) or not name or name != name.strip() or len(name) > 160:
        raise ValueError("Setting Character File name is invalid")
    canonical_envelope = {
        "format_id": SETTING_CHARACTER_FORMAT_ID,
        "format_version": format_version,
        "id": character_id,
        "schema": {
            "id": declaration.schema_id,
            "content_sha256": declaration.schema_sha256,
        },
        "summary": summary,
        "authored_profile": authored_profile,
        "participant": participant,
        "entity_state": entity_state,
    }
    if format_version == SETTING_CHARACTER_FORMAT_VERSION:
        canonical_envelope["setting_data"] = setting_data
    if source_migration is not None:
        canonical_envelope["source_migration"] = source_migration
    encoded = canonical_json_bytes(canonical_envelope)
    if len(encoded) > _MAX_PARTICIPANT_BYTES:
        raise ValueError("Setting Character File content is oversized")
    return ReusableParticipant(
        artifact_id=character_id,
        role=role,
        name=name,
        summary=summary,
        authored_profile=authored_profile,
        content_sha256=hashlib.sha256(encoded).hexdigest(),
        package_id=package.package_id,
        package_version=package.version,
        package_sha256=package.sha256,
        schema_id=declaration.schema_id,
        schema_sha256=declaration.schema_sha256,
        campaign_setting_id=campaign_setting.package_id,
        campaign_setting_version=campaign_setting.version,
        campaign_setting_sha256=campaign_setting.content_sha256,
        participant=cast(SafePayload, participant),
        entity_state=cast(SafePayload, entity_state),
        setting_data=cast(SafePayload, setting_data),
        source_migration=source_migration,
    )


def validate_reusable_participant_draft(
    *,
    role: str,
    summary: str,
    authored_profile: str,
    participant: SafePayload,
    entity_state: SafePayload,
    declaration: ParticipantArtifactDeclaration,
    setting_data: SafePayload | None = None,
    setting_character_schema: SafePayload | None = None,
) -> tuple[str, dict[str, object], dict[str, object], dict[str, object]]:
    """Check authored input and calculate a complete character without saving or campaign access."""

    if (
        not isinstance(summary, str)
        or summary != summary.strip()
        or not summary
        or len(summary) > 500
        or "\x00" in summary
    ):
        raise ValueError("Reusable participant metadata is invalid")
    profile_value = _validated_authored_profile(authored_profile)
    setting_value = validate_setting_character_data(
        {} if setting_data is None else setting_data, schema=setting_character_schema
    )
    participant_value, state_value = _validated_payload(
        role=role,
        participant=participant,
        entity_state=entity_state,
        declaration=declaration,
        creating=True,
        setting_data=setting_value,
    )
    name = participant_value.get("name")
    if not isinstance(name, str) or name != name.strip() or not name or len(name) > 160:
        raise ValueError("Reusable participant name is invalid")
    payload = {
        "participant": participant_value,
        "entity_state": state_value,
        "setting_data": setting_value,
    }
    if len(canonical_json_bytes(payload)) + len(profile_value.encode("utf-8")) > (
        _MAX_PARTICIPANT_BYTES
    ):
        raise ValueError("Reusable participant content is oversized")
    return profile_value, participant_value, state_value, setting_value


def save_reusable_participant(
    storage_root: Path,
    *,
    operation_id: str,
    role: str,
    summary: str,
    authored_profile: str,
    participant: SafePayload,
    entity_state: SafePayload,
    package: PackageLockEntry,
    declaration: ParticipantArtifactDeclaration,
    campaign_setting: PackageBinding,
    setting_data: SafePayload | None = None,
    setting_character_schema: SafePayload | None = None,
    source_profile: SafePayload | None = None,
) -> ReusableParticipant:
    """Validate and persist one immutable owner-local participant artifact."""

    if not isinstance(operation_id, str) or not operation_id:
        raise ValueError("Reusable participant operation identity is invalid")
    profile_value, participant_value, state_value, setting_value = (
        validate_reusable_participant_draft(
            role=role, summary=summary, authored_profile=authored_profile,
            participant=participant, entity_state=entity_state, declaration=declaration,
            setting_data=setting_data, setting_character_schema=setting_character_schema,
        )
    )
    name = str(participant_value["name"])
    source_profile = validate_source_profile(source_profile)
    payload = {
        "participant": participant_value, "entity_state": state_value, "setting_data": setting_value
    }
    identity = {
        "role": role,
        "source_profile": source_profile,
        "summary": summary,
        "authored_profile": profile_value,
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
    operation_suffix = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
    artifact_id = f"participant-{operation_suffix}-{content_sha256[:16]}"
    record = {
        "format_id": REUSABLE_PARTICIPANT_FORMAT_ID,
        "format_version": REUSABLE_PARTICIPANT_FORMAT_VERSION,
        "artifact_id": artifact_id,
        "source_profile": source_profile,
        "role": role,
        "name": name,
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
    target = _library_root(storage_root) / artifact_id
    with _LIBRARY_WRITE_LOCK:
        if target.exists():
            existing = load_reusable_participant(
                storage_root,
                artifact_id,
                package=package,
                declaration=declaration,
                campaign_setting=campaign_setting,
                setting_character_schema=setting_character_schema,
            )
            if existing.content_sha256 != content_sha256:
                raise ValueError("Reusable participant identity conflicts")
            return existing
        target.parent.mkdir(parents=True, exist_ok=True)
        # Make the immutable entry visible only after every file is ready. Other chats can refresh
        # the library during a save without observing a partial artifact.
        with tempfile.TemporaryDirectory(
            prefix=".participant-save-", dir=storage_root
        ) as temporary:
            staged = Path(temporary) / artifact_id
            staged.mkdir()
            _atomic_write(staged / "profile.md", profile_value.encode("utf-8"))
            _atomic_write(staged / "payload.json", canonical_json_bytes(payload))
            _atomic_write(staged / "record.json", canonical_json_bytes(record))
            staged.rename(target)
        return load_reusable_participant(
            storage_root,
            artifact_id,
            package=package,
            declaration=declaration,
            campaign_setting=campaign_setting,
            setting_character_schema=setting_character_schema,
        )


def delete_reusable_participant(
    storage_root: Path,
    artifact_id: str,
    *,
    expected_content_sha256: str,
    confirmation: str,
) -> bool:
    """Delete one exact local participant after fencing against a stale catalog choice."""

    if _ARTIFACT_ID.fullmatch(artifact_id) is None:
        raise ValueError("Reusable participant identity is invalid")
    if re.fullmatch(r"[0-9a-f]{64}", expected_content_sha256) is None:
        raise ValueError("Reusable participant version is invalid")
    if confirmation != f"permanently delete {artifact_id}":
        raise ValueError("Reusable participant deletion confirmation is not exact")
    with _LIBRARY_WRITE_LOCK:
        target = validated_library_entry(
            storage_root, library_name="participants", artifact_id=artifact_id
        )
        if target is None:
            return False
        owned_names = frozenset({"profile.md", "payload.json", "record.json"})
        validate_owned_files(target, owned_names)
        record_path = target / "record.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("artifact_id") != artifact_id:
            raise ValueError("Reusable participant record is invalid")
        if record.get("content_sha256") != expected_content_sha256:
            raise ValueError("Reusable participant changed; refresh the library and try again")
        return delete_exact_library_entry(
            storage_root,
            library_name="participants",
            artifact_id=artifact_id,
            owned_names=owned_names,
        )


def load_reusable_participant(
    storage_root: Path,
    artifact_id: str,
    *,
    package: PackageLockEntry,
    declaration: ParticipantArtifactDeclaration,
    campaign_setting: PackageBinding,
    legacy_campaign_setting: PackageBinding | None = None,
    setting_character_schema: SafePayload | None = None,
) -> ReusableParticipant:
    """Load and revalidate one exact local participant without trusting its record."""

    if _ARTIFACT_ID.fullmatch(artifact_id) is None:
        raise ValueError("Reusable participant identity is invalid")
    root = _library_root(storage_root) / artifact_id
    record = json.loads((root / "record.json").read_text(encoding="utf-8"))
    common_fields = {
        "format_id",
        "format_version",
        "artifact_id",
        "role",
        "name",
        "summary",
        "content_sha256",
        "package_id",
        "package_version",
        "package_sha256",
        "schema_id",
        "schema_sha256",
    }
    setting_fields = {
        "campaign_setting_id",
        "campaign_setting_version",
        "campaign_setting_sha256",
    }
    if not isinstance(record, dict):
        raise ValueError("Reusable participant record is invalid")
    format_version = record.get("format_version")
    if type(format_version) is not int:
        raise ValueError("Reusable participant record version is invalid")
    if format_version == 1:
        if set(record) != common_fields or legacy_campaign_setting != campaign_setting:
            raise ValueError("Reusable participant record is incompatible")
    elif format_version in {2, 3, 4, REUSABLE_PARTICIPANT_FORMAT_VERSION}:
        provenance_fields = {"source_profile"} if format_version == 5 else set()
        if set(record) != common_fields | setting_fields | provenance_fields:
            raise ValueError("Reusable participant record is invalid")
        if (
            record["campaign_setting_id"] != campaign_setting.package_id
            or record["campaign_setting_version"] != campaign_setting.version
            or record["campaign_setting_sha256"] != campaign_setting.content_sha256
        ):
            raise ValueError("Reusable participant record is incompatible")
    else:
        raise ValueError("Reusable participant record is incompatible")
    if (
        record["format_id"] != REUSABLE_PARTICIPANT_FORMAT_ID
        or record["artifact_id"] != artifact_id
        or record["package_id"] != package.package_id
        or record["package_version"] != package.version
        or record["package_sha256"] != package.sha256
        or record["schema_id"] != declaration.schema_id
        or record["schema_sha256"] != declaration.schema_sha256
    ):
        raise ValueError("Reusable participant record is incompatible")
    payload = json.loads((root / "payload.json").read_text(encoding="utf-8"))
    payload_fields = {"participant", "entity_state"}
    if format_version in {4, 5}:
        payload_fields.add("setting_data")
    if not isinstance(payload, dict) or set(payload) != payload_fields:
        raise ValueError("Reusable participant payload is invalid")
    setting_data = validate_setting_character_data(
        payload.get("setting_data", {}), schema=setting_character_schema
    )
    role = str(record["role"])
    participant, entity_state = _validated_payload(
        role=role,
        participant=cast(SafePayload, payload["participant"]),
        entity_state=cast(SafePayload, payload["entity_state"]),
        declaration=declaration,
        setting_data=setting_data,
    )
    authored_profile = (
        _validated_authored_profile((root / "profile.md").read_bytes().decode("utf-8"))
        if format_version in {3, 4, 5}
        else None
    )
    if authored_profile is not None and (
        len(canonical_json_bytes(payload)) + len(authored_profile.encode("utf-8"))
        > _MAX_PARTICIPANT_BYTES
    ):
        raise ValueError("Reusable participant content is oversized")
    identity = {
        "role": role,
        "summary": record["summary"],
        "package_id": package.package_id,
        "package_version": package.version,
        "package_sha256": package.sha256,
        "schema_id": declaration.schema_id,
        "schema_sha256": declaration.schema_sha256,
        "payload": {"participant": participant, "entity_state": entity_state},
    }
    if authored_profile is not None:
        identity["authored_profile"] = authored_profile
    source_profile = validate_source_profile(record.get("source_profile"))
    if format_version == 5:
        identity["source_profile"] = source_profile
    if format_version in {4, 5}:
        identity["payload"]["setting_data"] = setting_data
    if format_version in {2, 3, 4, 5}:
        identity.update(
            {
                "campaign_setting_id": campaign_setting.package_id,
                "campaign_setting_version": campaign_setting.version,
                "campaign_setting_sha256": campaign_setting.content_sha256,
            }
        )
    content_sha256 = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    name = participant.get("name")
    if (
        content_sha256 != record["content_sha256"]
        or not isinstance(name, str)
        or name != record["name"]
        or not isinstance(record["summary"], str)
        or not str(record["summary"]).strip()
        or str(record["summary"]) != str(record["summary"]).strip()
        or len(str(record["summary"])) > 500
    ):
        raise ValueError("Reusable participant content hash is invalid")
    return ReusableParticipant(
        artifact_id=artifact_id,
        role=role,
        name=name,
        summary=str(record["summary"]),
        source_profile=source_profile,
        authored_profile=authored_profile,
        content_sha256=content_sha256,
        package_id=package.package_id,
        package_version=package.version,
        package_sha256=package.sha256,
        schema_id=declaration.schema_id,
        schema_sha256=declaration.schema_sha256,
        campaign_setting_id=campaign_setting.package_id,
        campaign_setting_version=campaign_setting.version,
        campaign_setting_sha256=campaign_setting.content_sha256,
        participant=cast(SafePayload, participant),
        entity_state=cast(SafePayload, entity_state),
        setting_data=cast(SafePayload, setting_data),
    )


def compatible_reusable_participants(
    storage_root: Path,
    *,
    package: PackageLockEntry,
    declaration: ParticipantArtifactDeclaration,
    campaign_setting: PackageBinding,
    legacy_campaign_setting: PackageBinding | None = None,
    setting_character_schema: SafePayload | None = None,
    on_invalid: Callable[[str, Exception], None] | None = None,
) -> tuple[ReusableParticipant, ...]:
    """List participants compatible with one exact Setting and Ruleset schema."""

    root = _library_root(storage_root)
    if not root.exists():
        return ()
    directories = tuple(path for path in root.iterdir() if path.is_dir())
    if len(directories) > _MAX_LIBRARY_PARTICIPANTS:
        raise ValueError("Reusable participant library is oversized")
    compatible: list[ReusableParticipant] = []
    for path in sorted(directories, key=lambda item: item.name):
        try:
            participant = load_reusable_participant(
                storage_root,
                path.name,
                package=package,
                declaration=declaration,
                campaign_setting=campaign_setting,
                legacy_campaign_setting=legacy_campaign_setting,
                setting_character_schema=setting_character_schema,
            )
        except (OSError, UnicodeError, ValueError, CommandRejected) as error:
            if str(error) == "Reusable participant record is incompatible":
                continue
            if on_invalid is None:
                raise
            on_invalid(path.name, error)
            continue
        compatible.append(participant)
    return tuple(compatible)
