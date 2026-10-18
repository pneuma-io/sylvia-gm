"""Declarative selection and materialization for The Rift Awakening campaign."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any

from sylvia_gm.application.bundled_packages import bundled_rift_campaign_plan
from sylvia_gm.application.campaign_starting_world import materialize_starting_world
from sylvia_gm.application.initial_character_state import verify_initial_character_state
from sylvia_gm.application.reusable_participants import ReusableParticipant
from sylvia_gm.application.schema_admission import validate_parameters
from sylvia_gm.application.setting_character_requirements import validate_setting_character_data
from sylvia_gm.domain.authority import (
    PackageLockEntry,
    Participant,
    ParticipantValidationStatus,
    SafePayload,
    StateValue,
)
from sylvia_gm.domain.campaign_plan import (
    StartingPartyPolicy,
    ValidatedCampaignPlan,
    parse_starting_party_policy,
)
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import ParticipantArtifactDeclaration

RIFT_TEMPLATE_ID = "the-rift-awakening"
RIFT_RULESET_ID = "sylvia.srd-lite.bg3"
RIFT_RULESET_COMPANION_MAXIMUM = 5


@dataclass(frozen=True)
class RiftSelection:
    """One validated Rift setup ready for ordinary campaign construction."""

    participants: tuple[Participant, ...]
    initial_state: tuple[StateValue, ...]
    canonical_input: SafePayload
    campaign_plan: SafePayload
    campaign_plan_markdown: str


def validate_rift_party_handoff(
    selection: RiftSelection,
    starting_party_policy: Mapping[str, object],
) -> None:
    """Recheck the final materialized roster immediately before owner handoff."""

    policy = parse_starting_party_policy(starting_party_policy)
    players = [
        participant
        for participant in selection.participants
        if participant.data.get("party_role") == "player_character"
    ]
    companions = [
        participant
        for participant in selection.participants
        if participant.data.get("party_role") == "companion"
    ]
    selected_party = selection.canonical_input.get("selected_party", [])
    if not isinstance(selected_party, list):
        raise CommandRejected("The materialized Rift party record is invalid")
    required_count = sum(
        1
        for item in selected_party
        if isinstance(item, Mapping)
        and item.get("party_role") == "companion"
        and item.get("required") is True
    )
    custom_count = len(companions) - required_count
    if (
        len(players) != 1
        or len(companions) > RIFT_RULESET_COMPANION_MAXIMUM
        or required_count != len(policy.required_companions)
        or not policy.custom_companion_minimum <= custom_count <= policy.custom_companion_maximum
    ):
        raise CommandRejected(
            "The final party does not satisfy the Campaign Plan starting-party policy",
            code="campaign_plan_party_handoff_invalid",
        )


@lru_cache(maxsize=1)
def _template() -> dict[str, Any]:
    raw = (
        files("sylvia_gm.campaign").joinpath("the-rift-awakening.json").read_text(encoding="utf-8")
    )
    value = json.loads(raw)
    return validate_rift_template(value)


def validate_rift_template(value: object) -> dict[str, Any]:
    """Validate an authored Rift party template loaded from the selected Setting copy."""
    required = {
        "format",
        "template_id",
        "title",
        "description",
        "player_archetypes",
        "companions",
        "encounter_entities",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("The Rift template has an invalid top-level shape")
    if value["format"] != 2 or value["template_id"] != RIFT_TEMPLATE_ID:
        raise ValueError("The Rift template identity is invalid")
    archetypes = value["player_archetypes"]
    companions = value["companions"]
    if (
        not isinstance(archetypes, list)
        or not archetypes
        or not isinstance(companions, list)
        or len(companions) > 5
    ):
        raise ValueError("The Rift template content is invalid")
    for collection, label in ((archetypes, "archetype"), (companions, "companion")):
        identities = [item.get("id") for item in collection if isinstance(item, dict)]
        if len(identities) != len(collection) or len(set(identities)) != len(identities):
            raise ValueError(f"The Rift {label} identities are invalid")
    return value


def rift_public_template(
    *, reusable_participants: tuple[ReusableParticipant, ...] = (),
    template: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return path-free setup metadata safe for the desktop renderer."""

    value = _template() if template is None else validate_rift_template(template)
    local_players = [
        participant.catalog_payload()
        for participant in reusable_participants
        if participant.role == "player_character"
    ]
    local_companions = [
        participant.catalog_payload()
        for participant in reusable_participants
        if participant.role == "companion"
    ]
    return {
        "template_id": value["template_id"],
        "title": value["title"],
        "description": value["description"],
        "player_archetypes": [
            {
                "id": item["id"],
                "name": item["participant"]["name"],
                "label": item["label"],
                "summary": item["summary"],
                "source_ref": item.get("source_ref"),
            }
            for item in value["player_archetypes"]
        ]
        + [
            {
                "id": item["id"],
                "name": item["name"],
                "label": item["name"],
                "summary": item["summary"],
                "reusable": True,
                "profile_status": item["profile_status"],
                "campaign_ready": item["profile_status"] == "authored",
                **(
                    {"source_profile": item["source_profile"]} if item.get("source_profile") else {}
                ),
            }
            for item in local_players
        ],
        "companions": [
            {
                "id": item["id"],
                "name": item["name"],
                "summary": item["summary"],
                "source_ref": item.get("source_ref"),
            }
            for item in value["companions"]
        ]
        + [
            {
                "id": item["id"],
                "name": item["name"],
                "summary": item["summary"],
                "reusable": True,
                "profile_status": item["profile_status"],
                "campaign_ready": item["profile_status"] == "authored",
                **(
                    {"source_profile": item["source_profile"]} if item.get("source_profile") else {}
                ),
            }
            for item in local_companions
        ],
    }


def _selected_record(collection: list[dict[str, Any]], identity: str, label: str) -> dict[str, Any]:
    selected = next((item for item in collection if item["id"] == identity), None)
    if selected is None:
        raise CommandRejected(f"The Rift {label} selection is unavailable")
    return selected


def _character_file_record(source: Mapping[str, object]) -> dict[str, object]:
    """Preserve an exact Character File source without exposing it in the setup catalog."""

    source_id = source.get("id")
    authored_profile = source.get("authored_profile")
    profile_status = source.get("profile_status", "sparse")
    if (
        not isinstance(source_id, str)
        or not source_id
        or profile_status not in {"authored", "sparse"}
        or (profile_status == "authored" and not isinstance(authored_profile, str))
        or (profile_status == "sparse" and authored_profile is not None)
    ):
        raise RuntimeError("The Rift Character File source is invalid")
    return {
        "source_id": source_id,
        "profile_status": profile_status,
        "authored_profile": authored_profile,
        "setting_data": source.get("setting_data", {}),
    }


def _require_authored_reusable_profile(source: Mapping[str, object]) -> None:
    """Keep an incomplete legacy Character File out of a newly constructed campaign."""

    if source.get("reusable") is not True or source.get("profile_status") == "authored":
        return
    name = str(source.get("name") or "This character")
    raise CommandRejected(
        f"{name}'s Character File needs an authored profile before it can join a new campaign. "
        "Complete the Character File or choose someone else.",
        code="character_file_profile_required",
    )


def _record_for_plan_reference(
    collection: list[dict[str, Any]],
    *,
    identity: str,
    path: str,
    label: str,
) -> dict[str, Any]:
    """Resolve one exact Setting reference without treating reusable artifacts as aliases."""

    selected = next(
        (
            item
            for item in collection
            if isinstance(item.get("source_ref"), Mapping)
            and item["source_ref"].get("id") == identity
            and item["source_ref"].get("path") == path
        ),
        None,
    )
    if selected is None:
        raise CommandRejected(f"The Campaign Plan's fixed {label} is unavailable")
    return selected


def _validated_party_selection(
    *,
    policy: StartingPartyPolicy,
    archetypes: list[dict[str, Any]],
    companions: list[dict[str, Any]],
    archetype_id: str,
    companion_ids: tuple[str, ...],
) -> tuple[dict[str, Any], list[dict[str, Any]], set[str]]:
    """Apply the Plan policy inside the Ruleset's admitted Rift party envelope."""

    if (
        len(policy.required_companions) + policy.custom_companion_maximum
        > RIFT_RULESET_COMPANION_MAXIMUM
    ):
        raise CommandRejected(
            "The Campaign Plan starting party exceeds the Ruleset companion envelope",
            code="campaign_plan_party_exceeds_ruleset",
        )
    if policy.player_character_mode == "fixed":
        fixed = policy.fixed_player_character
        if fixed is None:  # pragma: no cover - parsed policy invariant
            raise CommandRejected("The Campaign Plan fixed player character is invalid")
        archetype = _record_for_plan_reference(
            archetypes,
            identity=fixed.character_id,
            path=fixed.path,
            label="player character",
        )
        if archetype_id != archetype["id"]:
            raise CommandRejected(
                "The Campaign Plan requires its fixed player character",
                code="campaign_plan_fixed_player_required",
            )
    else:
        archetype = _selected_record(archetypes, archetype_id, "archetype")

    required_records = [
        _record_for_plan_reference(
            companions,
            identity=reference.character_id,
            path=reference.path,
            label="companion",
        )
        for reference in policy.required_companions
    ]
    required_ids = {str(item["id"]) for item in required_records}
    selected_ids = set(companion_ids)
    if not required_ids <= selected_ids:
        raise CommandRejected(
            "The Campaign Plan requires every mandatory companion",
            code="campaign_plan_required_companion_missing",
        )
    custom_count = len(selected_ids - required_ids)
    if not policy.custom_companion_minimum <= custom_count <= policy.custom_companion_maximum:
        raise CommandRejected(
            "The selected custom companion count is outside the Campaign Plan policy",
            code="campaign_plan_custom_companion_count_invalid",
        )
    return (
        archetype,
        [_selected_record(companions, identity, "Companion") for identity in companion_ids],
        required_ids,
    )


def _participant(
    *,
    participant_id: str,
    party_role: str,
    source: dict[str, Any],
    package: PackageLockEntry,
    declaration: ParticipantArtifactDeclaration,
) -> tuple[Participant, dict[str, Any]]:
    data = copy.deepcopy(source["participant"])
    state = copy.deepcopy(source["entity_state"])
    if not isinstance(data, dict) or not isinstance(state, dict):
        raise RuntimeError("The Rift participant declaration is invalid")
    data["party_role"] = party_role
    validate_parameters(data, declaration.schema)
    if declaration.entity_state_schema is not None:
        state = dict(verify_initial_character_state(
            data, state,
            entity_state_schema=declaration.entity_state_schema,
            initial_state_calculations=declaration.initial_state_calculations,
            initial_state_validation=declaration.initial_state_validation,
            setting_data=source.get("setting_data", {}),
        ))
    return (
        Participant(
            participant_id=participant_id,
            kind=declaration.kind,
            package_id=package.package_id,
            package_version=package.version,
            data=data,
            package_sha256=package.sha256,
            schema_id=declaration.schema_id,
            schema_sha256=declaration.schema_sha256,
            validation_status=ParticipantValidationStatus.VALIDATED,
        ),
        state,
    )


def materialize_rift_selection(
    *,
    participant_namespace: str,
    lane: str,
    archetype_id: str,
    companion_ids: tuple[str, ...],
    customization_notes: str,
    package: PackageLockEntry,
    participant_declaration: ParticipantArtifactDeclaration,
    starting_party_policy: Mapping[str, object] | StartingPartyPolicy | None = None,
    reusable_participants: tuple[ReusableParticipant, ...] = (),
    template: dict[str, Any] | None = None,
    selected_plan: ValidatedCampaignPlan | None = None,
    setting_character_schema: SafePayload | None = None,
) -> RiftSelection:
    """Validate setup choices and render package-bound participants plus the authored plan."""

    if len(participant_namespace) != 24 or any(
        character not in "0123456789abcdef" for character in participant_namespace
    ):
        raise CommandRejected("The Rift participant namespace is invalid")
    if lane not in {"quick_start", "customize"}:
        raise CommandRejected("The Rift construction lane is unsupported")
    if package.package_id != RIFT_RULESET_ID or participant_declaration.kind != "player_character":
        raise CommandRejected("The Rift requires the bundled D&D Player Character schema")
    if len(companion_ids) > RIFT_RULESET_COMPANION_MAXIMUM or len(set(companion_ids)) != len(
        companion_ids
    ):
        raise CommandRejected("The Rift accepts zero to five unique Companions")
    if not isinstance(customization_notes, str) or len(customization_notes) > 4_000:
        raise CommandRejected("The Rift customization notes are invalid")
    if lane == "quick_start" and customization_notes.strip():
        raise CommandRejected("Quick Start cannot rewrite the authored campaign plan")

    value = _template() if template is None else validate_rift_template(template)
    local_archetypes = [
        participant.rift_source()
        for participant in reusable_participants
        if participant.role == "player_character"
    ]
    local_companions = [
        participant.rift_source()
        for participant in reusable_participants
        if participant.role == "companion"
    ]
    archetypes = [*value["player_archetypes"], *local_archetypes]
    companions = [*value["companions"], *local_companions]
    if len({str(item["id"]) for item in archetypes}) != len(archetypes) or len(
        {str(item["id"]) for item in companions}
    ) != len(companions):
        raise CommandRejected("The Rift participant library has conflicting identities")
    policy = (
        starting_party_policy
        if isinstance(starting_party_policy, StartingPartyPolicy)
        else parse_starting_party_policy(
            starting_party_policy
            if starting_party_policy is not None
            else {
                "player_character": {"mode": "compatible"},
                "required_companions": [],
                "custom_companions": {
                    "minimum": 0,
                    "maximum": RIFT_RULESET_COMPANION_MAXIMUM,
                },
            }
        )
    )
    archetype, selected_companions, required_companion_ids = _validated_party_selection(
        policy=policy,
        archetypes=archetypes,
        companions=companions,
        archetype_id=archetype_id,
        companion_ids=companion_ids,
    )
    _require_authored_reusable_profile(archetype)
    validate_setting_character_data(
        archetype.get("setting_data", {}), schema=setting_character_schema
    )
    for selected_companion in selected_companions:
        _require_authored_reusable_profile(selected_companion)
        validate_setting_character_data(
            selected_companion.get("setting_data", {}), schema=setting_character_schema
        )
    player, player_state = _participant(
        participant_id=f"rift-{participant_namespace}-player-character",
        party_role="player_character",
        source=archetype,
        package=package,
        declaration=participant_declaration,
    )
    player_name = str(player.data["name"])
    participants = [player]
    entities: dict[str, Any] = {player.participant_id: player_state}
    for companion in selected_companions:
        participant_id = f"rift-{participant_namespace}-companion-{companion['id']}"
        participant, state = _participant(
            participant_id=participant_id,
            party_role="companion",
            source=companion,
            package=package,
            declaration=participant_declaration,
        )
        participants.append(participant)
        entities[participant_id] = state
    entities.update(copy.deepcopy(value["encounter_entities"]))

    plan_artifact = bundled_rift_campaign_plan() if selected_plan is None else selected_plan
    plan = plan_artifact.readable_markdown
    roster = ", ".join([player_name, *(str(item["name"]) for item in selected_companions)])
    plan += f"\n\n## Selected campaign party\n\n{roster}\n"
    if lane == "customize" and customization_notes.strip():
        plan += "\n\n## Player customization brief\n\n" + customization_notes.strip() + "\n"

    canonical_input: dict[str, Any] = {
        "template_id": RIFT_TEMPLATE_ID,
        "template_version": value["format"],
        "player_character": {"name": player_name, "archetype_id": archetype_id},
        "companion_ids": list(companion_ids),
        "starting_party_policy": policy.as_payload(),
        "selected_party": [
            {
                "participant_id": player.participant_id,
                "party_role": "player_character",
                "name": player_name,
                "summary": str(archetype["summary"]),
                "character_file": _character_file_record(archetype),
            },
            *[
                {
                    "participant_id": participant.participant_id,
                    "party_role": "companion",
                    "name": str(companion["name"]),
                    "summary": str(companion["summary"]),
                    "character_file": _character_file_record(companion),
                    **(
                        {"required": True} if str(companion["id"]) in required_companion_ids else {}
                    ),
                }
                for participant, companion in zip(
                    participants[1:], selected_companions, strict=True
                )
            ],
        ],
    }
    if lane == "customize" and customization_notes.strip():
        canonical_input["customization_notes"] = customization_notes.strip()
    selection = RiftSelection(
        participants=tuple(participants),
        initial_state=materialize_starting_world(
            plan_artifact,
            entity_reference={"namespace": RIFT_RULESET_ID, "key": "entities"},
            party_entities=entities,
        ),
        canonical_input=canonical_input,
        campaign_plan=plan_artifact.payload,
        campaign_plan_markdown=plan,
    )
    validate_rift_party_handoff(selection, policy.as_payload())
    return selection
