"""legacy Misty Step uses the actual saved scene and shared casting budget."""

from .srd_control_behaviors import _entity, _lasting, _param
from .srd_scene_relations import _array
from .srd_timed_actor_defenses import _call, _require, _set, _tool, _when


def bind_travel_profiles(runtime):
    runtime["definitions"]["spells"]["misty_step"].update(range_kind="self", range_feet=0, casting_time="bonus_action")
    runtime["definitions"]["spell_casting"]["misty_step"].update(
        components=["verbal"], material="", material_parts=[], material_consumed=False,
        material_has_cost=False, focus_substitution=False, duration="Instantaneous",
    )


def _validation():
    return _tool(
        "Validate the caster's visible exact destination using admitted geometry before expenditure.",
        [_entity("caster_state"), _lasting(), _array("destination")],
        [
            _require("caster_state.is_alive == true", "Misty Step requires a living caster."),
            _call("srd_information_scene_entity", {"subject": "{_caster_state_id}"}, ("_info_scene_entity", "_info_position")),
            _set("_lite_travel_caster", "_info_scene_entity"),
            _require("_lite_travel_caster.metadata.plane != null", "Record the caster's actual plane before teleporting."),
            _set("_lite_travel_horizontal", "((destination[0] - _lite_travel_caster.position[0]) ** 2 + (destination[1] - _lite_travel_caster.position[1]) ** 2) ** 0.5"),
            _set("_lite_travel_range", "60 + max(0, _lite_travel_caster.position[2] - destination[2])"),
            _require("_lite_travel_horizontal <= _lite_travel_range", "Misty Step exceeds its actual horizontal range, including downward elevation."),
            _call("check_srd_travel_destination_sight", {"caster_state": "{_caster_state_id}", "point": "{destination}"}),
            _call("resolve_srd_travel_placement", {
                "subject": "{_caster_state_id}", "destination": "{destination}",
                "plane": "{_lite_travel_caster.metadata.plane}", "excluded": [], "lasting_state": "{_lasting_state_id}",
            }, ("_travel_placement",)),
            _require("_travel_placement.distance == 0", "Misty Step requires this exact unoccupied destination; a nearby space is insufficient."),
        ],
    )


def _cast():
    return _tool(
        "Teleport the real caster after the shared owner pays the selected Bonus Action and slot.",
        [_entity("caster_state"), _lasting(), _array("destination")],
        [
            _call("validate_srd_lite_misty_step", {"caster_state": "{_caster_state_id}", "lasting_state": "{_lasting_state_id}", "destination": "{destination}"}, ("_lite_travel_caster", "_lite_travel_horizontal", "_lite_travel_range")),
            _call("move_srd_travel_subject", {
                "subject": "{_caster_state_id}", "destination": "{destination}",
                "plane": "{_lite_travel_caster.metadata.plane}", "teleport": True, "lasting_state": "{_lasting_state_id}",
            }),
            _set("_spell_summary_rows", [{"label": "'Travel'", "value": "'Caster teleported to the selected visible space'"}]),
            _set("_spell_detail_rows", [{"label": "'Destination'", "value": "destination"}, {"label": "'Horizontal distance'", "value": "_lite_travel_horizontal"}, {"label": "'Current maximum range'", "value": "_lite_travel_range"}, {"label": "'Cost'", "value": "'A selected level-two or higher slot and the shared Bonus Action; no movement or extra upcast benefit.'"}]),
        ],
    )


def bind_travel_support(runtime):
    tools = runtime["tools"]
    tools["validate_srd_lite_misty_step"] = _validation()
    tools["cast_srd_lite_misty_step"] = _cast()
    for name in ("cast_spell", "begin_srd_spell_action"):
        effects = tools[name]["effects"]
        index = next(i for i,e in enumerate(effects) if e.get("set",{}).get("field") == "_spell_definition" or e.get("store_as") == "_spell_definition")
        effects[index+1:index+1] = [
            _when("_ms == 'misty_step'", [
                _require("_target_id == _caster_state_id and (targets == null or len(targets) == 0 or (len(targets) == 1 and targets[0] == _caster_state_id))", "Misty Step transports only its caster."),
                _call("validate_srd_lite_misty_step", {"caster_state": "{_caster_state_id}", "lasting_state": "{_lasting_state_id}", "destination": "{travel_destination}"}),
            ]),
        ]
    cast = tools["cast_spell"]
    index = next(i for i,e in enumerate(cast["effects"]) if e.get("call") == "begin_srd_spell_source_instance")
    cast["effects"].insert(index+1, _when("_ms == 'misty_step' and _spell_cast_failed != true and _pending_spell_operation_lost != true", [
        _call("cast_srd_lite_misty_step", {"caster_state": "{_caster_state_id}", "lasting_state": "{_lasting_state_id}", "destination": "{travel_destination}"}, ("_spell_summary_rows", "_spell_detail_rows")),
    ]))
    paths = 0
    for name in ("cast_spell", "finish_srd_retained_cast_tail"):
        for effects in _lists(tools[name]["effects"]):
            for effect in effects:
                if (isinstance(effect, dict)
                        and "_spell_definition.family == 'travel_spell'" in effect.get("condition", "")
                        and "cast_spell_travel_spell" in str(effect)):
                    identity = "_ms" if name == "cast_spell" else "_retained_cast_tail_choices.spell"
                    effect["condition"] += f" and {identity} != 'misty_step'"
                    paths += 1
    assert paths == 1, "Keep the retained travel route intact for every other profile."


def _lists(value):
    if isinstance(value,list):
        yield value
        for child in value:yield from _lists(child)
    elif isinstance(value,dict):
        for child in value.values():yield from _lists(child)
