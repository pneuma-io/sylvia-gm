"""Materialize bounded declarative package mechanics for ``AuthorityOwner``.

The package runtime never receives a database handle.  It executes against an
in-memory copy of the package-owned state roots and returns a ``MechanicOutcome``;
the application owner remains the only component allowed to commit mutations.
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
import re
import threading
from collections.abc import Iterator, Mapping, Sequence
from copy import deepcopy
from enum import Enum
from functools import lru_cache
from typing import Any, cast

import yaml
from pydantic import ValidationError

from sylvia_gm.application._yaml_bounds import YamlShapeError, validate_yaml_graph
from sylvia_gm.application.player_actions import (
    PlayerActionChecker,
    PlayerActionPreviewer,
    PlayerControls,
    parse_player_controls,
)
from sylvia_gm.application.result_card_admission import (
    validate_activity_label,
    validate_result_card_declaration,
)
from sylvia_gm.application.result_cards import project_activity_label, project_result_card
from sylvia_gm.application.shot_effects import project_shot_effect
from sylvia_gm.application.spatial_preview import project_spatial_preview
from sylvia_gm.application.tactical_grid import player_known_spatial_scene
from sylvia_gm.domain.authority import (
    JsonValue,
    Mechanic,
    MechanicContext,
    MechanicOutcome,
    MechanicsChoiceOption,
    MechanicsChoiceRequest,
    ModelToolBehavior,
    ParticipantMutation,
    SafePayload,
    StateMutation,
    owned_json_value,
    owned_payload,
)
from sylvia_gm.domain.content_validation import ContentShapeError, validate_content_shape
from sylvia_gm.domain.errors import RulesetDiagnostic, RulesetPreconditionRejected
from sylvia_gm.domain.package_library import StatePredicate, ToolDeclaration, canonical_json_bytes
from sylvia_gm.ruleset.schema import RulesetFile
from sylvia_gm.runtime._authoring_shape import runtime_authoring_shape_errors
from sylvia_gm.runtime._condition_evaluator import evaluate_condition, evaluate_expression
from sylvia_gm.runtime._effect_executor import _resolve_spatial_input
from sylvia_gm.runtime._exceptions import MechanicsChoiceSuspended, PreConditionError
from sylvia_gm.runtime._parameter_declarations import (
    ParameterDeclarationError,
    resolve_parameter_declarations,
)
from sylvia_gm.runtime._procedure_executor import trigger_procedure
from sylvia_gm.runtime._runtime import RulesetRuntime
from sylvia_gm.runtime._schema_validator import (
    validate_parameter_declarations,
    validate_ruleset_data,
)
from sylvia_gm.runtime._tool_executor import execute_tool, prepare_tool_context
from sylvia_gm.runtime._types import (
    ChoiceResolutionControl,
    ExecutionContext,
    RuntimeChoiceOption,
    RuntimeChoiceRequest,
    ToolDef,
    ToolResult,
)
from sylvia_gm.spatial import (
    AdmittedSpatialScene,
    SpatialEngine,
    admit_spatial_query,
    admit_spatial_scene,
    mutate_spatial_scene,
)

PACKAGE_RUNTIME_PATH = "runtime/ruleset.yaml"
PACKAGE_RUNTIME_BYTE_LIMIT = 16 * 1024 * 1024
_RUNTIME_FORMAT = 1
_MAX_RUNTIME_DEPTH = 64
# Complete rulesets can exceed 250,000 structural nodes without exceeding their work budget.
_MAX_RUNTIME_NODES = 500_000
_MAX_OBJECT_MEMBERS = 25_000
_MAX_SPATIAL_EVIDENCE = 128
_MAX_SPATIAL_RESULT_BYTES = 2 * 1024 * 1024
_DICE_EXECUTION_LOCK = threading.RLock()


class PackageRuntimeError(ValueError):
    """An admitted package's executable bytes are invalid or inconsistent."""

    def __init__(self, message: str, *, diagnostics: tuple[RulesetDiagnostic, ...] = ()) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


def _mutable_json(value: object) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _mutable_json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_mutable_json(item) for item in value]
    return value


def _json_safe(value: object) -> JsonValue:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = dataclasses.asdict(value)
    if isinstance(value, Enum):
        return _json_safe(value.value)
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise PackageRuntimeError("Runtime result contains a non-string key")
            result[key] = _json_safe(item)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise PackageRuntimeError(f"Runtime result type is not canonical JSON: {type(value).__name__}")


def _validate_shape(value: object) -> None:
    nodes = 0
    active: set[int] = set()

    def visit(item: object, depth: int) -> None:
        nonlocal nodes
        if depth > _MAX_RUNTIME_DEPTH:
            raise PackageRuntimeError("Package runtime nesting is too deep")
        nodes += 1
        if nodes > _MAX_RUNTIME_NODES:
            raise PackageRuntimeError("Package runtime has too many nodes")
        if isinstance(item, Mapping):
            if len(item) > _MAX_OBJECT_MEMBERS:
                raise PackageRuntimeError("Package runtime object is too large")
            identity = id(item)
            if identity in active:
                raise PackageRuntimeError("Package runtime contains a recursive alias")
            active.add(identity)
            try:
                for key, child in item.items():
                    if not isinstance(key, str):
                        raise PackageRuntimeError("Package runtime keys must be strings")
                    visit(child, depth + 1)
            finally:
                active.remove(identity)
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            identity = id(item)
            if identity in active:
                raise PackageRuntimeError("Package runtime contains a recursive alias")
            active.add(identity)
            try:
                for child in item:
                    visit(child, depth + 1)
            finally:
                active.remove(identity)
        elif item is not None and not isinstance(item, (str, bool, int, float)):
            raise PackageRuntimeError("Package runtime contains a non-JSON value")
        elif isinstance(item, float) and not math.isfinite(item):
            raise PackageRuntimeError("Package runtime contains a non-finite number")

    visit(value, 1)


def _nested_get(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    if not path:
        return current
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _nested_set(value: dict[str, Any], path: str, replacement: Any) -> None:
    if not path:
        raise PackageRuntimeError("Runtime state writes require a field path")
    parts = path.split(".")
    current = value
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = replacement


class AuthorityMemoryStateBridge:
    """Mutable package-local view used only during one mechanic execution."""

    def __init__(
        self,
        namespace: str,
        authority_state: Mapping[str, object],
        participants: Mapping[str, object] | None = None,
        *,
        player_spatial_view: bool = False,
    ) -> None:
        self.namespace = namespace
        self._player_spatial_view = player_spatial_view
        self._state = self._root(authority_state, "state")
        self._entities = self._root(authority_state, "entities")
        self._combat = self._root(authority_state, "combat")
        participant_values = _mutable_json(participants or {})
        if not isinstance(participant_values, dict) or any(
            not isinstance(value, dict) for value in participant_values.values()
        ):
            raise PackageRuntimeError("Participant context must be an object of artifacts")
        self._participants = cast(dict[str, dict[str, Any]], participant_values)
        self._before_participants = deepcopy(self._participants)
        self._before_state = deepcopy(self._state)
        self._before_entities = deepcopy(self._entities)
        self._before_combat = deepcopy(self._combat)
        self._spatial_engines: dict[str, SpatialEngine] = {}
        self._spatial_snapshots: dict[str, AdmittedSpatialScene] = {}
        self._spatial_recorded_snapshots: set[tuple[str, str]] = set()
        self._spatial_private_variables: set[str] = set()
        self._spatial_evidence: list[dict[str, object]] = []
        self._spatial_query_results: dict[tuple[str, str, bool, bytes], dict[str, object]] = {}
        self._state_parameter_sources: dict[str, str] = {}
        self._spatial_aggregate_sources: dict[str, set[str]] = {}
        self._shot_effects: list[dict[str, object]] = []
        self._shot_effects_unavailable = False

    def _root(self, authority_state: Mapping[str, object], key: str) -> dict[str, Any]:
        value = authority_state.get(f"{self.namespace}.{key}", {})
        mutable = _mutable_json(value)
        if not isinstance(mutable, dict):
            raise PackageRuntimeError(f"Package runtime root '{key}' must be an object")
        return mutable

    @property
    def combat_state(self) -> dict[str, Any]:
        return self._combat

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        if entity_id == "state":
            return deepcopy(self._state)
        value = self._entities.get(entity_id)
        if not isinstance(value, dict):
            raise KeyError(entity_id)
        return deepcopy(value)

    def get_entity_raw(self, entity_id: str) -> dict[str, Any]:
        return self.get_entity(entity_id)

    def entity_exists(self, entity_id: str) -> bool:
        return entity_id == "state" or isinstance(self._entities.get(entity_id), dict)

    def create_entity(self, entity_id: str, value: object) -> None:
        """Create one package-local identity; never replace a participant or existing state."""
        if (
            not isinstance(entity_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", entity_id) is None
            or entity_id in {"state", "combat", "entities", "participants"}
        ):
            raise PreConditionError("valid_entity_identity", "A valid new entity ID is required.")
        if entity_id in self._entities or entity_id in self._participants:
            raise PreConditionError(
                "entity_identity_absent",
                "This entity or participant already exists. Reuse its ID; its state was preserved.",
            )
        if not isinstance(value, Mapping):
            raise PackageRuntimeError("New entity state must be an object")
        _validate_shape(value)
        self._entities[entity_id] = _mutable_json(_json_safe(value))

    def get_participant(self, participant_id: str) -> dict[str, Any]:
        value = self._participants.get(participant_id)
        if not isinstance(value, dict):
            raise KeyError(participant_id)
        return deepcopy(value)

    def participant_exists(self, participant_id: str) -> bool:
        return isinstance(self._participants.get(participant_id), dict)

    def write_participant_field(self, participant_id: str, field_path: str, value: Any) -> None:
        """Edit only the data of a participant owned by this Ruleset."""
        participant = self._participants.get(participant_id)
        if not isinstance(participant, dict) or participant.get("package_id") != self.namespace:
            raise PackageRuntimeError("Participant mutation requires the owning Ruleset")
        if not field_path or not isinstance(participant.get("data"), dict):
            raise PackageRuntimeError("Participant mutation requires a data field")
        _nested_set(participant["data"], field_path, _mutable_json(value))

    def participant_mutations(self) -> tuple[ParticipantMutation, ...]:
        """Return detached live character changes for the authority transaction."""
        return tuple(
            ParticipantMutation(participant_id, owned_payload(participant["data"]))
            for participant_id, participant in self._participants.items()
            if participant != self._before_participants[participant_id]
        )

    def get_position(self, entity_id: str) -> dict[str, Any] | None:
        entity = self._entities.get(entity_id)
        if not isinstance(entity, dict):
            return None
        position = entity.get("position")
        return deepcopy(position) if isinstance(position, dict) else None

    def _entity(self, entity_id: str) -> dict[str, Any]:
        if entity_id == "state":
            return self._state
        entity = self._entities.get(entity_id)
        if not isinstance(entity, dict):
            raise KeyError(entity_id)
        return entity

    def read_field(self, entity_id: str, field_path: str) -> Any:
        return deepcopy(_nested_get(self._entity(entity_id), field_path))

    def write_field(self, entity_id: str, field_path: str, value: Any) -> None:
        _nested_set(self._entity(entity_id), field_path, _mutable_json(value))

    def modify_field(self, entity_id: str, field_path: str, delta: int) -> int:
        current = self.read_field(entity_id, field_path)
        if current is None:
            current = 0
        if not isinstance(current, (int, float)) or isinstance(current, bool):
            raise PackageRuntimeError("Runtime numeric mutation targeted a non-number")
        replacement = current + delta
        self.write_field(entity_id, field_path, replacement)
        return int(replacement)

    def take_damage(self, entity_id: str, amount: int) -> int:
        amount = max(0, int(amount))
        hp = self.read_field(entity_id, "hp")
        if not isinstance(hp, int):
            raise PackageRuntimeError("Damage target has no integer hp")
        replacement = max(0, hp - amount)
        self.write_field(entity_id, "hp", replacement)
        return replacement

    def heal(self, entity_id: str, amount: int) -> int:
        hp = self.read_field(entity_id, "hp")
        maximum = self.read_field(entity_id, "max_hp")
        if not isinstance(hp, int) or not isinstance(maximum, int):
            raise PackageRuntimeError("Healing target has no integer hp/max_hp")
        replacement = min(maximum, hp + max(0, int(amount)))
        self.write_field(entity_id, "hp", replacement)
        return replacement

    def add_condition(self, entity_id: str, condition: str) -> None:
        conditions = self.read_field(entity_id, "conditions")
        values = list(conditions) if isinstance(conditions, list) else []
        if condition not in values:
            values.append(condition)
        self.write_field(entity_id, "conditions", values)

    def remove_condition(self, entity_id: str, condition: str) -> None:
        conditions = self.read_field(entity_id, "conditions")
        values = list(conditions) if isinstance(conditions, list) else []
        if condition in values:
            values.remove(condition)
        self.write_field(entity_id, "conditions", values)

    def damage_ability(self, entity_id: str, ability: str, amount: int) -> int:
        path = f"abilities.{ability}.score"
        score = self.read_field(entity_id, path)
        if not isinstance(score, int):
            raise PackageRuntimeError("Ability damage target has no integer score")
        replacement = max(0, score - max(0, int(amount)))
        self.write_field(entity_id, path, replacement)
        return replacement

    def _spatial_engine(self, scene_path: str) -> SpatialEngine:
        root, separator, field_path = scene_path.partition(".")
        if root != "state" or not separator or not field_path:
            raise PackageRuntimeError("Spatial scene must use a package-owned state path")
        scene_value = self.read_field("state", field_path)
        if not isinstance(scene_value, Mapping):
            raise PackageRuntimeError("Package-owned spatial scene is unavailable")
        if self._player_spatial_view:
            scene_value = player_known_spatial_scene(scene_value)
        admitted = admit_spatial_scene(scene_value)
        engine = self._spatial_engines.get(scene_path)
        if engine is None:
            engine = SpatialEngine(scene_value)
            self._spatial_engines[scene_path] = engine
        elif admitted.snapshot_id != engine.snapshot.snapshot_id:
            engine.replace_scene(scene_value)
        return engine

    def _record_spatial_evidence(
        self,
        *,
        operation: str,
        query: Mapping[str, object],
        result: Mapping[str, object],
    ) -> None:
        if len(self._spatial_evidence) >= _MAX_SPATIAL_EVIDENCE:
            raise PackageRuntimeError("Spatial query evidence exceeds the action limit")
        result_bytes = canonical_json_bytes(result)
        if len(result_bytes) > _MAX_SPATIAL_RESULT_BYTES:
            raise PackageRuntimeError("Spatial query result exceeds the action byte limit")
        evidence = {
            "operation": operation,
            "map_id": result["map_id"],
            "geometry_revision": result["geometry_revision"],
            "snapshot_id": result["snapshot_id"],
            "algorithm_version": result["algorithm_version"],
            "query": _mutable_json(query),
            "ordered_result_sha256": hashlib.sha256(result_bytes).hexdigest(),
        }
        for key in ("target", "before", "after"):
            if key in result:
                evidence[key] = _mutable_json(result[key])
        self._spatial_evidence.append(evidence)

    def spatial_snapshot(self, scene_path: str) -> dict[str, object]:
        engine = self._spatial_engine(scene_path)
        snapshot = engine.snapshot
        self._spatial_snapshots[snapshot.snapshot_id] = snapshot
        result: dict[str, object] = {
            "kind": "snapshot",
            "map_id": snapshot.map_id,
            "geometry_revision": snapshot.geometry_revision,
            "snapshot_id": snapshot.snapshot_id,
            "algorithm_version": snapshot.algorithm_version,
        }
        key = (scene_path, snapshot.snapshot_id)
        if key not in self._spatial_recorded_snapshots:
            self._record_spatial_evidence(
                operation="snapshot",
                query={"kind": "snapshot", "map_id": snapshot.map_id},
                result=result,
            )
            self._spatial_recorded_snapshots.add(key)
        return deepcopy(result)

    def spatial_query(
        self,
        scene_path: str,
        *,
        operation: str,
        query: object,
        snapshot: object,
    ) -> dict[str, object]:
        if not isinstance(query, Mapping):
            raise PackageRuntimeError("Spatial query input must be an object")
        engine = self._spatial_engine(scene_path)
        selected: AdmittedSpatialScene | None = None
        if snapshot is not None:
            if not isinstance(snapshot, Mapping) or not isinstance(
                snapshot.get("snapshot_id"), str
            ):
                raise PackageRuntimeError("Spatial snapshot reference is invalid")
            selected = self._spatial_snapshots.get(cast(str, snapshot["snapshot_id"]))
            if selected is None:
                raise PackageRuntimeError("Spatial snapshot is unavailable in this action")
        admitted_scene = selected or engine.snapshot
        canonical_query = admit_spatial_query(
            admitted_scene,
            {"kind": operation, **dict(query)},
        ).as_payload()
        # Repeated reads of one immutable snapshot share the same evidence.
        # DSL effects still consume the shared operation budget; distinct queries
        # and changed snapshots retain the existing evidence and byte limits.
        key = (scene_path, admitted_scene.snapshot_id, selected is not None, canonical_json_bytes(canonical_query))
        cached = self._spatial_query_results.get(key)
        if cached is not None:
            return deepcopy(cached)
        result = engine.query(canonical_query, snapshot=selected).as_payload()
        self._record_spatial_evidence(
            operation=operation,
            query=canonical_query,
            result=result,
        )
        self._spatial_query_results[key] = deepcopy(result)
        return result

    def spatial_mutation(self, scene_path: str, request: object) -> dict[str, object]:
        """Advance one exact scene object while retaining prior in-action snapshots."""

        if not isinstance(request, Mapping):
            raise PackageRuntimeError("Spatial mutation input must be an object")
        engine = self._spatial_engine(scene_path)
        mutation = mutate_spatial_scene(engine.snapshot, request)
        field_path = scene_path.removeprefix("state.")
        engine.replace_scene(mutation.scene.as_payload())
        self.write_field("state", field_path, mutation.scene.as_payload())
        result = mutation.as_payload()
        self._record_spatial_evidence(
            operation="scene_mutation",
            query=cast(Mapping[str, object], _mutable_json(request)),
            result=result,
        )
        return result

    def register_spatial_private_variable(self, name: str) -> None:
        self._spatial_private_variables.add(name)

    def register_state_parameter(self, name: str, source: str) -> None:
        self._state_parameter_sources[name] = source

    def register_spatial_aggregate(self, name: str, source: object) -> None:
        """Retain aggregate source expressions until every used scene is known."""

        expressions = self._spatial_aggregate_sources.setdefault(name, set())

        def visit(value: object) -> None:
            if isinstance(value, str):
                expressions.add(value)
            elif isinstance(value, Mapping):
                for child in value.values():
                    visit(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    visit(child)

        visit(source)

    def record_shot_effect(self, request: object) -> None:
        """Project against the firing snapshot; keep partial/failed actions out of playback."""

        try:
            if not isinstance(request, Mapping) or len(self._shot_effects) >= 256:
                raise ValueError("Invalid shot presentation")
            reference = request.get("snapshot")
            if not isinstance(reference, Mapping):
                raise ValueError("Missing shot snapshot")
            snapshot = self._spatial_snapshots.get(reference.get("snapshot_id"))
            if snapshot is None:
                raise ValueError("Unavailable shot snapshot")
            scene_path = request.get("scene")
            if not isinstance(scene_path, str):
                raise ValueError("Missing shot scene")
            if self._spatial_engine(scene_path).snapshot.map_id != snapshot.map_id:
                raise ValueError("Shot snapshot belongs to another map")
            cue = project_shot_effect(
                {key: value for key, value in request.items() if key not in {"scene", "snapshot"}},
                scene=snapshot.as_payload(),
            )
            if cue is not None:
                self._shot_effects.append(cue)
            else:
                self._shot_effects_unavailable = True
        except (TypeError, ValueError, KeyError, PackageRuntimeError):
            self._shot_effects_unavailable = True

    @property
    def shot_presentation(self) -> dict[str, object]:
        return {
            "version": 1,
            "shots": deepcopy(self._shot_effects),
            "unavailable": self._shot_effects_unavailable,
        }

    @property
    def spatial_private_variables(self) -> frozenset[str]:
        private = set(self._spatial_private_variables) | {
            name
            for name, source in self._state_parameter_sources.items()
            if any(
                source == path or source.startswith(path + ".") for path in self._spatial_engines
            )
        }
        pending = dict(self._spatial_aggregate_sources)
        while pending:
            copied = {
                name
                for name, expressions in pending.items()
                if any(
                    private.intersection(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expression))
                    or any(
                        re.search(r"(?<![\w.])" + re.escape(path) + r"(?=$|[.\s\[])", expression)
                        for path in self._spatial_engines
                    )
                    for expression in expressions
                )
            }
            if not copied:
                break
            private.update(copied)
            for name in copied:
                pending.pop(name)
        return frozenset(private)

    @property
    def spatial_evidence(self) -> tuple[dict[str, object], ...]:
        return tuple(deepcopy(self._spatial_evidence))

    def mutations(self) -> tuple[StateMutation, ...]:
        changed: list[StateMutation] = []
        for key, before, after in (
            ("state", self._before_state, self._state),
            ("entities", self._before_entities, self._entities),
            ("combat", self._before_combat, self._combat),
        ):
            if after != before:
                changed.append(StateMutation(self.namespace, key, _json_safe(after)))
        return tuple(changed)


def _runtime_parameter_schema(tool: ToolDef) -> dict[str, object]:
    properties: dict[str, object] = {}
    required: list[str] = []
    for parameter in tool.parameters:
        if parameter.from_state is not None:
            continue
        if parameter.type == "enum":
            child: dict[str, object] = {
                "type": "string",
                "enum": list(parameter.enum_values or []),
            }
        elif parameter.type in {"entity_id", "string"}:
            child = {"type": "string"}
        elif parameter.type == "entity_id_list":
            child = {"type": "array", "items": {"type": "string"}}
        else:
            child = {"type": parameter.type}
        if parameter.type == "array" and parameter.items is not None:
            child["items"] = deepcopy(parameter.items)
        if parameter.resolve == "participant":
            child["x-sylvia-gm-reference"] = "participant"
        elif parameter.resolve == "entity":
            child["x-sylvia-gm-reference"] = "entity"
        elif parameter.resolve == "entity_list" and isinstance(child.get("items"), dict):
            cast(dict[str, object], child["items"])["x-sylvia-gm-reference"] = "entity"
        if parameter.description:
            child["description"] = parameter.description
        if parameter.minimum is not None:
            child["minimum"] = parameter.minimum
        if parameter.maximum is not None:
            child["maximum"] = parameter.maximum
        if parameter.min_items is not None:
            child["minItems"] = parameter.min_items
        if parameter.max_items is not None:
            child["maxItems"] = parameter.max_items
        if parameter.unique_items is not None:
            child["uniqueItems"] = parameter.unique_items
        properties[parameter.name] = child
        if parameter.required:
            required.append(parameter.name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _bound_runtime_parameter(bridge: AuthorityMemoryStateBridge, path: str) -> object:
    """Read one Ruleset-owned model parameter from authoritative package state."""

    root, separator, child_path = path.partition(".")
    if not separator or not child_path:
        raise PackageRuntimeError("Runtime state-bound parameter path is invalid")
    if root == "combat":
        value = _nested_get(bridge.combat_state, child_path)
    elif root == "state":
        value = bridge.read_field("state", child_path)
    else:  # pragma: no cover - rejected by runtime validation before materialization
        raise PackageRuntimeError("Runtime state-bound parameter root is unsupported")
    if value is None:
        raise PackageRuntimeError(f"Runtime state-bound parameter '{path}' is unavailable")
    return deepcopy(value)


def _schemas_agree(runtime_schema: Mapping[str, object], declaration: ToolDeclaration) -> bool:
    declared = declaration.input_schema
    if declared.get("type") != "object" or declared.get("additionalProperties") is not False:
        return False
    runtime_properties = runtime_schema.get("properties")
    declared_properties = declared.get("properties")
    if not isinstance(runtime_properties, Mapping) or not isinstance(declared_properties, Mapping):
        return False
    if set(runtime_properties) != set(declared_properties):
        return False
    if set(cast(Sequence[str], runtime_schema.get("required", []))) != set(
        cast(Sequence[str], declared.get("required", []))
    ):
        return False
    for name, expected in runtime_properties.items():
        actual = declared_properties[name]
        if not isinstance(expected, Mapping) or not isinstance(actual, Mapping):
            return False
        for field in (
            "type",
            "enum",
            "items",
            "minimum",
            "maximum",
            "minItems",
            "maxItems",
            "uniqueItems",
            "x-sylvia-gm-reference",
        ):
            if (field in expected) != (field in actual):
                return False
            if field in expected and canonical_json_bytes(expected[field]) != canonical_json_bytes(
                actual[field]
            ):
                return False
    return True


def _trigger_names(effects: Sequence[Mapping[str, Any]]) -> Iterator[str]:
    for effect in effects:
        if "trigger" in effect:
            body = effect["trigger"]
            if isinstance(body, str):
                name = body
            elif isinstance(body, Mapping):
                name = body.get("name", body.get("trigger"))
            else:
                name = None
            if not isinstance(name, str) or not name:
                raise PackageRuntimeError("Package runtime trigger identity is invalid")
            yield name
        for branch in ("on_true", "on_false", "do"):
            nested = effect.get(branch)
            if isinstance(nested, list) and all(isinstance(item, Mapping) for item in nested):
                yield from _trigger_names(cast(Sequence[Mapping[str, Any]], nested))
        for body in effect.values():
            if not isinstance(body, Mapping):
                continue
            for branch in ("on_true", "on_false", "do", "consequence"):
                nested = body.get(branch)
                if isinstance(nested, list) and all(isinstance(item, Mapping) for item in nested):
                    yield from _trigger_names(cast(Sequence[Mapping[str, Any]], nested))


def _validate_trigger_links(runtime: RulesetRuntime) -> None:
    available = {procedure.trigger for procedure in runtime.procedures.values()}
    referenced: set[str] = set()
    for tool in runtime.tools.values():
        referenced.update(_trigger_names(tool.effects))
    for procedure in runtime.procedures.values():
        referenced.update(_trigger_names(procedure.steps))
    if not referenced.issubset(available):
        raise PackageRuntimeError("Package runtime trigger has no matching procedure")


def _call_names(effects: Sequence[Mapping[str, Any]]) -> Iterator[str]:
    for effect in effects:
        if "call" in effect:
            body = effect["call"]
            if isinstance(body, str):
                name = body
            elif isinstance(body, Mapping):
                name = body.get("call", body.get("name"))
            else:
                name = None
            if not isinstance(name, str) or not name or not name.replace("_", "a").isalnum():
                raise PackageRuntimeError("Package runtime static call identity is invalid")
            yield name
        for branch in ("on_true", "on_false", "do"):
            nested = effect.get(branch)
            if isinstance(nested, list) and all(isinstance(item, Mapping) for item in nested):
                yield from _call_names(cast(Sequence[Mapping[str, Any]], nested))
        for body in effect.values():
            if not isinstance(body, Mapping):
                continue
            for branch in ("on_true", "on_false", "do", "consequence"):
                nested = body.get(branch)
                if isinstance(nested, list) and all(isinstance(item, Mapping) for item in nested):
                    yield from _call_names(cast(Sequence[Mapping[str, Any]], nested))


def _validate_static_call_graph(runtime: RulesetRuntime) -> None:
    tools = runtime.tools
    graph = {name: set(_call_names(tool.effects)) for name, tool in tools.items()}
    for procedure in runtime.procedures.values():
        for target in _call_names(procedure.steps):
            if target not in tools:
                raise PackageRuntimeError(
                    f"Package runtime static call references unknown tool '{target}'"
                )
    for source, targets in graph.items():
        unknown = sorted(target for target in targets if target not in tools)
        if unknown:
            raise PackageRuntimeError(
                f"Package runtime static call from '{source}' references unknown tool "
                f"'{unknown[0]}'"
            )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise PackageRuntimeError(f"Package runtime static call cycle includes '{name}'")
        if name in visited:
            return
        visiting.add(name)
        for target in graph[name]:
            visit(target)
        visiting.remove(name)
        visited.add(name)

    for name in graph:
        visit(name)


# Use LibYAML's safe parser when available; both loaders reject Python object tags.
_RuntimeSafeLoader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


class _BoundedRuntimeLoader(_RuntimeSafeLoader):  # type: ignore[misc]  # PyYAML has no bundled stubs.
    """Check the source graph before the YAML constructor expands aliases and merges."""

    def construct_document(self, node: yaml.Node) -> object:
        validate_yaml_graph(
            node,
            maximum_depth=_MAX_RUNTIME_DEPTH,
            maximum_nodes=_MAX_RUNTIME_NODES,
            maximum_object_members=_MAX_OBJECT_MEMBERS,
            count_mapping_keys=False,
        )
        return super().construct_document(node)


class _UniqueRuntimeLoader(_BoundedRuntimeLoader):
    """Reject duplicate authored YAML fields before the normal runtime validation."""

    def flatten_mapping(self, node: yaml.MappingNode) -> None:
        if not getattr(node, "_unique_keys_checked", False):
            seen = set()
            for key_node, _value_node in node.value:
                key = (
                    "<<"
                    if key_node.tag == "tag:yaml.org,2002:merge"
                    else self.construct_object(key_node)
                )
                if not isinstance(key, str) or key in seen:
                    raise ValueError("Runtime fields must be unique text keys")
                seen.add(key)
            node._unique_keys_checked = True
        # YAML merge overrides are intentional; reject duplicate authored fields before expansion.
        super().flatten_mapping(node)


@lru_cache(maxsize=16)
def _parse_runtime(
    *,
    package_id: str,
    runtime_bytes: bytes,
    authoring: bool = False,
) -> RulesetRuntime:
    """Parse and validate exact immutable package bytes once per owner process."""

    if len(runtime_bytes) > PACKAGE_RUNTIME_BYTE_LIMIT:
        raise PackageRuntimeError("Package runtime exceeds the byte limit")
    try:
        text = runtime_bytes.decode("utf-8", errors="strict")
        raw = yaml.load(text, Loader=_UniqueRuntimeLoader if authoring else _BoundedRuntimeLoader)
    except YamlShapeError as error:
        raise PackageRuntimeError(str(error)) from error
    except (UnicodeDecodeError, yaml.YAMLError, ValueError, TypeError, RecursionError) as error:
        raise PackageRuntimeError("Package runtime YAML is invalid") from error
    if not isinstance(raw, dict):
        raise PackageRuntimeError("Package runtime must be an object")
    _validate_shape(raw)
    if authoring:
        try:
            validate_content_shape("package_runtime", raw, file=PACKAGE_RUNTIME_PATH)
        except ContentShapeError as error:
            raise PackageRuntimeError(
                "Package runtime fields are invalid",
                diagnostics=error.diagnostics,
            ) from error
        executable_errors = runtime_authoring_shape_errors(raw)
        if executable_errors:
            raise PackageRuntimeError(
                "Package runtime effect fields are invalid",
                diagnostics=tuple(
                    RulesetDiagnostic(
                        code="runtime_effect_field_invalid",
                        phase="runtime",
                        file=PACKAGE_RUNTIME_PATH,
                        path=path,
                        message=message,
                        guidance="Read the package_runtime executable definition for this field.",
                    )
                    for path, message in executable_errors
                ),
            )
    if validate_parameter_declarations(raw):
        raise PackageRuntimeError("Package runtime parameter declarations are invalid")
    try:
        raw = resolve_parameter_declarations(raw, validate_shape=_validate_shape)
    except ParameterDeclarationError as error:
        raise PackageRuntimeError(str(error)) from error
    _validate_shape(raw)
    descriptor = raw.get("sylvia_gm_runtime")
    if not isinstance(descriptor, dict) or set(descriptor) != {"format", "state_namespace"}:
        raise PackageRuntimeError("Package runtime descriptor is invalid")
    if descriptor["format"] != _RUNTIME_FORMAT or descriptor["state_namespace"] != package_id:
        raise PackageRuntimeError("Package runtime contract does not match its manifest")
    if "meta" in raw:
        raise PackageRuntimeError("Package runtime repeats manifest-owned identity metadata")
    try:
        model = RulesetFile.model_validate(raw)
    except ValidationError as error:
        raise PackageRuntimeError("Package runtime configuration schema is invalid") from error
    if any(issue.severity == "error" for issue in model.validate()):
        raise PackageRuntimeError("Package runtime cross-references are invalid")
    if validate_ruleset_data(raw):
        raise PackageRuntimeError("Package runtime executable layer is invalid")
    try:
        runtime = RulesetRuntime(None, raw)
    except (KeyError, TypeError, ValueError) as error:
        raise PackageRuntimeError("Package runtime executable layer could not load") from error
    _validate_trigger_links(runtime)
    _validate_static_call_graph(runtime)
    return runtime


def _validate_runtime_declaration(
    runtime: RulesetRuntime,
    declaration: ToolDeclaration,
) -> None:
    tool = runtime.tools.get(declaration.name)
    if tool is None or not _schemas_agree(_runtime_parameter_schema(tool), declaration):
        raise PackageRuntimeError("Package runtime tool does not match its manifest declaration")


def validate_package_runtime(
    *,
    package_id: str,
    declarations: Sequence[ToolDeclaration],
    runtime_bytes: bytes,
) -> None:
    """Validate all exposed runtime tools before package candidate publication."""

    runtime = _parse_runtime(package_id=package_id, runtime_bytes=runtime_bytes, authoring=True)
    declared_names = {declaration.name for declaration in declarations}
    if len(declared_names) != len(declarations):
        raise PackageRuntimeError("Package runtime manifest tool identities are duplicated")
    for declaration in declarations:
        _validate_runtime_declaration(runtime, declaration)
        metadata = runtime.tools[declaration.name].metadata
        if "activity_label" in metadata:
            try:
                validate_activity_label(
                    metadata["activity_label"],
                    path=f"$.tools.{declaration.name}.metadata.activity_label",
                )
            except ContentShapeError as error:
                raise PackageRuntimeError(
                    "Package activity label is invalid", diagnostics=error.diagnostics,
                ) from error
        if "result_card" in metadata:
            try:
                validate_result_card_declaration(
                    metadata["result_card"],
                    path=f"$.tools.{declaration.name}.metadata.result_card",
                    tool_names=declared_names,
                )
            except ContentShapeError as error:
                raise PackageRuntimeError(
                    "Package result-card declaration is invalid",
                    diagnostics=error.diagnostics,
                ) from error


def package_tool_description(
    *,
    package_id: str,
    declaration: ToolDeclaration,
    runtime_bytes: bytes,
) -> str:
    """Return the package-authored model-facing purpose for one admitted tool."""

    runtime = _parse_runtime(package_id=package_id, runtime_bytes=runtime_bytes)
    _validate_runtime_declaration(runtime, declaration)
    return str(runtime.tools[declaration.name].description).strip()


def package_tool_model_behavior(
    *,
    package_id: str,
    declaration: ToolDeclaration,
    runtime_bytes: bytes,
) -> tuple[ModelToolBehavior, tuple[StatePredicate, ...]]:
    """Return the Ruleset-authored model behavior for one admitted mechanic."""

    runtime = _parse_runtime(package_id=package_id, runtime_bytes=runtime_bytes)
    _validate_runtime_declaration(runtime, declaration)
    raw: object = runtime.tools[declaration.name].metadata.get(
        "model_behavior", ModelToolBehavior.OPTIONAL.value
    )
    raw_predicates: object = ()
    if isinstance(raw, Mapping):
        raw_predicates = raw.get("state_predicates", ())
        raw = raw.get("kind")
    try:
        if not isinstance(raw, str):
            raise TypeError("model behavior kind must be a string")
        if not isinstance(raw_predicates, Sequence) or isinstance(
            raw_predicates, (str, bytes, bytearray)
        ):
            raise TypeError("model behavior state predicates must be a sequence")
        behavior = ModelToolBehavior(raw)
        predicates = tuple(
            StatePredicate(
                namespace=str(predicate["namespace"]),
                key=str(predicate["key"]),
                equals=owned_json_value(predicate["equals"]),
                negated=bool(predicate.get("negated", False)),
            )
            for predicate in raw_predicates
            if isinstance(predicate, Mapping)
        )
    except (KeyError, TypeError, ValueError) as error:  # pragma: no cover - validated earlier
        raise PackageRuntimeError("Package runtime model behavior is unsupported") from error
    return behavior, predicates


def package_model_decision_boundary(
    *,
    package_id: str,
    runtime_bytes: bytes,
) -> Mapping[str, object] | None:
    """Return the validated Ruleset-owned active-actor control projection."""

    runtime = _parse_runtime(package_id=package_id, runtime_bytes=runtime_bytes)
    declared = runtime.model_decision_scope or runtime.model_decision_boundary
    if declared is None:
        return None
    return cast(Mapping[str, object], _mutable_json(declared))


def package_tool_decision_title(
    *, package_id: str, declaration: ToolDeclaration, runtime_bytes: bytes,
) -> str:
    """Use only an authored player-facing result-card title to explain a native pause."""
    runtime = _parse_runtime(package_id=package_id, runtime_bytes=runtime_bytes)
    _validate_runtime_declaration(runtime, declaration)
    card = runtime.tools[declaration.name].metadata.get("result_card")
    title = card.get("title") if isinstance(card, Mapping) else None
    return title.strip() if isinstance(title, str) and 0 < len(title) <= 256 else ""


def package_tool_player_controls(
    *,
    package_id: str,
    declaration: ToolDeclaration,
    runtime_bytes: bytes,
) -> PlayerControls:
    """Read direct-input policy from the same exact package bytes as its mechanic."""

    runtime = _parse_runtime(package_id=package_id, runtime_bytes=runtime_bytes)
    return parse_player_controls(runtime.tools[declaration.name].metadata.get("player_controls"))


def _runtime_parameters(
    tool: ToolDef,
    parameters: SafePayload,
    bridge: AuthorityMemoryStateBridge,
) -> dict[str, Any]:
    """Use the same defaults and authority-bound values for checks and execution."""

    bound = cast(dict[str, Any], _mutable_json(parameters))
    for parameter in tool.parameters:
        if parameter.from_state is not None:
            bound[parameter.name] = _bound_runtime_parameter(bridge, parameter.from_state)
        elif parameter.name not in bound and parameter.default is not None:
            bound[parameter.name] = deepcopy(parameter.default)
    return bound


def materialize_player_action_checker(
    *,
    package_id: str,
    declaration: ToolDeclaration,
    runtime_bytes: bytes,
) -> PlayerActionChecker:
    """Check player prerequisites on copied state, without effects or raw context output."""

    runtime = _parse_runtime(package_id=package_id, runtime_bytes=runtime_bytes)
    tool = runtime.tools[declaration.name]
    policy = parse_player_controls(tool.metadata.get("player_controls"))

    def check(parameters: SafePayload, state: SafePayload, participants: SafePayload) -> str | None:
        if not policy.available:
            return "This Ruleset's player controls are unavailable"
        try:
            bridge = AuthorityMemoryStateBridge(package_id, state, participants)
            prepared = prepare_tool_context(
                declaration.name,
                _runtime_parameters(tool, parameters, bridge),
                runtime.tools,
                bridge,
                runtime.ruleset_config,
                runtime.definitions,
                bridge.combat_state,
            )
            if isinstance(prepared, ToolResult):
                return "This action's prerequisites are not currently met"
            _prepare_player_control_values(policy, prepared, bridge)
            for item in policy.checks:
                if not evaluate_condition(item["condition"], prepared):
                    return item["reason"]
        except Exception:
            # An invalid expression or missing state cannot enable a control or expose its inputs.
            return "This action's availability could not be determined"
        return None

    return check


def _prepare_player_control_values(
    policy: PlayerControls,
    context: ExecutionContext,
    bridge: AuthorityMemoryStateBridge,
) -> dict[str, SafePayload]:
    """Read physical facts and calculate local scalars without executing effects or rolling dice."""

    resolved_queries = {}
    for declaration in policy.queries:
        body, name = declaration["spatial_query"], declaration["store_as"]
        if name in context.vars:
            raise PackageRuntimeError("Player query would replace a bound variable")
        query = _resolve_spatial_input(body["query"], context)
        if not isinstance(query, Mapping):
            raise PackageRuntimeError("Player query is unavailable")
        if body["operation"] == "path" and (
            type(query.get("max_steps")) is not int or not 1 <= query["max_steps"] <= 4096
        ):
            raise PackageRuntimeError("Player path query exceeds the selection limit")
        context.vars[name] = bridge.spatial_query(
            body["scene"],
            operation=body["operation"],
            query=query,
            snapshot=None,
        )
        bridge.register_spatial_private_variable(name)
        resolved_queries[name] = owned_payload(query)
    for calculation in policy.calculations:
        name = calculation["set"]["field"]
        if name in context.vars:
            raise PackageRuntimeError("Player calculation would replace a bound variable")
        value = None
        if evaluate_condition(calculation.get("condition", "true"), context):
            value = evaluate_expression(calculation["set"]["value"], context)
            if value is not None and not (
                isinstance(value, (bool, int, float, str))
                and (not isinstance(value, float) or math.isfinite(value))
            ):
                raise PackageRuntimeError("Player calculation did not return a scalar")
        context.vars[name] = value
    return resolved_queries


def materialize_player_action_previewer(
    *,
    package_id: str,
    declaration: ToolDeclaration,
    runtime_bytes: bytes,
) -> PlayerActionPreviewer:
    """Reuse declared calculations with player-known geometry, without revealing hidden detours."""

    runtime = _parse_runtime(package_id=package_id, runtime_bytes=runtime_bytes)
    tool = runtime.tools[declaration.name]
    policy = parse_player_controls(tool.metadata.get("player_controls"))

    def preview(
        parameters: SafePayload,
        state: SafePayload,
        participants: SafePayload,
    ) -> SafePayload | None:
        if not policy.available or (policy.map_preview is None and policy.preview is None):
            return None
        try:
            bridge = AuthorityMemoryStateBridge(
                package_id,
                state,
                participants,
                player_spatial_view=True,
            )
            prepared = prepare_tool_context(
                declaration.name,
                _runtime_parameters(tool, parameters, bridge),
                runtime.tools,
                bridge,
                runtime.ruleset_config,
                runtime.definitions,
                bridge.combat_state,
            )
            if isinstance(prepared, ToolResult):
                return None
            queries = _prepare_player_control_values(policy, prepared, bridge)
            map_preview = None
            if policy.map_preview is not None:
                scenes = {
                    item["store_as"]: bridge._spatial_engine(
                        item["spatial_query"]["scene"],
                    ).snapshot.as_payload()
                    for item in policy.queries
                }
                map_preview = project_spatial_preview(
                    policy.map_preview,
                    results=prepared.vars,
                    queries=queries,
                    scenes=scenes,
                )
            return owned_payload(
                {
                    "preview": project_result_card(
                        policy.preview,
                        parameters=parameters,
                        public_context={
                            item["set"]["field"]: prepared.vars[item["set"]["field"]]
                            for item in policy.calculations
                        },
                    ),
                    "map_preview": map_preview,
                }
            )
        except Exception:
            # Selection changes and unsupported declarations cannot expose a private exception.
            return None

    return preview


def materialize_package_mechanic(
    *,
    package_id: str,
    package_version: str,
    package_sha256: str,
    declaration: ToolDeclaration,
    runtime_bytes: bytes,
) -> Mechanic:
    """Compile one exact package declaration into a data-only mechanic closure."""

    if package_id == "wotc.srd.5.2.1":
        raise PackageRuntimeError("Full is archived; migrate this executable binding to SRD Lite before launch.")

    runtime = _parse_runtime(
        package_id=package_id,
        runtime_bytes=runtime_bytes,
    )
    _validate_runtime_declaration(runtime, declaration)
    tools = runtime.tools
    procedures = runtime.procedures
    private_runtime: tuple[dict[str, Any], dict[str, Any]] | None = None
    preparation_lock = threading.Lock()

    def mechanic(context: MechanicContext) -> MechanicOutcome:
        # Catalog display needs validated metadata, not a private copy for every unused tool.
        # Retain one isolated copy per mechanic, initialized safely on its first execution.
        nonlocal private_runtime
        with preparation_lock:
            if private_runtime is None:
                private_runtime = (
                    deepcopy(runtime.ruleset_config), deepcopy(runtime.definitions),
                )
            ruleset_config, definitions = private_runtime
        bridge = AuthorityMemoryStateBridge(package_id, context.state, context.participants)
        parameters = _runtime_parameters(tools[declaration.name], context.parameters, bridge)
        choice_control = (
            None
            if context.choice_resolution is None
            else ChoiceResolutionControl(
                choice_id=context.choice_resolution.choice_id,
                option_id=context.choice_resolution.option_id,
                expected_request=RuntimeChoiceRequest(
                    choice_id=context.choice_resolution.expected_request.choice_id,
                    prompt=context.choice_resolution.expected_request.prompt,
                    options=tuple(
                        RuntimeChoiceOption(
                            option_id=option.option_id,
                            label=option.label,
                        )
                        for option in context.choice_resolution.expected_request.options
                    ),
                ),
                prior_answers=tuple(
                    (RuntimeChoiceRequest(answer.request.choice_id, answer.request.prompt, tuple(
                        RuntimeChoiceOption(option.option_id, option.label)
                        for option in answer.request.options
                    )), answer.option_id)
                    for answer in context.choice_resolution.prior_answers
                ),
            )
        )
        runtime_extras = (
            {} if choice_control is None else {"mechanics_choice_resolution": choice_control}
        )

        def procedure(
            trigger_name: str,
            trigger_context: dict[str, Any],
            parent: ExecutionContext,
        ) -> object:
            return trigger_procedure(
                trigger_name,
                procedures,
                tools,
                bridge,
                ruleset_config,
                definitions,
                context=trigger_context,
                combat_state=bridge.combat_state,
                budget=parent.budget,
            )

        def nested(
            tool_name: str,
            nested_parameters: dict[str, Any],
            _parent: ExecutionContext,
        ) -> ToolResult:
            if tool_name in tools:
                nested_parameters = _runtime_parameters(tools[tool_name], nested_parameters, bridge)
            return execute_tool(
                tool_name,
                nested_parameters,
                tools,
                bridge,
                ruleset_config,
                definitions,
                bridge.combat_state,
                tool_executor=nested,
                procedure_executor=procedure,
                extras=runtime_extras,
                budget=_parent.budget,
            )

        seed_basis = canonical_json_bytes(
            {
                "campaign_id": context.campaign_id,
                "turn_id": context.turn_id,
                "action_id": context.action_id,
                "package_id": package_id,
                "package_version": package_version,
                "package_sha256": package_sha256,
                "tool_name": declaration.name,
                "parameters": parameters,
            }
        )
        seed = int.from_bytes(hashlib.sha256(seed_basis).digest()[:8], "big")
        with _DICE_EXECUTION_LOCK:
            from sylvia_gm.dice import set_seed

            set_seed(seed)
            try:
                result = execute_tool(
                    declaration.name,
                    parameters,
                    tools,
                    bridge,
                    ruleset_config,
                    definitions,
                    bridge.combat_state,
                    tool_executor=nested,
                    procedure_executor=procedure,
                    extras=runtime_extras,
                )
            except MechanicsChoiceSuspended as suspended:
                if choice_control is not None and not choice_control.consumed:
                    raise PackageRuntimeError(
                        "Package did not consume the authorized choice"
                    ) from suspended
                request = suspended.request
                if not isinstance(request, RuntimeChoiceRequest):
                    raise PackageRuntimeError("Package choice request is invalid") from suspended
                return MechanicOutcome(
                    result=cast(
                        Mapping[str, JsonValue],
                        _json_safe({"status": "awaiting_choice", "tool": declaration.name}),
                    ),
                    choice=MechanicsChoiceRequest(
                        choice_id=request.choice_id,
                        prompt=request.prompt,
                        options=tuple(
                            MechanicsChoiceOption(
                                option_id=option.option_id,
                                label=option.label,
                            )
                            for option in request.options
                        ),
                    ),
                )
        if not result.success:
            if result.failure_kind == "precondition" and result.error is not None:
                raise RulesetPreconditionRejected(result.error)
            raise PackageRuntimeError("Package mechanic execution failed")
        if choice_control is not None and not choice_control.consumed:
            raise PackageRuntimeError("Package mechanic did not consume the authorized choice")
        if result.private_random_sampled and result.public_context is None:
            raise PackageRuntimeError("Hidden random results require explicit publication")
        if (runtime.model_decision_boundary
                and runtime.model_decision_boundary.get("isolated_participant")
                and result.public_context is None):
            raise PackageRuntimeError("Isolated participant actions require explicit publication")
        published_context = result.context if result.public_context is None else result.public_context
        visible_context = {
            key: value
            for key, value in published_context.items()
            if key not in bridge.spatial_private_variables
        }
        result_payload: dict[str, object] = {
            "status": "committed",
            "tool": result.tool_name,
            "context": visible_context,
            "narrative_hooks": result.narrative_hooks,
            "narrative_directives": result.narrative_directives,
            "logs": result.logs,
        }
        if bridge.spatial_evidence:
            result_payload["spatial_evidence"] = list(bridge.spatial_evidence)
        if bridge.shot_presentation["shots"] or bridge.shot_presentation["unavailable"]:
            result_payload["shot_effects"] = bridge.shot_presentation
        safe_result = cast(
            Mapping[str, JsonValue],
            _json_safe(result_payload),
        )
        card = project_result_card(
            tools[declaration.name].metadata.get("result_card"),
            parameters=parameters,
            public_context=cast(Mapping[str, object], safe_result["context"]),
        )
        activity_label = project_activity_label(
            tools[declaration.name].metadata.get("activity_label"),
            parameters=parameters,
            public_context=cast(Mapping[str, object], safe_result["context"]),
        )
        if activity_label is not None:
            safe_result = {**safe_result, "activity_label": activity_label}
            if card is not None and not any(
                card.get(key) for key in
                ("summary", "details", "actions", "equation", "equations", "subtitle")
            ):
                # A quiet lifecycle transition is already represented by its
                # activity label. Keep cards that contain actual saved results.
                card = None
        if "result_card" in tools[declaration.name].metadata:
            safe_result = {
                **safe_result,
                "result_card": None if card is None else cast(JsonValue, dict(card)),
            }
        consequences = tuple(
            cast(
                Mapping[str, JsonValue],
                _json_safe({"kind": "runtime_log", **log}),
            )
            for log in result.logs
        ) + tuple(
            cast(
                Mapping[str, JsonValue],
                _json_safe({"kind": "spatial_query", "evidence": evidence}),
            )
            for evidence in bridge.spatial_evidence
        )
        return MechanicOutcome(
            result=safe_result,
            mutations=bridge.mutations(),
            participant_mutations=bridge.participant_mutations(),
            consequences=consequences,
        )

    return mechanic


__all__ = (
    "PACKAGE_RUNTIME_BYTE_LIMIT",
    "PACKAGE_RUNTIME_PATH",
    "PackageRuntimeError",
    "materialize_package_mechanic",
    "package_tool_description",
    "package_tool_model_behavior",
    "package_model_decision_boundary",
    "validate_package_runtime",
)
