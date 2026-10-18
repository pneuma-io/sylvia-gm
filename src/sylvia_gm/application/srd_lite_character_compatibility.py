"""Exact retained Lite character contract admitted through equipment migration."""

from collections.abc import Mapping
from copy import deepcopy

from sylvia_gm.application._srd_lite_character_legacy import LEGACY_CHARACTER_CONTRACT
from sylvia_gm.domain.authority import PackageLockEntry, PackageRole
from sylvia_gm.domain.package_library import (
    ParticipantArtifactDeclaration,
    participant_schema_sha256,
)

SOURCE_SCHEMA = "f9386ab657153eadab17ff62e296fa40e863db70d8dcfd5a40832398100fdd88"
TARGET_SCHEMA = "78a45a5043067374dd53c7b79bf20dfc0463911cc84828acc5a35d80f41a0569"


def retained_equipment_character_contract(
    package: PackageLockEntry,
    declaration: ParticipantArtifactDeclaration,
    source_schema: Mapping[str, object],
) -> ParticipantArtifactDeclaration | None:
    """Return one trusted old contract; unknown pairs retain exact-schema rejection."""
    if (
        package.package_id != "sylvia.srd-lite.bg3"
        or package.version != "0.3.0"
        or package.role is not PackageRole.PRIMARY
        or declaration.kind != "player_character"
        or declaration.schema_id != "sylvia.srd-lite.bg3.player-character"
        or declaration.schema_sha256 != TARGET_SCHEMA
        or source_schema.get("id") != declaration.schema_id
        or source_schema.get("content_sha256") != SOURCE_SCHEMA
    ):
        return None
    actual = participant_schema_sha256(
        declaration.schema,
        entity_state_schema=declaration.entity_state_schema,
        initial_state_calculations=declaration.initial_state_calculations,
        initial_state_validation=declaration.initial_state_validation,
    )
    if actual != TARGET_SCHEMA:
        return None
    old = deepcopy(LEGACY_CHARACTER_CONTRACT)
    if (
        participant_schema_sha256(
            old["schema"],
            entity_state_schema=old["entity_state_schema"],
            initial_state_calculations=old["initial_state_calculations"],
            initial_state_validation=old["initial_state_validation"],
        )
        != SOURCE_SCHEMA
    ):
        raise ValueError("Retained Lite character migration contract changed")
    return ParticipantArtifactDeclaration(
        kind=old["kind"],
        schema_id=old["schema_id"],
        schema_sha256=SOURCE_SCHEMA,
        schema=old["schema"],
        entity_state_schema=old["entity_state_schema"],
        initial_state_calculations=tuple(old["initial_state_calculations"]),
        initial_state_validation=old["initial_state_validation"],
    )
