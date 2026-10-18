"""Turn system engine — manages resource pools and turn advancement.

The turn system is mode-driven: each mode declares its turn structure in
the ruleset YAML, and this engine enforces it at runtime.
"""

from __future__ import annotations

from typing import Any

from sylvia_gm.ruleset.schema import TurnSystemConfig
from sylvia_gm.runtime._types import ResourcePoolState, TurnState


class TurnSystem:
    """Manages turn state and resource pools for an active mode."""

    def __init__(self, config: TurnSystemConfig, participants: list[str] | None = None):
        """Initialize turn system from ruleset config.

        Args:
            config: TurnSystemConfig from the mode's ruleset declaration
            participants: Initial list of entity IDs participating in this mode
        """
        self.config = config
        self.state = TurnState(
            round=1,
            resolution_style=config.type,
            participants=participants or [],
            current_actor=None,
            current_resources={},
        )

    def consume(self, resource: str, amount: int | float = 1) -> bool:
        """Try to consume from a resource pool.

        Args:
            resource: Pool name (e.g. "action", "movement")
            amount: Amount to consume

        Returns:
            True if consumption succeeded, False if insufficient resources
        """
        if resource not in self.state.current_resources:
            return False

        pool = self.state.current_resources[resource]
        if pool.remaining < amount:
            return False

        pool.remaining -= amount
        return True

    def check(self, resource: str) -> int | float:
        """Get remaining amount in a resource pool.

        Args:
            resource: Pool name

        Returns:
            Remaining amount, or 0 if pool doesn't exist
        """
        if resource not in self.state.current_resources:
            return 0
        return self.state.current_resources[resource].remaining

    def grant(self, resource: str, amount: int | float) -> None:
        """Add to a resource pool.

        Args:
            resource: Pool name
            amount: Amount to add
        """
        if resource not in self.state.current_resources:
            # Pool doesn't exist — create it with no refresh rule
            self.state.current_resources[resource] = ResourcePoolState(
                remaining=amount, refresh="never", unit=None
            )
        else:
            self.state.current_resources[resource].remaining += amount

    def refresh_pools(self, trigger: str) -> None:
        """Reset pools marked with this refresh trigger.

        Args:
            trigger: Refresh trigger ("turn_start", "round_start", "phase_start")
        """
        # Find the actor config that matches current actor
        actor_config = None
        for actor in self.config.actors:
            if actor.entity == self.state.current_actor or (
                actor.entity is None and self.state.current_actor is None
            ):
                actor_config = actor
                break

        if actor_config is None:
            return

        # Refresh pools that match this trigger
        for resource_name, resource_config in actor_config.resources.items():
            if resource_config.refresh == trigger:
                # Resolve count from source if specified, else use count
                if resource_config.source:
                    # TODO: Resolve entity.speed at runtime
                    # For now, use a placeholder
                    count = 30  # Will be resolved from entity data
                else:
                    count = resource_config.count or 0

                self.state.current_resources[resource_name] = ResourcePoolState(
                    remaining=count,
                    refresh=resource_config.refresh,
                    unit=resource_config.unit,
                )

    def advance_turn(self) -> None:
        """Advance to the next actor in the turn order."""
        if not self.state.participants:
            return

        if self.state.current_actor is None:
            # Start of turn — set to first participant
            self.state.current_actor = self.state.participants[0]
        else:
            # Find current actor index
            try:
                current_idx = self.state.participants.index(self.state.current_actor)
            except ValueError:
                # Current actor not in participants — reset to first
                self.state.current_actor = self.state.participants[0]
                return

            # Advance to next participant
            next_idx = current_idx + 1
            if next_idx >= len(self.state.participants):
                # End of round — increment round counter, reset to first
                self.state.round += 1
                self.state.current_actor = self.state.participants[0]
                self.refresh_pools("round_start")
            else:
                self.state.current_actor = self.state.participants[next_idx]

            # Refresh turn-level pools for new actor
            self.refresh_pools("turn_start")

    def add_participant(self, entity_id: str) -> None:
        """Add an entity to the turn order.

        Args:
            entity_id: Entity ID to add
        """
        if entity_id not in self.state.participants:
            self.state.participants.append(entity_id)

    def remove_participant(self, entity_id: str) -> None:
        """Remove an entity from the turn order.

        Args:
            entity_id: Entity ID to remove
        """
        if entity_id in self.state.participants:
            self.state.participants.remove(entity_id)

            # If removed entity was current actor, advance to next
            if self.state.current_actor == entity_id:
                if self.state.participants:
                    self.advance_turn()
                else:
                    self.state.current_actor = None

    def is_complete(self) -> bool:
        """Check if the mode's exit condition is met.

        Returns:
            True if exit condition procedure returns true, False otherwise
        """
        # TODO: Call exit_condition procedure from ruleset
        # For now, return False (mode never auto-exits)
        return False
