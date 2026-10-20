"""Resolve saved campaign choices against their exact installed source packages."""

from __future__ import annotations

from sylvia_gm.application.launch_compatibility import require_active_ruleset

import hashlib
import io
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from sylvia_gm.application.bundled_campaign_plans import selected_campaign_plan
from sylvia_gm.application.bundled_starter_characters import available_reusable_participants
from sylvia_gm.application.campaign_plan_sources import (
    CampaignPlanToolAuthority,
    campaign_plan_sources,
    validate_retained_campaign_plan,
)
from sylvia_gm.application.campaign_starting_world import materialize_starting_world
from sylvia_gm.application.initial_character_state import (
    initialize_campaign_character_state, verify_placed_character,
)
from sylvia_gm.application.character_rules import campaign_character_state
from sylvia_gm.application.package_runtime import (
    PACKAGE_RUNTIME_BYTE_LIMIT,
    PACKAGE_RUNTIME_PATH,
    package_model_decision_boundary,
)
from sylvia_gm.application.reusable_participants import (
    ReusableParticipant,
    parse_setting_character,
)
from sylvia_gm.application.reusable_plans import ReusableCampaignPlan
from sylvia_gm.domain.authority import (
    JsonValue,
    PackageLockEntry,
    PackageRole,
    Participant,
    ParticipantValidationStatus,
    SafePayload,
    StateValue,
)
from sylvia_gm.domain.campaign_plan import (
    CampaignPlanCharacterReference,
    StartingPartyPolicy,
    ValidatedCampaignPlan,
    parse_starting_party_policy,
)
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import (
    PackageCandidate,
    ParticipantArtifactDeclaration,
    canonical_json_bytes,
)

if TYPE_CHECKING:
    from sylvia_gm.application.authority import AuthorityOwner


def _character_state_reference(
    owner: AuthorityOwner, package_id: str, version: str, sha256: str,
) -> Mapping[str, object] | None:
    """Read the exact Ruleset location used by both readiness and party materialization."""
    paths = owner.package_file_paths(package_id, version, sha256)
    if PACKAGE_RUNTIME_PATH not in paths:
        return None
    runtime = owner.package_text_file(
        package_id, version, sha256, PACKAGE_RUNTIME_PATH,
        maximum_bytes=PACKAGE_RUNTIME_BYTE_LIMIT,
    )
    boundary = package_model_decision_boundary(
        package_id=package_id, runtime_bytes=runtime.encode("utf-8"),
    )
    reference = boundary.get("actor_entities") if boundary is not None else None
    return reference if isinstance(reference, Mapping) else None


def _participant_choice(
    item: ReusableParticipant, entity_reference: Mapping[str, object] | None,
) -> SafePayload:
    """Keep reusable-save receipts and campaign choices honest about playable state."""
    row = dict(item.catalog_payload())
    missing_location = bool(item.entity_state) and entity_reference is None
    row["campaign_ready"] = item.authored_profile is not None and not missing_location
    if missing_location:
        row["unavailable_reason"] = "This Ruleset has no declared character state location."
    return row


def participant_catalog_payload(owner: AuthorityOwner, item: ReusableParticipant) -> SafePayload:
    """Project a saved Character's readiness against its immutable Ruleset, without a Plan."""
    reference = _character_state_reference(
        owner, item.package_id, item.package_version, item.package_sha256,
    )
    return _participant_choice(item, reference)


@dataclass(frozen=True)
class CampaignLibrarySelection:
    """Validated sources shared by party discovery and the final campaign handoff."""

    candidate: PackageCandidate
    sources: CampaignPlanToolAuthority
    plan: ReusableCampaignPlan
    participants: tuple[ReusableParticipant, ...]
    entity_reference: Mapping[str, object] | None
    required_participants: tuple[ReusableParticipant, ...] = ()

    def party_options(self) -> SafePayload:
        """Expose compatible authored files and explain unavailable required Setting files."""
        rows: dict[str, list[dict[str, object]]] = {"player_character": [], "companion": []}
        def row_for(item: ReusableParticipant) -> dict[str, object]:
            row = dict(_participant_choice(item, self.entity_reference))
            row["label"] = item.name
            if item.builtin_starter:
                row["starter_preview"] = {
                    "authored_profile": item.authored_profile,
                    "participant": json.loads(canonical_json_bytes(item.participant)),
                    "entity_state": json.loads(canonical_json_bytes(item.entity_state)),
                }
            return row

        policy = parse_starting_party_policy(self.plan.plan.payload["starting_party"])
        for item in self.participants:
            if item.role == "player_character" and policy.fixed_player_character is not None:
                continue
            rows[item.role].append(row_for(item))
        available = {item.artifact_id: item for item in self.required_participants}
        for role, reference in _required_character_references(policy):
            item = available.get(reference.character_id)
            row = (
                row_for(item) if item is not None else {
                    "id": reference.character_id,
                    "label": reference.character_id,
                    "name": reference.character_id,
                    "summary": "The required Setting character has no playable artifact available.",
                    "profile_status": "unavailable",
                    "campaign_ready": False,
                }
            )
            row.update(
                source_ref={"id": reference.character_id, "path": reference.path},
                reusable=False,
            )
            rows[role].append(row)
        return cast(
            SafePayload,
            {
                "template_id": self.plan.artifact_id,
                "title": self.plan.title,
                "description": self.plan.summary,
                "starting_party": self.plan.plan.payload["starting_party"],
                "player_archetypes": rows["player_character"],
                "companions": rows["companion"],
            },
        )


def _required_character_references(
    policy: StartingPartyPolicy,
) -> tuple[tuple[str, CampaignPlanCharacterReference], ...]:
    """Keep the Plan's exact files and party roles together during resolution and selection."""
    references = [("companion", ref) for ref in policy.required_companions]
    if policy.fixed_player_character is not None:
        references.append(("player_character", policy.fixed_player_character))
    return tuple(references)


def read_required_plan_characters(
    sources: CampaignPlanToolAuthority,
    policy: StartingPartyPolicy,
    primary: PackageLockEntry,
    declarations: tuple[ParticipantArtifactDeclaration, ...],
    *,
    require_complete: bool = False,
) -> tuple[ReusableParticipant, ...]:
    """Admit complete Setting files against the selected Ruleset's participant schemas."""
    references = _required_character_references(policy)
    identities = [ref.character_id for _, ref in references]
    if len(set(identities)) != len(identities):
        raise ValueError("Required Setting character identities must be distinct")
    participants = []
    for role, reference in references:
        problem = "No compatible participant schema is declared"
        validation_errors = ()
        try:
            source = sources.read_setting(reference.path)
        except (OSError, UnicodeError, ValueError, CommandRejected) as error:
            if require_complete:
                raise CommandRejected(
                    f"Required {role} {reference.character_id} at {reference.path} is unavailable"
                ) from error
            continue
        for declaration in declarations:
            try:
                participant = parse_setting_character(
                    source,
                    character_id=reference.character_id,
                    role=role,
                    package=primary,
                    declaration=declaration,
                    campaign_setting=sources.setting,
                    setting_character_schema=sources.setting_character_schema,
                )
            except (ValueError, CommandRejected) as error:
                problem = str(error)
                validation_errors = getattr(error, "diagnostics", ())
                continue
            participants.append(participant)
            break
        else:
            if require_complete:
                raise CommandRejected(
                    f"Required {role} {reference.character_id} at {reference.path}: {problem}",
                    diagnostics=validation_errors,
                )
    return tuple(participants)


def require_completed_plan_characters(
    owner: AuthorityOwner, candidate_id: str, plan: ValidatedCampaignPlan,
) -> None:
    """Require usable fixed/required files when Builder explicitly completes the Plan's party."""
    candidate = owner.package_candidate(candidate_id)
    sources = campaign_plan_sources(
        owner, candidate.package_lock,
        setting_id=str(plan.payload["bindings"]["campaign_setting"]["id"]),
    )
    if (
        sources is None
        or sources.closure_sha256 != plan.payload["bindings"]["package_closure_sha256"]
    ):
        raise CommandRejected("Required Character Files need the Plan's exact package snapshot")
    primary = next(
        entry for entry in candidate.package_lock.entries if entry.role is PackageRole.PRIMARY
    )
    read_required_plan_characters(
        sources, parse_starting_party_policy(plan.payload["starting_party"]), primary,
        owner.exact_participant_declarations(primary.package_id, primary.version, primary.sha256),
        require_complete=True,
    )


def resolve_campaign_library_selection(
    owner: AuthorityOwner,
    storage_root: Path,
    *,
    candidate_id: str,
    setting_id: str,
    plan_id: str,
) -> CampaignLibrarySelection:
    """Revalidate saved Plan and participant identities; picker metadata grants no authority."""
    retained = selected_campaign_plan(
        storage_root,
        plan_id,
        validate_plan=lambda payload, text: validate_retained_campaign_plan(owner, payload, text),
    )
    bindings = retained.plan.payload["bindings"]
    try:
        selected = owner.package_candidate(candidate_id)
    except CommandRejected as error:
        if error.code != "candidate_unavailable":
            raise
        selected_ruleset_id = candidate_id
    else:
        selected_ruleset_id = next(
            entry.package_id for entry in selected.package_lock.entries
            if entry.role is PackageRole.PRIMARY
        )
    require_active_ruleset(selected_ruleset_id)
    require_active_ruleset(str(bindings["ruleset"]["id"]))
    if (
        selected_ruleset_id != bindings["ruleset"]["id"]
        or setting_id != bindings["campaign_setting"]["id"]
    ):
        raise CommandRejected("The selected Ruleset or Setting does not match this saved Plan")
    # A saved Plan keeps its inherited bytes even when the source library has newer versions.
    candidate = next(
        (item for item in owner.package_candidates()
         if item.closure_sha256 == bindings["package_closure_sha256"]),
        None,
    )
    if candidate is None and retained.package_snapshot is not None:
        candidate = owner.install_package_archive(
            io.BytesIO(retained.package_snapshot),
            install_id=f"plan-snapshot-{hashlib.sha256(retained.package_snapshot).hexdigest()[:24]}",
        )
    if candidate is None:
        raise CommandRejected("The selected Campaign Plan snapshot is unavailable")
    candidate_id = candidate.candidate_id
    sources = campaign_plan_sources(owner, candidate.package_lock, setting_id=setting_id)
    if sources is None:
        raise CommandRejected("A saved Campaign Plan requires its installed Ruleset and Setting")
    try:
        plan = retained
        primary = next(
            entry for entry in candidate.package_lock.entries if entry.role is PackageRole.PRIMARY
        )
        declarations = owner.exact_participant_declarations(
            primary.package_id, primary.version, primary.sha256
        )
        participants = tuple(
            item
            for declaration in declarations
            for item in available_reusable_participants(
                storage_root,
                package=primary,
                declaration=declaration,
                campaign_setting=sources.setting,
                setting_character_schema=sources.setting_character_schema,
            )
        )
        required_participants = read_required_plan_characters(
            sources,
            parse_starting_party_policy(plan.plan.payload["starting_party"]),
            primary,
            declarations,
        )
        if {item.artifact_id for item in participants} & {
            item.artifact_id for item in required_participants
        }:
            raise ValueError("Setting and local Character File identities conflict")
        reference = _character_state_reference(
            owner, primary.package_id, primary.version, primary.sha256,
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise CommandRejected(
            "The selected campaign content is unavailable or incompatible; refresh its choices",
            code="campaign_library_selection_invalid",
        ) from error
    return CampaignLibrarySelection(
        candidate,
        sources,
        plan,
        participants,
        reference,
        required_participants,
    )


def materialize_library_party(
    owner: AuthorityOwner,
    selection: CampaignLibrarySelection,
    *,
    operation_id: str,
    player_character_id: str,
    companion_ids: tuple[str, ...],
) -> tuple[tuple[Participant, ...], tuple[StateValue, ...], list[dict[str, object]]]:
    """Copy selected characters into the Plan's authored world without replacing its NPCs."""
    policy = parse_starting_party_policy(selection.plan.plan.payload["starting_party"])
    required_artifacts = {item.artifact_id: item for item in selection.required_participants}
    if any(
        ref.character_id not in required_artifacts
        for _, ref in _required_character_references(policy)
    ):
        raise CommandRejected("A required Setting character has no playable artifact available")
    if (
        policy.fixed_player_character is not None
        and player_character_id != policy.fixed_player_character.character_id
    ):
        raise CommandRejected("This Plan requires its fixed Player Character")
    required_companions = {ref.character_id for ref in policy.required_companions}
    custom_companions = set(companion_ids) - required_companions
    if (
        len(set(companion_ids)) != len(companion_ids)
        or not required_companions <= set(companion_ids)
        or not policy.custom_companion_minimum
        <= len(custom_companions)
        <= policy.custom_companion_maximum
    ):
        raise CommandRejected(
            "The selected Companions do not meet this Plan's starting party policy"
        )
    artifacts = {item.artifact_id: item for item in selection.participants}
    artifacts.update(required_artifacts)
    declarations = {
        item.schema_id: item
        for item in owner.exact_participant_declarations(
            selection.sources.ruleset.package_id,
            selection.sources.ruleset.version,
            selection.sources.ruleset.content_sha256,
        )
    }
    participants: list[Participant] = []
    entities: dict[str, JsonValue] = {}
    selected_party: list[dict[str, object]] = []
    for artifact_id, role in [
        (player_character_id, "player_character"),
        *[(item, "companion") for item in companion_ids],
    ]:
        item = artifacts.get(artifact_id)
        if item is None or item.role != role or item.authored_profile is None:
            raise CommandRejected("A selected character is unavailable, unfinished or incompatible")
        if item.entity_state and selection.entity_reference is None:
            raise CommandRejected("This Ruleset has no declared character state location")
        participant_id = (
            "participant-"
            + hashlib.sha256(f"{operation_id}\0{artifact_id}".encode()).hexdigest()[:24]
        )
        declaration = declarations[item.schema_id]
        participants.append(
            Participant(
                participant_id=participant_id,
                kind=declaration.kind,
                package_id=item.package_id,
                package_version=item.package_version,
                data=item.participant,
                package_sha256=item.package_sha256,
                schema_id=item.schema_id,
                schema_sha256=item.schema_sha256,
                validation_status=ParticipantValidationStatus.VALIDATED,
            )
        )
        entities[participant_id] = cast(JsonValue, dict(item.entity_state))
        selected_party.append(
            {
                "participant_id": participant_id,
                "party_role": role,
                "name": item.name,
                "summary": item.summary,
                "character_file": {
                    "source_id": item.artifact_id,
                    "content_sha256": item.content_sha256,
                    "profile_status": item.profile_status,
                    "authored_profile": item.authored_profile,
                    "setting_data": item.setting_data,
                },
            }
        )
        if item.source_migration is not None:
            selected_party[-1]["character_file"]["source_migration"] = item.source_migration
    state = materialize_starting_world(
        selection.plan.plan,
        entity_reference=selection.entity_reference,
        party_entities=entities,
        party_names={str(item["participant_id"]): str(item["name"]) for item in selected_party},
    )
    # Placements preserve injuries but must retain the exact Ruleset's character contract.
    if selection.entity_reference is not None:
        reference = selection.entity_reference
        parts = str(reference["key"]).split(".")
        mutable_state = {(value.namespace, value.key): json.loads(canonical_json_bytes(value.value))
                         for value in state}
        by_entity = mutable_state[(reference["namespace"], parts[0])]
        for part in parts[1:]:
            by_entity = by_entity[part]
        for participant, selected in zip(participants, selected_party, strict=True):
            artifact = artifacts[selected["character_file"]["source_id"]]
            by_entity[participant.participant_id] = initialize_campaign_character_state(
                participant.data, by_entity[participant.participant_id],
                declarations[participant.schema_id],
                campaign_character_state(selection.plan.plan.payload, participant.package_id),
                artifact.setting_data,
            )
            verify_placed_character(participant.data, by_entity[participant.participant_id],
                                    declarations[participant.schema_id], artifact.setting_data)
        state = tuple(StateValue(namespace, key, value)
                      for (namespace, key), value in mutable_state.items())
    placed_payload = json.loads(canonical_json_bytes(selection.plan.plan.payload))
    placed_payload["initial_state"]["mechanics"] = [
        {"namespace": value.namespace, "key": value.key,
         "value": json.loads(canonical_json_bytes(value.value))} for value in state
    ]
    selection.sources.validate(placed_payload, selection.plan.plan.readable_markdown)
    return tuple(participants), state, selected_party


def plan_with_interview_intent(
    selection: CampaignLibrarySelection,
    intent: SafePayload,
) -> ValidatedCampaignPlan:
    """Retain the selected story and add the player's accepted campaign instructions."""
    payload = json.loads(canonical_json_bytes(selection.plan.plan.payload))
    lines: list[str] = []
    for key, label in (
        ("boundaries", "Hard Boundaries"),
        ("preferences", "Preferences"),
        ("specific_requests", "Specific Requests"),
    ):
        value = intent.get(key, "")
        if not isinstance(value, str) or len(value) > 131_072:
            raise CommandRejected("Campaign instructions must be bounded text")
        if value.strip():
            lines.append(f"{label}: {value.strip()}")
            if key == "boundaries":
                payload["boundaries"].append(f"Player Boundary: {value.strip()}")
            else:
                payload["gm_guidance"]["instructions"] += f"\nPlayer {label}: {value.strip()}"
    contributions = intent.get("contributions", {})
    if not isinstance(contributions, Mapping):
        raise CommandRejected("Campaign contributions must be an object")
    for step, answer in sorted(contributions.items()):
        if not isinstance(answer, Mapping) or not isinstance(answer.get("answers"), Mapping):
            continue
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(answer["answers"].items()))
        if rendered:
            line = f"Accepted {step.replace('_', ' ')}: {rendered}"
            lines.append(line)
            payload["gm_guidance"]["instructions"] += f"\n{line}"
    markdown = selection.plan.plan.readable_markdown
    if lines:
        markdown += (
            "\n\n## Accepted Campaign Construction\n\n"
            + "\n".join(f"- {line}" for line in lines)
            + "\n"
        )
    return selection.sources.validate(payload, markdown)
