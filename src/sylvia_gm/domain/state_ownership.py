"""Pure package-lock rules for mutable state namespace ownership."""

from __future__ import annotations

from collections.abc import Iterable


def owner_matches_package_lock(
    package_ids: Iterable[str], namespace: str, owner_package_id: str | None
) -> bool:
    """Return whether one recorded owner is compatible with an exact package lock."""

    selected = tuple(package_ids)
    if not selected:
        return owner_package_id is None
    if owner_package_id is None or owner_package_id not in selected:
        return False
    prefix_matches = tuple(
        package_id for package_id in selected if namespace.startswith(package_id + ".")
    )
    return len(prefix_matches) <= 1 and (
        not prefix_matches or prefix_matches[0] == owner_package_id
    )
