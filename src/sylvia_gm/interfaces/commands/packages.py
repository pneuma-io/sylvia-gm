"""Packages command handlers moved from the owner protocol router."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..owner_process import (
    _MAX_PACKAGE_BYTES,
    PLAN_BUNDLE_BYTE_LIMIT,
    AuthorityError,
    CommandRejected,
    OwnerProtocolError,
    Path,
    _install_plan_snapshot,
    _required_artifact_id,
    _required_text,
    base64,
    binascii,
    import_plan_bundle,
    io,
    offer_import_repair,
    zipfile,
)
from . import DispatchContext


def handle_package_candidates(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.bus.package_candidates()


def handle_inspect_package_candidate(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.bus.inspect_package_candidate(_required_text(params, "candidate_id"))


def handle_package_candidate_text(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.bus.package_candidate_text(
        _required_text(params, "candidate_id"),
        _required_text(params, "package_id"),
        _required_text(params, "path"),
    )


def handle_compare_package_candidates(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    return ctx.bus.compare_package_candidates(
        _required_text(params, "left_candidate_id"),
        _required_text(params, "right_candidate_id"),
    )


def handle_install_package(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    archive_text = _required_text(params, "archive_base64")
    try:
        archive_bytes = base64.b64decode(archive_text, validate=True)
    except (binascii.Error, ValueError) as error:
        raise OwnerProtocolError("archive_base64 is invalid") from error
    if len(archive_bytes) > _MAX_PACKAGE_BYTES:
        raise OwnerProtocolError("Package archive exceeds the desktop import limit")
    return ctx.bus.install_package(
        io.BytesIO(archive_bytes),
        install_id=_required_text(params, "install_id"),
    )


def handle_confirm_character_card_import(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    from sylvia_gm.application.character_card_import import (
        confirm_card_preview,
        stage_card_preview,
    )
    try:
        if ctx.method == "preview_character_card_file":
            return stage_card_preview(ctx.storage_root, Path(_required_text(params, "source_path")))
        return confirm_card_preview(ctx.storage_root, _required_text(params, "source_sha256"))
    except (ValueError, OSError) as error:
        raise CommandRejected(str(error), code="character_card_invalid") from error


def handle_install_package_file(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    source = Path(_required_text(params, "source_path")).expanduser().resolve()
    try:
        if not source.is_file():
            raise CommandRejected("Selected package file is unavailable")
        is_plan = source.suffix.lower() == ".sylvia-gm-plan"
        maximum = PLAN_BUNDLE_BYTE_LIMIT if is_plan else _MAX_PACKAGE_BYTES
        if source.stat().st_size > maximum:
            raise CommandRejected("Package archive exceeds the desktop import limit")
        with source.open("rb") as archive_stream:
            try:
                if is_plan:
                    return import_plan_bundle(
                        ctx.storage_root, archive_stream,
                        install_snapshot=lambda snapshot, expected: _install_plan_snapshot(
                            ctx.application_owner, snapshot, expected,
                        ),
                    ).catalog_payload()
                return ctx.bus.install_package(
                    archive_stream,
                    install_id=_required_text(params, "install_id"),
                )
            except (AuthorityError, ValueError) as error:
                repair = offer_import_repair(
                    ctx.storage_root, source,
                    install_id=_required_text(params, "install_id"), error=error,
                )
                if repair is not None:
                    return repair
                raise
    except (ValueError, zipfile.BadZipFile) as error:
        raise CommandRejected(
            "Content archive could not be admitted. Check its format and content, "
            "then export a new copy using Store or Deflate ZIP compression."
        ) from error
    except OSError as error:
        raise CommandRejected("Selected package file is unavailable") from error


def handle_export_package_candidate(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
    export_id = _required_artifact_id(params, "export_id")
    target = ctx.storage_root / "exports" / f"{export_id}.sylvia-gm-package"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as stream:
        result = ctx.bus.export_package_candidate(
            _required_text(params, "candidate_id"),
            stream,
        )
    if ctx.method == "export_package_candidate_file":
        return {"export": result, "artifact_path": str(target.resolve(strict=True))}
    return result
