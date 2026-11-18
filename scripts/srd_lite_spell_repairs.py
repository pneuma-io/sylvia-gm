"""SRD Lite 0.3.26 spell repairs, applied to the parsed runtime by update_srd_lite_spell_repairs.

Each repair edits one owning tool or definition and is idempotent, so the update script can be
rerun against an already repaired runtime without changing it again.
"""

from .srd_lite_profiles import lists


def _set(field, value, condition=None):
    effect = {"set": {"field": field, "value": value}}
    if condition is not None:
        effect["condition"] = condition
    return effect


def repair_spell_force_scene_bodies(runtime):
    """Issue 155: a Guardian of Faith body is a spell force, not a saved creature.

    Every other spell-force body (Mage Hand, Gate portals, Mansion servants) saves
    participant_ref null. The Guardian saved its own force ID there, so creature-only consumers
    such as the turn-end occupied-space check resolved it as a missing creature and refused.
    """
    tool = runtime["tools"]["create_srd_source_actor_scene"]
    for effects in lists(tool["effects"]):
        for index, effect in enumerate(effects):
            body = effect.get("set", {})
            if body.get("field") != "_created_actor_scene":
                continue
            metadata = body["value"].get("metadata", {})
            if metadata.get("participant_ref") == "_created_actor_participant_ref":
                return
            if metadata.get("participant_ref") != "summon_id":
                raise ValueError("Created-actor scene body seam changed")
            metadata["participant_ref"] = "_created_actor_participant_ref"
            effects[index:index] = [
                _set("_created_actor_participant_ref", "summon_id"),
                _set("_created_actor_participant_ref", None, "creature_type == 'spell_force'"),
            ]
            return
    raise ValueError("Created-actor scene body was not found")


def repair_creature_space_skips_spell_forces(runtime):
    """Issue 155: keep a Guardian already saved with its force ID out of creature-space pairs."""
    tool = runtime["tools"]["collect_srd_creature_space_intervals"]
    guard = " and _space_body.metadata.creature_type != 'spell_force'"
    for effects in lists(tool["effects"]):
        for effect in effects:
            loop = effect.get("for_each", {})
            if loop.get("as") != "_space_body":
                continue
            (pair,) = [e for e in loop["do"] if "_space_body.metadata.participant_ref" in str(e)]
            if guard not in pair["condition"]:
                pair["condition"] += guard
            return
    raise ValueError("Creature-space body loop was not found")


REPAIRS = (repair_spell_force_scene_bodies, repair_creature_space_skips_spell_forces)


def apply_repairs(runtime):
    for repair in REPAIRS:
        repair(runtime)
