"""Validated first-party rendering defaults, separate from character appearance."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ArtStyle:
    """One versioned Setting default and its immutable rendering reference."""

    style_id: str
    name: str
    campaign_setting_ids: tuple[str, ...]
    brief: str
    reference_sha256: str

    @property
    def reference_path(self) -> str:
        return f"assets/styles/{self.reference_sha256}.png"

    def as_payload(self) -> dict[str, Any]:
        return {
            "id": self.style_id, "name": self.name,
            "campaign_setting_ids": list(self.campaign_setting_ids), "brief": self.brief,
            "reference": {"path": self.reference_path, "sha256": self.reference_sha256},
        }


def parse_art_style(value: object) -> ArtStyle:
    """Reject malformed styles before any reference becomes a filesystem read."""
    if not isinstance(value, Mapping) or set(value) != {
        "id", "name", "campaign_setting_ids", "brief", "reference",
    }:
        raise ValueError("The shared artwork style is invalid")
    for key, limit in (("id", 128), ("name", 160), ("brief", 6000)):
        text = value[key]
        if not isinstance(text, str) or not 0 < len(text) <= limit or "\x00" in text:
            raise ValueError("The shared artwork style is invalid")
    if re.fullmatch(r"[a-z][a-z0-9._-]*", value["id"]) is None:
        raise ValueError("The shared artwork style identity is invalid")
    settings = value["campaign_setting_ids"]
    if (
        not isinstance(settings, list) or not 1 <= len(settings) <= 64
        or any(not isinstance(item, str) or not 0 < len(item) <= 160 or "\x00" in item
               for item in settings)
        or len(set(settings)) != len(settings)
    ):
        raise ValueError("The shared artwork style Settings are invalid")
    reference = value["reference"]
    if (
        not isinstance(reference, Mapping) or set(reference) != {"path", "sha256"}
        or not isinstance(reference["sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", reference["sha256"]) is None
        or reference["path"] != f"assets/styles/{reference['sha256']}.png"
    ):
        raise ValueError("The shared artwork style reference is invalid")
    return ArtStyle(
        value["id"], value["name"], tuple(settings), value["brief"], reference["sha256"],
    )


def catalogue_art_styles(
    catalogue: Mapping[str, Any], *, include_retained: bool = False,
) -> tuple[ArtStyle, ...]:
    """Retain immutable prior styles for saved requests without selecting them as defaults."""
    values = catalogue.get("styles", [])
    retained_values = catalogue.get("retained_styles", [])
    if (
        not isinstance(values, list) or not isinstance(retained_values, list)
        or len(values) + len(retained_values) > 64
    ):
        raise ValueError("The shared artwork styles are invalid")
    styles = tuple(parse_art_style(value) for value in values)
    retained = tuple(parse_art_style(value) for value in retained_values)
    identities = [style.style_id for style in (*styles, *retained)]
    settings = [setting for style in styles for setting in style.campaign_setting_ids]
    if len(set(identities)) != len(identities) or len(set(settings)) != len(settings):
        raise ValueError("The shared artwork styles have conflicting defaults")
    return (*styles, *retained) if include_retained else styles
