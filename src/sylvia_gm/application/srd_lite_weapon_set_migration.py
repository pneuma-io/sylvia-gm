"""Lite 0.2 -> 0.3 migration for AuthorityOwner.activate_package.

The callback returns detached state. The existing activation transaction changes
the pin and commits it atomically; this module never edits a live database.
"""

from copy import deepcopy

PACKAGE_ID = "sylvia.srd-lite.bg3"
MIGRATION_ID = "srd-lite-saved-weapon-sets-v1"


def migrate_state(state, weapons):
    copied = deepcopy(dict(state))
    story = copied.get(PACKAGE_ID + ".state", {})
    if story.get("pending_srd_action_stack"):
        raise ValueError("Complete the saved pending mechanic before upgrading equipment.")
    for actor in copied.get(PACKAGE_ID + ".entities", {}).values():
        if not isinstance(actor, dict) or actor.get("creature_kind") != "character":
            continue
        if actor.get("weapon_sets") is not None:
            if actor["weapon_sets"].get("format") != 1:
                raise ValueError("Unsupported saved equipment format.")
            continue
        shield = (actor.get("worn_shield") or {}).get("inventory_item")
        pairs = {
            "format": 1,
            "active_set": None,
            "melee": {"main_hand": None, "off_hand": shield},
            "ranged": {"main_hand": None, "off_hand": None},
        }
        inventory = actor.get("inventory", [])
        actual = [
            item for item in actor.get("held_items", []) if item in inventory and item in weapons
        ]
        if len(actual) == 1:
            item = actual[0]
            info = weapons[item]
            two = "two_handed" in info["properties"]
            name = "ranged" if info["kind"] == "ranged" else "melee"
            if name == "ranged" or not (two and shield):
                pairs[name] = {
                    "main_hand": item,
                    "off_hand": item if two else shield if name == "melee" else None,
                }
                pairs["active_set"] = name
        actor["weapon_sets"] = pairs
        # Preserve existing per-turn eligibility; this adds a receipt rather
        # than restoring Light/Nick, actions, ammunition or resources.
        actor.setdefault(
            "light_attack_source_items",
            [
                item + ":1"
                for item in actor.get("light_attack_source_weapons", [])
                if item in weapons
            ],
        )
    return copied
