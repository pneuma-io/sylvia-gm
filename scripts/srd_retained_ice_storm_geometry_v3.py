"""Retained-SRD Ice Storm v3: current support within the frozen cast footprint.

Bind after existing scene/environment/ice-terrain and saved-action owners. No
profile/header selection or damage/expiry replacement occurs in this module.
Neutral Cylinder area overlap and existing solid-path sampling are authoritative.
Public native acceptance remains required after normal candidate admission.
"""
from copy import deepcopy
import re

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _tool_scene
    from .srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from .srd_spell_magic_zones import _when
    from .srd_timed_senses import _loop, _push, _replace_tools
else:
    from srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _tool_scene
    from srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from srd_spell_magic_zones import _when
    from srd_timed_senses import _loop, _push, _replace_tools

AREA = "cast_spell_instantaneous_area_save_composite_damage"
QUALIFY = "qualify_srd_retained_ice_storm_geometry"
INTERVALS = "collect_srd_retained_ice_storm_ground_intervals"
LIVING = "read_srd_retained_ice_storm_living_creature"
INSTALL = "retain_srd_ice_storm_ground"
CLIPS = "ice_storm_ground_intersections"
PUBLIC = ("cast_spell", "cast_spell_outside_combat", "begin_srd_spell_action")
IDENTITY = "ice_storm"


_PROJECTION_FIELDS = ("targets", "half_cover_targets", "three_quarters_cover_targets", "origin_distance")
_READ_TOKEN = re.compile(r"'[^']*'|\"[^\"]*\"|(?<![A-Za-z0-9_.])(?:three_quarters_cover_targets|half_cover_targets|origin_distance|targets)(?![A-Za-z0-9_])")


def _call(name, params, exports=()):
    # The accepted convenience helper accepts identity-name iterables only.
    # Renamed scene/interval outputs need an explicit preserved source mapping.
    result = {"call": name, "params": params}
    if exports:
        result["export_context"] = dict(exports) if isinstance(exports, dict) else {key: key for key in exports}
    return result


def _dispatch_defaults():
    # A bare null-valued expression becomes text in _resolve_value; retain actual
    # null with typed literals, then copy only non-null original intent values.
    effects = []
    for key in _PROJECTION_FIELDS:
        name = "_retained_ice_dispatch_" + key
        effects.append(_set(name, None))
        effects.append(dict(_set(name, key), condition=key + " != null"))
    return effects


def _derived_dispatch():
    return [_set("_retained_ice_dispatch_" + key, value) for key, value in zip(_PROJECTION_FIELDS,
        ("_retained_ice_targets", "_retained_ice_half_cover", "_retained_ice_three_quarters_cover", "ceil(_retained_ice_distance)"))]


def _consistency_checks():
    # Actual expressions do not implement list equality. Public schemas retain
    # unique_items, so equal cardinality plus membership gives exact fact sets.
    names = tuple(zip(_PROJECTION_FIELDS[:3], ("_retained_ice_targets", "_retained_ice_half_cover", "_retained_ice_three_quarters_cover")))
    return [_require(" and ".join("(" + key + " == null or len(" + key + ") == 0 or len(" + key + ") == len(" + derived + "))"
        for key, derived in names), "Supplied Ice Storm creature or cover cardinalities contradict the admitted scene."),
        *[_loop(key, "_retained_ice_supplied_fact", [_require("_retained_ice_supplied_fact in " + derived,
            "Supplied Ice Storm creature or cover membership contradicts the admitted scene.")]) for key, derived in names],
        _require("origin_distance == null or origin_distance == 0 or origin_distance == ceil(_retained_ice_distance)", "The supplied Ice Storm distance contradicts the admitted scene.")]


def _project_reads(value, key=None):
    """Replace only executable reads; preserve persistent paths and literal keys."""
    if isinstance(value, dict):
        return {k: _project_reads(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_project_reads(v, key) for v in value]
    if isinstance(value, str) and key not in {"field", "as", "call", "error", "name", "description"}:
        return _READ_TOKEN.sub(lambda m: "_retained_ice_dispatch_" + m.group(0)
            if m.group(0) in _PROJECTION_FIELDS else m.group(0), value)
    return value


def _lists(effects):
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if isinstance(effect.get(key), list):
                yield from _lists(effect[key])
        if "for_each" in effect:
            yield from _lists(effect["for_each"]["do"])


def _one(values, label):
    if len(values) != 1:
        raise ValueError(label + " must have one exact retained owner")
    return values[0]


def _clips_parameter():
    return _parameter(CLIPS, "array", items={"type": "object"}, max_items=256)


def _query(operation, query, store):
    return {"spatial_query": {"scene": "state.spatial_scene", "operation": operation,
        "snapshot": "{_info_snapshot}", "query": query}, "store_as": store}


def _qualifier():
    # These are temporary context fields only: every reject precedes retained
    # state, Action staging, damage dice, slot payment and new terrain installation.
    # Private defaults preserve Ice Storm's original volume and terrain. Flame
    # Strike shares creature qualification without creating hail or ground clips.
    return _tool_scene("Derive actual Cylinder creatures and solid-path cover, optionally retaining hail ground intersections.",
        [_entity("caster_state"), _array("origin"),
         _parameter("range_feet", "integer", required=False, default=300, minimum=1),
         _parameter("radius_feet", "integer", required=False, default=20, minimum=1),
         _parameter("height_feet", "integer", required=False, default=40, minimum=1),
         _parameter("retain_ground", "boolean", required=False, default=True)], [
        _call("srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS),
        _require("_info_scene_entity.metadata.plane != null", "Ice Storm needs the caster's actual admitted plane."),
        _set("_retained_ice_plane", "_info_scene_entity.metadata.plane"),
        _set("_retained_ice_caster_position", "_info_position"),
        _call("measure_srd_information_distance", {"start": "{_retained_ice_caster_position}", "end": "{origin}"},
            {"_retained_ice_distance": "_scene_distance"}),
        _require("_retained_ice_distance <= range_feet", "The actual Cylinder point exceeds this spell's range."),
        _call("srd_information_barriers", {"start": "{_retained_ice_caster_position}", "end": "{origin}",
            "plane": "{_retained_ice_plane}", "rule": "solid"}, ("_information_blocked",)),
        _require("_information_blocked != true", "The Ice Storm origin has no actual clear solid path."),
        _set("_retained_ice_cylinder", {"kind": "'cylinder'", "center": "origin", "radius": "radius_feet", "height": "height_feet"}),
        _set("_retained_ice_include", ["entities"]),
        _when("retain_ground == true", [_set("_retained_ice_include", ["entities", "geometry", "zones"])]),
        _query("area", {"map_id": "{state.spatial_scene.map.id}", "shape": {"kind": "cylinder",
            "center": "{origin}", "radius": "{radius_feet}", "height": "{height_feet}"},
            "include": "{_retained_ice_include}", "max_results": 4096}, "_retained_ice_area"),
        _require("_retained_ice_area.truncated != true", "Ice Storm's actual recipients exceed the supported scene query."),
        _set("_retained_ice_targets", []), _set("_retained_ice_seen_creatures", []), _set("_retained_ice_half_cover", []),
        _set("_retained_ice_three_quarters_cover", []), _set("_retained_ice_ground_intersections", []),
        _set("_retained_ice_results", "_retained_ice_area.results"),
        # Display zones carry no physical plane or ground qualification. Only
        # exact package-owned zone markers bypass the unchanged fact checks.
        _loop("_retained_ice_results", "_retained_ice_result", [_when(
            "_retained_ice_result.kind != 'zone' or "
            "(_retained_ice_result.metadata.srd_lite_retained_overlay != true and "
            "_retained_ice_result.metadata.srd_lite_pending_area_overlay != true)", [
            _require("_retained_ice_result.metadata.plane != null", "Intersecting Ice Storm scene facts need their actual plane."),
            _when("_retained_ice_result.metadata.plane == _retained_ice_plane", [
                _when("_retained_ice_result.kind == 'entity' and _retained_ice_result.metadata.is_object != true", [
                    _require("_retained_ice_result.metadata.participant_ref != null", "An actual Ice Storm creature needs its saved participant owner."),
                    _call("srd_information_scene_entity", {"subject": "{_retained_ice_result.id}"}, POSITION_EXPORTS),
                    _require("_info_scene_entity.footprint.kind == 'box' or _info_scene_entity.footprint.kind == 'circle'", "Cylinder physical-cover sampling needs an actual admitted creature body."),
                    _set("_retained_ice_target", "_retained_ice_result.metadata.participant_ref"),
                    _require("_retained_ice_target not in _retained_ice_seen_creatures", "One creature cannot have duplicate intersecting scene owners."),
                    _push("_retained_ice_seen_creatures", "_retained_ice_target"),
                    _call(LIVING, {"target_state": "{_retained_ice_target}"}, ("_retained_ice_living",)),
                    _when("_retained_ice_living == true", [
                    _query("exposure", {"map_id": "{state.spatial_scene.map.id}",
                        "observer": {"point": "{origin}"}, "target": {"entity_id": "{_retained_ice_result.id}"},
                        "samples": {"horizontal": 4, "vertical": 4}, "include": ["geometry"],
                        "metadata_filter": {"key": "plane", "value": "{_retained_ice_plane}", "missing_matches": False}}, "_retained_ice_exposure"),
                    _require("len(_retained_ice_exposure.samples) == 16", "Use the actual bounded physical-cover sample result."),
                    _set("_retained_ice_clear_samples", 0),
                    _set("_retained_ice_samples", "_retained_ice_exposure.samples"),
                    _loop("_retained_ice_samples", "_retained_ice_sample", [
                        _call("srd_information_barriers", {"start": "{origin}", "end": "{_retained_ice_sample.point}",
                            "plane": "{_retained_ice_plane}", "rule": "solid"}, ("_information_blocked",)),
                        _when("_information_blocked != true", [_set("_retained_ice_clear_samples", "_retained_ice_clear_samples + 1")]),
                    ]),
                    _when("_retained_ice_clear_samples > 0", [
                        _call("validate_spell_area_target", {"target": "{_retained_ice_target}",
                            "save_ability": "dexterity", "construct_save_disadvantage": False}),
                        _push("_retained_ice_targets", "_retained_ice_target"),
                        _when("_retained_ice_clear_samples <= 4", [_push("_retained_ice_three_quarters_cover", "_retained_ice_target")],
                            [_when("_retained_ice_clear_samples <= 8", [_push("_retained_ice_half_cover", "_retained_ice_target")])]),
                    ]),
                    ]),
                ]),
                _when("retain_ground == true and (_retained_ice_result.kind == 'geometry' or _retained_ice_result.kind == 'zone') and _retained_ice_result.metadata.ground == true", [
                    _set("_retained_ice_ground_shape", None),
                    _set("_retained_ice_ground_rows", "state.spatial_scene.zones"),
                    _when("_retained_ice_result.kind == 'geometry'", [_set("_retained_ice_ground_rows", "state.spatial_scene.geometry")]),
                    _loop("_retained_ice_ground_rows", "_retained_ice_ground_row", [
                        _when("_retained_ice_ground_row.id == _retained_ice_result.id", [
                            _require("_retained_ice_ground_shape == null", "Ground geometry identity must be unambiguous."),
                            _when("_retained_ice_result.kind == 'geometry'", [
                                _require("_retained_ice_ground_row.active == true and _retained_ice_ground_row.movement == 'blocking'", "Actual Ice Storm support must remain active solid ground."),
                                _set("_retained_ice_ground_shape", "_retained_ice_ground_row.volume"),
                            ], [_set("_retained_ice_ground_shape", "_retained_ice_ground_row.shape")]),
                        ]),
                    ]),
                    _require("_retained_ice_ground_shape != null and (_retained_ice_ground_shape.kind == 'box' or _retained_ice_ground_shape.kind == 'cylinder')", "Ground intersections require actual supported Box or Cylinder shapes."),
                    _push("_retained_ice_ground_intersections", {"ground_kind": "_retained_ice_result.kind",
                        "ground_id": "_retained_ice_result.id", "ground_volume": "_retained_ice_ground_shape",
                        "ice_volume": "_retained_ice_cylinder", "plane": "_retained_ice_plane"}),
                ]),
            ]),
        ])]),
        _require("len(_retained_ice_targets) <= 64 and len(_retained_ice_ground_intersections) <= 256", "Ice Storm exceeds its preserved recipient/ground bounds."),
        _require("retain_ground != true or state.turn_effects == null or len(state.turn_effects) < 128", "Resolve existing lasting effects before casting Ice Storm."),
    ])


def _current_support_effects():
    # Re-resolve the original kind/ID from the current admitted scene. Zones have
    # no active field in the neutral schema: deletion, plane and ground metadata
    # determine whether they are still support. Geometry must be active/solid.
    return [
        _require("_retained_ice_clip.ground_kind == 'geometry' or _retained_ice_clip.ground_kind == 'zone'", "Saved hail support needs its original admitted kind."),
        _set("_retained_ice_current_support", None),
        _set("_retained_ice_support_rows", "state.spatial_scene.zones"),
        _when("_retained_ice_clip.ground_kind == 'geometry'", [_set("_retained_ice_support_rows", "state.spatial_scene.geometry")]),
        _loop("_retained_ice_support_rows", "_retained_ice_support_row", [
            _when("_retained_ice_support_row.id == _retained_ice_clip.ground_id and _retained_ice_support_row.map_id == state.spatial_scene.map.id and _retained_ice_support_row.metadata.plane == plane and _retained_ice_support_row.metadata.ground == true", [
                _when("_retained_ice_clip.ground_kind == 'geometry'", [
                    _when("_retained_ice_support_row.active == true and _retained_ice_support_row.movement == 'blocking'", [
                        _set("_retained_ice_current_support", "_retained_ice_support_row.volume"),
                    ]),
                ], [_set("_retained_ice_current_support", "_retained_ice_support_row.shape")]),
            ]),
        ]),
    ]


def _interval_tool():
    return _tool_scene("Intersect surviving current support, its original hail footprint and the fixed Cylinder on the current route.",
        [_array("start"), _array("end"), _parameter("plane")], _snapshot() + [
        _set("_retained_ice_route_intervals", []),
        _set("_retained_ice_route_records", "state.turn_effects"),
        _loop("_retained_ice_route_records", "_retained_ice_route_record", [
            _when("_retained_ice_route_record.kind == 'ice_storm_terrain' and _retained_ice_route_record.plane == plane and _retained_ice_route_record.ground_intersections != null", [
                _set("_retained_ice_route_clips", "_retained_ice_route_record.ground_intersections"),
                _loop("_retained_ice_route_clips", "_retained_ice_clip", [
                    *_current_support_effects(),
                    _when("_retained_ice_current_support != null", [
                        _require("_retained_ice_current_support.kind == 'box' or _retained_ice_current_support.kind == 'cylinder'", "Current hail support needs its actual supported Box or Cylinder."),
                        _call("srd_information_segment_interval", {"start": "{start}", "end": "{end}", "volume": "{_retained_ice_current_support}"},
                            {"_retained_ice_current_hit": "_segment_hit", "_retained_ice_current_entry": "_segment_entry", "_retained_ice_current_exit": "_segment_exit"}),
                        _when("_retained_ice_current_hit == true", [
                            _call("srd_information_segment_interval", {"start": "{start}", "end": "{end}", "volume": "{_retained_ice_clip.ground_volume}"},
                                {"_retained_ice_ground_hit": "_segment_hit", "_retained_ice_ground_entry": "_segment_entry", "_retained_ice_ground_exit": "_segment_exit"}),
                            _call("srd_information_segment_interval", {"start": "{start}", "end": "{end}", "volume": "{_retained_ice_clip.ice_volume}"},
                                {"_retained_ice_cylinder_hit": "_segment_hit", "_retained_ice_cylinder_entry": "_segment_entry", "_retained_ice_cylinder_exit": "_segment_exit"}),
                            _when("_retained_ice_ground_hit == true and _retained_ice_cylinder_hit == true and max(_retained_ice_current_entry, _retained_ice_ground_entry, _retained_ice_cylinder_entry) < min(_retained_ice_current_exit, _retained_ice_ground_exit, _retained_ice_cylinder_exit)", [
                                _push("_retained_ice_route_intervals", {"entry": "max(_retained_ice_current_entry, _retained_ice_ground_entry, _retained_ice_cylinder_entry)",
                                    "exit": "min(_retained_ice_current_exit, _retained_ice_ground_exit, _retained_ice_cylinder_exit)"}),
                            ]),
                        ]),
                    ]),
                ]),
            ]),
        ]),
    ])


def stage_runtime(runtime):
    """Mutate only owned tools in the supplied isolated runtime; keep definitions frozen."""
    tools = runtime["tools"]
    required = {AREA, INSTALL, "srd_information_movement_cost", "srd_information_barriers",
        "srd_information_scene_entity", "measure_srd_information_distance", "srd_information_segment_interval",
        "validate_spell_area_target", *PUBLIC}
    if missing := required - tools.keys():
        raise ValueError("Ice Storm needs actual retained owners: " + str(sorted(missing)))
    if {QUALIFY, INTERVALS, LIVING} & tools.keys():
        raise ValueError("Ice Storm geometry v3 is already bound; preserve final owner order")
    profile = runtime["definitions"]["spells"][IDENTITY]
    expected = {"family": "instantaneous_area_save_composite_damage", "range_feet": 300,
        "area_shape": "cylinder", "area_radius_feet": 20, "area_height_feet": 40,
        "component_1_base_dice": 2, "component_1_damage_die": "d10", "component_1_damage_type": "bludgeoning",
        "component_2_base_dice": 4, "component_2_damage_die": "d6", "component_2_damage_type": "cold"}
    if any(profile.get(k) != v for k, v in expected.items()):
        raise ValueError("Ice Storm no longer has the immutable retained SRD profile")
    origin = deepcopy(_one([p for p in tools["cast_spell"]["parameters"] if p["name"] == "environment_origin"], "Actual public chosen-point declaration"))
    changed = {QUALIFY: _qualifier(), INTERVALS: _interval_tool(),
        LIVING: _tool_scene("Read the actual saved creature life flag without a save or damage roll.",
            [_entity("target_state")], [_set("_retained_ice_living", "target_state.is_alive == true")])}

    install = deepcopy(tools[INSTALL])
    install["parameters"].append(_clips_parameter())
    # Replace the old trusted whole-zone validation, retaining its existing
    # actor/plane, bounded lasting-record and expiry/calculation owners.
    old_ground_loop = _one([e for e in install["effects"] if e.get("for_each", {}).get("collection") == "ice_storm_ground_zone_ids"], "Old whole-zone intent validator")
    install["effects"].remove(old_ground_loop)
    install["effects"].insert(0, _require("len(ice_storm_ground_zone_ids) == 0 and len(" + CLIPS + ") > 0", "Retained hail uses scene-derived ground intersections, never whole-zone IDs."))
    record = _one([e["set"]["value"] for rows in _lists(install["effects"]) for e in rows
        if e.get("set", {}).get("field") == "_ice_storm_ground_record"], "Existing next-caster-turn record")
    record["ground_intersections"] = CLIPS
    changed[INSTALL] = install

    area = deepcopy(tools[AREA])
    area["parameters"].append(origin)
    install_call = _one([e for rows in _lists(area["effects"]) for e in rows if e.get("call") == INSTALL], "Actual typed-damage terrain consequence")
    install_call["condition"] = "spell == 'ice_storm' and len(_retained_ice_ground_intersections) > 0"
    install_call["params"]["ice_storm_ground_zone_ids"] = []
    install_call["params"][CLIPS] = "{_retained_ice_ground_intersections}"
    area["effects"].insert(0, _when("spell == 'ice_storm'", [
        _require("environment_origin != null and len(environment_origin) == 3", "Ice Storm requires its actual chosen Cylinder point."),
        _call(QUALIFY, {"caster_state": "{_caster_state_id}", "origin": "{environment_origin}"},
            ("_retained_ice_targets", "_retained_ice_half_cover", "_retained_ice_three_quarters_cover",
             "_retained_ice_distance", "_retained_ice_ground_intersections")),
        *_derived_dispatch(),
    ]))
    # Project only executable read expressions/templates after the guard; all
    # schema keys, saved write paths and quoted intent keys remain literal.
    area["effects"][1:] = _project_reads(area["effects"][1:])
    area["effects"][0:0] = _dispatch_defaults()
    changed[AREA] = area

    # The pending-action owner pays/stages before cast resolution, so preflight
    # both public cast entry and real Action staging. Exploration inherits its
    # existing cast caller. Reentrant qualification retains no spell state.
    for name in ("cast_spell", "begin_srd_spell_action"):
        tool = deepcopy(tools[name])
        names = {p["name"] for p in tool["parameters"]}
        needed = {"spell", "caster_state", "environment_origin", "targets", "half_cover_targets",
            "three_quarters_cover_targets", "ice_storm_ground_zone_ids", "area_object_targets", "origin_distance"}
        if not needed <= names:
            raise ValueError(name + " lost actual public Ice Storm intent fields")
        tool["effects"].insert(0, _when("spell == 'ice_storm'", [
            _require("environment_origin != null and len(environment_origin) == 3", "Ice Storm requires its actual admitted Cylinder point."),
            _require("len(ice_storm_ground_zone_ids) == 0 and len(area_object_targets) == 0", "Ice Storm derives clipped ground and creature recipients; whole-zone and object lists contradict this source."),
            _call(QUALIFY, {"caster_state": "{_caster_state_id}", "origin": "{environment_origin}"},
                ("_retained_ice_distance", "_retained_ice_targets", "_retained_ice_half_cover", "_retained_ice_three_quarters_cover")),
            *_consistency_checks(),
            *_derived_dispatch(),
        ]))
        for rows in _lists(tool["effects"]):
            for effect in rows:
                if effect.get("call") == AREA:
                    effect["params"]["environment_origin"] = "{environment_origin}"
        tool["effects"][1:] = _project_reads(tool["effects"][1:])
        tool["effects"][0:0] = _dispatch_defaults()
        changed[name] = tool

    outside_effects = tools["cast_spell_outside_combat"]["effects"]
    if (len(outside_effects) != 2 or outside_effects[0].get("call") != "cast_spell"
        or outside_effects[1].get("condition") != "lasting_state.has_tsunami_pending_exits == true and (lasting_state.pending_srd_action_stack == null or len(lasting_state.pending_srd_action_stack) == 0)"):
        raise ValueError("Exploration owner changed; re-audit pre-spend delegation")
    for key in _PROJECTION_FIELDS:
        if outside_effects[0]["params"].get(key) != "{" + key + "}":
            raise ValueError("Exploration forwarding changed: " + key)

    cost = deepcopy(tools["srd_information_movement_cost"])
    cuts = _one([e for e in cost["effects"] if e.get("set", {}).get("field") == "_route_cuts"], "Actual route cut initializer")
    i = cost["effects"].index(cuts) + 1
    cost["effects"][i:i] = [_set("_retained_ice_route_intervals", []),
        _when("lasting_state.has_ice_storm_terrain == true and lasting_state.turn_effects != null and movement_mode == 'walk' and ignore_ground_environments != true", [
            _call(INTERVALS, {"start": "{start}", "end": "{end}", "plane": "{plane}"}, ("_retained_ice_route_intervals",)),
            _loop("_retained_ice_route_intervals", "_retained_ice_route_piece", [
                _push("_route_cuts", {"t": "_retained_ice_route_piece.entry"}),
                _push("_route_cuts", {"t": "_retained_ice_route_piece.exit"}),
            ]),
        ])]
    piece_rows = _one([rows for rows in _lists(cost["effects"])
        if any(e.get("set", {}).get("field") == "_route_piece_cost" for e in rows)
        and any(e.get("set", {}).get("field") == "_route_mid" for e in rows)], "Actual nonstacking movement piece")
    i = next(i for i, e in enumerate(piece_rows) if e.get("set", {}).get("field") == "_route_piece_cost")
    piece_rows[i:i] = [_loop("_retained_ice_route_intervals", "_retained_ice_route_piece", [
        _when("_route_mid > _retained_ice_route_piece.entry and _route_mid < _retained_ice_route_piece.exit", [
            _set("_route_piece_difficult", True), _set("_route_nonplant_difficult", True),
        ])])]
    changed["srd_information_movement_cost"] = cost
    tools.update(changed)
    return changed


def bind_runtime_consumers(text):
    runtime = _load_runtime(text)
    changed = stage_runtime(runtime)
    return _replace_tools(text, changed)
