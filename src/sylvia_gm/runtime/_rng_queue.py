"""RNG Queue — pre-seeded deterministic dice queue for simulation mode.

Generates N pre-rolled dice entries before a scene. The LLM consumes entries
sequentially from the queue. The queue wraps on exhaustion (index wraps to 0).

The queue is stored in ``ctx.extras["rng_queue"]`` and regenerated on new
scene or combat start.  It can be seeded for fully deterministic replay.

Usage::

    from sylvia_gm.runtime._rng_queue import RNGQueue, RNGQueueEntry

    # Create a queue with 12 entries, seeded for determinism
    queue = RNGQueue(size=12, seed=42)

    # LLM consumes entries one at a time
    entry = queue.consume()  # → RNGQueueEntry(d20=..., d4=..., ...)

    # Inject into LLM context
    block = queue.to_context_block()

    # Regenerate on new scene
    queue.regenerate(seed=99)

    # Store in context
    ctx.extras["rng_queue"] = queue

API Summary
-----------

>>> from sylvia_gm.runtime import RNGQueue, RNGQueueEntry
"""

from __future__ import annotations

import dataclasses
import random
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_QUEUE_SIZE: int = 12

# Die types in canonical display order
_DIE_TYPES: tuple[tuple[str, int], ...] = (
    ("d20", 20),
    ("d4", 4),
    ("d6", 6),
    ("d8", 8),
    ("d10", 10),
    ("d12", 12),
    ("d100", 100),
)


# ---------------------------------------------------------------------------
# RNGQueueEntry
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class RNGQueueEntry:
    """A single pre-rolled dice entry with all seven standard dice values.

    Each entry is immutable.  The LLM consumes the entry for the dice values
    it needs and discards the rest for that roll.

    >>> e = RNGQueueEntry(d20=15, d4=3, d6=5, d8=2, d10=7, d12=11, d100=42)
    >>> e.d20
    15
    >>> e.to_dict()["d20"]
    15
    """

    d20: int
    d4: int
    d6: int
    d8: int
    d10: int
    d12: int
    d100: int

    def to_dict(self) -> dict[str, int]:
        """Serialize to a JSON-compatible dictionary.

        >>> e = RNGQueueEntry(15, 3, 5, 2, 7, 11, 42)
        >>> e.to_dict()
        {'d20': 15, 'd4': 3, 'd6': 5, 'd8': 2, 'd10': 7, 'd12': 11, 'd100': 42}
        """
        return dataclasses.asdict(self)

    @classmethod
    def field_names(cls) -> tuple[str, ...]:
        """Return the canonical die field names in display order.

        >>> RNGQueueEntry.field_names()
        ('d20', 'd4', 'd6', 'd8', 'd10', 'd12', 'd100')
        """
        return tuple(f.name for f in dataclasses.fields(cls))


# ---------------------------------------------------------------------------
# RNGQueue
# ---------------------------------------------------------------------------


class RNGQueue:
    """Pre-seeded deterministic dice queue for simulation mode.

    Generates ``size`` entries before a scene, each containing pre-rolled
    values for every standard die type (d4, d6, d8, d10, d12, d20, d100).
    The LLM consumes entries sequentially.  On exhaustion the index wraps
    back to 0.

    The queue is regenerated on new scene / combat start (via
    ``regenerate()``).  A seed can be provided for fully deterministic
    replay.

    **Edge cases handled:**

    - ``seed=0`` is a valid seed (matching Python's ``random`` convention).
    - Size 1 is valid — the single entry is always consumed.
    - Index wraps cleanly on exhaustion.
    - Serialization round-trips maintain exact state.

    >>> queue = RNGQueue(size=12, seed=42)
    >>> entry = queue.consume()
    >>> isinstance(entry, RNGQueueEntry)
    True
    >>> 1 <= entry.d20 <= 20
    True
    >>> block = queue.to_context_block()
    >>> "[RNG_QUEUE]" in block
    True
    """

    DEFAULT_SIZE: int = DEFAULT_QUEUE_SIZE

    def __init__(
        self,
        size: int = DEFAULT_SIZE,
        seed: int | None = None,
    ) -> None:
        """Create a new RNG queue.

        Args:
            size: Number of pre-rolled entries (default 12). Must be >= 1.
            seed: Optional seed for deterministic generation.
                  ``None`` uses system entropy (non-deterministic).

        Raises:
            ValueError: If ``size < 1``.
        """
        if size < 1:
            raise ValueError(f"Queue size must be at least 1, got {size}")

        self.size: int = size
        self.seed: int | None = seed
        self.entries: list[RNGQueueEntry] = []
        self._index: int = 0
        self._generate()

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def index(self) -> int:
        """Current consumption index (position of next entry to consume)."""
        return self._index

    @property
    def remaining(self) -> int:
        """Number of unconsumed entries before the next wrap.

        >>> q = RNGQueue(size=5, seed=1)
        >>> q.remaining
        5
        >>> _ = q.consume()
        >>> q.remaining
        4
        """
        return self.size - self._index

    # ── Core operations ────────────────────────────────────────────────────

    def consume(self) -> RNGQueueEntry:
        """Consume the next entry and advance the index.

        The index wraps back to 0 on exhaustion — the queue is circular.

        Returns:
            The next ``RNGQueueEntry`` in sequence.

        >>> q = RNGQueue(size=3, seed=42)
        >>> e0 = q.consume()
        >>> e1 = q.consume()
        >>> e2 = q.consume()
        >>> # Wraps — should get e0 again
        >>> e0_again = q.consume()
        >>> e0 == e0_again
        True
        """
        entry = self.entries[self._index]
        self._index = (self._index + 1) % self.size
        return entry

    def peek(self) -> RNGQueueEntry:
        """Return the next entry without consuming it.

        >>> q = RNGQueue(size=2, seed=42)
        >>> before = q.index
        >>> _ = q.peek()
        >>> q.index == before
        True
        """
        return self.entries[self._index]

    def reset_index(self) -> None:
        """Reset the consumption index to 0 without regenerating entries.

        >>> q = RNGQueue(size=3, seed=42)
        >>> _ = q.consume()
        >>> _ = q.consume()
        >>> q.index
        2
        >>> q.reset_index()
        >>> q.index
        0
        """
        self._index = 0

    def regenerate(self, seed: int | None = None) -> None:
        """Regenerate all entries (e.g., on new scene or combat start).

        If *seed* is provided, it replaces the current seed.  If *seed* is
        ``None`` and the queue was created with a seed, the original seed
        is reused (deterministic scene transitions).  If the queue had no
        seed, new entries use system entropy.

        The consumption index is reset to 0.

        Args:
            seed: Optional new seed.  Pass ``None`` to keep the current seed.

        >>> q = RNGQueue(size=3, seed=42)
        >>> first = q.consume()
        >>> q.regenerate()       # Reuses seed=42
        >>> first_again = q.consume()
        >>> first == first_again
        True
        """
        if seed is not None:
            self.seed = seed
        self._generate()

    # ── Context injection ──────────────────────────────────────────────────

    def to_context_block(self) -> str:
        """Format the queue as an ``[RNG_QUEUE]`` block for LLM context injection.

        The block shows all pre-rolled entries with the current consumption
        index position marked by ``>``.  The LLM reads entries sequentially
        and uses the dice values for its rolls.

        Returns:
            A multi-line string formatted as ``[RNG_QUEUE] ... [/RNG_QUEUE]``.

        >>> q = RNGQueue(size=2, seed=42)
        >>> block = q.to_context_block()
        >>> "[RNG_QUEUE]" in block
        True
        >>> "[/RNG_QUEUE]" in block
        True
        >>> "seed=42" in block
        True
        """
        lines: list[str] = ["[RNG_QUEUE]"]
        lines.append(f"seed={self.seed if self.seed is not None else 'random'}")
        lines.append(f"size={self.size}")
        lines.append(f"index={self._index}")
        lines.append("---")

        for i, entry in enumerate(self.entries):
            marker = ">" if i == self._index else " "
            lines.append(
                f"{marker}[{i}] "
                f"d20={entry.d20} d4={entry.d4} d6={entry.d6} "
                f"d8={entry.d8} d10={entry.d10} d12={entry.d12} "
                f"d100={entry.d100}"
            )

        lines.append("[/RNG_QUEUE]")
        return "\n".join(lines)

    # ── Serialization ──────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary.

        >>> q = RNGQueue(size=2, seed=42)
        >>> d = q.to_dict()
        >>> d["size"]
        2
        >>> d["seed"]
        42
        >>> len(d["entries"])
        2
        """
        return {
            "seed": self.seed,
            "size": self.size,
            "index": self._index,
            "entries": [e.to_dict() for e in self.entries],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RNGQueue:
        """Deserialize from a dictionary produced by ``to_dict()``.

        The deserialized queue has its entries and index position restored
        exactly.  No regeneration occurs.

        >>> q1 = RNGQueue(size=3, seed=42)
        >>> _ = q1.consume()
        >>> d = q1.to_dict()
        >>> q2 = RNGQueue.from_dict(d)
        >>> q2.index == q1.index
        True
        >>> q2.entries == q1.entries
        True
        """
        queue = cls.__new__(cls)
        queue.size = data["size"]
        queue.seed = data.get("seed")
        queue._index = data["index"]
        queue.entries = [RNGQueueEntry(**e) for e in data["entries"]]
        return queue

    # ── Internal ──────────────────────────────────────────────────────────

    def _generate(self) -> None:
        """Pre-generate all entries using a private seeded RNG.

        Uses a private ``random.Random`` instance — does NOT affect the
        module-level dice RNG.  Each entry rolls every standard die type
        once in canonical order.
        """
        rng: random.Random
        if self.seed is not None:
            rng = random.Random(self.seed)
        else:
            rng = random.Random()

        self.entries.clear()
        for _ in range(self.size):
            self.entries.append(
                RNGQueueEntry(
                    d20=rng.randint(1, 20),
                    d4=rng.randint(1, 4),
                    d6=rng.randint(1, 6),
                    d8=rng.randint(1, 8),
                    d10=rng.randint(1, 10),
                    d12=rng.randint(1, 12),
                    d100=rng.randint(1, 100),
                )
            )
        self._index = 0

    # ── Dunder ────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"RNGQueue(size={self.size}, seed={self.seed!r}, "
            f"index={self._index})"
        )

    def __len__(self) -> int:
        return self.size

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RNGQueue):
            return NotImplemented
        return (
            self.size == other.size
            and self.seed == other.seed
            and self._index == other._index
            and self.entries == other.entries
        )

    def __hash__(self) -> int:
        return hash((self.size, self.seed, self._index, tuple(self.entries)))
