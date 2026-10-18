"""Legacy file-backed state API and compatibility exports.

StateAPI validates and saves the retained JSON state model. Current desktop
campaign mutations go through application.authority.AuthorityOwner and
infrastructure.sqlite_authority.SQLiteAuthorityStore.

Compatibility usage::

    from sylvia_gm.state import StateAPI

    store = StateAPI.create(Path("campaign/state.json"),
                           {"id": "my-campaign", "name": "My Campaign"})
    store.create_character("hero", name="Hero", kind="pc", species="Human",
                           archetype="Fighter", max_hp=20, ac=16,
                           abilities={"str": 14, "dex": 12, "wil": 10})
    store.save()
"""

from __future__ import annotations

# Exception hierarchy — re-export for convenience
from ._exceptions import (
    ChangelogIntegrityError,
    CombatAlreadyActiveError,
    DuplicateEntityError,
    EntityNotFoundError,
    InsufficientFundsError,
    InsufficientResourceError,
    InvalidOperationError,
    NoActiveCombatError,
    NoEntriesToUndoError,
    RulesetNotSupportedError,
    StateCorruptionError,
    StateFileNotFoundError,
    StateMigrationError,
    StateStoreError,
    StateValidationError,
)

# Ruleset config — re-export for T1.4
from ._ruleset_config import (
    RULESET_CONFIG,
    VALID_CONDITIONS,
    get_ruleset_config,
)
from .state_api import StateAPI

__all__ = [
    "StateAPI",
    # Exceptions
    "StateStoreError",
    "StateFileNotFoundError",
    "StateValidationError",
    "StateCorruptionError",
    "StateMigrationError",
    "EntityNotFoundError",
    "DuplicateEntityError",
    "CombatAlreadyActiveError",
    "NoActiveCombatError",
    "InsufficientResourceError",
    "InsufficientFundsError",
    "InvalidOperationError",
    "NoEntriesToUndoError",
    "ChangelogIntegrityError",
    "RulesetNotSupportedError",
    # Config
    "RULESET_CONFIG",
    "VALID_CONDITIONS",
    "get_ruleset_config",
]
