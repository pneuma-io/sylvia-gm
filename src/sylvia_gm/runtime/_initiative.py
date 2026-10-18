"""Initiative manager — initiative rolling, turn ordering, and turn advancement.

Provides initiative roll resolution, turn tracking, and turn-order
management for the engine. Does NOT contain ruleset-specific logic
(e.g., D&D vs Cairn initiative mechanics) — those are configured
in the ruleset YAML.

Integrates with the existing sort effect handler for ordering.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any


def roll_initiative(
    combatants: list[str],
    initiative_expr: str,
    state_bridge: Any,
    ctx: Any,
    *,
    modifier_field: str | None = None,
    modifier_minimum: int | None = None,
    modifier_maximum: int | None = None,
    required_truthy_field: str | None = None,
    label_field: str | None = None,
) -> list[dict[str, Any]]:
    """Roll initiative for all combatants and return ordered results.

    Args:
        combatants: List of entity IDs to roll for.
        initiative_expr: Dice expression or field reference for initiative.
            e.g., "d20 + combat.initiative" or just "d20".
        state_bridge: StateBridge for reading entity stats.
        ctx: ExecutionContext for expression evaluation.

    Returns:
        List of dicts with entity_id and initiative_result, sorted descending.
    """
    results: list[dict[str, Any]] = []
    for entity_id in combatants:
        # Fetch entity for modifier resolution
        if state_bridge and entity_id not in ctx.entities:
            with suppress(Exception):
                ctx.entities[entity_id] = state_bridge.get_entity(entity_id)

        entity = ctx.entities.get(entity_id, {})
        if required_truthy_field and not _nested_field(entity, required_truthy_field):
            raise ValueError(
                f"Initiative combatant {entity_id!r} must have a truthy "
                f"{required_truthy_field!r} field"
            )

        modifier = 0
        if modifier_field:
            raw_modifier = _nested_field(entity, modifier_field)
            if not isinstance(raw_modifier, (int, float)) or isinstance(raw_modifier, bool):
                raise ValueError(
                    f"Initiative combatant {entity_id!r} requires a numeric "
                    f"{modifier_field!r} field"
                )
            modifier = int(raw_modifier)
            if modifier_minimum is not None and modifier < modifier_minimum:
                raise ValueError(
                    f"Initiative combatant {entity_id!r} has {modifier_field!r} below "
                    f"{modifier_minimum}"
                )
            if modifier_maximum is not None and modifier > modifier_maximum:
                raise ValueError(
                    f"Initiative combatant {entity_id!r} has {modifier_field!r} above "
                    f"{modifier_maximum}"
                )

        # Parse and roll the package expression, then apply an optional
        # package-selected entity modifier.
        init_val, roll = _roll_initiative_expr(initiative_expr, ctx)
        entry = {
            "entity_id": entity_id,
            "initiative_result": init_val + modifier,
            "roll": roll,
            "modifier": modifier + (int(roll["modifier"]) if roll is not None else 0),
        }
        if label_field:
            label = _nested_field(entity, label_field)
            if not isinstance(label, str) or not label.strip() or len(label) > 200:
                raise ValueError("Initiative requires the declared entity display label")
            entry["label"] = label
        results.append(entry)

    # Sort descending (highest initiative first)
    results.sort(key=lambda r: r["initiative_result"], reverse=True)
    return results


def _nested_field(entity: Any, field_path: str) -> Any:
    """Read one dot-separated field from an initiative combatant snapshot."""

    current = entity
    for segment in field_path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    return current


def advance_turn(combat_state: dict[str, Any]) -> str | None:
    """Advance to the next combatant in turn order.

    Returns the entity ID of the current actor, or None if combat is empty.

    Args:
        combat_state: Mutable combat state dict with round, turn_index, order.

    Returns:
        The entity ID of the new current actor, or None.
    """
    order = combat_state.get("order", [])
    if not order:
        return None

    turn_index = combat_state.get("turn_index", -1)

    # Check if we need to advance the round
    if turn_index >= len(order) - 1:
        # End of round — advance to new round, reset turn
        combat_state["round"] = combat_state.get("round", 0) + 1
        turn_index = 0
        combat_state["turn_index"] = turn_index
        combat_state["_round_advanced"] = True
    else:
        turn_index += 1
        combat_state["turn_index"] = turn_index
        combat_state["_round_advanced"] = False

    current = order[turn_index]
    if isinstance(current, dict):
        current_id = current.get("entity_id", current.get("id", ""))
    else:
        current_id = str(current)

    combat_state["current_actor"] = current_id
    return current_id


def get_current_actor(combat_state: dict[str, Any]) -> str | None:
    """Return the entity ID of the current actor.

    Args:
        combat_state: Combat state dict.

    Returns:
        Entity ID of the current actor, or None.
    """
    order = combat_state.get("order", [])
    if not order:
        return None
    turn_index = combat_state.get("turn_index", 0)
    if turn_index < 0 or turn_index >= len(order):
        return None
    current = order[turn_index]
    if isinstance(current, dict):
        return current.get("entity_id", current.get("id", ""))
    return str(current)


def insert_combatant(
    combat_state: dict[str, Any],
    entity_id: str,
    initiative_result: int,
) -> None:
    """Insert a combatant into the turn order at the correct position.

    The combatant is placed in initiative-result descending order.
    Adjusts turn_index if inserting before the current actor.

    Args:
        combat_state: Mutable combat state dict.
        entity_id: Entity ID to insert.
        initiative_result: Initiative roll value for ordering.
    """
    order: list = combat_state.setdefault("order", [])
    turn_index = combat_state.get("turn_index", 0)

    new_entry: dict[str, Any] = {
        "entity_id": entity_id,
        "initiative_result": initiative_result,
    }

    # Find insertion point (maintain descending order)
    insert_at = len(order)
    for i, entry in enumerate(order):
        entry_init = 0
        if isinstance(entry, dict):
            entry_init = entry.get("initiative_result", 0)
        if initiative_result >= entry_init:
            insert_at = i
            break

    order.insert(insert_at, new_entry)

    # Adjust turn_index if we inserted before or at current position
    if insert_at <= turn_index and turn_index < len(order) - 1:
        combat_state["turn_index"] = turn_index + 1


def _roll_initiative_expr(expr: str, ctx: Any) -> tuple[int, dict[str, object] | None]:
    """Parse and roll a dice expression like 'd20' or 'd20 + 5'.

    Retain the actual dice alongside the total; a fixed value has no fabricated roll.
    """
    from sylvia_gm.dice import roll as dice_roll

    expr = expr.strip()
    modifier = 0

    # Check for inline modifier (e.g., "d20 + 5")
    if "+" in expr:
        parts = expr.rsplit("+", 1)
        expr = parts[0].strip()
        try:
            modifier = int(parts[1].strip())
        except ValueError:
            modifier = 0

    # Parse dice expression: "d20", "2d6", etc.
    expr_lower = expr.lower()
    if expr_lower.startswith("d"):
        sides = int(expr_lower[1:]) if expr_lower[1:] else 20
        count = 1
    elif "d" in expr_lower:
        count_str, sides_str = expr_lower.split("d", 1)
        count = int(count_str) if count_str else 1
        sides = int(sides_str) if sides_str else 20
    else:
        # Plain number (no dice) — treat as fixed value
        try:
            return int(expr) + modifier, None
        except ValueError:
            return modifier, None

    result = dice_roll(sides, count, modifier)
    return result.total + result.modifier, result.to_dict()
