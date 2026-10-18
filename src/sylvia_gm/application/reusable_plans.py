"""Small immutable local library for explicitly reviewed reusable Campaign Plans."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import cast

from sylvia_gm.application.bundled_packages import (
    bundled_rift_authority,
    bundled_rift_interview_contribution,
)
from sylvia_gm.application.plan_snapshot import (
    PLAN_SNAPSHOT_BYTE_LIMIT,
    PLAN_SNAPSHOT_FILENAME,
    validate_plan_snapshot,
)
from sylvia_gm.application.reusable_library_deletion import (
    delete_exact_library_entry,
    validate_owned_files,
    validated_library_entry,
)
from sylvia_gm.domain.authority import SafePayload
from sylvia_gm.domain.campaign_plan import ValidatedCampaignPlan, validate_campaign_plan
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.interview_contribution import parse_interview_contribution_bytes
from sylvia_gm.domain.package_library import canonical_json_bytes

REUSABLE_PLAN_FORMAT_ID = "sylvia-gm-reusable-campaign-plan"
REUSABLE_PLAN_FORMAT_VERSION = 3
_LEGACY_REUSABLE_PLAN_FORMAT_VERSION = 2
_PLAN_SNAPSHOT_NAME = PLAN_SNAPSHOT_FILENAME
_MAX_LIBRARY_PLANS = 256
_RIFT_SETUP_ID = "the-rift-awakening"
_LIBRARY_WRITE_LOCK = threading.Lock()

CampaignPlanValidator = Callable[[object, str], ValidatedCampaignPlan]


@dataclass(frozen=True)
class ReusableCampaignPlan:
    """One exact immutable reusable sibling and its player-safe catalog record."""

    artifact_id: str
    title: str
    summary: str
    content_sha256: str
    plan: ValidatedCampaignPlan
    interview_contribution: SafePayload | None
    setup_id: str | None
    package_snapshot: bytes | None = field(default=None, repr=False)

    def catalog_payload(self) -> SafePayload:
        bindings = cast(dict[str, object], self.plan.payload["bindings"])
        payload: dict[str, object] = {
            "kind": "campaign_plan",
            "id": self.artifact_id,
            "title": self.title,
            "summary": self.summary,
            "content_sha256": self.content_sha256,
            "reusable": True,
            "ruleset": cast(SafePayload, dict(cast(dict[str, object], bindings["ruleset"]))),
            "campaign_setting": cast(
                SafePayload,
                dict(cast(dict[str, object], bindings["campaign_setting"])),
            ),
            "starting_party": cast(
                SafePayload,
                json.loads(canonical_json_bytes(self.plan.payload["starting_party"])),
            ),
            # The Interview resolves questions from the selected saved content on demand.
            "interview_contribution": None,
        }
        if self.setup_id is not None:
            payload["setup_id"] = self.setup_id
        return payload


def _library_root(storage_root: Path) -> Path:
    return storage_root / "campaign-plans"


def _strip_section(markdown: str, heading: str) -> str:
    """Remove one known campaign-specific section without guessing through other prose."""

    pattern = re.compile(
        rf"\n+## {re.escape(heading)}\s*\n.*?(?=\n## |\Z)",
        flags=re.DOTALL,
    )
    return pattern.sub("", markdown).rstrip() + "\n"


def _generalized_rift_payload(
    payload: SafePayload, *, title: str, plan_id: str
) -> dict[str, object]:
    """Remove the known current-party and Interview overlays from one Rift-derived Plan."""

    value = json.loads(canonical_json_bytes(payload))
    if not isinstance(value, dict):
        raise ValueError("Campaign Plan payload is invalid")
    value["id"] = plan_id
    value["revision"] = 1
    value["title"] = title
    # Authored party policy and world state belong to this Plan; live party choices are separate.
    boundaries = value.get("boundaries")
    if isinstance(boundaries, list):
        value["boundaries"] = [
            item
            for item in boundaries
            if not isinstance(item, str) or not item.startswith("Player Boundary:")
        ]
    guidance = value.get("gm_guidance")
    if isinstance(guidance, dict) and isinstance(guidance.get("instructions"), str):
        guidance["instructions"] = "\n".join(
            line
            for line in guidance["instructions"].splitlines()
            if not line.startswith(
                (
                    "Player Preferences:",
                    "Player Specific Requests:",
                    "Accepted ruleset contribution:",
                    "Accepted setting contribution:",
                    "Accepted plan contribution:",
                )
            )
        ).strip()
    return value


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


def _validate_rift_plan(payload: object, readable_markdown: str) -> ValidatedCampaignPlan:
    """Validate a reusable Plan against the bundled Rift package closure."""

    authority = bundled_rift_authority()
    return validate_campaign_plan(
        payload,
        readable_markdown=readable_markdown,
        expected_ruleset=authority.ruleset,
        expected_setting=authority.setting,
        expected_closure_sha256=authority.closure_sha256,
        setting_paths=authority.setting_paths,
        setting_knowledge_ids=authority.setting_knowledge_ids,
    )


def _save_validated_campaign_plan(
    storage_root: Path,
    *,
    operation_id: str,
    source_payload: object,
    validated: ValidatedCampaignPlan,
    validate_plan: CampaignPlanValidator,
    setup_id: str | None,
    contribution_bytes: bytes = b"",
    package_snapshot: bytes | None = None,
) -> ReusableCampaignPlan:
    """Persist one validated immutable Plan with explicit catalog routing metadata."""

    if package_snapshot is not None:
        snapshot_validated = validate_plan_snapshot(
            package_snapshot,
            validated.payload,
            validated.readable_markdown,
        )
        if not isinstance(snapshot_validated, ValidatedCampaignPlan):
            raise ValueError("Reusable Campaign Plan snapshot validator returned an invalid result")
    content_sha256 = hashlib.sha256(
        canonical_json_bytes(validated.payload)
        + validated.readable_markdown.encode("utf-8")
        + contribution_bytes
        + (b"" if package_snapshot is None else package_snapshot)
    ).hexdigest()
    suffix = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
    artifact_id = f"campaign-plan-reusable-{suffix}-{content_sha256[:16]}"
    target = _library_root(storage_root) / artifact_id
    record = {
        "format_id": REUSABLE_PLAN_FORMAT_ID,
        "format_version": (
            _LEGACY_REUSABLE_PLAN_FORMAT_VERSION
            if package_snapshot is None
            else REUSABLE_PLAN_FORMAT_VERSION
        ),
        "artifact_id": artifact_id,
        "content_sha256": content_sha256,
        "plan_content_sha256": validated.content_sha256,
        "title": validated.title,
        "summary": str(validated.payload["premise"]),
        "source_plan_content_sha256": hashlib.sha256(
            canonical_json_bytes(source_payload)
        ).hexdigest(),
        "setup_id": setup_id,
    }
    if package_snapshot is not None:
        record["package_snapshot"] = _PLAN_SNAPSHOT_NAME
        record["package_snapshot_sha256"] = hashlib.sha256(package_snapshot).hexdigest()
    with _LIBRARY_WRITE_LOCK:
        if target.exists():
            existing = load_reusable_campaign_plan(
                storage_root,
                artifact_id,
                validate_plan=validate_plan,
            )
            if existing.content_sha256 != content_sha256:
                raise ValueError("Reusable Campaign Plan identity conflicts")
            return existing
        target.parent.mkdir(parents=True, exist_ok=True)
        # Catalog readers see either no entry or the complete immutable artifact.
        with tempfile.TemporaryDirectory(prefix=".plan-save-", dir=storage_root) as temporary:
            staged = Path(temporary) / artifact_id
            staged.mkdir()
            _atomic_write(staged / "plan.json", canonical_json_bytes(validated.payload))
            _atomic_write(staged / "plan.md", validated.readable_markdown.encode("utf-8"))
            if contribution_bytes:
                _atomic_write(staged / "interview-contribution.json", contribution_bytes)
            if package_snapshot is not None:
                _atomic_write(staged / _PLAN_SNAPSHOT_NAME, package_snapshot)
            _atomic_write(staged / "record.json", canonical_json_bytes(record))
            staged.rename(target)
        return load_reusable_campaign_plan(
            storage_root,
            artifact_id,
            validate_plan=validate_plan,
        )


def save_reusable_campaign_plan(
    storage_root: Path,
    *,
    operation_id: str,
    source_payload: object,
    source_markdown: str,
    validate_plan: CampaignPlanValidator,
    package_snapshot: bytes | None = None,
    setup_id: str | None = None,
    interview_contribution: SafePayload | None = None,
) -> ReusableCampaignPlan:
    """Persist an exact Plan admitted against its retained package context."""

    validated = validate_plan(source_payload, source_markdown)
    if not isinstance(validated, ValidatedCampaignPlan):
        raise ValueError("Reusable Campaign Plan validator returned an invalid result")
    contribution_bytes = b""
    if interview_contribution is not None:
        contribution = parse_interview_contribution_bytes(
            canonical_json_bytes(interview_contribution), expected_owner_kind="campaign_plan",
        )
        contribution_bytes = canonical_json_bytes(contribution.as_payload())
    return _save_validated_campaign_plan(
        storage_root,
        operation_id=operation_id,
        source_payload=source_payload,
        validated=validated,
        validate_plan=validate_plan,
        setup_id=setup_id,
        contribution_bytes=contribution_bytes,
        package_snapshot=package_snapshot,
    )


def save_reusable_rift_plan(
    storage_root: Path,
    *,
    operation_id: str,
    title: str,
    source_payload: SafePayload,
    source_markdown: str,
    package_snapshot: bytes | None = None,
) -> ReusableCampaignPlan:
    """Create one generalized immutable sibling from a validated Rift-derived Plan."""

    if (
        not isinstance(title, str)
        or title != title.strip()
        or not title
        or len(title) > 160
        or "\x00" in title
    ):
        raise ValueError("Reusable Campaign Plan title is invalid")
    suffix = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
    plan_id = f"reusable-{suffix}"
    payload = _generalized_rift_payload(source_payload, title=title, plan_id=plan_id)
    markdown = source_markdown.replace("\r\n", "\n").replace("\r", "\n")
    for heading in (
        "Selected campaign party",
        "Player customization brief",
        "Accepted Campaign Construction",
    ):
        markdown = _strip_section(markdown, heading)
    if markdown.lstrip().startswith("# "):
        markdown = re.sub(r"^# .*?$", f"# {title}", markdown, count=1, flags=re.MULTILINE)
    validator = (
        _validate_rift_plan if package_snapshot is None else
        lambda value, text: validate_plan_snapshot(package_snapshot, value, text)
    )
    validated = validator(payload, markdown)
    contribution = bundled_rift_interview_contribution()
    contribution_bytes = (
        b"" if contribution is None else canonical_json_bytes(contribution.as_payload())
    )
    return _save_validated_campaign_plan(
        storage_root,
        operation_id=operation_id,
        source_payload=source_payload,
        validated=validated,
        validate_plan=validator,
        setup_id=_RIFT_SETUP_ID,
        contribution_bytes=contribution_bytes,
        package_snapshot=package_snapshot,
    )


def load_reusable_campaign_plan(
    storage_root: Path,
    artifact_id: str,
    *,
    validate_plan: CampaignPlanValidator | None = None,
) -> ReusableCampaignPlan:
    """Load and revalidate one exact local reusable Plan without trusting directory contents."""

    if re.fullmatch(r"campaign-plan-reusable-[0-9a-f]{24}-[0-9a-f]{16}", artifact_id) is None:
        raise ValueError("Reusable Campaign Plan identity is invalid")
    root = validated_library_entry(
        storage_root, library_name="campaign-plans", artifact_id=artifact_id
    )
    if root is None:
        raise FileNotFoundError(artifact_id)
    all_owned_names = frozenset(
        {
            "plan.json",
            "plan.md",
            "interview-contribution.json",
            "record.json",
            _PLAN_SNAPSHOT_NAME,
        }
    )
    validate_owned_files(root, all_owned_names)
    record = json.loads((root / "record.json").read_text(encoding="utf-8"))
    common_fields = {
        "format_id",
        "format_version",
        "artifact_id",
        "content_sha256",
        "plan_content_sha256",
        "title",
        "summary",
        "source_plan_content_sha256",
    }
    if not isinstance(record, dict):
        raise ValueError("Reusable Campaign Plan record is invalid")
    format_version = record.get("format_version")
    if format_version == 1:
        if set(record) != common_fields:
            raise ValueError("Reusable Campaign Plan record is invalid")
        setup_id: str | None = _RIFT_SETUP_ID
    elif format_version == _LEGACY_REUSABLE_PLAN_FORMAT_VERSION:
        if set(record) != common_fields | {"setup_id"}:
            raise ValueError("Reusable Campaign Plan record is invalid")
        raw_setup_id = record["setup_id"]
        if raw_setup_id is not None and (
            not isinstance(raw_setup_id, str) or not raw_setup_id or "\x00" in raw_setup_id
        ):
            raise ValueError("Reusable Campaign Plan setup identity is invalid")
        setup_id = raw_setup_id
    elif format_version == REUSABLE_PLAN_FORMAT_VERSION:
        if set(record) != common_fields | {
            "setup_id",
            "package_snapshot",
            "package_snapshot_sha256",
        }:
            raise ValueError("Reusable Campaign Plan record is invalid")
        raw_setup_id = record["setup_id"]
        if raw_setup_id is not None and (
            not isinstance(raw_setup_id, str) or not raw_setup_id or "\x00" in raw_setup_id
        ):
            raise ValueError("Reusable Campaign Plan setup identity is invalid")
        setup_id = raw_setup_id
        if record["package_snapshot"] != _PLAN_SNAPSHOT_NAME:
            raise ValueError("Reusable Campaign Plan snapshot file is invalid")
        raw_snapshot_sha256 = record["package_snapshot_sha256"]
        if not isinstance(raw_snapshot_sha256, str) or re.fullmatch(
            r"[0-9a-f]{64}", raw_snapshot_sha256
        ) is None:
            raise ValueError("Reusable Campaign Plan snapshot hash is invalid")
    else:
        raise ValueError("Reusable Campaign Plan record is unsupported")
    if (
        record["format_id"] != REUSABLE_PLAN_FORMAT_ID
        or record["artifact_id"] != artifact_id
    ):
        raise ValueError("Reusable Campaign Plan record is unsupported")
    plan_bytes = (root / "plan.json").read_bytes()
    markdown = (root / "plan.md").read_bytes().decode("utf-8")
    contribution_path = root / "interview-contribution.json"
    contribution_bytes = contribution_path.read_bytes() if contribution_path.exists() else b""
    actual_names = frozenset(item.name for item in root.iterdir())
    required_names = {"plan.json", "plan.md", "record.json"}
    if contribution_path.exists():
        required_names.add("interview-contribution.json")
    package_snapshot: bytes | None = None
    if format_version == REUSABLE_PLAN_FORMAT_VERSION:
        required_names.add(_PLAN_SNAPSHOT_NAME)
        if actual_names != frozenset(required_names):
            raise ValueError("Reusable Campaign Plan files do not match its record")
        with (root / _PLAN_SNAPSHOT_NAME).open("rb") as snapshot_file:
            package_snapshot = snapshot_file.read(PLAN_SNAPSHOT_BYTE_LIMIT + 1)
        if len(package_snapshot) > PLAN_SNAPSHOT_BYTE_LIMIT:
            raise ValueError("Reusable Campaign Plan snapshot is too large")
        if hashlib.sha256(package_snapshot).hexdigest() != record["package_snapshot_sha256"]:
            raise ValueError("Reusable Campaign Plan snapshot hash is invalid")
        contained_validated = validate_plan_snapshot(
            package_snapshot,
            json.loads(plan_bytes),
            markdown,
        )
        validated = (
            contained_validated
            if validate_plan is None
            else validate_plan(json.loads(plan_bytes), markdown)
        )
    else:
        if actual_names != frozenset(required_names):
            raise ValueError("Reusable Campaign Plan files do not match its record")
        validator = _validate_rift_plan if validate_plan is None else validate_plan
        validated = validator(json.loads(plan_bytes), markdown)
    if not isinstance(validated, ValidatedCampaignPlan):
        raise ValueError("Reusable Campaign Plan validator returned an invalid result")
    if validated.content_sha256 != record["plan_content_sha256"]:
        raise ValueError("Reusable Campaign Plan structured hash is invalid")
    if record["title"] != validated.title or record["summary"] != validated.payload["premise"]:
        raise ValueError("Reusable Campaign Plan catalog metadata does not match its content")
    if setup_id is not None:
        if setup_id != _RIFT_SETUP_ID:
            raise ValueError("Reusable Campaign Plan setup is unsupported")
        bindings = validated.payload["bindings"]
        if (
            bindings["ruleset"]["id"] != "sylvia.srd-lite.bg3"
            or bindings["campaign_setting"]["id"] != "pneuma.severed-lands"
        ):
            raise ValueError("The Rift setup requires its own Ruleset and Setting")
    contribution = (
        None
        if not contribution_bytes
        else parse_interview_contribution_bytes(
            contribution_bytes,
            expected_owner_kind="campaign_plan",
        )
    )
    content_sha256 = hashlib.sha256(
        canonical_json_bytes(validated.payload)
        + validated.readable_markdown.encode("utf-8")
        + (b"" if contribution is None else canonical_json_bytes(contribution.as_payload()))
        + (b"" if package_snapshot is None else package_snapshot)
    ).hexdigest()
    if content_sha256 != record["content_sha256"]:
        raise ValueError("Reusable Campaign Plan content hash is invalid")
    return ReusableCampaignPlan(
        artifact_id=artifact_id,
        title=str(record["title"]),
        summary=str(record["summary"]),
        content_sha256=content_sha256,
        plan=validated,
        interview_contribution=(None if contribution is None else contribution.as_payload()),
        setup_id=setup_id,
        package_snapshot=package_snapshot,
    )


def reusable_campaign_plan_catalog(
    storage_root: Path,
    *,
    validate_plan: CampaignPlanValidator | None = None,
    on_invalid: Callable[[str, Exception], None] | None = None,
) -> tuple[ReusableCampaignPlan, ...]:
    """List valid Plans, reporting each rejected entry when discovery supplies a handler."""

    root = _library_root(storage_root)
    if not root.exists():
        return ()
    directories = tuple(path for path in root.iterdir() if path.is_dir())
    if len(directories) > _MAX_LIBRARY_PLANS:
        raise ValueError("Reusable Campaign Plan library is oversized")
    plans = []
    for path in sorted(directories, key=lambda item: item.name):
        try:
            plan = load_reusable_campaign_plan(
                storage_root, path.name, validate_plan=validate_plan,
            )
        except (OSError, UnicodeError, ValueError, CommandRejected) as error:
            if on_invalid is None:
                raise
            on_invalid(path.name, error)
            continue
        # A catalog retains metadata, not another archive copy for every saved Plan.
        plans.append(replace(plan, package_snapshot=None))
    return tuple(plans)


def delete_reusable_campaign_plan(
    storage_root: Path,
    artifact_id: str,
    *,
    expected_content_sha256: str,
    confirmation: str,
) -> bool:
    """Delete one exact local Plan after fencing against a stale catalog choice."""

    if re.fullmatch(r"campaign-plan-reusable-[0-9a-f]{24}-[0-9a-f]{16}", artifact_id) is None:
        raise ValueError("Reusable Campaign Plan identity is invalid")
    if re.fullmatch(r"[0-9a-f]{64}", expected_content_sha256) is None:
        raise ValueError("Reusable Campaign Plan version is invalid")
    if confirmation != f"permanently delete {artifact_id}":
        raise ValueError("Reusable Campaign Plan deletion confirmation is not exact")
    with _LIBRARY_WRITE_LOCK:
        target = validated_library_entry(
            storage_root, library_name="campaign-plans", artifact_id=artifact_id
        )
        if target is None:
            return False
        owned_names = frozenset(
            {
                "plan.json",
                "plan.md",
                "interview-contribution.json",
                "record.json",
                _PLAN_SNAPSHOT_NAME,
            }
        )
        validate_owned_files(target, owned_names)
        record_path = target / "record.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("artifact_id") != artifact_id:
            raise ValueError("Reusable Campaign Plan record is invalid")
        if record.get("content_sha256") != expected_content_sha256:
            raise ValueError("Reusable Campaign Plan changed; refresh the library and try again")
        return delete_exact_library_entry(
            storage_root,
            library_name="campaign-plans",
            artifact_id=artifact_id,
            owned_names=owned_names,
        )
