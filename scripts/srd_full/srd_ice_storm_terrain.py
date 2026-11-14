"""Ice Storm's hail uses admitted ground zones and the native next-turn effect lifetime."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS, _tool_scene
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from .srd_timed_senses import _loop, _push, _replace_tools
else:
    from srd_scene_relations import POSITION_EXPORTS, _tool_scene
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from srd_timed_senses import _loop, _push, _replace_tools

AREA = "cast_spell_instantaneous_area_save_composite_damage"
GROUND = "ice_storm_ground_zone_ids"
INSTALL = "retain_srd_ice_storm_ground"


def _intent():
    return _parameter(
        GROUND,
        "array",
        required=False,
        default=[],
        items={"type": "string"},
        max_items=64,
        unique_items=True,
        description="Actual ground zones the GM identifies as affected by Ice Storm's Cylinder.",
    )


def _lists(effects):
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if isinstance(effect.get(key), list):
                yield from _lists(effect[key])
        if "for_each" in effect:
            yield from _lists(effect["for_each"]["do"])


def _ground_tool():
    """Retain a paid hail consequence without changing ordinary terrain or caster Concentration."""
    return _tool_scene(
        "Retain GM-selected actual ground until the end of the caster's next turn.",
        [_entity("caster_state"), _intent()],
        [
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _require(
                "_info_scene_entity.metadata.plane != null", "Record the caster's actual plane."
            ),
            _set("_ice_storm_plane", "_info_scene_entity.metadata.plane"),
            _set("_ice_storm_zones", "state.spatial_scene.zones"),
            _loop(
                GROUND,
                "_ice_storm_ground_id",
                [
                    _set("_ice_storm_ground_found", False),
                    _loop(
                        "_ice_storm_zones",
                        "_ice_storm_zone",
                        [
                            _when(
                                "_ice_storm_zone.id == _ice_storm_ground_id",
                                [
                                    _require(
                                        "_ice_storm_zone.metadata.ground == true and "
                                        "_ice_storm_zone.metadata.plane == _ice_storm_plane",
                                    "Ice Storm terrain needs an admitted ground zone "
                                    "on the caster's plane.",
                                    ),
                                    _set("_ice_storm_ground_found", True),
                                ],
                            ),
                        ],
                    ),
                    _require(
                        "_ice_storm_ground_found == true",
                        "The selected Ice Storm ground zone is absent.",
                    ),
                ],
            ),
            _when("state.turn_effects == null", [_set("state.turn_effects", [])]),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _when("caster_state.turns_started == null", [_set("caster_state.turns_started", 0)]),
            _require("len(state.turn_effects) < 128", "Resolve existing lasting effects first."),
            _set(
                "_ice_storm_ground_record",
                {
                    "kind": "'ice_storm_terrain'",
                    "spell": "'ice_storm'",
                    "label": "'Ice Storm hail'",
                    "caster": "_caster_state_id",
                    "target": "_caster_state_id",
                    "owner": "_caster_state_id",
                    "phase": "'end'",
                    "turn": "caster_state.turns_started + 1",
                    "started_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + 2",
                    "plane": "_ice_storm_plane",
                    "ground_zone_ids": GROUND,
                    "magnitude": 0,
                    "dice_count": 0,
                    "damage_die": "''",
                    "damage_type": "''",
                    "dispel_ongoing": False,
                },
            ),
            _set("_ice_storm_ground_records", "state.turn_effects"),
            _push("_ice_storm_ground_records", "_ice_storm_ground_record"),
            _set("state.turn_effects", "_ice_storm_ground_records"),
            _set("state.has_ice_storm_terrain", True),
        ],
    )


def bind_runtime_consumers(text):
    """Bind native payment/area casts and the existing nonstacking terrain-cost owner."""
    tools = _load_runtime(text)["tools"]
    changed = {INSTALL: _ground_tool()}
    if AREA not in tools:
        raise ValueError("Ice Storm lost its native composite-area owner.")
    area = deepcopy(tools[AREA])
    if GROUND not in {p["name"] for p in area["parameters"]}:
        area["parameters"].append(_intent())
    if INSTALL not in str(area["effects"]):
        area["effects"].insert(
            0,
            _require(
                f"spell == 'ice_storm' or len({GROUND}) == 0",
                "Only Ice Storm retains these hail-covered ground zones.",
            ),
        )
        area["effects"].append(
            _when(
                f"spell == 'ice_storm' and len({GROUND}) > 0",
                [
                    _call(
                        INSTALL, {"caster_state": "{_caster_state_id}", GROUND: "{" + GROUND + "}"}
                    ),
                ],
            )
        )
    changed[AREA] = area

    # Forward current public/exploration intent; later saved clones inherit it.
    owners = {AREA}
    progress = True
    while progress:
        progress = False
        for name, original in tools.items():
            if name in owners:
                continue
            if any(
                e.get("call") in owners
                for rows in _lists(original.get("effects", []))
                for e in rows
            ):
                owners.add(name)
                progress = True
    for name in owners - {AREA}:
        tool = deepcopy(tools[name])
        if GROUND not in {p["name"] for p in tool["parameters"]}:
            tool["parameters"].append(_intent())
        for rows in _lists(tool["effects"]):
            for effect in rows:
                if effect.get("call") in owners:
                    effect.setdefault("params", {})[GROUND] = "{" + GROUND + "}"
        changed[name] = tool

    cost = deepcopy(tools["srd_information_movement_cost"])
    if "_ice_storm_route_zone_ids" not in str(cost["effects"]):
        cost["effects"][:0] = [
            _set("_ice_storm_route_zone_ids", []),
            _when(
                "lasting_state.has_ice_storm_terrain == true and "
                "lasting_state.turn_effects != null "
                "and ignore_ground_environments != true",
                [
                    _set("_ice_storm_route_records", "lasting_state.turn_effects"),
                    _loop(
                        "_ice_storm_route_records",
                        "_ice_storm_route_record",
                        [
                            _when(
                                "_ice_storm_route_record.kind == 'ice_storm_terrain' and "
                                "_ice_storm_route_record.plane == plane",
                                [
                                    _set(
                                        "_ice_storm_route_ids",
                                        "_ice_storm_route_record.ground_zone_ids",
                                    ),
                                    _loop(
                                        "_ice_storm_route_ids",
                                        "_ice_storm_route_id",
                                        [
                                            _when(
                                                "_ice_storm_route_id not in "
                                                "_ice_storm_route_zone_ids",
                                                [
                                                    _push(
                                                        "_ice_storm_route_zone_ids",
                                                        "_ice_storm_route_id",
                                                    ),
                                                ],
                                            ),
                                        ],
                                    )
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ]
        native_zone = "_route_zone.metadata.difficult == true"
        mirage_zone = native_zone + (
            " and (_mirage_ground_clear != true or "
            "_route_zone.metadata.ground_environment == true or "
            "_route_zone.metadata.source_spell != null or "
            "_route_zone.metadata.source_environment_id != null or "
            "_route_zone.metadata.plant_growth == true)"
        )
        matched = 0
        for rows in _lists(cost["effects"]):
            for effect in rows:
                if effect.get("condition") in {native_zone, mirage_zone}:
                    effect["condition"] = (
                        "(" + effect["condition"] + ") or "
                        "_route_zone.id in _ice_storm_route_zone_ids"
                    )
                    matched += 1
        if matched != 1:
            raise ValueError("Ice Storm lost the actual admitted-zone terrain-cost branch.")
    changed["srd_information_movement_cost"] = cost
    return _replace_tools(text, changed)
