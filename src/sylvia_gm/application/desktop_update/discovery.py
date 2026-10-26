"""Select one eligible official release; only its signed manifest supplies update authority."""

from __future__ import annotations

import threading
from collections.abc import Callable

from .manifest import (
    OFFICIAL_RELEASES,
    Manifest,
    UpdateError,
    Version,
    bounded_json,
    parse_current_recovery_manifest,
    parse_manifest,
    release_url,
)
from .transport import Transport, metadata_bytes


def retain_current_manifest(
    transport: Transport,
    cancel: threading.Event,
    *,
    current: Version,
    target: str,
    distribution: str,
    verify: Callable[[str, bytes, bytes], None],
) -> Manifest:
    """The compiled current version selects this fixed official recovery manifest, not IPC."""
    if distribution != "installed":
        raise UpdateError("wrong_distribution", "Only NSIS recovery needs a previous installer.")
    name = f"sylvia-gm-{current.text}-{target}-installed-update.json"
    url = release_url(
        f"https://github.com/pneuma-io/sylvia-gm/releases/download/v{current.text}/{name}",
        current.text,
    )
    return parse_current_recovery_manifest(
        metadata_bytes(transport, url, cancel),
        current=current,
        target=target,
        distribution=distribution,
        verify=verify,
    )


def discover(
    transport: Transport,
    cancel: threading.Event,
    *,
    current: Version,
    target: str,
    distribution: str,
    verify: Callable[[str, bytes, bytes], None],
) -> Manifest | None:
    releases = bounded_json(metadata_bytes(transport, OFFICIAL_RELEASES, cancel))
    if not isinstance(releases, list) or len(releases) > 30:
        raise UpdateError("invalid_metadata", "The official release index is invalid.")
    candidates: list[tuple[Version, dict[str, object]]] = []
    for release in releases:
        if not isinstance(release, dict):
            raise UpdateError("invalid_metadata", "The official release index is invalid.")
        if type(release.get("draft")) is not bool or type(release.get("prerelease")) is not bool:
            raise UpdateError("invalid_metadata", "The official release index is incomplete.")
        if release["draft"]:
            continue
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not tag.startswith("v"):
            continue
        try:
            version = Version.parse(tag[1:])
        except UpdateError:
            continue
        if version <= current or version.channel != current.channel:
            continue
        if bool(version.prerelease) != release["prerelease"]:
            raise UpdateError("invalid_metadata", "The official release channel is inconsistent.")
        candidates.append((version, release))
    if not candidates:
        return None
    # A broken newest release is an error, never a reason to select a silently older payload.
    version, release = max(candidates, key=lambda item: item[0])
    if sum(candidate == version for candidate, _ in candidates) != 1:
        raise UpdateError("invalid_metadata", "The official release version is ambiguous.")
    name = f"sylvia-gm-{version.text}-{target}-{distribution}-update.json"
    assets = release.get("assets")
    if not isinstance(assets, list) or len(assets) > 128:
        raise UpdateError("invalid_metadata", "The official release asset list is incomplete.")
    matches = [row for row in assets if isinstance(row, dict) and row.get("name") == name]
    if len(matches) != 1:
        raise UpdateError(
            "missing_manifest", "This release has no compatible signed update manifest."
        )
    url = release_url(matches[0].get("browser_download_url"), version.text)
    manifest = parse_manifest(
        metadata_bytes(transport, url, cancel),
        current=current,
        target=target,
        distribution=distribution,
        verify=verify,
    )
    if manifest.version.text != version.text:
        raise UpdateError("wrong_version", "The signed update does not match the selected release.")
    return manifest
