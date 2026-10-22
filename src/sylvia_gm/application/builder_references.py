"""Capture and inspect exact content references for independent Builder conversations."""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sylvia_gm.application.bundled_campaign_plans import selected_campaign_plan
from sylvia_gm.application.bundled_packages import (
    bundled_rift_archive,
    bundled_rift_authority,
)
from sylvia_gm.application.bundled_starter_characters import bundled_starter_participants
from sylvia_gm.application.campaign_plan_sources import (
    campaign_plan_sources,
    validate_retained_campaign_plan,
)
from sylvia_gm.application.character_profiles import profile_from_snapshot
from sylvia_gm.application.compatible_character_copy import authored_character_state
from sylvia_gm.domain.authority import PackageRole, SafePayload, owned_payload
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import (
    PackageCandidate,
    campaign_setting_entries,
    canonical_json_bytes,
)
from sylvia_gm.domain.session import SessionContext, SessionMessageKind
from sylvia_gm.infrastructure.package_archive import (
    PACKAGE_MEMBER_BYTE_LIMIT,
    PACKAGE_MEMBER_COUNT_LIMIT,
)

if TYPE_CHECKING:
    from sylvia_gm.application.authority import AuthorityOwner


@dataclass(frozen=True)
class BuilderReferences:
    """A bounded source index and reader fixed to the chat's original content choices."""

    paths: tuple[str, ...]
    read: Callable[[str], str]


ReferenceSource = BuilderReferences | Callable[[], BuilderReferences | None]


def capture_launch_references(
    owner: AuthorityOwner,
    storage_root: Path,
    context: SessionContext,
    catalog: Sequence[SafePayload],
) -> SafePayload:
    """Resolve selected content once, retaining Plan text and immutable package identities."""
    states = [
        message.content
        for message in context.messages
        if message.kind is SessionMessageKind.EVIDENCE
        and message.content.get("kind") == "campaign_interview_state"
    ]
    answers = states[-1].get("answers", {}) if states else {}
    messages = {message.message_id: message for message in context.messages}

    def selection(step: str, key: str) -> object:
        message = messages.get(str(answers.get(step))) if isinstance(answers, Mapping) else None
        value = message.content.get("interview_value") if message is not None else None
        return value.get(key) if isinstance(value, Mapping) else None

    return capture_content_references(
        owner,
        storage_root,
        catalog,
        ruleset_id=selection("ruleset", "candidate_id"),
        setting_id=selection("setting", "setting_id"),
        setting_candidate_id=selection("setting", "candidate_id"),
        plan_id=selection("plan", "plan_id"),
    )


def capture_content_references(
    owner: AuthorityOwner,
    storage_root: Path,
    catalog: Sequence[SafePayload],
    *,
    ruleset_id: object,
    setting_id: object = None,
    setting_candidate_id: object = None,
    plan_id: object = None,
) -> SafePayload:
    """Resolve explicit content choices through the same snapshot checks as an Interview."""
    selected = next(
        (
            item
            for item in catalog
            if item.get("kind") == "ruleset"
            and (
                item.get("selection_id", item.get("id")) == ruleset_id
                or ruleset_id in item.get("selection_aliases", [])
            )
        ),
        None,
    )
    selected_setting = next(
        (
            item
            for item in (selected or {}).get("compatible_settings", ())
            if item.get("id") == setting_id
        ),
        None,
    )
    candidate = (
        owner.package_candidate(setting_candidate_id)
        if isinstance(setting_candidate_id, str)
        else owner.package_candidate(str(selected_setting["candidate_id"]))
        if selected_setting is not None and selected_setting.get("candidate_id")
        else None
    )
    if selected is None and isinstance(ruleset_id, str):
        try:
            ruleset_candidate = owner.package_candidate(ruleset_id)
        except CommandRejected as error:
            if error.code != "candidate_unavailable":
                raise
        else:
            primary = next(
                entry
                for entry in ruleset_candidate.package_lock.entries
                if entry.role is PackageRole.PRIMARY
            )
            selected = {
                "id": primary.package_id,
                "version": primary.version,
                "content_sha256": primary.sha256,
            }
            candidate = candidate or ruleset_candidate
    if (
        candidate is not None
        and selected is not None
        and not any(
            entry.role is PackageRole.PRIMARY and entry.package_id == selected["id"]
            for entry in candidate.package_lock.entries
        )
    ):
        raise CommandRejected("Selected Builder Setting belongs to another Ruleset")
    if selected is not None:
        identity = (selected["id"], selected["version"], selected.get("content_sha256"))
        candidate = candidate or next(
            (
                item
                for item in owner.package_candidates()
                if any(
                    entry.role is PackageRole.PRIMARY
                    and (entry.package_id, entry.version, entry.sha256) == identity
                    for entry in item.package_lock.entries
                )
                and (selected_setting is None or any(
                    (entry.package_id, entry.version, entry.sha256) == (
                        selected_setting["id"], selected_setting["version"],
                        selected_setting["content_sha256"],
                    ) for entry in campaign_setting_entries(item.package_lock)
                ))
            ),
            None,
        )
        if candidate is None:
            bundled = bundled_rift_authority().ruleset
            if identity == (bundled.package_id, bundled.version, bundled.content_sha256):
                archive = bundled_rift_archive()
                suffix = hashlib.sha256(archive).hexdigest()[:24]
                candidate = owner.install_package_archive(
                    io.BytesIO(archive), install_id=f"install-bundled-rift-{suffix}"
                )
            else:
                raise CommandRejected("Selected Builder Ruleset references are unavailable")
    elif ruleset_id is not None:
        raise CommandRejected("Selected Builder Ruleset is unavailable")

    plan = None
    selected_plan = next(
        (
            item
            for item in catalog
            if item.get("kind") == "campaign_plan" and item.get("id") == plan_id
        ),
        None,
    )
    if selected_plan is not None:
        if selected_plan.get("reusable") or selected_plan.get("bundled"):
            reusable = selected_campaign_plan(
                storage_root,
                str(plan_id),
                validate_plan=lambda payload, text: validate_retained_campaign_plan(
                    owner, payload, text
                ),
            )
            if reusable.content_sha256 != selected_plan.get("content_sha256"):
                raise CommandRejected("Selected Builder Campaign Plan reference changed")
            validated = reusable.plan
        else:
            reusable = selected_campaign_plan(storage_root, str(plan_id))
            validated = reusable.plan
            if validated.payload["id"] != plan_id:
                raise CommandRejected("Selected Builder Campaign Plan reference is unavailable")
        bindings = validated.payload["bindings"]
        # A retained Plan owns its exact Setting snapshot, including plan-specific
        # characters and secrets. The explicit Setting ID still must match below.
        selected_setting = bindings["campaign_setting"]
        if (
            selected is None
            or bindings["ruleset"]["id"] != selected["id"]
            or bindings["campaign_setting"]["id"] != setting_id
        ):
            raise CommandRejected("Selected Builder Campaign Plan does not belong to these sources")
        candidate = next(
            (
                item
                for item in owner.package_candidates()
                if item.closure_sha256 == bindings["package_closure_sha256"]
            ),
            None,
        )
        if candidate is None:
            archive = reusable.package_snapshot
            if archive is None:
                if bindings["package_closure_sha256"] != bundled_rift_authority().closure_sha256:
                    raise CommandRejected("Selected Builder Campaign Plan snapshot is unavailable")
                archive = bundled_rift_archive()
            suffix = hashlib.sha256(archive).hexdigest()[:24]
            candidate = owner.install_package_archive(
                io.BytesIO(archive), install_id=f"install-bundled-rift-{suffix}"
            )
        plan = {
            "id": validated.artifact_id,
            "selection_id": plan_id,
            "content_sha256": validated.content_sha256,
            "title": validated.title,
            "payload": validated.payload,
            "readable_markdown": validated.readable_markdown,
        }
    elif plan_id is not None:
        raise CommandRejected("Selected Builder Campaign Plan reference is unavailable")

    if setting_id is not None and (
        candidate is None
        or not any(
            entry.package_id == setting_id and (
                selected_setting is None or (entry.version, entry.sha256) == (
                    selected_setting["version"], selected_setting["content_sha256"],
                )
            )
            for entry in campaign_setting_entries(candidate.package_lock)
        )
    ):
        raise CommandRejected("Selected Builder Campaign Setting snapshot is unavailable")
    packages = []
    if candidate is not None:
        for entry in candidate.package_lock.entries:
            prefix = (
                "ruleset"
                if entry.role is PackageRole.PRIMARY
                else "setting"
                if entry.package_id == setting_id
                else f"dependencies/{entry.package_id}"
            )
            packages.append(
                {
                    "prefix": prefix,
                    "id": entry.package_id,
                    "version": entry.version,
                    "content_sha256": entry.sha256,
                }
            )
    return owned_payload(
        {
            "candidate_id": None if candidate is None else candidate.candidate_id,
            "packages": packages,
            "plan": plan,
        }
    )


def launch_references(context: SessionContext) -> SafePayload | None:
    """Read the saved reference snapshot without consulting the originating campaign."""
    for message in context.messages:
        if (
            message.kind is SessionMessageKind.EVIDENCE
            and message.content.get("kind") == "builder_launch_context"
        ):
            value = message.content.get("references")
            return value if isinstance(value, Mapping) else None
    for message in reversed(context.messages):
        if (
            message.kind is SessionMessageKind.EVIDENCE
            and message.content.get("kind") == "builder_tool_result"
            and message.content.get("tool_name") in {
                "select_builder_ruleset", "select_builder_setting",
            }
        ):
            result = message.content.get("result")
            if isinstance(result, Mapping) and result.get("success") is True:
                value = result.get("references")
                if isinstance(value, Mapping):
                    return value
    return None


def with_character_examples(
    owner: AuthorityOwner, storage_root: Path, references: SafePayload, *, purpose: str,
) -> SafePayload:
    """Snapshot compatible built-in authored examples, never the player's saved library."""
    candidate = candidate_for_references(owner, references)
    setting = next(
        (item for item in references.get("packages", ()) if item["prefix"] == "setting"), None,
    )
    if candidate is None or setting is None:
        return references
    sources = campaign_plan_sources(owner, candidate.package_lock, setting_id=setting["id"])
    if sources is None:
        return references
    primary = next(entry for entry in candidate.package_lock.entries
                   if entry.role is PackageRole.PRIMARY)
    examples = []
    for declaration in sources.character_declarations:
        if declaration.kind != purpose:
            continue
        for starter in bundled_starter_participants(
            storage_root, package=primary, declaration=declaration,
            campaign_setting=sources.setting,
            setting_character_schema=sources.setting_character_schema,
        ):
            draft = {
                "schema_id": declaration.schema_id, "summary": starter.summary,
                "authored_profile": starter.authored_profile, "participant": starter.participant,
                "entity_state": authored_character_state(starter.entity_state, declaration),
                "setting_data": starter.setting_data,
            }
            examples.append({
                "path": f"character-examples/{starter.artifact_id}.json", "name": starter.name,
                "summary": starter.summary, "schema_id": declaration.schema_id,
                "content_sha256": hashlib.sha256(canonical_json_bytes(draft)).hexdigest(),
                "draft": draft,
            })
    return owned_payload({**references, "character_examples": examples})


def launch_candidate(owner: AuthorityOwner, context: SessionContext) -> PackageCandidate | None:
    """Resolve the retained closure, checking its bytes against the original launch snapshot."""
    return candidate_for_references(owner, launch_references(context))


def candidate_for_references(
    owner: AuthorityOwner, references: SafePayload | None,
) -> PackageCandidate | None:
    """Validate captured package identities before using a selected source."""
    if references is None or references.get("candidate_id") is None:
        return None
    candidate = owner.package_candidate(str(references["candidate_id"]))
    retained = {
        (item["id"], item["version"], item["content_sha256"])
        for item in references.get("packages", [])
    }
    actual = {
        (entry.package_id, entry.version, entry.sha256) for entry in candidate.package_lock.entries
    }
    if retained != actual:
        raise CommandRejected("Saved Builder package references do not match the retained closure")
    return candidate


def reference_reader(owner: AuthorityOwner, context: SessionContext) -> BuilderReferences | None:
    """Expose only exact saved package files and the original Plan through portable paths."""
    return reader_for_references(owner, launch_references(context))


def reader_for_references(
    owner: AuthorityOwner, references: SafePayload | None,
) -> BuilderReferences | None:
    """Build a bounded reader for an already captured source snapshot."""
    if references is None:
        return None
    files: dict[str, Mapping] = {}
    for package in references.get("packages", []):
        for path in owner.package_file_paths(
            package["id"], package["version"], package["content_sha256"],
            maximum_files=PACKAGE_MEMBER_COUNT_LIMIT,
        ):
            files[f"{package['prefix']}/{path}"] = {**package, "path": path}
    plan = references.get("plan")
    plan_files = {}
    for example in references.get("character_examples", ()):
        plan_files[example["path"]] = json.dumps(
            json.loads(canonical_json_bytes(example["draft"])), indent=2, ensure_ascii=False,
        ) + "\n"
    profile_snapshot = references.get("character_profile")
    if profile_snapshot is not None:
        profile = profile_from_snapshot(profile_snapshot)
        plan_files["character-profile/profile.json"] = json.dumps(
            dict(profile.profile), ensure_ascii=False, indent=2,
        )
    if isinstance(plan, Mapping):
        plan_files.update({
            "campaign-plan/plan.json": json.dumps(
                json.loads(canonical_json_bytes(plan["payload"])), indent=2, ensure_ascii=False
            ),
            "campaign-plan/plan.md": str(plan["readable_markdown"]),
        })

    def read(path: str) -> str:
        if path in plan_files:
            return plan_files[path]
        package = files.get(path)
        if package is None:
            raise ValueError("Builder reference path is unavailable")
        return owner.package_text_file(
            package["id"],
            package["version"],
            package["content_sha256"],
            package["path"],
            maximum_bytes=PACKAGE_MEMBER_BYTE_LIMIT,
        )

    return BuilderReferences(tuple(sorted((*files, *plan_files))), read)
