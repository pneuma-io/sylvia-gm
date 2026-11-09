"""The twelve authored SRD starters, admitted through ordinary Character File storage."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from importlib.resources import files
from pathlib import Path

from sylvia_gm.application.bundled_campaign_plans import bundled_plan_catalog
from sylvia_gm.application.bundled_packages import bundled_rift_authority
from sylvia_gm.application.reusable_participants import (
    ReusableParticipant,
    compatible_reusable_participants,
    save_reusable_participant,
)
from sylvia_gm.domain.authority import PackageLockEntry, SafePayload
from sylvia_gm.domain.campaign_plan import PackageBinding
from sylvia_gm.domain.package_library import ParticipantArtifactDeclaration, canonical_json_bytes

_ROSTERS: dict[tuple[str, ...], tuple[ReusableParticipant, ...]] = {}
_ROSTER_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def _bundled_bindings() -> frozenset[tuple[str, str, str, str, str, str]]:
    """Offer starters only for exact shipped closures, never arbitrary same-ID Settings."""
    authority = bundled_rift_authority()
    pairs = [(authority.ruleset.as_payload(), authority.setting.as_payload())]
    pairs.extend((row["ruleset"], row["campaign_setting"]) for row in bundled_plan_catalog())
    return frozenset(
        (
            rules["id"],
            rules["version"],
            rules["content_sha256"],
            setting["id"],
            setting["version"],
            setting["content_sha256"],
        )
        for rules, setting in pairs
        if rules["id"] == "sylvia.srd-lite.bg3"
    )


def bundled_starter_participants(
    storage_root: Path,
    *,
    package: PackageLockEntry,
    declaration: ParticipantArtifactDeclaration,
    campaign_setting: PackageBinding,
    setting_character_schema: SafePayload | None = None,
) -> tuple[ReusableParticipant, ...]:
    """Calculate and publish concrete builds against the selected bundled declaration.

    The private cache uses the existing immutable save/load path. Edited copies are saved
    in the ordinary participant library; library deletion cannot address this cache.
    """
    if (
        declaration.kind != "player_character"
        or package.package_id != "sylvia.srd-lite.bg3"
        or campaign_setting.package_id != "pneuma.severed-lands"
    ):
        return ()
    if (
        package.package_id,
        package.version,
        package.sha256,
        campaign_setting.package_id,
        campaign_setting.version,
        campaign_setting.content_sha256,
    ) not in _bundled_bindings():
        return ()
    source = files("sylvia_gm.campaign").joinpath("srd-starters.json").read_text(encoding="utf-8")
    key = (
        str(storage_root.resolve()),
        package.package_id,
        package.version,
        package.sha256,
        declaration.schema_id,
        declaration.schema_sha256,
        campaign_setting.package_id,
        campaign_setting.version,
        campaign_setting.content_sha256,
        hashlib.sha256(source.encode()).hexdigest(),
        hashlib.sha256(canonical_json_bytes(setting_character_schema or {})).hexdigest(),
    )
    # These first-party sources cannot be edited in the library. Avoid recalculating every
    # build on each Interview refresh, and never expose the cached objects to a caller.
    with _ROSTER_LOCK:
        if key not in _ROSTERS:
            rows = json.loads(source)
            cache = storage_root / "bundled-srd-starters"
            roster = tuple(
                replace(
                    save_reusable_participant(
                        cache,
                        operation_id=f"bundled-srd-starter-{row['id']}",
                        role="player_character",
                        summary=row["summary"],
                        authored_profile=row["authored_profile"],
                        participant=row["participant"],
                        entity_state=row["entity_state"],
                        package=package,
                        declaration=declaration,
                        campaign_setting=campaign_setting,
                        setting_character_schema=setting_character_schema,
                    ),
                    builtin_starter=True,
                )
                for row in rows
            )
            if len(_ROSTERS) >= 32:
                _ROSTERS.clear()
            _ROSTERS[key] = roster
        return deepcopy(_ROSTERS[key])


def available_reusable_participants(
    storage_root: Path,
    *,
    package: PackageLockEntry,
    declaration: ParticipantArtifactDeclaration,
    campaign_setting: PackageBinding,
    legacy_campaign_setting: PackageBinding | None = None,
    setting_character_schema: SafePayload | None = None,
    on_invalid: Callable[[str, Exception], None] | None = None,
) -> tuple[ReusableParticipant, ...]:
    """Combine the shipped roster and ordinary exact-bound saved characters."""
    starters = bundled_starter_participants(
        storage_root,
        package=package,
        declaration=declaration,
        campaign_setting=campaign_setting,
        setting_character_schema=setting_character_schema,
    )
    saved = compatible_reusable_participants(
        storage_root,
        package=package,
        declaration=declaration,
        campaign_setting=campaign_setting,
        legacy_campaign_setting=legacy_campaign_setting,
        setting_character_schema=setting_character_schema,
        on_invalid=on_invalid,
    )
    return (*starters, *saved)
