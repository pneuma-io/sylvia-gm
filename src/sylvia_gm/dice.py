"""Dice rolling utility — pure number computation.

This module computes numbers only. It provides deterministic (seedable) random
number generation, standard TTRPG dice mechanics, a DC-resolution helper, and
JSON-serializable result objects.

**Design philosophy (PURE UTILITY):**

- **Game semantics** (crits, auto-success/fail, expanded crit ranges, modifier
  application policy) live in the **ruleset runtime** (T1.2 ruleset parser +
  T1.4 combat engine).

- **Display formatting** (human-readable strings, CSS classes, channel-aware
  markup, backtick wrapping) lives in the **UI layer** (T4.1 chat formatting
  engine).

- **The dice module** returns ``RollResult`` and ``CheckResult`` dataclasses
  containing raw numeric data only. No ``formatted()`` methods. No crit
  booleans. No labels. No adjusted totals.

This separation means the dice module **never changes** when a new ruleset is
loaded or when the UI framework changes.

**Edge cases documented in this module:**

- ``seed=0`` is a valid deterministic seed (matching Python's convention).
- The module-level RNG is NOT thread-safe (v0.1 is single-threaded).
- d100 range is 1–100 inclusive (not 0–99).
- No combined product cap — per-parameter caps (1000 sides, 1000 count) are
  safety rails, not game semantics.
- ``from_dict()`` is deferred to v0.2.

**Stability:** ``RollResult`` and ``CheckResult`` dataclasses form a public API
contract. ``to_dict()`` output format is a serialization contract. Adding
fields is non-breaking; removing/renaming fields is breaking.

API Summary
-----------

>>> from sylvia_gm.dice import (
...     Die, RollResult, CheckResult, PoolResult, HungerPoolResult,
...     roll, d20, d6, d4, d8, d10, d12, d100, ndn,
...     advantage, disadvantage, check,
...     explode, count_successes, reroll, hunger_roll,
...     set_seed, get_seed, reseed,
... )

Total public symbols: 24.
"""

from __future__ import annotations

import dataclasses
import enum
import os
import random

# ---------------------------------------------------------------------------
# RNG state (module-level, single-threaded)
# ---------------------------------------------------------------------------

_rng: random.Random = random.Random(os.urandom(16))
_current_seed: int | None = None


def set_seed(seed: int) -> None:
    """Seed the module-level RNG for deterministic dice rolls.

    All subsequent rolls will produce the same sequence for the same seed.
    Use ``get_seed()`` to retrieve the current seed for session replay.
    """
    global _current_seed
    _current_seed = seed
    _rng.seed(seed)


def get_seed() -> int | None:
    """Return the current seed, or None if the RNG is using system entropy.

    Returns the integer passed to the most recent ``set_seed()`` call, or
    ``None`` if ``reseed()`` was called (or no seed was ever set).
    """
    return _current_seed


def reseed() -> None:
    """Re-seed from system entropy, returning to non-deterministic mode."""
    global _current_seed, _rng
    _current_seed = None
    _rng = random.Random(os.urandom(16))


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class Die(enum.Enum):
    """Standard TTRPG die types.

    >>> Die.D20.value
    20
    >>> Die.D6.value
    6
    """

    D4 = 4
    D6 = 6
    D8 = 8
    D10 = 10
    D12 = 12
    D20 = 20
    D100 = 100


@dataclasses.dataclass(frozen=True)
class RollResult:
    """Immutable result of one or more dice rolls.

    Attributes:
        sides: Number of faces on each die (e.g., 20).
        count: Number of dice rolled (e.g., 3 for 3d6).
        results: Individual die results as a tuple.
        total: Effective result — sum for normal rolls, max for advantage,
               min for disadvantage.
        modifier: Flat modifier stored for consumer reference. **Not** added
                  to ``total``. The consumer computes ``total + modifier``
                  when needed.

    >>> r = RollResult(sides=6, count=3, results=(4, 2, 6), total=12, modifier=0)
    >>> r.total
    12
    >>> r.to_dict()["results"]
    [4, 2, 6]
    """

    sides: int
    count: int
    results: tuple[int, ...]
    total: int
    modifier: int = 0

    def to_dict(self) -> dict[str, object]:
        """Serialize to a JSON-compatible dictionary.

        >>> r = RollResult(20, 1, (17,), 17, 5)
        >>> r.to_dict()
        {'sides': 20, 'count': 1, 'results': [17], 'total': 17, 'modifier': 5}
        """
        return {
            "sides": self.sides,
            "count": self.count,
            "results": list(self.results),
            "total": self.total,
            "modifier": self.modifier,
        }


@dataclasses.dataclass(frozen=True)
class CheckResult:
    """Immutable result of a DC (difficulty class) check.

    Attributes:
        success: Whether the effective roll meets or beats the DC.
        roll_result: The underlying ``RollResult`` (with modifier stored).
        dc: The difficulty class target.
        margin: ``effective_total - dc`` (positive = pass by N,
                negative = fail by N).
    """

    success: bool
    roll_result: RollResult
    dc: int
    margin: int

    def to_dict(self) -> dict[str, object]:
        """Serialize to a JSON-compatible dictionary.

        >>> from sylvia_gm.dice import RollResult
        >>> rr = RollResult(20, 1, (17,), 17, 5)
        >>> cr = CheckResult(True, rr, 15, 7)
        >>> d = cr.to_dict()
        >>> d["success"]
        True
        >>> d["dc"]
        15
        """
        return {
            "success": self.success,
            "dc": self.dc,
            "margin": self.margin,
            "roll_result": self.roll_result.to_dict(),
        }


@dataclasses.dataclass(frozen=True)
class PoolResult:
    """Immutable result of a dice pool with success counting.

    Attributes:
        sides: Number of faces on each die.
        count: Number of dice rolled.
        results: Individual die results as a tuple.
        threshold: Minimum value to count as a success.
        successes: Number of dice >= threshold (plus modifier).
        failures: Number of dice < threshold.
        botches: Number of dice that rolled 1.
        modifier: Flat bonus successes.

    >>> pr = PoolResult(sides=10, count=5, results=(8, 3, 6, 1, 10),
    ...                 threshold=6, successes=3, failures=2, botches=1, modifier=0)
    >>> pr.successes
    3
    """

    sides: int
    count: int
    results: tuple[int, ...]
    threshold: int
    successes: int
    failures: int
    botches: int
    modifier: int = 0

    def to_dict(self) -> dict[str, object]:
        """Serialize to a JSON-compatible dictionary.

        >>> pr = PoolResult(10, 5, (8, 3, 6, 1, 10), 6, 3, 2, 1)
        >>> pr.to_dict()
        {'sides': 10, 'count': 5, 'results': [8, 3, 6, 1, 10], 'threshold': 6, 'successes': 3, 'failures': 2, 'botches': 1, 'modifier': 0}
        """
        return {
            "sides": self.sides,
            "count": self.count,
            "results": list(self.results),
            "threshold": self.threshold,
            "successes": self.successes,
            "failures": self.failures,
            "botches": self.botches,
            "modifier": self.modifier,
        }


@dataclasses.dataclass(frozen=True)
class HungerPoolResult:
    """Immutable result of a dice pool with hunger dice (VtM style).

    Attributes:
        regular_results: Results from normal dice.
        hunger_results: Results from hunger dice.
        successes: Total successes (>= threshold).
        failures: Total failures (< threshold).
        bestial_failures: Count of hunger dice that rolled 1.
        messy_criticals: Count of pairs (hunger 10 + any other 10).
        threshold: Minimum value for a success.
        modifier: Flat bonus successes.

    >>> hpr = HungerPoolResult(
    ...     regular_results=(8, 4, 6, 3, 10, 5),
    ...     hunger_results=(9, 1),
    ...     successes=5, failures=3, bestial_failures=1,
    ...     messy_criticals=0, threshold=6, modifier=0,
    ... )
    >>> hpr.bestial_failures
    1
    """

    regular_results: tuple[int, ...]
    hunger_results: tuple[int, ...]
    successes: int
    failures: int
    bestial_failures: int
    messy_criticals: int
    threshold: int
    modifier: int = 0

    def to_dict(self) -> dict[str, object]:
        """Serialize to a JSON-compatible dictionary.

        >>> hpr = HungerPoolResult((8, 4, 6), (9, 1), 3, 2, 1, 0, 6)
        >>> d = hpr.to_dict()
        >>> d['regular_results']
        [8, 4, 6]
        >>> d['hunger_results']
        [9, 1]
        """
        return {
            "regular_results": list(self.regular_results),
            "hunger_results": list(self.hunger_results),
            "successes": self.successes,
            "failures": self.failures,
            "bestial_failures": self.bestial_failures,
            "messy_criticals": self.messy_criticals,
            "threshold": self.threshold,
            "modifier": self.modifier,
        }


# ---------------------------------------------------------------------------
# Internal validation
# ---------------------------------------------------------------------------

_MAX_SIDES: int = 1000
_MAX_COUNT: int = 1000


def _validate_modifier(modifier: object) -> None:
    """Raise TypeError if *modifier* is not an int."""
    if not isinstance(modifier, int):
        raise TypeError("modifier must be int")


def _resolve_sides(sides: int | Die) -> int:
    """Return the integer number of sides, or raise on invalid input."""
    if isinstance(sides, Die):
        return sides.value
    if isinstance(sides, int):
        if sides < 2:
            raise ValueError("Die must have at least 2 sides")
        if sides > _MAX_SIDES:
            raise ValueError(f"Die sides exceeds maximum ({_MAX_SIDES})")
        return sides
    raise TypeError("sides must be int or Die")


def _validate_count(count: object) -> None:
    """Raise on invalid die count."""
    if not isinstance(count, int):
        raise TypeError("count must be int")
    if count < 1:
        raise ValueError("Must roll at least 1 die")
    if count > _MAX_COUNT:
        raise ValueError(f"Die count exceeds maximum ({_MAX_COUNT})")


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------


def roll(sides: int | Die, count: int = 1, modifier: int = 0) -> RollResult:
    """Roll ``count`` dice with ``sides`` faces each.

    Args:
        sides: Number of faces (``int``) or a :class:`Die` enum member.
        count: Number of dice to roll (default 1).
        modifier: Flat modifier stored on the result. **Not** added to
                  ``total`` — the consumer computes ``total + modifier``.

    Returns:
        ``RollResult`` with individual results, total (sum of results),
        and modifier.

    Raises:
        ValueError: If ``sides < 2``, ``sides > 1000``, ``count < 1``, or
                   ``count > 1000``.
        TypeError: If ``sides`` is not ``int`` or :class:`Die`, or if
                   ``modifier`` is not ``int``.

    >>> roll(20)
    RollResult(sides=20, count=1, results=(...,), total=..., modifier=0)
    >>> roll(6, 3, modifier=2)
    RollResult(sides=6, count=3, results=(..., ..., ...), total=..., modifier=2)
    """
    _validate_modifier(modifier)
    actual_sides: int = _resolve_sides(sides)
    _validate_count(count)

    results: tuple[int, ...] = tuple(
        _rng.randint(1, actual_sides) for _ in range(count)
    )
    return RollResult(
        sides=actual_sides,
        count=count,
        results=results,
        total=sum(results),
        modifier=modifier,
    )


# -- Convenience functions --------------------------------------------------


def d4(modifier: int = 0) -> RollResult:
    """Roll 1d4. Equivalent to ``roll(4, 1, modifier)``."""
    return roll(4, 1, modifier)


def d6(modifier: int = 0) -> RollResult:
    """Roll 1d6. Equivalent to ``roll(6, 1, modifier)``."""
    return roll(6, 1, modifier)


def d8(modifier: int = 0) -> RollResult:
    """Roll 1d8. Equivalent to ``roll(8, 1, modifier)``."""
    return roll(8, 1, modifier)


def d10(modifier: int = 0) -> RollResult:
    """Roll 1d10. Equivalent to ``roll(10, 1, modifier)``."""
    return roll(10, 1, modifier)


def d12(modifier: int = 0) -> RollResult:
    """Roll 1d12. Equivalent to ``roll(12, 1, modifier)``."""
    return roll(12, 1, modifier)


def d20(modifier: int = 0) -> RollResult:
    """Roll 1d20. Equivalent to ``roll(20, 1, modifier)``."""
    return roll(20, 1, modifier)


def d100(modifier: int = 0) -> RollResult:
    """Roll 1d100 (range 1–100 inclusive). Equivalent to ``roll(100, 1, modifier)``."""
    return roll(100, 1, modifier)


def ndn(n: int, sides: int, modifier: int = 0) -> RollResult:
    """Roll ``n`` dice with ``sides`` faces each.

    ``ndn(3, 6)`` is ``3d6``.
    """
    return roll(sides, n, modifier)


# -- Advantage / Disadvantage ------------------------------------------------


def advantage(modifier: int = 0) -> RollResult:
    """Roll 2d20 with advantage: take the higher result.

    Uses ``_rng.randint()`` directly — does NOT delegate to ``roll(count=2)``,
    because ``roll(count=2)`` would **sum** both dice, producing
    ``total = die1 + die2`` (range 2–40). Advantage requires
    ``total = max(die1, die2)`` (range 1–20).

    Returns ``RollResult`` with ``count=2``, ``results=(higher, lower)`` sorted
    descending, ``total=higher``.
    """
    _validate_modifier(modifier)
    a = _rng.randint(1, 20)
    b = _rng.randint(1, 20)
    higher, lower = (a, b) if a >= b else (b, a)
    return RollResult(
        sides=20,
        count=2,
        results=(higher, lower),
        total=higher,
        modifier=modifier,
    )


def disadvantage(modifier: int = 0) -> RollResult:
    """Roll 2d20 with disadvantage: take the lower result.

    Uses ``_rng.randint()`` directly — does NOT delegate to ``roll(count=2)``.

    Returns ``RollResult`` with ``count=2``, ``results=(lower, higher)`` sorted
    ascending, ``total=lower``.
    """
    _validate_modifier(modifier)
    a = _rng.randint(1, 20)
    b = _rng.randint(1, 20)
    lower, higher = (a, b) if a <= b else (b, a)
    return RollResult(
        sides=20,
        count=2,
        results=(lower, higher),
        total=lower,
        modifier=modifier,
    )


# -- DC Resolution -----------------------------------------------------------


def check(
    dc: int,
    modifier: int = 0,
    advantage: bool = False,
    disadvantage: bool = False,
) -> CheckResult:
    """Roll a d20 against a difficulty class (DC).

    Args:
        dc: The difficulty class target.
        modifier: Flat modifier added to the effective roll for comparison.
        advantage: If ``True`` (and ``disadvantage`` is ``False``), roll with
                   advantage (2d20, take higher).
        disadvantage: If ``True`` (and ``advantage`` is ``False``), roll with
                      disadvantage (2d20, take lower).

    Returns:
        ``CheckResult`` with success/fail, the underlying ``RollResult``, the
        DC, and the margin.

    **Cancellation rule:** If both ``advantage=True`` and ``disadvantage=True``,
    they cancel → normal ``d20()`` roll (matching D&D 5e convention).

    **No crit semantics:** This function does NOT hard-code natural 20/1 as
    auto-success/auto-fail. The ruleset runtime (T1.4) interprets the raw
    ``RollResult`` based on the active ruleset configuration.
    """
    # Note: parameter names 'advantage'/'disadvantage' shadow the module-level
    # functions of the same name. We capture the function references up-front.
    _advantage = globals()["advantage"]
    _disadvantage = globals()["disadvantage"]

    if advantage and not disadvantage:
        roll_result = _advantage(modifier)
    elif disadvantage and not advantage:
        roll_result = _disadvantage(modifier)
    else:
        roll_result = d20(modifier)

    effective: int = roll_result.total + roll_result.modifier
    return CheckResult(
        success=effective >= dc,
        roll_result=roll_result,
        dc=dc,
        margin=effective - dc,
    )


# -- Exploding dice ---------------------------------------------------------


def explode(
    sides: int | Die,
    count: int = 1,
    modifier: int = 0,
    explode_on: int | None = None,
    max_explosions: int = 100,
) -> RollResult:
    """Roll exploding (acing) dice.

    When a die rolls its maximum value (or *explode_on*), an additional die
    is rolled and added to the total.  This repeats until no more dice
    explode or *max_explosions* extra dice have been rolled.

    Args:
        sides: Number of faces (``int``) or a :class:`Die` enum member.
        count: Number of dice to roll initially.
        modifier: Flat modifier stored on the result.
        explode_on: Value that triggers an explosion.  Defaults to *sides*
                    (the maximum possible roll).
        max_explosions: Hard cap on extra dice rolled (default 100).

    Returns:
        ``RollResult`` where ``results`` contains all rolled dice (original
        + exploded) and ``total`` is the sum of all results.
        ``count`` reflects the ORIGINAL count only (not the total rolled).

    >>> set_seed(42)
    >>> r = explode(6, 2)
    >>> r.sides == 6 and r.count == 2
    True
    >>> r.total == sum(r.results)
    True
    """
    _validate_modifier(modifier)
    actual_sides: int = _resolve_sides(sides)
    _validate_count(count)

    explode_val = explode_on if explode_on is not None else actual_sides
    if explode_val < 1 or explode_val > actual_sides:
        raise ValueError(
            f"explode_on must be between 1 and {actual_sides}, got {explode_val}"
        )
    if max_explosions < 0:
        raise ValueError("max_explosions must be non-negative")

    results: list[int] = []
    extra_count = 0

    # Roll initial dice
    for _ in range(count):
        results.append(_rng.randint(1, actual_sides))

    # Process explosions (including dies that explode from previous explosions)
    i = 0
    while i < len(results) and extra_count < max_explosions:
        if results[i] >= explode_val:
            extra_count += 1
            results.append(_rng.randint(1, actual_sides))
        i += 1

    return RollResult(
        sides=actual_sides,
        count=count,
        results=tuple(results),
        total=sum(results),
        modifier=modifier,
    )


# -- Dice pool with success counting ----------------------------------------


def count_successes(
    sides: int | Die,
    count: int,
    threshold: int,
    modifier: int = 0,
) -> PoolResult:
    """Roll a dice pool and count successes.

    Each die >= *threshold* is a success.  Each die == 1 is a botch.
    *modifier* is added to the success count after evaluation.

    Args:
        sides: Number of faces on each die.
        count: Number of dice to roll.
        threshold: Minimum value for a success.
        modifier: Flat bonus successes.

    Returns:
        ``PoolResult`` with individual results, success/failure/botch counts.

    >>> set_seed(42)
    >>> pr = count_successes(10, 5, threshold=6)
    >>> pr.sides == 10 and pr.count == 5
    True
    >>> pr.successes + pr.failures == 5
    True
    """
    _validate_modifier(modifier)
    actual_sides: int = _resolve_sides(sides)
    _validate_count(count)

    if threshold < 1:
        raise ValueError("threshold must be at least 1")
    if threshold > actual_sides + 1:
        raise ValueError(
            f"threshold ({threshold}) exceeds die maximum ({actual_sides})"
        )

    results: list[int] = [_rng.randint(1, actual_sides) for _ in range(count)]
    results_tuple = tuple(results)

    successes = sum(1 for r in results if r >= threshold) + modifier
    failures = sum(1 for r in results if r < threshold)
    botches = sum(1 for r in results if r == 1)

    return PoolResult(
        sides=actual_sides,
        count=count,
        results=results_tuple,
        threshold=threshold,
        successes=max(0, successes),
        failures=failures,
        botches=botches,
        modifier=modifier,
    )


# -- Reroll / Push -----------------------------------------------------------


def reroll(
    previous_result: RollResult,
    reroll_predicate: str = "all",
    modifier: int = 0,
    threshold: int | None = None,
) -> RollResult:
    """Reroll some or all dice from a previous ``RollResult``.

    Args:
        previous_result: The original roll to reroll from.
        reroll_predicate: Which dice to reroll:

            - ``"all"`` — reroll every die.
            - ``"ones"`` — reroll dice that rolled 1.
            - ``"failures"`` — reroll dice below *threshold* (requires
              *threshold* to be set).

        modifier: Flat modifier stored on the new result.
        threshold: Used when *reroll_predicate* is ``"failures"``.

    Returns:
        A NEW ``RollResult``.  The original is unchanged (frozen dataclass).

    >>> set_seed(42)
    >>> original = RollResult(sides=20, count=2, results=(1, 17), total=18, modifier=0)
    >>> r = reroll(original, "ones")
    >>> r.count == 2
    True
    """
    _validate_modifier(modifier)
    sides = previous_result.sides

    if reroll_predicate == "all":
        keep_mask = [False] * len(previous_result.results)
    elif reroll_predicate == "ones":
        keep_mask = [r != 1 for r in previous_result.results]
    elif reroll_predicate == "failures":
        if threshold is None:
            raise ValueError("threshold is required when reroll_predicate='failures'")
        keep_mask = [r >= threshold for r in previous_result.results]
    else:
        raise ValueError(
            f"Unknown reroll_predicate '{reroll_predicate}'. "
            "Use 'all', 'ones', or 'failures'."
        )

    new_results: list[int] = []
    for i, keep in enumerate(keep_mask):
        if keep:
            new_results.append(previous_result.results[i])
        else:
            new_results.append(_rng.randint(1, sides))

    new_tuple = tuple(new_results)
    return RollResult(
        sides=sides,
        count=len(new_tuple),
        results=new_tuple,
        total=sum(new_tuple),
        modifier=modifier,
    )


# -- Weighted Random Pick ---------------------------------------------------


def weighted_pick(
    items: list[dict],
    weight_field: str = "weight",
    roll_result: RollResult | None = None,
) -> dict | None:
    """Pick an item from a weighted list using the dice RNG.

    If *roll_result* is provided, its ``total`` is used as the lookup key
    against cumulative weights (e.g., a d100 roll).  Otherwise the selection
    is purely random, weighted by the values of *weight_field*.

    Args:
        items: List of dicts, each with at least a *weight_field* key.
        weight_field: Dict key holding the numeric weight (default ``"weight"``).
        roll_result: Optional pre-computed RollResult.  Its ``total`` is
                     treated as the roll for weighted lookup.

    Returns:
        The selected dict, or ``None`` if *items* is empty.

    >>> set_seed(42)
    >>> items = [{"name": "common", "weight": 70}, {"name": "rare", "weight": 30}]
    >>> result = weighted_pick(items)
    >>> result is not None
    True
    >>> "name" in result
    True

    **d100 table lookup (roll specified):**

    >>> rr = RollResult(sides=100, count=1, results=(45,), total=45, modifier=0)
    >>> items = [{"name": "a", "weight": 50}, {"name": "b", "weight": 50}]
    >>> weighted_pick(items, roll_result=rr)
    {'name': 'a', 'weight': 50}
    """
    if not items:
        return None

    total_weight = sum(item.get(weight_field, 0) for item in items)
    if total_weight == 0:
        # All-zero weights — pick uniformly
        return items[_rng.randint(0, len(items) - 1)]

    if roll_result is not None:
        # Use the provided roll's total to select
        roll_val = roll_result.total
    else:
        # Random selection proportional to weights
        roll_val = _rng.randint(1, total_weight)

    cumulative = 0
    for item in items:
        cumulative += item.get(weight_field, 0)
        if roll_val <= cumulative:
            return item

    # Fallback — should not reach here, but return last item
    return items[-1] if items else None


# -- Hunger Dice (Vampire: The Masquerade style) ----------------------------


def hunger_roll(
    pool_size: int,
    hunger: int,
    sides: int | Die = 10,
    threshold: int = 6,
    modifier: int = 0,
) -> HungerPoolResult:
    """Roll a dice pool with hunger dice (VtM / V5 style).

    Hunger dice are REPLACEMENT dice — the total pool is *pool_size*, of
    which *hunger* are hunger dice and the remaining
    *pool_size - hunger* are regular dice.

    Args:
        pool_size: Total dice in the pool (regular + hunger).
        hunger: Number of hunger dice (must be <= *pool_size*).
        sides: Die type (default d10 for VtM).
        threshold: Minimum value for a success (default 6 for V5).
        modifier: Flat bonus successes.

    Returns:
        ``HungerPoolResult`` with separated regular/hunger results, success
        counts, bestial failures, and messy criticals.

    **Bestial failure:** A hunger die rolls a 1 AND results in a failure.

    **Messy critical:** A hunger die rolls a 10 AND another die (regular or
    hunger) also rolls a 10 — i.e., a critical success "stained" by hunger.

    >>> set_seed(42)
    >>> hpr = hunger_roll(pool_size=6, hunger=2, sides=10, threshold=6)
    >>> hpr.threshold == 6
    True
    >>> len(hpr.regular_results) + len(hpr.hunger_results) == 6
    True
    """
    _validate_modifier(modifier)
    actual_sides: int = _resolve_sides(sides)

    if pool_size < 1:
        raise ValueError("pool_size must be at least 1")
    if hunger < 0:
        raise ValueError("hunger must be non-negative")
    if hunger > pool_size:
        raise ValueError(
            f"hunger ({hunger}) cannot exceed pool_size ({pool_size})"
        )
    if threshold < 1:
        raise ValueError("threshold must be at least 1")

    regular_count = pool_size - hunger

    regular_results: list[int] = [
        _rng.randint(1, actual_sides) for _ in range(regular_count)
    ]
    hunger_results: list[int] = [
        _rng.randint(1, actual_sides) for _ in range(hunger)
    ]

    all_results = regular_results + hunger_results

    successes = sum(1 for r in all_results if r >= threshold) + modifier
    failures = sum(1 for r in all_results if r < threshold)

    # Bestial failures: hunger die rolled 1
    bestial_failures = sum(1 for r in hunger_results if r == 1)

    # Messy criticals: at least one hunger die is a 10 AND
    # at least one other die (of any kind) is also a 10
    has_hunger_10 = any(r == 10 for r in hunger_results)
    other_tens = sum(1 for r in regular_results if r == 10)
    # Also count hunger dice that are 10 beyond the first one
    hunger_tens_count = sum(1 for r in hunger_results if r == 10)
    total_tens = other_tens + hunger_tens_count
    messy_criticals = 1 if (has_hunger_10 and total_tens >= 2) else 0

    return HungerPoolResult(
        regular_results=tuple(regular_results),
        hunger_results=tuple(hunger_results),
        successes=max(0, successes),
        failures=failures,
        bestial_failures=bestial_failures,
        messy_criticals=messy_criticals,
        threshold=threshold,
        modifier=modifier,
    )
