"""Run printed Thaumaturgy exploration effects against current fiction and retained objects."""

from copy import deepcopy

from .srd_control_behaviors import _lasting
from .srd_lite_exploration_objects import (
    LOAD,
    SCENE,
    SCOPE,
    _load,
    _save_body,
    _without_scene,
)
from .srd_lite_profiles import lists
from .srd_timed_actor_defenses import _call, _require, _set, _tool, _when
from .srd_timed_senses import _optional, _push

OWNER = "cast_spell_minor_world_spell"
CAST = "cast_srd_lite_exploration_thaumaturgy"
QUALIFY = "qualify_srd_lite_exploration_thaumaturgy"
DOOR = "apply_srd_lite_exploration_thaumaturgy_door"
SUBJECT = "record_srd_minor_world_subject"
SUBJECT_NODE = SUBJECT + "_node"
SUBJECT_EXPLORE = "lite_exploration_" + SUBJECT_NODE
PLANE = "minor_same_plane"
GUIDANCE = (
    " Outside combat, Thaumaturgy uses current established origin_distance, "
    "origin_has_clear_path and minor_same_plane facts for remote wonders, flames and "
    "doors/windows, even when an old combat map is saved. Use the exact admitted "
    "minor_object_id and existing subject/access/flame facts. Altered Eyes and Booming "
    "Voice affect the actual caster. No exploration coordinates are inferred."
)


def _index(rows, field):
    found = [i for i, row in enumerate(rows) if row.get("set", {}).get("field") == field]
    if len(found) != 1:
        raise ValueError("Preserve the actual minor world owner boundary: " + field)
    return found[0]


def _frame_save(patch):
    """Keep native access changes in the shared object pool until actual scene admission."""
    if set(patch) != {"metadata", "movement", "visibility"}:
        raise ValueError("Only the native door access frame can be adapted")
    if patch["metadata"] != "{_world_metadata}":
        raise ValueError("Preserve the native door metadata producer")
    return [
        _set(
            "_thau_frame",
            {"movement": "_minor_movement", "visibility": "_minor_visibility"},
        ),
        _set("_thau_metadata", {"metadata": "_world_metadata"}),
        _set("_thau_body", "merge(_object_scene, _thau_metadata)"),
        _set("_thau_body", "merge(_thau_body, _thau_frame)"),
        _set("_thau_changes", "merge(_lite_current_patch, _world_changes)"),
        *_save_body("_thau_body", changes="_thau_changes", frame_changes="_thau_frame"),
        _set("_object_scene", "_thau_body"),
    ]


def _scene_free(rows):
    """Adapt selected native Thaumaturgy consequences; retain every non-spatial guard."""
    output = []
    for original in rows:
        row = deepcopy(original)
        if row.get("call") == "resolve_srd_object_content_geometry":
            params = row["params"]
            if params.get("requires_sight") is not False or params.get("object_id") not in (
                "{minor_object_id}",
                "{object_id}",
            ):
                raise ValueError("Preserve the printed Thaumaturgy object boundary")
            output.append(_load(params["object_id"]))
            continue
        if row.get("call") == "apply_srd_minor_world_door":
            row["call"] = DOOR
        if row.get("spatial_query", {}).get("operation") == "snapshot":
            continue
        if row.get("require", {}).get("condition") in (
            SCENE + " != null",
            SCENE + ".map.grid.unit == 'ft'",
        ):
            continue
        if row.get("condition") == SCENE + " == null":
            output.extend(_scene_free(row["on_true"]))
            continue
        if "spatial_mutation" in row:
            mutation = row["spatial_mutation"]
            if mutation["target"] != {"kind": "entity", "id": "{object_id}"}:
                raise ValueError("Keep the exact native door target")
            output.extend(_frame_save(mutation["patch"]))
            continue
        if "spatial_query" in row or row.get("call") in (
            "srd_information_scene_entity",
            "resolve_srd_object_content_point",
        ):
            raise ValueError("Do not bypass another geometry owner")
        field = row.get("set", {}).get("field", "")
        if field in ("_world_origin", "_world_plane", "_world_lighting"):
            row["set"]["value"] = None
        if field in ("_minor_source", "lasting_state.srd_last_minor_world_event") and isinstance(
            row["set"]["value"], dict
        ):
            # Explicit nulls avoid the evaluator's unresolved-reference literal fallback.
            row["set"]["value"].update(origin=None, plane=None, geometry=None)
        for branch in ("on_true", "on_false"):
            if branch in row:
                row[branch] = _scene_free(row[branch])
        if "for_each" in row:
            loop = row["for_each"]
            if "." in loop["collection"] or "[" in loop["collection"]:
                raise ValueError("Selected native Thaumaturgy loops must use locals")
            loop["do"] = _scene_free(loop["do"])
        output.append(row)
    return output


def _fresh_facts():
    return [
        _require(SCOPE, "Exploration Thaumaturgy cannot bypass actual combat positions."),
        _require("spell == 'thaumaturgy'", "Use the selected printed Thaumaturgy contract."),
        _when(
            "minor_mode != 'altered_eyes' and minor_mode != 'booming_voice'",
            [
                _require(
                    "origin_distance != null and origin_distance >= 0 and "
                    "origin_distance <= _minor_profile.range_feet and "
                    "origin_has_clear_path == true and " + PLANE + " == true",
                    "Establish current Thaumaturgy distance within thirty feet, a clear "
                    "spell path and the shared plane.",
                ),
            ],
        ),
    ]


def _native_parts(runtime):
    native = runtime["tools"][OWNER]["effects"]
    world = _index(native, "_world_origin")
    subject = _index(native, "_minor_subject_id")
    hashes = _index(native, "_minor_source_hashes")
    if subject <= world or hashes <= subject:
        raise ValueError("Preserve actual minor-world initialization order")
    modes = {
        mode: next(row for row in native if row.get("condition") == "minor_mode == '" + mode + "'")
        for mode in ("fire_play", "invisible_hand")
    }
    ongoing = [row for row in native if "minor_mode == 'altered_eyes'" in row.get("condition", "")]
    summaries = [
        i
        for i, row in enumerate(native)
        if row.get("list_push", {}).get("list") == "_spell_summary_rows"
        and row["list_push"].get("value", {}).get("label") == "minor_mode"
    ]
    if len(ongoing) != 1 or len(summaries) != 1:
        raise ValueError("Preserve native Thaumaturgy timing and card feedback")
    return native, world, subject, hashes, modes, ongoing[0], summaries[0]


def _fire_arm(modes):
    rows = modes["fire_play"]["on_true"]
    sites = [i for i, row in enumerate(rows) if row.get("condition") == "spell == 'thaumaturgy'"]
    if len(sites) != 1 or sites[0] != len(rows) - 1:
        raise ValueError("Preserve the native printed Thaumaturgy Fire Play arm")
    at = sites[0]
    return _scene_free([*rows[:at], *rows[at]["on_true"]])


def _qualifier(runtime, parts):
    native, world, _, _, modes, _, _ = parts
    fire = _fire_arm(modes)
    # Fire Play's native retained source is created only by the paid owner.
    fire = fire[: _index(fire, "_minor_expires_tick")]
    invisible = _scene_free(modes["invisible_hand"]["on_true"])
    if invisible[-1].get("call") != DOOR:
        raise ValueError("Preserve the actual Invisible Hand delivery")
    door = _scene_free(runtime["tools"]["apply_srd_minor_world_door"]["effects"])
    door = door[: _index(door, "_minor_access_change")]
    return _tool(
        "Qualify current Thaumaturgy exploration facts and native object eligibility before costs.",
        deepcopy(runtime["tools"][OWNER]["parameters"]),
        [
            *deepcopy(native[:world]),
            *_fresh_facts(),
            _when("minor_mode == 'fire_play'", fire),
            _when("minor_mode == 'invisible_hand'", invisible[:-1] + door),
        ],
    )


def _cast(runtime, parts):
    native, world, subject, hashes, modes, ongoing, summary = parts
    # The only skipped initialization arm resolves a coordinate point. Fresh fiction
    # owns that separation outside combat; all source IDs and clocks remain native.
    initial = native[subject:hashes]
    point_arms = [row for row in initial if "on_true" in row]
    if len(point_arms) != 1 or "resolve_srd_object_content_point" not in str(point_arms[0]):
        raise ValueError("Preserve the actual minor-world point resolution boundary")
    before_modes = native[
        hashes : next(
            i
            for i, row in enumerate(native)
            if row.get("condition") == "minor_mode == 'weather_sensor'"
        )
    ]
    fire = _fire_arm(modes)
    at = _index(fire, "_minor_expires_tick")
    fire[at:at] = _save_body("_object_scene")
    return _tool(
        "Commit printed paid Thaumaturgy using retained exploration object facts.",
        deepcopy(runtime["tools"][OWNER]["parameters"]),
        [
            *deepcopy(native[:world]),
            *_fresh_facts(),
            _set("_world_origin", None),
            _set("_world_plane", None),
            _set("_world_lighting", None),
            *[deepcopy(row) for row in initial if row not in point_arms],
            *deepcopy(before_modes),
            _when("minor_mode == 'fire_play'", fire),
            _scene_free([ongoing])[0],
            _scene_free([modes["invisible_hand"]])[0],
            *_scene_free(native[summary:]),
            _when(
                "minor_mode == 'fire_play' or minor_mode == 'invisible_hand'",
                [
                    _push("_spell_detail_rows", {"label": "'Object'", "value": "minor_object_id"}),
                    _push(
                        "_spell_detail_rows", {"label": "'Operation'", "value": "minor_operation"}
                    ),
                ],
            ),
            _when(
                "minor_mode == 'fire_play' and minor_operation == 'color' and minor_color != ''",
                [_push("_spell_detail_rows", {"label": "'Flame color'", "value": "minor_color"})],
            ),
            _when(
                "minor_mode != 'invisible_hand' and minor_mode != 'phantom_sound'",
                [
                    _push("_spell_detail_rows", {"label": "'Source'", "value": "_minor_source_id"}),
                    _push("_spell_detail_rows", {"label": "'Duration (rounds)'", "value": 10}),
                ],
            ),
        ],
    )


def _qualify_call(identity="spell"):
    return _call(
        QUALIFY,
        {
            "caster_state": "{_caster_state_id}",
            "spell": "{" + identity + "}",
            "object_id": "{minor_object_id}",
            "minor_object_id": "{minor_object_id}",
            "minor_mode": "{minor_mode}",
            "minor_operation": "{minor_operation}",
            "origin_distance": "{origin_distance}",
            "origin_has_clear_path": "{origin_has_clear_path}",
            PLANE: "{" + PLANE + "}",
        },
    )


def _bind_transport(runtime):
    """Forward one typed fresh plane fact through the existing normal/staged capsules."""
    tools = runtime["tools"]
    changed = []
    for name, owner in tools.items():
        if any(p["name"] == "minor_mode" for p in owner["parameters"]):
            if any(p["name"] == PLANE for p in owner["parameters"]):
                continue  # The two newly created helpers already copy the declaration.
            owner["parameters"].append(_optional(PLANE, "boolean", False))
            changed.append(name)
    for _name, owner in tools.items():
        for group in list(lists(owner["effects"])):
            expanded = []
            for row in group:
                setting = row.get("set", {})
                value = setting.get("value")
                for capsule in value if isinstance(value, list) else [value]:
                    if isinstance(capsule, dict) and "minor_mode" in capsule:
                        capsule[PLANE] = PLANE
                params = row.get("params", {})
                target = tools.get(row.get("call"), {})
                if "minor_mode" in params and any(
                    p["name"] == PLANE for p in target.get("parameters", [])
                ):
                    mode = params["minor_mode"]
                    params[PLANE] = (
                        mode.replace("minor_mode", PLANE)
                        if isinstance(mode, str) and "minor_mode" in mode
                        else False
                    )
                expanded.append(row)
                field = setting.get("field", "")
                if field.endswith(".parameters.minor_mode"):
                    copied = deepcopy(row)
                    copied["set"]["field"] = field.removesuffix("minor_mode") + PLANE
                    copied["set"]["value"] = (
                        value.replace("minor_mode", PLANE)
                        if isinstance(value, str) and "minor_mode" in value
                        else False
                    )
                    expanded.append(copied)
            group[:] = expanded
    for name in ("cast_spell", "begin_srd_spell_action"):
        owner = tools[name]
        sites = [
            (group, i)
            for group in lists(owner["effects"])
            for i, row in enumerate(group)
            if row.get("set", {}).get("field") == "_casting_profile"
            or row.get("store_as") == "_casting_profile"
        ]
        if not sites:
            raise ValueError("Preserve the actual casting-profile preflight: " + name)
        identity = (
            "_ms"
            if any(
                row.get("set", {}).get("field") == "_ms"
                for group in lists(owner["effects"])
                for row in group
            )
            else "spell"
        )
        for group, i in reversed(sites):
            group.insert(
                i + 1,
                _when(SCOPE + " and " + identity + " == 'thaumaturgy'", [_qualify_call(identity)]),
            )
    for name in changed:
        if name in (
            "cast_spell",
            "cast_spell_node",
            "cast_spell_outside_combat",
            "begin_srd_spell_action",
        ):
            tools[name]["description"] += GUIDANCE
    return changed


def bind_thaumaturgy_exploration(runtime):
    """Bind after exploration objects and Mage Hand, before private pruning/schema refresh."""
    tools = runtime["tools"]
    if CAST in tools:
        raise ValueError("Thaumaturgy exploration was already bound")
    for required in (
        LOAD,
        OWNER,
        "apply_srd_minor_world_door",
        SUBJECT,
        "lite_exploration_record_srd_object_access_node",
    ):
        if required not in tools:
            raise ValueError("Keep the existing object/Mage Hand ownership: " + required)
    owner = tools[OWNER]
    owner["parameters"].append(_optional(PLANE, "boolean", False))
    parts = _native_parts(runtime)
    tools[QUALIFY] = _qualifier(runtime, parts)
    tools[CAST] = _cast(runtime, parts)
    door = deepcopy(tools["apply_srd_minor_world_door"])
    if not any(p["name"] == "lasting_state" for p in door["parameters"]):
        door["parameters"].append(_lasting())
    door["effects"] = [
        _require(SCOPE, "Keep combat doors on their actual geometry owner."),
        *_scene_free(door["effects"]),
    ]
    tools[DOOR] = door
    original = deepcopy(owner["effects"])
    forward = {
        p["name"]: "{_" + p["name"] + "_id}"
        if p.get("resolve") == "entity"
        else "{" + p["name"] + "}"
        for p in owner["parameters"]
    }
    dispatch = _call(CAST, forward)
    dispatch["export_context"] = {
        "_spell_summary_rows": "_spell_summary_rows",
        "_spell_detail_rows": "_spell_detail_rows",
    }
    owner["effects"] = [
        {
            "condition": SCOPE + " and spell == 'thaumaturgy'",
            "on_true": [dispatch],
            "on_false": original,
        }
    ]
    native_name = SUBJECT_NODE if SUBJECT_NODE in tools else SUBJECT
    subject = deepcopy(tools[native_name])
    if "srd_minor_world_subject" not in str(subject["effects"]):
        raise ValueError("Preserve the actual subject fact producer")
    subject["effects"] = _without_scene(subject["effects"])
    tools[SUBJECT_EXPLORE] = subject
    public = tools[SUBJECT]
    original_subject = deepcopy(public["effects"])
    if native_name == SUBJECT:
        branch = [
            _call(
                SUBJECT_EXPLORE,
                {
                    p["name"]: "{_" + p["name"] + "_id}"
                    if p.get("resolve") == "entity"
                    else "{" + p["name"] + "}"
                    for p in public["parameters"]
                },
            )
        ]
    else:
        deliveries = [
            row
            for group in lists(original_subject)
            for row in group
            if row.get("call") == SUBJECT_NODE
        ]
        if len(deliveries) != 1:
            raise ValueError("Keep the one public subject fact handoff")
        branch = _without_scene(original_subject, fact_nodes={SUBJECT_NODE: SUBJECT_EXPLORE})
    public["effects"] = [{"condition": SCOPE, "on_true": branch, "on_false": original_subject}]
    public["description"] += (
        " Outside combat, the exact retained exploration object owns these facts."
    )
    changed = _bind_transport(runtime)
    return sorted({OWNER, CAST, QUALIFY, DOOR, SUBJECT, SUBJECT_EXPLORE, *changed})
