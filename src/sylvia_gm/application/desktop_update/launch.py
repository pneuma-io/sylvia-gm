"""Immutable installation context supplied once on the native parent's private owner pipe."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sylvia_gm import __version__

from .manifest import UpdateError
from .storage import directory_identity


@dataclass(frozen=True)
class Installation:
    live: Path
    data: Path
    distribution: str
    target: str
    app_identity: tuple[int, int]
    data_identity: tuple[int, int]

    @classmethod
    def from_launch(cls, value: object, data: Path) -> Installation | None:
        return cls._from_launch(value, data, expected_version=__version__)

    @classmethod
    def for_recovery(cls, value: object, data: Path) -> Installation | None:
        """A mixed old/new pair may offer recovery, never ordinary play or install admission."""
        from .manifest import Version

        version = value.get("version") if isinstance(value, dict) else None
        return cls._from_launch(value, data, expected_version=Version.parse(version).text)

    @classmethod
    def _from_launch(
        cls, value: object, data: Path, *, expected_version: str,
    ) -> Installation | None:
        if value is None:
            return None  # Source/engineering owners never acquire an install capability.
        if (
            not isinstance(value, dict)
            or set(value) != {"live", "data_root", "version", "target", "distribution"}
            or value["version"] != expected_version
            or value["target"] != "windows-x86_64"
            or value["distribution"] not in ("installed", "portable")
            or any(
                not isinstance(value[name], str)
                or not 0 < len(value[name]) <= 4096
                or not Path(value[name]).is_absolute()
                for name in ("live", "data_root")
            )
        ):
            raise UpdateError("invalid_launch_context", "The native update context is invalid.")
        live, selected = Path(value["live"]), Path(value["data_root"])
        if selected != data or (
            value["distribution"] == "portable" and selected != live / "isolated-data"
        ):
            raise UpdateError("wrong_data_root", "The native update data folder is inconsistent.")
        return cls(
            live, selected, value["distribution"], value["target"],
            directory_identity(live), directory_identity(selected),
        )

    def check(self, target: str, distribution: str) -> None:
        if (
            (target, distribution) != (self.target, self.distribution)
            or directory_identity(self.live) != self.app_identity
            or directory_identity(self.data) != self.data_identity
        ):
            raise UpdateError("changed_update_context", "The selected application copy changed.")
