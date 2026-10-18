"""Time manager — campaign time advancement and tracking.

Handles duration parsing, time arithmetic, and persistence
on the _campaign entity. Triggers on_time_advanced procedures
via ctx.procedure_executor (LOCK-1).

Time state stored on _campaign entity (via StateBridge):
  day         (int, starting at 1)
  hour        (int, 0-23)
  minute      (int, 0-59)
  time_of_day (str, auto-computed: morning/afternoon/evening/night)
  round       (int, total elapsed rounds — backward compat)

Time advancement rules:
  1 round   = 6 seconds  (10 rounds = 1 minute)
  60 minutes = 1 hour
  24 hours   = 1 day

  time_of_day auto-updates:
    6-11  → morning
    12-17 → afternoon
    18-21 → evening
    22-5  → night

State Memo injection:
  After advancing time, ctx.vars["_current_time"] is set to the
  formatted string: "Day 3, Evening, 18:30"
"""

from __future__ import annotations

import re
from typing import Any

# Duration parsing: maps unit strings to relative magnitudes
# All times are tracked in a hierarchy: round → minute → hour → day → month → year
# The smallest unit is the round; larger units carry over.
_TIME_UNITS: dict[str, int] = {
    "round": 1,
    "rounds": 1,
    "minute": 10,       # 10 rounds per minute (6 seconds each)
    "minutes": 10,
    "hour": 600,         # 60 minutes * 10 rounds
    "hours": 600,
    "day": 14400,        # 24 hours * 600 rounds
    "days": 14400,
    "month": 432000,     # 30 days
    "months": 432000,
    "year": 5184000,     # 12 months
    "years": 5184000,
}

# Regex to parse a duration string: "1 round", "10 minutes", "2 hours", etc.
_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s+(\w+)\s*$")


def compute_time_of_day(hour: int) -> str:
    """Compute time_of_day enum from hour (0-23).

    Ranges:
        6-11  → morning
        12-17 → afternoon
        18-21 → evening
        22-5  → night

    Args:
        hour: Integer hour of the day (0-23).

    Returns:
        One of: "morning", "afternoon", "evening", "night".
    """
    if 6 <= hour <= 11:
        return "morning"
    elif 12 <= hour <= 17:
        return "afternoon"
    elif 18 <= hour <= 21:
        return "evening"
    else:
        return "night"


def _normalize_time_fields(
    day: int,
    hour: int,
    minute: int,
) -> tuple[int, int, int, int]:
    """Normalize time fields with carry-over.

    60 minutes → +1 hour,  24 hours → +1 day.
    Minutes and hours wrap, day accumulates.

    Args:
        day: Current day (1-indexed).
        hour: Current hour (0+).
        minute: Current minute (0+).

    Returns:
        (day, hour, minute, days_advanced)
    """
    extra_hours = minute // 60
    minute = minute % 60
    hour = hour + extra_hours
    extra_days = hour // 24
    hour = hour % 24
    day = day + extra_days
    return day, hour, minute, extra_days


def _minutes_from_rounds(rounds: int) -> int:
    """Convert total rounds to total minutes."""
    return rounds // 10


def parse_duration(duration_str: str) -> int:
    """Parse a duration string into rounds.

    Args:
        duration_str: e.g., "1 round", "10 minutes", "2 hours", "1 day".

    Returns:
        Total rounds represented by the duration.

    Raises:
        ValueError: If the duration string cannot be parsed.
    """
    match = _DURATION_RE.match(duration_str)
    if not match:
        raise ValueError(
            f"Cannot parse duration: {duration_str!r}. "
            f"Expected format: '<number> <unit>' (e.g., '1 round', '10 minutes')."
        )

    amount = float(match.group(1))
    unit = match.group(2).lower()
    multiplier = _TIME_UNITS.get(unit)
    if multiplier is None:
        raise ValueError(
            f"Unknown time unit: {match.group(2)!r}. "
            f"Valid units: {sorted(set(_TIME_UNITS.keys()))}"
        )

    return int(amount * multiplier)


def advance_time(
    duration: str,
    combat_state: dict[str, Any],
    state_bridge: Any,
    ctx: Any = None,
) -> dict[str, Any]:
    """Advance world time by a duration string.

    Updates combat_state.round/turn and the _campaign entity's time dict
    (day, hour, minute, time_of_day, round). Triggers on_time_advanced
    procedure via ctx.procedure_executor (LOCK-1).

    Injects formatted time into ctx.vars["_current_time"] for State Memo.

    Args:
        duration: Duration string ("1 round", "10 minutes", "2 hours", etc.).
        combat_state: Mutable combat state dict (round, turn_index, order).
        state_bridge: StateBridge instance for persistence.
        ctx: ExecutionContext (for procedure triggering and State Memo injection).

    Returns:
        Updated combat_state dict.
    """
    rounds_to_add = parse_duration(duration)
    current_round = int(combat_state.get("round", 0))
    combat_state["round"] = current_round + rounds_to_add

    # Persist campaign-level time on _campaign entity
    time_str = None
    if state_bridge is not None:
        try:
            state_bridge._ensure_campaign_entity()
            time_data = state_bridge.read_field("_campaign", "time") or {}
            if not isinstance(time_data, dict):
                time_data = {}

            # Read existing time fields with defaults
            day = int(time_data.get("day", 1))
            hour = int(time_data.get("hour", 0))
            minute = int(time_data.get("minute", 0))
            old_total_rounds = int(time_data.get("round", 0))

            # Add rounds to the campaign total
            new_total_rounds = old_total_rounds + rounds_to_add

            # Convert added rounds to minutes and add to existing time
            added_minutes = _minutes_from_rounds(rounds_to_add)
            new_minute = minute + added_minutes

            # Normalize (carry minutes → hours → days)
            day, hour, minute, _days_advanced = _normalize_time_fields(
                day, hour, new_minute
            )

            # Compute time_of_day
            time_of_day = compute_time_of_day(hour)

            # Build updated time data
            time_data.update({
                "day": day,
                "hour": hour,
                "minute": minute,
                "time_of_day": time_of_day,
                "round": new_total_rounds,
                "rounds_advanced": rounds_to_add,
            })
            state_bridge.write_field("_campaign", "time", dict(time_data))

            # Build formatted time string for State Memo
            time_str = f"Day {day}, {time_of_day.title()}, {hour:02d}:{minute:02d}"

        except Exception:
            pass

    # Inject time into ctx.vars for State Memo
    if ctx is not None and time_str:
        ctx.vars["_current_time"] = time_str

    # LOCK-1: Trigger on_time_advanced procedure
    if ctx is not None and ctx.procedure_executor:
        try:
            ctx.procedure_executor("on_time_advanced", ctx)
        except Exception:
            pass

    return combat_state


def get_current_time(combat_state: dict[str, Any]) -> str:
    """Return a human-readable time string for State Memo injection.

    Args:
        combat_state: The combat state dict.

    Returns:
        A string like "Round 3" or "Round 0 (not in combat)".
    """
    round_num = combat_state.get("round", 0)
    if round_num == 0:
        return "Round 0 (not in combat)"
    return f"Round {round_num}"


def get_current_datestamp(
    state_bridge: Any,
    combat_state: dict[str, Any] | None = None,
) -> str:
    """Return a formatted time string for the State Memo.

    Format: "Day 3, Evening, 18:30"

    Reads day, hour, minute, time_of_day from the _campaign entity.
    Falls back to combat_state round-only if no campaign time exists.

    Args:
        state_bridge: StateBridge for reading _campaign time.
        combat_state: Optional combat state.

    Returns:
        A time string for the State Memo.
    """
    parts: list[str] = []

    # Try campaign time first
    if state_bridge is not None:
        try:
            time_data = state_bridge.read_field("_campaign", "time") or {}
            if isinstance(time_data, dict) and time_data:
                day = time_data.get("day")
                hour = time_data.get("hour")
                minute = time_data.get("minute")
                time_of_day = time_data.get("time_of_day") or (
                    compute_time_of_day(int(hour)) if hour is not None else None
                )

                # Build the main time string: "Day N, TimeOfDay, HH:MM"
                if day is not None:
                    main_parts = [f"Day {day}"]
                    if time_of_day:
                        main_parts.append(time_of_day.title())
                    if hour is not None and minute is not None:
                        main_parts.append(f"{int(hour):02d}:{int(minute):02d}")
                    elif hour is not None:
                        main_parts.append(f"{int(hour):02d}:00")
                    parts.append(", ".join(main_parts))
        except Exception:
            pass

    # Fall back to combat round if available
    if combat_state:
        round_num = combat_state.get("round", 0)
        if round_num > 0:
            parts.append(f"Round {round_num}")

    # If we have campaign time but also in combat, add round after
    # (The campaign time is already first in parts)

    if not parts:
        return "Outside of time"

    return ", ".join(parts)


def get_time_for_context(campaign_time: dict[str, Any] | None = None) -> str:
    """Produce a compact time string for injection into context.

    Args:
        campaign_time: The time dict from _campaign entity, or None.

    Returns:
        Formatted string like "Day 3, Evening, 18:30" or empty string.
    """
    if not campaign_time or not isinstance(campaign_time, dict):
        return ""

    day = campaign_time.get("day")
    hour = campaign_time.get("hour")
    minute = campaign_time.get("minute")
    time_of_day = campaign_time.get("time_of_day") or (
        compute_time_of_day(int(hour)) if hour is not None else None
    )

    parts = []
    if day is not None:
        parts.append(f"Day {day}")
    if time_of_day:
        parts.append(time_of_day.title())
    if hour is not None and minute is not None:
        parts.append(f"{int(hour):02d}:{int(minute):02d}")
    elif hour is not None:
        parts.append(f"{int(hour):02d}:00")

    return ", ".join(parts) if parts else ""
