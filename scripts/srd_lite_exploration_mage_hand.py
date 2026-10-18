"""Retain native Mage Hand actions and source lifetime without exploration map movement."""

from copy import deepcopy

from .srd_control_behaviors import _each, _lasting, _push
from .srd_information_spells import ACTIVE
from .srd_lite_exploration_objects import (
    INDEX,
    LOAD,
    POOL,
    SCENE,
    SCOPE,
    _load,
    _save_body,
    _scene_candidate,
    _without_scene,
)
from .srd_lite_profiles import lists
from .srd_scene_relations import _tool_scene
from .srd_spell_object_content import _now
from .srd_spell_object_forces import _hand_contact, _hand_source
from .srd_spell_object_utilities import SOURCE_PAGES, _read_source
from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _tool, _when
from .srd_timed_senses import _optional

CAST = "cast_srd_mage_hand"
CONTROL = "control_srd_mage_hand"
OPERATE = "operate_srd_mage_hand"
SOURCE = "read_srd_lite_exploration_mage_hand"
WRITE = "write_srd_lite_exploration_mage_hand_object"
IDENTITY = "qualify_srd_lite_exploration_mage_hand_identity"
RECEIPT = "read_srd_lite_exploration_mage_hand_receipt"
CONTACT = "qualify_srd_lite_exploration_mage_hand_admitted_contact"
QUALIFY = "qualify_srd_lite_exploration_mage_hand_cast"
RECONCILE = "reconcile_srd_lite_exploration_mage_hand"
ONE = "reconcile_srd_lite_exploration_mage_hand_source"
RELEASED = "reconcile_srd_lite_exploration_mage_hand_released_object"
MARKER = "srd_mage_hand_placement"
FACTS = ("record_srd_object_utility_facts", "record_srd_object_access")
FACT_NODES = {name + "_node": "lite_exploration_" + name + "_node" for name in FACTS}
CHOICES = (
    ("mage_hand_placement", "string", "", {}),
    ("mage_hand_caster_distance", "number", -1, {"minimum": -1, "maximum": 10000}),
    ("mage_hand_move_distance", "number", -1, {"minimum": -1, "maximum": 10000}),
    ("mage_hand_clear_path", "boolean", False, {}),
    ("mage_hand_object_contact", "boolean", False, {}),
)
GUIDANCE = (
    " Outside combat, Mage Hand uses current fictional placement, nearest caster separation "
    "and actual clear-path/contact facts, including its cargo and shared plane. No combat "
    "turn or map is required. A later Magic action advances ordinary time by one round; "
    "the hand moves at most 30 feet and vanishes beyond 30 feet from its caster."
)


def _parameters():
    return [_optional(name, kind, default, **bounds) for name, kind, default, bounds in CHOICES]


def _choose(condition, yes, no=()):
    return {"condition": condition, "on_true": yes, "on_false": list(no)}


def _capture(prefix, source=None):
    rows = []
    for name, _, default, _ in CHOICES:
        field = name if source is None else source + "." + name
        rows.append(_set(prefix + name, repr(default) if isinstance(default, str) else default))
        rows.append(_when(field + " != null", [_set(prefix + name, field)]))
    return rows


def _choice_params(prefix=""):
    return {name: "{" + prefix + name + "}" for name, _, _, _ in CHOICES}


def _bound_collections(effects):
    """Copied native collection operands must be simple locals in the current evaluator."""
    serial = 0

    def bound(rows):
        nonlocal serial
        result = []
        for original in rows:
            row = deepcopy(original)
            for branch in ("on_true", "on_false"):
                if branch in row:
                    row[branch] = bound(row[branch])
            if "for_each" in row:
                loop = row["for_each"]
                loop["do"] = bound(loop["do"])
                if "." in loop["collection"] or "[" in loop["collection"]:
                    serial += 1
                    name = "_hand_native_collection_" + str(serial)
                    result.append(_set(name, loop["collection"]))
                    loop["collection"] = name
            result.append(row)
        return result

    return bound(effects)


def _save(body, changes, frame=None, *, identity="object_id"):
    params = {
        "object_id": "{" + identity + "}",
        "updates": "{_hand_write_updates}",
        "lasting_state": "{_lasting_state_id}",
    }
    return [
        _set(
            "_hand_write_updates",
            [
                {
                    "body": body,
                    "metadata_delta": changes,
                    "frame_delta": frame if frame is not None else {},
                }
            ],
        ),
        _call(WRITE, params),
    ]


def _write_tool():
    return _tool(
        "Store the native object's exact frame and finite Mage Hand property consequences.",
        [
            _parameter("object_id"),
            _parameter("updates", "array", items={"type": "object"}, min_items=1, max_items=1),
            _lasting(),
        ],
        [
            _require(SCOPE, "Combat object changes use the actual scene mutation owner."),
            _set("_hand_write", "updates[0]"),
            _require(
                "_hand_write.body != null and _hand_write.metadata_delta != null and "
                "_hand_write.frame_delta != null",
                "Supply the exact native object body and finite metadata/frame changes.",
            ),
            _require(
                "_hand_write.body.id == object_id and "
                "_hand_write.body.metadata.participant_ref == null",
                "Preserve the exact scenery identity and creature boundary.",
            ),
            _require(
                "len(_hand_write.frame_delta) <= 3", "Store only finite native property changes."
            ),
            _set("_hand_frame_keys", []),
            _when("'active' in _hand_write.frame_delta", [_push("_hand_frame_keys", "'active'")]),
            _when(
                "'movement' in _hand_write.frame_delta", [_push("_hand_frame_keys", "'movement'")]
            ),
            _when(
                "'visibility' in _hand_write.frame_delta",
                [_push("_hand_frame_keys", "'visibility'")],
            ),
            _require(
                "len(_hand_write.frame_delta) == len(_hand_frame_keys)",
                "Mage Hand cannot replace position, dimensions, HP or plane through a frame patch.",
            ),
            *_save_body(
                "_hand_write.body",
                changes="_hand_write.metadata_delta",
                frame_changes="_hand_write.frame_delta",
            ),
        ],
    )


def _identity_tool():
    return _tool(
        "Qualify a new force ID without replacing any retained object or scene identity.",
        [_parameter("object_id"), _lasting()],
        [
            *_scene_candidate(),
            _require(
                "object_id != '' and _lite_object_scene_body == null and "
                "(lasting_state."
                + POOL
                + " == null or object_id not in lasting_state."
                + POOL
                + ")",
                "Use a new force ID without replacing a retained or mapped object.",
            ),
            _when(
                SCENE + " != null",
                [
                    _set("_hand_existing_geometry", SCENE + ".geometry"),
                    _set("_hand_existing_zones", SCENE + ".zones"),
                    *[
                        _each(
                            collection,
                            "_hand_existing",
                            [
                                _require(
                                    "_hand_existing.id != object_id",
                                    "Preserve every existing scene identity.",
                                )
                            ],
                        )
                        for collection in ("_hand_existing_geometry", "_hand_existing_zones")
                    ],
                ],
            ),
        ],
    )


def _native_metadata(effects):
    """Retain native eligibility and metadata; replace only its selected scene storage leaf."""
    output = []
    for original in effects:
        row = deepcopy(original)
        guard = row.get("require", {}).get("condition")
        if guard in (SCENE + " != null", SCENE + ".map.grid.unit == 'ft'"):
            continue
        if row.get("spatial_query", {}).get("operation") == "snapshot":
            continue
        if row.get("call") == "srd_information_scene_entity":
            if row["params"].get("subject") != "{object_id}":
                raise ValueError("Keep other scene subjects in their actual owner")
            output += [_load(destination="_info_scene_entity")]
            continue
        if "spatial_mutation" in row:
            patch = row["spatial_mutation"]["patch"]
            if not set(patch) <= {"metadata", "active", "movement", "visibility"}:
                raise ValueError("Mage Hand fiction cannot replace map geometry or HP")
            metadata = patch.get("metadata")
            if metadata != "{_world_metadata}":
                raise ValueError("Keep the native object metadata merge")
            frame = {key: value for key, value in patch.items() if key != "metadata"}
            frame = {
                key: value[1:-1] if isinstance(value, str) and value.startswith("{") else value
                for key, value in frame.items()
            }
            output += [
                _set("_hand_frame_delta", frame),
                _set("_hand_body_delta", {"metadata": "_world_metadata", **frame}),
                _set("_object_scene", "merge(_object_scene, _hand_body_delta)"),
                *_save("_object_scene", "_world_changes", "_hand_frame_delta"),
            ]
            continue
        if "spatial_query" in row:
            raise ValueError("Keep actual combat geometry in the original Mage Hand owner")
        for branch in ("on_true", "on_false", "do"):
            if branch in row:
                row[branch] = _native_metadata(row[branch])
        output.append(row)
    return output


def _source_tool():
    native = _hand_source()
    call = next(
        i for i, row in enumerate(native) if row.get("call") == "srd_information_scene_entity"
    )
    return _tool(
        "Qualify the exact native Mage Hand source and retained force identity outside combat.",
        [_entity("caster_state"), _parameter("source_id"), _lasting()],
        [
            *deepcopy(native[:call]),
            _load("{_utility_record.force_id}", destination="_force_hand_scene"),
            *deepcopy(native[call + 2 :]),
        ],
    )


def _source_call():
    return _call(
        SOURCE,
        {
            "caster_state": "{_caster_state_id}",
            "source_id": "{source_id}",
            "lasting_state": "{_lasting_state_id}",
        },
        ["_utility_record", "_utility_records", "_object_now", "_force_hand_scene"],
    )


def _contact_prefix():
    return [
        _load(),
        _require(
            "mage_hand_object_contact == true and mage_hand_clear_path == true and "
            "object_id != _utility_record.force_id",
            "Establish actual contact with a separate object and a clear shared-plane path.",
        ),
    ]


def _operation_branch(runtime):
    """Reuse the current stamped operation arms, including every native object restriction."""
    owner = runtime["tools"][OPERATE]
    branches = [
        row for row in owner["effects"] if row.get("condition", "").startswith("operation ==")
    ]
    if len(branches) != 4:
        raise ValueError("Keep the four actual Mage Hand operation arms")
    rows = [_source_call()]
    for original in branches:
        row = deepcopy(original)
        if row["condition"] != "operation == 'drop'":
            arm = row["on_true"]
            contact = next(
                i
                for i, effect in enumerate(arm)
                if effect.get("require", {}).get("error")
                == "The actual spectral hand does not touch this object."
            )
            arm = [*_contact_prefix(), *arm[contact + 1 :]]
            if row["condition"] == "operation == 'hold'":
                eligibility = next(
                    effect
                    for effect in arm
                    if effect.get("require", {}).get("error")
                    == "The hand can hold one actual unheld object weighing at most ten pounds."
                )
                guard = eligibility["require"]["condition"]
                for holder in ("carried_by", "worn_by"):
                    guard = guard.replace(
                        "_object_scene.metadata." + holder + " == null",
                        "(_object_scene.metadata." + holder + " == null or "
                        "_object_scene.metadata." + holder + " == '')",
                    )
                eligibility["require"]["condition"] = guard
            if row["condition"] == "operation == 'pour'":
                poured = next(
                    effect["set"]["value"]
                    for effect in arm
                    if effect.get("set", {}).get("field") == "_force_poured"
                )
                poured["position"] = None
                poured["plane"] = None
                poured["fictional_placement"] = "mage_hand_placement"
            row["on_true"] = _native_metadata(arm)
            if row["condition"] == "operation == 'hold'":
                row["on_true"] += _mark_placement("_object_scene", "object_id")
        rows.append(row)
    return rows


def _mark_placement(body, identity, *, source="source_id", force="_utility_record.force_id"):
    """Save fictional movement separately; future map admission supplies actual coordinates."""
    return [
        _set(
            "_hand_placement_delta",
            {
                MARKER: {
                    "source_id": source,
                    "force_id": force,
                    "caster": "_caster_state_id",
                    "fiction": "mage_hand_placement",
                    "caster_distance": "mage_hand_caster_distance",
                    "pending": True,
                }
            },
        ),
        _set(
            "_hand_placement_metadata",
            {"metadata": "merge(" + body + ".metadata, _hand_placement_delta)"},
        ),
        _set("_hand_placed_body", "merge(" + body + ", _hand_placement_metadata)"),
        *_save("_hand_placed_body", "_hand_placement_delta", identity=identity),
    ]


def _initial_qualification(runtime):
    native = runtime["tools"][CAST]["effects"]
    start = next(
        i for i, row in enumerate(native) if row.get("set", {}).get("field") == "_force_form"
    )
    end = next(
        i
        for i, row in enumerate(native)
        if row.get("require", {}).get("error")
        == "Admit this actual caster's fictional spectral-hand depiction first."
    )
    body_prefix = [row for row in native[:start] if "_minor_body_source" in str(row)]
    guards = [
        _require(
            SCOPE + " and combat.pending_action == null", "Finish the current pending action."
        ),
        _require(ACTIVE, "A capable living caster controls this spectral hand."),
        _require(
            "mage_hand_placement != '' and mage_hand_caster_distance >= 0 and "
            "mage_hand_caster_distance <= 30 and mage_hand_clear_path == true",
            "Establish a reachable shared-plane hand placement within thirty feet.",
        ),
        *_now(),
        *deepcopy(body_prefix),
        *deepcopy(native[start : end + 1]),
        _call(IDENTITY, {"object_id": "{output_id}", "lasting_state": "{_lasting_state_id}"}),
        _require("operation != 'drop'", "A newly cast empty hand has no cargo to drop."),
    ]
    # Selected operation qualification stops before the first native mutation. The
    # actual stamped arm still performs the change only after successful payment.
    for row in _operation_branch(runtime)[1:]:
        if row["condition"] == "operation == 'drop'":
            continue
        arm = row["on_true"]
        prefix = []
        for effect in arm:
            if effect.get("set", {}).get("field") in (
                "_world_changes",
                "_force_access_change",
                "_force_vial",
            ):
                break
            prefix.append(deepcopy(effect))
        for effect in prefix:
            guard = effect.get("require", {}).get("condition", "")
            guard = guard.replace("_utility_record.force_id", "output_id")
            guard = guard.replace("_utility_record.cargo_id == ''", "true")
            if "require" in effect:
                effect["require"]["condition"] = guard
        guards.append(_when(row["condition"], prefix))
    return _tool(
        "Qualify the native depiction, identity and operation before casting cost or staging.",
        [
            _entity("caster_state"),
            _parameter("output_id"),
            _parameter("form_id"),
            _parameter("operation"),
            _optional("object_id", default=""),
            *_parameters(),
            _lasting(),
        ],
        guards,
    )


def _cast_branch(runtime):
    rows = []
    native = runtime["tools"][CAST]["effects"]
    creating = False
    saved = False
    for original in native:
        row = deepcopy(original)
        field = row.get("set", {}).get("field", "")
        if row.get("call") == "resolve_srd_object_content_point":
            rows += [
                _set("_world_origin", [0, 0, 0]),
                _set("_world_plane", None),
                _set("_world_lighting", None),
            ]
            continue
        if field == "_world_prior_entities":
            creating = True
            continue
        if creating and field == "_force_turn":
            creating = False
            rows += [
                *_mark_placement(
                    "_world_created_entity",
                    "output_id",
                    source="_utility_source_id",
                    force="output_id",
                ),
            ]
            saved = True
        if creating:
            if field == "_world_created_metadata" and isinstance(row["set"]["value"], dict):
                row["set"]["value"]["plane"] = None
                row["set"]["value"]["lighting"] = None
            if field == "_world_created_entity":
                row["set"]["value"].pop("map_id", None)
            if field not in {
                "_world_created_metadata",
                "_world_created_marker",
                "_utility_bound_stamp",
                "_utility_bound_metadata",
                "_world_created_entity",
            }:
                continue
        if row.get("call") == OPERATE:
            row["params"].update(_choice_params())
        rows.append(row)
    if not saved:
        raise ValueError("Keep the native hand body producer and stamped source record")
    rows += [
        _push("_utility_summary", {"label": "'Operation'", "value": "operation"}),
        _push("_utility_details", {"label": "'Placement'", "value": "mage_hand_placement"}),
    ]
    return rows


def _control_branch():
    return [
        _set("_hand_feedback_placement", "mage_hand_placement"),
        _require(
            SCOPE + " and combat.pending_action == null", "Finish the current pending action."
        ),
        _require(ACTIVE, "A capable living caster controls this spectral hand."),
        _source_call(),
        _require(
            "mage_hand_placement != '' and mage_hand_caster_distance >= 0 and "
            "mage_hand_move_distance >= 0 and mage_hand_move_distance <= 30 and "
            "mage_hand_clear_path == true",
            "Establish the full hand/cargo clear path and a move of at most thirty feet.",
        ),
        _call(
            "check_srd_active_control_action",
            {"actor_state": "{_caster_state_id}", "action_kind": "magic", "magic_action": True},
        ),
        _when(
            "_utility_record.cargo_id != ''",
            [
                _load("{_utility_record.cargo_id}", destination="_force_cargo"),
                _require(
                    "_force_cargo.metadata.srd_spell_carrier.source_id == source_id and "
                    "_force_cargo.metadata.srd_spell_carrier.force_id == _utility_record.force_id",
                    "Preserve this hand's exact native held cargo.",
                ),
                *_mark_placement("_force_cargo", "_utility_record.cargo_id"),
            ],
        ),
        *_mark_placement("_force_hand_scene", "_utility_record.force_id"),
        _set("_force_vanished", "mage_hand_caster_distance > 30"),
        _choose(
            "_force_vanished == true",
            [
                _call(
                    "retire_srd_mage_hand",
                    {"caster_state": "{_caster_state_id}", "source_id": "{source_id}"},
                )
            ],
            [
                _call(
                    OPERATE,
                    {
                        "caster_state": "{_caster_state_id}",
                        "source_id": "{source_id}",
                        "object_id": "{object_id}",
                        "operation": "{operation}",
                        **_choice_params(),
                    },
                )
            ],
        ),
        _call("advance_srd_lasting_time", {"elapsed_rounds": 1}),
        _call(
            RECEIPT,
            {"caster_state": "{_caster_state_id}", "source_id": "{source_id}"},
            ["_force_vanished"],
        ),
    ]


def _receipt_tool():
    return _tool(
        "Read the fresh native source owner after a control action and time advancement.",
        [_entity("caster_state"), _parameter("source_id"), _lasting()],
        [_set("_force_vanished", "caster_state.mage_hand_source_id != source_id")],
    )


def _contact_tool():
    return _tool_scene(
        "Qualify native hand/cargo contact after consuming explicit incoming physical positions.",
        [_entity("caster_state"), _parameter("source_id"), _parameter("object_id")],
        [*_hand_source(), *_hand_contact()],
    )


def _refresh_branch():
    return [
        *_read_source("mage_hand"),
        _set("_hand_fiction_expired", "_object_now >= _utility_record.expires_tick"),
        _when(
            "_hand_fiction_expired != true",
            [
                _load("{_utility_record.force_id}", destination="_force_scene"),
                _when(
                    "_force_scene.metadata." + MARKER + " != null",
                    [
                        _set(
                            "_hand_fiction_expired",
                            "_force_scene.metadata." + MARKER + ".caster_distance > 30",
                        )
                    ],
                ),
            ],
        ),
        _when(
            "_hand_fiction_expired == true",
            [
                _call(
                    "retire_srd_mage_hand",
                    {"caster_state": "{_caster_state_id}", "source_id": "{source_id}"},
                )
            ],
        ),
    ]


def _bind_intent(runtime):
    tools = runtime["tools"]
    public = [
        name
        for name in ("cast_spell", "cast_spell_node", "cast_spell_outside_combat")
        if name in tools
    ]
    for name in (*public, "begin_srd_spell_action", "finish_srd_retained_cast_tail"):
        owner = tools[name]
        owner["parameters"].extend(_parameters())
        owner["description"] += GUIDANCE
        for group in lists(owner["effects"]):
            for row in group:
                if row.get("call") in (
                    "cast_spell",
                    "cast_spell_node",
                    "finish_srd_retained_cast_tail",
                ):
                    row["params"].update(_choice_params())
                field = row.get("set", {}).get("field", "")
                if field == "_retained_cast_tail_choices" or field.endswith(
                    ".retained_cast_choices"
                ):
                    capture = row["set"]["value"]
                    if (
                        isinstance(capture, list)
                        and len(capture) == 1
                        and isinstance(capture[0], dict)
                    ):
                        capture[0].update({choice: choice for choice, _, _, _ in CHOICES})
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
            raise ValueError("Preserve the actual saved casting profile: " + name)
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
                _when(
                    SCOPE + " and " + identity + " == 'mage_hand'",
                    [
                        _call(
                            QUALIFY,
                            {
                                "caster_state": "{_caster_state_id}",
                                "output_id": "{utility_output_id}",
                                "form_id": "{utility_form_id}",
                                "operation": "{utility_operation}",
                                "object_id": "{utility_object_id}",
                                **_choice_params(),
                            },
                        )
                    ],
                ),
            )
    begin = tools["begin_srd_spell_action"]
    stamps = [
        (group, i)
        for group in lists(begin["effects"])
        for i, row in enumerate(group)
        if row.get("set", {}).get("field")
        == "lasting_state.pending_srd_actions.{_pending_action_id}.spell"
    ]
    if len(stamps) != 1:
        raise ValueError("Preserve the existing staged spell allocation")
    group, i = stamps[0]
    group[i:i] = [
        _set("lasting_state.pending_srd_actions.{_pending_action_id}.parameters." + name, name)
        for name, _, _, _ in CHOICES
    ]
    complete = tools["complete_srd_pending_action"]
    calls = [
        (group, i)
        for group in lists(complete["effects"])
        for i, row in enumerate(group)
        if row.get("call") == "cast_spell"
        and row.get("params", {}).get("spell") == "{_pending_action.parameters.spell}"
    ]
    if len(calls) != 1:
        raise ValueError("Preserve the existing staged spell completion")
    group, i = calls[0]
    group[i]["params"].update(_choice_params("_hand_pending_"))
    group[i:i] = _capture("_hand_pending_", "_pending_action.parameters")
    tail = tools["finish_srd_retained_cast_tail"]
    dispatches = [
        row
        for group in lists(tail["effects"])
        for row in group
        if row.get("call") == "cast_spell_object_utility_spell"
    ]
    if len(dispatches) != 1:
        raise ValueError("Keep the actual retained utility delivery")
    dispatches[0]["params"].update(_choice_params("_hand_choice_"))
    unwrap = [
        (group, i)
        for group in lists(tail["effects"])
        for i, row in enumerate(group)
        if row.get("set", {}).get("field") == "_retained_cast_tail_choices"
        and row["set"].get("value") == "_retained_cast_tail_choices[0]"
    ]
    if len(unwrap) != 1:
        raise ValueError("Keep the actual retained choice unwrap")
    group, i = unwrap[0]
    group[i + 1 : i + 1] = _capture("_hand_choice_", "_retained_cast_tail_choices")


def _find_body(rows, identity, destination):
    return [
        _set(destination, None),
        _each(
            rows,
            "_hand_admitted_row",
            [
                _when(
                    "_hand_admitted_row.id == " + identity,
                    [
                        _require(
                            destination + " == null", "Use one unambiguous exact body identity."
                        ),
                        _set(destination, "_hand_admitted_row"),
                    ],
                )
            ],
        ),
    ]


def _apply_pending_body(kind, identity, *, source="source_id"):
    stored, admitted = "_hand_stored_" + kind, "_hand_incoming_" + kind
    return [
        _set(
            "_hand_pending_spatial",
            {
                "plane": admitted + ".metadata.plane",
                "lighting": admitted + ".metadata.lighting",
                MARKER: {
                    "source_id": source,
                    "force_id": stored + ".metadata." + MARKER + ".force_id",
                    "caster": stored + ".metadata." + MARKER + ".caster",
                    "fiction": stored + ".metadata." + MARKER + ".fiction",
                    "caster_distance": "_scene_distance",
                    "pending": False,
                },
            },
        ),
        _set(
            "_hand_pending_frame",
            {
                "position": admitted + ".position",
                "facing": admitted + ".facing",
                "metadata": "merge(" + stored + ".metadata, _hand_pending_spatial)",
            },
        ),
        # Keep the old break-bounds reference until the immediately following
        # object reconciliation translates every retained break to the new body.
        _set(
            "_hand_pending_retained_patch",
            {"metadata": "merge(" + stored + ".metadata, _hand_pending_spatial)"},
        ),
        _set("_hand_pending_body", "merge(" + stored + ", _hand_pending_retained_patch)"),
        *_save("_hand_pending_body", "_hand_pending_spatial", identity=identity),
        _set("_hand_pending_rows", []),
        _each(
            "_hand_candidate_rows",
            "_hand_candidate_row",
            [
                _choose(
                    "_hand_candidate_row.id == " + identity,
                    [
                        _push(
                            "_hand_pending_rows", "merge(_hand_candidate_row, _hand_pending_frame)"
                        )
                    ],
                    [_push("_hand_pending_rows", "_hand_candidate_row")],
                )
            ],
        ),
        _set("_hand_candidate_rows", "_hand_pending_rows"),
    ]


def _dimensions(kind):
    stored, incoming = "_hand_stored_" + kind, "_hand_incoming_" + kind
    return [
        _require(
            incoming
            + " != null and "
            + incoming
            + ".active == true and "
            + incoming
            + ".metadata.participant_ref == null and "
            + incoming
            + ".footprint.kind == "
            + stored
            + ".footprint.kind and "
            + incoming
            + ".height == "
            + stored
            + ".height",
            "Include the exact retained force/cargo body and native dimensions.",
        ),
        _when(
            stored + ".footprint.kind == 'box'",
            [
                _require(
                    incoming
                    + ".footprint.half_extents[0] == "
                    + stored
                    + ".footprint.half_extents[0] and "
                    + incoming
                    + ".footprint.half_extents[1] == "
                    + stored
                    + ".footprint.half_extents[1]",
                    "Preserve the retained box dimensions.",
                )
            ],
        ),
        _when(
            stored + ".footprint.kind == 'circle'",
            [
                _require(
                    incoming + ".footprint.radius == " + stored + ".footprint.radius",
                    "Preserve the retained circular dimensions.",
                )
            ],
        ),
        _require(
            incoming + ".metadata.plane != null and " + incoming + ".metadata.lighting != null",
            "Supply the actual shared plane and ambient light.",
        ),
    ]


def _reconcile_one():
    """Advance changed body geometry before native contact snapshots reuse the saved scene."""
    return _tool(
        "Reconcile an exact hand's pending movement from explicit incoming bodies.",
        [
            _entity("caster_state"),
            _parameter("source_id"),
            _parameter("scenes", "array", items={"type": "object"}, max_items=1),
            _lasting(),
        ],
        [
            *_read_source("mage_hand"),
            _load("{_utility_record.force_id}", destination="_hand_stored_force"),
            _set("_hand_stored_cargo", None),
            _when(
                "_utility_record.cargo_id != ''",
                [_load("{_utility_record.cargo_id}", destination="_hand_stored_cargo")],
            ),
            _set("_hand_has_pending", "_hand_stored_force.metadata." + MARKER + ".pending == true"),
            _when(
                "_hand_stored_cargo != null and _hand_stored_cargo.metadata."
                + MARKER
                + ".pending == true",
                [_set("_hand_has_pending", True)],
            ),
            _when(
                "_hand_has_pending == true",
                [
                    _require(
                        "len(scenes) == 1",
                        "Supply this pending hand and cargo's actual combat placement.",
                    ),
                    _set("_hand_incoming_rows", "scenes[0].entities"),
                    _set("_hand_candidate_rows", SCENE + ".entities"),
                    *_find_body(
                        "_hand_incoming_rows", "_utility_record.force_id", "_hand_incoming_force"
                    ),
                    *_dimensions("force"),
                    _require(
                        "_hand_incoming_force.metadata.srd_object_force.source_id == source_id and "
                        "_hand_incoming_force.metadata.srd_object_force.spell == 'mage_hand' and "
                        "_hand_stored_force.metadata.srd_object_force.source_id == source_id",
                        "Preserve the actual native hand and source identity.",
                    ),
                    _require(
                        "_hand_incoming_force.metadata.srd_object_force.caster == "
                        "_caster_state_id and "
                        "_hand_incoming_force.metadata.srd_object_force.source_sha256 == "
                        + repr(SOURCE_PAGES[145]),
                        "Preserve the native caster and pinned hand source.",
                    ),
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_caster_state_id}"},
                        ["_info_scene_entity"],
                    ),
                    _require(
                        "_info_scene_entity.metadata.plane == _hand_incoming_force.metadata.plane",
                        "Admit the caster and spectral hand on their actual shared plane.",
                    ),
                    _call(
                        "measure_srd_information_distance",
                        {
                            "start": "{_info_scene_entity.position}",
                            "end": "{_hand_incoming_force.position}",
                        },
                        ["_scene_distance"],
                    ),
                    _require(
                        "_scene_distance <= 30 and _object_now < _utility_record.expires_tick",
                        "Place this live hand within its native thirty-foot caster range.",
                    ),
                    _when(
                        "_utility_record.cargo_id != ''",
                        [
                            *_find_body(
                                "_hand_incoming_rows",
                                "_utility_record.cargo_id",
                                "_hand_incoming_cargo",
                            ),
                            *_dimensions("cargo"),
                            _require(
                                "_hand_stored_cargo.metadata.srd_spell_carrier.source_id == "
                                "source_id and "
                                "_hand_stored_cargo.metadata.srd_spell_carrier.force_id"
                                " == _utility_record.force_id and "
                                "_hand_incoming_cargo.metadata.plane"
                                " == "
                                "_hand_incoming_force.metadata.plane",
                                "Preserve the exact source-owned cargo and shared plane.",
                            ),
                            _require(
                                "_hand_incoming_cargo.metadata.srd_spell_carrier.source_id == "
                                "source_id and "
                                "_hand_incoming_cargo.metadata.srd_spell_carrier.force_id == "
                                "_utility_record.force_id",
                                "Preserve the supplied cargo's exact native carrier identity.",
                            ),
                            *_apply_pending_body("cargo", "_utility_record.cargo_id"),
                        ],
                    ),
                    *_apply_pending_body("force", "_utility_record.force_id"),
                    _set(SCENE + ".map.revision", SCENE + ".map.revision + 1"),
                    _set(SCENE + ".entities", "_hand_candidate_rows"),
                    _when(
                        "_utility_record.cargo_id != ''",
                        [
                            _call(
                                CONTACT,
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "source_id": "{source_id}",
                                    "object_id": "{_utility_record.cargo_id}",
                                },
                            )
                        ],
                    ),
                ],
            ),
        ],
    )


def _reconcile_tool():
    return _tool(
        "Consume exact source-owned pending hand/cargo placements before object reconciliation.",
        [_lasting(), _parameter("scenes", "array", items={"type": "object"}, max_items=1)],
        [
            _require(
                SCOPE + " and combat.pending_action == null",
                "Reconcile hand placements before initiative.",
            ),
            _set("_hand_pending_sources", []),
            _when(
                "lasting_state.turn_effects != null",
                [_set("_hand_pending_sources", "lasting_state.turn_effects")],
            ),
            _each(
                "_hand_pending_sources",
                "_hand_pending_source",
                [
                    _when(
                        "_hand_pending_source.kind == 'object_utility_spell' and "
                        "_hand_pending_source.spell == 'mage_hand' and "
                        "_hand_pending_source.active == true",
                        [
                            _call(
                                ONE,
                                {
                                    "caster_state": "{_hand_pending_source.caster}",
                                    "source_id": "{_hand_pending_source.source_id}",
                                    "scenes": "{scenes}",
                                    "lasting_state": "{_lasting_state_id}",
                                },
                            )
                        ],
                    )
                ],
            ),
            _when(
                "lasting_state." + INDEX + " != null",
                [
                    _set("_hand_pending_object_ids", "lasting_state." + INDEX),
                    _each(
                        "_hand_pending_object_ids",
                        "_hand_pending_object_id",
                        [
                            _set(
                                "_hand_pending_object",
                                "lasting_state." + POOL + "[_hand_pending_object_id].body",
                            ),
                            _when(
                                "_hand_pending_object.metadata." + MARKER + ".pending == true and "
                                "_hand_pending_object.metadata.srd_object_force == null and "
                                "_hand_pending_object.metadata.srd_spell_carrier == null",
                                [
                                    _call(
                                        RELEASED,
                                        {
                                            "object_id": "{_hand_pending_object_id}",
                                            "scenes": "{scenes}",
                                            "lasting_state": "{_lasting_state_id}",
                                        },
                                    )
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _released_tool():
    """Keep released cargo's pending world placement even after the finite source retires."""
    return _tool(
        "Reconcile an explicitly selected released object from its retained native force identity.",
        [
            _parameter("object_id"),
            _parameter("scenes", "array", items={"type": "object"}, max_items=1),
            _lasting(),
        ],
        [
            _set("_hand_stored_cargo", "lasting_state." + POOL + "[object_id].body"),
            _set("_hand_cargo_placement", "_hand_stored_cargo.metadata." + MARKER),
            _set("_hand_released_source_id", "_hand_cargo_placement.source_id"),
            _require(
                "_hand_cargo_placement.force_id in lasting_state." + POOL,
                "Preserve the released object's exact retained force identity.",
            ),
            _set(
                "_hand_retained_force",
                "lasting_state." + POOL + "[_hand_cargo_placement.force_id].body",
            ),
            _require(
                "_hand_retained_force.metadata.srd_object_force.source_id == "
                "_hand_released_source_id and "
                "_hand_retained_force.metadata.srd_object_force.spell == 'mage_hand' and "
                "_hand_retained_force.metadata.srd_object_force.source_sha256 == "
                + repr(SOURCE_PAGES[145]),
                "Reconcile only the native source's genuinely released cargo.",
            ),
            _set("_hand_incoming_cargo", None),
            _when(
                "len(scenes) == 1",
                [
                    _set("_hand_incoming_rows", "scenes[0].entities"),
                    *_find_body("_hand_incoming_rows", "object_id", "_hand_incoming_cargo"),
                ],
            ),
            _when(
                "_hand_incoming_cargo != null",
                [
                    *_dimensions("cargo"),
                    _set("_hand_candidate_rows", SCENE + ".entities"),
                    _set("_scene_distance", "_hand_cargo_placement.caster_distance"),
                    *_apply_pending_body("cargo", "object_id", source="_hand_released_source_id"),
                    _set(SCENE + ".entities", "_hand_candidate_rows"),
                ],
            ),
        ],
    )


def bind_exploration_mage_hand(runtime):
    """Bind after native force stamps, object exploration and retained casting-tail composition."""
    tools = runtime["tools"]
    if SOURCE in tools or LOAD not in tools:
        raise ValueError("Bind the unbound Mage Hand after retained exploration objects")
    changed = {
        CAST,
        CONTROL,
        OPERATE,
        "qualify_srd_mage_hand_source",
        "release_srd_mage_hand_object",
        "deactivate_srd_mage_hand_scene",
        "refresh_srd_mage_hand",
        "cast_spell_object_utility_spell",
        *FACTS,
    }
    for name in changed:
        if name not in tools:
            raise ValueError("Preserve the actual native Mage Hand owner: " + name)
    tools.update(
        {
            SOURCE: _source_tool(),
            WRITE: _write_tool(),
            IDENTITY: _identity_tool(),
            RECEIPT: _receipt_tool(),
            CONTACT: _contact_tool(),
            QUALIFY: _initial_qualification(runtime),
            RECONCILE: _reconcile_tool(),
            ONE: _reconcile_one(),
            RELEASED: _released_tool(),
        }
    )
    branches = {
        CAST: _cast_branch(runtime),
        CONTROL: _control_branch(),
        OPERATE: _operation_branch(runtime),
        "qualify_srd_mage_hand_source": [_source_call()],
        "release_srd_mage_hand_object": _native_metadata(
            tools["release_srd_mage_hand_object"]["effects"]
        ),
        "deactivate_srd_mage_hand_scene": _native_metadata(
            tools["deactivate_srd_mage_hand_scene"]["effects"]
        ),
        "refresh_srd_mage_hand": _refresh_branch(),
    }
    for name, branch in branches.items():
        original = deepcopy(tools[name]["effects"])
        if name == CONTROL:
            destination = next(p for p in tools[name]["parameters"] if p["name"] == "destination")
            destination.update(required=False, default=None)
            original = [
                _require(
                    "destination != null", "Combat hand movement needs an actual destination."
                ),
                *original,
            ]
        tools[name]["effects"] = [_choose(SCOPE, _bound_collections(branch), original)]
        if name in (CAST, CONTROL, OPERATE):
            tools[name]["parameters"].extend(_parameters())
            tools[name]["description"] += GUIDANCE
    control_card = tools[CONTROL]["metadata"]["result_card"]
    control_card["summary"].append(
        {"label": "Operation", "value": {"path": "parameters.operation"}}
    )
    control_card.setdefault("details", []).extend(
        [
            {"label": "Source", "value": {"path": "parameters.source_id"}},
            {
                "label": "Placement",
                "value": {"path": "context._hand_feedback_placement"},
                "optional": True,
            },
        ]
    )
    # Public fact shims delegate to native private nodes. Clone only the two
    # selected physical-fact owners, retaining the original map owners intact.
    for native_name, exploration_name in FACT_NODES.items():
        if exploration_name in tools:
            raise ValueError("Exploration hand fact node was already bound: " + exploration_name)
        node = deepcopy(tools[native_name])
        node["effects"] = _bound_collections(_without_scene(node["effects"]))
        if not any(row.get("require", {}).get("condition") == SCOPE for row in node["effects"]):
            node["effects"].insert(
                0, _require(SCOPE, "Retained hand object facts are only used outside combat.")
            )
        tools[exploration_name] = node
    for name in FACTS:
        owner = tools[name]
        original = deepcopy(owner["effects"])
        owner["effects"] = [
            _choose(
                SCOPE, _bound_collections(_without_scene(original, fact_nodes=FACT_NODES)), original
            )
        ]
    leaf = tools["cast_spell_object_utility_spell"]
    leaf["parameters"].extend(_parameters())
    hand_calls = [
        row for group in lists(leaf["effects"]) for row in group if row.get("call") == CAST
    ]
    if len(hand_calls) != 1:
        raise ValueError("Keep the actual paid Mage Hand installer handoff")
    hand_calls[0]["params"].update(_choice_params())
    _bind_intent(runtime)
    return sorted(
        changed
        | {
            SOURCE,
            WRITE,
            IDENTITY,
            RECEIPT,
            CONTACT,
            QUALIFY,
            RECONCILE,
            ONE,
            RELEASED,
            *FACT_NODES.values(),
            "cast_spell",
            "cast_spell_outside_combat",
            "begin_srd_spell_action",
            "complete_srd_pending_action",
            "finish_srd_retained_cast_tail",
        }
        | ({"cast_spell_node"} if "cast_spell_node" in tools else set())
    )
