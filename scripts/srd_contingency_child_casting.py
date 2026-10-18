"""Retain genuine self-compatible family effects while setup owns both spell costs."""

from copy import deepcopy

if __package__:
    from .srd_actor_spell_modifiers import _call, _emit
    from .srd_contingency_completion import DRAIN, _emit_changed, normalized_tool
    from .srd_contingency_spell import CHILDREN, POOL, SOURCE_SHA256
    from .srd_control_behaviors import (
        _entity,
        _lasting,
        _param,
        _push,
        _require,
        _set,
        _tool,
        _walk,
        _when,
    )
    from .srd_extended_casting import INCAPACITATED
    from .srd_spell_detection_masks import CREATURE_TYPES as MASK_CREATURE_TYPES
    from .srd_timed_actor_defenses import _load_runtime
else:
    from srd_actor_spell_modifiers import _call, _emit
    from srd_contingency_completion import DRAIN, _emit_changed, normalized_tool
    from srd_contingency_spell import CHILDREN, POOL, SOURCE_SHA256
    from srd_control_behaviors import (
        _entity,
        _lasting,
        _param,
        _push,
        _require,
        _set,
        _tool,
        _walk,
        _when,
    )
    from srd_extended_casting import INCAPACITATED
    from srd_spell_detection_masks import CREATURE_TYPES as MASK_CREATURE_TYPES
    from srd_timed_actor_defenses import _load_runtime


DISPATCHES = {
    "timed_actor_defense": "cast_spell_timed_actor_defense",
    "timed_actor_modifier": "cast_spell_timed_actor_modifier",
    "timed_senses": "cast_spell_timed_senses",
    "spell_healing": "cast_spell_healing",
    "instantaneous_multi_target_healing": "cast_spell_instantaneous_multi_target_healing",
    "multi_target_timed_hit_point_bonus": "cast_spell_multi_target_timed_hit_point_bonus",
    "multi_target_timed_speed_bonus": "cast_spell_multi_target_timed_speed_bonus",
    "single_target_timed_condition_defense": "cast_spell_single_target_timed_condition_defense",
    "single_target_timed_death_prevention": "cast_spell_single_target_timed_death_prevention",
    "single_target_concentration_damage_resistance": (
        "cast_spell_single_target_concentration_damage_resistance"
    ),
    "multi_target_concentration_d20_modifier": "cast_spell_multi_target_concentration_d20_modifier",
    "multi_target_concentration_ability_advantage": (
        "cast_spell_multi_target_concentration_ability_advantage"
    ),
    "multi_target_timed_actor_mobility": "cast_spell_timed_actor_mobility",
    "information_spell": "cast_spell_information_spell",
    "recovery": "cast_srd_recovery",
    "illumination_spell": "cast_spell_illumination_spell",
    "illusion_image": "cast_srd_image_spell",
    "illusion_appearance": "cast_srd_appearance_spell",
    "detection_mask_spell": "apply_srd_detection_mask",
}
SELECTED = {
    "mage_armor": "timed_actor_defense",
    "blur": "timed_actor_defense",
    "heroism": "timed_actor_defense",
    "protection_from_evil_and_good": "timed_actor_defense",
    "haste": "timed_actor_modifier",
    "beacon_of_hope": "timed_actor_modifier",
    "freedom_of_movement": "timed_actor_modifier",
    "fire_shield": "timed_actor_modifier",
    "enlarge_reduce": "timed_actor_modifier",
    "darkvision": "timed_senses",
    "see_invisibility": "timed_senses",
    "comprehend_languages": "timed_senses",
    "cure_wounds": "spell_healing",
    "mass_cure_wounds": "instantaneous_multi_target_healing",
    "aid": "multi_target_timed_hit_point_bonus",
    "longstrider": "multi_target_timed_speed_bonus",
    "protection_from_poison": "single_target_timed_condition_defense",
    "death_ward": "single_target_timed_death_prevention",
    "protection_from_energy": "single_target_concentration_damage_resistance",
    "stoneskin": "single_target_concentration_damage_resistance",
    "bless": "multi_target_concentration_d20_modifier",
    "enhance_ability": "multi_target_concentration_ability_advantage",
    "fly": "multi_target_timed_actor_mobility",
    "spider_climb": "multi_target_timed_actor_mobility",
    "water_walk": "multi_target_timed_actor_mobility",
    "tongues": "information_spell",
    "speak_with_animals": "information_spell",
    "detect_magic": "information_spell",
    "detect_evil_and_good": "information_spell",
    "detect_poison_and_disease": "information_spell",
    "find_traps": "information_spell",
    "locate_animals_or_plants": "information_spell",
    "locate_creature": "information_spell",
    "locate_object": "information_spell",
    "remove_curse": "recovery",
    "greater_restoration": "recovery",
    "dispel_evil_and_good": "recovery",
    "invisibility": "illumination_spell",
    "greater_invisibility": "illumination_spell",
    "mirror_image": "illusion_image",
    "disguise_self": "illusion_appearance",
    "seeming": "illusion_appearance",
    "arcanists_magic_aura": "detection_mask_spell",
    "nondetection": "detection_mask_spell",
    "pass_without_trace": "detection_mask_spell",
}
CHOICE = "contingent_effect_choice"
IDS = "_contingent_child_ids"
PREPARED = "_contingent_prepared"
RELEASE = "release_srd_contingent_family_effect"
NO_EFFECT_DETAIL = "'No effect: ' + _contingent_no_effect_reason + '.'"
ABILITIES = ("strength", "dexterity", "intelligence", "wisdom", "charisma")
ENERGY_TYPES = ("acid", "cold", "fire", "lightning", "thunder")
SOURCE_PARAM = "contingency_source_id"
RECOVERY_ITEM = "contingent_recovery_item"
RECOVERY_SOURCE = "contingent_recovery_source_id"
INFORMATION_CHOICES = {
    "information_kind": "",
    "information_subject": "",
    "information_familiar": False,
    "information_seen_close": False,
}
APPEARANCE_SPECS = "appearance_specs"
MASK_TYPE = "mask_creature_type"
RETAINED_SELECTIONS = (
    RECOVERY_ITEM,
    RECOVERY_SOURCE,
    *INFORMATION_CHOICES,
    APPEARANCE_SPECS,
    MASK_TYPE,
)
NATIVE_PROFILE_RANGE = {
    "invisibility": 0,
    "greater_invisibility": 0,
    "mirror_image": 0,
    "arcanists_magic_aura": 5,
    "nondetection": 5,
    "pass_without_trace": 0,
}
RECOVERY_CHOICES = (
    "exhaustion",
    "charmed",
    "petrified",
    "curse",
    "maximum_hp",
    "constitution",
    "contact_other_plane",
    "befuddlement",
)


def _choice():
    return _param(
        CHOICE,
        "enum",
        enum_values=[
            "warm",
            "chill",
            "enlarge",
            "reduce",
            *ABILITIES,
            *ENERGY_TYPES,
            *RECOVERY_CHOICES,
        ],
        required=False,
        default="warm",
    )


def _selected(definitions):
    """Only source-admitted Action spells with an existing self-compatible effect owner qualify."""
    result = []
    for spell, family in SELECTED.items():
        if spell not in definitions:
            continue
        row = definitions[spell]
        if (
            row.get("family") != family
            or row.get("casting_time") != "action"
            or type(row.get("base_level")) is not int
            or not 1 <= row["base_level"] <= 5
            or not (
                row.get("range_kind") in {"self", "touch", "distance"}
                or (
                    spell in NATIVE_PROFILE_RANGE
                    and row.get("range_feet") == NATIVE_PROFILE_RANGE[spell]
                )
                or (
                    family == "instantaneous_multi_target_healing"
                    and row.get("targeting_mode") == "visible_point_sphere"
                    and type(row.get("range_feet")) is int
                    and row["range_feet"] > 0
                )
            )
        ):
            raise ValueError(f"{spell} changed its admitted contingent self-effect profile.")
        result.append(spell)
    return sorted(result)


def _ids(spells):
    return _set(IDS, [repr(spell) for spell in spells])


def _add_choice(tool):
    descriptors = [p for p in tool["parameters"] if p["name"] == CHOICE]
    original = _choice()
    original["enum_values"] = ["warm", "chill", "enlarge", "reduce"]
    previous = _choice()
    previous["enum_values"] = ["warm", "chill", "enlarge", "reduce", *ABILITIES, *ENERGY_TYPES]
    if descriptors and descriptors not in ([_choice()], [original], [previous]):
        raise ValueError("Retain the bounded source effect choice for the contingent spell.")
    if descriptors:
        descriptors[0]["enum_values"] = _choice()["enum_values"]
    else:
        tool["parameters"].append(_choice())
    for name in (RECOVERY_ITEM, RECOVERY_SOURCE):
        descriptor = _param(name, required=False, default="")
        existing = [p for p in tool["parameters"] if p["name"] == name]
        if existing and existing != [descriptor]:
            raise ValueError("Retain factual contingent recovery item/source identities.")
        if not existing:
            tool["parameters"].append(descriptor)
    descriptor = _param(
        APPEARANCE_SPECS,
        "array",
        required=False,
        default=[],
        max_items=64,
        items={
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "description": {"type": "string"},
                "height_offset_inches": {"type": "integer", "minimum": -12, "maximum": 12},
                "limb_arrangement": {"type": "string"},
                "willing": {"type": "boolean"},
            },
            "required": [
                "subject",
                "description",
                "height_offset_inches",
                "limb_arrangement",
                "willing",
            ],
            "additionalProperties": False,
        },
    )
    existing = [p for p in tool["parameters"] if p["name"] == APPEARANCE_SPECS]
    if existing and existing != [descriptor]:
        raise ValueError("Retain the native factual appearance descriptor.")
    if not existing:
        tool["parameters"].append(descriptor)
    for name, default in INFORMATION_CHOICES.items():
        descriptor = _param(
            name, "boolean" if type(default) is bool else "string", required=False, default=default
        )
        existing = [p for p in tool["parameters"] if p["name"] == name]
        if existing and existing != [descriptor]:
            raise ValueError("Retain the native factual information choices.")
        if not existing:
            tool["parameters"].append(descriptor)
    descriptor = _param(
        MASK_TYPE, "enum", required=False, default="humanoid", enum_values=MASK_CREATURE_TYPES
    )
    existing = [p for p in tool["parameters"] if p["name"] == MASK_TYPE]
    if existing and existing != [descriptor]:
        raise ValueError("Retain the actual Magic Aura creature-type choice.")
    if not existing:
        tool["parameters"].append(descriptor)


def _native_information_selection(tool):
    """Reuse the native locate choice requirements before either setup spell is paid."""
    condition = (
        "spell == 'locate_animals_or_plants' or spell == 'locate_object' or spell == "
        "'locate_creature'"
    )
    matches = [e for e in tool["effects"] if e.get("condition") == condition]
    if len(matches) != 1:
        raise ValueError("Retain the actual native locate selection requirements.")
    if any(
        set(n) - {"require", "condition", "error", "on_true", "on_false"} for n in _walk(matches)
    ):
        raise ValueError("Locate preparation may only reuse its native choice requirements.")
    return deepcopy(matches)


def _native_mask_selection(tool):
    """Reuse the actual willing-creature Mask selection before either setup cost is paid."""
    aura = [e for e in tool["effects"] if e.get("condition") == "spell == 'arcanists_magic_aura'"]
    if len(aura) != 2:
        raise ValueError("Retain Magic Aura's native creature selection and separate history.")
    choices = [
        e
        for e in aura
        if len(e.get("on_true", ())) == 1
        and e["on_true"][0].get("condition") == "_mask_subject_object == true"
    ]
    if len(choices) != 1:
        raise ValueError("Retain the native creature versus object Mask selection.")
    creature = choices[0]["on_true"][0]["on_false"]
    if [n.get("call") for n in creature if "call" in n] != ["resolve_srd_magical_creature_type"]:
        raise ValueError("Use the native physical type owner for the creature Mask.")
    if any(n.get("set", {}).get("field", "_mask_type") != "_mask_type" for n in creature):
        raise ValueError("Mask choice validation cannot install an effect before setup.")
    return [
        _call(
            "resolve_srd_detection_mask_subject",
            {"subject": "{_caster_state_id}"},
            ("_mask_scene", "_mask_scene_kind", "_mask_subject_actor", "_mask_subject_object"),
        ),
        _require(
            "_mask_scene_kind == 'entity' and _mask_subject_actor == _caster_state_id and "
            "_mask_subject_object != true",
            "The contingent mask affects only its actual self creature.",
        ),
        _when("spell == 'arcanists_magic_aura'", deepcopy(creature)),
    ]


def _prepare(tool, selected, *, native_curses=False, information_selection=(), mask_selection=()):
    """Extend actual preparation; no child effect, concentration or material spend occurs here."""
    _add_choice(tool)
    old = "spell == 'false_life' or spell == 'water_breathing'"
    guard = f"spell in {IDS}"
    tool["effects"] = [e for e in tool["effects"] if e.get("set", {}).get("field") != IDS]
    matches = [e for e in tool["effects"] if e.get("require", {}).get("condition") in {old, guard}]
    if len(matches) != 1:
        raise ValueError("Retained children need the actual unique preparation eligibility guard.")
    index = tool["effects"].index(matches[0])
    matches[0]["require"]["condition"] = guard
    tool["effects"].insert(index, _ids([*CHILDREN, *selected]))
    for node in tool["effects"]:
        require = node.get("require", {})
        if require.get("condition") == (
            "_spell_definition.base_level <= 5 and _spell_definition.concentration != true"
        ):
            require["condition"] = "_spell_definition.base_level <= 5"
            require["error"] = "The chosen spell must be at most level five."
        write = node.get("set", {})
        if write.get("field") == PREPARED:
            write["value"].update(
                caster_participant="_caster_id",
                family="_spell_definition.family",
                effect_choice=CHOICE,
                material_focus_kinds="_material_focus_kinds",
                material_waived="_material_waived",
                material_available="material_component_available",
                concentration="_contingent_concentration",
                recovery_item=RECOVERY_ITEM,
                recovery_source_id=RECOVERY_SOURCE,
                recovery_source_sequence="_contingent_recovery_source_sequence",
                **{name: name for name in INFORMATION_CHOICES},
                appearance_specs=APPEARANCE_SPECS,
                mask_creature_type=MASK_TYPE,
            )
    validations = [
        *deepcopy(information_selection),
        *(
            [_when("_spell_definition.family == 'detection_mask_spell'", deepcopy(mask_selection))]
            if mask_selection
            else []
        ),
        _when(
            "_spell_definition.family == 'illusion_appearance'",
            [
                _require(
                    "len(appearance_specs) == 1",
                    "Choose exactly one contingent self appearance.",
                ),
                _require(
                    "appearance_specs[0].subject == _caster_state_id and "
                    "appearance_specs[0].willing == true and appearance_specs[0].description != ''",
                    "The contingent appearance affects only its willing self caster.",
                ),
                _call(
                    "qualify_srd_disguise_anatomy",
                    {"subject_state": "{_caster_state_id}", "spec": "{appearance_specs[0]}"},
                ),
                _when(
                    "spell == 'seeming'",
                    [
                        _call(
                            "resolve_srd_illumination_target_sight",
                            {
                                "observer_state": "{_caster_state_id}",
                                "target_state": "{_caster_state_id}",
                            },
                            ("_illumination_target_can_see", "_illumination_point_distance"),
                        ),
                        _require(
                            "_illumination_target_can_see == true and "
                            "_illumination_point_distance <= 30",
                            "Seeming needs its actual self recipient seen within thirty feet.",
                        ),
                    ],
                ),
            ],
        ),
        _set("_contingent_concentration", False),
        _when(
            "_spell_definition.concentration == true",
            [_set("_contingent_concentration", True)],
        ),
        _require(
            "spell != 'enlarge_reduce' or "
            + CHOICE
            + " == 'enlarge' or "
            + CHOICE
            + " == 'reduce'",
            "Choose Enlarge or Reduce for that contingent spell.",
        ),
        _require(
            "spell != 'fire_shield' or " + CHOICE + " == 'warm' or " + CHOICE + " == 'chill'",
            "Choose the actual warm or chill Fire Shield.",
        ),
        _require(
            "spell != 'enhance_ability' or "
            + " or ".join(CHOICE + " == " + repr(ability) for ability in ABILITIES),
            "Choose a printed Enhance Ability ability; Constitution is not an option.",
        ),
        _require(
            "spell != 'protection_from_energy' or "
            + " or ".join(CHOICE + " == " + repr(kind) for kind in ENERGY_TYPES),
            "Choose a printed Protection from Energy damage type.",
        ),
        _require(
            "spell != 'greater_restoration' or " + CHOICE + " in _contingent_recovery_choices",
            "Choose the retained native Greater Restoration effect.",
        ),
        _require(
            RECOVERY_ITEM + " == '' or spell == 'remove_curse' or "
            "(spell == 'greater_restoration' and " + CHOICE + " == 'curse')",
            "Only a curse removal selects a cursed item.",
        ),
        _require(
            RECOVERY_SOURCE
            + " == '' or (spell == 'greater_restoration' and ("
            + CHOICE
            + " == 'curse' or "
            + CHOICE
            + " == 'befuddlement'))",
            "Only a selected Greater Restoration curse has a retained source identity.",
        ),
        _require(
            RECOVERY_ITEM + " == '' or " + RECOVERY_SOURCE + " == ''",
            "Select one cursed item or one creature curse source, not both.",
        ),
        _when(
            "_spell_definition.family == 'timed_actor_defense'",
            [
                _call(
                    "validate_srd_actor_defense_target",
                    {
                        "target_state": "{_caster_state_id}",
                        "spell": "{spell}",
                        "caster": "{_caster_id}",
                        "caster_state": "{_caster_state_id}",
                        "target_character": "{_caster_id}",
                    },
                )
            ],
        ),
        _when(
            "_spell_definition.family == 'timed_actor_modifier'",
            [
                _call(
                    "validate_srd_actor_modifier_target",
                    {
                        "target_state": "{_caster_state_id}",
                        "profile": "{_spell_definition.profile}",
                        "save_ability": "{_spell_definition.save_ability}",
                    },
                )
            ],
        ),
    ]
    validations.insert(
        0, _set("_contingent_recovery_choices", [repr(c) for c in (*ABILITIES, *RECOVERY_CHOICES)])
    )
    selected_native_curse = [
        _set("_contingent_native_curse", False),
    ]
    if native_curses:
        selected_native_curse += [
            _call(
                "read_srd_native_mummy_rot",
                {"source_id": "{" + RECOVERY_SOURCE + "}", "lasting_state": "{_lasting_state_id}"},
                {"_contingent_native_curse": "_native_mummy_rot"},
            )
        ]
    validations += [
        _set("_contingent_recovery_source_sequence", None),
        _when(
            RECOVERY_SOURCE + " != ''",
            [
                _set(
                    "_contingent_selected_source",
                    "lasting_state.timed_condition_sources[" + RECOVERY_SOURCE + "]",
                ),
                *selected_native_curse,
                _require(
                    "_contingent_selected_source != null and "
                    "_contingent_selected_source.installed == true and "
                    "_contingent_selected_source.target_state_id == _caster_state_id and "
                    "_contingent_selected_source.sequence != null",
                    "Select an actual current recovery source on the self caster.",
                ),
                _require(
                    "(" + CHOICE + " == 'curse' and ("
                    "_contingent_selected_source.control_profile == 'curse' or "
                    "_contingent_selected_source.control_profile == 'geas' or "
                    "_contingent_native_curse == true)) or "
                    "(" + CHOICE + " == 'befuddlement' and "
                    "_contingent_selected_source.spell_id == 'befuddlement')",
                    "Select the actual curse or Befuddlement source for that effect.",
                ),
                _set(
                    "_contingent_recovery_source_sequence", "_contingent_selected_source.sequence"
                ),
                _when(
                    "caster_state.concentration_effect_family == 'extended_casting' and "
                    "caster_state.extended_casting.spell == 'contingency'",
                    [
                        _require(
                            "caster_state.contingency_prepared.recovery_source_id == "
                            + RECOVERY_SOURCE
                            + " and "
                            "caster_state.contingency_prepared.recovery_source_sequence == "
                            "_contingent_recovery_source_sequence",
                            "Keep the actual recovery source selected when longer setup began.",
                        )
                    ],
                ),
            ],
        ),
    ]

    def on_caster(value):
        if isinstance(value, str):
            return value.replace("target_state", "caster_state").replace(
                "_target_state_id", "_caster_state_id"
            )
        if isinstance(value, list):
            return [on_caster(v) for v in value]
        if isinstance(value, dict):
            return {k: on_caster(v) for k, v in value.items()}
        return value

    validations.append(
        _when(
            "spell == 'greater_restoration' or " + RECOVERY_ITEM + " != ''",
            [
                _set(
                    "_contingency_child",
                    {
                        "effect_choice": CHOICE,
                        "recovery_item": RECOVERY_ITEM,
                        "recovery_source_id": RECOVERY_SOURCE,
                        "recovery_source_sequence": "_contingent_recovery_source_sequence",
                    },
                ),
                _set("_contingent_effect_eligible", True),
                *on_caster(_recovery_selection_eligibility(native_curses=native_curses)),
                _require(
                    "_contingent_effect_eligible == true",
                    "Select an actual current self recovery item or source before setup payment.",
                ),
            ],
        )
    )
    owned_selections = {
        RECOVERY_SOURCE + " != ''",
        "spell == 'greater_restoration' or " + RECOVERY_ITEM + " != ''",
        "_spell_definition.family == 'illusion_appearance'",
        "_spell_definition.family == 'detection_mask_spell'",
    }
    tool["effects"] = [
        e
        for e in tool["effects"]
        if e not in validations and e.get("condition") not in owned_selections
    ]
    end = next(
        i for i, e in enumerate(tool["effects"]) if e.get("set", {}).get("field") == PREPARED
    )
    tool["effects"][end:end] = validations


def _install(tool, selected, material):
    """Setup pays actual consumed child materials through the existing material owner."""
    old = (
        "_contingent_setup != null and _contingent_setup.caster == _caster_state_id and "
        "(_contingent_setup.spell == 'false_life' or "
        "_contingent_setup.spell == 'water_breathing') and "
        "_contingent_setup.slot_level >= 1 and _contingent_setup.slot_level <= 5"
    )
    guard = (
        "_contingent_setup != null and _contingent_setup.caster == _caster_state_id and "
        f"_contingent_setup.spell in {IDS} and "
        "_contingent_setup.slot_level >= 1 and _contingent_setup.slot_level <= 5"
    )
    tool["effects"] = [e for e in tool["effects"] if e.get("set", {}).get("field") != IDS]
    matches = [e for e in tool["effects"] if e.get("require", {}).get("condition") in {old, guard}]
    if len(matches) != 1:
        raise ValueError("Retained children need the unique committed setup witness.")
    matches[0]["require"]["condition"] = guard
    tool["effects"].insert(tool["effects"].index(matches[0]), _ids([*CHILDREN, *selected]))
    payment = deepcopy(material)
    payment["params"].update(
        spell="{_contingent_setup.spell}",
        permitted_focus_kinds="{_contingent_setup.material_focus_kinds}",
        item_ids="{_contingent_setup.material_item_ids}",
        materials_available="{_contingent_setup.material_available}",
        components_waived="{_contingent_setup.material_waived}",
        target_count=1,
        validate_only=False,
    )
    if "secure_area_final_consumption" in payment["params"]:
        payment["params"]["secure_area_final_consumption"] = False
    payment.pop("export_context", None)
    tool["effects"] = [e for e in tool["effects"] if e != payment]
    spend = next(
        i
        for i, e in enumerate(tool["effects"])
        if e.get("decrement", {}).get("field") == "caster_state.resources.{_cast_resource}"
    )
    tool["effects"].insert(spend, payment)


def extend_runtime(text):
    """Run after Contingency/Water emission, before Extended Casting captures choices."""
    runtime = _load_runtime(text)
    tools = runtime["tools"]
    selected = _selected(runtime["definitions"]["spells"])
    changed = {}
    prepare = deepcopy(tools["prepare_srd_contingent_child"])
    _prepare(
        prepare,
        selected,
        information_selection=_native_information_selection(tools[DISPATCHES["information_spell"]]),
    )
    changed["prepare_srd_contingent_child"] = prepare
    material = next(e for e in prepare["effects"] if e.get("call") == "resolve_srd_spell_materials")
    install = deepcopy(tools["install_srd_contingency_source"])
    _install(install, selected, material)
    changed["install_srd_contingency_source"] = install
    for name in ("cast_spell", "prepare_srd_contingency_cast"):
        tool = deepcopy(tools[name])
        _add_choice(tool)
        for p in tool["parameters"]:
            if p["name"] == "contingent_spell":
                p["enum_values"] = ["", *sorted({*CHILDREN, *selected})]
        for node in _walk(tool["effects"]):
            if node.get("call") in {"prepare_srd_contingency_cast", "prepare_srd_contingent_child"}:
                node["params"][CHOICE] = "{" + CHOICE + "}"
                for selection_name in RETAINED_SELECTIONS:
                    node["params"][selection_name] = "{" + selection_name + "}"
        changed[name] = tool
    return _emit(text, changed)


def bind_retained_choices(text):
    """Preserve the selected effect choice through genuine begin/continue/completion."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    selection = next(
        p for p in tools["cast_spell"]["parameters"] if p["name"] == "contingent_spell"
    )
    owners = {
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_spell_cast",
        "begin_srd_spell_cast_internal",
    } & tools.keys()
    for name in sorted(owners):
        tool = deepcopy(tools[name])
        if any(p["name"] == "contingent_spell" for p in tool["parameters"]):
            _add_choice(tool)
            for parameter in tool["parameters"]:
                if parameter["name"] == "contingent_spell":
                    if parameter["type"] != selection["type"]:
                        raise ValueError("Retain the actual contingent spell selector type.")
                    parameter["enum_values"] = deepcopy(selection["enum_values"])
        for node in _walk(tool["effects"]):
            write = node.get("set", {})
            if write.get("field") == "caster_state.extended_casting.contingency_choices":
                write["value"][CHOICE] = CHOICE
                write["value"].update(
                    {selection_name: selection_name for selection_name in RETAINED_SELECTIONS}
                )
            if node.get("call") == "prepare_srd_contingency_cast":
                node["params"][CHOICE] = "{" + CHOICE + "}"
                for selection_name in RETAINED_SELECTIONS:
                    node["params"][selection_name] = "{" + selection_name + "}"
            if node.get("call") in owners:
                node["params"][CHOICE] = "{" + CHOICE + "}"
                for selection_name in RETAINED_SELECTIONS:
                    node["params"][selection_name] = "{" + selection_name + "}"
            if node.get("condition") == "spell == 'contingency'" and any(
                child.get("require", {}).get("error") == "Keep the original Contingency choices."
                for child in node.get("on_true", [])
            ):
                check = _require(
                    "caster_state.extended_casting.contingency_choices." + CHOICE + " == " + CHOICE,
                    "Keep the original Contingency choices.",
                )
                if check not in node["on_true"]:
                    node["on_true"].append(check)
                for selection_name in RETAINED_SELECTIONS:
                    check = _require(
                        "caster_state.extended_casting.contingency_choices."
                        + selection_name
                        + " == "
                        + selection_name,
                        "Keep the original Contingency choices.",
                    )
                    if check not in node["on_true"]:
                        node["on_true"].append(check)
        if tool != tools[name]:
            changed[name] = tool
    return _emit_changed(text, changed)


def _dispatch(call):
    """Retain the actual family call and exports, with source-derived self-only bindings."""
    result = deepcopy(call)
    if result["call"] == "cast_srd_recovery" and result["params"].get("recovery_effect") == (
        "{recovery_effect}"
    ):
        recovery_spells = {spell for spell, family in SELECTED.items() if family == "recovery"}
        if recovery_spells - {"remove_curse", "greater_restoration", "dispel_evil_and_good"}:
            raise ValueError(
                "New recovery children need their genuine retained effect-group choice."
            )
        # These creature recovery children select no Guards and Wards Dispel effect group.
        result["params"]["recovery_effect"] = ""
    source_bindings = {
        "actual_spell_level": (
            {"{_slot_level}", "{actual_spell_level}"},
            "{_contingency_child.slot_level}",
        ),
        "spell_cast_id": (
            {"{_spell_source_cast_id}", "{spell_cast_id}"},
            "{_spell_source_cast_id}",
        ),
        "spell_cast_owner": (
            {"{_caster_state_id}", "{_spell_cast_owner_id}"},
            "{_target_state_id}",
        ),
    }
    for key, (allowed, retained) in source_bindings.items():
        if key not in result["params"]:
            continue
        if result["params"].get(key) not in allowed:
            raise ValueError(f"Retain the actual common Source forwarding for {key}.")
        result["params"][key] = retained
    replacements = {
        "{spell}": "{_contingency_child.spell}",
        "{_slot_level}": "{_contingency_child.slot_level}",
        "{_caster_id}": "{_contingency_child.caster_participant}",
        "{_caster_state_id}": "{_target_state_id}",
        "{_scalar_target_id}": "{_target_state_id}",
        "{_target_character_id}": "{_contingency_child.caster_participant}",
        "{_casting_profile.modifier}": "{_contingency_child.casting_profile.modifier}",
        "{_casting_profile.save_dc}": "{_contingency_child.casting_profile.save_dc}",
        "{_casting_profile.attack_bonus}": "{_contingency_child.casting_profile.attack_bonus}",
        "{_casting_profile.concentration_save_modifier}": (
            "{_contingency_child.casting_profile.concentration_save_modifier}"
        ),
        "{_casting_profile.ability}": "{_contingency_child.casting_profile.ability}",
        "{_casting_profile.scaling_level}": "{_contingency_child.casting_profile.scaling_level}",
        "{_defense_dispatch_targets}": "{_contingent_self_targets}",
        "{_sense_dispatch_targets}": "{_contingent_self_targets}",
        "{targets}": "{_contingent_self_targets}",
        "{_mobility_targets}": "{_contingent_self_targets}",
        "{_forego_save_targets}": [],
        "{_target_id}": "{_target_state_id}",
        "{_recovery_target}": "{_target_state_id}",
        "{_information_self_target_id}": "{_target_state_id}",
        "{_modifier_dispatch_targets}": "{_contingent_self_targets}",
        "{_modifier_forego_targets}": "{_contingent_self_targets}",
        "{actor_spell_choice}": "{_contingency_child.effect_choice}",
        "{actor_spell_weapon}": "",
        "{target_willing}": True,
        "{all_targets_willing}": True,
        "{_modifier_dispatch_willing}": True,
        "{_mobility_willing}": True,
        "{_caster_can_touch_target}": True,
        "{caster_can_touch_all_targets}": True,
        "{_modifier_dispatch_touch}": True,
        "{_mobility_touch}": True,
        "{caster_can_touch_target}": True,
        "{targets_max_distance}": 0,
        "{target_distance}": 0,
        "{_modifier_dispatch_distance}": 0,
        "{_mobility_distance}": 0,
        "{origin_distance}": 0,
        "{all_targets_in_area}": True,
        "{_cast_resource}": "no_slot",
        "{_spell_cover}": "none",
        "{all_targets_have_clear_path}": True,
        "{_mobility_clear_path}": True,
        "{caster_can_see_target}": True,
        "{caster_can_see_all_targets}": True,
        "{caster_can_see_origin}": True,
        "{origin_has_clear_path}": True,
        "{selected_damage_type}": "{_contingent_damage_type}",
        "{information_position}": [0, 0, 0],
        "{information_lighting}": None,
        "{information_sense}": "seeing",
        **{"{" + name + "}": "{_contingent_" + name + "}" for name in INFORMATION_CHOICES},
        "{information_obvious_place}": False,
        "{material_item_ids}": "{_contingency_child.material_item_ids}",
        "{calm_indifference_targets}": [],
        "{calm_indifferent_toward}": [],
        "{calm_allies}": [],
        "{recovery_choice}": "{_contingency_child.effect_choice}",
        "{recovery_item}": "{_contingency_child.recovery_item}",
        "{recovery_source_id}": "{_contingency_child.recovery_source_id}",
        "{recovery_position}": [],
        "{recovery_surface}": "",
        "{recovery_spoken_name}": "",
        "{illumination_object}": "",
        "{illumination_position}": [0, 0, 0],
        "{illumination_points}": [],
        "{illumination_combined}": False,
        "{illumination_color}": "white",
        "{illusion_position}": [0, 0, 0],
        "{illusion_size}": [5, 5, 5],
        "{illusion_lighting}": "bright",
        "{illusion_description}": "",
        "{illusion_minor_mode}": "image",
        "{illusion_subject_kind}": "unqualified",
        "{illusion_opaque}": True,
        "{illusion_sound}": "",
        "{illusion_sound_volume}": "normal",
        "{illusion_smell}": "",
        "{illusion_temperature}": "",
        "{illusion_trigger}": "",
        "{illusion_trigger_modality}": "visual",
        "{illusion_performance_seconds}": 300,
        "{phantasm_dangerous}": False,
        "{phantasm_damage_appearance}": "",
        "{project_image_location}": "",
        "{appearance_specs}": "{_contingency_child.appearance_specs}",
        "{appearance_position}": [0, 0, 0],
        "{appearance_cube_min}": [0, 0, 0],
        "{appearance_description}": "",
        "{appearance_sound}": "",
        "{appearance_smell}": "",
        "{appearance_terrain_kind}": "",
        "{appearance_tactile_kind}": "",
        "{script_surface}": "",
        "{script_original}": "",
        "{script_apparent}": "",
        "{script_language}": "",
        "{script_handwriting}": "",
        "{script_readers}": [],
        "{witness_state_ids}": [],
        "{mask_subject}": "{_target_state_id}",
        "{mask_willing}": True,
        "{mask_creature_type}": "{_contingency_child.mask_creature_type}",
        "{mask_chosen_creatures}": [],
        "{mask_object_aura}": "magical",
        "{mask_school}": "illusion",
        "{mask_end_description}": "",
        "{mask_end_after_rounds}": 0,
    }
    # The production final tail captures these same native choices in its owned dictionary.
    for name in (
        "spell",
        "material_item_ids",
        "information_position",
        "information_sense",
        "information_kind",
        "information_subject",
        "information_familiar",
        "information_seen_close",
        "information_obvious_place",
        "illumination_object",
        "illumination_position",
        "illumination_points",
        "illumination_combined",
        "illumination_color",
        "illusion_position",
        "illusion_size",
        "illusion_lighting",
        "illusion_description",
        "illusion_minor_mode",
        "illusion_subject_kind",
        "illusion_opaque",
        "illusion_sound",
        "illusion_sound_volume",
        "illusion_smell",
        "illusion_temperature",
        "illusion_trigger",
        "illusion_trigger_modality",
        "illusion_performance_seconds",
        "phantasm_dangerous",
        "phantasm_damage_appearance",
        "project_image_location",
        "appearance_specs",
        "appearance_position",
        "appearance_cube_min",
        "appearance_description",
        "appearance_sound",
        "appearance_smell",
        "appearance_terrain_kind",
        "appearance_tactile_kind",
        "script_surface",
        "script_original",
        "script_apparent",
        "script_language",
        "script_handwriting",
        "script_readers",
        "mask_subject",
        "mask_willing",
        "mask_creature_type",
        "mask_chosen_creatures",
        "mask_object_aura",
        "mask_school",
        "mask_end_description",
        "mask_end_after_rounds",
    ):
        replacements["{_retained_cast_tail_choices." + name + "}"] = deepcopy(
            replacements["{" + name + "}"]
        )
    for key, value in result["params"].items():
        if key in source_bindings:
            continue
        if isinstance(value, str) and value in replacements:
            result["params"][key] = deepcopy(replacements[value])
        elif isinstance(value, str) and value.startswith("{_spell_definition."):
            result["params"][key] = value.replace(
                "{_spell_definition.", "{_contingency_definition."
            )
        elif isinstance(value, str) and value in {
            "{_" + ability + "_targets}" for ability in ABILITIES
        }:
            result["params"][key] = "{_contingent_" + key + "}"
        elif value != "{_spell_source_cast_id}" and isinstance(value, str) and "{" in value:
            raise ValueError(f"Contingency has no genuine retained binding for {key}: {value}")
    return result


def _retained_family(tool):
    """Reuse native effects, replacing only continued preparation with the paid source witness."""
    result = deepcopy(tool)
    if any(p["name"] == "mask_subject" for p in result["parameters"]):
        initializers = [
            n
            for n in result["effects"]
            if n.get("set", {}).get("field") == "_mask_subject"
            and (
                n["set"]["value"] == "mask_subject"
                or (
                    n["set"]["value"] == "_caster_state_id"
                    and any(p["name"] == SOURCE_PARAM for p in result["parameters"])
                )
            )
        ]
        if len(initializers) != 1 and not (
            len(initializers) == 2
            and all(n["set"]["value"] == "_caster_state_id" for n in initializers)
            and any(p["name"] == SOURCE_PARAM for p in result["parameters"])
        ):
            raise ValueError("Retain the native mask's exact subject initializer.")
        # The private copy is self-only; use its authenticated ID without parameter auto-resolution.
        initializers[0]["set"]["value"] = "_caster_state_id"
    completion_guard = normalized_tool(
        "Owning completion",
        [],
        [
            _when(
                "state.contingency_release_queue != null and "
                "len(state.contingency_release_queue) > 0",
                [_call(DRAIN, {})],
            )
        ],
    )["effects"][0]

    def omit_outer_completion(sequence):
        sequence[:] = [node for node in sequence if node != completion_guard]
        for node in sequence:
            for branch in ("on_true", "on_false"):
                if branch in node:
                    omit_outer_completion(node[branch])
            if "for_each" in node:
                omit_outer_completion(node["for_each"]["do"])

    # A previously generated parent may already have its owned outer drain.
    # The retained copy is inside that drain and must leave completion to it.
    omit_outer_completion(result["effects"])
    descriptor = _param(SOURCE_PARAM, model_hidden=True)
    if descriptor not in result["parameters"]:
        if any(p["name"] == SOURCE_PARAM for p in result["parameters"]):
            raise ValueError("Retain the private Contingency source identity parameter.")
        result["parameters"].append(descriptor)
    if not any(p["name"] == "lasting_state" for p in result["parameters"]):
        result["parameters"].append(_lasting())
    conditions = result.setdefault("pre_conditions", [])
    prepared = [p for p in conditions if "prepared_spells" in p.get("condition", "")]
    if len(prepared) > 1 or any(
        p["condition"]
        not in {
            "spell in caster_state.prepared_spells",
            "spell in caster_state.prepared_spells and slot_level >= base_level",
        }
        for p in prepared
    ):
        raise ValueError("Retain the actual known native preparation gate for deferred casting.")
    result["pre_conditions"] = [p for p in conditions if p not in prepared]
    for gate in prepared:
        if gate["condition"].endswith(" and slot_level >= base_level"):
            result["pre_conditions"].append(dict(gate, condition="slot_level >= base_level"))
    source = f"lasting_state.{POOL}[{SOURCE_PARAM}]"
    guards = [
        {
            "condition": f"{source} != null and {source}.status == 'released' and "
            f"{source}.caster == _caster_state_id and {source}.source_sha256 == "
            + repr(SOURCE_SHA256),
            "error": "Use the genuine consumed Contingency and its self caster.",
        },
        {
            "condition": f"{source}.child_release_completed == true and "
            f"{source}.child.spell == spell and {source}.child.slot_level == slot_level",
            "error": "Use that already-paid chosen spell once at its actual release.",
        },
    ]
    # Information's effect owner has no slot parameter; its paid level is in the parent.
    if not any(p["name"] == "slot_level" for p in result["parameters"]):
        guards[1]["condition"] = (
            f"{source}.child_release_completed == true and {source}.child.spell == spell"
        )
    result["pre_conditions"] = guards + [p for p in result["pre_conditions"] if p not in guards]
    return result


def _self_scene_eligibility():
    """Derive actual self geometry before a deferred effect can fail its required scene lookup."""
    return [
        _set("_contingent_scene_subject", "_target_state_id"),
        _when(
            "lasting_state.has_magic_jar_sources == true",
            [
                _call(
                    "resolve_srd_magic_jar_scene_subject",
                    {"subject": "{_target_state_id}"},
                    ("_magic_jar_scene_id",),
                ),
                _set("_contingent_scene_subject", "_magic_jar_scene_id"),
            ],
        ),
        _set("_contingent_scene_matches", 0),
        _set("_contingent_self_scene", None),
        _when(
            "lasting_state.spatial_scene != null and "
            "lasting_state.spatial_scene.map.grid.unit == 'ft'",
            [
                _set("_contingent_scene_entities", "lasting_state.spatial_scene.entities"),
                {
                    "for_each": {
                        "collection": "_contingent_scene_entities",
                        "as": "_contingent_scene_item",
                        "do": [
                            _when(
                                "_contingent_scene_item.active == true and "
                                "(_contingent_scene_item.id == _contingent_scene_subject or "
                                "_contingent_scene_item.metadata.participant_ref == "
                                "_contingent_scene_subject)",
                                [
                                    _set(
                                        "_contingent_scene_matches", "_contingent_scene_matches + 1"
                                    ),
                                    _set("_contingent_self_scene", "_contingent_scene_item"),
                                ],
                            )
                        ],
                    }
                },
            ],
        ),
        _when(
            "_contingent_scene_matches != 1 or _contingent_self_scene.metadata.plane == null",
            [
                _set("_contingent_effect_eligible", False),
                _set("_contingent_no_effect_reason", "'no unique current self scene and plane'"),
            ],
        ),
    ]


def _recovery_selection_eligibility(*, native_curses=False):
    """Check retained factual selections before the native recovery owner's strict selectors."""
    native_selection = [_set("_contingent_native_curse", False)]
    if native_curses:
        native_selection += [
            _call(
                "read_srd_native_mummy_rot",
                {"source_id": "{_contingent_recovery_id}", "lasting_state": "{_lasting_state_id}"},
                {"_contingent_native_curse": "_native_mummy_rot"},
            )
        ]
    result = [
        _set("_contingent_recovery_ids", []),
        _set("_contingent_selected_curses", []),
        _set("_contingent_selected_befuddlement", []),
        _when(
            "target_state.timed_condition_source_ids != null",
            [_set("_contingent_recovery_ids", "target_state.timed_condition_source_ids")],
        ),
        {
            "for_each": {
                "collection": "_contingent_recovery_ids",
                "as": "_contingent_recovery_id",
                "do": [
                    _set(
                        "_contingent_recovery_source",
                        "lasting_state.timed_condition_sources[_contingent_recovery_id]",
                    ),
                    *native_selection,
                    _when(
                        "_contingent_recovery_source != null and "
                        "_contingent_recovery_source.installed == true",
                        [
                            _when(
                                "_contingency_child.effect_choice == 'curse' and "
                                "_contingent_recovery_source.native_profile == 'mummy_rot' and "
                                "_contingent_recovery_source.native_body_source_sequence == "
                                "_contingent_recovery_source.sequence",
                                [
                                    _set("_contingent_effect_eligible", False),
                                    _set(
                                        "_contingent_no_effect_reason",
                                        "'native Mummy curse selection needs its late owner join'",
                                    ),
                                ],
                            ),
                            _when(
                                "_contingent_recovery_source.control_profile == 'curse' or "
                                "_contingent_recovery_source.control_profile == 'geas' or "
                                "_contingent_native_curse == true",
                                [_push("_contingent_selected_curses", "_contingent_recovery_id")],
                            ),
                            _when(
                                "_contingent_recovery_source.spell_id == 'befuddlement'",
                                [
                                    _push(
                                        "_contingent_selected_befuddlement",
                                        "_contingent_recovery_id",
                                    )
                                ],
                            ),
                        ],
                    ),
                ],
            }
        },
        _when(
            "_contingency_child.effect_choice == 'maximum_hp' and "
            "target_state.native_undead_max_hp_reduction_receipts != null and "
            "len(target_state.native_undead_max_hp_reduction_receipts) > 0",
            [
                _set("_contingent_effect_eligible", False),
                _set(
                    "_contingent_no_effect_reason",
                    "'native undead maximum-HP restoration needs its late owner join'",
                ),
            ],
        ),
        _when(
            "_contingency_child.recovery_source_id != ''",
            [
                _set(
                    "_contingent_selected_source",
                    "lasting_state.timed_condition_sources[_contingency_child.recovery_source_id]",
                ),
                _when(
                    "_contingent_selected_source == null or "
                    "_contingent_selected_source.installed != true or "
                    "_contingent_selected_source.target_state_id != _target_state_id or "
                    "_contingent_selected_source.sequence != "
                    "_contingency_child.recovery_source_sequence",
                    [
                        _set("_contingent_effect_eligible", False),
                        _set(
                            "_contingent_no_effect_reason",
                            "'the saved recovery source has ended or changed'",
                        ),
                    ],
                ),
            ],
        ),
        _when(
            "_contingency_child.effect_choice == 'curse' and "
            "_contingency_child.recovery_item == '' and "
            "len(_contingent_selected_curses) > 0 and not ("
            "_contingency_child.recovery_source_id in _contingent_selected_curses or "
            "(_contingency_child.recovery_source_id == '' and "
            "len(_contingent_selected_curses) == 1))",
            [
                _set("_contingent_effect_eligible", False),
                _set("_contingent_no_effect_reason", "'the saved curse selection is not current'"),
            ],
        ),
        _when(
            "_contingency_child.effect_choice == 'befuddlement' and not ("
            "_contingency_child.recovery_source_id in _contingent_selected_befuddlement or "
            "(_contingency_child.recovery_source_id == '' and "
            "len(_contingent_selected_befuddlement) <= 1))",
            [
                _set("_contingent_effect_eligible", False),
                _set(
                    "_contingent_no_effect_reason",
                    "'the saved Befuddlement selection is not current'",
                ),
            ],
        ),
        _when(
            "_contingency_child.recovery_item != ''",
            [
                _set("_contingent_cursed_items", []),
                _set("_contingent_cursed_item_found", False),
                _when(
                    "target_state.cursed_items != null",
                    [_set("_contingent_cursed_items", "target_state.cursed_items")],
                ),
                {
                    "for_each": {
                        "collection": "_contingent_cursed_items",
                        "as": "_contingent_cursed_item",
                        "do": [
                            _when(
                                "_contingent_cursed_item.item == "
                                "_contingency_child.recovery_item and "
                                "target_state.inventory != null and "
                                "_contingency_child.recovery_item in target_state.inventory",
                                [_set("_contingent_cursed_item_found", True)],
                            )
                        ],
                    }
                },
                _when(
                    "_contingent_cursed_item_found != true",
                    [
                        _set("_contingent_effect_eligible", False),
                        _set(
                            "_contingent_no_effect_reason",
                            "'the saved cursed item is no longer carried'",
                        ),
                    ],
                ),
            ],
        ),
    ]
    if native_curses:
        result = [
            n
            for n in result
            if "target_state.native_undead_max_hp_reduction_receipts" not in n.get("condition", "")
        ]
        for node in _walk(result):
            if "on_true" in node:
                node["on_true"] = [
                    n
                    for n in node["on_true"]
                    if "_contingent_recovery_source.native_profile == 'mummy_rot'"
                    not in n.get("condition", "")
                ]
    return result


def _appearance_eligibility():
    """A changed or unqualified body consumes the saved disguise with explicit no effect."""
    return [
        _set("_contingent_appearance_valid", False),
        _when(
            "_contingency_child.appearance_specs != null and "
            "len(_contingency_child.appearance_specs) == 1",
            [
                _set("_contingent_spec", "_contingency_child.appearance_specs[0]"),
                _set(
                    "_contingent_appearance_valid",
                    "_contingent_spec.subject == _target_state_id and "
                    "_contingent_spec.willing == true and _contingent_spec.description != '' and "
                    "_contingent_self_scene.metadata.basic_limb_arrangement != null and "
                    "_contingent_spec.limb_arrangement == "
                    "_contingent_self_scene.metadata.basic_limb_arrangement",
                ),
                _when(
                    "target_state.form_id != null and target_state.form_id != '' and "
                    "_contingent_self_scene.metadata.anatomy_form_id != target_state.form_id",
                    [_set("_contingent_appearance_valid", False)],
                ),
                _when(
                    "_contingent_self_scene.height + "
                    "_contingent_spec.height_offset_inches * 12 ** -1 <= 0",
                    [_set("_contingent_appearance_valid", False)],
                ),
                _when(
                    "_contingency_child.spell == 'seeming' and "
                    "_contingent_appearance_valid == true",
                    [
                        _call(
                            "resolve_srd_illumination_target_sight",
                            {
                                "observer_state": "{_target_state_id}",
                                "target_state": "{_target_state_id}",
                            },
                            ("_illumination_target_can_see", "_illumination_point_distance"),
                        ),
                        _set(
                            "_contingent_appearance_valid",
                            "_illumination_target_can_see == true and "
                            "_illumination_point_distance <= 30",
                        ),
                    ],
                ),
            ],
        ),
        _when(
            "_contingent_appearance_valid != true",
            [
                _set("_contingent_effect_eligible", False),
                _set(
                    "_contingent_no_effect_reason",
                    "'the saved self appearance no longer fits the body'",
                ),
            ],
        ),
    ]


def _mask_eligibility():
    """An absent self or changed native type consumes the mask without a strict-leaf rollback."""
    return [
        _set(
            "_contingent_mask_valid",
            "_contingent_self_scene.metadata.participant_ref == _target_state_id and "
            "_contingent_self_scene.metadata.is_object != true",
        ),
        _when(
            "_contingency_child.spell == 'arcanists_magic_aura'",
            [
                _set(
                    "_contingent_mask_valid",
                    "_contingent_mask_valid == true and target_state.creature_type != null and "
                    "_contingency_child.mask_creature_type != target_state.creature_type and "
                    "(_contingent_self_scene.metadata.creature_type == null or "
                    "_contingent_self_scene.metadata.creature_type == target_state.creature_type)",
                ),
            ],
        ),
        _when(
            "_contingent_mask_valid != true",
            [
                _set("_contingent_effect_eligible", False),
                _set(
                    "_contingent_no_effect_reason", "'the saved creature mask no longer fits self'"
                ),
            ],
        ),
    ]


def _actual_dispatch(tools, family):
    """Resolve the unique native call before or after retained-tail factoring."""
    name = DISPATCHES[family]
    if not isinstance(tools.get(name), dict):
        raise ValueError(f"Retain the actual native dispatch provider for {family}.")
    cast = tools.get("cast_spell")
    if not isinstance(cast, dict) or not isinstance(cast.get("effects"), list):
        raise ValueError(f"Retain the actual paid-cast dispatch provider for {family}.")
    final_tail = "finish_srd_retained_cast_tail"
    tail_families = {
        "timed_senses",
        "information_spell",
        "illumination_spell",
        "illusion_image",
        "illusion_appearance",
        "detection_mask_spell",
    }
    owner = cast
    links = [n for n in _walk(cast["effects"]) if n.get("call") == final_tail]
    if family in tail_families and (final_tail in tools or links):
        tail = tools.get(final_tail)
        if not isinstance(tail, dict):
            raise ValueError(
                f"Unsupported final-layout fixture for {family}: missing {final_tail}."
            )
        if (
            len(links) != 1
            or links[0].get("params", {}).get("_retained_cast_tail_choices")
            != "{_retained_cast_tail_choices}"
            or tail.get("metadata", {}).get("retained_spell_reactions_v1") is not True
            or any(n.get("call") == name for n in _walk(cast["effects"]))
        ):
            raise ValueError(f"Retain the actual final paid-cast tail for {family}.")
        owner = tail
    matches = [n for n in _walk(owner["effects"]) if n.get("call") == name]
    if family == "recovery":
        matches = [n for n in matches if "target_state" in n.get("params", {})]
    if len(matches) != 1:
        raise ValueError(f"Retain exactly one actual {family} dispatch for Contingency.")
    return matches[0]


def bind_release(text):
    """Bind after final native Recovery/Source owners, before Jar/receivers/outer capture."""
    runtime = _load_runtime(text)
    tools = runtime["tools"]
    selected = _selected(runtime["definitions"]["spells"])
    calls = {}
    changed = {}
    native_curses = "read_srd_native_mummy_rot" in tools
    preparation = deepcopy(tools["prepare_srd_contingent_child"])
    _prepare(
        preparation,
        selected,
        native_curses=native_curses,
        information_selection=_native_information_selection(tools[DISPATCHES["information_spell"]]),
        mask_selection=_native_mask_selection(tools[DISPATCHES["detection_mask_spell"]]),
    )
    # Keep resolved parameters last, matching the actual loader's field order.
    preparation["parameters"] = preparation.pop("parameters")
    changed["prepare_srd_contingent_child"] = preparation
    for family, name in DISPATCHES.items():
        calls[family] = _dispatch(_actual_dispatch(tools, family))
        if family not in {"timed_actor_defense", "timed_actor_modifier", "timed_senses"}:
            retained_name = "release_srd_contingent_" + family
            changed[retained_name] = _retained_family(tools[name])
            calls[family]["call"] = retained_name
            calls[family]["params"][SOURCE_PARAM] = "{source_id}"
    prefix = f"lasting_state.{POOL}.{{source_id}}"
    dispatch = [
        _call(
            "begin_srd_spell_source_instance",
            {
                "caster_state": "{_target_state_id}",
                "spell": "{_contingency_child.spell}",
                "slot_level": "{_contingency_child.slot_level}",
            },
            ("_spell_source_cast_id",),
        ),
        _set(prefix + ".child_cast_id", "_spell_source_cast_id"),
        *[
            _when("_contingency_child.family == " + repr(family), [call])
            for family, call in calls.items()
        ],
    ]
    selections = [
        _set("_contingent_damage_type", None),
        _when(
            "_contingency_child.spell == 'protection_from_energy'",
            [_set("_contingent_damage_type", "_contingency_child.effect_choice")],
        ),
    ]
    for name, default in INFORMATION_CHOICES.items():
        selections += [
            _set("_contingent_" + name, repr(default) if isinstance(default, str) else default),
            _when(
                "_contingency_child." + name + " != null",
                [_set("_contingent_" + name, "_contingency_child." + name)],
            ),
        ]
    for ability in ABILITIES:
        selections += [
            _set("_contingent_" + ability + "_targets", []),
            _when(
                "_contingency_child.spell == 'enhance_ability' and "
                "_contingency_child.effect_choice == " + repr(ability),
                [_set("_contingent_" + ability + "_targets", ["_target_state_id"])],
            ),
        ]
    effect = _tool(
        "Release the genuinely paid retained family effect on its caster without another cost.",
        [_entity("target_state"), _param("source_id", model_hidden=True), _lasting()],
        [
            _set("_contingency_record", f"lasting_state.{POOL}[source_id]"),
            _require(
                "_contingency_record != null and _contingency_record.status == 'released' and "
                "_contingency_record.caster == _target_state_id and "
                "_contingency_record.source_sha256 == " + repr(SOURCE_SHA256),
                "Use the actual consumed Contingency source and its recorded self caster.",
            ),
            _when(
                "_contingency_record.child_release_completed != true",
                [
                    _set("_contingency_child", "_contingency_record.child"),
                    _set(prefix + ".child_release_completed", True),
                    _set("_contingent_self_targets", ["_target_state_id"]),
                    {
                        "table_lookup": {
                            "table": "definitions.spells",
                            "key": "_contingency_child.spell",
                            "required": True,
                        },
                        "store_as": "_contingency_definition",
                    },
                    *selections,
                    _set("_nested_spell_summary_rows", []),
                    _set("_nested_spell_detail_rows", []),
                    _set("_contingent_effect_eligible", True),
                    _set("_contingent_no_effect_reason", None),
                    _when(
                        "_contingency_child.family == 'recovery' and "
                        "(_contingency_child.spell == 'greater_restoration' or "
                        "_contingency_child.recovery_item != '')",
                        _recovery_selection_eligibility(native_curses=native_curses),
                    ),
                    _when(
                        "_contingency_child.spell == 'mage_armor' and "
                        "target_state.worn_armor != null",
                        [
                            _set("_contingent_effect_eligible", False),
                            _set("_contingent_no_effect_reason", "'wearing armor'"),
                        ],
                    ),
                    _when(
                        "_contingency_child.concentration == true and not ("
                        + INCAPACITATED.replace("caster_state", "target_state")
                        + ")",
                        [
                            _set("_contingent_effect_eligible", False),
                            _set("_contingent_no_effect_reason", "'cannot concentrate'"),
                        ],
                    ),
                    _when(
                        "(_contingency_child.family == 'information_spell' and "
                        "_contingency_definition.information_mode != 'language') or "
                        "_contingency_child.family == 'illumination_spell' or "
                        "_contingency_child.family == 'illusion_image' or "
                        "_contingency_child.family == 'illusion_appearance' or "
                        "_contingency_child.family == 'detection_mask_spell' or "
                        "_contingency_child.spell == 'mass_cure_wounds'",
                        _self_scene_eligibility(),
                    ),
                    _when(
                        "_contingency_child.family == 'illusion_appearance' and "
                        "_contingent_effect_eligible == true",
                        _appearance_eligibility(),
                    ),
                    _when(
                        "_contingency_child.family == 'detection_mask_spell' and "
                        "_contingent_effect_eligible == true",
                        _mask_eligibility(),
                    ),
                    _when(
                        "_contingency_child.spell == 'mass_cure_wounds' and "
                        "_contingent_effect_eligible == true",
                        [
                            _call(
                                "resolve_srd_illumination_point_sight",
                                {
                                    "observer_state": "{_target_state_id}",
                                    "point": "{_contingent_self_scene.position}",
                                    "perception_plane": "{_contingent_self_scene.metadata.plane}",
                                },
                                ("_illumination_point_can_see",),
                            ),
                            _when(
                                "_illumination_point_can_see != true",
                                [
                                    _set("_contingent_effect_eligible", False),
                                    _set(
                                        "_contingent_no_effect_reason",
                                        "'cannot see the self-centered healing point'",
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _when(
                        "_contingent_effect_eligible == true",
                        [
                            _set(prefix + ".child_effect_applied", True),
                            _set(prefix + ".child_status", "'applied'"),
                            *dispatch,
                        ],
                        [
                            _set(prefix + ".child_status", "'no_effect'"),
                            _set(prefix + ".child_effect_applied", False),
                            _set(
                                prefix + ".child_no_effect_reason", "_contingent_no_effect_reason"
                            ),
                            _set(
                                "_nested_spell_summary_rows",
                                [
                                    {
                                        "label": "'Contingent spell'",
                                        "value": NO_EFFECT_DETAIL,
                                    }
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    child = deepcopy(tools["release_srd_contingent_spell"])
    child["parameters"] = child.pop("parameters")
    alive = [
        n for n in _walk(child["effects"]) if n.get("condition") == "target_state.is_alive == true"
    ]
    if len(alive) != 1:
        raise ValueError("Retain the actual once-consumed child release/self lifetime boundary.")
    branch = _when(
        f"_contingency_child.spell in {IDS}",
        [
            _call(
                RELEASE,
                {"target_state": "{_target_state_id}", "source_id": "{source_id}"},
                {
                    "_contingent_summary_rows": "_nested_spell_summary_rows",
                    "_contingent_detail_rows": "_nested_spell_detail_rows",
                    "_contingent_effect_eligible": "_contingent_effect_eligible",
                    "_contingent_no_effect_reason": "_contingent_no_effect_reason",
                },
            ),
            _when(
                "_contingent_effect_eligible == true",
                [
                    _set(
                        "_spell_effect_summary", "'Contingent family spell released on its caster.'"
                    )
                ],
                [
                    _set(
                        "_spell_effect_summary",
                        "'Contingent spell has no effect: ' + _contingent_no_effect_reason + '.'",
                    )
                ],
            ),
        ],
    )
    alive[0]["on_true"] = [
        n
        for n in alive[0]["on_true"]
        if n.get("condition") != branch["condition"] and n.get("set", {}).get("field") != IDS
    ]
    alive[0]["on_true"] += [_ids(selected), branch]
    changed.update({RELEASE: effect, "release_srd_contingent_spell": child})
    return _emit(text, changed)
