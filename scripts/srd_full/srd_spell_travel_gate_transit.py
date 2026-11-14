"""Gate transit authenticates real fronts and exact zero-distance circular arrival witnesses."""

import re
from copy import deepcopy

if __package__:
    from .srd_scene_relations import _array, _tool_scene
    from .srd_spell_travel_gate import _lookup
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from .srd_timed_senses import _optional, _public
else:
    from srd_scene_relations import _array, _tool_scene
    from srd_spell_travel_gate import _lookup
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from srd_timed_senses import _optional, _public

PUBLIC_TOOLS = {"traverse_srd_gate", "look_through_srd_gate"}


def _pair():
    """Derive both actual circular frames from one retained live source and its saved bodies."""
    if __package__:
        from .srd_spell_travel import _scene_subject
    else:
        from srd_spell_travel import _scene_subject
    return [
        *_lookup(),
        _require(
            "_gate_source.active == true and state.lasting_tick < _gate_source.expires_tick",
            "The actual Gate source has ended.",
        ),
        _require(
            "portal_id == _gate_source.near_id or portal_id == _gate_source.far_id",
            "Use one of this exact Gate's actual paired portals.",
        ),
        *_scene_subject("{portal_id}", "_gate_entry"),
        _set("_gate_exit_id", "_gate_source.near_id"),
        _when("portal_id == _gate_source.near_id", [_set("_gate_exit_id", "_gate_source.far_id")]),
        *_scene_subject("{_gate_exit_id}", "_gate_exit"),
        *[
            effect
            for local, peer in (("_gate_entry", "_gate_exit"), ("_gate_exit", "_gate_entry"))
            for effect in [
                _require(
                    f"{local}.footprint.kind == 'circle' and "
                    f"{local}.footprint.radius == _gate_source.diameter * 0.5 and "
                    f"{local}.height == _gate_source.depiction_depth and "
                    f"{local}.metadata.gate_portal.source_id == source_id and "
                    f"{local}.metadata.gate_portal.peer_id == {peer}.id and "
                    f"{local}.metadata.gate_portal.cast_id == _gate_source.cast_id and "
                    f"{local}.metadata.gate_portal.created_tick == _gate_source.created_tick and "
                    f"{local}.metadata.gate_portal.front == _gate_source.front",
                    "Authenticate this source's actual unchanged circular frames.",
                ),
            ]
        ],
        *[
            effect
            for local in ("_gate_entry", "_gate_exit")
            for effect in [
                _when(
                    f"{local}.id == _gate_source.near_id",
                    [
                        _require(
                            " and ".join(
                                f"{local}.position[{axis}] == _gate_source.origin[{axis}]"
                                for axis in range(3)
                            )
                            + " and "
                            f"{local}.metadata.plane == _gate_source.origin_plane",
                            "The actual near Gate must retain its saved position and plane.",
                        ),
                    ],
                ),
                _when(
                    f"{local}.id == _gate_source.far_id",
                    [
                        _require(
                            " and ".join(
                                f"{local}.position[{axis}] == _gate_source.destination[{axis}]"
                                for axis in range(3)
                            )
                            + " and "
                            f"{local}.metadata.plane == _gate_source.destination_plane",
                            "The actual far Gate must retain its saved position and plane.",
                        ),
                    ],
                ),
            ]
        ],
        _set("_gate_entry_plane", "_gate_entry.position[2]"),
        _set("_gate_exit_plane", "_gate_exit.position[2]"),
        _when(
            "_gate_source.front == 'up'",
            [
                _set("_gate_entry_plane", "_gate_entry.position[2] + _gate_entry.height"),
                _set("_gate_exit_plane", "_gate_exit.position[2] + _gate_exit.height"),
            ],
        ),
    ]


def _fit(body, point, portal):
    """Every corner of the actual axis-aligned box footprint must fit the real circle."""
    return [
        _require(
            f"{body}.footprint.kind == 'box'",
            "Exact Gate aperture clearance currently supports actual box bodies.",
        ),
        _require(
            f"(abs({point}[0] - {portal}.position[0]) + {body}.footprint.half_extents[0]) ** 2 + "
            f"(abs({point}[1] - {portal}.position[1]) + {body}.footprint.half_extents[1]) ** 2 "
            f"<= {portal}.footprint.radius ** 2",
            "The whole actual footprint must fit the circular aperture, including its corners.",
        ),
    ]


def _magic(tools):
    if "check_srd_magic_zone_effect" not in tools:
        return [
            _require(
                "state.has_spell_magic_zones != true",
                "Gate transit needs the genuine active magic-zone owner.",
            )
        ]
    return [
        effect
        for subject in ("{_gate_source.caster}", "{_gate_entry.id}", "{_gate_exit.id}")
        for effect in [
            _call(
                "check_srd_magic_zone_effect",
                {
                    "source_subject": "{_gate_source.caster}",
                    "target_subject": subject,
                    "effect_kind": "spell",
                    "actual_spell_level": "{_gate_source.slot_level}",
                },
                ["_magic_zone_effect_allowed"],
            ),
            _require(
                "_magic_zone_effect_allowed == true",
                "Actual magic suppression prevents this Gate effect.",
            ),
        ]
    ]


def _approach_node(tools, *, capture=False):
    """Retain final ordinary body/path/budget qualification through its mutation boundary."""
    fallback = _tool_scene(
        "Gate approach requires the final genuine ordinary movement owner.",
        [
            _entity("actor_state"),
            _array("destination"),
            _optional("mode", "enum", "walk", enum_values=["walk", "fly", "swim", "climb"]),
        ],
        [_require("false", "Gate approach needs final ordinary movement qualification.")],
    )
    if not capture:
        return fallback
    owner = tools.get("move_srd_scene_creature_environment_segment")
    if owner is None:
        raise ValueError("Final Gate approach requires the actual ordinary segment owner.")
    boundaries = [i for i, row in enumerate(owner["effects"]) if "spatial_mutation" in row]
    if len(boundaries) != 1:
        raise ValueError(
            "Final Gate approach must retain one canonical movement mutation boundary."
        )
    node = deepcopy(owner)
    node["effects"] = node["effects"][: boundaries[0]]
    node["description"] = (
        "Qualify and pay actual approach through final ordinary movement, "
        "without a second position mutation."
    )
    node["metadata"] = {"category": "internal", "visible_to_player": False, "costs_action": False}
    if not any("spend_combat_movement" in str(row) for row in node["effects"]):
        raise ValueError("Gate approach must retain actual ordinary movement costs.")
    return node


def gate_transit_tools(tools):
    """Allow only actual front approaches and genuinely nearest zero-distance disk arrivals."""
    if __package__:
        from .srd_spell_travel import ACTIVE, _move, _placement, _scene_subject
    else:
        from srd_spell_travel import ACTIVE, _move, _placement, _scene_subject
    transit = _public(
        _tool_scene(
            "Traverse a live Gate front with a real approach "
            "and clear exact on-disk arrival witness.",
            [
                _entity("actor_state"),
                _parameter("source_id"),
                _parameter("portal_id"),
                _array("entry_point"),
                _array("arrival_point"),
                _optional("mode", "enum", "walk", enum_values=["walk", "fly", "swim", "climb"]),
            ],
            [
                _require(ACTIVE, "A capable living creature must perform this actual movement."),
                _call("refresh_srd_gate_source", {"source_id": "{source_id}"}),
                *_pair(),
                *_magic(tools),
                *_scene_subject("{_actor_state_id}", "_gate_traveler"),
                _require(
                    "_gate_traveler.metadata.plane == _gate_entry.metadata.plane",
                    "Approach the actual portal on the creature's current plane.",
                ),
                *_fit("_gate_traveler", "entry_point", "_gate_entry"),
                _when(
                    "_gate_source.front == 'up'",
                    [
                        _require(
                            "_gate_traveler.position[2] > _gate_entry_plane and "
                            "entry_point[2] == _gate_entry_plane",
                            "Move the actual body from above into this upward front face.",
                        ),
                    ],
                ),
                _when(
                    "_gate_source.front == 'down'",
                    [
                        _require(
                            "_gate_traveler.position[2] + _gate_traveler.height < "
                            "_gate_entry_plane and "
                            "entry_point[2] + _gate_traveler.height == _gate_entry_plane",
                            "Move the actual body from below into this downward front face.",
                        ),
                    ],
                ),
                *_fit("_gate_traveler", "arrival_point", "_gate_exit"),
                _require(
                    "arrival_point[2] == _gate_exit_plane",
                    "Supported nearest arrival must have its actual origin "
                    "on the paired circular disk.",
                ),
                _set("_travel_excluded", []),
                *_placement("{_actor_state_id}", "{arrival_point}", "{_gate_exit.metadata.plane}"),
                _require(
                    "_travel_placement.distance == 0 and "
                    + " and ".join(
                        f"_travel_placement.position[{axis}] == arrival_point[{axis}]"
                        for axis in range(3)
                    ),
                    "The exact on-disk witness is occupied; "
                    "nonzero circular nearest placement remains unsupported.",
                ),
                _call(
                    "qualify_srd_gate_approach_node",
                    {
                        "actor_state": "{_actor_state_id}",
                        "destination": "{entry_point}",
                        "mode": "{mode}",
                    },
                    ["_route_cost", "_route_distance"],
                ),
                _set(
                    "_gate_transit_receipt",
                    {
                        "source_id": "source_id",
                        "cast_id": "_gate_source.cast_id",
                        "created_tick": "_gate_source.created_tick",
                        "sequence": "_gate_source.sequence",
                        "subject": "_gate_traveler.id",
                        "creature_id": "_actor_state_id",
                        "entry_id": "_gate_entry.id",
                        "exit_id": "_gate_exit.id",
                        "front": "_gate_source.front",
                        "before": "_gate_traveler.position",
                        "entry_point": "entry_point",
                        "arrival": "arrival_point",
                        "origin_plane": "_gate_entry.metadata.plane",
                        "destination_plane": "_gate_exit.metadata.plane",
                        "movement_cost": "_route_cost",
                        "movement_distance": "_route_distance",
                        "magical": True,
                        "planar": True,
                        "voluntary": True,
                    },
                ),
                _set("state.gate_transit_receipt", "_gate_transit_receipt"),
                *_move(
                    "{_actor_state_id}", "{arrival_point}", "{_gate_exit.metadata.plane}", False
                ),
                _set("state.gate_transit_receipt", None),
                _set("_gate_transited_source", "source_id"),
                _set("_gate_arrival_distance", 0),
            ],
        ),
        "movement",
        "Traverse Gate",
        [
            ("Gate", "_gate_transited_source"),
            ("Movement cost (feet)", "_route_cost"),
            ("Distance to actual circular disk", "_gate_arrival_distance"),
        ],
    )
    view = _public(
        _tool_scene(
            "Look through a live Gate front along its actual perpendicular circular axis.",
            [
                _entity("observer_state"),
                _parameter("source_id"),
                _parameter("portal_id"),
                _array("point"),
            ],
            [
                _call("refresh_srd_gate_source", {"source_id": "{source_id}"}),
                _call(
                    "resolve_srd_gate_view_node",
                    {
                        "observer_state": "{_observer_state_id}",
                        "source_id": "{source_id}",
                        "portal_id": "{portal_id}",
                        "point": "{point}",
                    },
                    ["_gate_view_can_see", "_gate_view_plane", "_gate_view_distance"],
                ),
            ],
        ),
        "information",
        "Look through Gate",
        [
            ("Can see actual vista", "_gate_view_can_see"),
            ("Vista plane", "_gate_view_plane"),
            ("Sight path (feet)", "_gate_view_distance"),
        ],
    )
    return {
        "traverse_srd_gate": transit,
        "qualify_srd_gate_approach_node": _approach_node(tools),
        "look_through_srd_gate": view,
        "resolve_srd_gate_view_node": _view_node(tools),
    }


def emit_late_gate_geometry(text):
    """Extract the final ordinary approach prefix after genuine movement guards are bound."""
    if __package__:
        from .srd_timed_actor_defenses import _load_runtime
        from .srd_timed_senses import _replace_tools
    else:
        from srd_timed_actor_defenses import _load_runtime
        from srd_timed_senses import _replace_tools
    tools = _load_runtime(text)["tools"]
    if "qualify_srd_gate_approach_node" not in tools:
        return text
    changed = {
        "qualify_srd_gate_approach_node": _approach_node(tools, capture=True),
        "resolve_srd_gate_view_node": _view_node(tools, capture=True),
    }
    for name in ("inspect_srd_gate", "inspect_srd_gate_node"):
        if name not in tools:
            continue
        reader = deepcopy(tools[name])
        for effect in reader["effects"]:
            if effect.get("set", {}).get("field") == "_gate_transit_supported":
                effect["set"]["value"] = True
        reader["description"] = (
            "Report actual Gate lifetime and supported bounded circular transit."
        )
        for row in reader["metadata"].get("result_card", {}).get("summary", []):
            if row.get("label") == "Transit supported":
                row["label"] = "Bounded circular transit supported"
        changed[name] = reader
    if all(tools[name] == tool for name, tool in changed.items()):
        return text
    return _replace_tools(text, changed)


def _view_node(tools, *, capture=False):
    """Keep final point sight behind a pure source-authenticated normal-axis portal ray."""
    parameters = [
        _entity("observer_state"),
        _parameter("source_id"),
        _parameter("portal_id"),
        _array("point"),
    ]
    if not capture:
        return _tool_scene(
            "Gate viewing needs the final actual point-sight owner.",
            parameters,
            [_require("false", "Gate viewing needs final source-qualified point sight.")],
        )
    owner = tools.get("resolve_srd_illumination_point_sight")
    if owner is None or "qualify_srd_travel_perception" not in tools:
        raise ValueError(
            "Final Gate view needs genuine point sight and its pure perception qualifier."
        )
    effects = deepcopy(owner["effects"])
    boundary = next(
        i
        for i, row in enumerate(effects)
        if row.get("set", {}).get("field") == "_illumination_point_ambient"
    )
    prefix = effects[:boundary]
    calls = [
        row
        for row in prefix
        if row.get("call") in {"resolve_srd_travel_perception", "qualify_srd_travel_perception"}
    ]
    if len(calls) != 1:
        raise ValueError("Gate sight must retain one actual pure perception boundary.")
    calls[0]["call"] = "qualify_srd_travel_perception"
    calls[0]["params"].update(point="{_gate_view_entry_point}", perception_plane="")
    tail = effects[boundary:]
    obstruction = next(
        i
        for i, row in enumerate(tail)
        if row.get("set", {}).get("field") == "_illumination_point_obstruction"
    )

    def near(value):
        if isinstance(value, list):
            return [near(item) for item in value]
        if isinstance(value, dict):
            return {key: near(item) for key, item in value.items()}
        if isinstance(value, str):
            return re.sub(
                r"\bpoint\b",
                "_gate_view_entry_point",
                value.replace("_illumination_point_", "_gate_near_point_"),
            )
        return value

    near_geometry = near(tail[:obstruction])
    combines = [
        _when("_gate_near_point_obscured == true", [_set("_illumination_point_obscured", True)]),
        _when("_gate_near_point_opaque == true", [_set("_illumination_point_opaque", True)]),
        _when(
            "_gate_near_point_total_cover == true", [_set("_illumination_point_total_cover", True)]
        ),
        _when(
            "_gate_near_point_lighting == 'magical_darkness'",
            [
                _call(
                    "resolve_srd_timed_senses",
                    {"target_state": "{_observer_state_id}"},
                    ["_sense_truesight"],
                ),
                _when(
                    "_sense_truesight < _illumination_point_distance",
                    [
                        _set("_illumination_point_obscured", True),
                    ],
                ),
            ],
        ),
    ]
    tail[obstruction:obstruction] = combines
    if "check_srd_illusion_visibility" in tools:
        tail.append(
            _when(
                "_illumination_point_can_see == true and state.has_illusion_spells == true",
                [
                    _call(
                        "check_srd_illusion_visibility",
                        {
                            "observer_state": "{_observer_state_id}",
                            "start": "{_gate_view_origin}",
                            "end": "{_gate_view_entry_point}",
                            "plane": "{_gate_entry.metadata.plane}",
                        },
                        ["_illusion_visual_blocked"],
                    ),
                    _when(
                        "_illusion_visual_blocked == true",
                        [
                            _set("_illumination_point_can_see", False),
                        ],
                    ),
                ],
            )
        )
    # Secure-area vision must inspect each actual segment, never a cross-plane chord.
    for index, row in enumerate(tail):
        if row.get("condition") == "lasting_state.has_spell_secure_areas == true":
            tail[index] = _when(
                "state.has_spell_secure_areas == true",
                [
                    *[
                        effect
                        for start, end, plane in (
                            (
                                "{_gate_view_origin}",
                                "{_gate_view_entry_point}",
                                "{_gate_entry.metadata.plane}",
                            ),
                            ("{_gate_view_exit_point}", "{point}", "{_gate_exit.metadata.plane}"),
                        )
                        for effect in [
                            _call(
                                "check_srd_secure_area_information",
                                {
                                    "start": start,
                                    "end": end,
                                    "plane": plane,
                                    "query_kind": "vision",
                                },
                                ["_secure_area_information_allowed"],
                            ),
                            _when(
                                "_secure_area_information_allowed != true and "
                                "(observer_state.blindsight_feet == null or "
                                "observer_state.blindsight_feet < "
                                "_illumination_point_distance or "
                                "_illumination_point_total_cover == true)",
                                [_set("_illumination_point_can_see", False)],
                            ),
                        ]
                    ],
                ],
            )
    from_plane = [
        _set("_gate_near_point_origin", "_gate_view_origin"),
        _set("_gate_near_point_plane", "_gate_entry.metadata.plane"),
        *near_geometry,
    ]
    return _tool_scene(
        "Resolve exact normal-axis front viewing through paired circles and final actual sight.",
        parameters,
        [
            *_pair(),
            *_magic(tools),
            _set(
                "_gate_view_entry_point",
                ["_gate_entry.position[0]", "_gate_entry.position[1]", "_gate_entry_plane"],
            ),
            _set(
                "_gate_view_exit_point",
                ["_gate_exit.position[0]", "_gate_exit.position[1]", "_gate_exit_plane"],
            ),
            *prefix,
            _require(
                "_illumination_point_plane == _gate_entry.metadata.plane",
                "The actual observer must be on this opening plane.",
            ),
            _set("_gate_view_origin", "_illumination_point_origin"),
            _require(
                "_gate_view_origin[0] == _gate_entry.position[0] and "
                "_gate_view_origin[1] == _gate_entry.position[1] and "
                "point[0] == _gate_exit.position[0] and point[1] == _gate_exit.position[1]",
                "Supported Gate viewing follows the actual perpendicular circular axis; "
                "off-axis views remain selected.",
            ),
            _when(
                "_gate_source.front == 'up'",
                [
                    _require(
                        "_gate_view_origin[2] > _gate_entry_plane and point[2] > _gate_exit_plane",
                        "Observe this upward front and the actual counterpart front-side vista.",
                    ),
                ],
            ),
            _when(
                "_gate_source.front == 'down'",
                [
                    _require(
                        "_gate_view_origin[2] < _gate_entry_plane and point[2] < _gate_exit_plane",
                        "Observe this downward front and the actual counterpart front-side vista.",
                    ),
                ],
            ),
            _call(
                "measure_srd_information_distance",
                {"start": "{_gate_view_origin}", "end": "{_gate_view_entry_point}"},
                ["_scene_distance"],
            ),
            _set("_gate_view_near_distance", "_scene_distance"),
            _call(
                "measure_srd_information_distance",
                {"start": "{_gate_view_exit_point}", "end": "{point}"},
                ["_scene_distance"],
            ),
            _set("_gate_view_distance", "_gate_view_near_distance + _scene_distance"),
            *from_plane,
            _set("_illumination_point_origin", "_gate_view_exit_point"),
            _set("_illumination_point_plane", "_gate_exit.metadata.plane"),
            _set("_illumination_point_distance", "_gate_view_distance"),
            *tail,
            _set("_gate_view_can_see", "_illumination_point_can_see == true"),
            _set("_gate_view_plane", "_gate_exit.metadata.plane"),
        ],
    )
