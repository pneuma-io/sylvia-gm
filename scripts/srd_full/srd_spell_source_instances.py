"""Identify actual time-span casts without inventing a second effect registry."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_control_behaviors import (
        _call,
        _entity,
        _lasting,
        _param,
        _set,
        _tool,
        _walk,
        _when,
    )
    from .srd_timed_senses import _replace_tools
else:
    from srd_control_behaviors import (
        _call,
        _entity,
        _lasting,
        _param,
        _set,
        _tool,
        _walk,
        _when,
    )
    from srd_timed_senses import _replace_tools


# These installers own every ongoing effect of these families. Other owners can
# adopt the same identity only after their exact retirement path is available.
SUPPORTED_FAMILIES = {
    "earthquake_spell",
    "contingency_spell",
    "move_earth",
    "storm_of_vengeance",
    "reverse_gravity",
    "animate_objects",
    "arcane_hand",
    "delayed_blast_fireball",
    "find_path",
    "solid_spell_wall",
    "sunbeam",
    "gust_of_wind",
    "phantom_steed",
    "meld_stone_spell",
    "attack_rider_spell",
    "dragon_breath",
    "retained_spell_attack",
    "multi_target_timed_control",
    "multi_target_timed_repeating_save_condition",
    "single_target_timed_armor_class_floor",
    "multi_target_timed_hit_point_bonus",
    "single_target_timed_death_prevention",
    "multi_target_timed_speed_bonus",
    "timed_actor_defense",
    "timed_senses",
    "multi_target_timed_actor_mobility",
    "illusion_spell",
    "minor_world_spell",
    "memory_dialogue_spell",
    "illusion_appearance",
    "illusion_image",
    "supply_spell",
    "heat_metal_spell",
    "spell_barrier",
    "control_binding_spell",
    "detection_mask_spell",
    "dream_spell",
    "spell_secure_area",
    "mirage_spell",
}
SPECIFIC_DURATION_OWNERS = {
    "conjure_celestial": "install_srd_conjured_presence",
    "conjure_fey": "install_srd_conjured_presence",
    "gate": "install_srd_gate",
    "phantom_steed": "cast_srd_phantom_steed",
    "prismatic_wall": "cast_srd_prismatic_wall",
    "rope_trick": "install_srd_rope_trick",
    "magnificent_mansion": "install_srd_magnificent_mansion",
    "mage_hand": "cast_srd_mage_hand",
    "floating_disk": "cast_srd_floating_disk",
    "feather_fall": "install_srd_feather_fall",
    "guiding_bolt": "install_srd_turn_effect",
    "etherealness": "apply_srd_etherealness_target",
    "blink": "apply_srd_blink_target",
    "instant_summons": "cast_spell_object_utility_spell",
    "levitate": "install_srd_levitate",
    "passwall": "create_srd_passwall_source",
    "sleep": "cast_srd_control_behavior",
    "eyebite": "cast_srd_control_afflictions",
    "telekinesis": "install_srd_telekinesis",
    "guards_and_wards": "cast_srd_guards_wards",
}
UNTIL_DISPELLED_OWNERS = {
    "secret_chest": "install_srd_secret_chest",
    "instant_summons": "cast_spell_object_utility_spell",
    "hallow": "cast_srd_hallow",
}
INSTALLERS = {
    "cast_srd_earthquake",
    "install_srd_contingency_source",
    "install_srd_conjured_presence",
    "cast_srd_stone_spell_wall",
    "install_srd_gate",
    "cast_srd_solid_spell_wall",
    "install_srd_sunbeam",
    "cast_srd_gust_of_wind",
    "cast_srd_phantom_steed",
    "cast_srd_prismatic_wall",
    "cast_srd_meld_into_stone",
    "install_srd_secret_chest",
    "install_srd_rope_trick",
    "install_srd_magnificent_mansion",
    "cast_srd_mage_hand",
    "cast_srd_floating_disk",
    "install_srd_turn_effect",
    "install_srd_timed_condition_source",
    "install_srd_timed_actor_defense",
    "install_srd_timed_senses",
    "install_srd_timed_actor_mobility",
    "install_srd_timed_hit_point_bonus",
    "install_srd_timed_armor_class_floor",
    "install_srd_timed_speed_bonus",
    "install_srd_timed_death_prevention",
    "apply_srd_etherealness_target",
    "cast_spell_illusion_spell",
    "cast_spell_minor_world_spell",
    "apply_srd_blink_target",
    "install_srd_appearance_source",
    "cast_srd_image_spell",
    "cast_srd_spell_barrier",
    "cast_spell_supply_spell",
    "cast_spell_object_utility_spell",
    "cast_spell_heat_metal_spell",
    "apply_srd_detection_mask",
    "cast_srd_dream_spell",
    "cast_srd_spell_secure_area",
    "install_srd_levitate",
    "create_srd_passwall_source",
    "cast_srd_mirage_arcane",
    "cast_srd_hallow",
    "install_srd_telekinesis",
    "install_srd_telekinesis_restraint",
    "cast_srd_guards_wards",
}
CONCENTRATION_STARTERS = {"start_srd_condition_concentration"}
CAST_ID = "spell_cast_id"
CAST_OWNER = "spell_cast_owner"


def supported_time_spans(requirements, definitions, *, tools=None):
    """Admit the source header and actual owning family together, not school labels."""
    return sorted(
        spell
        for spell, definition in definitions.items()
        if (
            definition.get("family") in SUPPORTED_FAMILIES
            or (
                spell in SPECIFIC_DURATION_OWNERS
                and (tools is None or SPECIFIC_DURATION_OWNERS[spell] in tools)
            )
        )
        and re.fullmatch(
            r"(?:Concentration,? up to |Up to )?\d+ (?:rounds?|minutes?|hours?|days?)",
            requirements[spell]["duration"],
        )
        or (
            spell in UNTIL_DISPELLED_OWNERS
            and UNTIL_DISPELLED_OWNERS[spell] in (tools or INSTALLERS)
            and requirements[spell]["duration"].lower() == "until dispelled"
        )
    )


def source_instance_tool():
    """A committed cast allocates one identity; saving/retrying reuses its context."""
    return _tool(
        "Allocate the identity of one actual committed time-span spell casting.",
        [
            _entity("caster_state"),
            _lasting(),
            _param("spell"),
            _param("slot_level", "integer", minimum=0, maximum=9),
        ],
        [
            _when(
                "lasting_state.spell_source_serial == null",
                [
                    _set("lasting_state.spell_source_serial", 0),
                ],
            ),
            {"increment": {"field": "lasting_state.spell_source_serial", "amount": 1}},
            _call(
                "bind_srd_spell_source_identity",
                {
                    "cast_id": "srd_cast:{lasting_state.spell_source_serial}",
                },
                ("_spell_source_cast_id",),
            ),
            _set("caster_state.last_spell_source_cast_id", "_spell_source_cast_id"),
            _set("caster_state.last_spell_source_spell", "spell"),
        ],
    )


def _call_paths(tools):
    """Only actual cast-to-installer paths receive the private source parameters."""
    graph = {
        name: {e["call"] for e in _walk(tool.get("effects", [])) if "call" in e}
        for name, tool in tools.items()
    }
    downstream = {"cast_spell"}
    while True:
        updated = downstream | set().union(*(graph.get(n, set()) for n in downstream))
        if updated == downstream:
            break
        downstream = updated
    upstream = (INSTALLERS | CONCENTRATION_STARTERS) & tools.keys()
    while True:
        updated = upstream | {n for n, calls in graph.items() if calls & upstream}
        if updated == upstream:
            break
        upstream = updated
    return downstream & upstream


def _stamp_installer(name, tool):
    if name == "install_srd_telekinesis_restraint":
        # This condition is an aspect of the actual Telekinesis pool source.
        # Its registry owner dispels both the ability and grip with one save.
        def bind_restraint(effects):
            retained = []
            for effect in effects:
                if effect.get("condition") == "caster_state.telekinesis_source_id != null" and (
                    "telekinesis_parent_source_id" in str(effect)
                ):
                    continue
                for branch in ("on_true", "on_false"):
                    if branch in effect:
                        effect[branch] = bind_restraint(effect[branch])
                retained.append(effect)
                if effect.get("call") == "install_srd_timed_condition_source":
                    retained.append(
                        _when(
                            "caster_state.telekinesis_source_id != null",
                            [
                                _set(
                                    "state.timed_condition_sources.{_timed_condition_source_id}"
                                    ".telekinesis_parent_source_id",
                                    "caster_state.telekinesis_source_id",
                                ),
                                _set(
                                    "state.timed_condition_sources.{_timed_condition_source_id}"
                                    ".dispel_ongoing",
                                    False,
                                ),
                            ],
                        )
                    )
            return retained

        tool["effects"] = bind_restraint(tool["effects"])
    if name in {
        "cast_srd_dream_spell",
        "cast_srd_spell_secure_area",
        "install_srd_levitate",
        "create_srd_passwall_source",
        "cast_srd_mirage_arcane",
        "cast_srd_hallow",
        "install_srd_telekinesis",
        "cast_srd_guards_wards",
    }:
        spell = (
            "spell"
            if name == "cast_srd_spell_secure_area"
            else repr(
                {
                    "cast_srd_dream_spell": "dream",
                    "install_srd_levitate": "levitate",
                    "create_srd_passwall_source": "passwall",
                    "cast_srd_mirage_arcane": "mirage_arcane",
                    "cast_srd_hallow": "hallow",
                    "install_srd_telekinesis": "telekinesis",
                    "cast_srd_guards_wards": "guards_and_wards",
                }[name]
            )
        )
        marker = "spell_cast_id != ''"
        owner = "_lifetime_source_cast_owner"
        tool["effects"] = [
            e
            for e in tool["effects"]
            if e.get("set", {}).get("field") != owner
            and not (e.get("condition") == "spell_cast_owner != null" and owner in str(e))
            and not (e.get("condition") == marker and "actual lifetime cast owner" in str(e))
        ]
        tool["effects"][:0] = [
            _when(
                marker,
                [
                    {
                        "require": {
                            "condition": "spell_cast_owner != null and _spell_cast_owner_id == "
                            "_caster_state_id and "
                            "caster_state.last_spell_source_cast_id == spell_cast_id "
                            "and caster_state.last_spell_source_spell == "
                            + spell
                            + " and actual_spell_level == slot_level",
                            "error": "Use this actual lifetime cast owner, spell and paid slot.",
                        }
                    }
                ],
            ),
        ]
        for node in _walk(tool.get("effects", [])):
            record = node.get("set", {}).get("value")
            field = node.get("set", {}).get("field", "")
            if not isinstance(record, dict):
                continue
            if (
                name == "cast_srd_dream_spell"
                and field.startswith(
                    ("state.dream_spell_sources.", "lasting_state.dream_spell_sources.")
                )
                or name == "cast_srd_spell_secure_area"
                and field.startswith(
                    ("state.spell_secure_area_sources.", "lasting_state.spell_secure_area_sources.")
                )
                or name == "install_srd_levitate"
                and field == "_levitate_source"
                or name == "create_srd_passwall_source"
                and field == "_pw_source"
                or name == "cast_srd_mirage_arcane"
                and field.startswith(("state.mirage_sources.", "lasting_state.mirage_sources."))
                or name == "cast_srd_hallow"
                and field.startswith(
                    ("state.spell_hallow_sources.", "lasting_state.spell_hallow_sources.")
                )
                or name == "install_srd_telekinesis"
                and field == "_tk_source"
                or name == "cast_srd_guards_wards"
                and field.startswith(
                    (
                        "state.spell_guards_wards_sources.",
                        "lasting_state.spell_guards_wards_sources.",
                    )
                )
            ):
                record.update(
                    cast_id=CAST_ID,
                    source_caster_id=None,
                    spell_level="slot_level",
                    dispel_ongoing=name != "cast_srd_guards_wards",
                    ongoing_duration=(
                        False
                        if name == "cast_srd_hallow"
                        else "spell_cast_id != '' and _guards_daily_count < 365"
                        if name == "cast_srd_guards_wards"
                        else "spell_cast_id != '' and _secure_permanent != true"
                        if name == "cast_srd_spell_secure_area"
                        else "spell_cast_id != ''"
                    ),
                )

        # A nested nullable context value becomes literal expression text in
        # this DSL. Keep real null in the constructor, then bind only a real
        # allocated owner at the actual record write.
        def bind_lifetime_owners(effects):
            retained = []
            for effect in effects:
                if (
                    effect.get("condition") == "spell_cast_owner != null and spell_cast_id != ''"
                    and "_lifetime_owner_patch" in str(effect)
                ):
                    continue
                for branch in ("on_true", "on_false"):
                    if branch in effect:
                        effect[branch] = bind_lifetime_owners(effect[branch])
                retained.append(effect)
                assigned = effect.get("set", {})
                field = assigned.get("field", "")
                record = assigned.get("value")
                if not isinstance(record, dict) or record.get("cast_id") != CAST_ID:
                    continue
                if not (
                    "dream_spell_sources." in field
                    or "spell_secure_area_sources." in field
                    or "mirage_sources." in field
                    or "spell_hallow_sources." in field
                    or "spell_guards_wards_sources." in field
                    or field in {"_levitate_source", "_pw_source", "_tk_source"}
                ):
                    continue
                binding = [
                    _set("_lifetime_owner_patch", {"source_caster_id": "_spell_cast_owner_id"}),
                ]
                binding.append(
                    _set(field, "merge(" + field + ", _lifetime_owner_patch)")
                    if field.startswith("_")
                    else _set(field + ".source_caster_id", "_spell_cast_owner_id")
                )
                retained.append(_when("spell_cast_owner != null and spell_cast_id != ''", binding))
            return retained

        tool["effects"] = bind_lifetime_owners(tool["effects"])
    if name in {"cast_spell_illusion_spell", "cast_spell_minor_world_spell"}:
        # Permanent images retain Dispel provenance but cannot be dismissed as
        # a finite time-span casting. Legacy private installs have no cast ID.
        marker = "spell_cast_owner != null"
        owner_field = (
            "_illusion_source_caster_id"
            if name == "cast_spell_illusion_spell"
            else "_minor_source_caster_id"
        )
        tool["effects"] = [
            e
            for e in tool["effects"]
            if e.get("set", {}).get("field") != owner_field
            and not (e.get("condition") == marker and owner_field in str(e))
        ]
        tool["effects"][:0] = [
            _set(owner_field, None),
            _when(marker, [_set(owner_field, "_spell_cast_owner_id")]),
        ]
    for node in _walk(tool.get("effects", [])):
        for operation in ("set", "list_push"):
            record = node.get(operation, {}).get("value")
            if isinstance(record, dict) and "spell" in record and "kind" in record:
                record["cast_id"] = CAST_ID
                record["source_caster_id"] = "_spell_cast_owner_id"
                record["ongoing_duration"] = "spell_cast_id != ''"
                if name == "cast_spell_minor_world_spell":
                    record["ongoing_duration"] = "spell_cast_id != '' and _minor_ongoing == true"
                    record["source_caster_id"] = "_minor_source_caster_id"
                if name == "install_srd_turn_effect":
                    record["spell_level"] = "actual_spell_level"
                if name == "cast_spell_object_utility_spell":
                    record["ongoing_duration"] = False
            if (
                name == "cast_spell_supply_spell"
                and operation == "set"
                and (
                    node.get(operation, {}).get("field") == "_supply_batch"
                    and isinstance(record, dict)
                    and record.get("spell") == "'goodberry'"
                )
            ):
                record.update(
                    cast_id=CAST_ID,
                    source_caster_id="_spell_cast_owner_id",
                    ongoing_duration="spell_cast_id != ''",
                )
            if (
                name == "cast_spell_illusion_spell"
                and operation == "set"
                and (
                    node.get(operation, {}).get("field") == "_illusion_record"
                    and isinstance(record, dict)
                    and "spell" in record
                )
            ):
                record.update(
                    cast_id=CAST_ID,
                    source_caster_id="_illusion_source_caster_id",
                    spell_level="actual_spell_level",
                    dispel_ongoing=True,
                    ongoing_duration="spell_cast_id != '' and _illusion_profile.effect_rounds "
                    "> 0 and not (spell == 'major_image' and slot_level >= 4)",
                )
    if name == "install_srd_timed_condition_source":
        prefix = "lasting_state.timed_condition_sources.{_allocated_timed_condition_source_id}"
        fields = {prefix + ".cast_id", prefix + ".source_caster_id"}
        tool["effects"] = [
            e
            for e in tool["effects"]
            if e.get("set", {}).get("field") not in fields
            and not (
                e.get("condition") == "spell_cast_owner != null"
                and prefix + ".source_caster_id" in str(e)
            )
        ]
        tool["effects"] += [
            _set(prefix + ".cast_id", CAST_ID),
            _set(prefix + ".source_caster_id", None),
            _when(
                "spell_cast_owner != null",
                [_set(prefix + ".source_caster_id", "_spell_cast_owner_id")],
            ),
        ]
        # Existing progressive/Instantaneous source owners retain their own
        # duration classification when this cast path does not qualify.
        marker = "spell_cast_id != ''"
        tool["effects"] = [e for e in tool["effects"] if e.get("condition") != marker]
        tool["effects"].append(_when(marker, [_set(prefix + ".ongoing_duration", True)]))


def _stamp_concentration(effects, value):
    """The actual concentration start binds its caster owner to this same cast."""
    retained = []
    for effect in effects:
        if effect.get("set", {}).get("field") == "caster_state.concentration_cast_id":
            continue
        for branch in ("on_true", "on_false"):
            if branch in effect:
                effect[branch] = _stamp_concentration(effect[branch], value)
        retained.append(effect)
        field = effect.get("set", {}).get("field")
        assigned = effect.get("set", {}).get("value")
        if field == "caster_state.concentration_spell_id" and assigned in {"spell", "'heat_metal'"}:
            retained.append(_set("caster_state.concentration_cast_id", value))
    return retained


def extend_runtime(text, *, requirements=None, definitions=None):
    runtime = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    tools = runtime["tools"]
    requirements = requirements or runtime["definitions"]["spell_casting"]
    definitions = definitions or runtime["definitions"]["spells"]
    admitted = supported_time_spans(requirements, definitions, tools=tools)
    reached = _call_paths(tools)
    changed = {
        "begin_srd_spell_source_instance": source_instance_tool(),
        "bind_srd_spell_source_identity": _tool(
            "Retain the already allocated exact casting identity as a string.",
            [_param("cast_id")],
            [_set("_spell_source_cast_id", "cast_id")],
        ),
    }
    for name in reached:
        tool = deepcopy(tools[name])
        if name != "cast_spell":
            for param in (
                _param(CAST_ID, required=False, default="", model_hidden=True),
                _param(
                    "actual_spell_level",
                    "integer",
                    required=False,
                    default=-1,
                    minimum=-1,
                    maximum=9,
                    model_hidden=True,
                ),
                _param(
                    CAST_OWNER, resolve="entity", required=False, default=None, model_hidden=True
                ),
            ):
                if not any(p["name"] == param["name"] for p in tool["parameters"]):
                    tool["parameters"].append(param)
        for node in _walk(tool.get("effects", [])):
            if node.get("call") in reached and node["call"] != "cast_spell":
                node["params"][CAST_ID] = (
                    "{_spell_source_cast_id}" if name == "cast_spell" else "{spell_cast_id}"
                )
                node["params"][CAST_OWNER] = (
                    "{_caster_state_id}" if name == "cast_spell" else "{_spell_cast_owner_id}"
                )
                node["params"]["actual_spell_level"] = (
                    "{_slot_level}" if name == "cast_spell" else "{actual_spell_level}"
                )
        if name in INSTALLERS:
            _stamp_installer(name, tool)
        tool["effects"] = _stamp_concentration(
            tool["effects"],
            "_spell_source_cast_id" if name == "cast_spell" else CAST_ID,
        )
        changed[name] = tool
    cast = changed["cast_spell"]
    cast["effects"] = [
        e
        for e in cast["effects"]
        if e.get("set", {}).get("field")
        not in {
            "_spell_source_cast_id",
            "_spell_time_span_ids",
        }
        and not (
            "spell in _spell_time_span_ids" in e.get("condition", "")
            and any(
                child.get("call") == "begin_srd_spell_source_instance"
                for child in e.get("on_true", [])
            )
        )
    ]
    dispatch = next(
        i
        for i, e in enumerate(cast["effects"])
        if any(n.get("call", "").startswith("cast_spell_") for n in _walk([e]))
    )
    cast["effects"].insert(dispatch, _set("_spell_source_cast_id", "''"))
    cast["effects"].insert(
        dispatch + 1,
        _set(
            "_spell_time_span_ids",
            [f"'{spell}'" for spell in admitted],
        ),
    )
    duration_condition = (
        "_spell_cast_failed != true and _pending_spell_operation_lost != true "
        "and spell in _spell_time_span_ids"
    )
    if "cast_spell_minor_world_spell" in tools:
        # The shared Duration header includes immediate modes. Allocate only the
        # actual printed ongoing clause admitted by this family owner.
        duration_condition += (
            " and (spell != 'prestidigitation' and spell != 'thaumaturgy' or "
            "(spell == 'prestidigitation' and (minor_mode == 'minor_sensation' or "
            "minor_mode == 'magic_mark' or minor_mode == 'minor_creation')) or "
            "(spell == 'thaumaturgy' and (minor_mode == 'altered_eyes' or minor_mode == "
            "'booming_voice' or minor_mode == 'fire_play' or minor_mode == 'tremors')))"
        )
    cast["effects"].insert(
        dispatch + 2,
        _when(
            duration_condition,
            [
                _call(
                    "begin_srd_spell_source_instance",
                    {
                        "caster_state": "{_caster_state_id}",
                        "lasting_state": "{_lasting_state_id}",
                        "spell": "{spell}",
                        "slot_level": "{_slot_level}",
                    },
                    ("_spell_source_cast_id",),
                )
            ],
        ),
    )
    cast["effects"] = [
        e for e in cast["effects"] if e.get("condition") != "_spell_source_cast_id != ''"
    ]
    cast["effects"].append(
        _when(
            "_spell_source_cast_id != ''",
            [
                {
                    "list_push": {
                        "list": "_spell_card_summary",
                        "value": {
                            "label": "'Cast reference'",
                            "value": "_spell_source_cast_id",
                        },
                    }
                },
            ],
        )
    )
    return _replace_tools(text, changed)
