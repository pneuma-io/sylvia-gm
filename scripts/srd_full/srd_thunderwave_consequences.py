"""Thunderwave's fixed displacement and boom use native saves, scene motion and Silence."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS
    from .srd_spell_damage_consumers import _effect_lists
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_senses import _loop, _public, _push, _replace_tools
else:
    from srd_scene_relations import POSITION_EXPORTS
    from srd_spell_damage_consumers import _effect_lists
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from srd_timed_senses import _loop, _public, _push, _replace_tools

AREA = "cast_spell_instantaneous_area_save_damage"
TARGET = "resolve_spell_area_save_damage_target"
MOVE = "move_srd_travel_subject"
NODE = "move_srd_travel_subject_node"
BEGIN = "begin_srd_thunderwave_effect"
PUSH = "displace_srd_thunderwave_subject"
QUALIFY = "qualify_srd_thunderwave_motion"
SOUND = "query_srd_thunderwave_sound"
OBJECTS = "thunderwave_unsecured_objects"
RECEIPT = "thunderwave_receipt_id"
PUBLIC_TOOLS = {SOUND}
SOURCE_SHA = "49b2000fd8566ea098e4052af87257f659dc929358cae97ddd4eb3740ca77e84"


def _objects():
    return _parameter(
        OBJECTS,
        "array",
        required=False,
        default=[],
        items={"type": "string"},
        unique_items=True,
        max_items=64,
        description="Actual unsecured objects the GM identifies as entirely inside the Cube.",
    )


def _receipt():
    return _parameter(RECEIPT, "integer", required=False, default=0, minimum=0, model_hidden=True)


def _scene(subject, local):
    return [
        _call("srd_information_scene_entity", {"subject": subject}, POSITION_EXPORTS),
        _set(local, "_info_scene_entity"),
    ]


def _plane(local, entity):
    return [
        _set(local, f"{entity}.metadata.plane"),
        _when(f"{local} == null", [_set(local, "'material'")]),
    ]


def _tools():
    begin = _tool(
        "Retain the actual instantaneous paid Thunderwave origin and fixed boom fact.",
        [_entity("caster_state")],
        [
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            *_scene("{_caster_state_id}", "_tw_caster"),
            *_plane("_tw_cast_plane", "_tw_caster"),
            _when("state.thunderwave_sequence == null", [_set("state.thunderwave_sequence", 0)]),
            _set("state.thunderwave_sequence", "state.thunderwave_sequence + 1"),
            _set("_tw_cast_sequence", "state.thunderwave_sequence"),
            _set("_tw_cast_origin", "_tw_caster.position"),
            _set(
                "state.thunderwave_current_cast",
                {
                    "spell": "'thunderwave'",
                    "caster": "_caster_state_id",
                    "sequence": "_tw_cast_sequence",
                    "origin": "_tw_cast_origin",
                    "plane": "_tw_cast_plane",
                    "started_tick": "state.lasting_tick",
                    "source_sha256": repr(SOURCE_SHA),
                    "audible_feet": 300,
                    "dispel_ongoing": False,
                },
            ),
        ],
    )
    push = _tool(
        "Derive one exact ten-foot-away endpoint from this actual native Thunderwave effect.",
        [
            _entity("caster_state"),
            _parameter("subject"),
            _parameter("cast_sequence", "integer", minimum=1),
            _parameter("object_motion", "boolean", required=False, default=False),
        ],
        [
            _set("_tw_cast", "state.thunderwave_current_cast"),
            _require(
                "_tw_cast != null and _tw_cast.spell == 'thunderwave' and "
                "_tw_cast.caster == _caster_state_id and _tw_cast.sequence == cast_sequence",
                "Use the actual paid Thunderwave effect receipt.",
            ),
            _require(
                f"_tw_cast.source_sha256 == '{SOURCE_SHA}' and "
                "_tw_cast.started_tick == state.lasting_tick",
                "A stale or unrelated effect cannot authorize a Thunderwave push.",
            ),
            *_scene("{subject}", "_tw_subject"),
            *_plane("_tw_subject_plane", "_tw_subject"),
            _require(
                "_tw_subject_plane == _tw_cast.plane", "Thunderwave pushes on its actual plane."
            ),
            _when(
                "object_motion == true",
                [
                    _require(
                        "_tw_subject.metadata.is_object == true and "
                        "_tw_subject.metadata.unsecured == true",
                        "Record the actual unsecured object fact before selecting it.",
                    ),
                ],
            ),
            _set("_tw_dx", "_tw_subject.position[0] - _tw_cast.origin[0]"),
            _set("_tw_dy", "_tw_subject.position[1] - _tw_cast.origin[1]"),
            _set("_tw_dz", "_tw_subject.position[2] - _tw_cast.origin[2]"),
            _set("_tw_separation", "(_tw_dx * _tw_dx + _tw_dy * _tw_dy + _tw_dz * _tw_dz) ** 0.5"),
            _require(
                "_tw_separation > 0",
                "Coincident anchors need an actual away direction from the GM.",
            ),
            _set(
                "_tw_destination",
                [
                    "_tw_subject.position[0] + _tw_dx * 10 * _tw_separation ** -1",
                    "_tw_subject.position[1] + _tw_dy * 10 * _tw_separation ** -1",
                    "_tw_subject.position[2] + _tw_dz * 10 * _tw_separation ** -1",
                ],
            ),
            _when(
                "state.thunderwave_motion_sequence == null",
                [
                    _set("state.thunderwave_motion_sequence", 0),
                ],
            ),
            _set("state.thunderwave_motion_sequence", "state.thunderwave_motion_sequence + 1"),
            _set(
                "state.thunderwave_pending_motion",
                {
                    "kind": "'thunderwave_motion'",
                    "spell": "'thunderwave'",
                    "caster": "_caster_state_id",
                    "cast_sequence": "cast_sequence",
                    "receipt_id": "state.thunderwave_motion_sequence",
                    "subject": "_tw_subject.id",
                    "origin": "_tw_cast.origin",
                    "from_position": "_tw_subject.position",
                    "destination": "_tw_destination",
                    "plane": "_tw_cast.plane",
                    "started_tick": "state.lasting_tick",
                    "source_sha256": repr(SOURCE_SHA),
                    "consumed": False,
                },
            ),
            _call(
                MOVE,
                {
                    "subject": "{_tw_subject.id}",
                    "destination": "{_tw_destination}",
                    "plane": "{_tw_cast.plane}",
                    "teleport": False,
                    RECEIPT: "{state.thunderwave_motion_sequence}",
                },
            ),
        ],
    )
    qualify = _tool(
        "Authenticate the exact unconsumed source receipt at the sole relocation owner.",
        [
            _parameter("subject"),
            _parameter("destination", "array", items={"type": "number"}, min_items=3, max_items=3),
            _parameter("plane"),
            _receipt(),
        ],
        [
            _set("_tw_motion", "state.thunderwave_pending_motion"),
            _set("_tw_cast", "state.thunderwave_current_cast"),
            _require(
                "_tw_motion != null and _tw_cast != null and thunderwave_receipt_id > 0 and "
                "_tw_motion.receipt_id == thunderwave_receipt_id and _tw_motion.consumed == false",
                "Use the exact unconsumed paid Thunderwave motion receipt.",
            ),
            _require(
                "_tw_motion.kind == 'thunderwave_motion' and "
                f"_tw_motion.source_sha256 == '{SOURCE_SHA}' "
                "and _tw_motion.started_tick == state.lasting_tick and "
                "_tw_motion.cast_sequence == _tw_cast.sequence and "
                "_tw_motion.caster == _tw_cast.caster",
                "The motion must belong to this actual instantaneous Thunderwave.",
            ),
            *_scene("{subject}", "_tw_receiver"),
            *_plane("_tw_receiver_plane", "_tw_receiver"),
            _require(
                "_tw_receiver.id == _tw_motion.subject and plane == _tw_motion.plane and "
                "plane == _tw_receiver_plane",
                "The receiver, endpoint and plane must match the source-derived ten-foot push.",
            ),
            *[
                _require(
                    " and ".join(f"{first}[{i}] == {second}[{i}]" for i in range(3)),
                    "Use the exact actual source origin, recipient position and derived endpoint.",
                )
                for first, second in (
                    ("_tw_receiver.position", "_tw_motion.from_position"),
                    ("destination", "_tw_motion.destination"),
                    ("_tw_motion.origin", "_tw_cast.origin"),
                )
            ],
            _set("_thunderwave_forced", True),
        ],
    )
    sound = _public(
        _tool(
            "Query the recorded Thunderwave boom's fixed range and current Silence.",
            [_entity("listener_state")],
            [
                _set("_tw_boom", "state.thunderwave_current_cast"),
                _require(
                    "_tw_boom != null and _tw_boom.spell == 'thunderwave'",
                    "There is no recorded Thunderwave boom in this scene.",
                ),
                *_scene("{_listener_state_id}", "_tw_listener"),
                *_plane("_tw_listener_plane", "_tw_listener"),
                _set("_tw_sound_audible", False),
                _set(
                    "_tw_sound_distance",
                    "((_tw_listener.position[0] - _tw_boom.origin[0]) ** 2 + "
                    "(_tw_listener.position[1] - _tw_boom.origin[1]) ** 2 + "
                    "(_tw_listener.position[2] - _tw_boom.origin[2]) ** 2) ** 0.5",
                ),
                _when(
                    "_tw_listener_plane == _tw_boom.plane and _tw_sound_distance <= 300",
                    [
                        _set("_tw_sound_audible", True),
                        _when(
                            "state.has_spell_environments == true",
                            [
                                _call(
                                    "resolve_srd_environment_segment",
                                    {
                                        "start": "{_tw_boom.origin}",
                                        "end": "{_tw_listener.position}",
                                        "plane": "{_tw_boom.plane}",
                                    },
                                    ["_environment_sound_blocked"],
                                ),
                                _when(
                                    "_environment_sound_blocked == true",
                                    [_set("_tw_sound_audible", False)],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "information",
        "Thunderwave boom",
        [("Audible", "_tw_sound_audible"), ("Distance", "_tw_sound_distance")],
    )
    return {BEGIN: begin, PUSH: push, QUALIFY: qualify, SOUND: sound}


def _bind_motion(tools):
    """Add authenticated context to the existing owner, retaining its sole mutation and suffix."""
    changed = {}
    mutation_owners = 0
    for name in (MOVE, NODE):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        if RECEIPT in {p["name"] for p in tool["parameters"]}:
            continue
        tool["parameters"].append(_receipt())
        qualify = _when(
            f"{RECEIPT} > 0",
            [
                _require(
                    "teleport == false", "Thunderwave is a source-forced push, not teleportation."
                ),
                _call(
                    QUALIFY,
                    {
                        "subject": "{subject}",
                        "destination": "{destination}",
                        "plane": "{plane}",
                        RECEIPT: "{" + RECEIPT + "}",
                    },
                    ["_thunderwave_forced"],
                ),
            ],
        )
        tool["effects"][:0] = [_set("_thunderwave_forced", False), deepcopy(qualify)]
        for rows in _effect_lists(tool["effects"]):
            for i, effect in reversed(list(enumerate(rows))):
                call = effect.get("call")
                if call == NODE:
                    effect["params"][RECEIPT] = "{" + RECEIPT + "}"
                if call not in {"check_srd_secure_area_movement", "check_srd_hallow_movement"}:
                    continue
                fields = {"voluntary": False}
                if call == "check_srd_secure_area_movement":
                    fields["magical"] = True
                bindings = []
                for field, forced in fields.items():
                    local = "_tw_" + field
                    current = effect["params"].get(field, field == "voluntary")
                    if isinstance(current, str) and current.startswith("{"):
                        current = current[1:-1]
                    bindings += [
                        _set(local, current),
                        _when("_thunderwave_forced == true", [_set(local, forced)]),
                    ]
                    effect["params"][field] = "{" + local + "}"
                rows[i:i] = bindings
        indices = [i for i, e in enumerate(tool["effects"]) if "spatial_mutation" in e]
        if indices:
            if len(indices) != 1:
                raise ValueError("Thunderwave must preserve the single actual relocation mutation.")
            mutation_owners += 1
            tool["effects"][indices[0] : indices[0]] = [
                _when(
                    f"{RECEIPT} > 0",
                    [
                        deepcopy(qualify),
                        {
                            "spatial_query": {
                                "scene": "state.spatial_scene",
                                "operation": "sweep",
                                "query": {
                                    "map_id": "{lasting_state.spatial_scene.map.id}",
                                    "moving_entity_id": "{_travel_mover.id}",
                                    "destination": "{destination}",
                                    "include_entities": True,
                                    "metadata_filter": {
                                        "key": "plane",
                                        "value": "{plane}",
                                        "missing_matches": False,
                                    },
                                },
                            },
                            "store_as": "_tw_push_sweep",
                        },
                        _require(
                            "_tw_push_sweep.supported == true and _tw_push_sweep.clear == true",
                            "The fixed push needs a clear route; collision remains GM work.",
                        ),
                        _set("lasting_state.thunderwave_pending_motion.consumed", True),
                    ],
                )
            ]
        changed[name] = tool
    if changed and mutation_owners != 1:
        raise ValueError("Bind Thunderwave after the final existing Travel relocation owner.")
    return changed


def bind_runtime_consumers(text):
    """Join native area outcomes and final Travel context before retained cast capture."""
    tools = _load_runtime(text)["tools"]
    changed = _tools()
    changed.update(_bind_motion(tools))
    area = deepcopy(tools[AREA])
    if OBJECTS not in {p["name"] for p in area["parameters"]}:
        area["parameters"].append(_objects())
        area["effects"][:0] = [
            _set("_tw_cast_sequence", 0),
            _require(
                f"spell == 'thunderwave' or len({OBJECTS}) == 0",
                "This unsecured-object intent belongs to Thunderwave.",
            ),
            _when(
                "spell == 'thunderwave'",
                [
                    _call(BEGIN, {"caster_state": "{_caster_state_id}"}, ["_tw_cast_sequence"]),
                    _when(
                        "len(targets) == 0",
                        [
                            _set("_spell_area_last_save", None),
                            _set("_spell_area_last_save_outcome", "''"),
                            _set("_spell_area_last_save_advantage", False),
                            _set("_spell_area_last_save_disadvantage", False),
                            _set("_spell_area_last_damage_amount", 0),
                        ],
                    ),
                ],
            ),
        ]
        next(p for p in area["parameters"] if p["name"] == "targets")["min_items"] = 0
        for rows in _effect_lists(area["effects"]):
            for i, effect in reversed(list(enumerate(rows))):
                if effect.get("call") == TARGET:
                    rows[i + 1 : i + 1] = [
                        _when(
                            "spell == 'thunderwave' and _spell_area_last_save_outcome == 'failure'",
                            [
                                _call(
                                    PUSH,
                                    {
                                        "caster_state": "{_caster_state_id}",
                                        "subject": "{_area_target}",
                                        "cast_sequence": "{_tw_cast_sequence}",
                                    },
                                ),
                                _push(
                                    "_spell_detail_rows",
                                    {"label": "_area_target", "value": "'Pushed ten feet away.'"},
                                ),
                            ],
                        )
                    ]
        area["effects"].append(
            _when(
                "spell == 'thunderwave'",
                [
                    _loop(
                        OBJECTS,
                        "_tw_object_id",
                        [
                            _call(
                                PUSH,
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "subject": "{_tw_object_id}",
                                    "cast_sequence": "{_tw_cast_sequence}",
                                    "object_motion": True,
                                },
                            ),
                        ],
                    ),
                    _push(
                        "_spell_detail_rows",
                        {
                            "label": "'Thunderwave boom'",
                            "value": "'Audible to 300 feet; Silence blocks sound.'",
                        },
                    ),
                ],
            )
        )
    changed[AREA] = area
    owners = {AREA}
    progress = True
    while progress:
        progress = False
        for name, tool in tools.items():
            if name not in owners and any(
                e.get("call") in owners
                for rows in _effect_lists(tool.get("effects", []))
                for e in rows
            ):
                owners.add(name)
                progress = True
    for name in owners - {AREA}:
        tool = deepcopy(tools[name])
        for parameter in tool["parameters"]:
            if parameter["name"] == "targets":
                parameter["min_items"] = 0
        if OBJECTS not in {p["name"] for p in tool["parameters"]}:
            tool["parameters"].append(_objects())
        for rows in _effect_lists(tool["effects"]):
            for effect in rows:
                if effect.get("call") in owners:
                    effect.setdefault("params", {})[OBJECTS] = "{" + OBJECTS + "}"
        changed[name] = tool
    return _replace_tools(text, changed)
