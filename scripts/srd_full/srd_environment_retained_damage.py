"""Retained damage spells keep their paid source and settle later genuine uses."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS, _array
    from .srd_spell_environment import (
        PROFILES,
        _call,
        _effect_lists,
        _entity,
        _environment_tool,
        _optional,
        _param,
        _public,
        _require,
        _set,
        _when,
    )
else:
    from srd_scene_relations import POSITION_EXPORTS, _array
    from srd_spell_environment import (
        PROFILES,
        _call,
        _effect_lists,
        _entity,
        _environment_tool,
        _optional,
        _param,
        _public,
        _require,
        _set,
        _when,
    )

CALL_LIGHTNING_SHA = "9abd042be323813dfa45923df963dfff9a6a25a032f5daec712ec85c5955c134"
CALL_LIGHTNING_PROFILE = PROFILES["call_lightning"]


def _sight(point, plane, caster):
    """Require actual saved eye/source/senses sight, forwarding the actual plane."""
    return [
        _call(
            "resolve_srd_illumination_point_sight",
            {
                "observer_state": "{" + caster + "}",
                "point": "{" + point + "}",
                "perception_plane": "{" + plane + "}",
            },
            ("_illumination_point_can_see",),
        ),
        _require(
            "_illumination_point_can_see == true",
            "The caster must actually see this lightning point.",
        ),
    ]


def _bolt_space(point, source):
    """A chosen bolt must remain below the genuine saved cloud on its actual plane."""
    return [
        _require(
            f"{point}[2] < {source}.origin[2] and "
            f"({point}[0] - {source}.origin[0]) ** 2 + "
            f"({point}[1] - {source}.origin[1]) ** 2 <= 3600",
            "Choose the actual seen point under this cloud's sixty-foot radius.",
        ),
        _require(
            " and ".join(
                f"{point}[{i}] >= state.spatial_scene.map.bounds.min[{i}] and "
                f"{point}[{i}] <= state.spatial_scene.map.bounds.max[{i}]"
                for i in range(3)
            ),
            "The selected bolt point must be in the admitted map.",
        ),
    ]


def _source_tool():
    return _environment_tool(
        "Authenticate the actual paid Call Lightning source and its current caster concentration.",
        [_entity("caster_state"), _param("area_id")],
        [
            _set("_lightning_source", "state.persistent_spell_areas[area_id]"),
            _set(
                "_lightning_source_current",
                "_lightning_source.profile == 'environment' and "
                "_lightning_source.mode == 'call_lightning' and "
                "_lightning_source.spell == 'call_lightning' and "
                f"_lightning_source.source_sha256 == {CALL_LIGHTNING_SHA!r}",
            ),
            _set(
                "_lightning_source_current",
                "_lightning_source_current == true and _lightning_source.active == true and "
                "state.lasting_tick < _lightning_source.expires_tick and "
                "caster_state.is_alive == true and caster_state.hp > 0 and "
                "_lightning_source.caster == _caster_state_id",
            ),
            _set(
                "_lightning_source_current",
                "_lightning_source_current == true and caster_state.environment_area_id == area_id "
                "and caster_state.concentration_spell_id == 'call_lightning' and "
                "caster_state.concentration_effect_family == 'spell_environment'",
            ),
            _when(
                "_lightning_source_current == true",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_caster_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _set(
                        "_lightning_source_current",
                        "_info_scene_entity.metadata.plane == _lightning_source.plane",
                    ),
                ],
            ),
        ],
    )


def _target_tool(existing):
    """Reuse exact Box/Sphere distance and physical exposure at the actual saved bolt point."""
    tool = deepcopy(existing["qualify_srd_environment_sphere_target"])
    for effects in _effect_lists(tool["effects"]):
        for effect in effects:
            if effect.get("call") == "qualify_srd_environment_sphere_source":
                effect["call"] = "qualify_srd_call_lightning_source"
                effect["export_context"] = {"_sphere_source_current": "_lightning_source_current"}
    source = next(
        i
        for i, e in enumerate(tool["effects"])
        if e.get("set", {}).get("field") == "_sphere_target_source"
    )
    tool["effects"][source : source + 1] = [
        _set("_lightning_target_source", tool["effects"][source]["set"]["value"]),
        _set(
            "_sphere_target_source",
            {
                "caster": "_lightning_target_source.caster",
                "plane": "_lightning_target_source.plane",
                "origin": "_lightning_target_source.bolt_point",
                "radius": 5,
            },
        ),
    ]
    tool["description"] = (
        "Qualify actual living creature-space within five feet of the genuine bolt point."
    )
    return tool


def _event_tool(existing):
    """Keep the established save/typed packet lifecycle, consuming one exact bolt receipt."""
    tool = deepcopy(existing["resolve_srd_environment_sphere_event"])
    source_index = next(
        i
        for i, e in enumerate(tool["effects"])
        if e.get("set", {}).get("field") == "_sphere_event_source"
    )
    clock_end = next(
        i
        for i, e in enumerate(tool["effects"])
        if e.get("set", {}).get("field") == "_sphere_prior_targets"
    )
    tool["effects"][source_index + 1 : clock_end] = [
        _require(
            "_sphere_event_source.spell == 'call_lightning' and "
            "_sphere_event_source.appearance_in_progress == true and "
            "_sphere_event_source.appearance_tick == state.lasting_tick and "
            "_sphere_event_source.bolt_sequence > 0 and "
            "_target_state_id in _sphere_event_source.appearance_targets",
            "Settle only a creature captured by this genuine pending lightning bolt.",
        ),
        _set("_sphere_clock", "'lightning_bolt'"),
        _set("_sphere_turn", "_sphere_event_source.bolt_sequence"),
    ]
    for effects in list(_effect_lists(tool["effects"])):
        for i, effect in enumerate(effects):
            if effect.get("call") == "qualify_srd_environment_sphere_target":
                effect["call"] = "qualify_srd_call_lightning_target"
            elif effect.get("call") == "qualify_srd_environment_sphere_effect":
                effects[i] = _set("_sphere_effect_applies", True)
            if effect.get("set", {}).get("field") == "target_state.last_environment_sphere_save":
                effect["set"]["field"] = "target_state.last_call_lightning_save"
                record = effect["set"]["value"]
                record["origin"] = "_sphere_event_source.bolt_point"
                record["storm_bonus"] = "_sphere_event_source.storm_bonus"
                record["source_sequence"] = "_sphere_event_source.sequence"
        begin = next(
            (i for i, e in enumerate(effects) if e.get("set", {}).get("field") == "_sphere_dice"),
            None,
        )
        if begin is not None:
            end = next(
                i
                for i, e in enumerate(effects)
                if e.get("set", {}).get("field") == "_sphere_roll_divisor"
            )
            effects[begin:end] = [
                _set("_sphere_damage_roll", "_sphere_event_source.bolt_damage_roll"),
                _set("_sphere_amount", "_sphere_event_source.bolt_damage_amount"),
                _set(
                    "_sphere_unallocated_penalty", "_sphere_event_source.bolt_unallocated_penalty"
                ),
            ]
    return tool


def _bolt_tool(existing):
    scene = deepcopy(existing["resolve_srd_environment_sphere_scene"])
    capture, *scene["effects"] = scene["effects"]
    assert "_environment_area_key" in capture.get("export_context", {})
    for effects in _effect_lists(scene["effects"]):
        for effect in effects:
            if effect.get("call") == "qualify_srd_environment_sphere_target":
                effect["call"] = "qualify_srd_call_lightning_target"
            if effect.get("call") == "resolve_srd_environment_sphere_event":
                effect["call"] = "settle_srd_call_lightning_target"
            if "require" in effect:
                effect["require"]["condition"] = (
                    "_sphere_scene_source.profile == 'environment' and "
                    "_sphere_scene_source.spell == 'call_lightning' and "
                    "_sphere_scene_source.active == true"
                )
                effect["require"]["error"] = (
                    "Settle only this actual source's selected lightning bolt."
                )
    return _environment_tool(
        "Capture actual recipients and settle their saves with one shared native bolt roll.",
        [_entity("caster_state"), _param("area_id"), _array("point")],
        [
            _call(
                "qualify_srd_call_lightning_source",
                {
                    "caster_state": "{_caster_state_id}",
                    "area_id": "{area_id}",
                },
                ("_lightning_source_current", "_lightning_source"),
            ),
            _require(
                "_lightning_source_current == true",
                "This actual lightning source must still be current.",
            ),
            *_bolt_space("point", "_lightning_source"),
            *_sight("point", "_lightning_source.plane", "_caster_state_id"),
            _set("state.persistent_spell_areas.{area_id}.bolt_point", "point"),
            {
                "increment": {
                    "field": "state.persistent_spell_areas.{area_id}.bolt_sequence",
                    "amount": 1,
                }
            },
            _set("_lightning_dice", "_lightning_source.spell_level"),
            _when(
                "_lightning_source.storm_bonus == true",
                [_set("_lightning_dice", "_lightning_dice + 1")],
            ),
            _call(
                "format_srd_information_projection",
                {"message": "{_lightning_dice}d10"},
                {"_lightning_expression": "_info_projection_message"},
            ),
            {"dice": {"expression": "{_lightning_expression}"}, "store_as": "_lightning_roll"},
            _call(
                "resolve_srd_actor_spell_modifiers",
                {"target_state": "{_caster_state_id}", "lasting_state": "state"},
            ),
            _call(
                "modify_srd_actor_raw_damage_roll",
                {
                    "actor_state": "{_caster_state_id}",
                    "lasting_state": "state",
                    "amount": "{_lightning_roll.total}",
                },
                {"_lightning_amount": "_ray_raw_damage"},
            ),
            _set("state.persistent_spell_areas.{area_id}.bolt_damage_roll", "_lightning_roll"),
            _set(
                "state.persistent_spell_areas.{area_id}.bolt_damage_amount",
                "max(0, _lightning_amount)",
            ),
            _set(
                "state.persistent_spell_areas.{area_id}.bolt_unallocated_penalty",
                "max(0, -_lightning_amount)",
            ),
            *scene["effects"],
        ],
    )


def tools(existing):
    """Emit Call Lightning's live source, source-qualified bolt and paid later-use leaves."""
    return {
        "qualify_srd_call_lightning_source": _source_tool(),
        "qualify_srd_call_lightning_target": _target_tool(existing),
        "settle_srd_call_lightning_target": _event_tool(existing),
        "resolve_srd_call_lightning_bolt": _bolt_tool(existing),
        "call_srd_lightning_source": _public(
            _environment_tool(
                "Take a Magic action for another bolt from the actual retained storm cloud.",
                [_entity("caster_state"), _param("area_id"), _array("point")],
                [
                    _call(
                        "qualify_srd_call_lightning_source",
                        {
                            "caster_state": "{_caster_state_id}",
                            "area_id": "{area_id}",
                        },
                        ("_lightning_source_current",),
                    ),
                    _require(
                        "_lightning_source_current == true",
                        "The actual Call Lightning source must still be current.",
                    ),
                    _require(
                        "combat.turn_active == true and combat.active_actor == _caster_state_id",
                        "Use the caster's actual turn for this retained Magic action.",
                    ),
                    _call("spend_srd_action", {"magic_action": True}),
                    _call(
                        "resolve_srd_call_lightning_bolt",
                        {
                            "caster_state": "{_caster_state_id}",
                            "area_id": "{area_id}",
                            "point": "{point}",
                        },
                    ),
                ],
                public=True,
            ),
            "combat",
            "Call Lightning",
            [("Caster", "_caster_state_id"), ("Point", "point")],
        ),
    }


def _bind_public_lightning_point(public):
    """Keep this owning public choice singular when a generated runtime is re-emitted."""
    public["parameters"] = [
        p for p in public["parameters"] if p["name"] != "environment_lightning_point"
    ]
    public["parameters"].append(
        _optional(
            "environment_lightning_point",
            "array",
            None,
            min_items=3,
            max_items=3,
            items={"type": "number"},
        )
    )
    for effects in _effect_lists(public["effects"]):
        for effect in effects:
            if effect.get("call") == "cast_spell_environment":
                effect["params"]["lightning_point"] = "{environment_lightning_point}"


def bind_sources(changed):
    """Place the real cloud before source effects and settle its initial selected bolt."""
    cast = changed["cast_spell_environment"]
    cast["parameters"].append(
        _optional(
            "lightning_point", "array", None, min_items=3, max_items=3, items={"type": "number"}
        )
    )
    check = next(
        i
        for i, e in enumerate(cast["effects"])
        if e.get("condition")
        == (
            "_environment_profile.concentration == true and "
            "caster_state.concentration_spell_id != null"
        )
    )
    cast["effects"].insert(
        check,
        _when(
            "_environment_profile.environment_mode == 'call_lightning'",
            [
                _require(
                    "save_dc >= 1 and lightning_point != null",
                    "Use the actual caster DC and chosen initial lightning point.",
                ),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_caster_state_id}"},
                    POSITION_EXPORTS,
                ),
                _set("_lightning_cast_scene", "_info_scene_entity"),
                _require(
                    "_lightning_cast_scene.metadata.setting == 'indoors' or "
                    "_lightning_cast_scene.metadata.setting == 'outdoors'",
                    "Admit the caster's actual indoor/outdoor scene fact before casting.",
                ),
                _require(
                    "_lightning_cast_scene.metadata.weather == 'storm' or "
                    "_lightning_cast_scene.metadata.weather == 'other'",
                    "Admit the caster's actual storm/other weather scene fact before casting.",
                ),
                _set(
                    "_lightning_cast_storm",
                    "_lightning_cast_scene.metadata.setting == 'outdoors' and "
                    "_lightning_cast_scene.metadata.weather == 'storm'",
                ),
                _require(
                    "_environment_cast_origin[2] > _info_position[2] + "
                    "_info_scene_entity.height and "
                    "(_environment_cast_origin[0] - _info_position[0]) ** 2 + "
                    "(_environment_cast_origin[1] - _info_position[1]) ** 2 <= 3600",
                    "The chosen cloud must actually be above this caster.",
                ),
                _require(
                    " and ".join(
                        f"_environment_cast_origin[{i}] - 60 >= "
                        f"state.spatial_scene.map.bounds.min[{i}] and "
                        f"_environment_cast_origin[{i}] + 60 <= "
                        f"state.spatial_scene.map.bounds.max[{i}]"
                        for i in range(2)
                    ),
                    "The whole sixty-foot-radius cloud must fit the admitted map.",
                ),
                _require(
                    "_environment_cast_origin[2] >= state.spatial_scene.map.bounds.min[2] and "
                    "_environment_cast_origin[2] + 10 <= state.spatial_scene.map.bounds.max[2]",
                    "The whole ten-foot cloud height must fit the admitted map.",
                ),
                *_sight("_environment_cast_origin", "_environment_plane", "_caster_state_id"),
                _set("_lightning_cast_shape", {"origin": "_environment_cast_origin"}),
                *_bolt_space("lightning_point", "_lightning_cast_shape"),
                *_sight("lightning_point", "_environment_plane", "_caster_state_id"),
            ],
        ),
    )
    cast["effects"].append(
        _when(
            "_environment_profile.environment_mode == 'call_lightning'",
            [
                _set(
                    "state.persistent_spell_areas.{_environment_cast_id}.source_sha256",
                    repr(CALL_LIGHTNING_SHA),
                ),
                _set(
                    "state.persistent_spell_areas.{_environment_cast_id}.storm_bonus",
                    "_lightning_cast_storm",
                ),
                _set(
                    "state.persistent_spell_areas.{_environment_cast_id}.weather_subject",
                    "_lightning_cast_scene.id",
                ),
                _set(
                    "state.persistent_spell_areas.{_environment_cast_id}.setting",
                    "_lightning_cast_scene.metadata.setting",
                ),
                _set(
                    "state.persistent_spell_areas.{_environment_cast_id}.weather",
                    "_lightning_cast_scene.metadata.weather",
                ),
                _set("state.persistent_spell_areas.{_environment_cast_id}.bolt_sequence", 0),
                _set(
                    "state.persistent_spell_areas.{_environment_cast_id}.volume",
                    {
                        "kind": "'cylinder'",
                        "center": "_environment_cast_origin",
                        "radius": 60,
                        "height": 10,
                    },
                ),
                _call(
                    "resolve_srd_call_lightning_bolt",
                    {
                        "caster_state": "{_caster_state_id}",
                        "area_id": "{_environment_cast_id}",
                        "point": "{lightning_point}",
                    },
                ),
            ],
        )
    )
    _bind_public_lightning_point(changed["cast_spell"])
    dispel = changed["dispel_srd_environment_source"]
    for effects in _effect_lists(dispel["effects"]):
        for effect in effects:
            requirement = effect.get("require", {})
            condition = requirement.get("condition")
            if isinstance(condition, str) and "_environment_dispel_source.mode == '" in condition:
                requirement["condition"] = (
                    "(" + condition + ") or _environment_dispel_source.mode == 'call_lightning'"
                )
