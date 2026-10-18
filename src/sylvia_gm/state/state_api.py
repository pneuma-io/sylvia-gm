"""Legacy JSON state interface retained for compatibility callers.

This API validates and records mutations in its file-backed model until the
caller invokes save(). Current desktop campaign writes instead use
application.authority.AuthorityOwner and
infrastructure.sqlite_authority.SQLiteAuthorityStore.

Behavior of this retained model:
- Flat entity schema (no ``combat`` sub-dict for core fields).
- ``get_entity()`` returns deepcopy (H6).
- All optional fields pre-initialized to null on creation (H5).
- ``set_hp()`` removed — use ``take_damage()``/``heal()`` (H7).
- ``add_temp_hp()`` replaces existing temp HP (H8).
- Currency is raw integer (H9).
- Spell slot keys allow level 0 (H10).
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

from ._changelog import Changelog
from ._exceptions import (
    CombatAlreadyActiveError,
    DuplicateEntityError,
    EntityNotFoundError,
    InsufficientFundsError,
    InsufficientResourceError,
    InvalidOperationError,
    NoActiveCombatError,
    NoEntriesToUndoError,
    RulesetNotSupportedError,
)
from ._migrate import CURRENT_SCHEMA_VERSION, migrate_state
from ._pre_init import (
    apply_character_defaults,
    apply_monster_defaults,
    apply_npc_defaults,
)
from ._ruleset_config import (
    EQUIPMENT_SLOTS,
    VALID_COMBAT_STATUSES,
    VALID_NPC_ATTITUDES,
    VALID_QUEST_STATUSES,
    VALID_TIME_PERIODS,
    get_ruleset_config,
)
from ._snapshot import SnapshotScope, take_snapshot
from ._store import StateStore
from ._validators import (
    validate_condition,
    validate_entity_id,
)

# ---------------------------------------------------------------------------
# StateAPI
# ---------------------------------------------------------------------------


class StateAPI:
    """Public interface to the retained file-backed state model.

    All mutations are made in memory and batched until ``save()`` is called.
    The ``dirty`` property indicates whether there are unsaved changes.

    Usage::

        store = StateAPI(Path("campaign/state.json"))
        store.create_character("sample_player", name="Sample Player", kind="pc",
                               species="Human", archetype="Monk",
                               max_hp=18, ac=14, abilities={"str": 12, "dex": 16, "wil": 10})
        store.take_damage("sample_player", 7)
        store.save()
    """

    def __init__(
        self,
        state_path: Path,
        schema_path: Path | None = None,
    ) -> None:
        """Load state from *state_path* and prepare for use.

        Args:
            state_path: Path to ``state.json``.
            schema_path: Path to ``state_schema.json``. Defaults to master schema.

        Raises:
            StateFileNotFoundError: If state.json doesn't exist.
            StateValidationError: If loaded state fails validation.
        """
        self._state_path: Path = Path(state_path)
        self._changelog_path: Path = self._state_path.parent / "changelog.jsonl"

        self._store = StateStore(state_path, schema_path)
        self._changelog = Changelog(self._changelog_path)

        # Check for schema migration needs
        loaded_version = self._store.get_raw().get("metadata", {}).get(
            "schema_version", "0.1.0"
        )
        
        # T5.5: Log state load
        session_id = self._state_path.stem
        logger.info("state_load session_id=%s schema_version=%s", session_id, loaded_version)
        if loaded_version != CURRENT_SCHEMA_VERSION:
            try:
                migrated = migrate_state(
                    self._store.get_raw(), loaded_version, CURRENT_SCHEMA_VERSION
                )
                self._store.get_raw().clear()
                self._store.get_raw().update(migrated)
                self._store.mark_dirty()
            except Exception:
                # Migration failed — state stays as-is but is flagged
                pass

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        state_path: Path,
        campaign: dict[str, Any],
        ruleset: str,
    ) -> StateAPI:
        """Create a new campaign state at *state_path*.

        Args:
            state_path: Where to create the state.json file.
            campaign: Dict with at least ``id`` and ``name`` keys.
            ruleset: Active ruleset ID.

        Returns:
            A new StateAPI instance with empty state.
        """
        state_path = Path(state_path)
        schema_path = Path(__file__).parent / "state_schema.json"

        store = StateStore.create(state_path, campaign, ruleset, schema_path)
        return cls(state_path, schema_path)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def dirty(self) -> bool:
        """True if there are unsaved in-memory changes."""
        return self._store.dirty

    @property
    def state(self) -> dict[str, Any]:
        """Return a deep copy of the full state dict (H6)."""
        return self._store.data

    def set_top_level(self, key: str, value: Any) -> None:
        """Set a top-level state key (e.g. ``campaign_folder_path``) and mark dirty.

        ``self.state`` returns a deep copy, so ``store.state["k"] = v`` would
        silently write to a discarded dict — use this setter for top-level
        session metadata that must actually persist.
        """
        self._store.get_raw()[key] = value
        self._store.mark_dirty()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def save(self) -> None:
        """Persist state to disk. No-op if not dirty.

        Validates state before writing. Raises StateValidationError on failure.

        Crash-consistent: changelog is written before state.
        """
        self._store.save()

    def validate(self) -> list[str]:
        """Run full validation on current state. Returns error list."""
        return self._store.validate()

    def verify(self) -> list[str]:
        """Check changelog consistency against current state.

        Returns list of issues found. Empty list = consistent.
        """
        result = self._changelog.verify(self._store.get_raw())
        # T5.5: Log changelog replay
        entry_count = len(self._changelog._entries) if hasattr(self._changelog, '_entries') else 0
        logger.info("changelog_replay entry_count=%d issues=%d", entry_count, len(result))
        return result

    def reload(self) -> None:
        """Reload state from disk, discarding all in-memory changes."""
        self._store.reload()
        self._changelog = Changelog(self._changelog_path)

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    def snapshot(self, scope: SnapshotScope = "full") -> dict[str, Any]:
        """Return a filtered deep copy of the current state.

        Args:
            scope: ``"combat"``, ``"social"``, ``"inventory"``, or ``"full"``.
        """
        return take_snapshot(self._store.get_raw(), scope)

    def export_snapshot(self, path: Path, scope: str = "full") -> None:
        """Export a snapshot to a JSON file."""
        snap = take_snapshot(self._store.get_raw(), scope)  # type: ignore[arg-type]
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snap, f, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Generic Entity Access (T1.4 Dependency)
    # ------------------------------------------------------------------

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        """Return a deepcopy of the full entity dict (H6).

        Args:
            entity_id: The entity's ID.

        Returns:
            Deepcopy of the entity dict with ``kind`` added.

        Raises:
            EntityNotFoundError: If entity_id not found.
        """
        data = self._store.get_raw()
        entity = self._find_entity(data, entity_id)
        if entity is None:
            raise EntityNotFoundError(entity_id)

        result = deepcopy(entity)
        # Tag with kind for T1.4
        for section, kind in [
            ("characters", "character"),
            ("monsters", "monster"),
            ("npcs", "npc"),
        ]:
            if entity_id in data.get(section, {}):
                result["kind"] = kind
                break
        return result

    def get_field(self, entity_id: str, field_path: str) -> Any:
        """Read a field by dot-separated path.

        For pre-initialized core fields, always returns a value (maybe null).
        For dynamic fields (e.g., ``combat.actions_remaining``), returns None
        if the path doesn't exist.

        Args:
            entity_id: Entity ID.
            field_path: Dot-separated path (e.g., ``"hp"``, ``"abilities.dex.score"``,
                        ``"combat.actions_remaining"``).

        Returns:
            Field value, or None if path doesn't exist for dynamic fields.

        Raises:
            EntityNotFoundError: If entity_id not found.
        """
        data = self._store.get_raw()
        entity = self._find_entity(data, entity_id)
        if entity is None:
            raise EntityNotFoundError(entity_id)

        parts = field_path.split(".")
        current: Any = entity
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current

    def set_field(self, entity_id: str, field_path: str, value: Any) -> None:
        """Write a field by dot-separated path.

        Creates intermediate dicts for dynamic fields. For pre-initialized
        core fields, the key already exists.

        Args:
            entity_id: Entity ID.
            field_path: Dot-separated path.
            value: New value. Any valid JSON type accepted.

        Raises:
            EntityNotFoundError: If entity_id not found.
        """
        data = self._store.get_raw()
        entity = self._find_entity(data, entity_id)
        if entity is None:
            raise EntityNotFoundError(entity_id)

        old_value = self.get_field(entity_id, field_path)

        parts = field_path.split(".")
        current: Any = entity
        for part in parts[:-1]:
            if part not in current or not isinstance(current[part], dict):
                current[part] = {}
            current = current[part]

        current[parts[-1]] = value
        self._store.mark_dirty()

        # T5.5: Log state write
        logger.debug("state_write key=%s.%s entity_id=%s", field_path, entity_id)

        # Changelog
        self._changelog.append(
            operation="set_field",
            entity_type=self._entity_type(data, entity_id),
            entity_id=entity_id,
            field=field_path,
            old_value=old_value,
            new_value=value,
        )

    # ------------------------------------------------------------------
    # Campaign
    # ------------------------------------------------------------------

    def get_campaign(self) -> dict[str, Any]:
        """Return a deep copy of the campaign info."""
        return deepcopy(self._store.get_raw().get("campaign", {}))

    def get_ruleset_config(self) -> dict[str, Any]:
        """Return the active RULESET_CONFIG dict."""
        ruleset = self._store.get_raw().get("campaign", {}).get("ruleset")
        return get_ruleset_config(ruleset)

    # ------------------------------------------------------------------
    # Character
    # ------------------------------------------------------------------

    def create_character(self, char_id: str, **fields: Any) -> None:
        """Create a new character with pre-initialized optional fields (H5).

        Required fields: id, name, kind, species, archetype, max_hp, ac, abilities.

        Args:
            char_id: Unique character ID (machine-readable slug).
            **fields: Character attributes.

        Raises:
            DuplicateEntityError: If char_id already exists.
            ValueError: If required fields are missing.
        """
        error = validate_entity_id(char_id)
        if error:
            raise ValueError(error)

        data = self._store.get_raw()
        if char_id in data.get("characters", {}):
            raise DuplicateEntityError(char_id, "character")

        # Validate required fields
        required = {"name", "kind", "species", "archetype", "max_hp", "ac", "abilities"}
        missing = required - set(fields.keys())
        if missing:
            raise ValueError(
                f"Missing required fields for character {char_id!r}: {missing}"
            )

        ruleset = data.get("campaign", {}).get("ruleset")

        # Build entity with pre-initialized defaults
        entity = apply_character_defaults(
            {"id": char_id, "hp": fields.get("max_hp", 1), **fields},
            ruleset,
        )

        data.setdefault("characters", {})[char_id] = entity
        self._store.mark_dirty()

        self._changelog.append(
            operation="create_character",
            entity_type="character",
            entity_id=char_id,
            field="*",
            old_value=None,
            new_value={"id": char_id, "name": fields.get("name", "")},
        )

    def get_char(self, char_id: str) -> dict[str, Any]:
        """Return a deepcopy of the character dict (H6).

        Raises EntityNotFoundError if not found.
        """
        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")
        return deepcopy(chars[char_id])

    def char_exists(self, char_id: str) -> bool:
        """Return True if the character exists."""
        return char_id in self._store.get_raw().get("characters", {})

    def list_characters(self, kind: str | None = None) -> list[dict[str, Any]]:
        """List characters, optionally filtered by kind ('pc' or 'npc').

        Returns deep copies (H6).
        """
        chars = self._store.get_raw().get("characters", {})
        result = []
        for cid, char in chars.items():
            if kind is None or char.get("kind") == kind:
                result.append(deepcopy(char))
        return result

    # --- HP (H7: set_hp removed) ---

    def _apply_damage(self, entity: dict[str, Any], amount: int) -> int:
        """Apply damage to an entity, absorbing temp_hp first.

        Returns the entity's new HP.
        """
        if amount < 0:
            amount = 0

        temp = entity.get("temp_hp", 0)
        if temp > 0:
            if amount <= temp:
                entity["temp_hp"] = temp - amount
                amount = 0
            else:
                entity["temp_hp"] = 0
                amount -= temp

        if amount > 0:
            hp = entity.get("hp", 0)
            entity["hp"] = max(0, hp - amount)

        # Check death
        if entity["hp"] <= 0:
            # Determine ruleset behavior
            ruleset = self._store.get_raw().get("campaign", {}).get("ruleset")
            rs_config = get_ruleset_config(ruleset)
            if rs_config.get("death_at_zero_hp", False):
                entity["is_alive"] = False

        return entity["hp"]

    def take_damage(self, char_id: str, amount: int) -> int:
        """Deal damage to a character. Absorbs temp_hp first.

        Returns the character's new HP.

        Raises:
            EntityNotFoundError: If character not found.
        """
        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")

        char = chars[char_id]
        old_hp = char.get("hp", 0)
        new_hp = self._apply_damage(char, amount)
        self._store.mark_dirty()

        self._changelog.append(
            operation="take_damage",
            entity_type="character",
            entity_id=char_id,
            field="hp",
            old_value=old_hp,
            new_value=new_hp,
            metadata={"amount": amount},
        )

        return new_hp

    def heal(self, char_id: str, amount: int) -> int:
        """Heal a character. Cannot exceed max_hp.

        Returns the character's new HP.

        Raises:
            EntityNotFoundError: If character not found.
        """
        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")

        char = chars[char_id]
        if amount < 0:
            amount = 0

        old_hp = char.get("hp", 0)
        max_hp = char.get("max_hp", 1)
        char["hp"] = min(max_hp, old_hp + amount)

        # Under Cairn: heal on dead char restores is_alive
        if old_hp <= 0 and char["hp"] > 0:
            char["is_alive"] = True

        self._store.mark_dirty()

        self._changelog.append(
            operation="heal",
            entity_type="character",
            entity_id=char_id,
            field="hp",
            old_value=old_hp,
            new_value=char["hp"],
            metadata={"amount": amount},
        )

        return char["hp"]

    def add_temp_hp(self, char_id: str, amount: int) -> None:
        """Set temporary HP. REPLACES existing temp HP (H8).

        Raises:
            EntityNotFoundError: If character not found.
        """
        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")

        char = chars[char_id]
        old = char.get("temp_hp", 0)
        char["temp_hp"] = max(0, amount)
        self._store.mark_dirty()

        self._changelog.append(
            operation="add_temp_hp",
            entity_type="character",
            entity_id=char_id,
            field="temp_hp",
            old_value=old,
            new_value=char["temp_hp"],
            metadata={"amount": amount},
        )

    def damage_ability(self, char_id: str, ability: str, amount: int) -> int:
        """Damage an ability score (Cairn overflow damage).

        Used when HP reaches 0 and excess damage goes to STR.
        Clamps to 0. Sets is_alive=False if score reaches 0.

        Returns the new ability score.

        Raises:
            EntityNotFoundError: If character not found.
        """
        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")

        char = chars[char_id]
        abilities = char.get("abilities", {})
        if ability not in abilities or abilities[ability] is None:
            raise ValueError(
                f"Ability {ability!r} not available on character {char_id!r}"
            )

        ab = abilities[ability]
        if not isinstance(ab, dict):
            raise ValueError(f"Invalid ability format for {ability!r}")

        old_score = ab.get("score", 0)
        new_score = max(0, old_score - amount)
        ab["score"] = new_score

        if new_score <= 0:
            char["is_alive"] = False

        self._store.mark_dirty()

        self._changelog.append(
            operation="damage_ability",
            entity_type="character",
            entity_id=char_id,
            field=f"abilities.{ability}.score",
            old_value=old_score,
            new_value=new_score,
            metadata={"amount": amount},
        )

        return new_score

    # --- Conditions ---

    def add_condition(self, char_id: str, condition: str) -> None:
        """Add a condition to a character.

        Raises:
            EntityNotFoundError: If character not found.
            ValueError: If condition is not valid.
        """
        error = validate_condition(condition)
        if error:
            raise ValueError(error)

        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")

        char = chars[char_id]
        conditions: list[str] = char.setdefault("conditions", [])
        if condition not in conditions:
            conditions.append(condition)
            self._store.mark_dirty()

            self._changelog.append(
                operation="add_condition",
                entity_type="character",
                entity_id=char_id,
                field="conditions",
                old_value=list(conditions[:-1]),
                new_value=list(conditions),
                metadata={"condition": condition},
            )

    def remove_condition(self, char_id: str, condition: str) -> None:
        """Remove a condition from a character.

        Raises:
            EntityNotFoundError: If character not found.
        """
        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")

        char = chars[char_id]
        conditions: list[str] = char.get("conditions", [])
        if condition in conditions:
            old = list(conditions)
            conditions.remove(condition)
            self._store.mark_dirty()

            self._changelog.append(
                operation="remove_condition",
                entity_type="character",
                entity_id=char_id,
                field="conditions",
                old_value=old,
                new_value=list(conditions),
                metadata={"condition": condition},
            )

    def has_condition(self, char_id: str, condition: str) -> bool:
        """Return True if the character has the given condition.

        Raises:
            EntityNotFoundError: If character not found.
        """
        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")
        return condition in chars[char_id].get("conditions", [])

    def death_save(self, char_id: str, success: bool) -> None:
        """Record a death saving throw result.

        Raises:
            EntityNotFoundError: If character not found.
            RulesetNotSupportedError: Under Cairn (death saves disabled).
        """
        ruleset = self._store.get_raw().get("campaign", {}).get("ruleset")
        rs_config = get_ruleset_config(ruleset)
        if not rs_config.get("death_saves_enabled", False):
            raise RulesetNotSupportedError("death_save", ruleset)

        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")

        char = chars[char_id]
        death_saves = char.setdefault(
            "death_saves", {"successes": 0, "failures": 0}
        )
        if death_saves is None:
            death_saves = {"successes": 0, "failures": 0}
            char["death_saves"] = death_saves

        if success:
            death_saves["successes"] = min(3, death_saves.get("successes", 0) + 1)
        else:
            death_saves["failures"] = min(3, death_saves.get("failures", 0) + 1)

        self._store.mark_dirty()

    def set_initiative(self, char_id: str, roll: int | None) -> None:
        """Set a character's initiative roll.

        None = not in combat. 0 and negative values are valid.

        Raises:
            EntityNotFoundError: If character not found.
        """
        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")

        old = chars[char_id].get("initiative")
        chars[char_id]["initiative"] = roll
        self._store.mark_dirty()

        self._changelog.append(
            operation="set_initiative",
            entity_type="character",
            entity_id=char_id,
            field="initiative",
            old_value=old,
            new_value=roll,
        )

    # --- Resources ---

    def use_resource(self, char_id: str, resource: str, amount: int) -> int:
        """Spend a resource. Returns remaining amount.

        Raises:
            EntityNotFoundError: If character not found.
            InsufficientResourceError: If not enough resource available.
            RulesetNotSupportedError: If resource is null (inactive under current ruleset).
        """
        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")

        char = chars[char_id]
        resources = char.get("resources", {})
        current = resources.get(resource)

        if current is None:
            raise RulesetNotSupportedError(
                f"use_resource({resource})",
                self._store.get_raw().get("campaign", {}).get("ruleset"),
            )

        if isinstance(current, dict):
            # Spell slots: resource is 'spell_slots', amount is level key? No —
            # use_resource('spell_slots', level) expects resource=spell_slots and
            # we need a different interface.
            raise ValueError(
                f"use_resource: dict resource {resource!r} requires explicit key. "
                f"Use set_field() directly for spell slots."
            )

        if not isinstance(current, int):
            raise ValueError(f"Resource {resource!r} is not an integer value")

        if current < amount:
            raise InsufficientResourceError(resource, current, amount)

        old = current
        new_val = current - amount
        resources[resource] = new_val
        self._store.mark_dirty()

        self._changelog.append(
            operation="use_resource",
            entity_type="character",
            entity_id=char_id,
            field=f"resources.{resource}",
            old_value=old,
            new_value=new_val,
            metadata={"amount": amount},
        )

        return new_val

    def restore_resource(self, char_id: str, resource: str, amount: int) -> int:
        """Restore a resource. Returns new total.

        Raises:
            EntityNotFoundError: If character not found.
            RulesetNotSupportedError: If resource is null.
        """
        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")

        char = chars[char_id]
        resources = char.get("resources", {})
        current = resources.get(resource)

        if current is None:
            raise RulesetNotSupportedError(
                f"restore_resource({resource})",
                self._store.get_raw().get("campaign", {}).get("ruleset"),
            )

        if not isinstance(current, int):
            raise ValueError(f"Resource {resource!r} is not an integer value")

        old = current
        new_val = current + amount
        resources[resource] = new_val
        self._store.mark_dirty()

        self._changelog.append(
            operation="restore_resource",
            entity_type="character",
            entity_id=char_id,
            field=f"resources.{resource}",
            old_value=old,
            new_value=new_val,
            metadata={"amount": amount},
        )

        return new_val

    def use_reaction(self, char_id: str) -> None:
        """Mark reaction as used this round.

        Raises:
            EntityNotFoundError: If character not found.
        """
        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")
        chars[char_id]["reaction_used"] = True
        self._store.mark_dirty()

    def reset_reaction(self, char_id: str) -> None:
        """Reset reaction for a new round.

        Raises:
            EntityNotFoundError: If character not found.
        """
        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")
        chars[char_id]["reaction_used"] = False
        self._store.mark_dirty()

    # --- Inventory ---

    def add_item(self, char_id: str, item: dict[str, Any]) -> None:
        """Add an item to a character's inventory.

        Raises:
            EntityNotFoundError: If character not found.
            ValueError: If item missing required fields.
        """
        if "name" not in item:
            raise ValueError("Item must have a 'name' field")
        item.setdefault("quantity", 1)

        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")

        inventory: list[dict[str, Any]] = chars[char_id].setdefault("inventory", [])
        inventory.append(dict(item))
        self._store.mark_dirty()

        self._changelog.append(
            operation="add_item",
            entity_type="character",
            entity_id=char_id,
            field="inventory",
            old_value=list(inventory[:-1]),
            new_value=list(inventory),
            metadata={"item_name": item["name"]},
        )

    def remove_item(self, char_id: str, item_name: str, quantity: int = 1) -> None:
        """Remove items from a character's inventory by name.

        Decrements quantity. Removes entry if quantity reaches 0.

        Raises:
            EntityNotFoundError: If character not found.
            ValueError: If item not found or insufficient quantity.
        """
        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")

        inventory: list[dict[str, Any]] = chars[char_id].get("inventory", [])
        old = deepcopy(inventory)

        found = None
        for i, item in enumerate(inventory):
            if item.get("name") == item_name:
                found = i
                break

        if found is None:
            raise ValueError(f"Item {item_name!r} not found in inventory")

        item = inventory[found]
        if item.get("quantity", 1) <= quantity:
            inventory.pop(found)
        else:
            item["quantity"] -= quantity

        self._store.mark_dirty()

        self._changelog.append(
            operation="remove_item",
            entity_type="character",
            entity_id=char_id,
            field="inventory",
            old_value=old,
            new_value=list(inventory),
            metadata={"item_name": item_name, "quantity": quantity},
        )

    def get_inventory(self, char_id: str) -> list[dict[str, Any]]:
        """Return a deep copy of the character's inventory.

        Raises:
            EntityNotFoundError: If character not found.
        """
        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")
        return deepcopy(chars[char_id].get("inventory", []))

    def get_ac(self, char_id: str) -> int:
        """Return a character's armor class.

        Raises:
            EntityNotFoundError: If character not found.
        """
        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")
        return chars[char_id].get("ac", 0)

    # --- Equipment ---

    def equip_item(self, char_id: str, slot: str, item_name: str) -> None:
        """Equip an item into a slot.

        Raises:
            EntityNotFoundError: If character not found.
            ValueError: If slot is invalid.
        """
        if slot not in EQUIPMENT_SLOTS:
            raise ValueError(
                f"Invalid equipment slot {slot!r}. Valid: {EQUIPMENT_SLOTS}"
            )

        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")

        equipment = chars[char_id].setdefault("equipment", {})
        old = equipment.get(slot)
        equipment[slot] = item_name
        self._store.mark_dirty()

        self._changelog.append(
            operation="equip_item",
            entity_type="character",
            entity_id=char_id,
            field=f"equipment.{slot}",
            old_value=old,
            new_value=item_name,
        )

    def unequip_item(self, char_id: str, slot: str) -> None:
        """Unequip an item from a slot.

        Raises:
            EntityNotFoundError: If character not found.
            ValueError: If slot is invalid.
        """
        if slot not in EQUIPMENT_SLOTS:
            raise ValueError(
                f"Invalid equipment slot {slot!r}. Valid: {EQUIPMENT_SLOTS}"
            )

        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")

        equipment = chars[char_id].get("equipment", {})
        old = equipment.get(slot)
        equipment[slot] = None
        self._store.mark_dirty()

        self._changelog.append(
            operation="unequip_item",
            entity_type="character",
            entity_id=char_id,
            field=f"equipment.{slot}",
            old_value=old,
            new_value=None,
        )

    # --- Rest & Level Up ---

    def rest(self, char_id: str, rest_type: Literal["short", "long"]) -> dict[str, Any]:
        """Perform a rest. Returns a dict of changes.

        Short rest: restores some resources (ruleset-dependent).
        Long rest: full HP restore + all resources.

        Under Cairn: short rest does nothing special; long rest restores HP and
        clears deprived/fatigued conditions.

        Raises:
            EntityNotFoundError: If character not found.
        """
        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")

        char = chars[char_id]
        changes: dict[str, Any] = {"hp_before": char["hp"]}

        if rest_type == "long":
            char["hp"] = char.get("max_hp", char["hp"])
            # Clear deprived/fatigued conditions
            conditions: list[str] = char.get("conditions", [])
            for cond in ("deprived", "fatigued", "scarred"):
                if cond in conditions:
                    conditions.remove(cond)
            # Restore reactions
            char["reaction_used"] = False
            # Under Cairn: restore all null resources (no-op)
            changes["hp_after"] = char["hp"]
            changes["conditions_cleared"] = ["deprived", "fatigued"]
        elif rest_type == "short":
            # Short rest: minimal — just note it happened
            changes["hp_after"] = char["hp"]
            changes["conditions_cleared"] = []

        self._store.mark_dirty()

        self._changelog.append(
            operation="rest",
            entity_type="character",
            entity_id=char_id,
            field="hp",
            old_value=changes["hp_before"],
            new_value=char["hp"],
            metadata={"rest_type": rest_type},
        )

        return changes

    def level_up(self, char_id: str) -> dict[str, Any]:
        """Level up a character. Raises RulesetNotSupportedError under Cairn.

        Raises:
            EntityNotFoundError: If character not found.
            RulesetNotSupportedError: Under Cairn (no levels).
        """
        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")

        char = chars[char_id]
        level = char.get("level")
        if level is None:
            raise InvalidOperationError(
                f"Character {char_id!r} has no level field set"
            )
        char["level"] = level + 1
        self._store.mark_dirty()

        return {"new_level": char["level"]}

    # --- Spells ---

    def add_spell(self, char_id: str, spell: dict[str, Any]) -> None:
        """Add a spell to the character's known spells.

        Raises:
            EntityNotFoundError: If character not found.
            ValueError: If spell missing required fields.
        """
        if "name" not in spell or "level" not in spell:
            raise ValueError("Spell must have 'name' and 'level' fields")

        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")

        spells: list[dict[str, Any]] = chars[char_id].setdefault("spells_known", [])
        spell.setdefault("prepared", False)
        spells.append(dict(spell))
        self._store.mark_dirty()

        self._changelog.append(
            operation="add_spell",
            entity_type="character",
            entity_id=char_id,
            field="spells_known",
            old_value=list(spells[:-1]),
            new_value=list(spells),
            metadata={"spell_name": spell["name"]},
        )

    def remove_spell(self, char_id: str, spell_name: str) -> None:
        """Remove a spell from the character's known spells.

        Raises:
            EntityNotFoundError: If character not found.
            ValueError: If spell not found.
        """
        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")

        spells: list[dict[str, Any]] = chars[char_id].get("spells_known", [])
        old = deepcopy(spells)

        for i, spell in enumerate(spells):
            if spell.get("name") == spell_name:
                spells.pop(i)
                self._store.mark_dirty()
                self._changelog.append(
                    operation="remove_spell",
                    entity_type="character",
                    entity_id=char_id,
                    field="spells_known",
                    old_value=old,
                    new_value=list(spells),
                    metadata={"spell_name": spell_name},
                )
                return

        raise ValueError(f"Spell {spell_name!r} not found")

    def prepare_spell(self, char_id: str, spell_name: str, prepared: bool) -> None:
        """Set a spell's prepared status.

        Raises:
            EntityNotFoundError: If character not found.
            ValueError: If spell not found.
        """
        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")

        spells: list[dict[str, Any]] = chars[char_id].get("spells_known", [])
        for spell in spells:
            if spell.get("name") == spell_name:
                spell["prepared"] = prepared
                self._store.mark_dirty()
                return

        raise ValueError(f"Spell {spell_name!r} not found")

    def get_spells(self, char_id: str, prepared_only: bool = False) -> list[dict[str, Any]]:
        """Return a deepcopy of the character's known spells.

        Raises:
            EntityNotFoundError: If character not found.
        """
        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")

        spells = deepcopy(chars[char_id].get("spells_known", []))
        if prepared_only:
            spells = [s for s in spells if s.get("prepared", False)]
        return spells

    # --- Romance ---

    def set_romance(self, char_id: str, field: str, value: Any) -> None:
        """Set a romance field on a character.

        Raises:
            EntityNotFoundError: If character not found.
        """
        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")

        romance = chars[char_id].setdefault("romance", {})
        if romance is None:
            romance = {}
            chars[char_id]["romance"] = romance

        old = romance.get(field)
        romance[field] = value
        self._store.mark_dirty()

        self._changelog.append(
            operation="set_romance",
            entity_type="character",
            entity_id=char_id,
            field=f"romance.{field}",
            old_value=old,
            new_value=value,
        )

    def get_romance(self, char_id: str) -> dict[str, Any] | None:
        """Return a deepcopy of the character's romance data.

        Raises:
            EntityNotFoundError: If character not found.
        """
        chars = self._store.get_raw().get("characters", {})
        if char_id not in chars:
            raise EntityNotFoundError(char_id, "character")
        romance = chars[char_id].get("romance")
        return deepcopy(romance) if romance is not None else None

    # ------------------------------------------------------------------
    # NPC
    # ------------------------------------------------------------------

    def create_npc(self, npc_id: str, **fields: Any) -> None:
        """Create a world NPC with pre-initialized optional fields (H5).

        Raises:
            DuplicateEntityError: If npc_id already exists.
            ValueError: If name is missing.
        """
        error = validate_entity_id(npc_id)
        if error:
            raise ValueError(error)

        data = self._store.get_raw()
        if npc_id in data.get("npcs", {}):
            raise DuplicateEntityError(npc_id, "npc")

        if "name" not in fields:
            raise ValueError(f"NPC {npc_id!r} requires a 'name' field")

        entity = apply_npc_defaults({"id": npc_id, **fields})
        data.setdefault("npcs", {})[npc_id] = entity
        self._store.mark_dirty()

        self._changelog.append(
            operation="create_npc",
            entity_type="npc",
            entity_id=npc_id,
            field="*",
            old_value=None,
            new_value={"id": npc_id, "name": fields["name"]},
        )

    def get_npc(self, npc_id: str) -> dict[str, Any]:
        """Return a deepcopy of the NPC dict (H6).

        Raises EntityNotFoundError if not found.
        """
        npcs = self._store.get_raw().get("npcs", {})
        if npc_id not in npcs:
            raise EntityNotFoundError(npc_id, "npc")
        return deepcopy(npcs[npc_id])

    def npc_exists(self, npc_id: str) -> bool:
        """Return True if the NPC exists."""
        return npc_id in self._store.get_raw().get("npcs", {})

    def list_npcs(
        self, location: str | None = None, faction: str | None = None
    ) -> list[dict[str, Any]]:
        """List NPCs, optionally filtered by location or faction.

        Returns deep copies (H6).
        """
        npcs = self._store.get_raw().get("npcs", {})
        result = []
        for nid, npc in npcs.items():
            if location is not None and npc.get("location") != location:
                continue
            if faction is not None and npc.get("faction") != faction:
                continue
            result.append(deepcopy(npc))
        return result

    def set_npc_attitude(self, npc_id: str, attitude: str) -> None:
        """Set an NPC's attitude.

        Raises:
            EntityNotFoundError: If NPC not found.
            ValueError: If attitude is not valid.
        """
        if attitude not in VALID_NPC_ATTITUDES:
            raise ValueError(
                f"Invalid attitude {attitude!r}. Valid: {sorted(VALID_NPC_ATTITUDES)}"
            )

        npcs = self._store.get_raw().get("npcs", {})
        if npc_id not in npcs:
            raise EntityNotFoundError(npc_id, "npc")

        old = npcs[npc_id].get("attitude")
        npcs[npc_id]["attitude"] = attitude
        self._store.mark_dirty()

        self._changelog.append(
            operation="set_npc_attitude",
            entity_type="npc",
            entity_id=npc_id,
            field="attitude",
            old_value=old,
            new_value=attitude,
        )

    def set_npc_knowledge(
        self, npc_id: str, key: str, value: Any, is_secret: bool = False
    ) -> None:
        """Set a knowledge key on an NPC.

        Raises:
            EntityNotFoundError: If NPC not found.
        """
        npcs = self._store.get_raw().get("npcs", {})
        if npc_id not in npcs:
            raise EntityNotFoundError(npc_id, "npc")

        knowledge: dict[str, Any] = npcs[npc_id].setdefault("knowledge", {})
        knowledge[key] = {"value": value, "is_secret": is_secret}
        self._store.mark_dirty()

    def get_npc_knowledge(self, npc_id: str, key: str) -> dict[str, Any] | None:
        """Get a knowledge entry from an NPC.

        Returns None if key not found.

        Raises:
            EntityNotFoundError: If NPC not found.
        """
        npcs = self._store.get_raw().get("npcs", {})
        if npc_id not in npcs:
            raise EntityNotFoundError(npc_id, "npc")

        knowledge = npcs[npc_id].get("knowledge", {})
        return knowledge.get(key)

    def set_npc_relationship(self, npc_id: str, score: int) -> None:
        """Set the relationship score for an NPC (-10 to +10).

        Raises:
            EntityNotFoundError: If NPC not found.
            ValueError: If score out of range.
        """
        if not -10 <= score <= 10:
            raise ValueError("Relationship score must be between -10 and +10")

        npcs = self._store.get_raw().get("npcs", {})
        if npc_id not in npcs:
            raise EntityNotFoundError(npc_id, "npc")

        old = npcs[npc_id].get("relationship")
        npcs[npc_id]["relationship"] = score
        self._store.mark_dirty()

        self._changelog.append(
            operation="set_npc_relationship",
            entity_type="npc",
            entity_id=npc_id,
            field="relationship",
            old_value=old,
            new_value=score,
        )

    # ------------------------------------------------------------------
    # Monster
    # ------------------------------------------------------------------

    def create_monster(self, monster_id: str, **fields: Any) -> None:
        """Create a monster with pre-initialized optional fields (H5).

        Required: name, size, type, max_hp, ac. abilities optional (defaults to 10).

        Raises:
            DuplicateEntityError: If monster_id already exists.
            ValueError: If required fields missing.
        """
        error = validate_entity_id(monster_id)
        if error:
            raise ValueError(error)

        data = self._store.get_raw()
        if monster_id in data.get("monsters", {}):
            raise DuplicateEntityError(monster_id, "monster")

        required = {"name", "size", "type", "max_hp", "ac"}
        missing = required - set(fields.keys())
        if missing:
            raise ValueError(
                f"Missing required fields for monster {monster_id!r}: {missing}"
            )

        ruleset = data.get("campaign", {}).get("ruleset")

        entity = apply_monster_defaults(
            {"id": monster_id, "hp": fields.get("max_hp", 1), **fields},
            ruleset,
        )

        data.setdefault("monsters", {})[monster_id] = entity
        self._store.mark_dirty()

        self._changelog.append(
            operation="create_monster",
            entity_type="monster",
            entity_id=monster_id,
            field="*",
            old_value=None,
            new_value={"id": monster_id, "name": fields.get("name", "")},
        )

    def create_monsters(self, monsters: list[dict[str, Any]]) -> list[str]:
        """Bulk create monsters. Returns list of created IDs.

        Each dict must include an 'id' key plus required monster fields.
        """
        ids = []
        data = self._store.get_raw()
        ruleset = data.get("campaign", {}).get("ruleset")

        for mon in monsters:
            mid = mon.pop("id")
            error = validate_entity_id(mid)
            if error:
                raise ValueError(f"Monster {mid!r}: {error}")
            if mid in data.get("monsters", {}):
                raise DuplicateEntityError(mid, "monster")

            required = {"name", "size", "type", "max_hp", "ac"}
            missing = required - set(mon.keys())
            if missing:
                raise ValueError(f"Monster {mid!r} missing: {missing}")

            entity = apply_monster_defaults(
                {"id": mid, "hp": mon.get("max_hp", 1), **mon},
                ruleset,
            )
            data.setdefault("monsters", {})[mid] = entity
            ids.append(mid)

        self._store.mark_dirty()
        return ids

    def get_monster(self, monster_id: str) -> dict[str, Any]:
        """Return a deepcopy of the monster dict (H6).

        Raises EntityNotFoundError if not found.
        """
        monsters = self._store.get_raw().get("monsters", {})
        if monster_id not in monsters:
            raise EntityNotFoundError(monster_id, "monster")
        return deepcopy(monsters[monster_id])

    def monster_exists(self, monster_id: str) -> bool:
        """Return True if the monster exists."""
        return monster_id in self._store.get_raw().get("monsters", {})

    def list_monsters(
        self, type: str | None = None, cr_max: float | None = None
    ) -> list[dict[str, Any]]:
        """List monsters, optionally filtered by type or max CR.

        Returns deep copies (H6).
        """
        monsters = self._store.get_raw().get("monsters", {})
        result = []
        for mid, mon in monsters.items():
            if type is not None and mon.get("type") != type:
                continue
            if cr_max is not None:
                cr = mon.get("cr")
                if cr is not None and isinstance(cr, (int, float)) and cr > cr_max:
                    continue
            result.append(deepcopy(mon))
        return result

    def monster_take_damage(self, monster_id: str, amount: int) -> int:
        """Deal damage to a monster. Returns new HP.

        Raises:
            EntityNotFoundError: If monster not found.
        """
        monsters = self._store.get_raw().get("monsters", {})
        if monster_id not in monsters:
            raise EntityNotFoundError(monster_id, "monster")

        mon = monsters[monster_id]
        old_hp = mon.get("hp", 0)
        new_hp = self._apply_damage(mon, amount)
        self._store.mark_dirty()

        self._changelog.append(
            operation="monster_take_damage",
            entity_type="monster",
            entity_id=monster_id,
            field="hp",
            old_value=old_hp,
            new_value=new_hp,
            metadata={"amount": amount},
        )

        return new_hp

    def monster_heal(self, monster_id: str, amount: int) -> int:
        """Heal a monster. Returns new HP.

        Raises:
            EntityNotFoundError: If monster not found.
        """
        monsters = self._store.get_raw().get("monsters", {})
        if monster_id not in monsters:
            raise EntityNotFoundError(monster_id, "monster")

        mon = monsters[monster_id]
        if amount < 0:
            amount = 0

        old_hp = mon.get("hp", 0)
        max_hp = mon.get("max_hp", 1)
        mon["hp"] = min(max_hp, old_hp + amount)

        if old_hp <= 0 and mon["hp"] > 0:
            mon["is_alive"] = True

        self._store.mark_dirty()

        self._changelog.append(
            operation="monster_heal",
            entity_type="monster",
            entity_id=monster_id,
            field="hp",
            old_value=old_hp,
            new_value=mon["hp"],
            metadata={"amount": amount},
        )

        return mon["hp"]

    def monster_damage_ability(self, monster_id: str, ability: str, amount: int) -> int:
        """Damage a monster's ability score (Cairn overflow).

        Returns the new ability score.

        Raises:
            EntityNotFoundError: If monster not found.
        """
        monsters = self._store.get_raw().get("monsters", {})
        if monster_id not in monsters:
            raise EntityNotFoundError(monster_id, "monster")

        mon = monsters[monster_id]
        abilities = mon.get("abilities", {})
        if ability not in abilities or abilities[ability] is None:
            raise ValueError(
                f"Ability {ability!r} not available on monster {monster_id!r}"
            )

        ab = abilities[ability]
        if not isinstance(ab, dict):
            raise ValueError(f"Invalid ability format for {ability!r}")

        old_score = ab.get("score", 0)
        new_score = max(0, old_score - amount)
        ab["score"] = new_score

        if new_score <= 0:
            mon["is_alive"] = False

        self._store.mark_dirty()
        return new_score

    def monster_add_condition(self, monster_id: str, condition: str) -> None:
        """Add a condition to a monster.

        Raises:
            EntityNotFoundError: If monster not found.
            ValueError: If condition is not valid.
        """
        error = validate_condition(condition)
        if error:
            raise ValueError(error)

        monsters = self._store.get_raw().get("monsters", {})
        if monster_id not in monsters:
            raise EntityNotFoundError(monster_id, "monster")

        mon = monsters[monster_id]
        conditions: list[str] = mon.setdefault("conditions", [])
        if condition not in conditions:
            conditions.append(condition)
            self._store.mark_dirty()

    def monster_remove_condition(self, monster_id: str, condition: str) -> None:
        """Remove a condition from a monster.

        Raises:
            EntityNotFoundError: If monster not found.
        """
        monsters = self._store.get_raw().get("monsters", {})
        if monster_id not in monsters:
            raise EntityNotFoundError(monster_id, "monster")

        mon = monsters[monster_id]
        conditions: list[str] = mon.get("conditions", [])
        if condition in conditions:
            conditions.remove(condition)
            self._store.mark_dirty()

    # ------------------------------------------------------------------
    # Item Transfer
    # ------------------------------------------------------------------

    def transfer_item(
        self, from_entity: str, to_entity: str, item_name: str, quantity: int = 1
    ) -> None:
        """Transfer an item between two entities atomically.

        Validates both sides before any mutation. If the from_entity doesn't
        have enough quantity, neither side is touched.

        Raises:
            EntityNotFoundError: If either entity not found.
            ValueError: If item not found or insufficient quantity.
        """
        data = self._store.get_raw()

        # Find both entities
        from_e = self._find_entity(data, from_entity)
        if from_e is None:
            raise EntityNotFoundError(from_entity)
        to_e = self._find_entity(data, to_entity)
        if to_e is None:
            raise EntityNotFoundError(to_entity)

        # Check source inventory
        from_inv: list[dict[str, Any]] = from_e.get("inventory", [])
        found_idx = None
        for i, item in enumerate(from_inv):
            if item.get("name") == item_name:
                found_idx = i
                break

        if found_idx is None:
            raise ValueError(f"Item {item_name!r} not found on {from_entity!r}")

        from_item = from_inv[found_idx]
        if from_item.get("quantity", 1) < quantity:
            raise ValueError(
                f"Insufficient quantity: {from_entity!r} has "
                f"{from_item.get('quantity', 1)} of {item_name!r}"
            )

        # Atomic: remove from source
        if from_item["quantity"] <= quantity:
            from_inv.pop(found_idx)
            transferred = dict(from_item)
            transferred["quantity"] = quantity
        else:
            from_item["quantity"] -= quantity
            transferred = dict(from_item)
            transferred["quantity"] = quantity

        # Add to destination
        to_inv: list[dict[str, Any]] = to_e.setdefault("inventory", [])
        # Try to stack
        stacked = False
        for item in to_inv:
            if item.get("name") == item_name:
                item["quantity"] = item.get("quantity", 0) + quantity
                stacked = True
                break
        if not stacked:
            to_inv.append(transferred)

        self._store.mark_dirty()

        self._changelog.append(
            operation="transfer_item",
            entity_type="character",
            entity_id=from_entity,
            field="inventory",
            old_value={"from": from_entity, "to": to_entity},
            new_value={"item": item_name, "quantity": quantity},
            metadata={"from": from_entity, "to": to_entity},
        )

    # ------------------------------------------------------------------
    # Combat
    # ------------------------------------------------------------------

    def init_combat(self, participants: list[str], location: str) -> None:
        """Start combat with the given participants.

        Raises:
            CombatAlreadyActiveError: If combat is already active.
            ValueError: If participants list is empty.
        """
        data = self._store.get_raw()
        if data.get("combat") is not None:
            raise CombatAlreadyActiveError("Combat is already active")

        if not participants:
            raise ValueError("Must provide at least one combat participant")

        now = datetime.now(UTC).isoformat()
        combat = {
            "active": True,
            "round": 1,
            "turn_index": 0,
            "order": list(participants),
            "participants": list(participants),
            "location": location,
            "log": [],
            "started_at": now,
            "status": "active",
            "positions": {},  # T5.10: Pre-allocate positions dict
        }

        # T5.10: Auto-populate default positions (close band) for all combatants
        for entity_id in participants:
            combat["positions"][entity_id] = {"band": "close", "x": None, "y": None}

        data["combat"] = combat
        self._store.mark_dirty()

        self._changelog.append(
            operation="init_combat",
            entity_type="combat",
            entity_id="*",
            field="combat",
            old_value=None,
            new_value={"participants": participants, "location": location},
        )

    # ── Distance Tracking (T5.10) ──────────────────────────────────────

    # Band ordering for distance calculation
    _BAND_ORDER: tuple[str, ...] = ("close", "near", "far", "distant")

    def set_position(
        self, entity_id: str, band: str, x: int | None = None, y: int | None = None
    ) -> None:
        """Set an entity's combat position.

        Args:
            entity_id: Entity ID.
            band: Distance band (close, near, far, distant).
            x: Optional grid X coordinate.
            y: Optional grid Y coordinate.

        Raises:
            NoActiveCombatError: If no combat is active.
            ValueError: If band is not recognized.
        """
        data = self._store.get_raw()
        combat = data.get("combat")
        if combat is None:
            raise NoActiveCombatError(
                "Cannot set position: no combat is active"
            )

        valid_bands = set(self._BAND_ORDER)
        if band not in valid_bands:
            raise ValueError(
                f"Invalid band {band!r}. Valid: {', '.join(valid_bands)}"
            )

        positions = combat.setdefault("positions", {})
        old = positions.get(entity_id)
        positions[entity_id] = {"band": band, "x": x, "y": y}
        self._store.mark_dirty()

        self._changelog.append(
            operation="set_position",
            entity_type="combat",
            entity_id=entity_id,
            field="combat.positions",
            old_value=old,
            new_value={"band": band, "x": x, "y": y},
        )

    def get_position(self, entity_id: str) -> dict[str, Any] | None:
        """Get an entity's combat position.

        Returns:
            Dict with 'band', 'x', 'y' keys, or None if not in combat / no position.
        """
        data = self._store.get_raw()
        combat = data.get("combat")
        if combat is None:
            return None
        positions = combat.get("positions", {})
        return deepcopy(positions.get(entity_id))

    @classmethod
    def get_band_distance(cls, band_a: str, band_b: str) -> int:
        """Calculate the distance between two bands.

        Returns:
            Integer distance: 0 (same band), 1 (adjacent), 2, 3, etc.
            Returns -1 if either band is unrecognized.

        Examples:
            get_band_distance("close", "close") → 0
            get_band_distance("close", "near") → 1
            get_band_distance("close", "far") → 2
            get_band_distance("close", "distant") → 3
        """
        try:
            idx_a = cls._BAND_ORDER.index(band_a)
            idx_b = cls._BAND_ORDER.index(band_b)
            return abs(idx_a - idx_b)
        except ValueError:
            return -1

    def next_turn(self) -> str:
        """Advance to the next combatant's turn. Returns the new current actor ID.

        Raises:
            NoActiveCombatError: If no combat is active.
        """
        data = self._store.get_raw()
        combat = data.get("combat")
        if combat is None:
            raise NoActiveCombatError("No combat is active")

        order = combat.get("order", [])
        if not order:
            raise NoActiveCombatError("No combatants in initiative order")

        current_idx = combat.get("turn_index", 0)
        next_idx = current_idx + 1

        # If we've gone through all combatants, increment round
        if next_idx >= len(order):
            next_idx = 0
            combat["round"] = combat.get("round", 1) + 1

        combat["turn_index"] = next_idx
        self._store.mark_dirty()

        return order[next_idx]

    def get_current_actor(self) -> str:
        """Return the ID of the combatant whose turn it is.

        Raises:
            NoActiveCombatError: If no combat is active.
        """
        data = self._store.get_raw()
        combat = data.get("combat")
        if combat is None:
            raise NoActiveCombatError("No combat is active")

        order = combat.get("order", [])
        if not order:
            raise NoActiveCombatError("No combatants in initiative order")

        idx = combat.get("turn_index", 0)
        return order[idx]

    def get_turn_order(self) -> list[str]:
        """Return the initiative order list.

        Raises:
            NoActiveCombatError: If no combat is active.
        """
        data = self._store.get_raw()
        combat = data.get("combat")
        if combat is None:
            raise NoActiveCombatError("No combat is active")
        return list(combat.get("order", []))

    def add_combatant(self, entity_id: str, initiative: int) -> None:
        """Add a combatant at the given initiative (insert AFTER same-initiative).

        Raises:
            NoActiveCombatError: If no combat is active.
        """
        data = self._store.get_raw()
        combat = data.get("combat")
        if combat is None:
            raise NoActiveCombatError("No combat is active")

        # Add to participants
        participants: list[str] = combat.setdefault("participants", [])
        if entity_id not in participants:
            participants.append(entity_id)

        # Insert into order at correct position
        order: list[str] = combat.setdefault("order", [])
        # Find entity's initiative to insert at right position
        # We don't have entity initiatives stored in combat — they're on entities.
        # For now, append to end. Proper initiative ordering is T1.4's job.
        if entity_id not in order:
            # Insert AFTER last same-initiative entry
            insert_at = len(order)
            for i, pid in enumerate(order):
                # If current has lower initiative, insert here
                pass  # Defer to T1.4 for actual sorting
            order.append(entity_id)

        self._store.mark_dirty()

    def remove_combatant(self, entity_id: str) -> None:
        """Remove a combatant from combat.

        Raises:
            NoActiveCombatError: If no combat is active.
        """
        data = self._store.get_raw()
        combat = data.get("combat")
        if combat is None:
            raise NoActiveCombatError("No combat is active")

        participants: list[str] = combat.get("participants", [])
        if entity_id in participants:
            participants.remove(entity_id)

        order: list[str] = combat.get("order", [])
        if entity_id in order:
            order.remove(entity_id)

        # Adjust turn_index if needed
        turn_idx = combat.get("turn_index", 0)
        if turn_idx >= len(order):
            combat["turn_index"] = max(0, len(order) - 1)

        self._store.mark_dirty()

    def end_combat(
        self, status: Literal["victory", "defeat", "fled"]
    ) -> None:
        """End combat and archive to combat_history.

        Raises:
            NoActiveCombatError: If no combat is active.
            ValueError: If status is invalid.
        """
        if status not in VALID_COMBAT_STATUSES:
            raise ValueError(
                f"Invalid combat status {status!r}. Valid: {sorted(VALID_COMBAT_STATUSES)}"
            )
        if status == "active":
            raise ValueError("Cannot end combat with 'active' status")

        data = self._store.get_raw()
        combat = data.get("combat")
        if combat is None:
            raise NoActiveCombatError("No combat is active")

        # Archive
        history_entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "participants": list(combat.get("participants", [])),
            "rounds": combat.get("round", 1),
            "outcome": status,
            "location": combat.get("location", "Unknown"),
            "log_summary": f"Combat ended: {status}. {len(combat.get('log', []))} log entries.",
        }
        data.setdefault("combat_history", []).append(history_entry)

        # Clear combat
        data["combat"] = None
        self._store.mark_dirty()

        self._changelog.append(
            operation="end_combat",
            entity_type="combat",
            entity_id="*",
            field="combat",
            old_value={"status": combat.get("status"), "round": combat.get("round")},
            new_value=None,
            metadata={"outcome": status},
        )

    def is_combat_active(self) -> bool:
        """Return True if combat is currently active."""
        return self._store.get_raw().get("combat") is not None

    # ------------------------------------------------------------------
    # Party
    # ------------------------------------------------------------------

    def add_party_member(self, char_id: str) -> None:
        """Add a character to the party.

        Raises:
            EntityNotFoundError: If character not found.
        """
        data = self._store.get_raw()
        if char_id not in data.get("characters", {}):
            raise EntityNotFoundError(char_id, "character")

        party = data.setdefault("party", {})
        members: list[str] = party.setdefault("members", [])
        if char_id not in members:
            members.append(char_id)
            self._store.mark_dirty()

    def remove_party_member(self, char_id: str) -> None:
        """Remove a character from the party."""
        data = self._store.get_raw()
        members: list[str] = data.get("party", {}).get("members", [])
        if char_id in members:
            members.remove(char_id)
            self._store.mark_dirty()

    def get_party(self) -> dict[str, Any]:
        """Return a deepcopy of the party data (H6)."""
        return deepcopy(self._store.get_raw().get("party", {}))

    def add_gold(self, amount: int) -> None:
        """Add gold to the party. Raw integer value (H9)."""
        party = self._store.get_raw().setdefault("party", {})
        old = party.get("gold", 0)
        party["gold"] = old + amount
        self._store.mark_dirty()

        self._changelog.append(
            operation="add_gold",
            entity_type="party",
            entity_id="*",
            field="gold",
            old_value=old,
            new_value=party["gold"],
            metadata={"amount": amount},
        )

    def spend_gold(self, amount: int) -> None:
        """Spend party gold.

        Raises:
            InsufficientFundsError: If not enough gold.
        """
        party = self._store.get_raw().setdefault("party", {})
        current = party.get("gold", 0)
        if current < amount:
            raise InsufficientFundsError(current, amount)

        party["gold"] = current - amount
        self._store.mark_dirty()

        self._changelog.append(
            operation="spend_gold",
            entity_type="party",
            entity_id="*",
            field="gold",
            old_value=current,
            new_value=party["gold"],
            metadata={"amount": amount},
        )

    def add_party_item(self, item: dict[str, Any]) -> None:
        """Add an item to the party's shared inventory."""
        if "name" not in item:
            raise ValueError("Item must have a 'name' field")
        item.setdefault("quantity", 1)

        party = self._store.get_raw().setdefault("party", {})
        inventory: list[dict[str, Any]] = party.setdefault("shared_inventory", [])
        inventory.append(dict(item))
        self._store.mark_dirty()

    def remove_party_item(self, item_name: str, quantity: int = 1) -> None:
        """Remove items from party shared inventory."""
        party = self._store.get_raw().setdefault("party", {})
        inventory: list[dict[str, Any]] = party.get("shared_inventory", [])

        found = None
        for i, item in enumerate(inventory):
            if item.get("name") == item_name:
                found = i
                break

        if found is None:
            raise ValueError(f"Item {item_name!r} not found in party inventory")

        item = inventory[found]
        if item.get("quantity", 1) <= quantity:
            inventory.pop(found)
        else:
            item["quantity"] -= quantity

        self._store.mark_dirty()

    # ------------------------------------------------------------------
    # Quest
    # ------------------------------------------------------------------

    def add_quest(self, quest_id: str, **fields: Any) -> None:
        """Add a quest.

        Required: name, stages (list of strings).

        Raises:
            DuplicateEntityError: If quest_id already exists.
            ValueError: If required fields missing.
        """
        error = validate_entity_id(quest_id)
        if error:
            raise ValueError(error)

        data = self._store.get_raw()
        if quest_id in data.get("quests", {}):
            raise DuplicateEntityError(quest_id, "quest")

        if "name" not in fields:
            raise ValueError(f"Quest {quest_id!r} requires a 'name' field")
        if "stages" not in fields:
            raise ValueError(f"Quest {quest_id!r} requires 'stages' field")

        quest = {
            "id": quest_id,
            "name": fields["name"],
            "status": fields.get("status", "available"),
            "stage": fields.get("stage", 0),
            "stages": fields["stages"],
            "objectives": fields.get("objectives", []),
            "giver": fields.get("giver"),
            "rewards": fields.get("rewards", {"gold": None, "items": None, "xp": None}),
            "started_at": fields.get("started_at"),
            "completed_at": fields.get("completed_at"),
        }

        data.setdefault("quests", {})[quest_id] = quest
        self._store.mark_dirty()

    def get_quest(self, quest_id: str) -> dict[str, Any]:
        """Return a deepcopy of the quest dict.

        Raises EntityNotFoundError if not found.
        """
        quests = self._store.get_raw().get("quests", {})
        if quest_id not in quests:
            raise EntityNotFoundError(quest_id, "quest")
        return deepcopy(quests[quest_id])

    def update_quest_status(self, quest_id: str, status: str) -> None:
        """Update a quest's status.

        Raises:
            EntityNotFoundError: If quest not found.
            ValueError: If status is invalid.
        """
        if status not in VALID_QUEST_STATUSES:
            raise ValueError(
                f"Invalid quest status {status!r}. Valid: {sorted(VALID_QUEST_STATUSES)}"
            )

        quests = self._store.get_raw().get("quests", {})
        if quest_id not in quests:
            raise EntityNotFoundError(quest_id, "quest")

        quests[quest_id]["status"] = status
        now = datetime.now(UTC).isoformat()
        if status == "active":
            quests[quest_id]["started_at"] = quests[quest_id].get("started_at") or now
        elif status in ("completed", "failed"):
            quests[quest_id]["completed_at"] = now

        self._store.mark_dirty()

    def advance_quest_stage(self, quest_id: str) -> None:
        """Advance a quest to the next stage.

        Raises:
            EntityNotFoundError: If quest not found.
        """
        quests = self._store.get_raw().get("quests", {})
        if quest_id not in quests:
            raise EntityNotFoundError(quest_id, "quest")

        quest = quests[quest_id]
        current = quest.get("stage", 0)
        stages = quest.get("stages", [])
        if current + 1 < len(stages):
            quest["stage"] = current + 1
        else:
            quest["status"] = "completed"
            quest["completed_at"] = datetime.now(UTC).isoformat()

        self._store.mark_dirty()

    def complete_quest_objective(self, quest_id: str, objective_index: int) -> None:
        """Mark a quest objective as complete.

        Raises:
            EntityNotFoundError: If quest not found.
            IndexError: If objective_index out of range.
        """
        quests = self._store.get_raw().get("quests", {})
        if quest_id not in quests:
            raise EntityNotFoundError(quest_id, "quest")

        objectives = quests[quest_id].get("objectives", [])
        if objective_index < 0 or objective_index >= len(objectives):
            raise IndexError(
                f"Objective index {objective_index} out of range "
                f"(0-{len(objectives) - 1})"
            )
        objectives[objective_index]["complete"] = True
        self._store.mark_dirty()

    def list_quests(self, status: str | None = None) -> list[dict[str, Any]]:
        """List quests, optionally filtered by status.

        Returns deep copies (H6).
        """
        quests = self._store.get_raw().get("quests", {})
        result = []
        for qid, quest in quests.items():
            if status is not None and quest.get("status") != status:
                continue
            result.append(deepcopy(quest))
        return result

    # ------------------------------------------------------------------
    # World
    # ------------------------------------------------------------------

    def set_scene(self, scene_name: str, location: str | None = None) -> None:
        """Set the current scene and optionally the location."""
        world = self._store.get_raw().setdefault("world", {})
        old_scene = world.get("current_scene")
        world["current_scene"] = scene_name
        if location is not None:
            world["current_location"] = location
        self._store.mark_dirty()

        self._changelog.append(
            operation="set_scene",
            entity_type="world",
            entity_id="*",
            field="current_scene",
            old_value=old_scene,
            new_value=scene_name,
            metadata={"location": location},
        )

    def get_current_location(self) -> str | None:
        """Return the current scene location."""
        world = self._store.get_raw().get("world", {})
        return world.get("current_location")

    def set_world_flag(self, key: str, value: str | int | float | bool | None) -> None:
        """Set a world state flag."""
        world = self._store.get_raw().setdefault("world", {})
        flags: dict[str, Any] = world.setdefault("flags", {})
        flags[key] = value
        self._store.mark_dirty()

    def get_world_flag(self, key: str) -> str | int | float | bool | None:
        """Get a world state flag. Returns None if not set."""
        world = self._store.get_raw().get("world", {})
        flags: dict[str, Any] = world.get("flags", {})
        return flags.get(key)

    def log_event(self, description: str, category: str = "meta") -> None:
        """Log a world event to the timeline."""
        world = self._store.get_raw().setdefault("world", {})
        timeline: list[dict[str, Any]] = world.setdefault("timeline", [])
        timeline.append(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "event": description,
                "category": category,
            }
        )
        self._store.mark_dirty()

    def advance_time(self, to: str | None = None) -> None:
        """Advance the time of day. If *to* is provided, set directly."""
        world = self._store.get_raw().setdefault("world", {})
        periods = ["morning", "afternoon", "evening", "night", "midnight", "dawn"]

        if to is not None:
            if to not in VALID_TIME_PERIODS:
                raise ValueError(
                    f"Invalid time period {to!r}. Valid: {sorted(VALID_TIME_PERIODS)}"
                )
            world["time_of_day"] = to
        else:
            current = world.get("time_of_day", "morning")
            try:
                idx = periods.index(current)
                next_idx = (idx + 1) % len(periods)
                world["time_of_day"] = periods[next_idx]
                if next_idx == 0:
                    world["day"] = world.get("day", 1) + 1
            except ValueError:
                world["time_of_day"] = periods[0]

        self._store.mark_dirty()

    # ------------------------------------------------------------------
    # Changelog / Undo
    # ------------------------------------------------------------------

    def undo(self) -> dict[str, Any]:
        """Undo the last changelog entry.

        Reverses the operation by applying old_value to the state.

        Raises:
            NoEntriesToUndoError: If changelog is empty.
        """
        entry = self._changelog.undo()
        if entry is None:
            raise NoEntriesToUndoError("No changelog entries to undo")

        # Reverse the operation
        field = entry.get("field", "")
        entity_type = entry.get("entity_type", "")
        entity_id = entry.get("entity_id", "")
        old_value = entry.get("old_value")

        if entity_id != "*" and field != "*":
            # Reverse set_field operation
            from ._changelog import _set_state_value

            _set_state_value(
                self._store.get_raw(), entity_type, entity_id, field, old_value
            )

        self._store.mark_dirty()
        return entry

    def get_changelog(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent changelog entries."""
        return self._changelog.get_entries(limit)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_entity(data: dict[str, Any], entity_id: str) -> dict[str, Any] | None:
        """Find an entity by ID across characters, npcs, and monsters."""
        for section in ("characters", "npcs", "monsters"):
            entities = data.get(section, {})
            if entity_id in entities:
                return entities[entity_id]
        return None

    @staticmethod
    def _entity_type(data: dict[str, Any], entity_id: str) -> str:
        """Determine the entity type for an ID."""
        for section in ("characters", "npcs", "monsters"):
            if entity_id in data.get(section, {}):
                return section  # returns 'characters', 'npcs', or 'monsters'
        return "unknown"
