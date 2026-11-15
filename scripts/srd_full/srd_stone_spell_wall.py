"""Stone panels reuse the genuine solid Wall body, lifetime and object attack owners."""

from copy import deepcopy

if __package__:
    from . import srd_solid_spell_walls as solid
    from .srd_actor_spell_modifiers import _call, _emit, _when
    from .srd_control_behaviors import _each
    from .srd_scene_relations import POSITION_EXPORTS, _snapshot
    from .srd_spell_barriers import _effect_lists, _tool
    from .srd_timed_actor_defenses import _entity, _load_runtime, _parameter, _require, _set
else:
    import srd_solid_spell_walls as solid
    from srd_actor_spell_modifiers import _call, _emit, _when
    from srd_control_behaviors import _each
    from srd_scene_relations import POSITION_EXPORTS, _snapshot
    from srd_spell_barriers import _effect_lists, _tool
    from srd_timed_actor_defenses import _entity, _load_runtime, _parameter, _require, _set

SPELL = "wall_of_stone"
SOURCE_SHA256 = "6ed3739a43979bc4bbb6f0dbe479e81b9e8c9d703d93aea89b5067f4b660dcdc"
PROFILES = {SPELL: dict(solid.PROFILES["wall_of_force"])}


def validate_profiles(definitions):
    """Validate the Stone row without changing the immutable Force row validator."""
    if SPELL in definitions and definitions[SPELL] != PROFILES[SPELL]:
        raise SystemExit("Wall of Stone differs from its source-owned profile.")


def _rewrite(value):
    """Derive only the printed Stone dimensions/name from the same real panel owner."""
    if isinstance(value, str):
        return value.replace("Force", "Stone").replace(solid.SOURCE_SHA256, SOURCE_SHA256)
    if isinstance(value, list):
        return [_rewrite(item) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite(item) for key, item in value.items()}
    if isinstance(value, float) and value == 1 / 96:
        return 1 / 4
    return value


def _prepare():
    """A fully supported straight wall merges with actual saved stone, never caller statistics."""
    tool = _rewrite(solid._prepare())
    tool["description"] = (
        "Validate ten contiguous10x10ft,6inch Stone panels on continuous admitted stone support."
    )
    tool["effects"] += [
        _set("_stone_support_found", False),
        _each(
            "_solid_wall_existing_geometry",
            "_stone_support",
            [
                _when(
                    "_stone_support.active == true and _stone_support.movement == 'blocking' and "
                    "_stone_support.volume.kind == 'box' and "
                    "_stone_support.metadata.material == 'stone' and "
                    "(_stone_support.metadata.plane == _solid_wall_plane or "
                    "(_stone_support.metadata.plane == null and _solid_wall_plane == 'material'))",
                    [
                        _when(
                            "abs(_stone_support.volume.max[2] - _solid_wall_min[2]) <= 0.000001 "
                            "and _stone_support.volume.min[0] <= _solid_wall_min[0] and "
                            "_stone_support.volume.max[0] >= _solid_wall_max[0] and "
                            "_stone_support.volume.min[1] <= _solid_wall_min[1] and "
                            "_stone_support.volume.max[1] >= _solid_wall_max[1]",
                            [_set("_stone_support_found", True)],
                        ),
                    ],
                ),
            ],
        ),
        _require(
            "_stone_support_found == true",
            "Selected Stone panels need continuous actual stone support at their complete base.",
        ),
        _call(
            "resolve_srd_illumination_point_sight",
            {
                "observer_state": "{_caster_state_id}",
                "point": "{origin}",
                "perception_plane": "{_solid_wall_plane}",
            },
            {"_solid_wall_light": "_illumination_point_lighting"},
        ),
    ]
    return tool


def _install():
    """The printed panel HP lives on its sole scene body and is never guessed from size tables."""
    tool = _rewrite(solid._install())
    tool["description"] = "Install source-qualified solid Stone panels from the actual paid cast."
    next(p for p in tool["parameters"] if p["name"] == "spell")["enum_values"] = [SPELL]
    for effects in _effect_lists(tool["effects"]):
        for effect in effects:
            if effect.get("call") == "prepare_srd_solid_spell_wall":
                effect["call"] = "prepare_srd_stone_spell_wall"
                effect["export_context"]["_solid_wall_light"] = "_solid_wall_light"
            field = effect.get("set", {}).get("field")
            value = effect.get("set", {}).get("value")
            if field and field.endswith(f".{solid.POOL}.{{_new_solid_wall_id}}"):
                value.update(permanent=False, dispel_ongoing=True)
            if field == "_solid_wall_new_panel":
                value["visibility"] = "'opaque'"
                metadata = value["metadata"]
                metadata.update(kind="'object'", invisible=False, is_object=True)
                metadata["lighting"] = "_solid_wall_light"
                metadata["srd_spell_object"] = {
                    "kind": "'wall_stone_panel'",
                    "source_sha256": repr(SOURCE_SHA256),
                    "source_id": "_new_solid_wall_id",
                    "serial": "state.lasting_serial",
                    "index": "_solid_wall_number",
                    "armor_class": 15,
                    "max_hp": 180,
                    "hp": 180,
                    "destroyed": False,
                }
    return tool


def _source_union(tool):
    """Allow only either exact printed source at shared lifetime/removal boundaries."""
    for effects in _effect_lists(tool["effects"]):
        for effect in effects:
            requirement = effect.get("require", {})
            if requirement.get("condition") == (
                f"_solid_wall_source.source_sha256 == {solid.SOURCE_SHA256!r} and "
                "_solid_wall_source.spell_id == 'wall_of_force'"
            ):
                requirement["condition"] = (
                    f"(_solid_wall_source.source_sha256 == {solid.SOURCE_SHA256!r} and "
                    "_solid_wall_source.spell_id == 'wall_of_force') or "
                    f"(_solid_wall_source.source_sha256 == {SOURCE_SHA256!r} and "
                    "_solid_wall_source.spell_id == 'wall_of_stone')"
                )
                requirement["error"] = "Use an actual source-qualified Force or Stone Wall."


def _latch():
    """Latch before generic Concentration pruning at the real completed-duration clock boundary."""
    return _tool(
        "Make this genuine Stone wall permanent only after its complete Concentration duration.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            *solid._source(),
            _set(
                "_stone_latch_time",
                "_solid_wall_source.spell_id == 'wall_of_stone' and "
                "_solid_wall_source.installed == true and _solid_wall_source.permanent != true and "
                "state.lasting_tick >= _solid_wall_source.expires_tick",
            ),
            _set(
                "_stone_latch_concentration",
                "caster_state.is_alive == true and caster_state.hp > 0 and "
                "caster_state.concentration_spell_id == 'wall_of_stone' and "
                "caster_state.concentration_effect_family == 'solid_spell_wall' and "
                "caster_state.solid_wall_source_id == source_id",
            ),
            _when(
                "_stone_latch_time == true and _stone_latch_concentration == true",
                [
                    _set(f"state.{solid.POOL}.{{source_id}}.permanent", True),
                    _set(f"state.{solid.POOL}.{{source_id}}.expires_tick", None),
                    _set(f"state.{solid.POOL}.{{source_id}}.ongoing_duration", False),
                    _set(f"state.{solid.POOL}.{{source_id}}.dispel_ongoing", False),
                    _call(
                        "finish_srd_solid_spell_wall_concentration",
                        {"caster_state": "{_caster_state_id}", "source_id": "{source_id}"},
                    ),
                ],
            ),
        ],
    )


def _latch_all():
    return _tool(
        "Settle only actual Stone sources at the real clock before ordinary lifetime callbacks.",
        [],
        [
            _set("_stone_clock_ids", f"state.{solid.INDEX}"),
            _each(
                "_stone_clock_ids",
                "_stone_clock_id",
                [
                    _set("_stone_clock_source", f"state.{solid.POOL}[_stone_clock_id]"),
                    _when(
                        "_stone_clock_source.spell_id == 'wall_of_stone' and "
                        "_stone_clock_source.installed == true and "
                        "_stone_clock_source.permanent != true",
                        [
                            _call(
                                "latch_srd_stone_wall_duration",
                                {
                                    "caster_state": "{_stone_clock_source.caster_state_id}",
                                    "source_id": "{_stone_clock_id}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _qualify_object():
    """Actual panel identity, body and HP are joined to the retained authentic source."""
    return _tool(
        "Qualify one real Stone scene panel with its printed AC15 and180HP for6inch thickness.",
        [_parameter("object_id")],
        [
            _call("srd_information_scene_entity", {"subject": "{object_id}"}, POSITION_EXPORTS),
            _set("_stone_panel_scene", "_info_scene_entity"),
            _set("_stone_panel_profile", "_stone_panel_scene.metadata.srd_spell_object"),
            _require(
                "_stone_panel_profile != null and len(_stone_panel_profile) == 9 and "
                "_stone_panel_profile.kind == 'wall_stone_panel' and "
                f"_stone_panel_profile.source_sha256 == {SOURCE_SHA256!r}",
                "Use a genuine saved Stone panel, never generic object-stat replacement.",
            ),
            _set("_stone_panel_source_id", "_stone_panel_profile.source_id"),
            _require(
                f"state.{solid.POOL} != null and _stone_panel_source_id in state.{solid.POOL}",
                "Select the actual saved Stone panel source.",
            ),
            _set("_solid_wall_source", f"state.{solid.POOL}[_stone_panel_source_id]"),
            _require(
                "_solid_wall_source.spell_id == 'wall_of_stone' and "
                f"_solid_wall_source.source_sha256 == {SOURCE_SHA256!r}",
                "The object must retain its exact Stone spell source.",
            ),
            _call(
                "qualify_srd_solid_spell_wall_source",
                {
                    "caster_state": "{_solid_wall_source.caster_state_id}",
                    "source_id": "{_stone_panel_source_id}",
                },
                ("_solid_wall_current",),
            ),
            _require(
                "_solid_wall_current == true and _solid_wall_source.spell_id == 'wall_of_stone' "
                "and _stone_panel_scene.id in _solid_wall_source.panel_ids and "
                "_stone_panel_scene.metadata.participant_ref == null and "
                "_stone_panel_scene.metadata.is_object == true and "
                "_stone_panel_scene.metadata.solid_wall_source_id == _stone_panel_source_id",
                "The panel must belong to this exact current Stone source.",
            ),
            _require(
                "_stone_panel_profile.serial == _solid_wall_source.serial and "
                "_stone_panel_scene.metadata.solid_wall_serial == _solid_wall_source.serial",
                "The panel must belong to this exact current Stone source and serial.",
            ),
            _require(
                "_stone_panel_profile.index >= 0 and _stone_panel_profile.index < 10 and "
                "_stone_panel_profile.index == floor(_stone_panel_profile.index) and "
                "_stone_panel_profile.armor_class == 15 and _stone_panel_profile.max_hp == 180 "
                "and _stone_panel_profile.hp >= 0 and _stone_panel_profile.hp <= 180 and "
                "_stone_panel_profile.destroyed == (_stone_panel_profile.hp == 0)",
                "Stone panel rule statistics must retain their printed source values.",
            ),
        ],
    )


def _settle_object():
    return _tool(
        "Remove only the actual destroyed Stone panel body, preserving all other sections.",
        [_parameter("object_id")],
        [
            _call(
                "qualify_srd_stone_wall_object",
                {"object_id": "{object_id}"},
                ("_stone_panel_scene", "_stone_panel_profile"),
            ),
            _when(
                "_stone_panel_profile.destroyed == true",
                [
                    *_snapshot(),
                    {
                        "spatial_mutation": {
                            "scene": "state.spatial_scene",
                            "map_id": "{state.spatial_scene.map.id}",
                            "expected_revision": "{_info_snapshot.geometry_revision}",
                            "target": {"kind": "entity", "id": "{object_id}"},
                            "patch": {"active": False},
                        },
                        "store_as": "_stone_panel_breached",
                    },
                ],
            ),
        ],
    )


def _bind_objects(tools, changed):
    """Extend actual object attacks without another roll, caller amount or HP authority."""
    for name in (
        "resolve_srd_spell_object_relation",
        "resolve_srd_spell_object_attack",
        "resolve_srd_sorcerous_burst_object_attack",
    ):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        if "qualify_srd_stone_wall_object" in str(
            tool["effects"]
        ) or "settle_srd_stone_wall_panel" in str(tool["effects"]):
            continue
        for effects in list(_effect_lists(tool["effects"])):
            start = next(
                (
                    i
                    for i, e in enumerate(effects)
                    if e.get("set", {}).get("field") == "_object_profile"
                    and e["set"]["value"] == "_object_scene.metadata.srd_spell_object"
                ),
                None,
            )
            if start is not None:
                end = (
                    next(
                        i
                        for i in range(start, len(effects))
                        if effects[i].get("require", {}).get("condition")
                        == "_object_profile.hp > 0"
                    )
                    + 1
                )
                ordinary = effects[start:end]
                effects[start:end] = [
                    _when(
                        "_object_scene.metadata.srd_spell_object.kind == 'wall_stone_panel'",
                        [
                            _call(
                                "qualify_srd_stone_wall_object",
                                {"object_id": "{object_id}"},
                                ("_stone_panel_profile",),
                            ),
                            _set("_object_profile", "_stone_panel_profile"),
                            _require("_object_profile.hp > 0", "This Stone section is destroyed."),
                        ],
                        ordinary,
                    ),
                ]
            for effect in list(effects):
                mutation = effect.get("spatial_mutation", {})
                if mutation.get("target", {}).get(
                    "id"
                ) == "{object_id}" and "metadata" in mutation.get("patch", {}):
                    effects.insert(
                        effects.index(effect) + 1,
                        _when(
                            "_object_profile.kind == 'wall_stone_panel'",
                            [_call("settle_srd_stone_wall_panel", {"object_id": "{object_id}"})],
                        ),
                    )
        changed[name] = tool


def extend_runtime(text):
    """Compose after genuine Force emission and before Source/Reaction capture."""
    tools = _load_runtime(text)["tools"]
    changed = {
        "prepare_srd_stone_spell_wall": _prepare(),
        "cast_srd_stone_spell_wall": _install(),
        "latch_srd_stone_wall_duration": _latch(),
        "latch_all_srd_stone_wall_durations": _latch_all(),
        "qualify_srd_stone_wall_object": _qualify_object(),
        "settle_srd_stone_wall_panel": _settle_object(),
    }
    for name in solid._tools():
        changed[name] = deepcopy(tools[name])
    for tool in changed.values():
        _source_union(tool)
    check = changed["qualify_srd_solid_spell_wall_source"]
    effect = next(
        e["set"] for e in check["effects"] if e.get("set", {}).get("field") == "_solid_wall_current"
    )
    if "permanent == true" not in effect["value"]:
        effect["value"] = (
            "_solid_wall_source.installed == true and "
            "(_solid_wall_source.permanent == true or (" + effect["value"] + "))"
        )
    retire = changed["retire_srd_solid_spell_wall_record"]
    next(
        e
        for e in retire["effects"]
        if e.get("condition", "").startswith("_solid_wall_source.installed == true")
    )["condition"] = "_solid_wall_source.installed == true and _solid_wall_source.permanent != true"
    movement = changed["check_srd_solid_wall_movement"]
    for effects in _effect_lists(movement["effects"]):
        for e in effects:
            condition = e.get("condition", "")
            if (
                "_solid_wall_mover.metadata.plane == 'ethereal'" in condition
                and "_solid_wall_source.spell_id == 'wall_of_force'" not in condition
            ):
                e["condition"] = condition.replace(
                    "_solid_wall_mover.metadata.plane == 'ethereal'",
                    "(_solid_wall_source.spell_id == 'wall_of_force' and "
                    "_solid_wall_mover.metadata.plane == 'ethereal')",
                )
    cast = deepcopy(tools["cast_spell"])
    if "cast_srd_stone_spell_wall" not in str(cast["effects"]):
        for effects in list(_effect_lists(cast["effects"])):
            for e in list(effects):
                if e.get("call") == "cast_srd_solid_spell_wall":
                    call = deepcopy(e)
                    call["call"] = "cast_srd_stone_spell_wall"
                    effects[effects.index(e) : effects.index(e) + 1] = [
                        _when("spell == 'wall_of_stone'", [call], [e]),
                    ]
    changed["cast_spell"] = cast
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    if "latch_all_srd_stone_wall_durations" not in str(clock["effects"]):
        index = next(
            i
            for i, e in enumerate(clock["effects"])
            if e.get("set", {}).get("field") == "lasting_state.lasting_tick"
            and "elapsed_ticks" in str(e["set"]["value"])
        )
        clock["effects"].insert(
            index + 1,
            _when(
                "lasting_state.has_solid_spell_walls == true",
                [_call("latch_all_srd_stone_wall_durations", {})],
            ),
        )
    changed["advance_srd_lasting_clock"] = clock
    _bind_objects(tools, changed)
    for tool in changed.values():
        solid._normalize_scene(tool)
    return _emit(text, changed)


def bind_runtime_consumers(text):
    """Preserve Stone qualification when final object spell owners rebuild their actual leaves."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    _bind_objects(tools, changed)
    for tool in changed.values():
        solid._normalize_scene(tool)
    return _emit(text, changed)
