"""StateBridge — adapter between T1.4 runtime and T1.1 StateAPI.

Responsibilities:
- Translate runtime field paths to T1.1 API calls
- Handle entity type dispatch (pc/npc vs monster)
- Manage dynamic fields
- Provide normalized entity snapshots for condition evaluation

Entity normalization:
  T1.1 v3 stores core fields FLAT at entity top level (hp, ac, max_hp, ...).
  Expressions use `entity.combat.hp`, `entity.combat.ac`, etc.
  The bridge injects a virtual `combat` dict on snapshots that contains
  both the flat core fields AND any existing dynamic combat sub-dict.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sylvia_gm.runtime._exceptions import EntityNotFoundRuntimeError

# Core fields stored flat at entity top level (T1.1 v3 flat schema).
# These are included in the virtual combat dict for expression compatibility.
_CORE_COMBAT_FIELDS: frozenset[str] = frozenset({
    "hp", "max_hp", "temp_hp", "ac", "is_alive",
    "initiative", "speed", "size",
    "conditions", "death_saves",
})


class StateBridge:
    """Adapter between RulesetRuntime and StateAPI.

    Maps runtime operations to T1.1 API calls.
    Handles entity type dispatch and field path remapping.
    """

    def __init__(self, store: Any) -> None:
        """Initialize with a T1.1 StateAPI instance.

        Args:
            store: StateAPI instance from sylvia_gm.state.
        """
        self._store = store

    # ── Campaign entity lifecycle (LOCK-4) ──────────────────────────────

    def _ensure_campaign_entity(self) -> str:
        """Ensure the _campaign entity exists. Returns its entity_id.

        Created once on first access. Stores campaign-level state:
        flags, current_scene_id, time, clocks, and scenes.

        Uses the StateAPI's ``state`` dict for top-level access.
        """
        if self.entity_exists("_campaign"):
            return "_campaign"

        if self._store is None:
            return "_campaign"  # No store — return ID but don't persist

        # StateAPI exposes the full state dict via .state
        state_dict = self._store.state
        if "_campaign" not in state_dict:
            state_dict["_campaign"] = {}
            self._store.save()
        return "_campaign"

    # ── Read operations ──────────────────────────────────────────────────

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        """Fetch entity state with normalization.

        Returns a deepcopy with a virtual `combat` dict containing
        both flat core fields and any dynamic combat fields.
        """
        try:
            raw = self._store.get_entity(entity_id)
        except Exception:
            raise EntityNotFoundRuntimeError(entity_id)  # noqa: B904
        return _normalize_entity(raw)

    def get_entity_raw(self, entity_id: str) -> dict[str, Any]:
        """Fetch entity state without normalization."""
        try:
            return dict(self._store.get_entity(entity_id))  # T1.1 returns Any
        except Exception:
            raise EntityNotFoundRuntimeError(entity_id)  # noqa: B904

    def entity_exists(self, entity_id: str) -> bool:
        """Check if an entity exists in the state store."""
        try:
            self._store.get_entity(entity_id)
            return True
        except Exception:
            return False

    def get_position(self, entity_id: str) -> dict[str, Any] | None:
        """Get the combat position of an entity.

        Returns:
            Dict with 'band', 'x', 'y' keys, or None if not in combat / no position.
        """
        try:
            return self._store.get_position(entity_id)
        except Exception:
            return None

    def read_field(self, entity_id: str, field_path: str) -> Any:
        """Read a specific field from an entity.

        Path remapping: combat.hp → hp, combat.ac → ac, etc.
        Dynamic fields pass through unchanged.
        """
        try:
            mapped_path = _map_write_path(field_path)
            return self._store.get_field(entity_id, mapped_path)
        except Exception:
            return None

    # ── Write operations ─────────────────────────────────────────────────

    def write_field(self, entity_id: str, field_path: str, value: Any) -> None:
        """Write a value to a specific field.

        Handles path remapping for core fields.
        Creates intermediate dicts for dynamic fields.
        """
        mapped_path = _map_write_path(field_path)
        try:
            self._store.set_field(entity_id, mapped_path, value)
        except Exception as e:
            raise RuntimeError(
                f"Failed to write field '{field_path}' on '{entity_id}': {e}"
            ) from e

    # ── HP operations (entity type dispatch) ─────────────────────────────

    def take_damage(self, entity_id: str, amount: int) -> int:
        """Apply damage through T1.1. Returns new HP.

        T1.1 v3 take_damage() accepts (entity_id, amount) only.
        Routes to take_damage() or monster_take_damage() based on kind.
        """
        entity = self.get_entity_raw(entity_id)
        kind = entity.get("kind", "unknown")
        if kind in ("pc", "npc", "character"):
            return self._store.take_damage(entity_id, amount)
        elif kind == "monster":
            return self._store.monster_take_damage(entity_id, amount)
        else:
            current_hp = self.read_field(entity_id, "hp")
            if current_hp is not None:
                new_hp = max(0, current_hp - amount)
                self.write_field(entity_id, "hp", new_hp)
                return new_hp
            return 0

    def heal(self, entity_id: str, amount: int) -> int:
        """Heal through T1.1. Returns new HP.

        T1.1 v3: All HP restoration goes through heal()/monster_heal()
        with internal clamping to max_hp.
        """
        entity = self.get_entity_raw(entity_id)
        kind = entity.get("kind", "unknown")
        if kind in ("pc", "npc", "character"):
            return self._store.heal(entity_id, amount)
        elif kind == "monster":
            return self._store.monster_heal(entity_id, amount)
        else:
            current_hp = self.read_field(entity_id, "hp")
            max_hp = self.read_field(entity_id, "max_hp")
            if current_hp is not None and max_hp is not None:
                new_hp = min(max_hp, max(0, current_hp + amount))
                self.write_field(entity_id, "hp", new_hp)
                return new_hp
            return 0

    # ── Condition operations ─────────────────────────────────────────────

    def add_condition(self, entity_id: str, condition: str) -> None:
        """Add a condition to an entity."""
        entity = self.get_entity_raw(entity_id)
        kind = entity.get("kind", "unknown")
        if kind in ("pc", "npc", "character"):
            self._store.add_condition(entity_id, condition)
        elif kind == "monster":
            self._store.monster_add_condition(entity_id, condition)
        else:
            conditions: list[str] = list(
                self.read_field(entity_id, "conditions") or []
            )
            if condition not in conditions:
                conditions.append(condition)
                self.write_field(entity_id, "conditions", conditions)

    def remove_condition(self, entity_id: str, condition: str) -> None:
        """Remove a condition from an entity."""
        entity = self.get_entity_raw(entity_id)
        kind = entity.get("kind", "unknown")
        if kind in ("pc", "npc", "character"):
            self._store.remove_condition(entity_id, condition)
        elif kind == "monster":
            self._store.monster_remove_condition(entity_id, condition)
        else:
            conditions: list[str] = list(
                self.read_field(entity_id, "conditions") or []
            )
            if condition in conditions:
                conditions.remove(condition)
                self.write_field(entity_id, "conditions", conditions)

    # ── Ability damage (Cairn overflow) ──────────────────────────────────

    def damage_ability(self, entity_id: str, ability: str, amount: int) -> int:
        """Damage an ability score. Returns new score."""
        entity = self.get_entity_raw(entity_id)
        kind = entity.get("kind", "unknown")
        if kind in ("pc", "npc", "character"):
            return self._store.damage_ability(entity_id, ability, amount)
        elif kind == "monster":
            return self._store.monster_damage_ability(entity_id, ability, amount)
        else:
            score = self.read_field(entity_id, f"abilities.{ability}.score")
            if score is None:
                raise ValueError(f"Ability '{ability}' not found on '{entity_id}'")
            new_score = max(0, score - amount)
            self.write_field(entity_id, f"abilities.{ability}.score", new_score)
            if new_score <= 0:
                self.write_field(entity_id, "is_alive", False)
            return new_score

    # ── Decrement / Increment helpers ────────────────────────────────────

    def modify_field(self, entity_id: str, field_path: str, delta: int) -> int:
        """Read-modify-write a numeric field. Returns new value."""
        current = self.read_field(entity_id, field_path)
        if current is None:
            current = 0
        new_value = int(current) + delta
        self.write_field(entity_id, field_path, new_value)
        return new_value


# ---------------------------------------------------------------------------
# Entity normalization — provides virtual combat dict for flat schema
# ---------------------------------------------------------------------------


def _normalize_entity(entity: dict[str, Any]) -> dict[str, Any]:
    """Add a virtual `combat` dict with core fields for expression compat.

    T1.1 v3 stores core fields flat at entity top level.
    Expressions use `entity.combat.hp`, `entity.combat.ac`, etc.
    This creates a `combat` dict on the snapshot that merges:
    - Core flat fields (hp, ac, max_hp, etc.)
    - Any existing dynamic combat sub-dict (actions_remaining, etc.)

    Does not mutate the original.
    """
    result = deepcopy(entity)

    # Extract existing combat sub-dict (dynamic fields)
    existing_combat = result.get("combat", {})
    if not isinstance(existing_combat, dict):
        existing_combat = {}

    # Build virtual combat dict with core fields
    combat: dict[str, Any] = dict(existing_combat)

    for field in _CORE_COMBAT_FIELDS:
        if field in result:
            combat.setdefault(field, result[field])

    result["combat"] = combat
    return result


# ---------------------------------------------------------------------------
# Write-path remapping
# ---------------------------------------------------------------------------

_COMBAT_TO_FLAT: dict[str, str] = {
    # combat.X → X (core fields stored flat at entity top level)
    "hp": "hp",
    "max_hp": "max_hp",
    "temp_hp": "temp_hp",
    "ac": "ac",
    "is_alive": "is_alive",
    "initiative": "initiative",
    "speed": "speed",
    "size": "size",
    "conditions": "conditions",
    "death_saves": "death_saves",
    "abilities": "abilities",
    "inventory": "inventory",
    "equipment": "equipment",
    "resources": "resources",
    "progression": "progression",
}


def _map_write_path(path: str) -> str:
    """Remap runtime field paths to T1.1 entity field paths for writing.

    combat.hp → hp
    combat.actions_remaining → combat.actions_remaining (dynamic, pass-through)
    abilities.dex.modifier → abilities.dex.modifier (pass-through)
    """
    parts = path.split(".")

    if len(parts) >= 2 and parts[0] == "combat":
        field = parts[1]
        if field in _COMBAT_TO_FLAT:
            rest = ".".join(parts[2:])
            if rest:
                return f"{_COMBAT_TO_FLAT[field]}.{rest}"
            return _COMBAT_TO_FLAT[field]

    return path
