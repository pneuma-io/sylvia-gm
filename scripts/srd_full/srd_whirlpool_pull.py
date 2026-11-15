"""Whirlpool's exact tapered proximity precedes its source-qualified forced receiver."""

import re

import yaml

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS, _array
    from .srd_spell_environment import (
        _call,
        _each,
        _effect_lists,
        _entity,
        _environment_tool,
        _param,
        _require,
        _set,
        _when,
    )
    from .srd_timed_senses import _replace_tools
    from .srd_water_whirlpool import _source
else:
    from srd_scene_relations import POSITION_EXPORTS, _array
    from srd_spell_environment import (
        _call,
        _each,
        _effect_lists,
        _entity,
        _environment_tool,
        _param,
        _require,
        _set,
        _when,
    )
    from srd_timed_senses import _replace_tools
    from srd_water_whirlpool import _source


def _submerged():
    return [
        _set(
            "_whirl_pull_submerged",
            " and ".join(
                f"_whirl_pull_body.position[{i}] - "
                f"_whirl_pull_body.footprint.half_extents[{i}] >= "
                f"_whirl_water.volume.min[{i}] and "
                f"_whirl_pull_body.position[{i}] + "
                f"_whirl_pull_body.footprint.half_extents[{i}] <= "
                f"_whirl_water.volume.max[{i}]"
                for i in range(2)
            ),
        ),
        _set(
            "_whirl_pull_submerged",
            "_whirl_pull_submerged == true and "
            "_whirl_pull_body.position[2] >= _whirl_water.volume.min[2] and "
            "_whirl_pull_body.position[2] + _whirl_pull_body.height <= _whirl_water.volume.max[2]",
        ),
    ]


def _distance():
    """Minimize Box–taper distance at caps and exact side projections without samples."""
    return [
        _set(
            "_whirl_pull_radial_distance",
            "(max(0, abs(_whirl_pull_body.position[0] - _whirl_shape.origin[0]) - "
            "_whirl_pull_body.footprint.half_extents[0]) ** 2 + "
            "max(0, abs(_whirl_pull_body.position[1] - _whirl_shape.origin[1]) - "
            "_whirl_pull_body.footprint.half_extents[1]) ** 2) ** 0.5",
        ),
        _set("_whirl_pull_slope", "(_whirl_shape.top_radius - 2.5) * 0.04"),
        _set("_whirl_pull_low_z", "_whirl_pull_body.position[2] - _whirl_shape.origin[2]"),
        _set("_whirl_pull_high_z", "_whirl_pull_low_z + _whirl_pull_body.height"),
        _set(
            "_whirl_pull_side_numerator",
            "_whirl_pull_slope * (_whirl_pull_radial_distance - 2.5)",
        ),
        _set(
            "_whirl_pull_z_candidates",
            [
                0,
                25,
                "min(25, max(0, _whirl_pull_high_z))",
                "max(0, min(min(25, _whirl_pull_low_z), "
                "(_whirl_pull_side_numerator + _whirl_pull_low_z) * "
                "(1 + _whirl_pull_slope ** 2) ** -1))",
                "min(25, max(max(0, _whirl_pull_high_z), "
                "(_whirl_pull_side_numerator + _whirl_pull_high_z) * "
                "(1 + _whirl_pull_slope ** 2) ** -1))",
            ],
        ),
        _set(
            "_whirl_pull_distance_squared",
            "max(0, _whirl_pull_radial_distance - 2.5) ** 2 + "
            "max(0, max(_whirl_pull_low_z, -_whirl_pull_high_z)) ** 2",
        ),
        _set("_whirl_pull_nearest_z", 0),
        _each(
            "_whirl_pull_z_candidates",
            "_whirl_pull_candidate_z",
            [
                _set(
                    "_whirl_pull_candidate_distance_squared",
                    "max(0, _whirl_pull_radial_distance - 2.5 - "
                    "_whirl_pull_slope * _whirl_pull_candidate_z) ** 2 + "
                    "max(0, max(_whirl_pull_low_z - _whirl_pull_candidate_z, "
                    "_whirl_pull_candidate_z - _whirl_pull_high_z)) ** 2",
                ),
                _when(
                    "_whirl_pull_candidate_distance_squared < _whirl_pull_distance_squared",
                    [
                        _set(
                            "_whirl_pull_distance_squared",
                            "_whirl_pull_candidate_distance_squared",
                        ),
                        _set("_whirl_pull_nearest_z", "_whirl_pull_candidate_z"),
                    ],
                ),
            ],
        ),
        _set("_whirl_pull_near", "_whirl_pull_distance_squared <= 625"),
    ]


def _target_tool():
    return _environment_tool(
        "Qualify living whole-submerged Box proximity to the exact paid Whirlpool taper.",
        [_entity("target_state"), _param("area_id")],
        [
            _set("_whirl_pull_near", False),
            _set("_whirl_pull_submerged", False),
            _set("_whirl_pull_body", None),
            _set("_whirl_pull_distance_squared", None),
            *_source(),
            _when(
                "_whirl_current == true and target_state.is_alive == true",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_target_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _set("_whirl_pull_body", "_info_scene_entity"),
                    _when(
                        "_whirl_pull_body.metadata.plane == _whirl_source.plane and "
                        "_whirl_pull_body.metadata.is_object != true and "
                        "_whirl_pull_body.metadata.creature_type != 'spell_force'",
                        [
                            _require(
                                "_whirl_pull_body.metadata.participant_ref == _target_state_id",
                                "Use this actual bound living creature body.",
                            ),
                            _require(
                                "_whirl_pull_body.footprint.kind == 'box'",
                                "Whirlpool pull proximity needs the actual admitted Box body.",
                            ),
                            *_submerged(),
                            _when("_whirl_pull_submerged == true", _distance()),
                        ],
                    ),
                ],
            ),
        ],
    )


def _endpoint():
    """This bounded ten-foot horizontal consequence uses an actual same-height point on the axis."""
    return [
        _set(
            "_whirl_pull_axis_distance",
            "((_whirl_pull_body.position[0] - _whirl_shape.origin[0]) ** 2 + "
            "(_whirl_pull_body.position[1] - _whirl_shape.origin[1]) ** 2) ** 0.5",
        ),
        _require(
            "_whirl_pull_axis_distance >= 10 and "
            "_whirl_pull_body.position[2] >= _whirl_shape.origin[2] and "
            "_whirl_pull_body.position[2] < _whirl_shape.origin[2] + 25",
            "Closer, coincident or vertical Whirlpool pulls need their actual direction consumer.",
        ),
        _set(
            "_whirl_pull_destination",
            [
                f"_whirl_pull_body.position[{i}] + "
                f"(_whirl_shape.origin[{i}] - _whirl_pull_body.position[{i}]) * "
                "10 * _whirl_pull_axis_distance ** -1"
                for i in range(2)
            ] + ["_whirl_pull_body.position[2]"],
        ),
        _require(
            " and ".join(
                f"_whirl_pull_destination[{i}] - "
                f"_whirl_pull_body.footprint.half_extents[{i}] >= "
                f"_whirl_water.volume.min[{i}] and "
                f"_whirl_pull_destination[{i}] + "
                f"_whirl_pull_body.footprint.half_extents[{i}] <= "
                f"_whirl_water.volume.max[{i}]"
                for i in range(2)
            ),
            "A partly immersed receiving Whirlpool body needs its actual consumer.",
        ),
    ]


def _motion_tool():
    """Requalify the real source/body and recompute its endpoint before the sole mutation."""
    return _environment_tool(
        "Authenticate only the source's current unconsumed exact ten-foot pull receipt.",
        [_param("subject"), _array("destination"), _param("plane")],
        [
            _set("_whirl_motion_allowed", False),
            _set("_whirl_motion_receipt", "state.whirlpool_pending_motion"),
            _require(
                "_whirl_motion_receipt.kind == 'whirlpool_pull' and "
                "_whirl_motion_receipt.consumed == false and "
                "_whirl_motion_receipt.subject == subject and "
                "_whirl_motion_receipt.plane == plane and "
                "_whirl_motion_receipt.tick == state.lasting_tick",
                "Use the authentic current paid Whirlpool motion receipt.",
            ),
            _call(
                "qualify_srd_whirlpool_pull_target",
                {"target_state": "{subject}", "area_id": "{_whirl_motion_receipt.source_id}"},
                (
                    "_whirl_pull_near", "_whirl_pull_body", "_whirl_source", "_whirl_shape",
                    "_whirl_water",
                ),
            ),
            _require(
                "_whirl_pull_near == true and _whirl_source.whirlpool_pull_batch_active == true "
                "and _whirl_source.sequence == _whirl_motion_receipt.sequence and "
                "_whirl_source.whirlpool_effect_sequence == "
                "_whirl_motion_receipt.effect_sequence and "
                "_whirl_source.whirlpool_pull_batch == _whirl_motion_receipt.batch and "
                "_whirl_source.caster == _whirl_motion_receipt.caster",
                "The actual paid source pull batch changed.",
            ),
            _require(
                "_whirl_pull_body.id == _whirl_motion_receipt.body_id and "
                "_whirl_pull_body.metadata.plane == plane and "
                "_whirl_pull_body.height == _whirl_motion_receipt.height and "
                "_whirl_pull_body.footprint.half_extents[0] == _whirl_motion_receipt.extents[0] "
                "and _whirl_pull_body.footprint.half_extents[1] == "
                "_whirl_motion_receipt.extents[1]",
                "The actual receiving creature body changed.",
            ),
            *_endpoint(),
            *[
                _require(
                    f"_whirl_pull_body.position[{i}] == _whirl_motion_receipt.origin[{i}] and "
                    f"destination[{i}] == _whirl_motion_receipt.destination[{i}] and "
                    f"destination[{i}] == _whirl_pull_destination[{i}]",
                    "Use only the actual source-derived ten-foot endpoint.",
                )
                for i in range(3)
            ],
            _set("_whirl_motion_allowed", True),
        ],
    )


def _pull_tool():
    return _environment_tool(
        "Pull one actual eligible creature through its authenticated sole receiver.",
        [_entity("target_state"), _param("area_id")],
        [
            _call(
                "qualify_srd_whirlpool_pull_target",
                {"target_state": "{_target_state_id}", "area_id": "{area_id}"},
                (
                    "_whirl_pull_near", "_whirl_pull_body", "_whirl_source", "_whirl_shape",
                    "_whirl_water",
                ),
            ),
            _when(
                "_whirl_pull_near == true",
                [
                    _require(
                        "_whirl_source.whirlpool_pull_batch_active == true",
                        "Only the actual source formation or repeat can issue this pull.",
                    ),
                    *_endpoint(),
                    _call(
                        "qualify_srd_whirlpool_target",
                        {"target_state": "{_target_state_id}", "area_id": "{area_id}"},
                        {"_whirl_pull_was_inside": "_whirl_target_inside"},
                    ),
                    _set(
                        "state.whirlpool_pending_motion",
                        {
                            "kind": "'whirlpool_pull'", "source_id": "area_id",
                            "sequence": "_whirl_source.sequence",
                            "effect_sequence": "_whirl_source.whirlpool_effect_sequence",
                            "batch": "_whirl_source.whirlpool_pull_batch",
                            "caster": "_whirl_source.caster", "tick": "state.lasting_tick",
                            "subject": "_target_state_id", "body_id": "_whirl_pull_body.id",
                            "origin": "_whirl_pull_body.position",
                            "destination": "_whirl_pull_destination",
                            "height": "_whirl_pull_body.height",
                            "extents": "_whirl_pull_body.footprint.half_extents",
                            "plane": "_whirl_source.plane", "consumed": False,
                        },
                    ),
                    _call(
                        "move_srd_whirlpool_subject",
                        {
                            "subject": "{_target_state_id}",
                            "destination": "{_whirl_pull_destination}",
                            "plane": "{_whirl_source.plane}",
                        },
                    ),
                    _set("state.whirlpool_pending_motion", None),
                    _when(
                        "_whirl_pull_was_inside != true",
                        [
                            _call(
                                "resolve_srd_environment_hazard_event",
                                {"target_state": "{_target_state_id}", "area_id": "{area_id}"},
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _batch_tool():
    return _environment_tool(
        "Settle one authentic formation/repeated Magic Action pull over actual living bodies.",
        [_param("area_id")],
        [
            *_source(),
            _require("_whirl_current == true", "Use the actual current paid Whirlpool."),
            _require(
                "_whirl_source.whirlpool_pull_batch_active != true",
                "Complete the actual source's prior receiving callback first.",
            ),
            _require(
                "_whirl_source.whirlpool_pull_settled_sequence != "
                "_whirl_source.whirlpool_effect_sequence",
                "This actual formation or repeated Magic Action pull already settled.",
            ),
            _set("_whirl_pull_batch", 1),
            _when(
                "_whirl_source.whirlpool_pull_batch != null",
                [_set("_whirl_pull_batch", "_whirl_source.whirlpool_pull_batch + 1")],
            ),
            _set(
                "state.persistent_spell_areas.{area_id}.whirlpool_pull_batch", "_whirl_pull_batch"
            ),
            _set("state.persistent_spell_areas.{area_id}.whirlpool_pull_batch_active", True),
            _set(
                "state.persistent_spell_areas.{area_id}.whirlpool_pull_settled_sequence",
                "_whirl_source.whirlpool_effect_sequence",
            ),
            _set("_whirl_pull_scene_bodies", "state.spatial_scene.entities"),
            _each(
                "_whirl_pull_scene_bodies", "_whirl_pull_candidate",
                [
                    _when(
                        "_whirl_pull_candidate.active == true and "
                        "_whirl_pull_candidate.metadata.participant_ref != null and "
                        "_whirl_pull_candidate.metadata.is_object != true and "
                        "_whirl_pull_candidate.metadata.creature_type != 'spell_force'",
                        [
                            _call(
                                "pull_srd_whirlpool_creature",
                                {
                                    "target_state": (
                                        "{_whirl_pull_candidate.metadata.participant_ref}"
                                    ),
                                    "area_id": "{area_id}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
            _set("state.persistent_spell_areas.{area_id}.whirlpool_pull_batch_active", False),
        ],
    )


def tools():
    """Candidate leaves stay unregistered until the authentic paid receiver vertical proves."""
    return {
        "qualify_srd_whirlpool_pull_target": _target_tool(),
        "qualify_srd_whirlpool_motion": _motion_tool(),
        "pull_srd_whirlpool_creature": _pull_tool(),
        "resolve_srd_whirlpool_pull_batch": _batch_tool(),
    }


def bind_sources(changed):
    """Issue pulls only after genuine paid formation or the real later Magic Action."""
    cast = changed["cast_spell_environment"]
    source_install = cast["effects"][-1]
    assert source_install["condition"] == (
        "_environment_profile.environment_mode == 'control_water'"
    )
    source_install["on_true"].append(
        _when(
            "water_effect == 'whirlpool'",
            [_call(
                "resolve_srd_whirlpool_pull_batch",
                {"area_id": "{_environment_cast_id}"},
            )],
        )
    )
    change = changed["change_srd_control_water"]
    change["effects"].append(
        _when(
            "water_effect == 'whirlpool'",
            [_call(
                "resolve_srd_whirlpool_pull_batch",
                {"area_id": "{_environment_area_key}"},
            )],
        )
    )


def bind_runtime_consumers(text, *, receiver_factory=None):
    """Capture final Travel callbacks; the optional Python factory is an exact build dependency."""
    if receiver_factory is None:
        if __package__:
            from .srd_reverse_gravity import _receiver
        else:
            from srd_reverse_gravity import _receiver
        receiver_factory = _receiver
    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    names = {
        "move_srd_reverse_gravity_subject": "move_srd_whirlpool_subject",
        "move_srd_reverse_gravity_subject_node": "move_srd_whirlpool_subject_node",
        "qualify_srd_reverse_gravity_motion": "qualify_srd_whirlpool_motion",
        "_gravity_motion_allowed": "_whirl_motion_allowed",
        "_gravity_up_sweep": "_whirl_up_sweep",
        **{
            "_gravity_receiver_" + name: "_whirl_receiver_" + name
            for name in (
                "teleport", "levitate_source_id", "telekinesis_source_id", "sphere_source_id"
            )
        },
    }

    def specialize(value):
        # Rename only this factory's receiver ABI; other source callbacks stay exact.
        if isinstance(value, str):
            value = {
                "The actual secure source prevents this paid gravity consequence.": (
                    "The actual secure source prevents this paid Whirlpool consequence."
                ),
                "An unsupported paid transit save cannot be replaced with an upward fall.": (
                    "An unsupported paid transit save cannot be replaced with a Whirlpool pull."
                ),
            }.get(value, value)
            value = value.replace(
                "state.reverse_gravity_pending_motion.consumed",
                "state.whirlpool_pending_motion.consumed",
            )
            for old, new in names.items():
                value = re.sub(r"\b" + re.escape(old) + r"\b", new, value)
            return value
        if isinstance(value, list):
            return [specialize(item) for item in value]
        if isinstance(value, dict):
            return {names.get(key, key): specialize(item) for key, item in value.items()}
        return value

    receivers = specialize(receiver_factory(runtime["tools"]))
    for tool in receivers.values():
        tool["description"] = "Commit only an authentic source-qualified Whirlpool pull."
        for effects in _effect_lists(tool["effects"]):
            for effect in effects:
                if effect.get("require", {}).get("condition") == (
                    "_whirl_up_sweep.supported == true and _whirl_up_sweep.clear == true"
                ):
                    effect["require"]["error"] = (
                        "An actual body collision prevents this ten-foot Whirlpool pull."
                    )
    return _replace_tools(text, receivers)
