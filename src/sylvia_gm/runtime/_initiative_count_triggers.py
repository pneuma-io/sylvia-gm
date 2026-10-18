"""Initiative-count-based triggers (lair actions / legendary actions).

Manages triggers that fire when the initiative count reaches a specific
value (e.g., "on count 20, the dragon uses its lair action").

The manager is stored in ctx.extras["initiative_triggers"] and
check_and_fire() is called during turn advancement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class InitiativeTrigger:
    """A trigger that fires on a specific initiative count.

    Attributes:
        trigger_id: Unique identifier.
        count: Initiative count to fire on (e.g., 20, 10, 0).
        procedure_name: Procedure to trigger.
        source: Entity or tool that registered this trigger.
        repeats: If True, triggers every time count hits (e.g., every
            round on count 20). If False, fires once then auto-deregisters.
        context_data: Extra data injected into ctx.vars when firing.
    """

    trigger_id: str
    count: int
    procedure_name: str
    source: str = ""
    repeats: bool = True
    context_data: dict[str, Any] = field(default_factory=dict)


class InitiativeTriggerManager:
    """Manages initiative-count-based triggers.

    Stored in ctx.extras["initiative_triggers"].

    Usage::

        mgr = InitiativeTriggerManager()
        mgr.register("lair_20", 20, "dragon_lair_action",
                      source="ancient_dragon", repeats=True)
        # ... during initiative advancement ...
        mgr.check_and_fire(ctx, current_count=20)
    """

    def __init__(self) -> None:
        self._triggers: dict[str, InitiativeTrigger] = {}

    # ── public API ────────────────────────────────────────────────────

    def register(
        self,
        trigger_id: str,
        count: int,
        procedure_name: str,
        *,
        source: str = "",
        repeats: bool = True,
        context_data: dict[str, Any] | None = None,
    ) -> InitiativeTrigger:
        """Register a new initiative-count trigger.

        Args:
            trigger_id: Unique ID for this trigger.
            count: Initiative count to fire on (e.g. 20, 10).
            procedure_name: Procedure to trigger.
            source: Entity/tool that registered this trigger.
            repeats: If True, fires every round at this count.
                If False, auto-deregisters after firing once.
            context_data: Extra vars injected into ctx.vars on fire.

        Returns:
            The registered InitiativeTrigger.
        """
        trigger = InitiativeTrigger(
            trigger_id=trigger_id,
            count=count,
            procedure_name=procedure_name,
            source=source,
            repeats=repeats,
            context_data=dict(context_data or {}),
        )
        self._triggers[trigger_id] = trigger
        return trigger

    def deregister(self, trigger_id: str) -> InitiativeTrigger | None:
        """Remove a trigger by ID.

        Returns the removed trigger, or None if not found.
        """
        return self._triggers.pop(trigger_id, None)

    def get(self, trigger_id: str) -> InitiativeTrigger | None:
        """Get a trigger without removing it."""
        return self._triggers.get(trigger_id)

    def get_all(self) -> list[InitiativeTrigger]:
        """Return all registered triggers."""
        return list(self._triggers.values())

    def get_by_count(self, count: int) -> list[InitiativeTrigger]:
        """Return all triggers that fire at a given initiative count."""
        return [t for t in self._triggers.values() if t.count == count]

    def check_and_fire(
        self,
        ctx: Any,
        current_count: int,
    ) -> list[str]:
        """Check and fire triggers for the current initiative count.

        Args:
            ctx: ExecutionContext with procedure_executor set.
            current_count: The current initiative count to check against.

        Returns:
            List of trigger IDs that fired.
        """
        fired: list[str] = []
        to_remove: list[str] = []

        for trigger in self._triggers.values():
            if trigger.count != current_count:
                continue

            # Inject context data
            for key, value in trigger.context_data.items():
                ctx.vars[key] = value
            ctx.vars["_trigger_id"] = trigger.trigger_id
            ctx.vars["_trigger_count"] = current_count
            ctx.vars["_trigger_source"] = trigger.source

            # Always mark as fired
            fired.append(trigger.trigger_id)

            # Fire procedure via ctx.procedure_executor (LOCK-1)
            if ctx.procedure_executor:
                try:
                    ctx.procedure_executor(trigger.procedure_name, ctx)
                except Exception:
                    pass

            if not trigger.repeats:
                to_remove.append(trigger.trigger_id)

        # Clean up one-shot triggers
        for tid in to_remove:
            self._triggers.pop(tid, None)

        return fired

    def clear(self) -> None:
        """Remove all registered triggers."""
        self._triggers.clear()

    def __len__(self) -> int:
        return len(self._triggers)

    def __repr__(self) -> str:
        return f"InitiativeTriggerManager(triggers={len(self._triggers)})"
