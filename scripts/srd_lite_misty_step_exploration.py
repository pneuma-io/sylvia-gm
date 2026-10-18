"""Keep Lite Misty Step factual in exploration and spatially authoritative in combat."""

from copy import deepcopy

from sylvia_gm.domain.package_library import participant_schema_sha256

from .srd_control_behaviors import _each, _lasting, _when
from .srd_lite_mass_cure_geometry import _effect_lists
from .srd_lite_misty_step_card import OWNER as CARD
from .srd_lite_misty_step_card import ROWS
from .srd_lite_steed_fey_step import MOUNT_FIELD, RIDER_FIELD
from .srd_scene_relations import _array
from .srd_timed_actor_defenses import _call, _entity, _require, _set, _tool

FACTS = "travel_exploration_destinations"
VALIDATE = "validate_revised_srd_misty_step"
COMBAT_VALIDATE = "validate_srd_misty_step_combat"
EXPLORE_VALIDATE = "validate_srd_lite_misty_exploration"
EXPLORE_CAST = "cast_srd_lite_misty_exploration"
DETACH = "detach_srd_lite_misty_exploration_mount"
PAID = "cast_spell_travel_spell"
ARRIVAL = "travel_location"
EXPORTS = ("_misty_exploration_destination", "_misty_distance", "_misty_range")
LOCATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "place": {"type": "string", "minLength": 1, "maxLength": 300},
        "plane": {"type": "string", "minLength": 1, "maxLength": 100},
        "native_anchor": {"type": "boolean", "enum": [False]},
        "via_spell": {"type": "string", "enum": ["misty_step"]},
    },
    "required": ["place", "plane", "native_anchor", "via_spell"],
}
PARAMETER = {
    "name": FACTS,
    "type": "array",
    "required": False,
    "default": [],
    "min_items": 0,
    "max_items": 1,
    "items": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "place": {"type": "string", "minLength": 1},
            "current_plane": {"type": "string", "minLength": 1},
            "plane": {"type": "string", "minLength": 1},
            "horizontal_distance_feet": {"type": "number", "minimum": 0},
            "elevation_change_feet": {"type": "number"},
            "visible": {"type": "boolean"},
            "unoccupied": {"type": "boolean"},
        },
        "required": [
            "place",
            "current_plane",
            "plane",
            "horizontal_distance_feet",
            "elevation_change_feet",
            "visible",
            "unoccupied",
        ],
    },
    "description": (
        "For Misty Step outside combat, supply exactly one established destination fact record: "
        "its named place, actual current_plane and destination plane, horizontal_distance_feet, "
        "signed elevation_change_feet (negative means downward), visible and unoccupied. "
        "Do not invent these facts; the destination must be on the current plane and within "
        "the original 30-foot straight-line range, including either upward or downward elevation. "
        "Combat ignores this record and uses travel_destination and the saved scene."
    ),
}


def _exploration_validation():
    """Validate established world facts without requiring, querying or moving a coordinate scene."""
    fact = "_misty_exploration_destination"
    return _tool(
        "Validate an established visible, unoccupied same-plane exploration destination.",
        [_entity("caster_state"), _lasting(), deepcopy(PARAMETER)],
        [
            _require("combat.encounter_active != true", "Combat Misty Step uses saved positions."),
            _require("caster_state.is_alive == true", "Misty Step needs a living caster."),
            _require("len(" + FACTS + ") == 1", "Establish one actual exploration destination."),
            _set(fact, FACTS + "[0]"),
            _require(
                fact
                + ".place != null and len("
                + fact
                + ".place) > 0 and "
                + fact
                + ".current_plane != null and len("
                + fact
                + ".current_plane) > 0 and "
                + fact
                + ".plane != null and len("
                + fact
                + ".plane) > 0",
                "Name the established destination and actual current and destination planes.",
            ),
            _require(
                "len(" + fact + ".place) <= 300 and len(" + fact
                + ".current_plane) <= 100 and len(" + fact + ".plane) <= 100",
                "Use a bounded destination name and current and destination planes.",
            ),
            _require(
                fact + ".plane == " + fact + ".current_plane",
                "Misty Step cannot change the caster's actual plane.",
            ),
            _set("_misty_current_plane", None),
            _when(
                "caster_state." + ARRIVAL + ".plane != null",
                [_set("_misty_current_plane", "caster_state." + ARRIVAL + ".plane")],
            ),
            _when(
                "caster_state.find_path_current_plane != null",
                [_set("_misty_current_plane", "caster_state.find_path_current_plane")],
            ),
            _when(
                "lasting_state.spatial_scene != null and _misty_current_plane == null",
                [
                    _set("_misty_known_bodies", "lasting_state.spatial_scene.entities"),
                    _each(
                        "_misty_known_bodies",
                        "_misty_known_body",
                        [
                            _when(
                                "_misty_known_body.active == true and "
                                "(_misty_known_body.id == _caster_state_id or "
                                "_misty_known_body.metadata.participant_ref == _caster_state_id)",
                                [_set("_misty_current_plane", "_misty_known_body.metadata.plane")],
                            )
                        ],
                    ),
                ],
            ),
            _require(
                "_misty_current_plane == null or _misty_current_plane == "
                + fact
                + ".current_plane",
                "Destination facts must preserve the caster's actual saved current plane.",
            ),
            _require(
                fact + ".visible == true and 'blinded' not in caster_state.conditions",
                "Misty Step needs an established destination the caster can actually see.",
            ),
            _require(fact + ".unoccupied == true", "Establish an actual unoccupied destination."),
            _require(
                fact
                + ".horizontal_distance_feet != null and "
                + fact
                + ".horizontal_distance_feet >= 0 and "
                + fact
                + ".elevation_change_feet != null",
                "Establish actual horizontal distance and signed elevation change in feet.",
            ),
            _set(
                "_misty_distance",
                "("
                + fact
                + ".horizontal_distance_feet ** 2 + "
                + fact
                + ".elevation_change_feet ** 2) ** 0.5",
            ),
            _set("_misty_range", 30),
            _require(
                "_misty_distance <= _misty_range",
                "The destination exceeds Misty Step's original 30-foot straight-line range.",
            ),
            _when(
                "caster_state."
                + MOUNT_FIELD
                + " != null and caster_state."
                + MOUNT_FIELD
                + " != ''",
                [
                    _call(
                        DETACH,
                        {
                            "caster_state": "{_caster_state_id}",
                            "steed_state": "{caster_state." + MOUNT_FIELD + "}",
                        },
                    ),
                ],
            ),
        ],
    )


def _validation_route():
    """Keep the public prepayment seam while choosing authority strictly by combat state."""
    return _tool(
        "Validate Lite Misty Step before payment: saved combat geometry or exploration facts.",
        [_entity("caster_state"), _lasting(), _array("destination"), deepcopy(PARAMETER)],
        [
            _when(
                "combat.encounter_active == true",
                [
                    _call(
                        COMBAT_VALIDATE,
                        {
                            "caster_state": "{_caster_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                            "destination": "{destination}",
                        },
                    )
                ],
                [
                    _call(
                        EXPLORE_VALIDATE,
                        {
                            "caster_state": "{_caster_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                            FACTS: "{" + FACTS + "}",
                        },
                        EXPORTS,
                    )
                ],
            )
        ],
    )


def _detach():
    """Validate the actual Steed pair; clear it only when solo exploration travel commits."""
    return _tool(
        "Detach the caster's authentic saved Otherworldly Steed relation after solo travel.",
        [
            _entity("caster_state"),
            _entity("steed_state"),
            {"name": "detach", "type": "boolean", "required": False, "default": False},
        ],
        [
            _require(
                "steed_state.created_spell_id == 'find_steed' and "
                "steed_state.created_active == true "
                "and caster_state." + MOUNT_FIELD + " == _steed_state_id and "
                "steed_state." + RIDER_FIELD + " == _caster_state_id",
                "Solo Misty Step needs the actual reciprocal saved Otherworldly Steed pair.",
            ),
            _when(
                "detach == true",
                [
                    _set("caster_state." + MOUNT_FIELD, None),
                    _set("steed_state." + RIDER_FIELD, None),
                ],
            ),
        ],
    )


def _exploration_cast():
    """Commit one durable fictional arrival only after the existing shared spell payment."""
    return _tool(
        "Commit the paid caster-only same-plane exploration arrival without a combat map.",
        [_entity("caster_state"), _lasting(), deepcopy(PARAMETER)],
        [
            _call(
                EXPLORE_VALIDATE,
                {
                    "caster_state": "{_caster_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                    FACTS: "{" + FACTS + "}",
                },
                EXPORTS,
            ),
            _set(
                "caster_state." + ARRIVAL,
                {
                    "place": "_misty_exploration_destination.place",
                    "plane": "_misty_exploration_destination.plane",
                    "native_anchor": False,
                    "via_spell": "'misty_step'",
                },
            ),
            _when(
                "caster_state.find_path_current_place != null",
                [
                    _set(
                        "caster_state.find_path_current_place",
                        "_misty_exploration_destination.place",
                    ),
                    _set(
                        "caster_state.find_path_current_plane",
                        "_misty_exploration_destination.plane",
                    ),
                ],
            ),
            _when(
                "caster_state."
                + MOUNT_FIELD
                + " != null and caster_state."
                + MOUNT_FIELD
                + " != ''",
                [
                    _call(
                        DETACH,
                        {
                            "caster_state": "{_caster_state_id}",
                            "steed_state": "{caster_state." + MOUNT_FIELD + "}",
                            "detach": True,
                        },
                    ),
                ],
            ),
            _set(
                "_spell_summary_rows",
                [
                    {
                        "label": "'Travel'",
                        "value": "'Teleported to the established exploration destination'",
                    },
                ],
            ),
            _set(
                "_spell_detail_rows",
                [
                    {"label": "'Distance (ft)'", "value": "_misty_distance"},
                    {"label": "'Maximum range (ft)'", "value": "_misty_range"},
                    {"label": "'Destination'", "value": "_misty_exploration_destination.place"},
                ],
            ),
        ],
    )


def _bind_transport(tools):
    """Carry the new optional facts through the existing cast, pending and retained-choice paths."""
    template = "travel_destination"
    changed = []
    for name, owner in tools.items():
        if any(p["name"] == template for p in owner["parameters"]):
            owner["parameters"] = [*owner["parameters"], deepcopy(PARAMETER)]
            changed.append(name)
    for name, owner in tools.items():
        for rows in list(_effect_lists(owner.get("effects", []))):
            expanded = []
            for row in rows:
                setting = row.get("set", {})
                value = setting.get("value")
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict) and template in item:
                            item[FACTS] = FACTS
                if isinstance(value, dict) and template in value:
                    value[FACTS] = FACTS
                params = row.get("params", {})
                target = tools.get(row.get("call"), {})
                if template in params and any(
                    p["name"] == FACTS for p in target.get("parameters", [])
                ):
                    params[FACTS] = params[template].replace(template, FACTS)
                if row.get("call") == VALIDATE:
                    params[FACTS] = "{" + FACTS + "}"
                expanded.append(row)
                field = setting.get("field", "")
                if (
                    name == "begin_srd_spell_action"
                    and field == "_retained_nullable_capture_rows"
                    and isinstance(value, list)
                    and any(item.get("key") == repr(template) for item in value)
                ):
                    # Normal pending casts use the existing named-row capture
                    # loop rather than individual parameter writes.
                    value.append(
                        {"key": repr(FACTS), "missing": FACTS + " == null", "value": FACTS}
                    )
                if field.endswith(".parameters." + template):
                    copy = deepcopy(row)
                    copy["set"]["field"] = field.removesuffix(template) + FACTS
                    if isinstance(value, str):
                        copy["set"]["value"] = value.replace(template, FACTS)
                    if "condition" in copy:
                        copy["condition"] = copy["condition"].replace(template, FACTS)
                    expanded.append(copy)
            rows[:] = expanded
    # The fact record joins the already opaque choice capsule; no separate
    # argument is added to the continuation's required typed inputs.
    tail = tools["finish_srd_retained_cast_tail"]
    for value in tail.get("metadata", {}).values():
        if isinstance(value, list) and template in value and FACTS not in value:
            value.append(FACTS)
    return changed


def bind_misty_step_exploration(runtime, record):
    """Bind after card/scene/recipient composition and before family/private pruning.

    The selected identity changes only its preflight, paid travel branch and arrival
    receipt. Original headers, profiles, slot scaling, action payment and random
    rolls stay with their existing owners. Other travel identities retain their
    exact original effect list in the false branch.
    """
    tools = runtime["tools"]
    if any(name in tools for name in (EXPLORE_VALIDATE, EXPLORE_CAST, DETACH)):
        raise ValueError("Misty Step exploration must be composed exactly once.")
    if any(name not in tools for name in (VALIDATE, PAID, CARD)):
        raise ValueError("Retain the admitted Misty Step preflight, travel and receipt owners.")
    profiles = deepcopy(runtime["definitions"]["spells"])
    headers = deepcopy(runtime["definitions"]["spell_casting"])
    profile = profiles["misty_step"]
    if any(
        profile.get(key) != value
        for key, value in {
            "base_level": 2,
            "casting_time": "bonus_action",
            "concentration": False,
            "range_kind": "self",
            "range_feet": 0,
            "family": "travel_spell",
        }.items()
    ):
        raise ValueError("Preserve the approved Misty Step profile.")
    artifacts = [
        artifact
        for artifact in record["participant_artifacts"]
        if artifact["kind"] == "player_character"
    ]
    if len(artifacts) != 1:
        raise ValueError("Misty Step needs the admitted Player Character state contract.")
    artifact = artifacts[0]
    entity_schema = artifact["entity_state_schema"]
    if (
        entity_schema.get("type") != "object"
        or entity_schema.get("additionalProperties") is not False
    ):
        raise ValueError("Preserve the admitted strict Player Character entity-state schema.")
    if ARRIVAL in entity_schema.get("required", []):
        raise ValueError("The exploration arrival must remain optional for existing characters.")
    properties = entity_schema["properties"]
    if ARRIVAL in properties and properties[ARRIVAL] != LOCATION_SCHEMA:
        raise ValueError("Do not overwrite another declared travel-location contract.")
    properties[ARRIVAL] = deepcopy(LOCATION_SCHEMA)
    # An already admitted record carries its complete creation-contract hash.
    # Fresh Lite build finalization later removes this hash after all variant
    # edits; the ordinary materializer uses this same primitive to bind the final
    # schema, starting calculations and validation without an adaptation layer.
    if "schema_sha256" in artifact:
        artifact["schema_sha256"] = participant_schema_sha256(
            artifact["schema"],
            entity_state_schema=entity_schema,
            initial_state_calculations=artifact.get("initial_state_calculations", ()),
            initial_state_validation=artifact.get("initial_state_validation"),
        )
    tools.update(
        {
            # Preserve the admitted prepayment owner verbatim in combat. legacy is
            # the positioning reference; its historical mobility rules are not
            # selected spell mechanics.
            COMBAT_VALIDATE: deepcopy(tools[VALIDATE]),
            EXPLORE_VALIDATE: _exploration_validation(),
            EXPLORE_CAST: _exploration_cast(),
            DETACH: _detach(),
            VALIDATE: _validation_route(),
        }
    )
    transport = _bind_transport(tools)
    paid = tools[PAID]
    native = paid["effects"]
    paid["effects"] = [
        _when(
            "spell == 'misty_step' and combat.encounter_active != true",
            [
                _require(
                    "targets == null or len(targets) == 0", "Misty Step transports only its caster."
                ),
                _call(
                    EXPLORE_CAST,
                    {
                        "caster_state": "{_caster_state_id}",
                        "lasting_state": "{_lasting_state_id}",
                        FACTS: "{" + FACTS + "}",
                    },
                    ("_spell_summary_rows", "_spell_detail_rows"),
                ),
            ],
            native,
        )
    ]
    card = tools[CARD]
    native_receipt = card["effects"]
    card["effects"] = [
        _when(
            "combat.encounter_active == true",
            native_receipt,
            [
                _require(
                    "caster_state." + ARRIVAL + ".via_spell == 'misty_step'",
                    "The paid exploration arrival must be saved before showing its receipt.",
                ),
                _set("_misty_mount_status", "'Unmounted'"),
                _when(
                    "caster_state."
                    + MOUNT_FIELD
                    + " != null and caster_state."
                    + MOUNT_FIELD
                    + " != ''",
                    [_set("_misty_mount_status", "'Mounted'")],
                ),
                _set(
                    ROWS,
                    [
                        {"label": "'Arrival'", "value": "caster_state." + ARRIVAL + ".place"},
                        {"label": "'Plane'", "value": "caster_state." + ARRIVAL + ".plane"},
                        {"label": "'Mount status'", "value": "_misty_mount_status"},
                    ],
                ),
            ],
        )
    ]
    for name in ("cast_spell", "begin_srd_spell_action", "cast_spell_outside_combat"):
        tools[name]["description"] += " " + PARAMETER["description"]
    if (
        profiles != runtime["definitions"]["spells"]
        or headers != runtime["definitions"]["spell_casting"]
    ):
        raise ValueError("Misty Step composition must preserve profiles and casting rules.")
    return [
        VALIDATE,
        COMBAT_VALIDATE,
        EXPLORE_VALIDATE,
        EXPLORE_CAST,
        DETACH,
        PAID,
        CARD,
        *transport,
    ]
