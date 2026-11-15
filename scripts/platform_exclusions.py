"""Platform-specific cases: one marker, and exact-ID exclusions for the local gate.

A test that can only run on some platforms carries ``@pytest.mark.runs_on(...)``. On any
other platform a direct pytest run shows a skip with a fixed reason, and the journey runner
excludes the case by exact ID instead of requiring it. Ordinary skips still fail the gate.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SUPPORTED_PLATFORMS = ("win32", "linux")
EXCLUSIONS_FILE_VARIABLE = "SYLVIA_PLATFORM_EXCLUSIONS_FILE"


def platform_reason(platforms, reason):
    """The single spelling of a platform exclusion, shared by pytest skips and the receipt."""
    return f"Runs only on {', '.join(platforms)}: {reason}"


def runs_on_requirement(item):
    """Return the item's (platforms, reason), or None; a malformed marker is a usage error."""
    import pytest

    markers = list(item.iter_markers("runs_on"))
    if not markers:
        return None
    if len(markers) > 1:
        raise pytest.UsageError(f"{item.nodeid}: use exactly one runs_on marker per case")
    marker = markers[0]
    platforms = marker.args
    reason = marker.kwargs.get("reason")
    unknown = sorted(set(marker.kwargs) - {"reason"})
    if not platforms:
        raise pytest.UsageError(f"{item.nodeid}: runs_on needs at least one platform")
    if unknown:
        raise pytest.UsageError(f"{item.nodeid}: runs_on accepts only reason=, not {unknown}")
    for platform in platforms:
        if platform not in SUPPORTED_PLATFORMS:
            raise pytest.UsageError(
                f"{item.nodeid}: runs_on platform {platform!r} is not one of "
                f"{', '.join(SUPPORTED_PLATFORMS)}"
            )
    if len(set(platforms)) != len(platforms):
        raise pytest.UsageError(f"{item.nodeid}: runs_on names a platform twice")
    if not isinstance(reason, str) or not reason.strip():
        raise pytest.UsageError(f"{item.nodeid}: runs_on needs a non-empty reason=")
    return tuple(platforms), reason


def apply_runs_on(items):
    """Skip cases for other platforms and, for the runner, record them by exact ID."""
    import pytest

    excluded = []
    for item in items:
        requirement = runs_on_requirement(item)
        if requirement is None:
            continue
        platforms, reason = requirement
        if sys.platform not in platforms:
            item.add_marker(pytest.mark.skip(reason=platform_reason(platforms, reason)))
            excluded.append({"nodeid": item.nodeid, "platforms": list(platforms), "reason": reason})
    destination = os.environ.get(EXCLUSIONS_FILE_VARIABLE)
    if destination:
        Path(destination).write_text(json.dumps(excluded, indent=2), encoding="utf-8")
    return excluded


def read_platform_exclusions(path):
    """Read the collection's exclusions; a missing or malformed file is a failed collection."""
    try:
        entries = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"Platform exclusions were not recorded by collection: {error}") from error
    if not isinstance(entries, list):
        raise ValueError("Platform exclusions must be a list")
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"nodeid", "platforms", "reason"}
            or not isinstance(entry["nodeid"], str)
            or not isinstance(entry["reason"], str)
            or not entry["reason"].strip()
            or not isinstance(entry["platforms"], list)
            or not entry["platforms"]
            or not set(entry["platforms"]) <= set(SUPPORTED_PLATFORMS)
        ):
            raise ValueError(f"Malformed platform exclusion: {entry!r}")
    if len({entry["nodeid"] for entry in entries}) != len(entries):
        raise ValueError("Platform exclusions list a case twice")
    return entries


def pytest_collection_modifyitems(items):
    """Lets a synthetic checkout load this module with ``-p scripts.platform_exclusions``."""
    apply_runs_on(items)
