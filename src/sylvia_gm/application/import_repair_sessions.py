"""Remember a failed file selection privately and open an independent repair chat on request."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from sylvia_gm.application.import_repair import inspect_repair_source, stage_repair_copy
from sylvia_gm.builder._package_materializer import _reject_reparse_chain
from sylvia_gm.domain.authority import SafePayload
from sylvia_gm.domain.errors import AuthorityError, CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.domain.session import BuilderChatRecord, SessionMessageKind

if TYPE_CHECKING:
    from sylvia_gm.application.authority import AuthorityOwner


def _check_private_path(storage_root: Path, target: Path) -> None:
    """Check existing ancestors before creating an owner-private receipt or workspace."""

    current = target
    while not current.exists() and not current.is_symlink() and current != storage_root:
        current = current.parent
    _reject_reparse_chain(storage_root, current)


def offer_import_repair(
    storage_root: Path,
    source: Path,
    *,
    install_id: str,
    error: Exception,
) -> SafePayload | None:
    """Offer only a safely readable archive; keep its machine path out of frontend and chat."""

    try:
        inspection = inspect_repair_source(source)
    except (AuthorityError, OSError, ValueError):
        return None
    repair_id = hashlib.sha256(
        canonical_json_bytes([install_id, inspection.source_sha256, str(source)])
    ).hexdigest()
    errors = [item.as_payload() for item in getattr(error, "diagnostics", ())[:16]]
    if not errors:
        errors = [item.as_payload() for item in inspection.diagnostics[:16]]
    record = {
        "source_path": str(source),
        "source_sha256": inspection.source_sha256,
        "kind": inspection.kind,
        "file_name": inspection.file_name,
        "message": str(error)[:1000],
        "validation_errors": errors,
    }
    root = storage_root / "import-repair-sources"
    target = root / f"{repair_id}.json"
    _check_private_path(storage_root, target)
    root.mkdir(parents=True, exist_ok=True)
    encoded = canonical_json_bytes(record)
    try:
        with target.open("xb") as output:
            output.write(encoded)
    except FileExistsError:
        if target.read_bytes() != encoded:
            raise CommandRejected(
                "The failed import receipt changed; select the file again"
            ) from None
    return {
        "status": "repairable",
        "repair_id": repair_id,
        "file_name": inspection.file_name,
        "message": str(error)[:1000],
        "validation_errors": errors,
    }


def start_import_repair_chat(
    owner: AuthorityOwner,
    storage_root: Path,
    *,
    repair_id: str,
    operation_id: str,
    provider_id: str,
    model_id: str,
) -> BuilderChatRecord:
    """Copy the selected source once; later turns use the saved chat and edited workspace."""

    if re.fullmatch(r"[0-9a-f]{64}", repair_id) is None:
        raise CommandRejected("The failed import is unavailable; select the file again")
    receipt = storage_root / "import-repair-sources" / f"{repair_id}.json"
    _reject_reparse_chain(storage_root, receipt)
    try:
        with receipt.open("rb") as source:
            raw = source.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            raise ValueError("oversized")
        record = json.loads(raw)
        if not isinstance(record, dict) or record.get("kind") not in {"package", "plan"}:
            raise ValueError("invalid")
    except (OSError, ValueError) as error:
        raise CommandRejected("The failed import is unavailable; select the file again") from error
    session_id = (
        "session-builder-"
        + hashlib.sha256(canonical_json_bytes([operation_id, repair_id])).hexdigest()[:24]
    )
    workspace = storage_root / "builder-chats" / session_id
    _check_private_path(storage_root, workspace)
    existing = next(
        (chat for chat in owner.builder_chat_index() if chat.session.session_id == session_id), None
    )
    evidence = {
        "kind": "import_repair_context",
        "repair_id": repair_id,
        "source_sha256": record["source_sha256"],
        "import_kind": record["kind"],
        "file_name": record["file_name"],
        "workspace_path": "repair-draft",
        "message": record["message"],
        "validation_errors": record["validation_errors"],
    }
    if existing is not None:
        context = owner.session_context(session_id)
        if any(message.content == evidence for message in context.messages):
            return existing
        raise CommandRejected("Repair chat source does not match this request")
    stage_repair_copy(
        Path(record["source_path"]),
        record["source_sha256"],
        workspace,
        expected_kind=record["kind"],
    )
    chat = owner.create_builder_chat(
        provider_id=provider_id,
        model_id=model_id,
        session_id=session_id,
        display_name=f"Repair {record['file_name']}"[:120],
    )
    owner.append_agent_message(
        session_id=session_id,
        message_id=f"repair-source-{repair_id[:24]}",
        kind=SessionMessageKind.EVIDENCE,
        content=evidence,
    )
    return chat
