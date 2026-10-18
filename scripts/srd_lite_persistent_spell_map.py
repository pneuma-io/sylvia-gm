"""Present explicitly known spell bodies and source-owned range extents in combat."""

from .srd_control_behaviors import _each, _push
from .srd_lite_retained_area_map import _zone_changes
from .srd_scene_relations import _snapshot
from .srd_spell_environment import _call, _environment_tool
from .srd_timed_actor_defenses import _parameter, _require, _set

KNOWN = "known_spell_effect"
BODY = "present_srd_lite_known_spell_body"
REFRESH = "refresh_srd_lite_persistent_spell_map"
ANCHOR = "read_srd_lite_public_spell_anchor"
TAG = "srd_lite_persistent_spell_map"
MAP = "srd_lite_spell_birth_map"
CAPSULE_FACT_MERGE = "merge(_retained_cast_tail_choices[0], _lite_object_retained_facts)"
BODY_LABELS = {
    "mage_hand": "Mage Hand",
    "guardian_of_faith": "Guardian of Faith",
    "find_steed": "Otherworldly Steed",
    "summon_dragon": "Summoned Dragon",
    "wall_of_stone": "Wall of Stone panel",
}
GUIDANCE = (
    "\nFor the offered spell bodies, Stone/Fire walls, Aura of Life and magical light, "
    "set known_spell_effect=true only for this cast's effect or created body whose "
    "presence and placement the player knows from a visible cast or an established "
    "current fact. It presents that effect, never a hidden caster, target, scouting "
    "eye or unrelated source. Omitted/false keeps private effects private. Range "
    "extents do not establish recipients, illumination, damage or safe movement."
)


def _lists(rows):
    yield rows
    for row in rows:
        for branch in ("on_true", "on_false"):
            yield from _lists(row.get(branch, []))
        if "for_each" in row:
            yield from _lists(row["for_each"]["do"])


def _when(condition, on_true, on_false=None):
    row = {"condition": condition, "on_true": on_true}
    if on_false is not None:
        row["on_false"] = on_false
    return row


def _known_parameter():
    return {"name": KNOWN, "type": "boolean", "required": False, "default": False}


def _body_tool():
    """Only the owning producer may give an exact native body public presentation."""
    same_source = (
        "_visual_input_body.metadata.source_ref == spell and "
        "_visual_input_body.metadata.source_caster == _visual_caster"
    )
    hand_source = (
        "_visual_input_body.metadata.srd_object_force.spell == 'mage_hand' and "
        "_visual_input_body.metadata.srd_object_force.caster == _visual_caster and "
        "_visual_input_body.metadata.srd_object_force.source_id == source_id"
    )
    wall_source = (
        "_visual_wall.installed == true and _visual_wall.spell_id == 'wall_of_stone' and "
        "_visual_wall.caster_state_id == _visual_caster and "
        "_visual_input_body.metadata.solid_wall_source_id == source_id"
    )
    wall_stamp = (
        "_visual_input_body.metadata.srd_spell_object.kind == 'wall_stone_panel' and "
        "_visual_input_body.metadata.srd_spell_object.source_id == source_id and "
        "_visual_input_body.metadata.srd_spell_object.serial == _visual_wall.serial and "
        "_visual_input_body.metadata.solid_wall_serial == _visual_wall.serial"
    )
    return _environment_tool(
        "Give only this exact source's known physical effect a closed public label.",
        [
            {
                "name": "body_capsule",
                "type": "array",
                "required": True,
                "min_items": 1,
                "max_items": 1,
                "items": {"type": "object"},
            },
            _parameter("spell"),
            _parameter("caster_id"),
            {"name": "source_id", "type": "string", "required": False, "default": ""},
            _known_parameter(),
        ],
        [
            _set("_visual_input_body", "body_capsule[0]"),
            _set("_visual_body", "_visual_input_body"),
            _set("_visual_added", False),
            _when(
                KNOWN + " == true",
                [
                    _require(
                        "_visual_input_body.active == true and "
                        "_visual_input_body.metadata != null",
                        "Public spell presentation needs the actual active body.",
                    ),
                    _call(
                        "format_srd_information_projection",
                        {"message": "{caster_id}"},
                        {"_visual_caster": "_info_projection_message"},
                    ),
                    _set(
                        "_visual_labels", {name: repr(label) for name, label in BODY_LABELS.items()}
                    ),
                    _require(
                        "spell in _visual_labels", "This spell has no public physical-body owner."
                    ),
                    _when(
                        "spell == 'wall_of_stone'",
                        [
                            _set("_visual_wall", "state.solid_spell_wall_sources[source_id]"),
                            _require(wall_source, "Present only this actual Stone source's panel."),
                            _require(wall_stamp, "The Stone panel's native source stamp changed."),
                            _require(
                                "_visual_wall.permanent == true or state.lasting_tick < "
                                "_visual_wall.expires_tick",
                                "The nonpermanent Stone source has expired.",
                            ),
                        ],
                        [
                            _when(
                                "spell == 'mage_hand' and "
                                "_visual_input_body.metadata.srd_object_force != null",
                                [
                                    _require(
                                        hand_source,
                                        "Present only this caster's exact spectral hand.",
                                    ),
                                ],
                                [
                                    _require(
                                        same_source,
                                        "The born body's exact spell/caster identity changed.",
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _when(
                        "_visual_input_body.metadata.player == null",
                        [
                            _set(
                                "_visual_public",
                                {
                                    TAG: True,
                                    "player": {
                                        "label": "_visual_labels[spell]",
                                        "kind": "'spell_body'",
                                        "state": "'Active'",
                                        "interactive": True,
                                    },
                                },
                            ),
                            _set(
                                "_visual_metadata",
                                "merge(_visual_input_body.metadata, _visual_public)",
                            ),
                            _set("_visual_body_patch", {"metadata": "_visual_metadata"}),
                            _set("_visual_body", "merge(_visual_input_body, _visual_body_patch)"),
                            _set("_visual_added", True),
                        ],
                    ),
                ],
            ),
        ],
    )


def _anchor_tool():
    """Omitted or private anchors do not block an unrelated arena or disclose its position."""
    keys = (
        "label",
        "kind",
        "state",
        "stance",
        "side",
        "color",
        "icon",
        "interactive",
        "height_label",
        "material_label",
    )
    return _environment_tool(
        "Read one current active, player-known anchor without requiring an omitted body.",
        [_parameter("subject")],
        [
            _set("_visual_anchor_known", False),
            _set("_visual_anchor_position", None),
            _set("_visual_anchor_id", None),
            _set("_visual_anchor_count", 0),
            _call(
                "format_srd_information_projection",
                {"message": "{subject}"},
                {"_visual_anchor_subject": "_info_projection_message"},
            ),
            _set("_visual_anchor_rows", "state.spatial_scene.entities"),
            _each(
                "_visual_anchor_rows",
                "_visual_anchor_row",
                [
                    _when(
                        "_visual_anchor_row.active == true and "
                        "(_visual_anchor_row.id == _visual_anchor_subject or "
                        "_visual_anchor_row.metadata.participant_ref == _visual_anchor_subject)",
                        [
                            _set("_visual_anchor_count", "_visual_anchor_count + 1"),
                            _set("_visual_anchor_id", "_visual_anchor_row.id"),
                            _set("_visual_anchor_position", "_visual_anchor_row.position"),
                            _set("_visual_anchor_player", "_visual_anchor_row.metadata.player"),
                            _when(
                                "_visual_anchor_player != null and _visual_anchor_player.label != "
                                "null and "
                                "_visual_anchor_player.kind != null",
                                [
                                    _set("_visual_anchor_keys", [repr(key) for key in keys]),
                                    _set("_visual_anchor_key_count", 0),
                                    _each(
                                        "_visual_anchor_keys",
                                        "_visual_anchor_key",
                                        [
                                            _when(
                                                "_visual_anchor_key in _visual_anchor_player",
                                                [
                                                    _set(
                                                        "_visual_anchor_key_count",
                                                        "_visual_anchor_key_count + 1",
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                    _set(
                                        "_visual_anchor_known",
                                        "len(_visual_anchor_player) == "
                                        "_visual_anchor_key_count and "
                                        "_visual_anchor_player.label != '' and "
                                        "_visual_anchor_player.kind != '' and "
                                        "len(_visual_anchor_player.label) <= 256 and "
                                        "len(_visual_anchor_player.kind) <= 64",
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _require(
                "_visual_anchor_count <= 1", "Resolve the actual duplicate spell-anchor body."
            ),
        ],
    )


def _zone(shape, suffix, label, explanation):
    return [
        _set(
            "_visual_zone_metadata",
            {
                TAG: True,
                "source_key": "_visual_source",
                "player": {
                    "label": repr(label),
                    "kind": "'spell_extent'",
                    "state": "'Extent only'",
                    "height_label": repr(explanation),
                    "interactive": False,
                },
            },
        ),
        _when(
            "_visual_anchor_id != null",
            [
                _set("_visual_zone_anchor", {"display_anchor": "_visual_anchor_id"}),
                _set("_visual_zone_metadata", "merge(_visual_zone_metadata, _visual_zone_anchor)"),
            ],
        ),
        _call(
            "format_srd_information_projection",
            {"message": "known-spell:{_visual_zone_index}:" + suffix},
            ("_info_projection_message",),
        ),
        _push(
            "_map_next_zones",
            {
                "id": "_info_projection_message",
                "map_id": "state.spatial_scene.map.id",
                "shape": shape,
                "metadata": "_visual_zone_metadata",
            },
        ),
        _set("_visual_zone_index", "_visual_zone_index + 1"),
    ]


def _round_extent(radius, suffix, label):
    """Project a nominal radial XY extent, clipping with an explicitly labeled box."""
    lower, upper = "state.spatial_scene.map.bounds.min", "state.spatial_scene.map.bounds.max"
    bottom = f"max(_visual_origin[2] - {radius}, {lower}[2])"
    top = f"min(_visual_origin[2] + {radius}, {upper}[2])"
    box = {
        "kind": "'box'",
        "min": [f"max(_visual_origin[{i}] - {radius}, {lower}[{i}])" for i in range(3)],
        "max": [f"min(_visual_origin[{i}] + {radius}, {upper}[{i}])" for i in range(3)],
    }
    fits = " and ".join(
        f"_visual_origin[{i}] - {radius} >= {lower}[{i}] and "
        f"_visual_origin[{i}] + {radius} <= {upper}[{i}]"
        for i in (0, 1)
    )
    return [
        _set("_visual_clip", box),
        _when(
            " and ".join(f"_visual_clip.max[{i}] > _visual_clip.min[{i}]" for i in range(3)),
            [
                _when(
                    fits,
                    _zone(
                        {
                            "kind": "'cylinder'",
                            "center": ["_visual_origin[0]", "_visual_origin[1]", bottom],
                            "radius": radius,
                            "height": f"({top}) - ({bottom})",
                        },
                        suffix,
                        label,
                        "Nominal radius extent; native body, plane and barrier rules "
                        "decide effects",
                    ),
                    _zone(
                        "_visual_clip",
                        suffix + "-clipped",
                        label + " · clipped extent",
                        "Extent continues beyond map; native qualification decides effects",
                    ),
                ),
            ],
        ),
    ]


def _fire_zones():
    return [
        _set("_visual_area_ids", []),
        _when(
            "state.persistent_spell_area_ids != null",
            [_set("_visual_area_ids", "state.persistent_spell_area_ids")],
        ),
        _each(
            "_visual_area_ids",
            "_visual_area_id",
            [
                _set("_visual_anchor_id", None),
                _set("_visual_area", "state.persistent_spell_areas[_visual_area_id]"),
                _when(
                    "_visual_area.spell == 'wall_of_fire' and _visual_area."
                    + KNOWN
                    + " == true and "
                    "_visual_area." + MAP + " == state.spatial_scene.map.id",
                    [
                        _call(
                            "qualify_srd_wall_of_fire_source",
                            {
                                "caster_state": "{_visual_area.caster}",
                                "area_id": "{_visual_area_id}",
                            },
                            ("_sphere_source_current",),
                        ),
                        _when(
                            "_sphere_source_current == true",
                            [
                                _set("_visual_source", "_visual_area_id"),
                                _when(
                                    "_visual_area.volume.kind == 'box'",
                                    _zone(
                                        "_visual_area.volume",
                                        "wall",
                                        "Wall of Fire",
                                        "Actual wall; hot-side damage is qualified separately",
                                    ),
                                    [
                                        _require(
                                            "_visual_area.volume.kind == 'annulus'",
                                            "This Fire wall has no supported display volume.",
                                        ),
                                        *_zone(
                                            "_visual_area.volume.outer",
                                            "ring",
                                            "Wall of Fire · ring extent",
                                            "Hollow interior is not the wall; "
                                            "hot-side damage is qualified separately",
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    ]


def _aura_zones():
    return [
        _set("_visual_ward_ids", []),
        _when(
            "state.spell_ward_source_ids != null",
            [_set("_visual_ward_ids", "state.spell_ward_source_ids")],
        ),
        _each(
            "_visual_ward_ids",
            "_visual_ward_id",
            [
                _set("_visual_anchor_id", None),
                _set("_visual_ward", "state.spell_ward_sources[_visual_ward_id]"),
                _when(
                    "_visual_ward.spell_id == 'aura_of_life' and _visual_ward."
                    + KNOWN
                    + " == true",
                    [
                        _call(
                            "check_srd_spell_ward_source",
                            {
                                "caster_state": "{_visual_ward.caster_state_id}",
                                "source_id": "{_visual_ward_id}",
                            },
                            ("_ward_source_active",),
                        ),
                        _when(
                            "_ward_source_active == true",
                            [
                                _call(
                                    ANCHOR,
                                    {"subject": "{_visual_ward.caster_state_id}"},
                                    (
                                        "_visual_anchor_known",
                                        "_visual_anchor_position",
                                        "_visual_anchor_id",
                                    ),
                                ),
                                _when(
                                    "_visual_anchor_known == true",
                                    [
                                        _set("_visual_source", "_visual_ward_id"),
                                        _set("_visual_origin", "_visual_anchor_position"),
                                        *_round_extent(
                                            30, "aura", "Aura of Life · 30-foot nominal extent"
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    ]


def _light_zones():
    return [
        _set("_visual_records", []),
        _when("state.turn_effects != null", [_set("_visual_records", "state.turn_effects")]),
        _each(
            "_visual_records",
            "_visual_record",
            [
                _set("_visual_anchor_id", None),
                _when(
                    "_visual_record." + KNOWN + " == true",
                    [
                        _when(
                            "_visual_record.kind == 'illumination_spell' and "
                            "(_visual_record.spell == 'light' or _visual_record.spell == "
                            "'daylight') and "
                            "(_visual_record.source != '' or _visual_record."
                            + MAP
                            + " == state.spatial_scene.map.id)",
                            [
                                _call(
                                    "check_srd_illumination_record",
                                    {
                                        "record": "{_visual_record}",
                                        "caster_state": "{_visual_record.caster}",
                                    },
                                    (
                                        "_illumination_active",
                                        "_illumination_origin",
                                        "_illumination_plane",
                                        "_illumination_covered",
                                    ),
                                ),
                                _when(
                                    "_illumination_active == true and "
                                    "_illumination_covered != true and "
                                    "_illumination_origin != null and _illumination_plane != null",
                                    [
                                        _set("_visual_anchor_known", "_visual_record.source == ''"),
                                        _when(
                                            "_visual_record.source != ''",
                                            [
                                                _call(
                                                    "srd_information_scene_entity",
                                                    {"subject": "{_visual_record.source}"},
                                                    ("_info_scene_entity",),
                                                ),
                                                _set(
                                                    "_visual_holder",
                                                    "_info_scene_entity.metadata.carried_by",
                                                ),
                                                _when(
                                                    "_visual_holder == null or "
                                                    "_visual_holder == ''",
                                                    [
                                                        _set(
                                                            "_visual_holder",
                                                            "_info_scene_entity.metadata.worn_by",
                                                        ),
                                                    ],
                                                ),
                                                _when(
                                                    "_visual_holder != null and "
                                                    "_visual_holder != ''",
                                                    [
                                                        _call(
                                                            ANCHOR,
                                                            {"subject": "{_visual_holder}"},
                                                            (
                                                                "_visual_anchor_known",
                                                                "_visual_anchor_id",
                                                            ),
                                                        ),
                                                    ],
                                                    [
                                                        _call(
                                                            ANCHOR,
                                                            {"subject": "{_visual_record.source}"},
                                                            (
                                                                "_visual_anchor_known",
                                                                "_visual_anchor_id",
                                                            ),
                                                        ),
                                                    ],
                                                ),
                                            ],
                                        ),
                                        _when(
                                            "_visual_anchor_known == true",
                                            [
                                                _call(
                                                    "format_srd_information_projection",
                                                    {
                                                        "message": (
                                                            "{_visual_record.caster}:"
                                                            "{_visual_record.spell}:"
                                                            "{_visual_record.started_tick}:"
                                                            "{_visual_record.instance}"
                                                        )
                                                    },
                                                    {"_visual_source": "_info_projection_message"},
                                                ),
                                                _set("_visual_origin", "_illumination_origin"),
                                                *_round_extent(
                                                    "_visual_record.bright_radius",
                                                    "bright",
                                                    "Magical bright-light extent",
                                                ),
                                                *_round_extent(
                                                    "_visual_record.dim_radius",
                                                    "dim",
                                                    "Magical outer dim-light extent · "
                                                    "bright core included",
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        _when(
                            "_visual_record.kind == 'actor_spell_modifier' and "
                            "_visual_record.spell == 'fire_shield'",
                            [
                                _set("_visual_anchor_id", None),
                                _call(
                                    "check_srd_actor_spell_light",
                                    {
                                        "actor_state": "{_visual_record.target}",
                                        "lasting_state": "{_lasting_state_id}",
                                    },
                                    (
                                        "_actor_spell_bright_light_feet",
                                        "_actor_spell_dim_light_feet",
                                    ),
                                ),
                                _when(
                                    "_actor_spell_dim_light_feet > 0",
                                    [
                                        _call(
                                            ANCHOR,
                                            {"subject": "{_visual_record.target}"},
                                            (
                                                "_visual_anchor_known",
                                                "_visual_anchor_position",
                                                "_visual_anchor_id",
                                            ),
                                        ),
                                        _when(
                                            "_visual_anchor_known == true",
                                            [
                                                _call(
                                                    "format_srd_information_projection",
                                                    {
                                                        "message": (
                                                            "{_visual_record.caster}:"
                                                            "fire_shield:"
                                                            "{_visual_record.target}:"
                                                            "{_visual_record.started_tick}"
                                                        )
                                                    },
                                                    {"_visual_source": "_info_projection_message"},
                                                ),
                                                _set("_visual_origin", "_visual_anchor_position"),
                                                *_round_extent(
                                                    "_actor_spell_bright_light_feet",
                                                    "bright",
                                                    "Fire Shield bright-light extent",
                                                ),
                                                *_round_extent(
                                                    "_actor_spell_dim_light_feet",
                                                    "dim",
                                                    "Fire Shield outer dim-light extent · "
                                                    "bright core included",
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    ]


def _refresh_tool():
    """Follow qualified current sources; never turn a cached light origin into current geometry."""

    def retag(value):
        if isinstance(value, str):
            return value.replace("srd_lite_retained_overlay", TAG)
        if isinstance(value, dict):
            return {key: retag(item) for key, item in value.items()}
        if isinstance(value, list):
            return [retag(item) for item in value]
        return value

    return _environment_tool(
        "Refresh known spell extents from the current native sources without changing mechanics.",
        [],
        [
            _when(
                "combat.encounter_active == true and state.spatial_scene != null",
                [
                    _set("_map_next_zones", []),
                    _set("_visual_zone_index", 0),
                    _set("_visual_anchor_id", None),
                    _set("_visual_old_zones", "state.spatial_scene.zones"),
                    _each(
                        "_visual_old_zones",
                        "_visual_old_zone",
                        [
                            _when(
                                "_visual_old_zone.metadata." + TAG + " != true",
                                [_push("_map_next_zones", "_visual_old_zone")],
                            ),
                        ],
                    ),
                    *_fire_zones(),
                    *_aura_zones(),
                    *_light_zones(),
                    _require(
                        "len(_map_next_zones) <= 256",
                        "Known spell extents exceed the existing scene-zone limit.",
                    ),
                    *retag(_zone_changes()),
                    _set("_visual_zone_index", 0),
                    _each(
                        "_map_next_zones",
                        "_visual_next_zone",
                        [
                            _when(
                                "_map_zones_changed != true and _visual_next_zone.metadata."
                                + TAG
                                + " == true",
                                [
                                    _set(
                                        "_visual_previous_zone",
                                        "state.spatial_scene.zones[_visual_zone_index]",
                                    ),
                                    _when(
                                        "_visual_next_zone.metadata.source_key != "
                                        "_visual_previous_zone.metadata.source_key or "
                                        "_visual_next_zone.metadata.display_anchor != "
                                        "_visual_previous_zone.metadata.display_anchor",
                                        [_set("_map_zones_changed", True)],
                                    ),
                                ],
                            ),
                            _set("_visual_zone_index", "_visual_zone_index + 1"),
                        ],
                    ),
                    _when(
                        "_map_zones_changed == true",
                        [
                            _set("state.spatial_scene.zones", "_map_next_zones"),
                            _set(
                                "state.spatial_scene.map.revision",
                                "state.spatial_scene.map.revision + 1",
                            ),
                            *_snapshot(),
                        ],
                    ),
                ],
            )
        ],
    )


def _present_rows(field, spell="{spell}", source="", known="{known_spell_effect}"):
    return [
        _set("_visual_body_capsule", [field]),
        _call(
            BODY,
            {
                "body_capsule": "{_visual_body_capsule}",
                "spell": spell,
                "caster_id": "{_caster_state_id}",
                "source_id": source,
                KNOWN: known,
                "lasting_state": "{_lasting_state_id}",
            },
            ("_visual_body", "_visual_added"),
        ),
        _set(field, "_visual_body"),
    ]


def _admitted_birth_presentation(tools):
    """Apply retained positive birth knowledge only after the existing exact actor admission."""
    owner = tools.get("reconcile_srd_lite_exploration_created_actor")
    if owner is None:
        return
    points = [
        (rows, index)
        for rows in _lists(owner["effects"])
        for index, row in enumerate(rows)
        if row.get("set", {}).get("field")
        == "lasting_state.exploration_created_actor_records.{_actor_state_id}.body_admitted"
    ]
    if len(points) != 1:
        raise ValueError("Keep the actual successful created-actor admission marker")
    rows, index = points[0]
    rows[index + 1 : index + 1] = [
        _call(
            "srd_information_scene_entity",
            {"subject": "{_actor_state_id}"},
            ("_info_scene_entity",),
        ),
        *_present_rows(
            "_info_scene_entity", "{_birth.spell}", known="{_birth.known_spell_effect == true}"
        ),
        _when(
            "_visual_added == true",
            [
                _set("_visual_updated", []),
                _set("_visual_existing", "lasting_state.spatial_scene.entities"),
                _each(
                    "_visual_existing",
                    "_visual_existing_body",
                    [
                        _when(
                            "_visual_existing_body.id == _actor_state_id",
                            [_push("_visual_updated", "_visual_body")],
                            [_push("_visual_updated", "_visual_existing_body")],
                        ),
                    ],
                ),
                _set("lasting_state.spatial_scene.entities", "_visual_updated"),
                _set(
                    "lasting_state.spatial_scene.map.revision",
                    "lasting_state.spatial_scene.map.revision + 1",
                ),
                *_snapshot(),
            ],
        ),
    ]


def _stamp_producers(tools):
    targets = set()
    body_fields = {
        "create_srd_source_actor_scene": ("_created_actor_scene", "{spell}", ""),
        "cast_srd_stone_spell_wall": (
            "_solid_wall_new_panel",
            "wall_of_stone",
            "{_new_solid_wall_id}",
        ),
        "cast_srd_mage_hand": ("_world_created_entity", "mage_hand", "{_utility_source_id}"),
    }
    for name, (field, spell, source) in body_fields.items():
        if name not in tools:
            continue
        owner, count = tools[name], 0
        for rows in list(_lists(owner["effects"])):
            for index in range(len(rows) - 1, -1, -1):
                if rows[index].get("set", {}).get("field") == field:
                    rows[index + 1 : index + 1] = _present_rows(field, spell, source)
                    count += 1
        if not count:
            raise ValueError("Keep the selected native spell body producer: " + name)
        targets.add(name)
    records = {
        "cast_srd_spell_ward": "lasting_state.spell_ward_sources.{_new_ward_id}",
        "install_srd_illumination_source": "_light_install_record",
        "install_srd_lite_exploration_light": "_light_install_record",
        "cast_srd_mage_hand": "_utility_record",
        "cast_spell_environment": "lasting_state.persistent_spell_areas.{_environment_cast_id}",
    }
    for name, field in records.items():
        if name not in tools:
            continue
        owner, count = tools[name], 0
        for rows in _lists(owner["effects"]):
            for row in rows:
                setting = row.get("set", {})
                if setting.get("field") == field and isinstance(setting.get("value"), dict):
                    setting["value"][KNOWN] = KNOWN
                    if name in ("install_srd_illumination_source", "cast_spell_environment"):
                        setting["value"][MAP] = "_visual_birth_map"
                    count += 1
        if not count:
            raise ValueError("Keep the native spell source record: " + name)
        if name in ("install_srd_illumination_source", "cast_spell_environment"):
            owner["effects"][:0] = [
                _set("_visual_birth_map", None),
                _when(
                    "combat.encounter_active == true and lasting_state.spatial_scene != null",
                    [_set("_visual_birth_map", "lasting_state.spatial_scene.map.id")],
                ),
            ]
        targets.add(name)
    owner = tools.get("create_srd_source_actor_scene")
    if owner:
        for rows in _lists(owner["effects"]):
            for row in rows:
                setting = row.get("set", {})
                if (
                    setting.get("field")
                    == "lasting_state.exploration_created_actor_records.{summon_id}"
                ):
                    setting["value"][KNOWN] = KNOWN
    owner = tools.get("install_srd_actor_spell_modifier")
    if owner:
        records = [
            row["list_push"]["value"]
            for rows in _lists(owner["effects"])
            for row in rows
            if isinstance(row.get("list_push", {}).get("value"), dict)
            and row["list_push"]["value"].get("kind") == "'actor_spell_modifier'"
        ]
        if len(records) != 1:
            raise ValueError("Keep the native actor modifier source installer")
        records[0][KNOWN] = KNOWN
        targets.add("install_srd_actor_spell_modifier")
    return targets


def _forward_knowledge(tools, targets):
    """Carry one fact along actual cast edges, stopping at existing retained choice capsules."""
    stops = {
        "cast_spell",
        "cast_spell_node",
        "cast_spell_outside_combat",
        "finish_srd_retained_cast_tail",
    }
    reached = set(targets)
    while True:
        additions = {
            name
            for name, owner in tools.items()
            if name not in reached
            and any(
                row.get("call") in reached
                for rows in _lists(owner.get("effects", []))
                for row in rows
            )
        }
        additions -= stops
        if not additions:
            break
        reached.update(additions)
    for name in reached | (stops & set(tools)):
        if name == "finish_srd_retained_cast_tail":
            continue
        owner = tools[name]
        if not any(p["name"] == KNOWN for p in owner["parameters"]):
            owner["parameters"].append(_known_parameter())
        captured = False
        for rows in _lists(owner.get("effects", [])):
            for row in rows:
                if row.get("call") in reached or row.get("call") == "cast_spell":
                    row.setdefault("params", {})[KNOWN] = "{" + KNOWN + "}"
                setting = row.get("set", {})
                field, value = setting.get("field", ""), setting.get("value")
                if field == "_retained_cast_tail_choices" or field.endswith(
                    ".retained_cast_choices"
                ):
                    if value == [CAPSULE_FACT_MERGE] and captured:
                        continue
                    if (
                        not isinstance(value, list)
                        or len(value) != 1
                        or not isinstance(value[0], dict)
                    ):
                        raise ValueError("Keep the checked retained cast capsule")
                    value[0][KNOWN] = KNOWN
                    captured = True
    tail = tools.get("finish_srd_retained_cast_tail")
    if tail:
        choices = tail.get("metadata", {}).get("retained_spell_reactions_v1_choices")
        if not isinstance(choices, list):
            raise ValueError("Keep the retained spell choice metadata")
        choices.append(KNOWN)
        unpacks = [
            (rows, index)
            for rows in _lists(tail["effects"])
            for index, row in enumerate(rows)
            if row.get("set")
            == {
                "field": "_retained_cast_tail_choices",
                "value": "_retained_cast_tail_choices[0]",
            }
        ]
        if len(unpacks) != 1:
            raise ValueError("Keep exactly one actual retained cast choice unpack")
        rows, index = unpacks[0]
        rows.insert(
            index + 1,
            _set("_visual_cast_known", "_retained_cast_tail_choices." + KNOWN + " == true"),
        )
        for rows in _lists(tail["effects"]):
            for row in rows:
                if row.get("call") in reached:
                    row.setdefault("params", {})[KNOWN] = "{_visual_cast_known}"
    begin = tools.get("begin_srd_spell_action")
    if begin:
        begin["parameters"].append(_known_parameter())
        stamps = [
            (rows, i)
            for rows in _lists(begin["effects"])
            for i, row in enumerate(rows)
            if row.get("set", {}).get("field")
            == "lasting_state.pending_srd_actions.{_pending_action_id}.spell"
        ]
        if len(stamps) != 1:
            raise ValueError("Keep the actual staged spell allocation")
        rows, index = stamps[0]
        rows.insert(
            index,
            _set(
                "lasting_state.pending_srd_actions.{_pending_action_id}.parameters." + KNOWN, KNOWN
            ),
        )
    complete = tools.get("complete_srd_pending_action")
    if complete:
        dispatches = [
            (rows, index, row)
            for rows in _lists(complete["effects"])
            for index, row in enumerate(rows)
            if row.get("call") == "cast_spell"
            and row.get("params", {}).get("spell") == "{_pending_action.parameters.spell}"
        ]
        if len(dispatches) != 1:
            raise ValueError("Keep exactly one actual staged spell dispatcher")
        rows, index, row = dispatches[0]
        rows.insert(
            index, _set("_visual_pending_known", "_pending_action.parameters." + KNOWN + " == true")
        )
        row["params"][KNOWN] = "{_visual_pending_known}"
    for name in stops & set(tools) | ({"begin_srd_spell_action"} & set(tools)):
        if name != "finish_srd_retained_cast_tail":
            tools[name]["description"] += GUIDANCE
    return reached


def _refresh_binding(name, tool):
    """Use the owner's saved-state binding, including its exact public wrapper."""
    if any(p["name"] == "lasting_state" for p in tool["parameters"]):
        return {"lasting_state": "{_lasting_state_id}"}
    wrappers = [
        row
        for rows in _lists(tool["effects"])
        for row in rows
        if row.get("call") == name + "_node"
        and row.get("params", {}).get("lasting_state") == "state"
    ]
    if len(wrappers) != 1:
        raise ValueError("Use the actual mutable saved-state binding for map refresh: " + name)
    return {"lasting_state": "state"}


def bind_persistent_spell_map(runtime):
    """Draft late binder; root must qualify exact hooks and expansion before adoption."""
    tools = runtime["tools"]
    if any(name in tools for name in (BODY, REFRESH, ANCHOR)):
        raise ValueError("Persistent spell map presentation was already bound")
    targets = _stamp_producers(tools)
    reached = _forward_knowledge(tools, targets)
    tools[BODY], tools[REFRESH] = _body_tool(), _refresh_tool()
    tools[ANCHOR] = _anchor_tool()
    _admitted_birth_presentation(tools)
    # Refresh at existing settlement/clock boundaries, not inside source-reading loops.
    refresh_owners = targets & {
        "cast_spell_environment",
        "cast_srd_spell_ward",
        "install_srd_illumination_source",
        "install_srd_actor_spell_modifier",
    }
    refresh_owners |= {
        "finish_srd_shared_action_settlements",
        "advance_lasting_effects",
        "end_concentration",
        "set_srd_illumination_cover",
        "dismiss_srd_illumination_spell",
        "move_srd_scene_creature",
        "start_combat_encounter_entities",
        "start_group_combat_encounter",
    } & set(tools)
    for name in refresh_owners:
        tools[name]["effects"].append(_call(REFRESH, _refresh_binding(name, tools[name])))
    return sorted(reached | refresh_owners | {BODY, REFRESH, ANCHOR})
