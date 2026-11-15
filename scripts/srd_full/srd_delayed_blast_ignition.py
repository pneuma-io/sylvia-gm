"""Ended beads ignite admitted mundane flame sources through actual Sphere and Cover."""

from copy import deepcopy

if __package__:
    from . import srd_sunburst_geometry as sphere
    from .srd_retained_spell_attacks import _lists
    from .srd_scene_relations import POSITION_EXPORTS, _snapshot, _tool_scene
    from .srd_timed_actor_defenses import _call, _load_runtime, _parameter, _require, _set, _when
    from .srd_timed_senses import _loop, _push, _replace_tools
else:
    import srd_sunburst_geometry as sphere
    from srd_retained_spell_attacks import _lists
    from srd_scene_relations import POSITION_EXPORTS, _snapshot, _tool_scene
    from srd_timed_actor_defenses import _call, _load_runtime, _parameter, _require, _set, _when
    from srd_timed_senses import _loop, _push, _replace_tools


IGNITE = "ignite_srd_delayed_blast_flame_sources"
OBJECT_COVER = "qualify_srd_delayed_blast_flame_cover"
FLAME_KINDS = ("candle", "torch", "lamp", "campfire", "small_campfire", "fire")


def _object_cover(tools):
    """The established physical Cover algorithm needs no creature liveness for scenery."""
    tool = deepcopy(tools["qualify_srd_delayed_blast_target_cover"])
    tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "target_state"]
    hits = 0
    for effects in _lists(tool["effects"]):
        for effect in effects:
            row = effect.get("set", {})
            if row.get("field") == "_breath_target_affected":
                if (
                    row["value"]
                    != "_breath_target_cover != 'total' and target_state.is_alive != false"
                ):
                    raise ValueError("The actual Delayed Blast physical Cover owner changed.")
                row["value"] = "_breath_target_cover != 'total'"
                hits += 1
    if hits != 1 or "target_state" in str(tool["effects"]):
        raise ValueError("Object ignition must preserve the real physical Cover owner.")
    tool["description"] = "Qualify admitted flame-source scenery through actual physical Cover."
    return tool


def _collect_objects(tool):
    """Reuse one blocker scan and snapshot; ordinary creature collection stays unchanged."""
    tool = deepcopy(tool)
    if "flame_objects" in {p["name"] for p in tool["parameters"]}:
        return tool
    tool["parameters"].append(
        _parameter(
            "flame_objects",
            "array",
            required=False,
            default=[],
            items={"type": "string"},
            max_items=64,
            model_hidden=True,
        )
    )
    tool["effects"].append(
        _loop(
            "flame_objects",
            "_dbf_flame_id",
            [
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_dbf_flame_id}"},
                    POSITION_EXPORTS,
                ),
                _set("_dbf_flame_scene", "_info_scene_entity"),
                _require(
                    "_dbf_flame_scene.id == _dbf_flame_id and "
                    "_dbf_flame_scene.metadata.participant_ref == null and "
                    "_dbf_flame_scene.metadata.plane == area_plane",
                    "Ignition needs the actual same-plane scenery object.",
                ),
                _call(
                    OBJECT_COVER,
                    {
                        "source_id": "{_breath_source_scene_id}",
                        "source_frame": "{_sunburst_cover_source}",
                        "origin": "{origin}",
                        "geometry_blockers": "{_breath_geometry_blockers}",
                        "entity_blockers": "{_breath_entity_blockers}",
                        "target_scene": "{_dbf_flame_scene}",
                        "scene_snapshot": "{_info_snapshot}",
                        "nearest_faces": "{_area_f}",
                    },
                    ("_breath_target_affected",),
                ),
                _when(
                    "_breath_target_affected == true",
                    [_push("_srd_area_targets", "_dbf_flame_id")],
                ),
            ],
        )
    )
    return tool


def _ignition_tool():
    kinds = " or ".join(f"_dbf_flame_kind.kind == '{kind}'" for kind in FLAME_KINDS)
    return _tool_scene(
        "Ignite actual unworn, uncarried admitted mundane flame sources in the ended Sphere.",
        [
            _parameter("source_id"),
            _parameter("record", "object", from_state="state.delayed_blast_detonations"),
        ],
        [
            _require(
                "record.kind == 'delayed_blast_fireball' and record.source_id == source_id "
                "and record.cast_id == source_id and "
                "state.delayed_blast_detonations[source_id].status == 'queued'",
                "Ignition belongs to the authentic queued bead ending.",
            ),
            _set("_dbf_flame_objects", []),
            _set("_bead_fuels", "state.spatial_scene.entities"),
            _loop(
                "_bead_fuels",
                "_bead_fuel",
                [
                    _when(
                        "_bead_fuel.active == true and "
                        "_bead_fuel.metadata.participant_ref == null and "
                        "_bead_fuel.metadata.is_object == true and "
                        "_bead_fuel.metadata.srd_ordinary_flame != null and "
                        "_bead_fuel.metadata.srd_minor_world_subject != null and "
                        "(_bead_fuel.metadata.plane == record.plane or "
                        "_bead_fuel.metadata.plane == null) and "
                        "(_bead_fuel.metadata.carried_by == null or "
                        "_bead_fuel.metadata.carried_by == '') and "
                        "(_bead_fuel.metadata.worn_by == null or "
                        "_bead_fuel.metadata.worn_by == '')",
                        [
                            _set(
                                "_dbf_flame_kind", "_bead_fuel.metadata.srd_minor_world_subject"
                            ),
                            _set("_dbf_flame_fact", "_bead_fuel.metadata.srd_ordinary_flame"),
                            _when(
                                f"({kinds}) and _dbf_flame_kind.nonliving == true and "
                                "_dbf_flame_fact.kind == 'ordinary_flame' and "
                                "_dbf_flame_fact.burning == false",
                                [
                                    _require(
                                        "_bead_fuel.metadata.plane != null and "
                                        "_bead_fuel.footprint.kind == 'box'",
                                        "Ignition needs the admitted flame object's Box and plane.",
                                    ),
                                    _set(
                                        "_dbf_flame_near",
                                        sphere._nearest("_bead_fuel", "record.origin"),
                                    ),
                                    _when(
                                        "(_dbf_flame_near[0] - record.origin[0]) ** 2 + "
                                        "(_dbf_flame_near[1] - record.origin[1]) ** 2 + "
                                        "(_dbf_flame_near[2] - record.origin[2]) ** 2 <= 20 ** 2",
                                        [
                                            _require(
                                                "len(_dbf_flame_objects) < 64",
                                                "The ended bead exceeds64 admitted flame objects.",
                                            ),
                                            _push("_dbf_flame_objects", "_bead_fuel.id"),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                "len(_dbf_flame_objects) > 0",
                [
                    _call(
                        "collect_srd_delayed_blast_cover_targets",
                        {
                            "source_state": "{record.caster}",
                            "origin": "{record.origin}",
                            "area_plane": "{record.plane}",
                            "targets": [],
                            "flame_objects": "{_dbf_flame_objects}",
                        },
                        ("_srd_area_targets",),
                    ),
                    _set("_dbf_flame_affected", "_srd_area_targets"),
                    _loop(
                        "_dbf_flame_affected",
                        "_dbf_flame_object",
                        [
                            _call(
                                "srd_information_scene_entity",
                                {"subject": "{_dbf_flame_object}"},
                                POSITION_EXPORTS,
                            ),
                            _set("_dbf_flame_current", "_info_scene_entity"),
                            _set("_dbf_flame_changes", {"burning": True}),
                            _set(
                                "_dbf_flame_result",
                                "merge(_dbf_flame_current.metadata.srd_ordinary_flame, "
                                "_dbf_flame_changes)",
                            ),
                            _set(
                                "_dbf_flame_metadata_patch",
                                {"srd_ordinary_flame": "_dbf_flame_result"},
                            ),
                            _set(
                                "_dbf_flame_metadata",
                                "merge(_dbf_flame_current.metadata, _dbf_flame_metadata_patch)",
                            ),
                            *_snapshot(),
                            {
                                "spatial_mutation": {
                                    "scene": "state.spatial_scene",
                                    "map_id": "{state.spatial_scene.map.id}",
                                    "expected_revision": "{_info_snapshot.geometry_revision}",
                                    "target": {"kind": "entity", "id": "{_dbf_flame_object}"},
                                    "patch": {"metadata": "{_dbf_flame_metadata}"},
                                },
                                "store_as": "_dbf_flame_mutation",
                            },
                        ],
                    ),
                ],
            ),
        ],
    )


def bind_runtime_consumers(text):
    """Bind after authentic bead/physical area owners; every other damage body is retained."""
    tools = _load_runtime(text)["tools"]
    changed = {
        OBJECT_COVER: _object_cover(tools),
        IGNITE: _ignition_tool(),
        "collect_srd_delayed_blast_cover_targets": _collect_objects(
            tools["collect_srd_delayed_blast_cover_targets"]
        ),
    }
    tool = deepcopy(tools["resolve_srd_delayed_blast_fireball"])
    if IGNITE not in {e.get("call") for seq in _lists(tool["effects"]) for e in seq}:
        index = next(
            i
            for i, e in enumerate(tool["effects"])
            if e.get("set", {}).get("field")
            == "lasting_state.delayed_blast_detonations.{source_id}.status"
        )
        tool["effects"].insert(
            index,
            _call(IGNITE, {"source_id": "{source_id}", "record": "{_dbf_record}"}),
        )
    changed["resolve_srd_delayed_blast_fireball"] = tool
    return _replace_tools(
        text, {name: tool for name, tool in changed.items() if tools.get(name) != tool}
    )
