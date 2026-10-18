"""Explicitly copy one unchanged character to a compatible exact Setting binding."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from sylvia_gm.application.initial_character_state import verify_initial_character_state
from sylvia_gm.application.reusable_participants import (
    ReusableParticipant,
    save_reusable_participant,
)
from sylvia_gm.application.schema_admission import validate_json_schema, validate_parameters
from sylvia_gm.application.setting_character_requirements import validate_setting_character_data
from sylvia_gm.domain.authority import PackageLockEntry, SafePayload
from sylvia_gm.domain.campaign_plan import PackageBinding
from sylvia_gm.domain.errors import CommandRejected, MechanicsRejected, RulesetDiagnostic
from sylvia_gm.domain.package_library import ParticipantArtifactDeclaration, canonical_json_bytes

_SHA256 = re.compile(r"[0-9a-f]{64}")


def _adaptation_required(error: Exception, *, path: str, message: str) -> CommandRejected:
    """Convert destination incompatibility into bounded, value-free Builder guidance."""

    existing = getattr(error, "diagnostics", ())
    if existing:
        diagnostics = tuple(
            RulesetDiagnostic(
                code="character_adaptation_required",
                phase="compatible_character_copy",
                path=item.path,
                message=message,
                expected=item.expected,
                actual=item.actual,
                guidance="Adapt an explicit copy in Builder for the selected Setting.",
            )
            for item in existing[:16]
        )
    elif isinstance(error, MechanicsRejected) and error.validation_issues:
        diagnostics = tuple(
            RulesetDiagnostic(
                code="character_adaptation_required",
                phase="compatible_character_copy",
                path=issue.path,
                message=message,
                expected=issue.expected_type,
                actual=issue.actual_type,
                guidance="Adapt an explicit copy in Builder for the selected Setting.",
            )
            for issue in error.validation_issues[:16]
        )
    else:
        diagnostics = (
            RulesetDiagnostic(
                code="character_adaptation_required",
                phase="compatible_character_copy",
                path=path,
                message=message,
                guidance="Adapt an explicit copy in Builder for the selected Setting.",
            ),
        )
    return CommandRejected(
        "The character needs explicit adaptation for the selected Setting",
        code="character_adaptation_required",
        diagnostics=diagnostics,
    )


def authored_character_state(
    complete: SafePayload, declaration: ParticipantArtifactDeclaration
) -> SafePayload:
    """Omit calculated leaves before the established save path recomputes them."""

    authored = json.loads(canonical_json_bytes(complete))
    for calculation in declaration.initial_state_calculations:
        setting = calculation.get("set")
        field = setting.get("field") if isinstance(setting, Mapping) else None
        if not isinstance(field, str):
            raise ValueError("Destination calculation target is invalid")
        current: object = authored
        parts = field.split(".")
        for part in parts[:-1]:
            if not isinstance(current, dict) or not isinstance(current.get(part), dict):
                current = None
                break
            current = current[part]
        if isinstance(current, dict):
            current.pop(parts[-1], None)
    return cast(SafePayload, authored)


def copy_compatible_character(
    storage_root: Path,
    *,
    source: ReusableParticipant,
    expected_content_sha256: str,
    operation_id: str,
    package: PackageLockEntry,
    declaration: ParticipantArtifactDeclaration,
    campaign_setting: PackageBinding,
    setting_character_schema: SafePayload | None = None,
) -> ReusableParticipant:
    """Save a new exact binding only when every unchanged source value satisfies the destination."""

    if (
        not isinstance(expected_content_sha256, str)
        or _SHA256.fullmatch(expected_content_sha256) is None
        or source.content_sha256 != expected_content_sha256
    ):
        raise CommandRejected(
            "The source character changed; refresh it before copying", code="stale_character"
        )
    if source.authored_profile is None:
        raise _adaptation_required(
            ValueError(),
            path="authored_profile",
            message="The source character does not contain an authored profile.",
        )
    try:
        validate_json_schema(declaration.schema, label="Destination participant schema")
        validate_parameters(source.participant, declaration.schema)
        properties = declaration.schema.get("properties")
        if (
            isinstance(properties, Mapping)
            and "party_role" in properties
            and source.participant.get("party_role") != source.role
        ):
            raise ValueError("Destination role field would change the participant")
        world_data = validate_setting_character_data(
            source.setting_data, schema=setting_character_schema
        )
        if declaration.entity_state_schema is None:
            raise ValueError("Destination creation contract is incomplete")
        complete = verify_initial_character_state(
            source.participant,
            source.entity_state,
            entity_state_schema=declaration.entity_state_schema,
            initial_state_calculations=declaration.initial_state_calculations,
            initial_state_validation=declaration.initial_state_validation,
            setting_data=world_data,
        )
        authored_state = authored_character_state(complete, declaration)
    except (CommandRejected, TypeError, ValueError) as error:
        raise _adaptation_required(
            error,
            path="character",
            message="The unchanged character data does not satisfy the destination requirements.",
        ) from error

    return save_reusable_participant(
        storage_root,
        operation_id=operation_id,
        role=source.role,
        summary=source.summary,
        authored_profile=source.authored_profile,
        participant=source.participant,
        entity_state=authored_state,
        setting_data=source.setting_data,
        package=package,
        declaration=declaration,
        campaign_setting=campaign_setting,
        setting_character_schema=setting_character_schema,
        source_profile=source.source_profile,
    )


__all__ = ["authored_character_state", "copy_compatible_character"]
