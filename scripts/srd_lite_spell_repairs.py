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


def repair_area_concentration_card_rows(runtime):
    """Issue 166: one concentration save shows as one card row carrying its recorded roll.

    Area damage pushed a text row and a second caption row with the same label for the
    same saving throw. The text row now carries the equation and the caption row is gone.
    """
    for name in ("resolve_spell_area_save_damage_target", "resolve_srd_dragon_breath_target"):
        tool = runtime["tools"][name]
        (block,) = [
            effect
            for effects in lists(tool["effects"])
            for effect in effects
            if effect.get("condition") == "_area_target_concentration_save_roll != null"
        ]
        rows = block["on_true"]
        pushes = [e for e in rows if e.get("list_push", {}).get("list") == "_spell_detail_rows"]
        if len(pushes) == 1 and "equation" in pushes[0]["list_push"]["value"]:
            continue
        text, visual = pushes
        if text["list_push"]["value"].get("value") != "_area_concentration_text":
            raise ValueError("Area concentration text row changed: " + name)
        caption_export = {"_visual_concentration_caption": "_info_projection_message"}
        (caption,) = [e for e in rows if e.get("export_context") == caption_export]
        text["list_push"]["value"]["equation"] = visual["list_push"]["value"]["equation"]
        rows.remove(caption)
        rows.remove(visual)


def repair_spell_display_names(runtime):
    """Issue 166: card titles use the approved roster's names, not PDF extraction artifacts.

    The SRD text extraction reads the small-caps heading as "Acid SplASh"; the approved launch
    roster (content/launch-roster.json) and catalogue name it "Acid Splash". It is the only
    runtime spell name with this artifact.
    """
    definitions = runtime["definitions"]
    for table, row in (
        (definitions["srd_lite_spell_display_names"], "acid_splash"),
        (definitions["spell_casting"]["acid_splash"], "name"),
    ):
        if table[row] not in {"Acid SplASh", "Acid Splash"}:
            raise ValueError("Acid Splash display name changed")
        table[row] = "Acid Splash"


REPAIRS = (
    repair_spell_force_scene_bodies,
    repair_creature_space_skips_spell_forces,
    repair_area_concentration_card_rows,
    repair_spell_display_names,
)


def apply_repairs(runtime):
    for repair in REPAIRS:
        repair(runtime)
