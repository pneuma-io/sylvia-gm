"""SRD Lite 0.3.29 repairs for issue 279: a combat casting window must never lock the fight.

In combat, begin_srd_spell_action reserves the Action and opens the casting window; the spell
resolves later, in complete_srd_pending_action. The 21 Full-authored spells retained beside the
96 Lite profiles were never given the saved-scene projection the Lite profiles use, so begin
accepted a cast that completion then refused for a missing or impossible scene fact. A refused
completion leaves its window open, and every turn end, combat end and new cast waits for it.

Each repair edits one owning tool or definition and is idempotent.
"""

from .srd_lite_catalogue import RETAINED_FULL_AUTHORED_SPELLS
from .srd_lite_combat_target_facts import FAMILIES as SCALAR_FAMILIES
from .srd_lite_multi_target_facts import FAMILIES as MULTI_FAMILIES
from .srd_lite_profiles import lists

# Multi-recipient families of retained spells that the Lite multi projection did not list.
RETAINED_MULTI_FAMILIES = {"multi_target_concentration_ability_advantage"}


def _set(field, value, condition=None):
    effect = {"set": {"field": field, "value": value}}
    if condition is not None:
        effect["condition"] = condition
    return effect


def retained_geometry_spells(spells):
    """The retained creature-targeting spells, split into scalar and multi-recipient ones."""
    scalar, multi = set(), set()
    for name in RETAINED_FULL_AUTHORED_SPELLS:
        row = spells[name]
        if row.get("range_kind") not in ("distance", "touch"):
            continue
        if row["family"] in SCALAR_FAMILIES:
            scalar.add(name)
        elif row["family"] in MULTI_FAMILIES or row["family"] in RETAINED_MULTI_FAMILIES:
            multi.add(name)
    return scalar, multi


def repair_retained_spells_read_the_combat_scene(runtime):
    """Issue 279: retained creature spells read range, sight, touch and cover from the map.

    Dissonant Whispers, Harm, Acid Arrow, Bane, Stoneskin and the other retained spells were
    left out of srd_lite_scalar_geometry_spells and srd_lite_multi_geometry_spells, so in
    combat nothing measured their recipient. Begin committed without a distance and the spell's
    own resolution then refused at completion. Listed spells are measured from the saved scene
    at begin and again at completion; a recipient out of range or without a clear path is
    refused at begin, before anything is spent.
    """
    definitions = runtime["definitions"]
    scalar, multi = retained_geometry_spells(definitions["spells"])
    scalar_list = definitions["srd_lite_scalar_geometry_spells"]
    multi_list = definitions["srd_lite_multi_geometry_spells"]
    definitions["srd_lite_scalar_geometry_spells"] = sorted(set(scalar_list) | scalar)
    definitions["srd_lite_multi_geometry_spells"] = sorted(set(multi_list) | scalar | multi)


SCALAR_COMBAT_EXEMPTION = (
    "(combat.encounter_active == true and spell in definitions.srd_lite_scalar_geometry_spells"
    " and (targets == null or len(targets) == 0))"
)
MEASURED_COMBAT_EXEMPTION = (
    "(combat.encounter_active == true and spell in definitions.srd_lite_multi_geometry_spells)"
)


def repair_begin_measures_listed_recipients(runtime):
    """Issue 279: a measured spell in combat need not restate scene facts it measures.

    Begin's scalar fact checks waived target_distance and touch only when no targets list was
    sent, although the projection measures every recipient of a listed spell either way. The
    release fight sent target and targets together, so the same spell would have been refused
    for a fact the engine had already measured. The waiver now covers every measured spell.
    """
    for condition in runtime["tools"]["begin_srd_spell_action"]["pre_conditions"]:
        text = condition.get("condition", "") if isinstance(condition, dict) else ""
        if SCALAR_COMBAT_EXEMPTION in text:
            condition["condition"] = text.replace(SCALAR_COMBAT_EXEMPTION, MEASURED_COMBAT_EXEMPTION)


RELEASE_TOOL = "release_srd_unresolved_spell_cast"
RELEASED = "released_unresolved"
RELEASE_DESCRIPTION = (
    "In combat, close an open casting window that complete_srd_pending_action refused and that "
    "no corrected input can complete. The cast fails without resolving: its Action stays used, "
    "no spell slot is spent, and a creature's reserved daily or feature use is settled as for a "
    "countered spell. Use it only after a refused completion, never to cancel a spell that can "
    "still resolve."
)
RELEASE_CARD = (
    "{_pending_card_title}: casting released; the spell could not resolve. "
    "Its Action stays used and no spell slot is spent."
)
CARD_EXPORTS = (
    "_pending_card_title",
    "_pending_card_summary",
    "_pending_card_details",
)
GUARD_ERROR = "Complete the pending reaction window before advancing its turn or time."
GUARD_HINT = (
    " If its completion is refused, close that casting window with "
    "release_srd_unresolved_spell_cast."
)
# The package record entry for the new public operation, beside complete_srd_pending_action.
NEW_PUBLIC_TOOLS = {
    RELEASE_TOOL: {
        "after": "complete_srd_pending_action",
        "capability": "dnd.srd.5.2.1.spells",
        "state_predicates": [],
        "turn_statuses": ["open"],
        "writers": ["dm"],
    },
}


def _require(condition, error):
    return {"require": {"condition": condition, "error": error}}


def repair_refused_cast_can_be_released(runtime):
    """Issue 279: a casting window whose completion is refused can be closed.

    The new public operation closes it as a lost cast, the same settlement as a successful
    Counterspell: the Action stays used, no spell slot is spent and a creature's reserved daily
    or feature use is settled as lost. complete_srd_pending_action keeps its exact contract, so
    a saved 0.3.28 campaign can adopt these rules and then release its stuck window.
    """
    tools = runtime["tools"]
    complete = tools["complete_srd_pending_action"]
    tools[RELEASE_TOOL] = {
        "description": RELEASE_DESCRIPTION,
        "effects": [
            _require(
                "lasting_state.pending_srd_actions != null and "
                "pending_action_id in lasting_state.pending_srd_actions",
                "This pending action does not exist.",
            ),
            _set("_release_action", "lasting_state.pending_srd_actions[pending_action_id]"),
            _set("_release_stack", "lasting_state.pending_srd_action_stack"),
            _require(
                "len(_release_stack) > 0 and _release_stack[len(_release_stack) - 1] == "
                "pending_action_id",
                "Release the innermost pending action first.",
            ),
            _require(
                "_release_action.kind == 'spell_cast' and _release_action.status == 'open' and "
                "_release_action.owner_kind != 'extended_casting'",
                "Only an open casting window can be released.",
            ),
            _require(
                "_release_action.turn_actor != null and combat.turn_active == true and "
                "combat.active_actor == _release_action.turn_actor",
                "Release the casting window on its original turn.",
            ),
            _set("lasting_state.pending_srd_actions.{pending_action_id}.status", "'lost'"),
            _set(f"lasting_state.pending_srd_actions.{{pending_action_id}}.{RELEASED}", True),
            {
                "call": "complete_srd_pending_action",
                "params": {"pending_action_id": "{pending_action_id}"},
                "export_context": {name: name for name in CARD_EXPORTS},
            },
        ],
        "metadata": {
            "category": "combat",
            "result_card": {
                key: value
                for key, value in complete["metadata"]["result_card"].items()
                if key != "equations"
            },
        },
        "parameters": [
            {"name": "pending_action_id", "type": "string"},
            {
                "name": "lasting_state",
                "required": False,
                "default": "state",
                "resolve": "entity",
                "model_hidden": True,
            },
        ],
    }
    effects = complete["effects"]
    lost_card = [
        e
        for e in effects
        if e.get("condition") == "_pending_action.spell != null"
        and any(
            row.get("condition") == "_pending_action.status == 'lost' or "
            "_pending_action.owner_kind == 'extended_casting'"
            for row in e.get("on_true", [])
        )
    ]
    if len(lost_card) != 1:
        raise ValueError("Pending spell card seam changed")
    rows = lost_card[0]["on_true"]
    if not any(e.get("condition") == f"_pending_action.{RELEASED} == true" for e in rows):
        rows.append(
            {
                "condition": f"_pending_action.{RELEASED} == true",
                "on_true": [
                    {
                        "call": "record_srd_result_text",
                        "params": {"text": RELEASE_CARD},
                        "export_context": {"_recorded_result_text": "_recorded_result_text"},
                    },
                    _set(
                        "_pending_card_summary",
                        [{"label": "'Casting'", "value": "_recorded_result_text"}],
                    ),
                ],
            }
        )
    settle = tools["settle_srd_countered_cast_use"]["effects"]
    (check,) = [
        e for e in settle
        if e.get("require", {}).get("error")
        == "Only the actual countered casting action can settle this use."
    ]
    old = "_countered_cast.countered_by != null"
    new = f"(_countered_cast.countered_by != null or _countered_cast.{RELEASED} == true)"
    if new not in check["require"]["condition"]:
        if old not in check["require"]["condition"]:
            raise ValueError("Countered cast settlement seam changed")
        check["require"]["condition"] = check["require"]["condition"].replace(old, new)


def repair_window_guards_name_the_release(runtime):
    """Issue 279: the refusals a stuck window causes say how to close it."""
    for tool in runtime["tools"].values():
        for owner in (tool.get("pre_conditions") or []), tool.get("effects") or []:
            for item in _walk(owner):
                if item.get("error") == GUARD_ERROR:
                    item["error"] = GUARD_ERROR + GUARD_HINT


def _walk(effects):
    for effect in effects:
        if not isinstance(effect, dict):
            continue
        if "error" in effect and "condition" in effect:
            yield effect
        for key in ("require",):
            if isinstance(effect.get(key), dict):
                yield effect[key]
        for key in ("on_true", "on_false", "do", "effects"):
            if isinstance(effect.get(key), list):
                yield from _walk(effect[key])
        for key in ("for_each",):
            if isinstance(effect.get(key), dict):
                yield from _walk(effect[key].get("do") or [])


def _calls_in(tool, name):
    """Each (effect list, index) holding a nested call of the named tool."""
    for effects in lists(tool["effects"]):
        for index, effect in enumerate(effects):
            if effect.get("call") == name:
                yield effects, index


def _derive_call_fact(cast, call, parameter, private, source, fallback_condition, fallback):
    """Pass a call a private copy of a fact, filled with a safe value when it was left out."""
    sites = list(_calls_in(cast, call))
    if not sites:
        raise ValueError("Fact seam missing: " + call)
    for effects, index in sites:
        params = effects[index]["params"]
        if params[parameter] == "{" + private + "}":
            continue
        if params[parameter] != "{" + source + "}":
            raise ValueError(f"Fact seam changed: {call}.{parameter}")
        params[parameter] = "{" + private + "}"
        effects[index:index] = [
            _set(private, source),
            _set(private, fallback, f"{private} == null and ({fallback_condition})"),
        ]


def repair_unmeasured_facts_have_safe_values(runtime):
    """Issue 279: facts a spell cannot use no longer block its completion when left out.

    Fear and Sleep need no sight of their recipients, Blur touches only its caster and Dispel
    Magic's range is not Touch, yet each required the unused fact, which nothing measured for
    them in combat. The fact keeps any supplied value; when it is missing it takes the value
    that grants nothing (no sight, no touch), or automatic self-touch for a Self spell. Dispel
    Magic's creature recipient is now measured from the saved scene like the scalar spells.
    """
    cast = runtime["tools"]["cast_spell"]
    _derive_call_fact(
        cast, "cast_srd_control_behavior", "caster_can_see_all_targets",
        "_control_sight_fact", "_multi_fact_caster_can_see_all_targets",
        "definitions.spells[_ms].requires_sight != true", False,
    )
    _derive_call_fact(
        cast, "cast_spell_timed_actor_defense", "caster_can_touch_all_targets",
        "_defense_touch_fact", "_multi_fact_caster_can_touch_all_targets",
        "definitions.spells[_ms].range_kind == 'self'", True,
    )
    _derive_call_fact(
        cast, "cast_srd_recovery", "can_touch",
        "_recovery_touch_fact", "_coord_fact_caster_can_touch_target",
        "definitions.spells[_ms].range_kind != 'touch'", False,
    )
    definitions = runtime["definitions"]
    for key in ("srd_lite_scalar_geometry_spells", "srd_lite_multi_geometry_spells"):
        definitions[key] = sorted({*definitions[key], "dispel_magic"})
    for effects, index in _calls_in(cast, "qualify_srd65_dispel_recipient"):
        params = effects[index]["params"]
        if params["target_distance"] == "{target_distance}":
            params["target_distance"] = "{_coord_fact_target_distance}"


RESOLVING = "srd_resolving_cast_id"
OPEN_WINDOW = (
    "lasting_state.pending_srd_action_stack == null or "
    "len(lasting_state.pending_srd_action_stack) == 0"
)
RESOLVING_WINDOW = (
    f" or (len(lasting_state.pending_srd_action_stack) == 1 and lasting_state.{RESOLVING} != null"
    f" and lasting_state.pending_srd_action_stack[0] == lasting_state.{RESOLVING})"
)


def repair_resolving_cast_is_not_a_blocking_window(runtime):
    """Issue 279: a cast's own resolution is not blocked by its own casting window.

    complete_srd_pending_action resolves a cast while that cast's window is still the only
    entry on the pending stack. True Strike's weapon attack and a frightened creature's dropped
    items then refused because a window was open, so a combat True Strike always locked, and
    Fear did whenever a target held something. Completion now names the window it is
    resolving, and those two guards admit exactly that window and nothing else.
    """
    complete = runtime["tools"]["complete_srd_pending_action"]
    sites = list(_calls_in(complete, "set_srd_pending_cast_completion"))
    if len(sites) != 2 or sites[0][0] is not sites[1][0]:
        raise ValueError("Cast completion seam changed")
    effects = sites[0][0]
    if not any(e.get("set", {}).get("field") == f"lasting_state.{RESOLVING}" for e in effects):
        start, end = sites[0][1], sites[1][1]
        effects.insert(end + 1, _set(f"lasting_state.{RESOLVING}", None))
        effects.insert(start + 1, _set(f"lasting_state.{RESOLVING}", "pending_action_id"))
    guards = (
        ("require_srd_pending_window_closed",
         "Complete the pending reaction window before another ordinary spell action."),
        ("resolve_srd_settled_weapon_attack",
         "Complete the previous attack's reaction window first."),
    )
    for name, error in guards:
        found = [
            item for item in _walk(runtime["tools"][name]["effects"])
            if item.get("error") == error
        ]
        if len(found) != 1:
            raise ValueError("Pending window guard seam changed: " + name)
        (guard,) = found
        if RESOLVING in guard["condition"]:
            continue
        if guard["condition"] != OPEN_WINDOW:
            raise ValueError("Pending window guard changed: " + name)
        guard["condition"] = OPEN_WINDOW + RESOLVING_WINDOW


TRUE_STRIKE_RANGE = (
    "_ts_scene_same_plane == true and _ts_scene_line_of_sight == true"
    " and ceil(_ts_scene_distance) <= _ts_weapon.long_range"
    " and (true_strike_attack_mode != 'melee'"
    " or ceil(_ts_scene_distance) <= _ts_weapon.normal_range)"
    " and ((true_strike_attack_mode == 'melee' and (('reach' in _ts_weapon.properties and"
    " _ts_scene_distance <= caster_state.melee_reach + 5) or ('reach' not in"
    " _ts_weapon.properties and _ts_scene_distance <= caster_state.melee_reach)))"
    " or (true_strike_attack_mode != 'melee' and _ts_scene_distance <= _ts_weapon.long_range))"
)
TRUE_STRIKE_ERROR = (
    "The actual True Strike target is beyond the chosen weapon's range or reach, or lacks a "
    "clear path on this plane. Check input 'target'."
)


def repair_true_strike_checks_its_weapon_range_at_begin(runtime):
    """Issue 279: a combat True Strike refuses an out-of-reach target before anything is spent.

    The weapon's range and reach were checked only when the cast resolved, after begin had
    reserved the Action. Begin now measures the saved scene with the weapon attack's own test.
    """
    effects = runtime["tools"]["begin_srd_spell_action"]["effects"]
    for effect in effects:
        for row in effect.get("on_true", []):
            if row.get("require", {}).get("error") == TRUE_STRIKE_ERROR:
                return
    (anchor,) = [
        i for i, e in enumerate(effects)
        if str(e.get("condition", "")).startswith(
            "combat.encounter_active == true and len(_multi_recipients) > 0")
    ]
    effects.insert(anchor + 1, {
        "condition": (
            "combat.encounter_active == true and spell == 'true_strike' and _target_id != null"
            " and true_strike_weapon != null"
        ),
        "on_true": [
            {
                "call": "resolve_srd_scene_relation_node",
                "params": {
                    "actor_state": "{_caster_state_id}",
                    "target_state": "{_target_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
                "export_context": {
                    "_ts_scene_distance": "_scene_distance",
                    "_ts_scene_same_plane": "_scene_same_plane",
                    "_ts_scene_line_of_sight": "_scene_line_of_sight",
                },
            },
            _set("_ts_weapon", "definitions.weapons[true_strike_weapon]"),
            {"require": {"condition": TRUE_STRIKE_RANGE, "error": TRUE_STRIKE_ERROR}},
        ],
    })


def _missing(name, why):
    return f"Missing input '{name}'. {why}"


# Inputs a combat cast's resolution refuses without, checked at begin before anything is spent.
BEGIN_REQUIREMENTS = (
    # Scene facts and placement the saved map does not establish for these spells.
    ("spell != 'ice_knife' or combat.encounter_active != true or targets == null"
     " or len(targets) == 0 or target_distance != null",
     _missing("target_distance", "Supply the current distance in feet to Ice Knife's attack "
              "target, or omit targets so the saved scene measures it.")),
    ("spell != 'chain_lightning' or combat.encounter_active != true"
     " or caster_can_see_target == true",
     _missing("caster_can_see_target", "Chain Lightning's primary target must be in the "
              "caster's sight; supply true only when it is.")),
    ("spell != 'arcane_eye' or information_lighting == 'bright' or information_lighting == 'dim'"
     " or information_lighting == 'darkness' or information_lighting == 'magical_darkness'",
     _missing("information_lighting", "Specify the actual ambient lighting at the sensor "
              "position: bright, dim, darkness or magical_darkness.")),
    ("spell != 'light' or (illumination_object != null and illumination_object != '')",
     _missing("illumination_object", "Choose the touched scene object.")),
    ("spell != 'sunburst' or (sunburst_origin != null and len(sunburst_origin) == 3)",
     _missing("sunburst_origin", "Choose Sunburst's actual three-dimensional point.")),
    # Choices the resolution needs, which were reported only under internal step names.
    ("spell != 'mage_hand' or (utility_output_id != null and utility_output_id != ''"
     " and utility_form_id != null and utility_form_id != '')",
     _missing("utility_output_id", "Mage Hand needs utility_output_id and utility_form_id "
              "for the hand it creates.")),
    ("spell != 'summon_dragon' or (summon_id != null and summon_id != '')",
     _missing("summon_id", "Choose the summoned dragon's identity.")),
    ("spell != 'polymorph' or (transformation_form != null and transformation_form != '')",
     _missing("transformation_form", "Choose Polymorph's new form.")),
    ("spell != 'dragons_breath' or selected_damage_type != null",
     _missing("selected_damage_type", "Choose the breath's damage type.")),
    ("spell != 'suggestion' or (targets != null and len(targets) > 0)",
     _missing("targets", "Select Suggestion's actual recipients.")),
    ("spell != 'blindness_deafness' or (blinded_targets != null and len(blinded_targets) > 0)"
     " or (deafened_targets != null and len(deafened_targets) > 0)",
     _missing("blinded_targets", "Assign each recipient to blinded_targets or "
              "deafened_targets.")),
    ("spell != 'enhance_ability' or (strength_targets != null and len(strength_targets) > 0)"
     " or (dexterity_targets != null and len(dexterity_targets) > 0)"
     " or (intelligence_targets != null and len(intelligence_targets) > 0)"
     " or (wisdom_targets != null and len(wisdom_targets) > 0)"
     " or (charisma_targets != null and len(charisma_targets) > 0)",
     _missing("strength_targets", "Assign each recipient to one ability's targets list "
              "(strength_targets, dexterity_targets, intelligence_targets, wisdom_targets "
              "or charisma_targets).")),
)


def repair_begin_requires_resolution_inputs(runtime):
    """Issue 279: begin refuses a cast whose resolution would refuse for a missing input."""
    conditions = runtime["tools"]["begin_srd_spell_action"]["pre_conditions"]
    present = {c.get("error") for c in conditions if isinstance(c, dict)}
    for condition, error in BEGIN_REQUIREMENTS:
        if error not in present:
            conditions.append({"condition": condition, "error": error})


# Completion refusals that stay releasable (#291); each now names the input to correct.
NAMED_REFUSALS = {
    "The spell's damage-type selection does not match its package definition.":
        "selected_damage_type",
    "The selected damage type does not match this spell profile.": "selected_damage_type",
    "Each slot above seventh permits three total travelers, including the caster.": "targets",
    "Suggestion requires at most 25 words describing achievable activity without obvious "
    "harm to targets or allies.": "activity_words",
    "Mage Armor needs the target's validated Character File or saved NPC equipment ability "
    "profile.": "target",
    "Above-threshold damage needs authoritative Hit Point lifecycle and defense state.":
        "target",
    "Formation crossing an actual body needs the printed push owner.": "solid_wall_origin",
    "Choose a point on actual same-plane supporting ground.": "environment_origin",
}


def repair_refusals_name_their_input(runtime):
    """Issue 279: a refusal the GM must report names the input it concerns."""
    for tool in runtime["tools"].values():
        for owner in (tool.get("pre_conditions") or []), tool.get("effects") or []:
            for item in _walk(owner):
                name = NAMED_REFUSALS.get(item.get("error"))
                if name is not None:
                    item["error"] = f"{item['error']} Check input '{name}'."


WINDOW_REPAIRS = (
    repair_retained_spells_read_the_combat_scene,
    repair_begin_measures_listed_recipients,
    repair_refused_cast_can_be_released,
    repair_window_guards_name_the_release,
    repair_unmeasured_facts_have_safe_values,
    repair_resolving_cast_is_not_a_blocking_window,
    repair_true_strike_checks_its_weapon_range_at_begin,
    repair_begin_requires_resolution_inputs,
    repair_refusals_name_their_input,
)
