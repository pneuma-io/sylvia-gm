"""First-party launch policy; unrelated rulesets retain the generic package path."""

from collections.abc import Sequence

from sylvia_gm.domain.authority import PackageLock, PackageRole, SafePayload, owned_payload
from sylvia_gm.domain.errors import CommandRejected

ARCHIVED_RULESET_IDS = frozenset({"wotc.srd.5.2.1"})


def require_active_ruleset(package_id: str) -> None:
    if package_id in ARCHIVED_RULESET_IDS:
        raise CommandRejected(
            "Full is archived; migrate this executable binding to SRD Lite before launch.",
            code="archived_ruleset",
        )


def player_ui_surfaces(
    surfaces: Sequence[SafePayload], package_lock: PackageLock,
) -> tuple[SafePayload, ...]:
    """Withdraw Lite's player encounter-ending control without rewriting saved package pins."""

    if not any(entry.role is PackageRole.PRIMARY and entry.package_id == "sylvia.srd-lite.bg3"
               for entry in package_lock.entries):
        return tuple(surfaces)
    return tuple(owned_payload({
        **surface,
        "actions": [action for action in surface["actions"]
                    if action.get("intent") != "end_combat_encounter"],
    }) for surface in surfaces)
