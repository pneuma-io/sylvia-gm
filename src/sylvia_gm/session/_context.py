"""Context Manager — scoped state snapshots for the LLM context.

T3.3: Instead of injecting the full state into every LLM call, the
ContextManager builds scoped snapshots tailored to the current situation.
This keeps context size manageable and prevents the LLM from seeing
irrelevant state.

Three primary scopes:
- combat: HP, AC, conditions, initiative order
- social: NPC attitudes, relationships, knowledge boundaries
- inventory: items, equipment, gold
- full: everything (used rarely, at session start)

Per PRD performance budget: snapshots should stay under 1500 characters.

Callers select a scope from authoritative session state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sylvia_gm.state.state_api import StateAPI


class ContextManager:
    """Scopes what the AI DM sees based on the current situation.

    Builds text snapshots from the state store, filtered to the
    domain most relevant to the player's action. Reduces token
    usage and keeps the LLM focused.

    Usage::

        ctx = ContextManager()
        snapshot = ctx.snapshot(store, "combat")
    """

    # ── Constants ────────────────────────────────────────────────────

    MAX_CHARS = 1500
    """Maximum characters per snapshot (PRD performance budget)."""

    # ── Public API ────────────────────────────────────────────────────

    def snapshot(self, store: "StateAPI", scope: str) -> str:
        """Return a text snapshot of the requested scope.

        Args:
            store: The StateAPI instance.
            scope: One of ``"combat"``, ``"social"``, ``"inventory"``, ``"full"``.

        Returns:
            A formatted text string (≤ MAX_CHARS).

        Raises:
            ValueError: If scope is not recognized.
        """
        scope = scope.lower().strip()
        if scope == "combat":
            return self._combat_snapshot(store)
        if scope == "social":
            return self._social_snapshot(store)
        if scope == "inventory":
            return self._inventory_snapshot(store)
        if scope == "full":
            return self._full_snapshot(store)
        raise ValueError(
            f"Unknown scope: {scope!r}. Must be one of: combat, social, inventory, full."
        )

    # ── Snapshot Builders ─────────────────────────────────────────────

    def _combat_snapshot(self, store: "StateAPI") -> str:
        """Build a combat-focused text snapshot.

        Includes HP, AC, conditions, and initiative order for all
        combat participants.
        """
        snap = store.snapshot("combat")
        participants = snap.get("participants", {})
        combat = snap.get("combat") or {}
        combat_history = snap.get("combat_history", [])

        lines: list[str] = []
        lines.append("=== COMBAT STATE ===")

        # Combat status
        status = combat.get("status", "inactive")
        round_num = combat.get("round", 0)
        lines.append(f"Status: {status} | Round: {round_num}")

        # Initiative order
        initiative_order = combat.get("initiative_order", [])
        if initiative_order:
            lines.append(f"Initiative: {' → '.join(initiative_order)}")

        # Participants
        if participants:
            lines.append("---")
            for pid, data in sorted(participants.items()):
                if not isinstance(data, dict):
                    continue
                name = data.get("name", pid)
                hp = data.get("hp", "?")
                max_hp = data.get("max_hp", "?")
                ac = data.get("ac", "?")
                conditions = data.get("conditions", [])
                alive = data.get("is_alive", True)

                status_str = ""
                if not alive:
                    status_str = " [DEAD]"
                elif conditions:
                    cond_str = ", ".join(conditions)
                    status_str = f" [{cond_str}]"

                lines.append(f"  {name}: HP {hp}/{max_hp} | AC {ac}{status_str}")

        # Recent combat history (last 3 entries)
        if combat_history:
            recent = combat_history[-3:]
            lines.append("---")
            lines.append("Recent:")
            for entry in recent:
                if isinstance(entry, dict):
                    lines.append(f"  - {entry.get('description', str(entry))}")
                else:
                    lines.append(f"  - {entry}")

        result = "\n".join(lines)
        return self._truncate(result)

    def _social_snapshot(self, store: "StateAPI") -> str:
        """Build a social-focused text snapshot.

        Includes NPC attitudes, relationships, and visible knowledge.
        """
        snap = store.snapshot("social")
        npcs = snap.get("npcs", {})
        party = snap.get("party", {})
        world = snap.get("world", {})

        lines: list[str] = []
        lines.append("=== SOCIAL STATE ===")

        # World context
        scene = world.get("current_scene") or world.get("current_location", "Unknown")
        time_of_day = world.get("time_of_day", "Unknown")
        day_num = world.get("day", "?")
        lines.append(f"Location: {scene} | {time_of_day} | Day {day_num}")

        # Party reputation
        reputation = party.get("reputation", {})
        if reputation:
            lines.append("Reputation:")
            for faction, rep in sorted(reputation.items()):
                lines.append(f"  {faction}: {rep}")

        # NPCs present
        if npcs:
            lines.append("---")
            lines.append("NPCs present:")
            for npc_id, npc in sorted(npcs.items()):
                if not isinstance(npc, dict):
                    continue
                name = npc.get("name", npc_id)
                role = npc.get("role") or "Unspecified"
                attitude = npc.get("attitude", "neutral")
                rel_score = npc.get("relationship", 0)

                rel_str = f"({attitude}"
                if abs(rel_score) > 3:
                    rel_str += f", {rel_score:+d}"
                rel_str += ")"

                lines.append(f"  [{name}] Role: {role} {rel_str}")

                # Visible knowledge
                knowledge = npc.get("knowledge", {})
                if knowledge:
                    visible_keys = [
                        k
                        for k, v in knowledge.items()
                        if isinstance(v, dict) and not v.get("is_secret", False)
                    ]
                    if visible_keys:
                        lines.append(f"    Knows about: {', '.join(visible_keys)}")

        if len(npcs) == 0:
            lines.append("No NPCs currently present.")

        # World flags relevant to social context
        flags = world.get("flags", {})
        social_flags = {
            k: v for k, v in flags.items() if k.startswith("social_") or k.startswith("reputation_")
        }
        if social_flags:
            lines.append("---")
            lines.append("Social flags:")
            for flag, value in sorted(social_flags.items()):
                lines.append(f"  {flag}: {value}")

        result = "\n".join(lines)
        return self._truncate(result)

    def _inventory_snapshot(self, store: "StateAPI") -> str:
        """Build an inventory-focused text snapshot.

        Includes items, equipment, and gold for all party members.
        """
        snap = store.snapshot("inventory")
        characters = snap.get("characters", {})
        shared = snap.get("party_shared_inventory", [])

        lines: list[str] = []
        lines.append("=== INVENTORY STATE ===")

        # Shared inventory
        if shared:
            lines.append("Party shared:")
            for item in shared:
                if isinstance(item, dict):
                    lines.append(f"  - {item.get('name', 'Unknown item')}")
                    if item.get("quantity", 1) > 1:
                        lines[-1] += f" x{item['quantity']}"
                else:
                    lines.append(f"  - {item}")

        # Character inventories
        for char_id, char_data in sorted(characters.items()):
            if not isinstance(char_data, dict):
                continue
            name = char_data.get("name", char_id)
            inventory = char_data.get("inventory", [])
            equipment = char_data.get("equipment", {})

            lines.append(f"{name}:")
            if inventory:
                for item in inventory:
                    if isinstance(item, dict):
                        item_name = item.get("name", str(item))
                        qty = item.get("quantity", 1)
                        if qty > 1:
                            lines.append(f"  - {item_name} x{qty}")
                        else:
                            lines.append(f"  - {item_name}")
                    else:
                        lines.append(f"  - {item}")
            else:
                lines.append("  (empty)")

            if equipment:
                lines.append("  Equipment:")
                for slot, item in sorted(equipment.items()):
                    if isinstance(item, dict):
                        item_name = item.get("name", str(item))
                    else:
                        item_name = str(item) if item else "—"
                    lines.append(f"    {slot}: {item_name}")

        if not characters and not shared:
            lines.append("No items in inventory.")

        result = "\n".join(lines)
        return self._truncate(result)

    def _full_snapshot(self, store: "StateAPI") -> str:
        """Build a full state snapshot (all domains).

        Used sparingly — at session start or when the player needs
        a complete overview.
        """
        parts = [
            self._combat_snapshot(store),
            self._social_snapshot(store),
            self._inventory_snapshot(store),
        ]
        result = "\n\n".join(parts)
        return self._truncate(result, max_chars=self.MAX_CHARS * 2)

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _truncate(text: str, max_chars: int = MAX_CHARS) -> str:
        """Truncate text to *max_chars* with an ellipsis marker.

        Args:
            text: The text to potentially truncate.
            max_chars: Maximum allowed characters.

        Returns:
            The (possibly truncated) text.
        """
        if len(text) <= max_chars:
            return text
        # "... [truncated]" is 15 characters
        cutoff = max_chars - 15
        if cutoff < 0:
            cutoff = 0
        return text[:cutoff] + "... [truncated]"
