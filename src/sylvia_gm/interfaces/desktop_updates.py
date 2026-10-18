"""Manual release discovery and browser handoff; never install or touch saved data."""

from __future__ import annotations

import json
import re
import webbrowser
from dataclasses import dataclass
from functools import total_ordering
from typing import Any
from urllib.parse import quote

import httpx

REPOSITORY_URL = "https://github.com/pneuma-io/sylvia-gm"
RELEASES_API = "https://api.github.com/repos/pneuma-io/sylvia-gm/releases?per_page=100"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_NOTES_CHARS = 32_000
_VERSION = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
)


class UpdateCheckError(ValueError):
    """A release cannot be safely presented; the user may explicitly retry."""


@total_ordering
@dataclass(frozen=True)
class Version:
    core: tuple[int, int, int]
    prerelease: tuple[str, ...]

    @classmethod
    def parse(cls, value: object) -> Version:
        """Parse SemVer precedence without treating alpha.10 as older than alpha.2."""
        match = _VERSION.fullmatch(value) if isinstance(value, str) and len(value) <= 128 else None
        if match is None:
            raise UpdateCheckError("The release version is invalid. Try checking again later.")
        prerelease = tuple(match[4].split(".")) if match[4] else ()
        if any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in prerelease):
            raise UpdateCheckError("The release version is invalid. Try checking again later.")
        return cls((int(match[1]), int(match[2]), int(match[3])), prerelease)

    def __lt__(self, other: Version) -> bool:
        if self.core != other.core:
            return self.core < other.core
        if not self.prerelease or not other.prerelease:
            return bool(self.prerelease) and not other.prerelease
        for left, right in zip(self.prerelease, other.prerelease, strict=False):
            if left == right:
                continue
            if left.isdigit() and right.isdigit():
                return int(left) < int(right)
            if left.isdigit() != right.isdigit():
                return left.isdigit()
            return left < right
        return len(self.prerelease) < len(other.prerelease)


def _same_channel(current: Version, candidate: Version) -> bool:
    """Stable builds see stable releases; prereleases also see their named channel."""
    return not candidate.prerelease or bool(
        current.prerelease and current.prerelease[0] == candidate.prerelease[0]
    )


def _release_url(tag: str) -> str:
    return f"{REPOSITORY_URL}/releases/tag/{quote(tag, safe='')}"


def _release_projection(release: dict[str, Any], tag: str, version: str) -> dict[str, object]:
    """Accept only the official Windows distribution; never use release-supplied links."""
    url = _release_url(tag)
    if release.get("html_url") != url:
        raise UpdateCheckError("The release link is not the official Sylvia GM release page.")
    assets = release.get("assets")
    notes = release.get("body")
    if notes is None:
        notes = ""
    if not isinstance(assets, list) or not isinstance(notes, str):
        raise UpdateCheckError("Release information is incomplete. Try checking again later.")
    expected = {
        f"sylvia-gm-{version}-windows-x86_64-setup.exe": "Installer",
        f"sylvia-gm-{version}-windows-x86_64-portable.zip": "Portable ZIP",
        "SHA256SUMS.txt": "Checksums",
    }
    packages = []
    seen = set()
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
            continue
        if asset["name"] not in expected:
            continue
        name = asset["name"]
        download_url = f"{REPOSITORY_URL}/releases/download/{quote(tag, safe='')}/{name}"
        size = asset.get("size")
        if (
            name in seen
            or asset.get("browser_download_url") != download_url
            or type(size) is not int
            or size <= 0
            or asset.get("state") != "uploaded"
        ):
            raise UpdateCheckError("The release download information is invalid. Try again later.")
        seen.add(name)
        packages.append(expected[name])
    if "Checksums" not in packages or not any(p != "Checksums" for p in packages):
        raise UpdateCheckError("This release has no complete Windows download and checksum set.")
    return {
        "version": version,
        "tag": tag,
        "url": url,
        "notes": notes[:MAX_NOTES_CHARS],
        "notes_truncated": len(notes) > MAX_NOTES_CHARS,
        "packages": packages,
    }


def check_for_update(
    current_version: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, object]:
    """Read recent public releases only after a user request, without account credentials."""
    current = Version.parse(current_version)
    try:
        with (
            httpx.Client(
                timeout=10,
                follow_redirects=False,
                trust_env=False,
                transport=transport,
                headers={"Accept": "application/vnd.github+json", "User-Agent": "Sylvia-GM"},
            ) as client,
            client.stream("GET", RELEASES_API) as response,
        ):
            if response.status_code in {403, 429}:
                raise UpdateCheckError("GitHub is limiting release checks. Try again later.")
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_bytes(chunk_size=65_536):
                content.extend(chunk)
                if len(content) > MAX_RESPONSE_BYTES:
                    raise UpdateCheckError("Release information is too large. Try again later.")
        releases = json.loads(content)
    except (httpx.HTTPError, OSError) as error:
        raise UpdateCheckError(
            "Could not reach GitHub releases. Check your connection and retry."
        ) from error
    except (ValueError, UnicodeError) as error:
        if isinstance(error, UpdateCheckError):
            raise
        raise UpdateCheckError(
            "GitHub returned unreadable release information. Try again later."
        ) from error
    if not isinstance(releases, list) or any(not isinstance(item, dict) for item in releases):
        raise UpdateCheckError("GitHub returned invalid release information. Try again later.")
    candidates = []
    for release in releases:
        if type(release.get("draft")) is not bool:
            raise UpdateCheckError(
                "GitHub returned incomplete release information. Try again later."
            )
        if release["draft"]:
            continue
        tag = release.get("tag_name")
        if not isinstance(tag, str) or type(release.get("prerelease")) is not bool:
            raise UpdateCheckError(
                "GitHub returned incomplete release information. Try again later."
            )
        version = tag.removeprefix("v")
        try:
            parsed = Version.parse(version)
        except UpdateCheckError:
            continue
        if parsed <= current or not _same_channel(current, parsed):
            continue
        if release.get("prerelease") is not bool(parsed.prerelease):
            raise UpdateCheckError(
                "The release channel information is inconsistent. Try again later."
            )
        candidates.append((parsed, tag, version, release))
    newest = max(candidates, key=lambda item: item[0]) if candidates else None
    return {
        "current_version": current_version,
        "channel": f"{current.prerelease[0]} and stable" if current.prerelease else "stable",
        "release": _release_projection(newest[3], newest[1], newest[2]) if newest else None,
    }


def open_update_release(current_version: str, tag: str) -> dict[str, object]:
    """Open a fixed official release page; the renderer cannot supply an arbitrary URL."""
    current, candidate = Version.parse(current_version), Version.parse(tag.removeprefix("v"))
    if candidate <= current or not _same_channel(current, candidate):
        raise UpdateCheckError(
            "Check again and choose a newer release on this installation's channel."
        )
    url = _release_url(tag)
    try:
        opened = webbrowser.open(url, new=2)
    except (OSError, webbrowser.Error):
        opened = False
    return {"opened": bool(opened), "url": url}
