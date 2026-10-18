"""Exception hierarchy for the state store subsystem.

All exceptions inherit from ``StateStoreError`` so the session runner
can catch everything with one ``except`` clause and call ``store.reload()``
to discard all in-memory mutations.
"""

from __future__ import annotations


class StateStoreError(Exception):
    """Base exception for all state store errors."""


class StateFileNotFoundError(StateStoreError):
    """The state.json file does not exist at the expected path."""


class StateValidationError(StateStoreError):
    """State data failed schema or custom validation.

    Attributes:
        errors: List of human-readable error messages.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        msg = "State validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        super().__init__(msg)


class StateCorruptionError(StateStoreError):
    """State file is corrupted and cannot be recovered."""


class StateMigrationError(StateStoreError):
    """Cannot migrate from current schema version to the required version."""


class EntityNotFoundError(StateStoreError):
    """The requested entity ID does not exist in the store.

    Attributes:
        entity_id: The ID that was not found.
        entity_type: Type hint (e.g., 'character', 'monster', 'npc', 'quest').
    """

    def __init__(self, entity_id: str, entity_type: str = "entity") -> None:
        self.entity_id = entity_id
        self.entity_type = entity_type
        super().__init__(f"{entity_type.capitalize()} {entity_id!r} not found")


class DuplicateEntityError(StateStoreError):
    """Attempted to create an entity with an ID that already exists.

    Attributes:
        entity_id: The duplicate ID.
        entity_type: Type hint.
    """

    def __init__(self, entity_id: str, entity_type: str = "entity") -> None:
        self.entity_id = entity_id
        self.entity_type = entity_type
        super().__init__(
            f"{entity_type.capitalize()} {entity_id!r} already exists"
        )


class CombatAlreadyActiveError(StateStoreError):
    """Attempted to init_combat() while combat is already active."""


class NoActiveCombatError(StateStoreError):
    """Combat operation called when no combat is active."""


class InsufficientResourceError(StateStoreError):
    """Not enough of a resource to spend.

    Attributes:
        resource: Name of the resource.
        available: How much is available.
        requested: How much was requested.
    """

    def __init__(
        self, resource: str, available: int, requested: int
    ) -> None:
        self.resource = resource
        self.available = available
        self.requested = requested
        super().__init__(
            f"Insufficient {resource}: "
            f"requested {requested}, available {available}"
        )


class InsufficientFundsError(StateStoreError):
    """Not enough gold to spend.

    Attributes:
        available: How much gold is available.
        requested: How much was requested.
        shortfall: How much more is needed.
    """

    def __init__(self, available: int, requested: int) -> None:
        self.available = available
        self.requested = requested
        self.shortfall = requested - available
        super().__init__(
            f"Insufficient funds: requested {requested}, "
            f"available {available} (shortfall {self.shortfall})"
        )


class InvalidOperationError(StateStoreError):
    """Operation is not valid in the current state."""


class NoEntriesToUndoError(StateStoreError):
    """No changelog entries exist to undo."""


class ChangelogIntegrityError(StateStoreError):
    """Changelog consistency check failed."""


class RulesetNotSupportedError(StateStoreError):
    """Operation is not supported by the active ruleset.

    Attributes:
        operation: The operation name.
        ruleset: The active ruleset.
    """

    def __init__(self, operation: str, ruleset: str) -> None:
        self.operation = operation
        self.ruleset = ruleset
        super().__init__(
            f"Operation {operation!r} is not supported "
            f"by ruleset {ruleset!r}"
        )
