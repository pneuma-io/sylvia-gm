"""Stateless MCP 2026-07-28 boundary for model-facing mechanics.

Every list and call creates a fresh official SDK server/client pair.  The MCP
handler retains no campaign, TURN, permission, catalog, idempotency, or
conversation state: all authority is supplied on the request and revalidated
against the sole :class:`AuthorityOwner` for that request.
"""

from __future__ import annotations

import asyncio
import atexit
import hashlib
import json
import re
import threading
import uuid
from collections.abc import Coroutine, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar, cast

from mcp import types
from mcp.client import Client
from mcp.server import Server
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError

from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import (
    ActionReceipt,
    ActionStatus,
    AwaitingChoiceReceipt,
    MechanicsChoiceOption,
    MechanicsChoiceRequest,
    ModelToolBehavior,
    SafePayload,
    WriterRole,
)
from sylvia_gm.domain.errors import (
    AuthorityError,
    CommandRejected,
    IdempotencyConflict,
    MechanicsRejected,
    MechanicsValidationIssue,
    RulesetPreconditionRejected,
    StaleRevision,
    WrongOwner,
    WrongWriter,
)
from sylvia_gm.domain.inventory import inventory_update_schema

MCP_PROTOCOL_VERSION = "2026-07-28"
MCP_SDK_VERSION = "2.0.0"
_SERVER_NAME = "sylvia-gm-mechanics"
_KNOWLEDGE_SEARCH_TOOL_NAME = "search_campaign_knowledge"
_REFERENCE_TOOL_NAME = "read_campaign_reference"
_CAMPAIGN_FILE_UPDATE_TOOL_NAME = "update_campaign_file"
_CAMPAIGN_FILE_STATE_CHANGE = "state_change"
_CAMPAIGN_FILE_STATE_CHANGE_SUMMARY = "state_change_summary"
_REFERENCE_PACKAGE_ID = "sylvia.application"
_NARRATIVE_TOOL_NAMES = {
    "record_memory": "record_campaign_memory",
    "record_entity": "record_campaign_entity",
    "update_scene": "update_campaign_scene",
    "update_plan_progress": "update_campaign_plan_progress",
    "reveal_secret": "reveal_campaign_secret",
}
_NARRATIVE_OPERATIONS_BY_TOOL = {
    tool_name: operation for operation, tool_name in _NARRATIVE_TOOL_NAMES.items()
}
_CAMPAIGN_FILE_COLLECTIONS = (
    ("record_entity", "entities"),
    ("record_memory", "facts"),
    ("update_scene", "scenes"),
    ("reveal_secret", "reveals"),
    ("update_plan_progress", "plan_progress"),
    ("update_character", "character_updates"),
    ("update_inventory", "inventory_updates"),
)
_META_PREFIX = "sylvia_gm/"
_SESSION_KEYS = frozenset({"session_id", "mcp_session_id", "Mcp-Session-Id"})
_T = TypeVar("_T")
_SYNC_LOOP = asyncio.new_event_loop()
_SYNC_LOOP_LOCK = threading.RLock()


def _close_sync_loop() -> None:
    if not _SYNC_LOOP.is_closed() and not _SYNC_LOOP.is_running():
        _SYNC_LOOP.close()


atexit.register(_close_sync_loop)


@dataclass(frozen=True, slots=True)
class ModelMechanicsTool:
    """Package-bound tool projection safe to advertise to a model provider."""

    package_id: str
    package_version: str
    package_sha256: str
    name: str
    capability: str
    input_schema: Mapping[str, object]
    parameter_derivations: Mapping[str, Mapping[str, object]] | None = None
    description: str = ""
    category: str = "mechanic"
    model_behavior: ModelToolBehavior = ModelToolBehavior.OPTIONAL


ACTION_SEQUENCE_TOOL_NAMES = frozenset({
    "read_action_sequence_controls", "read_action_sequence",
    "compose_action_sequence", "execute_action_sequence",
})


def _sequence_selection_contract(
    surface: Mapping[str, object], action: Mapping[str, object],
) -> dict[str, object]:
    """Describe existing raw input requirements without granting control or legality."""

    bindings = action.get("bindings")
    if not isinstance(bindings, Mapping):
        return {"required_keys": [], "context": {}}
    keys = {value for value in bindings.values() if isinstance(value, str)}
    context: dict[str, object] = {}
    if surface.get("renderer") in {"tactical_grid", "coordinate_map"}:
        keys.discard("path")  # The authoritative owner derives paths from raw intent.
        keys.add("map_revision")
        data = surface.get("data")
        if isinstance(data, Mapping) and "map_revision" in data:
            context["map_revision"] = data["map_revision"]
    elif surface.get("renderer") == "network_graph":
        keys.add("graph_revision")
        data = surface.get("data")
        if isinstance(data, Mapping) and "graph_revision" in data:
            context["graph_revision"] = data["graph_revision"]
    return {"required_keys": sorted(keys), "context": context}


def action_sequence_model_tools(owner: AuthorityOwner) -> tuple[ModelMechanicsTool, ...]:
    """Advertise the application queue only when its authoritative service is available."""

    if not all(callable(getattr(owner, name, None)) for name in (
        "action_sequence", "campaign_panels", "_compose_action_sequence_from_agent",
        "_execute_action_sequence_from_agent",
    )):
        return ()
    identity = {"type": "string", "minLength": 1, "maxLength": 256}
    version = {"type": "integer", "minimum": 0}
    source = {
        "type": "object", "properties": {
            "kind": {"type": "string", "enum": ["panel", "result_card"]},
            "surface_id": identity, "action_id": identity,
            "button_index": {"type": "integer", "minimum": 0, "maximum": 7},
        }, "required": ["kind", "action_id"], "additionalProperties": False,
        "description": (
            "An exact declared panel source, or an exact saved result-card button source."
        ),
    }
    specifications = (
        ("read_action_sequence_controls", "reference", {}, [],
         "READ-ONLY: discover the campaign's actual declared panel sources, bindings, selection "
         "data and current player-control scope. Each selection_contract lists the exact required "
         "selection keys and current map_revision or graph_revision context values. "
         "Copy context values exactly and fill other keys from player intent. "
         "A displayed actor is never a control grant. "
         "Manual busy flags remain truthful while this GM operation owns work; "
         "the shared queue "
         "service separately validates the owning operation and each actor."),
        ("read_action_sequence", "reference", {"sequence_id": identity}, [],
         "READ-ONLY: inspect one saved executable action queue, or the latest queue when its ID "
         "is omitted. Pending intentions differ from committed receipts awaiting narration. "
         "Use the saved version and exact committed action IDs; never recreate a committed step."),
        ("compose_action_sequence", "action_sequence", {
            "sequence_id": identity, "expected_revision": version, "expected_version": version,
            "steps": {"type": "array", "minItems": 0, "maxItems": 32, "items": {
                "type": "object", "properties": {
                    "step_id": identity, "source": source,
                    "selection": {
                        "type": ["object", "null"], "additionalProperties": True,
                        "description": (
                            "Use exactly the discovered control's "
                            "selection_contract.required_keys; "
                            "copy its context values, including map_revision, without alteration. "
                            "Fill remaining keys from player intent. "
                            "Never supply an owner-derived path."
                        ),
                    },
                }, "required": ["step_id", "source", "selection"], "additionalProperties": False,
            }},
        }, ["sequence_id", "expected_revision", "steps"],
         "Compose or edit ordered intentions using only exact sources discovered by "
         "read_action_sequence_controls, including each control's exact selection_contract keys "
         "and context values. The same authoritative player controls validate them. "
         "Editing spends no resources and cannot undo committed mechanics. Preserve every existing "
         "committed step. An empty intention list removes only pending intentions. Later steps are "
         "revalidated after earlier outcomes and may become illegal. expected_version defaults to "
         "zero for a new queue. Composition returns the exact queue "
         "and confirmation for inspection."),
        ("execute_action_sequence", "action_sequence", {
            "sequence_id": identity, "expected_version": version,
            "confirmation": {"type": "string", "minLength": 1, "maxLength": 256},
        }, ["sequence_id", "expected_version"],
         "Execute the inspected saved queue through the same authoritative action pipeline. "
         "When the player explicitly requested that exact sequence, use the confirmation returned "
         "by compose/read; otherwise inspect and obtain the player's decision before executing. "
         "Only currently controlled, eligible actors may act. Stop on failed, stale or paused "
         "results; do not resume remaining intentions without a fresh player direction. Never "
         "implicitly End Turn, expand actor control, reroll or repeat a committed step. Narrate "
         "the committed outcomes together afterward through the ordinary "
         "Campaign File disposition."),
    )
    return tuple(ModelMechanicsTool(
        package_id=_REFERENCE_PACKAGE_ID, package_version="1", package_sha256="0" * 64,
        name=name, capability=f"action_sequence.{name}", category=category,
        description=description,
        input_schema={"type": "object", "properties": properties,
                      "required": required, "additionalProperties": False},
    ) for name, category, properties, required, description in specifications)


def call_action_sequence_tool(
    owner: AuthorityOwner, *, campaign_id: str, session_id: str,
    name: str, parameters: Mapping[str, object],
) -> dict[str, object]:
    (
        "Dispatch queue metadata to the owning GM's shared application service, "
        "never raw mechanics."
    )

    from sylvia_gm.application.player_actions import player_control_origin
    from sylvia_gm.application.schema_admission import validate_parameters

    tool = next((row for row in action_sequence_model_tools(owner) if row.name == name), None)
    if tool is None:
        raise CommandRejected(
            "Action sequence operation is unavailable", code="action_sequence_unavailable"
        )
    validate_parameters(cast(SafePayload, parameters), tool.input_schema)
    context = owner.session_context(session_id)
    if context.session.campaign_id != campaign_id or context.session.role is not WriterRole.DM:
        raise CommandRejected("Action sequence belongs to another GM campaign")
    if name == "read_action_sequence_controls":
        panels = owner.campaign_panels(campaign_id)
        surfaces = cast(Sequence[Mapping[str, object]], panels.get("surfaces", ()))
        return cast(dict[str, object], _plain_json({
            "kind": "action_sequence_controls", "campaign_id": campaign_id,
            "revision": panels["revision"], "player_control": panels.get("player_control"),
            "direct_actions_available": panels.get("direct_actions_available", False),
            "controls": [
                {"source": {
                    "kind": "panel", "surface_id": surface["id"], "action_id": action["id"],
                },
                 "declaration": action,
                 "selection_contract": _sequence_selection_contract(surface, action)}
                for surface in surfaces
                for action in cast(Sequence[Mapping[str, object]], surface.get("actions", ()))
            ],
            "surfaces": surfaces,
        }))
    if name == "read_action_sequence":
        result = owner.action_sequence(
            session_id=session_id, sequence_id=parameters.get("sequence_id")
        )
    elif name == "compose_action_sequence":
        for step in cast(Sequence[Mapping[str, object]], parameters["steps"]):
            player_control_origin(cast(SafePayload, step["source"]),
                expected_revision=cast(int, parameters["expected_revision"]),
                selection=cast(SafePayload, step["selection"]), session_id=session_id)
        result = owner._compose_action_sequence_from_agent(
            campaign_id=campaign_id, session_id=session_id,
            sequence_id=parameters["sequence_id"],
            expected_revision=parameters["expected_revision"],
            expected_version=parameters.get("expected_version", 0), steps=parameters["steps"],
        )
    else:
        result = owner._execute_action_sequence_from_agent(
            campaign_id=campaign_id, session_id=session_id,
            sequence_id=parameters["sequence_id"], expected_version=parameters["expected_version"],
            confirmation=parameters.get("confirmation"),
        )
    return cast(dict[str, object], _plain_json({
        "kind": "action_sequence_result", "operation": name, "result": result,
    }))


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain_json(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise CommandRejected("MCP payload is not JSON-safe", code="invalid_mcp_payload")


def _string_sequence(value: object) -> tuple[str, ...]:
    """Read one canonical internal string sequence without weakening its type."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise CommandRejected("Campaign narrative options are invalid")
    if not all(isinstance(item, str) for item in value):
        raise CommandRejected("Campaign narrative options are invalid")
    return tuple(cast(str, item) for item in value)


def _model_input_schema(
    schema: Mapping[str, object],
    *,
    participant_ids: Sequence[str],
) -> tuple[dict[str, object], dict[str, Mapping[str, object]]]:
    """Project one executable model contract and its authority-owned argument derivations."""

    projected = cast(dict[str, object], _plain_json(schema))
    raw_properties = projected.get("properties")
    if not isinstance(raw_properties, Mapping):
        raise CommandRejected("Catalog input schema properties are invalid")
    properties: dict[str, object] = {}
    derivations: dict[str, Mapping[str, object]] = {}
    model_required: set[str] = set()
    for raw_name, raw_schema in raw_properties.items():
        name = str(raw_name)
        if not isinstance(raw_schema, Mapping):
            raise CommandRejected("Catalog input schema property is invalid")
        child = cast(dict[str, object], _plain_json(raw_schema))
        derivation = child.pop("x-sylvia-gm-derived", None)
        hidden = child.pop("x-sylvia-gm-model-hidden", False)
        required_for_model = child.pop("x-sylvia-gm-model-required", False)
        if derivation is not None:
            if not isinstance(derivation, Mapping):
                raise CommandRejected("Catalog model derivation is invalid")
            derivations[name] = cast(Mapping[str, object], _plain_json(derivation))
            continue
        if hidden is True:
            continue
        if required_for_model is True:
            model_required.add(name)
        if child.get("x-sylvia-gm-reference") == "participant":
            child["enum"] = list(participant_ids)
        properties[name] = child
    raw_required = projected.get("required", ())
    required = (
        {str(name) for name in raw_required if str(name) in properties}
        if isinstance(raw_required, Sequence)
        and not isinstance(raw_required, (str, bytes, bytearray))
        else set()
    )
    projected["properties"] = properties
    projected["required"] = sorted(required | model_required)
    return projected, derivations


def _narrative_input_schema(
    operation: str, options: Mapping[str, object]
) -> dict[str, object]:
    """Describe one exact Campaign File write without a provider-hostile schema union."""

    if operation == "update_character":
        return cast(dict[str, object], _plain_json(options["character_update_schema"]))
    if operation == "update_inventory":
        return inventory_update_schema()
    properties: dict[str, object]
    required: list[str]
    if operation == "record_memory":
        properties = {
                "fact_id": {
                    "type": "string",
                    "description": (
                        "A stable identifier for this lasting campaign fact. Reuse it when a "
                        "plan is completed, an obligation fulfilled, or this truth corrected. "
                        "Write the complete current truth, including the outcome; earlier versions "
                        "remain in history. If separate records already describe the old plan and "
                        "its outcome, update the old plan record to say it is completed or "
                        "superseded instead of leaving it as a current instruction."
                    ),
                },
                "category": {
                    "type": "string",
                    "enum": [
                        "decision",
                        "relationship",
                        "discovery",
                        "promise",
                        "consequence",
                        "continuity",
                    ],
                    "description": "The kind of lasting fact established by play.",
                },
                "text": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 2000,
                    "description": (
                        "The concise campaign-specific truth to remember (1-2000 characters). "
                        "Keep separate lasting facts in separate records."
                    ),
                },
                "visibility": {
                    "type": "string",
                    "enum": ["player_known", "gm_private"],
                    "description": (
                        "Knowledge boundary for the entire fact as written. Use player_known only "
                        "when released play, player-authored text, or prior player-known Campaign "
                        "State establishes every detail; otherwise use gm_private."
                    ),
                },
                "known_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Exact identities that have actually learned this truth in the fiction. "
                        "Do not include the player merely because the GM knows it or plans a "
                        "future reveal."
                    ),
                },
                "subjects": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional stable entity identifiers this fact is about.",
                },
        }
        required = ["category", "fact_id", "known_by", "text", "visibility"]
    elif operation == "record_entity":
        properties = {
                "entity_id": {
                    "type": "string",
                    "description": (
                        "Stable campaign identity. Reuse it when the same person, place, creature, "
                        "or object changes later."
                    ),
                },
                "entity_kind": {
                    "type": "string",
                    "description": "Plain entity kind, such as person, place, creature, or object.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 300,
                    "description": (
                        "Current name, 1-300 characters, appropriate to this record's visibility. "
                        "An unrevealed true "
                        "name from Plan or Setting preparation belongs in a gm_private entity "
                        "record, not a player_known one."
                    ),
                },
                "attributes": {
                    "type": "object",
                    "description": (
                        "Current campaign-specific facts, such as location, condition, allegiance, "
                        "or status. Include the complete current values that should remain true. "
                        "For a player character, never infer posture, alertness, equipment use, or "
                        "another action from inventory alone."
                    ),
                },
                "visibility": {
                    "type": "string",
                    "enum": ["player_known", "gm_private"],
                    "description": (
                        "Knowledge boundary for the entire entity record, including its name and "
                        "attributes. Use player_known only when every included detail has been "
                        "established for the player; otherwise use gm_private."
                    ),
                },
                "known_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Exact identities that have actually learned this entity state in the "
                        "fiction. Do not include the player merely because the GM knows it or "
                        "plans a future reveal."
                    ),
                },
                "source_ref": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "Optional admitted Setting or participant source path for this identity. "
                        "Omit when there is no admitted source; never send an empty string."
                    ),
                },
        }
        required = [
            "attributes",
            "entity_id",
            "entity_kind",
            "known_by",
            "name",
            "visibility",
        ]
    elif operation == "update_scene":
        properties = {
                "summary": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 2000,
                    "description": (
                        "The concise current scene situation established by play "
                        "(1-2000 characters). Never add a "
                        "player action or equipment posture that player text, the participant "
                        "artifact, or existing Campaign State did not establish."
                    ),
                },
                "location_id": {
                    "type": "string",
                    "description": "Optional stable current location identifier.",
                },
        }
        scene_act_ids = list(_string_sequence(options.get("scene_act_ids", ())))
        if scene_act_ids:
            properties["act_id"] = {
                "type": "string",
                "enum": scene_act_ids,
                "description": (
                    "Optional Campaign Plan preparation item associated with what play reached."
                ),
            }
        required = ["summary"]
    elif operation == "update_plan_progress":
        properties = {
                "plan_item_id": {
                    "type": "string",
                    "description": "Exact private Campaign Plan item identity from this enum.",
                },
                "progress_status": {
                    "type": "string",
                    "enum": ["introduced", "engaged", "deferred", "resolved", "retired"],
                    "description": (
                        "What play has actually done with this prepared item, never what the Plan "
                        "hoped would happen. 'Introduced' means the GM made it available while "
                        "the player's response remains unresolved; 'engaged' requires a player "
                        "choice that interacts with it; 'deferred' means the player clearly "
                        "declined, avoided, or left the introduced material behind. Deferred "
                        "material remains possible but is not a current obligation. 'Resolved' "
                        "means play has met all authored completion conditions and the closing "
                        "event, including an explicitly allowed failure outcome. An act requires "
                        "all of its linked objectives; side work cannot replace them. 'Retired' "
                        "means play has made the item inapplicable, not that the act was "
                        "completed. "
                        "For ending:<id>, read the final act/structured/ending and resolve only "
                        "after required acts/objectives and the selected immediate aftermath. "
                        "This opens the player's continue/finish card; leave the after-story "
                        "for their explicit Finish choice. "
                        "Plan Progress "
                        "is a bookmark, not a turn log: omit it when the status and practical "
                        "meaning have not changed."
                    ),
                },
                "progress_summary": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 2000,
                    "description": (
                        "Short private reason, 1-2000 characters, grounded only in what play "
                        "established. Describe "
                        "what was offered or changed, and record an express player choice when it "
                        "changes the status. Refresh a superseded summary when the outcome, "
                        "evidence, or remaining obligation changes even if the status stays "
                        "engaged. Do not rewrite an existing summary merely to mention "
                        "the current turn when its status and practical meaning are unchanged. "
                        "Otherwise leave the player's response unresolved. "
                        "Never infer the player's interest, intent, acceptance, route, or next "
                        "action. Do not describe the player as weighing, considering, interested "
                        "in, or drawn to an option unless player text establishes it; use neutral "
                        "no-choice-yet wording instead."
                    ),
                },
        }
        plan_item_ids = list(_string_sequence(options.get("plan_item_ids", ())))
        if plan_item_ids:
            cast(dict[str, object], properties["plan_item_id"])["enum"] = plan_item_ids
        required = ["plan_item_id", "progress_status", "progress_summary"]
    elif operation == "reveal_secret":
        properties = {
                "secret_id": {
                    "type": "string",
                    "enum": list(_string_sequence(options.get("eligible_secret_ids", ()))),
                    "description": "An established secret currently eligible to be revealed.",
                },
                "audience": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Exact identities that learned the secret in play.",
                },
        }
        required = ["audience", "secret_id"]
    else:
        raise CommandRejected(
            "Campaign narrative operation is unsupported",
            code="unsupported_campaign_narrative_operation",
        )
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _narrative_tool_description(operation: str) -> str:
    """Explain one narrow Campaign File write while preserving the GM's fictional freedom."""

    duties = {
        "record_memory": (
            "remember a lasting decision, discovery, promise, relationship, consequence, or "
            "continuity fact established by play"
        ),
        "record_entity": (
            "create or update the current campaign-specific state of a person, place, creature, "
            "or object"
        ),
        "update_scene": "record the current scene or location after play changes it",
        "update_plan_progress": (
            "record what play actually did with one prepared Campaign Plan item; this observes "
            "progress and never forces the item to occur"
        ),
        "reveal_secret": "record an established secret that events have just revealed",
    }
    return (
        f"Before player-facing prose, {duties[operation]}. This writes the campaign's durable "
        "GM-screen memory. Do not rely on recent dialogue as long-term memory. It records what "
        "play established and does not choose what happens in the fiction. Record the resulting "
        "world, not a transcript of player input: an unfulfilled player request or attempt is not "
        "a substitute for the GM-owned outcome the response will establish. Use Ruleset tools, "
        "not this write, to resolve mechanics."
    )


def _campaign_file_update_schema(options: Mapping[str, object]) -> dict[str, object]:
    """Expose one deep batch interface over the available GM-owned note types."""

    available = set(_string_sequence(options.get("operations", ())))
    properties: dict[str, object] = {
        _CAMPAIGN_FILE_STATE_CHANGE: {
            "type": "string",
            "enum": ["changed", "unchanged"],
            "description": (
                "After you compare the complete response with pre-action Campaign State, declare "
                "whether any durable truth changed. Use 'changed' when any supplied collection "
                "records new post-action truth, otherwise use 'unchanged'."
            ),
        },
        _CAMPAIGN_FILE_STATE_CHANGE_SUMMARY: {
            "type": "string",
            "minLength": 1,
            "maxLength": 1000,
            "description": (
                "Brief private accounting of what durably changed, or why the response leaves "
                "Campaign State exactly unchanged. This assessment is audited but is not itself "
                "stored as campaign truth. Maximum 1000 characters, including spaces and newlines. "
                "Aim for one or two short sentences well below the maximum; put full facts in "
                "their own records instead of repeating them here."
            ),
        },
    }
    for operation, collection_name in _CAMPAIGN_FILE_COLLECTIONS:
        if operation not in available:
            continue
        properties[collection_name] = {
            "type": "array",
            "minItems": 1,
            "maxItems": 64,
            "items": _narrative_input_schema(operation, options),
            "description": (
                "Every change of this kind established by the current GM response. Omit this "
                "collection when none changed."
            ),
        }
    return {
        "type": "object",
        "properties": properties,
        "required": [_CAMPAIGN_FILE_STATE_CHANGE, _CAMPAIGN_FILE_STATE_CHANGE_SUMMARY],
        "additionalProperties": False,
    }


def _json_type_name(value: object) -> str:
    """Return a JSON-schema-style type name without exposing the submitted value."""

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "array"
    return "unknown"


def _parent_path(path: str) -> str:
    """Identify the object or collection that must directly contain one field."""

    if "." in path:
        return path.rsplit(".", 1)[0]
    if "[" in path and path.endswith("]"):
        return path.rsplit("[", 1)[0]
    return "$"


def _schema_issue(
    *,
    path: str,
    kind: str,
    schema: Mapping[str, object] | None = None,
    actual: object = ...,
    parent_path: str | None = None,
) -> MechanicsValidationIssue:
    """Build one bounded correction issue using schema facts and value type only."""

    expected_type = schema.get("type") if isinstance(schema, Mapping) else None
    enum = schema.get("enum") if isinstance(schema, Mapping) else None
    allowed_values = (
        tuple(str(item) for item in enum)
        if isinstance(enum, Sequence) and not isinstance(enum, (str, bytes, bytearray))
        else ()
    )
    minimum = None
    maximum = None
    if isinstance(schema, Mapping):
        for name in ("minLength", "minItems"):
            candidate = schema.get(name)
            if isinstance(candidate, int):
                minimum = candidate
                break
        for name in ("maxLength", "maxItems"):
            candidate = schema.get(name)
            if isinstance(candidate, int):
                maximum = candidate
                break
    return MechanicsValidationIssue(
        path=path,
        kind=kind,
        expected_type=str(expected_type) if isinstance(expected_type, str) else None,
        actual_type=None if actual is ... else _json_type_name(actual),
        parent_path=parent_path or _parent_path(path),
        allowed_values=allowed_values,
        minimum=minimum,
        maximum=maximum,
        actual_length=(
            len(actual) if isinstance(actual, str)
            and kind in {"string_too_short", "string_too_long"} else None
        ),
    )


def _campaign_file_updates(
    arguments: Mapping[str, object],
    options: Mapping[str, object],
) -> tuple[dict[str, object], ...]:
    """Validate a model batch completely before any Campaign File write begins."""

    schema = _campaign_file_update_schema(options)
    properties = schema["properties"]
    assert isinstance(properties, Mapping)
    disposition_schema = {
        "type": "object",
        "properties": {
            _CAMPAIGN_FILE_STATE_CHANGE: properties[_CAMPAIGN_FILE_STATE_CHANGE],
            _CAMPAIGN_FILE_STATE_CHANGE_SUMMARY: properties[
                _CAMPAIGN_FILE_STATE_CHANGE_SUMMARY
            ],
        },
        "required": [_CAMPAIGN_FILE_STATE_CHANGE, _CAMPAIGN_FILE_STATE_CHANGE_SUMMARY],
        "additionalProperties": False,
    }
    disposition_arguments = {
        name: arguments[name]
        for name in (_CAMPAIGN_FILE_STATE_CHANGE, _CAMPAIGN_FILE_STATE_CHANGE_SUMMARY)
        if name in arguments
    }
    issues: list[MechanicsValidationIssue] = list(
        _narrative_shape_issues(disposition_arguments, disposition_schema)
    )
    issues.extend(
        _schema_issue(
            path=str(name),
            kind="unexpected_field",
            actual=arguments[name],
            parent_path="$",
        )
        for name in sorted(set(arguments) - set(properties))
    )
    updates: list[dict[str, object]] = []
    operations_by_collection = {
        collection_name: operation
        for operation, collection_name in _CAMPAIGN_FILE_COLLECTIONS
    }
    for collection_name, raw_items in arguments.items():
        if str(collection_name) in {
            _CAMPAIGN_FILE_STATE_CHANGE,
            _CAMPAIGN_FILE_STATE_CHANGE_SUMMARY,
        }:
            continue
        if str(collection_name) not in properties:
            continue
        if not isinstance(raw_items, Sequence) or isinstance(
            raw_items, (str, bytes, bytearray)
        ):
            issues.append(
                _schema_issue(
                    path=str(collection_name),
                    kind="wrong_type",
                    schema=cast(Mapping[str, object], properties[str(collection_name)]),
                    actual=raw_items,
                    parent_path="$",
                )
            )
            continue
        if not raw_items:
            issues.append(
                _schema_issue(
                    path=str(collection_name),
                    kind="empty_collection",
                    schema=cast(Mapping[str, object], properties[str(collection_name)]),
                    actual=raw_items,
                    parent_path="$",
                )
            )
            continue
        if len(raw_items) > 64:
            issues.append(
                _schema_issue(
                    path=str(collection_name),
                    kind="collection_too_long",
                    schema=cast(Mapping[str, object], properties[str(collection_name)]),
                    actual=raw_items,
                    parent_path="$",
                )
            )
            continue
        operation = operations_by_collection[str(collection_name)]
        item_schema = _narrative_input_schema(operation, options)
        for index, raw_item in enumerate(raw_items):
            if not isinstance(raw_item, Mapping):
                issues.append(
                    _schema_issue(
                        path=f"{collection_name}[{index}]",
                        kind="wrong_type",
                        schema={"type": "object"},
                        actual=raw_item,
                        parent_path=str(collection_name),
                    )
                )
                continue
            item = {str(name): _plain_json(value) for name, value in raw_item.items()}
            item_issues = _narrative_shape_issues(
                item, item_schema, path_prefix=f"{collection_name}[{index}]"
            )
            issues.extend(item_issues)
            if item_issues:
                continue
            updates.append({"operation": operation, **item})
    if issues:
        ordered = tuple(sorted(issues, key=lambda issue: (issue.path, issue.kind)))
        raise MechanicsRejected(
            "Campaign File parameters do not match the exposed update schema",
            code="campaign_file_update_invalid",
            field_paths=tuple(dict.fromkeys(issue.path for issue in ordered)),
            validation_issues=ordered,
            state_changed=False,
        )
    state_change = arguments[_CAMPAIGN_FILE_STATE_CHANGE]
    disposition_is_inconsistent = (state_change == "changed" and not updates) or (
        state_change == "unchanged" and bool(updates)
    )
    if disposition_is_inconsistent:
        issue = MechanicsValidationIssue(
            path=_CAMPAIGN_FILE_STATE_CHANGE,
            kind="inconsistent_disposition",
            expected_type="string",
            actual_type="string",
            parent_path="$",
            allowed_values=("changed", "unchanged"),
        )
        raise MechanicsRejected(
            "Campaign File state assessment contradicts the supplied collections",
            code="campaign_file_disposition_inconsistent",
            field_paths=(_CAMPAIGN_FILE_STATE_CHANGE,),
            validation_issues=(issue,),
            state_changed=False,
        )
    if len(updates) > 64:
        issue = MechanicsValidationIssue(
            path="$",
            kind="update_too_large",
            expected_type="object",
            actual_type="object",
            parent_path="$",
            maximum=64,
        )
        raise MechanicsRejected(
            "Campaign File update contains too many changes",
            code="campaign_file_update_too_large",
            field_paths=("$",),
            validation_issues=(issue,),
            state_changed=False,
        )
    return tuple(updates)


def _campaign_file_update_description() -> str:
    """Describe the GM's note-taking duty without dictating the fiction."""

    return (
        "Use character_updates for Ruleset-declared ordinary inventory/currency bookkeeping. "
        "Read the participant campaign reference for current fields, exact schema and revision "
        "first; submit complete replacement field values preserving unrelated holdings. "
        "Narrative attributes/facts alone never change equipment or money. Send related character "
        "changes and transaction facts together; rejection saves neither. Do not narrate success "
        "before acceptance or call failure a stale panel. Combat outcomes require their mechanics. "
        "When inventory_updates is offered, it is the active fallback for this campaign. "
        "Read campaign_state path inventory for its current holdings and revision; establish "
        "owner/holder entities in the same call when needed. Use holdings to establish or "
        "correct counts and transfers to move existing quantities. Loans, storage and returns "
        "preserve ownership; only explicit gifts/sales change it. Never infer currency exchange "
        "rates or use the fallback to bypass a Ruleset rejection. "
        "Update your durable Campaign File to the complete decided post-action truth in effect "
        "when control returns to the player. If Sylvia is holding a player-facing response, "
        "review it against that truth before committing. Store every lasting truth the response "
        "establishes or changes, including newly introduced people, "
        "places, objects, clues, relationships, promises, knowledge, scene state, and Plan "
        "Progress together in one call. Record the GM-owned outcome, not a summary of the "
        "player's request and not transient atmosphere. Use Ruleset tools for mechanics. The "
        "whole update is validated and committed together before the player sees the response. "
        "Any player-facing response and this disposition must describe the same post-action truth. "
        "Sylvia releases a settled response only after this call succeeds; it must not advance "
        "beyond or contradict the admitted durable state. "
        "Set state_change to 'unchanged' and omit all state collections only when the response "
        "changes no durable Campaign File truth; that records an unchanged decision without "
        "creating a database revision. An unchanged assessment asserts that location, scene, "
        "time, physical positions, player or NPC "
        "decisions, knowledge, relationships, people, places, objects, reveals, and Plan status "
        "are all exactly unchanged from the supplied pre-action State. "
        "An unsuccessful outcome can still change location, time, knowledge, or scene facts; "
        "record those consequences even when the player's goal remains unmet. Restating supplied "
        "Campaign State is not a change. Omit unchanged collections; an included collection must "
        "contain at least one change."
    )


def _validate_narrative_shape(
    arguments: Mapping[str, object],
    schema: Mapping[str, object],
    *,
    path_prefix: str = "",
) -> None:
    """Reject missing or foreign Campaign File fields with safe, actionable paths."""

    issues = _narrative_shape_issues(
        arguments,
        schema,
        path_prefix=path_prefix,
    )
    if issues:
        raise MechanicsRejected(
            "MCP Campaign State parameters do not match the exposed schema",
            code="campaign_file_update_invalid",
            field_paths=tuple(dict.fromkeys(issue.path for issue in issues)),
            validation_issues=issues,
            state_changed=False,
        )


def _narrative_shape_invalid_paths(
    arguments: Mapping[str, object],
    schema: Mapping[str, object],
    *,
    path_prefix: str = "",
) -> tuple[str, ...]:
    """Return every safely identifiable schema violation in one model correction."""

    return tuple(
        dict.fromkeys(
            issue.path
            for issue in _narrative_shape_issues(
                arguments,
                schema,
                path_prefix=path_prefix,
            )
        )
    )


def _narrative_shape_issues(
    arguments: Mapping[str, object],
    schema: Mapping[str, object],
    *,
    path_prefix: str = "",
) -> tuple[MechanicsValidationIssue, ...]:
    """Return every schema violation with enough safe detail for one correction."""

    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, Mapping) or not isinstance(required, Sequence):
        raise CommandRejected("Campaign narrative schema is invalid")
    allowed = {str(name) for name in properties}
    supplied = {str(name) for name in arguments}
    missing = sorted(str(name) for name in required if str(name) not in supplied)
    foreign = sorted(supplied - allowed)
    issues: list[MechanicsValidationIssue] = []
    for name in missing:
        path = f"{path_prefix}.{name}" if path_prefix else name
        property_schema = properties.get(name)
        issues.append(
            _schema_issue(
                path=path,
                kind="missing_required_field",
                schema=property_schema if isinstance(property_schema, Mapping) else None,
                parent_path=path_prefix or "$",
            )
        )
    for name in foreign:
        path = f"{path_prefix}.{name}" if path_prefix else name
        issues.append(
            _schema_issue(
                path=path,
                kind="unexpected_field",
                actual=arguments[name],
                parent_path=path_prefix or "$",
            )
        )

    for raw_name, value in arguments.items():
        name = str(raw_name)
        raw_property = properties.get(name)
        if not isinstance(raw_property, Mapping):  # pragma: no cover - schema invariant
            continue
        expected_type = raw_property.get("type")
        path = f"{path_prefix}.{name}" if path_prefix else name
        invalid_type = False
        if expected_type == "string":
            invalid_type = not isinstance(value, str)
            if isinstance(value, str):
                minimum = raw_property.get("minLength")
                maximum = raw_property.get("maxLength")
                if isinstance(minimum, int) and len(value) < minimum:
                    issues.append(
                        _schema_issue(
                            path=path,
                            kind="string_too_short",
                            schema=raw_property,
                            actual=value,
                        )
                    )
                    continue
                if isinstance(maximum, int) and len(value) > maximum:
                    issues.append(
                        _schema_issue(
                            path=path,
                            kind="string_too_long",
                            schema=raw_property,
                            actual=value,
                        )
                    )
                    continue
        elif expected_type == "array":
            invalid_type = not isinstance(value, Sequence) or isinstance(
                value, (str, bytes, bytearray)
            )
            item_schema = raw_property.get("items")
            item_type_invalid = False
            if (
                not invalid_type
                and isinstance(item_schema, Mapping)
                and item_schema.get("type") == "string"
            ):
                for index, item in enumerate(cast(Sequence[object], value)):
                    if not isinstance(item, str):
                        item_type_invalid = True
                        issues.append(
                            _schema_issue(
                                path=f"{path}[{index}]",
                                kind="wrong_type",
                                schema=item_schema,
                                actual=item,
                                parent_path=path,
                            )
                        )
                if item_type_invalid:
                    continue
        elif expected_type == "object":
            invalid_type = not isinstance(value, Mapping)
        if invalid_type:
            issues.append(
                _schema_issue(
                    path=path,
                    kind="wrong_type",
                    schema=raw_property,
                    actual=value,
                )
            )
            continue
        enum = raw_property.get("enum")
        if (
            isinstance(enum, Sequence)
            and not isinstance(enum, (str, bytes, bytearray))
            and value not in enum
        ):
            issues.append(
                _schema_issue(
                    path=path,
                    kind="invalid_enum_value",
                    schema=raw_property,
                    actual=value,
                )
            )
    return tuple(sorted(issues, key=lambda issue: (issue.path, issue.kind)))


def _require_protocol(context: ServerRequestContext[Any]) -> None:
    if context.protocol_version != MCP_PROTOCOL_VERSION:
        raise CommandRejected(
            "Unsupported MCP protocol version",
            code="unsupported_mcp_protocol_version",
        )


def _request_meta(context: ServerRequestContext[Any]) -> Mapping[str, object]:
    raw = context.meta
    if raw is None or not isinstance(raw, Mapping):
        raise CommandRejected("MCP request metadata is required", code="missing_mcp_metadata")
    meta = cast(Mapping[str, object], raw)
    if _SESSION_KEYS.intersection(meta):
        raise CommandRejected(
            "MCP transport sessions cannot carry mechanics authority",
            code="mcp_session_authority_forbidden",
        )
    return meta


def _meta_text(meta: Mapping[str, object], name: str) -> str:
    value = meta.get(f"{_META_PREFIX}{name}")
    if not isinstance(value, str) or not value or value != value.strip():
        raise CommandRejected(
            "MCP request identity is invalid",
            code="invalid_mcp_request_identity",
        )
    return value


def _meta_optional_text(meta: Mapping[str, object], name: str) -> str | None:
    value = meta.get(f"{_META_PREFIX}{name}")
    if value is None:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise CommandRejected(
            "MCP request identity is invalid",
            code="invalid_mcp_request_identity",
        )
    return value


def _meta_revision(meta: Mapping[str, object]) -> int:
    value = meta.get(f"{_META_PREFIX}expected_revision")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CommandRejected(
            "MCP expected revision is invalid",
            code="invalid_mcp_expected_revision",
        )
    return value


def _safe_error(error: AuthorityError) -> types.CallToolResult:
    """Return a bounded correction contract without forwarding arbitrary exception text."""

    raw_code = str(getattr(error, "code", "authority_rejected"))
    code = "mechanics_validation_failed" if raw_code == "command_rejected" else raw_code
    reasons = {
        "mechanics_validation_failed": (
            "The operation did not satisfy its declared parameters or current rules state."
        ),
        "stale_revision": "Campaign state changed before the operation could be admitted.",
        "wrong_owner": "The operation did not originate from the current campaign authority.",
        "wrong_writer": "The current session is not allowed to perform this operation.",
        "idempotency_conflict": "The operation identity conflicts with an earlier request.",
        "mcp_tool_unavailable": "The selected operation is no longer available.",
        "campaign_knowledge_limit_invalid": (
            "Search parameter 'limit' must be an integer from 1 to 8. Retry with limit=5."
        ),
        "campaign_knowledge_search_invalid": (
            "Use a nonempty query of at most 500 characters and distinct sources "
            "from the tool schema."
        ),
        "campaign_reference_too_large": (
            "This Campaign State read exceeds its byte bound; facts may still be present. "
            "Use search_campaign_knowledge with sources=['campaign_state'] and limit=5, then "
            "read_campaign_reference with source='campaign_state' and an exact returned path. "
            "Do not repeat the oversized whole-state read."
        ),
    }
    field_paths = (
        sorted(set(error.field_paths))
        if isinstance(error, MechanicsRejected)
        else sorted(
            {
                match
                for pattern in (
                    r"parameter '([A-Za-z][A-Za-z0-9_.-]{0,79})'",
                    r"field '([A-Za-z][A-Za-z0-9_.-]{0,79})'",
                )
                for match in re.findall(pattern, str(error))
            }
        )
    )
    validation: dict[str, object] = {"field_paths": field_paths}
    if code == "campaign_knowledge_limit_invalid":
        validation = {
            "field_paths": ["limit"], "minimum": 1, "maximum": 8, "expected_type": "integer",
        }
    elif code == "campaign_reference_too_large":
        validation = {"field_paths": ["path"]}
    if isinstance(error, MechanicsRejected) and error.validation_issues:
        validation["issues"] = [issue.as_payload() for issue in error.validation_issues]
    reason = (
        error.safe_reason
        if isinstance(error, RulesetPreconditionRejected)
        else reasons.get(code, "Mechanics authority rejected the operation.")
    )
    payload = {
        "status": "rejected",
        "code": code,
        "reason": reason,
        "validation": validation,
        "state_changed": (
            error.state_changed if isinstance(error, MechanicsRejected) else False
        ),
    }
    return types.CallToolResult(
        content=[
            types.TextContent(text=json.dumps(payload, sort_keys=True, separators=(",", ":")))
        ],
        structured_content=payload,
        is_error=True,
    )


def _validation_issues_from_payload(value: object) -> tuple[MechanicsValidationIssue, ...]:
    """Rehydrate the bounded MCP correction contract without accepting submitted values."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    issues: list[MechanicsValidationIssue] = []
    for raw in value[:64]:
        if not isinstance(raw, Mapping):
            continue
        path = raw.get("path")
        kind = raw.get("kind")
        if not isinstance(path, str) or not isinstance(kind, str):
            continue
        allowed = raw.get("allowed_values", ())
        allowed_values = (
            tuple(str(item) for item in allowed[:64] if isinstance(item, str))
            if isinstance(allowed, Sequence) and not isinstance(allowed, (str, bytes, bytearray))
            else ()
        )
        issues.append(
            MechanicsValidationIssue(
                path=path[:240],
                kind=kind[:80],
                expected_type=(
                    str(raw["expected_type"])[:80]
                    if isinstance(raw.get("expected_type"), str)
                    else None
                ),
                actual_type=(
                    str(raw["actual_type"])[:80]
                    if isinstance(raw.get("actual_type"), str)
                    else None
                ),
                parent_path=(
                    str(raw["parent_path"])[:240]
                    if isinstance(raw.get("parent_path"), str)
                    else None
                ),
                allowed_values=allowed_values,
                minimum=(raw.get("minimum") if isinstance(raw.get("minimum"), int) else None),
                maximum=(raw.get("maximum") if isinstance(raw.get("maximum"), int) else None),
                actual_length=(
                    raw["actual_length"]
                    if type(raw.get("actual_length")) is int and raw["actual_length"] >= 0
                    else None
                ),
            )
        )
    return tuple(issues)


def build_stateless_mcp_server(owner: AuthorityOwner) -> Server[Any]:
    """Build one state-free SDK server whose handlers query *owner* afresh."""

    async def list_tools(
        context: ServerRequestContext[Any],
        _params: types.PaginatedRequestParams | None,
    ) -> types.ListToolsResult:
        try:
            _require_protocol(context)
            meta = _request_meta(context)
            campaign_id = _meta_text(meta, "campaign_id")
            snapshot = owner.snapshot(campaign_id)
            participant_ids = tuple(
                participant.participant_id
                for participant in getattr(snapshot, "participants", ())
            )
            projected: list[types.Tool] = []
            for tool in owner.catalog(campaign_id):
                schema = _plain_json(tool.input_schema)
                if not isinstance(schema, dict):  # pragma: no cover - CatalogTool contract
                    raise CommandRejected("Catalog input schema is invalid")
                model_schema, parameter_derivations = _model_input_schema(
                    schema,
                    participant_ids=participant_ids,
                )
                projected.append(
                    types.Tool(
                        name=tool.name,
                        description=tool.description or tool.capability,
                        input_schema=model_schema,
                        _meta={
                            f"{_META_PREFIX}package_id": tool.package_id,
                            f"{_META_PREFIX}package_version": tool.package_version,
                            f"{_META_PREFIX}package_sha256": tool.package_sha256,
                            f"{_META_PREFIX}capability": tool.capability,
                            f"{_META_PREFIX}model_behavior": tool.model_behavior.value,
                            f"{_META_PREFIX}parameter_derivations": parameter_derivations,
                        },
                    )
                )
            accepted_plan_reader = getattr(owner, "accepted_campaign_plan", None)
            try:
                accepted_plan = (
                    accepted_plan_reader(campaign_id)
                    if callable(accepted_plan_reader)
                    else None
                )
            except CommandRejected:
                accepted_plan = None
            session_id = _meta_optional_text(meta, "session_id")
            structured_plan_available = accepted_plan is not None and isinstance(
                accepted_plan.content.get("campaign_plan"), Mapping
            )
            readable_plan_available = accepted_plan is not None and isinstance(
                accepted_plan.content.get("campaign_plan_markdown"), str
            )
            if session_id is not None or structured_plan_available or readable_plan_available:
                reference_sources = ["campaign_state", "participant"]
                if structured_plan_available or readable_plan_available:
                    reference_sources.append("campaign_plan")
                if structured_plan_available:
                    reference_sources.extend(["campaign_setting", "ruleset"])
                projected.append(
                    types.Tool(
                        name=_KNOWLEDGE_SEARCH_TOOL_NAME,
                        description=(
                            "READ-ONLY discovery: find a small set of exact Campaign State, "
                            "Campaign Plan, Setting, Ruleset, or Character File references when "
                            "you do not already know their IDs or paths. Results identify "
                            "candidate references and provenance; they are not the facts "
                            "themselves. Follow a relevant result with an exact read through "
                            "read_campaign_reference. Campaign State remains current truth. "
                            "Similar names may identify different records. If results are empty "
                            "or irrelevant, refine with a known name/subject and source, or read "
                            "an available index reference; do not infer that a fact does not exist."
                        ),
                        input_schema={
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 500,
                                    "description": (
                                        "A natural question or a person, place, subject, "
                                        "relationship, current location, rule, or prepared item."
                                    ),
                                },
                                "sources": {
                                    "type": "array",
                                    "items": {
                                        "type": "string",
                                        "enum": reference_sources,
                                    },
                                    "minItems": 1,
                                    "maxItems": len(reference_sources),
                                    "uniqueItems": True,
                                    "description": (
                                        "Optional authority sources to search. Omit to search "
                                        "every source available to this campaign."
                                    ),
                                },
                                "limit": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 8,
                                    "default": 5,
                                },
                            },
                            "required": ["query"],
                            "additionalProperties": False,
                        },
                        _meta={
                            f"{_META_PREFIX}package_id": _REFERENCE_PACKAGE_ID,
                            f"{_META_PREFIX}package_version": "1",
                            f"{_META_PREFIX}package_sha256": "0" * 64,
                            f"{_META_PREFIX}capability": "context.search",
                            f"{_META_PREFIX}category": "reference",
                        },
                    )
                )
                projected.append(
                    types.Tool(
                        name=_REFERENCE_TOOL_NAME,
                        description=(
                            "READ-ONLY: retrieve current Campaign State or one additional "
                            "available Plan, Setting, Ruleset, or Character File. A participant "
                            "reference returns that admitted character's authored summary and "
                            "structured baseline when available. This "
                            "tool cannot "
                            "update, write, save, or commit Campaign File state. Campaign State is "
                            "current truth; Plan and Setting material are preparation and "
                            "defaults. "
                            "Read for a concrete missing fact that changes present adjudication, "
                            "or consult Plan preparation when choosing a new situation, assessing "
                            "objective completion, or closing an episode. Read the smallest "
                            "relevant item, not to decorate or expand narration. Retrieved "
                            "detail remains private unless the current action and vantage reveal "
                            "it. Do not reread Plan or Setting material already projected into the "
                            "current request. An ID, title or reference path does not include "
                            "the source contents. For an engine-owned structured input format, "
                            "read source engine_format, path spatial_scene before constructing "
                            "a combat scene; it returns the installed schema, example "
                            "and requirements."
                        ),
                        input_schema={
                            "type": "object",
                            "properties": {
                                "source": {
                                    "type": "string",
                                    "enum": [*reference_sources, "engine_format"],
                                },
                                "path": {
                                    "type": "string",
                                    "description": (
                                        "For campaign_state prefer a stable Campaign File "
                                        "record ID "
                                        "from search_campaign_knowledge. 'current' reads all state "
                                        "and may exceed the byte bound in a mature campaign. "
                                        "For campaign_plan use 'structured' for "
                                        "the whole Plan, 'structured/structure/<act-id>' for one "
                                        "act, 'structured/objectives/<objective-id>' for one "
                                        "objective and its completion conditions, or 'readable'. "
                                        "For a longer readable Plan use 'readable/page/0' and "
                                        "follow the returned next_path; each page is bounded. "
                                        "For participant use its exact participant ID to retrieve "
                                        "that Character File. For "
                                        "campaign_setting or ruleset use an exact admitted package "
                                        "path. Append '#heading-slug' to read one Markdown section "
                                        "without reading the rest of its file. Large UTF-8 files "
                                        "return bounded '#text/<character-offset>' excerpts. "
                                        "Follow next_path or search_campaign_knowledge for a "
                                        "relevant excerpt. For engine_format "
                                        "use spatial_scene (the installed scene schema "
                                        "and example)."
                                    ),
                                },
                            },
                            "required": ["source", "path"],
                            "additionalProperties": False,
                        },
                        _meta={
                            f"{_META_PREFIX}package_id": _REFERENCE_PACKAGE_ID,
                            f"{_META_PREFIX}package_version": "1",
                            f"{_META_PREFIX}package_sha256": "0" * 64,
                            f"{_META_PREFIX}capability": "context.read",
                            f"{_META_PREFIX}category": "reference",
                        },
                    )
                )
                if session_id is not None:
                    options = owner.campaign_narrative_options(session_id)
                    if options.get("operations"):
                        projected.append(
                            types.Tool(
                                name=_CAMPAIGN_FILE_UPDATE_TOOL_NAME,
                                description=_campaign_file_update_description(),
                                input_schema=_campaign_file_update_schema(options),
                                _meta={
                                    f"{_META_PREFIX}package_id": _REFERENCE_PACKAGE_ID,
                                    f"{_META_PREFIX}package_version": "1",
                                    f"{_META_PREFIX}package_sha256": "0" * 64,
                                    f"{_META_PREFIX}capability": (
                                        "narrative_state.update_campaign_file"
                                    ),
                                    f"{_META_PREFIX}category": "narrative_state",
                                },
                            )
                        )
            return types.ListToolsResult(tools=projected, ttl_ms=0, cache_scope="private")
        except AuthorityError as error:
            raise MCPError(
                types.INVALID_PARAMS,
                str(error),
                {"code": getattr(error, "code", "authority_rejected")},
            ) from error

    async def call_tool(
        context: ServerRequestContext[Any],
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        try:
            _require_protocol(context)
            meta = _request_meta(context)
            campaign_id = _meta_text(meta, "campaign_id")
            if params.name == _CAMPAIGN_FILE_UPDATE_TOOL_NAME:
                if _meta_text(meta, "tool_name") != _CAMPAIGN_FILE_UPDATE_TOOL_NAME:
                    raise CommandRejected(
                        "MCP Campaign File tool identity does not match the request",
                        code="mcp_tool_identity_mismatch",
                    )
                session_id = _meta_text(meta, "session_id")
                basis_message_id = _meta_text(meta, "basis_message_id")
                update_id = _meta_text(meta, "update_id")
                turn_id = _meta_text(meta, "turn_id")
                supplied_arguments = params.arguments or {}
                if not isinstance(supplied_arguments, dict):
                    raise CommandRejected(
                        "MCP Campaign File parameters must be an object",
                        code="campaign_file_update_invalid",
                    )
                options = owner.campaign_narrative_options(session_id)
                updates = _campaign_file_updates(supplied_arguments, options)
                if not updates:
                    current = owner.snapshot(campaign_id)
                    plain_payload = {
                        "status": "campaign_file_unchanged",
                        "update_id": update_id,
                        "campaign_revision": current.revision,
                        "state_changed": False,
                    }
                    return types.CallToolResult(
                        content=[
                            types.TextContent(
                                text=json.dumps(
                                    plain_payload,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                    ensure_ascii=False,
                                )
                            )
                        ],
                        structured_content=plain_payload,
                    )
                payload = owner.update_campaign_file(
                    session_id=session_id,
                    update_id=update_id,
                    updates=updates,
                    basis_message_ids=(basis_message_id,),
                    turn_id=turn_id,
                )
                plain_payload = cast(dict[str, object], _plain_json(payload))
                return types.CallToolResult(
                    content=[
                        types.TextContent(
                            text=json.dumps(
                                plain_payload,
                                sort_keys=True,
                                separators=(",", ":"),
                                ensure_ascii=False,
                            )
                        )
                    ],
                    structured_content=plain_payload,
                )
            if params.name in _NARRATIVE_OPERATIONS_BY_TOOL:
                if _meta_text(meta, "tool_name") != params.name:
                    raise CommandRejected(
                        "MCP narrative tool identity does not match the request",
                        code="mcp_tool_identity_mismatch",
                    )
                session_id = _meta_text(meta, "session_id")
                basis_message_id = _meta_text(meta, "basis_message_id")
                update_id = _meta_text(meta, "update_id")
                turn_id = _meta_text(meta, "turn_id")
                supplied_arguments = params.arguments or {}
                if not isinstance(supplied_arguments, dict):
                    raise CommandRejected("MCP narrative parameters must be an object")
                operation = _NARRATIVE_OPERATIONS_BY_TOOL[params.name]
                options = owner.campaign_narrative_options(session_id)
                available_operations = set(_string_sequence(options.get("operations", ())))
                if operation not in available_operations:
                    raise CommandRejected("MCP narrative operation is unavailable")
                _validate_narrative_shape(
                    supplied_arguments,
                    _narrative_input_schema(operation, options),
                )
                arguments = {"operation": operation, **supplied_arguments}
                if operation == "record_memory":
                    if set(arguments) not in ({
                        "operation",
                        "fact_id",
                        "category",
                        "text",
                        "visibility",
                        "known_by",
                    }, {
                        "operation",
                        "fact_id",
                        "category",
                        "text",
                        "visibility",
                        "known_by",
                        "subjects",
                    }):
                        raise CommandRejected("MCP campaign memory parameters are invalid")
                    known_by = arguments.get("known_by")
                    subjects = arguments.get("subjects", [])
                    if not isinstance(known_by, list) or not isinstance(subjects, list):
                        raise CommandRejected("MCP campaign memory knowledge is invalid")
                    receipt = owner.record_campaign_memory(
                        session_id=session_id,
                        memory_id=update_id,
                        fact_id=str(arguments.get("fact_id", "")),
                        category=str(arguments.get("category", "")),
                        text=str(arguments.get("text", "")),
                        visibility=str(arguments.get("visibility", "")),
                        known_by=tuple(str(item) for item in known_by),
                        subjects=tuple(str(item) for item in subjects),
                        basis_message_ids=(basis_message_id,),
                        turn_id=turn_id,
                    )
                elif operation == "record_entity":
                    required = {
                        "operation",
                        "entity_id",
                        "entity_kind",
                        "name",
                        "attributes",
                        "visibility",
                        "known_by",
                    }
                    if set(arguments) not in (required, required | {"source_ref"}):
                        raise CommandRejected("MCP campaign entity parameters are invalid")
                    attributes = arguments.get("attributes")
                    known_by = arguments.get("known_by")
                    source_ref = arguments.get("source_ref")
                    if not isinstance(attributes, dict) or not isinstance(known_by, list):
                        raise CommandRejected("MCP campaign entity values are invalid")
                    receipt = owner.record_campaign_entity(
                        session_id=session_id,
                        update_id=update_id,
                        entity_id=str(arguments.get("entity_id", "")),
                        entity_kind=str(arguments.get("entity_kind", "")),
                        name=str(arguments.get("name", "")),
                        attributes=cast(Mapping[str, Any], attributes),
                        visibility=str(arguments.get("visibility", "")),
                        known_by=tuple(str(item) for item in known_by),
                        source_ref=(str(source_ref) if isinstance(source_ref, str) else None),
                        basis_message_ids=(basis_message_id,),
                        turn_id=turn_id,
                    )
                elif operation == "update_scene":
                    if set(arguments) not in (
                        {"operation", "summary"},
                        {"operation", "summary", "act_id"},
                        {"operation", "summary", "location_id"},
                        {"operation", "act_id", "summary", "location_id"},
                    ):
                        raise CommandRejected("MCP campaign scene parameters are invalid")
                    summary = arguments.get("summary")
                    location = arguments.get("location_id")
                    act = arguments.get("act_id")
                    if not isinstance(summary, str):
                        raise CommandRejected(
                            "MCP campaign scene field 'summary' is invalid",
                            code="campaign_scene_invalid",
                        )
                    if "location_id" in arguments and not isinstance(location, str):
                        raise CommandRejected(
                            "MCP campaign scene field 'location_id' is invalid",
                            code="campaign_scene_invalid",
                        )
                    if "act_id" in arguments and not isinstance(act, str):
                        raise CommandRejected(
                            "MCP campaign scene field 'act_id' is invalid",
                            code="campaign_scene_invalid",
                        )
                    receipt = owner.record_campaign_scene(
                        session_id=session_id,
                        scene_id=update_id,
                        act_id=str(act) if isinstance(act, str) else None,
                        summary=summary,
                        location_id=str(location) if isinstance(location, str) else None,
                        basis_message_ids=(basis_message_id,),
                        turn_id=turn_id,
                    )
                elif operation == "reveal_secret":
                    if set(arguments) != {"operation", "secret_id", "audience"}:
                        raise CommandRejected("MCP campaign secret parameters are invalid")
                    eligible_secret_ids = _string_sequence(
                        options.get("eligible_secret_ids", ())
                    )
                    if arguments.get("secret_id") not in eligible_secret_ids:
                        raise CommandRejected("MCP campaign secret reveal is unavailable")
                    audience = arguments.get("audience")
                    if not isinstance(audience, list):
                        raise CommandRejected("MCP campaign secret audience is invalid")
                    receipt = owner.record_campaign_secret_reveal(
                        session_id=session_id,
                        reveal_id=update_id,
                        secret_id=str(arguments.get("secret_id", "")),
                        audience=tuple(str(item) for item in audience),
                        basis_message_ids=(basis_message_id,),
                        turn_id=turn_id,
                    )
                elif operation == "update_plan_progress":
                    if set(arguments) != {
                        "operation",
                        "plan_item_id",
                        "progress_status",
                        "progress_summary",
                    }:
                        raise CommandRejected("MCP Plan Progress parameters are invalid")
                    receipt = owner.record_campaign_plan_progress(
                        session_id=session_id,
                        update_id=update_id,
                        plan_item_id=str(arguments.get("plan_item_id", "")),
                        progress_status=str(arguments.get("progress_status", "")),
                        summary=str(arguments.get("progress_summary", "")),
                        basis_message_ids=(basis_message_id,),
                        turn_id=turn_id,
                    )
                else:
                    raise CommandRejected("MCP narrative operation is unavailable")
                payload = {
                    "status": "narrative_state_committed",
                    "operation_id": receipt.operation_id,
                    "record_id": receipt.record_id,
                    "kind": receipt.kind,
                    "resulting_revision": receipt.resulting_revision,
                }
                return types.CallToolResult(
                    content=[
                        types.TextContent(
                            text=json.dumps(payload, sort_keys=True, separators=(",", ":"))
                        )
                    ],
                    structured_content=payload,
                )
            if params.name == _KNOWLEDGE_SEARCH_TOOL_NAME:
                if _meta_text(meta, "tool_name") != _KNOWLEDGE_SEARCH_TOOL_NAME:
                    raise CommandRejected(
                        "MCP knowledge tool identity does not match the request",
                        code="mcp_tool_identity_mismatch",
                    )
                arguments = params.arguments or {}
                if not isinstance(arguments, dict) or not set(arguments) <= {
                    "query",
                    "sources",
                    "limit",
                }:
                    raise CommandRejected("MCP knowledge search parameters are invalid")
                query = arguments.get("query")
                sources = arguments.get("sources")
                limit = arguments.get("limit", 5)
                if (
                    not isinstance(query, str)
                    or (sources is not None and not isinstance(sources, list))
                    or (
                        isinstance(sources, list)
                        and not all(isinstance(item, str) for item in sources)
                    )
                    or not isinstance(limit, int)
                    or isinstance(limit, bool)
                ):
                    raise CommandRejected("MCP knowledge search parameters are invalid")
                payload = owner.search_campaign_knowledge(
                    campaign_id,
                    query=query,
                    sources=cast(list[str] | None, sources),
                    limit=limit,
                )
                return types.CallToolResult(
                    content=[
                        types.TextContent(
                            text=json.dumps(
                                _plain_json(payload),
                                sort_keys=True,
                                separators=(",", ":"),
                                ensure_ascii=False,
                            )
                        )
                    ],
                    structured_content=cast(dict[str, object], _plain_json(payload)),
                )
            if params.name == _REFERENCE_TOOL_NAME:
                if _meta_text(meta, "tool_name") != _REFERENCE_TOOL_NAME:
                    raise CommandRejected(
                        "MCP reference tool identity does not match the request",
                        code="mcp_tool_identity_mismatch",
                    )
                arguments = params.arguments or {}
                if not isinstance(arguments, dict):
                    raise CommandRejected("MCP reference parameters must be an object")
                if set(arguments) != {"source", "path"}:
                    raise CommandRejected("MCP reference parameters are invalid")
                source = arguments.get("source")
                path = arguments.get("path")
                if not isinstance(source, str) or not isinstance(path, str):
                    raise CommandRejected("MCP reference parameters are invalid")
                payload = owner.read_campaign_reference(
                    campaign_id,
                    source=source,
                    path=path,
                )
                return types.CallToolResult(
                    content=[
                        types.TextContent(
                            text=json.dumps(
                                _plain_json(payload),
                                sort_keys=True,
                                separators=(",", ":"),
                                ensure_ascii=False,
                            )
                        )
                    ],
                    structured_content=cast(dict[str, object], _plain_json(payload)),
                )
            package_id = _meta_text(meta, "package_id")
            package_version = _meta_text(meta, "package_version")
            package_sha256 = _meta_text(meta, "package_sha256")
            tool_name = _meta_text(meta, "tool_name")
            if params.name != tool_name:
                raise CommandRejected(
                    "MCP tool identity does not match the request",
                    code="mcp_tool_identity_mismatch",
                )
            current_tools = tuple(
                tool
                for tool in owner.catalog(campaign_id)
                if (
                    tool.package_id,
                    tool.package_version,
                    tool.package_sha256,
                    tool.name,
                )
                == (package_id, package_version, package_sha256, tool_name)
            )
            if len(current_tools) != 1:
                raise CommandRejected(
                    "MCP tool is no longer available from the canonical catalog",
                    code="mcp_tool_unavailable",
                )
            arguments = params.arguments or {}
            if not isinstance(arguments, dict):  # pragma: no cover - SDK type contract
                raise CommandRejected("MCP tool parameters must be an object")
            command = ActionCommand(
                campaign_id=campaign_id,
                action_id=_meta_text(meta, "action_id"),
                idempotency_key=_meta_text(meta, "idempotency_key"),
                owner_id=_meta_text(meta, "owner_id"),
                writer=WriterRole(_meta_text(meta, "writer")),
                expected_revision=_meta_revision(meta),
                turn_id=_meta_text(meta, "turn_id"),
                package_id=package_id,
                tool_name=tool_name,
                parameters=cast(SafePayload, arguments),
                initiating_session_id=_meta_optional_text(meta, "initiating_session_id"),
            )
            action_receipt = owner.execute_action(command)
            action_payload: dict[str, Any]
            if isinstance(action_receipt, AwaitingChoiceReceipt):
                action_payload = {
                    "campaign_id": action_receipt.campaign_id,
                    "action_id": action_receipt.action_id,
                    "status": action_receipt.status,
                    "basis_revision": action_receipt.basis_revision,
                    "choice_sequence": action_receipt.choice_sequence,
                    "turn_id": action_receipt.turn_id,
                    "choice": {
                        "choice_id": action_receipt.choice.choice_id,
                        "prompt": action_receipt.choice.prompt,
                        "options": [
                            {"option_id": option.option_id, "label": option.label}
                            for option in action_receipt.choice.options
                        ],
                    },
                }
            else:
                action_payload = {
                    "campaign_id": action_receipt.campaign_id,
                    "action_id": action_receipt.action_id,
                    "status": action_receipt.status.value,
                    "result": _plain_json(action_receipt.result),
                    "resulting_revision": action_receipt.resulting_revision,
                }
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        text=json.dumps(action_payload, sort_keys=True, separators=(",", ":"))
                    )
                ],
                structured_content=action_payload,
            )
        except AuthorityError as error:
            return _safe_error(error)
        except (TypeError, ValueError):
            return _safe_error(
                CommandRejected(
                    "MCP request metadata is invalid",
                    code="invalid_mcp_request_metadata",
                )
            )

    return Server(
        _SERVER_NAME,
        version=MCP_SDK_VERSION,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


def _run(coroutine: Coroutine[Any, Any, _T]) -> _T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        with _SYNC_LOOP_LOCK:
            return _SYNC_LOOP.run_until_complete(coroutine)
    coroutine.close()
    raise RuntimeError("Synchronous MCP mechanics calls cannot run inside an event loop")


def _meta(values: Mapping[str, object]) -> types.RequestParamsMeta:
    return cast(
        types.RequestParamsMeta,
        {f"{_META_PREFIX}{key}": value for key, value in values.items()},
    )


class StatelessMcpMechanics:
    """Synchronous canonical-loop adapter over fresh MCP 2026 SDK requests."""

    def __init__(self, owner: AuthorityOwner) -> None:
        self._owner = owner

    async def _list_tools(
        self,
        campaign_id: str,
        *,
        purpose: str,
        discovery_id: str,
        session_id: str | None,
    ) -> tuple[ModelMechanicsTool, ...]:
        basis = self._owner.snapshot(campaign_id)
        server = build_stateless_mcp_server(self._owner)
        async with Client(server, raise_exceptions=True) as client:
            if client.protocol_version != MCP_PROTOCOL_VERSION:
                raise CommandRejected(
                    "MCP client negotiated an unsupported protocol version",
                    code="unsupported_mcp_protocol_version",
                )
            list_meta: dict[str, object] = {"campaign_id": campaign_id}
            if session_id is not None:
                list_meta["session_id"] = session_id
            result = await client.list_tools(
                meta=_meta(list_meta),
                cache_mode="bypass",
            )
        projected: list[ModelMechanicsTool] = []
        for tool in result.tools:
            raw_meta = tool.meta or {}
            raw_derivations = raw_meta.get(f"{_META_PREFIX}parameter_derivations", {})
            if not isinstance(raw_derivations, Mapping):
                raise CommandRejected("MCP model parameter derivations are invalid")
            projected.append(
                ModelMechanicsTool(
                    package_id=_meta_text(raw_meta, "package_id"),
                    package_version=_meta_text(raw_meta, "package_version"),
                    package_sha256=_meta_text(raw_meta, "package_sha256"),
                    name=tool.name,
                    capability=_meta_text(raw_meta, "capability"),
                    input_schema=cast(Mapping[str, object], tool.input_schema),
                    parameter_derivations={
                        str(name): cast(Mapping[str, object], _plain_json(derivation))
                        for name, derivation in raw_derivations.items()
                        if isinstance(derivation, Mapping)
                    },
                    description=tool.description or "",
                    category=_meta_optional_text(raw_meta, "category") or "mechanic",
                    model_behavior=ModelToolBehavior(
                        _meta_optional_text(raw_meta, "model_behavior")
                        or ModelToolBehavior.OPTIONAL.value
                    ),
                )
            )
        projected_tools = tuple(projected)
        current = self._owner.snapshot(campaign_id)
        self._owner.record_admin_full_audit(
            {
                "kind": "mcp_list_tools",
                "discovery_id": discovery_id,
                "purpose": purpose,
                "campaign_id": campaign_id,
                "session_id": session_id,
                "basis_revision": basis.revision,
                "result_revision": current.revision,
                "writer": current.writer.value,
                "open_turn_ids": sorted(
                    turn_id
                    for turn_id, status in current.turns.items()
                    if status.value == "open"
                ),
                "ruleset_state": {
                    key: _plain_json(value)
                    for key, value in current.state.items()
                    if key.endswith(".combat") or key.endswith(".phase")
                },
                "tools": [
                    {
                        "package_id": tool.package_id,
                        "package_version": tool.package_version,
                        "package_sha256": tool.package_sha256,
                        "name": tool.name,
                        "capability": tool.capability,
                        "description": tool.description,
                        "input_schema": _plain_json(tool.input_schema),
                        "category": tool.category,
                        "model_behavior": tool.model_behavior.value,
                    }
                    for tool in projected_tools
                ],
            }
        )
        return projected_tools

    def list_tools(
        self,
        campaign_id: str,
        *,
        purpose: str = "direct_discovery",
        discovery_id: str | None = None,
        session_id: str | None = None,
    ) -> tuple[ModelMechanicsTool, ...]:
        return _run(
            self._list_tools(
                campaign_id,
                purpose=purpose,
                discovery_id=discovery_id or f"mcp-discovery-{uuid.uuid4().hex}",
                session_id=session_id,
            )
        )

    async def _commit_campaign_file(
        self,
        campaign_id: str,
        *,
        session_id: str,
        basis_message_id: str,
        turn_id: str,
        update_id: str,
        parameters: Mapping[str, object],
    ) -> Mapping[str, object]:
        """Commit one model-selected Campaign File batch through fresh MCP authority."""

        server = build_stateless_mcp_server(self._owner)
        async with Client(server, raise_exceptions=True) as client:
            if client.protocol_version != MCP_PROTOCOL_VERSION:
                raise CommandRejected(
                    "MCP client negotiated an unsupported protocol version",
                    code="unsupported_mcp_protocol_version",
                )
            await client.list_tools(
                meta=_meta({"campaign_id": campaign_id, "session_id": session_id}),
                cache_mode="bypass",
            )
            result = await client.call_tool(
                _CAMPAIGN_FILE_UPDATE_TOOL_NAME,
                cast(dict[str, object], _plain_json(parameters)),
                meta=_meta(
                    {
                        "campaign_id": campaign_id,
                        "session_id": session_id,
                        "basis_message_id": basis_message_id,
                        "turn_id": turn_id,
                        "update_id": update_id,
                        "tool_name": _CAMPAIGN_FILE_UPDATE_TOOL_NAME,
                    }
                ),
            )
        payload = result.structured_content
        self._owner.record_admin_full_audit(
            {
                "kind": "mcp_update_campaign_file",
                "campaign_id": campaign_id,
                "session_id": session_id,
                "basis_message_id": basis_message_id,
                "turn_id": turn_id,
                "update_id": update_id,
                "parameters": _plain_json(parameters),
                "result": _plain_json(payload),
                "is_error": result.is_error,
            }
        )
        if result.is_error or not isinstance(payload, Mapping):
            error_payload = payload if isinstance(payload, Mapping) else {}
            code = str(error_payload.get("code", "mcp_campaign_file_update_rejected"))
            reason = error_payload.get("reason")
            validation = error_payload.get("validation")
            raw_paths = validation.get("field_paths", ()) if isinstance(validation, Mapping) else ()
            raw_issues = validation.get("issues", ()) if isinstance(validation, Mapping) else ()
            field_paths = tuple(
                str(path)
                for path in raw_paths
                if isinstance(raw_paths, Sequence)
                and not isinstance(raw_paths, (str, bytes, bytearray))
                and isinstance(path, str)
            )
            raise MechanicsRejected(
                str(reason)
                if isinstance(reason, str)
                else "Campaign File authority rejected the update.",
                code=code,
                field_paths=field_paths,
                validation_issues=_validation_issues_from_payload(raw_issues),
                state_changed=False,
            )
        return cast(Mapping[str, object], _plain_json(payload))

    def commit_campaign_file(
        self,
        campaign_id: str,
        *,
        session_id: str,
        basis_message_id: str,
        parameters: Mapping[str, object],
        turn_id: str | None = None,
    ) -> Mapping[str, object]:
        """Commit one replay-safe Campaign File batch selected by the active GM."""

        resolved_turn_id = turn_id or self._owner.current_open_turn(campaign_id)
        if resolved_turn_id is None:
            raise CommandRejected(
                "Campaign File update requires an open TURN",
                code="campaign_turn_unavailable",
            )
        identity_payload = json.dumps(
            {
                "campaign_id": campaign_id,
                "session_id": session_id,
                "basis_message_id": basis_message_id,
                "turn_id": resolved_turn_id,
                "parameters": _plain_json(parameters),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        update_id = f"campaign-file-{hashlib.sha256(identity_payload).hexdigest()[:32]}"
        return _run(
            self._commit_campaign_file(
                campaign_id,
                session_id=session_id,
                basis_message_id=basis_message_id,
                turn_id=resolved_turn_id,
                update_id=update_id,
                parameters=parameters,
            )
        )

    async def _commit_narrative_state(
        self,
        campaign_id: str,
        *,
        session_id: str,
        basis_message_id: str,
        turn_id: str,
        update_id: str,
        parameters: Mapping[str, object],
    ) -> Mapping[str, object]:
        operation = parameters.get("operation")
        if not isinstance(operation, str) or operation not in _NARRATIVE_TOOL_NAMES:
            raise CommandRejected(
                "Campaign narrative operation is unavailable",
                code="unsupported_campaign_narrative_operation",
            )
        tool_name = _NARRATIVE_TOOL_NAMES[operation]
        tool_parameters = {
            str(name): _plain_json(value)
            for name, value in parameters.items()
            if str(name) != "operation"
        }
        server = build_stateless_mcp_server(self._owner)
        async with Client(server, raise_exceptions=True) as client:
            if client.protocol_version != MCP_PROTOCOL_VERSION:
                raise CommandRejected(
                    "MCP client negotiated an unsupported protocol version",
                    code="unsupported_mcp_protocol_version",
                )
            await client.list_tools(
                meta=_meta({"campaign_id": campaign_id, "session_id": session_id}),
                cache_mode="bypass",
            )
            result = await client.call_tool(
                tool_name,
                tool_parameters,
                meta=_meta(
                    {
                        "campaign_id": campaign_id,
                        "session_id": session_id,
                        "basis_message_id": basis_message_id,
                        "turn_id": turn_id,
                        "update_id": update_id,
                        "tool_name": tool_name,
                    }
                ),
            )
        payload = result.structured_content
        self._owner.record_admin_full_audit(
            {
                "kind": "mcp_commit_campaign_narrative_state",
                "campaign_id": campaign_id,
                "session_id": session_id,
                "basis_message_id": basis_message_id,
                "turn_id": turn_id,
                "update_id": update_id,
                "tool_name": tool_name,
                "parameters": _plain_json(parameters),
                "result": _plain_json(payload),
                "is_error": result.is_error,
            }
        )
        if result.is_error or not isinstance(payload, Mapping):
            error_payload = payload if isinstance(payload, Mapping) else {}
            code = str(error_payload.get("code", "mcp_narrative_state_rejected"))
            reason = error_payload.get("reason")
            validation = error_payload.get("validation")
            raw_paths = validation.get("field_paths", ()) if isinstance(validation, Mapping) else ()
            field_paths = tuple(
                str(path)
                for path in raw_paths
                if isinstance(raw_paths, Sequence)
                and not isinstance(raw_paths, (str, bytes, bytearray))
                and isinstance(path, str)
            )
            state_changed = error_payload.get("state_changed") is True
            recorder = getattr(self._owner, "record_mechanics_diagnostic", None)
            if callable(recorder):
                recorder(
                    session_id=session_id,
                    turn_id=turn_id,
                    action_id=update_id,
                    tool_name=tool_name,
                    rejection_code=code,
                    validation_field_paths=field_paths,
                    state_changed=state_changed,
                )
            raise MechanicsRejected(
                str(reason)
                if isinstance(reason, str)
                else "Campaign State authority rejected the write.",
                code=code,
                field_paths=field_paths,
                state_changed=state_changed,
            )
        return cast(Mapping[str, object], _plain_json(payload))

    def commit_narrative_state(
        self,
        campaign_id: str,
        *,
        session_id: str,
        basis_message_id: str,
        parameters: Mapping[str, object],
        turn_id: str | None = None,
    ) -> Mapping[str, object]:
        """Commit one exact session narrative update through a fresh MCP request."""

        resolved_turn_id = turn_id or self._owner.current_open_turn(campaign_id)
        if resolved_turn_id is None:
            raise CommandRejected(
                "Campaign narrative commit requires an open TURN",
                code="campaign_turn_unavailable",
            )
        identity_payload = json.dumps(
            {
                "campaign_id": campaign_id,
                "session_id": session_id,
                "basis_message_id": basis_message_id,
                "turn_id": resolved_turn_id,
                "parameters": _plain_json(parameters),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        update_id = f"narrative-{hashlib.sha256(identity_payload).hexdigest()[:32]}"
        return _run(
            self._commit_narrative_state(
                campaign_id,
                session_id=session_id,
                basis_message_id=basis_message_id,
                turn_id=resolved_turn_id,
                update_id=update_id,
                parameters=parameters,
            )
        )

    async def _read_reference(
        self,
        campaign_id: str,
        *,
        source: str,
        path: str,
        session_id: str | None = None,
    ) -> Mapping[str, object]:
        server = build_stateless_mcp_server(self._owner)
        async with Client(server, raise_exceptions=True) as client:
            if client.protocol_version != MCP_PROTOCOL_VERSION:
                raise CommandRejected(
                    "MCP client negotiated an unsupported protocol version",
                    code="unsupported_mcp_protocol_version",
                )
            # Session identity keeps participant reads discoverable without an accepted Plan.
            identity = {"campaign_id": campaign_id}
            if session_id is not None:
                identity["session_id"] = session_id
            await client.list_tools(meta=_meta(identity), cache_mode="bypass")
            result = await client.call_tool(
                _REFERENCE_TOOL_NAME,
                {"source": source, "path": path},
                meta=_meta(
                    {
                        "campaign_id": campaign_id,
                        "tool_name": _REFERENCE_TOOL_NAME,
                    }
                ),
            )
        payload = result.structured_content
        self._owner.record_admin_full_audit(
            {
                "kind": "mcp_read_campaign_reference",
                "campaign_id": campaign_id,
                "source": source,
                "path": path,
                "result": _plain_json(payload),
                "is_error": result.is_error,
            }
        )
        if result.is_error or not isinstance(payload, Mapping):
            detail = next(
                (
                    item.text
                    for item in result.content
                    if isinstance(item, types.TextContent) and item.text
                ),
                "no structured result",
            )
            raise CommandRejected(f"MCP campaign reference read was rejected: {detail[:500]}")
        return cast(Mapping[str, object], _plain_json(payload))

    async def _search_knowledge(
        self,
        campaign_id: str,
        *,
        query: str,
        sources: Sequence[str] | None = None,
        limit: int = 5,
    ) -> Mapping[str, object]:
        """Search exact campaign authority through a fresh private MCP request."""

        server = build_stateless_mcp_server(self._owner)
        arguments: dict[str, object] = {"query": query, "limit": limit}
        if sources is not None:
            arguments["sources"] = list(sources)
        async with Client(server, raise_exceptions=True) as client:
            if client.protocol_version != MCP_PROTOCOL_VERSION:
                raise CommandRejected(
                    "MCP client negotiated an unsupported protocol version",
                    code="unsupported_mcp_protocol_version",
                )
            await client.list_tools(
                meta=_meta({"campaign_id": campaign_id}),
                cache_mode="bypass",
            )
            result = await client.call_tool(
                _KNOWLEDGE_SEARCH_TOOL_NAME,
                arguments,
                meta=_meta(
                    {
                        "campaign_id": campaign_id,
                        "tool_name": _KNOWLEDGE_SEARCH_TOOL_NAME,
                    }
                ),
            )
        payload = result.structured_content
        self._owner.record_admin_full_audit(
            {
                "kind": "mcp_search_campaign_knowledge",
                "campaign_id": campaign_id,
                "query": query,
                "sources": list(sources) if sources is not None else None,
                "limit": limit,
                "result": _plain_json(payload),
                "is_error": result.is_error,
            }
        )
        if result.is_error or not isinstance(payload, Mapping):
            detail = next(
                (
                    item.text
                    for item in result.content
                    if isinstance(item, types.TextContent) and item.text
                ),
                "no structured result",
            )
            raise CommandRejected(f"MCP campaign knowledge search was rejected: {detail[:500]}")
        return cast(Mapping[str, object], _plain_json(payload))

    def search_knowledge(
        self,
        campaign_id: str,
        *,
        query: str,
        sources: Sequence[str] | None = None,
        limit: int = 5,
    ) -> Mapping[str, object]:
        """Discover bounded campaign references through a fresh MCP request."""

        return _run(
            self._search_knowledge(
                campaign_id,
                query=query,
                sources=sources,
                limit=limit,
            )
        )

    def read_reference(
        self,
        campaign_id: str,
        *,
        source: str,
        path: str,
        session_id: str | None = None,
    ) -> Mapping[str, object]:
        """Read one exact campaign reference through a fresh MCP request."""

        return _run(
            self._read_reference(
                campaign_id,
                source=source,
                path=path,
                session_id=session_id,
            )
        )

    async def _execute_action(
        self,
        command: ActionCommand,
        expected: ModelMechanicsTool,
    ) -> ActionReceipt | AwaitingChoiceReceipt:
        server = build_stateless_mcp_server(self._owner)
        async with Client(server, raise_exceptions=True) as client:
            if client.protocol_version != MCP_PROTOCOL_VERSION:
                raise CommandRejected(
                    "MCP client negotiated an unsupported protocol version",
                    code="unsupported_mcp_protocol_version",
                )
            await client.list_tools(
                meta=_meta({"campaign_id": command.campaign_id}),
                cache_mode="bypass",
            )
            result = await client.call_tool(
                expected.name,
                dict(command.parameters),
                meta=_meta(
                    {
                        "campaign_id": command.campaign_id,
                        "action_id": command.action_id,
                        "idempotency_key": command.idempotency_key,
                        "owner_id": command.owner_id,
                        "writer": command.writer.value,
                        "expected_revision": command.expected_revision,
                        "turn_id": command.turn_id,
                        "package_id": expected.package_id,
                        "package_version": expected.package_version,
                        "package_sha256": expected.package_sha256,
                        "tool_name": expected.name,
                        **(
                            {"initiating_session_id": command.initiating_session_id}
                            if command.initiating_session_id is not None
                            else {}
                        ),
                    }
                ),
            )
        payload = result.structured_content
        self._owner.record_admin_full_audit(
            {
                "kind": "mcp_call_tool",
                "campaign_id": command.campaign_id,
                "action_id": command.action_id,
                "turn_id": command.turn_id,
                "expected_revision": command.expected_revision,
                "package_id": expected.package_id,
                "package_version": expected.package_version,
                "package_sha256": expected.package_sha256,
                "tool_name": expected.name,
                "arguments": _plain_json(command.parameters),
                "result": _plain_json(payload),
                "is_error": result.is_error,
            }
        )
        if not isinstance(payload, Mapping):
            raise CommandRejected("MCP tool returned no structured result")
        if result.is_error:
            code = payload.get("code", "mcp_action_rejected")
            reason = payload.get("reason")
            validation = payload.get("validation")
            raw_paths = validation.get("field_paths", ()) if isinstance(validation, Mapping) else ()
            raw_issues = validation.get("issues", ()) if isinstance(validation, Mapping) else ()
            field_paths = tuple(
                str(path)
                for path in raw_paths
                if isinstance(raw_paths, Sequence)
                and not isinstance(raw_paths, (str, bytes, bytearray))
                and isinstance(path, str)
            )
            recorder = getattr(self._owner, "record_mechanics_diagnostic", None)
            if callable(recorder):
                recorder(
                    session_id=command.initiating_session_id,
                    turn_id=command.turn_id,
                    action_id=command.action_id,
                    tool_name=expected.name,
                    rejection_code=str(code),
                    validation_field_paths=field_paths,
                    state_changed=payload.get("state_changed") is True,
                )
            typed_errors: dict[str, tuple[type[AuthorityError], str]] = {
                "idempotency_conflict": (IdempotencyConflict, "idempotency conflict"),
                "stale_revision": (StaleRevision, "stale revision"),
                "wrong_owner": (WrongOwner, "wrong owner"),
                "wrong_writer": (WrongWriter, "Command writer is not authoritative"),
            }
            typed_error = typed_errors.get(str(code))
            if typed_error is not None:
                error_type, message = typed_error
                raise error_type(f"{message} rejected by MCP mechanics authority")
            raise MechanicsRejected(
                str(reason)
                if isinstance(reason, str)
                else "Mechanics authority rejected the operation.",
                code=str(code),
                field_paths=field_paths,
                validation_issues=_validation_issues_from_payload(raw_issues),
                state_changed=payload.get("state_changed") is True,
            )
        if payload.get("status") == "awaiting_choice":
            try:
                raw_choice = payload["choice"]
                if not isinstance(raw_choice, Mapping):
                    raise TypeError("choice must be an object")
                raw_options = raw_choice["options"]
                if not isinstance(raw_options, Sequence) or isinstance(
                    raw_options, (str, bytes, bytearray)
                ):
                    raise TypeError("choice options must be an array")
                return AwaitingChoiceReceipt(
                    campaign_id=str(payload["campaign_id"]),
                    action_id=str(payload["action_id"]),
                    basis_revision=int(payload["basis_revision"]),
                    choice_sequence=int(payload.get("choice_sequence", 0)),
                    turn_id=str(payload["turn_id"]),
                    choice=MechanicsChoiceRequest(
                        choice_id=str(raw_choice["choice_id"]),
                        prompt=str(raw_choice["prompt"]),
                        options=tuple(
                            MechanicsChoiceOption(
                                option_id=str(cast(Mapping[str, object], option)["option_id"]),
                                label=str(cast(Mapping[str, object], option)["label"]),
                            )
                            for option in raw_options
                            if isinstance(option, Mapping)
                        ),
                    ),
                )
            except (KeyError, TypeError, ValueError) as error:
                raise CommandRejected("MCP mechanics choice receipt is invalid") from error
        receipt_result = payload.get("result")
        if not isinstance(receipt_result, Mapping):
            raise CommandRejected("MCP action result is invalid")
        try:
            return ActionReceipt(
                campaign_id=str(payload["campaign_id"]),
                action_id=str(payload["action_id"]),
                status=ActionStatus(str(payload["status"])),
                result=cast(SafePayload, receipt_result),
                resulting_revision=int(payload["resulting_revision"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise CommandRejected("MCP action receipt is invalid") from error

    def execute_action(self, command: ActionCommand) -> ActionReceipt | AwaitingChoiceReceipt:
        current = tuple(
            tool
            for tool in self.list_tools(
                command.campaign_id,
                purpose="call_tool_revalidation",
            )
            if tool.package_id == command.package_id and tool.name == command.tool_name
        )
        if len(current) != 1:
            raise CommandRejected(
                "MCP tool is unavailable from the canonical catalog",
                code="mcp_tool_unavailable",
            )
        return _run(self._execute_action(command, current[0]))
