"""First-party launch policy; unrelated rulesets retain the generic package path."""

from sylvia_gm.domain.errors import CommandRejected

ARCHIVED_RULESET_IDS = frozenset({"wotc.srd.5.2.1"})


def require_active_ruleset(package_id: str) -> None:
    if package_id in ARCHIVED_RULESET_IDS:
        raise CommandRejected(
            "Full is archived; migrate this executable binding to SRD Lite before launch.",
            code="archived_ruleset",
        )
