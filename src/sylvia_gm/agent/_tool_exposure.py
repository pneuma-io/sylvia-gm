"""Decide which Ruleset tools the GM model is shown in the current campaign state.

A Ruleset may annotate a tool's input schema with ``x-sylvia-gm-exposure``. The annotation lists
conditions, any one of which makes the tool relevant now. A tool whose conditions all fail is
left out of the model's tool list. This is presentation only: the authority catalog, admission and
schema validation are unchanged, so a call is never refused because of this annotation.

A Ruleset should only declare a condition when the tool cannot succeed or has no effect without
it. Conditions are deliberately simple and Ruleset-neutral:

``{"spells": [spell_id, ...]}``
    True when any listed spell can be cast by a current participant or prepared creature, or when
    any listed spell id appears as a string anywhere in the campaign state. Active effects keep
    the id of the spell that created them, so a lasting effect keeps its follow-up tools visible
    after its caster is gone.
``{"entity_field": "dotted.path", "op": "not_null" | "true" | "gte1"}``
    True when any saved entity in a ``*.entities`` state root has that field and it satisfies the
    operator.

A schema without the annotation, or with a condition this host does not understand, is always
shown, so older packages and other Rulesets behave exactly as before.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

EXPOSURE_KEY = "x-sylvia-gm-exposure"
SPELL_SCOPE_KEY = "x-sylvia-gm-spell-scope"
_ENTITY_OPERATORS = frozenset({"not_null", "true", "gte1"})


def _string_list(value: object) -> tuple[str, ...] | None:
    if (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
        and value
        and all(isinstance(item, str) and item for item in value)
    ):
        return tuple(value)
    return None


def exposure_conditions(schema: Mapping[str, object]) -> tuple[Mapping[str, object], ...] | None:
    """Return the declared conditions, or ``None`` when the tool is always shown."""

    raw = schema.get(EXPOSURE_KEY)
    if not isinstance(raw, Mapping):
        return None
    conditions = raw.get("any_of")
    if (
        not isinstance(conditions, Sequence)
        or isinstance(conditions, (str, bytes, bytearray))
        or not conditions
        or not all(isinstance(item, Mapping) for item in conditions)
    ):
        return None
    for condition in conditions:
        if not _understood(condition):
            return None
    return tuple(conditions)


def _understood(condition: Mapping[str, object]) -> bool:
    if set(condition) == {"spells"}:
        return _string_list(condition["spells"]) is not None
    if set(condition) == {"entity_field", "op"}:
        path = condition["entity_field"]
        return (
            isinstance(path, str)
            and bool(path)
            and all(path.split("."))
            and condition["op"] in _ENTITY_OPERATORS
        )
    return False


def exposure_spell_ids(schemas: Iterable[Mapping[str, object]]) -> frozenset[str]:
    """Collect every spell id any exposure condition asks about."""

    found: set[str] = set()
    for schema in schemas:
        for condition in exposure_conditions(schema) or ():
            spells = _string_list(condition.get("spells"))
            if spells is not None:
                found.update(spells)
    return frozenset(found)


def spell_ids_in_state(state: Mapping[str, object], wanted: frozenset[str]) -> frozenset[str]:
    """Find which of *wanted* appear as string values anywhere in campaign state."""

    if not wanted:
        return frozenset()
    found: set[str] = set()
    stack: list[object] = list(state.values())
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            if value in wanted:
                found.add(value)
        elif isinstance(value, Mapping):
            stack.extend(value.values())
        elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            stack.extend(value)
    return frozenset(found)


def _entities(state: Mapping[str, object]) -> Iterable[Mapping[str, object]]:
    for key, root in state.items():
        if key.endswith(".entities") and isinstance(root, Mapping):
            for entity in root.values():
                if isinstance(entity, Mapping):
                    yield entity


def _field(entity: Mapping[str, object], path: str) -> tuple[bool, object]:
    value: object = entity
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return False, None
        value = value[part]
    return True, value


def _entity_field_holds(state: Mapping[str, object], path: str, operator: str) -> bool:
    for entity in _entities(state):
        present, value = _field(entity, path)
        if not present:
            continue
        if operator == "not_null" and value is not None:
            return True
        if operator == "true" and value is True:
            return True
        if (
            operator == "gte1"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value >= 1
        ):
            return True
    return False


class ExposureContext:
    """Evaluate exposure conditions against one campaign state, scanning it at most once."""

    def __init__(
        self,
        state: Mapping[str, object],
        *,
        castable_spell_ids: frozenset[str],
        asked_spell_ids: frozenset[str],
    ) -> None:
        self._state = state
        self._present_spells = castable_spell_ids | spell_ids_in_state(
            state, asked_spell_ids - castable_spell_ids
        )

    def exposed(self, schema: Mapping[str, object]) -> bool:
        conditions = exposure_conditions(schema)
        if conditions is None:
            return True
        for condition in conditions:
            spells = _string_list(condition.get("spells"))
            if spells is not None:
                if any(spell in self._present_spells for spell in spells):
                    return True
                continue
            if _entity_field_holds(
                self._state, str(condition["entity_field"]), str(condition["op"])
            ):
                return True
        return False


def scoped_spell_choices(
    enum: Sequence[object],
    *,
    castable_spell_ids: frozenset[str] | None,
    scope: object,
    aliases: object,
) -> list[object]:
    """Narrow one ``spell`` choice list to castable spells this tool accepts.

    *scope* is the optional ``x-sylvia-gm-spell-scope`` list. An alias stays when the spell it
    names stays, so a narrowed list keeps the display names the Ruleset declared for it.
    """

    allowed: set[str] | None = (
        set(castable_spell_ids) if castable_spell_ids is not None else None
    )
    scope_ids = _string_list(scope)
    if scope_ids is not None:
        allowed = set(scope_ids) if allowed is None else allowed & set(scope_ids)
    if allowed is None:
        return list(enum)
    alias_targets = (
        {str(key): str(value) for key, value in aliases.items()}
        if isinstance(aliases, Mapping)
        else {}
    )
    return [
        value
        for value in enum
        if isinstance(value, str)
        and (value in allowed or alias_targets.get(value) in allowed)
    ]
