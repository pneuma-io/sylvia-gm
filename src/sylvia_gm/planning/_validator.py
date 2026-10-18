"""Cross-reference validation for planning output.

Validates that secret references in the campaign bible point to existing
entries in the reveal registry, and that entity cross-references
(e.g., leader_id, parent_location, owner) resolve correctly.

Called by PlanningEngine._parse_response() after Pydantic validation.
"""

from __future__ import annotations

from sylvia_gm.planning._campaign_bible import CampaignBible
from sylvia_gm.planning._planning_output import PlanningError
from sylvia_gm.planning._reveal_registry import RevealRegistry


class CrossPlanValidator:
    """Validates cross-references between campaign bible and reveal registry.

    Checks:
    1. Every secret ID referenced in any bible entity's ``secrets[]``
       exists in the reveal registry.
    2. Entity cross-references (leader_id, parent_location, owner) resolve
       to existing entities within the same bible.
    """

    @staticmethod
    def validate(bible: CampaignBible, registry: RevealRegistry) -> None:
        """Run all cross-reference validations.

        Raises:
            PlanningError: If any cross-reference fails to resolve.
        """
        CrossPlanValidator._validate_secret_references(bible, registry)
        CrossPlanValidator._validate_entity_references(bible)

    # ── Secret Reference Validation ────────────────────────────────────

    @staticmethod
    def _validate_secret_references(
        bible: CampaignBible, registry: RevealRegistry
    ) -> None:
        """Ensure every secret ID referenced in bible entities exists in the registry.

        Collects all secret IDs from every entity's ``secrets[]`` list and
        checks that each one has a matching entry in the reveal registry.
        """
        registry_ids = {s.id for s in registry.secrets}

        missing: list[tuple[str, str, str]] = []  # (entity_type, entity_id, secret_id)

        # Check world
        for sid in bible.world.secrets:
            if sid not in registry_ids:
                missing.append(("world", bible.world.id, sid))

        # Check factions
        for faction in bible.factions:
            for sid in faction.secrets:
                if sid not in registry_ids:
                    missing.append(("faction", faction.id, sid))

        # Check NPCs
        for npc in bible.npcs:
            for sid in npc.secrets:
                if sid not in registry_ids:
                    missing.append(("npc", npc.id, sid))

        # Check locations
        for loc in bible.locations:
            for sid in loc.secrets:
                if sid not in registry_ids:
                    missing.append(("location", loc.id, sid))

        # Check items
        for item in bible.items:
            for sid in item.secrets:
                if sid not in registry_ids:
                    missing.append(("item", item.id, sid))

        if missing:
            details = "; ".join(
                f"{t} '{eid}' references unknown secret '{sid}'"
                for t, eid, sid in missing[:10]
            )
            raise PlanningError(
                f"Cross-reference validation failed: {len(missing)} secret(s) "
                f"referenced in bible but missing from reveal registry. "
                f"Details: {details}"
            )

    # ── Entity Cross-Reference Validation ──────────────────────────────

    @staticmethod
    def _validate_entity_references(bible: CampaignBible) -> None:
        """Ensure entity-to-entity references resolve within the same bible.

        Checks:
        - faction.leader_id → must reference an existing NPC
        - location.parent_location → must reference an existing location
        - item.owner → must reference an existing NPC or location
        - faction.allies / faction.rivals → must reference existing factions
        - location.connecting_locations → must reference existing locations
        """
        npc_ids = {n.id for n in bible.npcs}
        location_ids = {l.id for l in bible.locations}
        faction_ids = {f.id for f in bible.factions}
        item_ids = {i.id for i in bible.items}

        errors: list[str] = []

        # Faction leader references
        for faction in bible.factions:
            if faction.leader_id and faction.leader_id not in npc_ids:
                errors.append(
                    f"Faction '{faction.id}' leader_id '{faction.leader_id}' "
                    f"does not reference an existing NPC"
                )
            # Ally references
            for ally_id in faction.allies:
                if ally_id and ally_id not in faction_ids:
                    errors.append(
                        f"Faction '{faction.id}' ally '{ally_id}' "
                        f"does not reference an existing faction"
                    )
            # Rival references
            for rival_id in faction.rivals:
                if rival_id and rival_id not in faction_ids:
                    errors.append(
                        f"Faction '{faction.id}' rival '{rival_id}' "
                        f"does not reference an existing faction"
                    )

        # Location parent + connections
        for loc in bible.locations:
            if loc.parent_location and loc.parent_location not in location_ids:
                errors.append(
                    f"Location '{loc.id}' parent_location '{loc.parent_location}' "
                    f"does not reference an existing location"
                )
            for conn_id in loc.connecting_locations:
                if conn_id and conn_id not in location_ids:
                    errors.append(
                        f"Location '{loc.id}' connecting_location '{conn_id}' "
                        f"does not reference an existing location"
                    )

        # Item owner references
        for item in bible.items:
            if item.owner:
                if item.owner not in npc_ids and item.owner not in location_ids:
                    errors.append(
                        f"Item '{item.id}' owner '{item.owner}' "
                        f"does not reference an existing NPC or location"
                    )

        if errors:
            raise PlanningError(
                f"Entity cross-reference validation failed: "
                f"{'; '.join(errors[:10])}"
            )

        # ── Duplicate ID Check ─────────────────────────────────────────

        all_ids: list[str] = []
        all_ids.append(bible.world.id)
        all_ids.extend(f.id for f in bible.factions)
        all_ids.extend(n.id for n in bible.npcs)
        all_ids.extend(l.id for l in bible.locations)
        all_ids.extend(i.id for i in bible.items)

        seen: set[str] = set()
        for eid in all_ids:
            if eid in seen:
                raise PlanningError(
                    f"Duplicate entity ID '{eid}' found in campaign bible. "
                    f"All entity IDs must be unique."
                )
            seen.add(eid)
