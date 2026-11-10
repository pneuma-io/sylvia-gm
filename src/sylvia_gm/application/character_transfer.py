"""Transfer selected character sources through existing immutable libraries and image storage."""

from __future__ import annotations

import io
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, cast

from sylvia_gm.application.bundled_character_art import bundled_character_image
from sylvia_gm.application.campaign_plan_sources import (
    CampaignPlanToolAuthority,
    campaign_plan_sources,
)
from sylvia_gm.application.character_bundle import (
    CHARACTER_BUNDLE_BYTE_LIMIT,
    read_character_bundle,
    write_character_bundle,
)
from sylvia_gm.application.character_card_import import (
    card_portrait,
    load_import_original,
    retain_import_original,
)
from sylvia_gm.application.character_profiles import (
    CharacterProfile,
    load_character_profile,
    save_character_profile,
)
from sylvia_gm.application.compatible_character_copy import authored_character_state
from sylvia_gm.application.image_hooks import ImageData, ImageHookError
from sylvia_gm.application.reusable_library_deletion import (
    is_link_or_reparse,
    validate_owned_files,
    validated_library_entry,
)
from sylvia_gm.application.reusable_participants import (
    ReusableParticipant,
    _atomic_write,
    load_reusable_participant,
    parse_setting_character,
    save_reusable_participant,
)
from sylvia_gm.domain.authority import PackageLockEntry, PackageRole, SafePayload, thaw_json_value
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.image_art import PortraitFrame, validate_character_visuals
from sylvia_gm.domain.package_library import (
    CandidateStatus,
    ParticipantArtifactDeclaration,
    campaign_setting_entries,
    canonical_json_bytes,
)

if TYPE_CHECKING:
    from sylvia_gm.application.authority import AuthorityOwner

_PURPOSES = ("full_body", "reference_sheet", "portrait")
_DIGEST = re.compile(r"[0-9a-f]{64}")


def export_character_source(
    owner: AuthorityOwner,
    storage_root: Path,
    source: CharacterProfile | ReusableParticipant,
    *,
    target: BinaryIO,
) -> dict[str, Any]:
    """Capture the selected source's adopted art, including a usable original card portrait."""
    value = (
        cast(dict[str, Any], thaw_json_value(source.profile))
        if isinstance(source, CharacterProfile)
        else {}
    )
    original = None
    if value.get("import_source"):
        try:
            original = load_import_original(storage_root, str(value["import_source"]["sha256"]))
        except (OSError, ValueError) as error:
            raise ValueError(
                "Import this character's original card again before exporting it"
            ) from error
    visuals_value = owner._store.images.published_visuals(source.artifact_id, source.content_sha256)
    selected_visuals: object = visuals_value or (
        value.get("visuals") if isinstance(source, CharacterProfile) else source.visuals
    )
    visuals = validate_character_visuals(selected_visuals)
    extra = {}
    if (
        original is not None
        and value["import_source"]["container"] == "png"
        and not (visuals or {}).get("portrait")
    ):
        try:
            portrait = ImageData(card_portrait(original))
        except ImageHookError:
            pass  # Retain the original while preserving the importer's unsupported-image outcome.
        else:
            biography = value.get("biography", {})
            appearance = (
                biography.get("appearance") or biography.get("description") or value["name"]
            )
            visuals = visuals or {"appearance": str(appearance)[:16000]}
            visuals.update(
                status="partial",
                missing=[
                    purpose
                    for purpose in _PURPOSES
                    if purpose == "portrait" or purpose not in visuals
                ],
            )
            path = f"assets/characters/{portrait.sha256}.png"
            visuals["portrait"] = {"path": path, "sha256": portrait.sha256}
            visuals.setdefault("frame", PortraitFrame().as_payload())
            extra[path] = portrait.data

    def image(reference: Mapping[str, Any]) -> bytes:
        path = reference.get("path", f"assets/characters/{reference['sha256']}.png")
        if path in extra:
            return extra[path]
        operation = owner._store.images.get(reference.get("operation_id", ""))
        if (
            operation
            and operation["status"] == "succeeded"
            and operation["result_sha256"] == reference["sha256"]
        ):
            return owner._store.images.image(reference["sha256"])
        prepared = bundled_character_image(path, reference["sha256"])
        if prepared is not None:
            return prepared
        if isinstance(source, ReusableParticipant):
            raw = owner._store.package_file_bytes(
                source.campaign_setting_id,
                source.campaign_setting_version,
                source.campaign_setting_sha256,
                path,
                maximum_bytes=16 * 1024 * 1024,
            )
            if raw is not None:
                return raw
        raise ValueError(
            "Saved artwork is missing; restore the character's complete images before exporting"
        )

    return write_character_bundle(
        source,
        image_reader=image,
        target=target,
        visuals=visuals,
        original_card=original,
    )


def _character_context(
    owner: AuthorityOwner,
    bindings: dict[str, Any],
    schema: dict[str, Any],
) -> tuple[PackageLockEntry, ParticipantArtifactDeclaration, CampaignPlanToolAuthority]:
    """Require an installed closure with the exact Ruleset, Setting and participant schema."""
    for candidate in owner.package_candidates():
        if candidate.status not in {CandidateStatus.VALIDATED, CandidateStatus.ACTIVATED}:
            continue
        primary = next(
            (
                entry
                for entry in candidate.package_lock.entries
                if entry.role is PackageRole.PRIMARY
            ),
            None,
        )
        if primary is None or (primary.package_id, primary.version, primary.sha256) != tuple(
            bindings["ruleset"][key] for key in ("id", "version", "content_sha256")
        ):
            continue
        for setting in campaign_setting_entries(candidate.package_lock):
            if (setting.package_id, setting.version, setting.sha256) != tuple(
                bindings["campaign_setting"][key] for key in ("id", "version", "content_sha256")
            ):
                continue
            authority = campaign_plan_sources(
                owner, candidate.package_lock, setting_id=setting.package_id
            )
            if authority is None:
                continue
            declaration = next(
                (
                    item
                    for item in authority.character_declarations
                    if item.schema_id == schema["id"]
                    and item.schema_sha256 == schema["content_sha256"]
                ),
                None,
            )
            if declaration is not None:
                return primary, declaration, authority
    raise CommandRejected(
        "Install this character's matching Ruleset and Setting packages, then import it again. "
        "Its package versions and character schema must match; no build is substituted.",
        code="character_packages_missing",
    )


def export_character_file(
    owner: AuthorityOwner,
    storage_root: Path,
    *,
    export_id: str,
    artifact_id: str,
    expected_content_sha256: str,
    kind: str,
) -> dict[str, Any]:
    """Publish a complete export atomically inside the owner's export directory."""
    if (
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", export_id) is None
        or Path(export_id).is_reserved()
        or export_id.endswith(".")
    ):
        raise ValueError("Choose a valid character export identity")
    source = load_character_source(
        owner,
        storage_root,
        artifact_id=artifact_id,
        expected_content_sha256=expected_content_sha256,
        kind=kind,
    )
    output = io.BytesIO()
    receipt = export_character_source(owner, storage_root, source, target=output)
    root = storage_root / "exports"
    target = root / f"{export_id}.sylvia-gm-character"
    if os.path.lexists(root) and (is_link_or_reparse(root) or not root.is_dir()):
        raise ValueError("Character export storage is unsafe")
    if os.path.lexists(target) and (is_link_or_reparse(target) or not target.is_file()):
        raise ValueError("Character export destination is unsafe")
    root.mkdir(parents=True, exist_ok=True)
    _atomic_write(target, output.getvalue())
    return {"export": receipt, "artifact_path": str(target.resolve(strict=True))}


def load_character_source(
    owner: AuthorityOwner,
    storage_root: Path,
    *,
    artifact_id: str,
    expected_content_sha256: str,
    kind: str,
) -> CharacterProfile | ReusableParticipant:
    """Load one selected immutable source, including an already-built starter cache entry."""
    if kind == "character_profile":
        return load_character_profile(storage_root, artifact_id, expected_content_sha256)
    if kind not in {"player_character", "companion"}:
        raise ValueError("Choose a saved Character File or Universal character profile")
    for root in (storage_root, storage_root / "bundled-srd-starters"):
        if os.path.lexists(root) and is_link_or_reparse(root):
            raise ValueError("Character library storage is unsafe")
        entry = validated_library_entry(root, library_name="participants", artifact_id=artifact_id)
        if entry is None:
            continue
        validate_owned_files(entry, frozenset({"record.json", "payload.json", "profile.md"}))
        if any(path.stat().st_size > 256 * 1024 for path in entry.iterdir()):
            raise ValueError("Saved character files exceed the supported size")
        record = json.loads((entry / "record.json").read_bytes())
        if (
            not isinstance(record, dict)
            or record.get("content_sha256") != expected_content_sha256
            or record.get("role") != kind
        ):
            raise ValueError("The selected character changed; refresh its library entry")
        bindings = {
            "ruleset": {
                "id": record["package_id"],
                "version": record["package_version"],
                "content_sha256": record["package_sha256"],
            },
            "campaign_setting": {
                "id": record["campaign_setting_id"],
                "version": record["campaign_setting_version"],
                "content_sha256": record["campaign_setting_sha256"],
            },
        }
        package, declaration, authority = _character_context(
            owner,
            bindings,
            {
                "id": record["schema_id"],
                "content_sha256": record["schema_sha256"],
            },
        )
        return load_reusable_participant(
            root,
            artifact_id,
            package=package,
            declaration=declaration,
            campaign_setting=authority.setting,
            setting_character_schema=authority.setting_character_schema,
        )
    raise ValueError("The selected character is unavailable; refresh the library")


def import_character_source(
    owner: AuthorityOwner,
    storage_root: Path,
    stream: BinaryIO,
    *,
    expected_content_sha256: str,
) -> dict[str, Any]:
    """Validate every source and PNG before retaining local images or publishing a library entry."""
    bundle = read_character_bundle(stream)
    if bundle.content_sha256 != expected_content_sha256:
        raise ValueError("This character file changed; preview it again before importing")
    data = cast(dict[str, Any], thaw_json_value(bundle.character))
    context = None
    if bundle.manifest["kind"] == "character_file":
        context = _character_context(owner, bundle.manifest["bindings"], data["schema"])
        package, declaration, authority = context
        parse_setting_character(
            bundle.files["character.json"].decode(),
            character_id=data["id"],
            role=bundle.manifest["role"],
            package=package,
            declaration=declaration,
            campaign_setting=authority.setting,
            setting_character_schema=authority.setting_character_schema,
        )
    scope = "character-import:" + bundle.content_sha256
    visuals = validate_character_visuals(data.get("visuals") or bundle.manifest["visuals"])
    if visuals is not None:
        owner._store.images.attach_character_visuals(
            scope_id=scope,
            campaign_id=None,
            session_id=scope,
            target_id="character",
            visuals=visuals,
            assets=bundle.files,
            name=bundle.preview()["name"],
        )
        retained = {row["purpose"]: row for row in owner._store.images.list_selections(scope)}
        for purpose in _PURPOSES:
            if purpose in visuals:
                visuals[purpose]["operation_id"] = retained[purpose]["operation_id"]
        if not (bundle.manifest["kind"] == "character_profile" and data["format_version"] == 1):
            data["visuals"] = visuals
    if "original-card.bin" in bundle.files:
        retain_import_original(
            storage_root,
            bundle.files["original-card.bin"],
            expected_sha256=data["import_source"]["sha256"],
        )
    saved: CharacterProfile | ReusableParticipant
    if context is None:
        saved = save_character_profile(
            storage_root,
            operation_id=scope,
            source=canonical_json_bytes(data),
        )
        if visuals is not None and data["format_version"] == 1:
            owner._store.images.bind_publication(
                [row["operation_id"] for row in retained.values()],
                saved.artifact_id,
                saved.content_sha256,
            )
    else:
        package, declaration, authority = context
        saved = save_reusable_participant(
            storage_root,
            operation_id=scope,
            role=bundle.manifest["role"],
            summary=data["summary"],
            authored_profile=data["authored_profile"],
            participant=data["participant"],
            entity_state=authored_character_state(data["entity_state"], declaration),
            setting_data=data["setting_data"],
            package=package,
            declaration=declaration,
            campaign_setting=authority.setting,
            setting_character_schema=authority.setting_character_schema,
            source_profile=bundle.manifest["source_profile"],
            visuals=cast(SafePayload, visuals),
        )
    return {
        "status": "imported",
        "source_sha256": bundle.content_sha256,
        "character": saved.catalog_payload(),
    }


def _preview_path(storage_root: Path, digest: str) -> Path:
    """Confine preview tokens to owned regular files, independent of the chosen source path."""
    if _DIGEST.fullmatch(digest) is None:
        raise ValueError("Character preview identity is invalid")
    root = storage_root / "character-bundle-previews"
    if os.path.lexists(root) and (is_link_or_reparse(root) or not root.is_dir()):
        raise ValueError("Character preview storage is unsafe")
    target = root / digest
    if os.path.lexists(target) and (is_link_or_reparse(target) or not target.is_file()):
        raise ValueError("Character preview is unsafe")
    return target


def stage_character_preview(storage_root: Path, source: Path) -> dict[str, Any]:
    """Capture the exact chosen character file for a later deliberate import."""
    if is_link_or_reparse(source) or not source.is_file():
        raise ValueError("Choose a regular Sylvia character file")
    with source.open("rb") as stream:
        raw = stream.read(CHARACTER_BUNDLE_BYTE_LIMIT + 1)
    bundle = read_character_bundle(io.BytesIO(raw))
    target = _preview_path(storage_root, bundle.content_sha256)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        if sum(1 for path in target.parent.iterdir() if _DIGEST.fullmatch(path.name)) >= 4:
            raise ValueError("Import or cancel a pending character preview before choosing another")
        with target.open("xb") as stream:
            stream.write(raw)
    else:
        with target.open("rb") as stream:
            if read_character_bundle(stream).content_sha256 != bundle.content_sha256:
                raise ValueError("Character preview changed; choose the file again")
    return bundle.preview()


def confirm_character_preview(
    owner: AuthorityOwner,
    storage_root: Path,
    digest: str,
) -> dict[str, Any]:
    source = _preview_path(storage_root, digest)
    receipt = _preview_path(storage_root, digest).with_suffix(".json")
    if os.path.lexists(receipt) and (is_link_or_reparse(receipt) or not receipt.is_file()):
        raise ValueError("Character import receipt is unsafe")
    if not source.exists():
        with receipt.open("rb") as stream:
            raw = stream.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            raise ValueError("Character import receipt is too large")
        result = json.loads(raw)
        if (
            not isinstance(result, dict)
            or result.get("status") != "imported"
            or result.get("source_sha256") != digest
            or not isinstance(result.get("character"), dict)
        ):
            raise ValueError("Character import receipt is invalid")
        item = result["character"]
        if any(not isinstance(item.get(key), str) for key in ("id", "content_sha256", "kind")):
            raise ValueError("Character import receipt is invalid")
        saved = load_character_source(
            owner,
            storage_root,
            artifact_id=item["id"],
            expected_content_sha256=item["content_sha256"],
            kind=item["kind"],
        )
        return {"status": "imported", "source_sha256": digest, "character": saved.catalog_payload()}
    with source.open("rb") as stream:
        result = import_character_source(
            owner, storage_root, stream, expected_content_sha256=digest
        )
    _atomic_write(receipt, canonical_json_bytes(result))
    source.unlink()
    return result


def cancel_character_preview(storage_root: Path, digest: str) -> dict[str, str]:
    """Discard only this verified temporary preview; the user's chosen file is untouched."""
    source = _preview_path(storage_root, digest)
    if source.exists():
        with source.open("rb") as stream:
            if read_character_bundle(stream).content_sha256 != digest:
                raise ValueError("Character preview changed; choose the file again")
        source.unlink()
    return {"status": "cancelled", "source_sha256": digest}
