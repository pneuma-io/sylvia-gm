"""Bounded value operations for package-declared cards, pieces and ordered collections."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from copy import deepcopy
from typing import Any, cast

LIMIT = 1024


def collection(value: object) -> list[Any]:
    """Copy one bounded JSON sequence without treating text as a collection."""
    if not isinstance(value, (list, tuple)) or len(value) > LIMIT:
        raise ValueError("A bounded sequence is required")
    return list(value)


def indices(stop: int) -> list[int]:
    if type(stop) is not int or not 0 <= stop <= LIMIT:
        raise ValueError("Sequence length is outside its bound")
    return list(range(stop))


def portion(value: object, start: int, stop: int) -> list[Any]:
    if type(start) is not int or type(stop) is not int:
        raise ValueError("Sequence offsets must be integers")
    return deepcopy(collection(value)[start:stop])


def joined(first: object, second: object) -> list[Any]:
    values = collection(first) + collection(second)
    if len(values) > LIMIT:
        raise ValueError("Combined sequence exceeds its bound")
    return deepcopy(values)


def replaced(value: object, index: int, item: object) -> list[Any]:
    values = collection(value)
    if type(index) is not int or not 0 <= index < len(values):
        raise ValueError("Sequence index is outside its bound")
    values[index] = item
    return deepcopy(values)


def without(value: object, removed: object) -> list[Any]:
    values = collection(value)
    for item in collection(removed):
        if item not in values:
            raise ValueError("Removed item is not present")
        values.remove(item)
    return deepcopy(values)


def occurrences(value: object, item: object) -> int:
    return collection(value).count(item)


def text(value: object) -> str:
    if not isinstance(value, (str, int, float, bool)) or len(str(value)) > 256:
        raise ValueError("A bounded scalar is required")
    return str(value)


def total(value: object) -> int | float:
    values = collection(value)
    if any(type(item) not in (int, float) for item in values):
        raise ValueError("Numeric sequence required")
    return sum(cast(list[int | float], values))


def one(value: object) -> list[Any]:
    return [deepcopy(value)]


def ordered(value: object) -> list[Any]:
    return sorted(collection(value))


def partitions(
    items: object,
    patterns: object,
    sizes: object,
    *,
    consume: Callable[[], None] | None = None,
) -> list[list[int]]:
    """Enumerate bounded exact covers of a multiset by declared pattern identities."""
    values, rows, required = collection(items), collection(patterns), collection(sizes)
    if len(values) > 32 or len(rows) > 128 or len(required) > 8:
        raise ValueError("Pattern partition exceeds its bound")
    if any(type(item) not in (int, str) for item in values):
        raise ValueError("Pattern items must be scalar identities")
    if any(type(size) is not int or not 1 <= size <= 8 for size in required):
        raise ValueError("Pattern sizes must be positive bounded integers")
    candidates = []
    for index, row in enumerate(rows):
        pattern = collection(row)
        if not pattern or any(type(item) not in (int, str) for item in pattern):
            raise ValueError("Invalid pattern")
        candidates.append((index, Counter(pattern)))
    found: list[list[int]] = []
    work = 0

    def visit(remaining: Counter[Any], pending: list[int], chosen: list[int]) -> None:
        nonlocal work
        work += 1
        if consume is not None:
            consume()
        if work > 16384 or len(found) > 256:
            raise ValueError("Pattern search exceeds its bound")
        if not pending:
            if not +remaining:
                found.append(sorted(chosen))
            return
        if sum(remaining.values()) != sum(pending):
            return
        size = pending[0]
        first = next(iter(+remaining), None) if len(set(pending)) == 1 else None
        for index, pattern in candidates:
            if sum(pattern.values()) != size or any(remaining[k] < n for k, n in pattern.items()):
                continue
            if first is not None and first not in pattern:
                continue
            visit(remaining - pattern, pending[1:], [*chosen, index])

    visit(Counter(values), required, [])
    return [list(row) for row in dict.fromkeys(tuple(row) for row in found)]


def completions(
    items: object,
    patterns: object,
    sizes: object,
    universe: object,
    *,
    consume: Callable[[], None] | None = None,
) -> list[Any]:
    values, candidates = collection(items), collection(universe)
    if len(candidates) > 128:
        raise ValueError("Completion universe exceeds its bound")
    return [
        item for item in candidates if partitions([*values, item], patterns, sizes, consume=consume)
    ]


FUNCTIONS = {
    "indices": indices,
    "portion": portion,
    "joined": joined,
    "replaced": replaced,
    "without": without,
    "occurrences": occurrences,
    "text": text,
    "total": total,
    "partitions": partitions,
    "one": one,
    "ordered": ordered,
    "completions": completions,
}
