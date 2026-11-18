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


BONUS_INSTALLERS = ("install_srd_timed_speed_bonus", "install_srd_timed_hit_point_bonus")


def repair_bonus_caster_ids(runtime):
    """Issue 164: Longstrider and Aid records save their caster's ID, not its whole state.

    The installers took the caster as a plain caster_state_id string. The engine resolves a
    parameter whose value names an entity into that entity's state, so the saved record's
    caster field held a full character copy. Like the actor-defense installer, they now take
    an entity-resolved caster_state and save _caster_state_id.
    """
    tools = runtime["tools"]
    for name in BONUS_INSTALLERS:
        tool = tools[name]
        if "param_caster_state_5310b8b23a525eea" in tool["parameters"]:
            continue
        index = tool["parameters"].index("param_caster_state_id_3392afbdff598c32")
        tool["parameters"][index] = "param_caster_state_5310b8b23a525eea"
        (record,) = [
            e["list_push"]["value"]
            for effects in lists(tool["effects"])
            for e in effects
            if isinstance(e.get("list_push", {}).get("value"), dict)
            and "caster" in e["list_push"]["value"]
        ]
        if record["caster"] != "caster_state_id":
            raise ValueError("Bonus record caster changed: " + name)
        record["caster"] = "_caster_state_id"
    for tool in tools.values():
        for effects in lists(tool.get("effects", [])):
            for effect in effects:
                if effect.get("call") in BONUS_INSTALLERS and "caster_state_id" in effect["params"]:
                    params = effect["params"]
                    effect["params"] = {
                        ("caster_state" if key == "caster_state_id" else key): value
                        for key, value in params.items()
                    }


MULTI_TARGET_FAMILIES = (
    "multi_target_concentration_ability_advantage",
    "multi_target_concentration_d20_modifier",
    "multi_target_timed_repeating_save_condition",
    "multi_target_timed_speed_bonus",
)
TARGET_COUNT_ERROR = "The selected slot cannot affect that many creatures."


def repair_begin_checks_slot_target_count(runtime):
    """Issue 164: a combat cast checks its slot's recipient limit before reserving the Action.

    Each multi-target family (Bless, Bane, Hold Person, Longstrider and their kin) refuses too
    many creatures when it resolves, but begin_srd_spell_action only checked that at
    completion, after the Action was reserved. The same formula now runs at begin.
    """
    effects = runtime["tools"]["begin_srd_spell_action"]["effects"]
    if any(e.get("require", {}).get("error") == TARGET_COUNT_ERROR for e in effects):
        return
    (slot_check,) = [
        i
        for i, e in enumerate(effects)
        if e.get("require", {}).get("condition") == "_slot_level >= _spell_definition.base_level"
    ]
    other_family = " and ".join(
        f"_spell_definition.family != '{family}'" for family in MULTI_TARGET_FAMILIES
    )
    effects.insert(
        slot_check + 1,
        {
            "require": {
                "condition": (
                    f"({other_family}) or targets == null"
                    " or len(targets) <= _spell_definition.base_targets + (_slot_level"
                    " - _spell_definition.base_level) * _spell_definition.targets_per_slot_level"
                ),
                "error": TARGET_COUNT_ERROR,
            }
        },
    )


SELF_RANGE_ERROR = "This spell's range is Self; it affects only its caster."


def repair_self_range_information_target(runtime):
    """Issue 161: a Self-range information spell refuses another chosen creature.

    Speak with Animals (Range: Self) accepted a different target and silently applied the
    spell to its caster. Each public casting root now refuses that choice right after it
    resolves the spell, before any payment.
    """
    check = {
        "require": {
            "condition": (
                "definitions.spells[_ms].family != 'information_spell'"
                " or definitions.spells[_ms].range_kind != 'self'"
                " or _target_id == null or _target_id == _caster_state_id"
            ),
            "error": SELF_RANGE_ERROR,
        }
    }
    for name in ("cast_spell", "cast_spell_outside_combat", "begin_srd_spell_action"):
        effects = runtime["tools"][name]["effects"]
        if any(e.get("require", {}).get("error") == SELF_RANGE_ERROR for e in effects):
            continue
        (catalogue,) = [
            i
            for i, e in enumerate(effects)
            if e.get("require", {}).get("condition") == "_ms in definitions.srd_lite_catalogue"
        ]
        effects.insert(catalogue + 1, dict(check))


DUPLICATE_TARGET_ERROR = "Each creature can be chosen only once for this spell."


def repair_multi_target_distinct_recipients(runtime):
    """Issue 164: Bless, Bane, Hold Person, Longstrider and kin refuse a repeated creature.

    Each extra creature costs a higher slot level, so naming one creature twice is not a
    valid choice. These families accepted it; like the actor-defense family they now refuse.
    """
    for family in MULTI_TARGET_FAMILIES:
        effects = runtime["tools"]["cast_spell_" + family]["effects"]
        if any(e.get("set", {}).get("field") == "_distinct_spell_targets" for e in effects):
            continue
        effects[1:1] = [
            _set("_distinct_spell_targets", []),
            {
                "for_each": {
                    "collection": "targets",
                    "as": "_distinct_spell_target",
                    "do": [
                        {
                            "require": {
                                "condition": (
                                    "_distinct_spell_target not in _distinct_spell_targets"
                                ),
                                "error": DUPLICATE_TARGET_ERROR,
                            }
                        },
                        {
                            "list_push": {
                                "list": "_distinct_spell_targets",
                                "value": "_distinct_spell_target",
                            }
                        },
                    ],
                }
            },
        ]


OBJECT_TARGET_ERROR = "A timed movement spell needs a living creature, not an object."


def repair_speed_bonus_needs_a_creature(runtime):
    """Issue 164: Longstrider touches a creature; a saved object is not a valid recipient."""
    conditions = runtime["tools"]["install_srd_timed_speed_bonus"]["pre_conditions"]
    if any(c.get("error") == OBJECT_TARGET_ERROR for c in conditions):
        return
    conditions.insert(
        1,
        {"condition": "target_state.creature_kind != 'object'", "error": OBJECT_TARGET_ERROR},
    )


REPAIRS = (
    repair_spell_force_scene_bodies,
    repair_creature_space_skips_spell_forces,
    repair_area_concentration_card_rows,
    repair_spell_display_names,
    repair_bonus_caster_ids,
    repair_begin_checks_slot_target_count,
    repair_self_range_information_target,
    repair_multi_target_distinct_recipients,
    repair_speed_bonus_needs_a_creature,
)


def apply_repairs(runtime):
    for repair in REPAIRS:
        repair(runtime)
