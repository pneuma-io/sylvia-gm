"""Actual Gaseous sources narrow around panels without passing through intact solids."""

from copy import deepcopy

if __package__:
    from . import srd_solid_spell_walls as solid
    from .srd_information_spells import _interval_effects
else:
    import srd_solid_spell_walls as solid
    from srd_information_spells import _interval_effects


def _point_segment():
    """Keep shared panel faces solid; a zero-length contact does not cross the body."""
    effects = _interval_effects()
    effects.append(
        solid._set("_segment_hit", "_segment_hit == true and _segment_entry < _segment_exit")
    )
    return solid._tool(
        "Intersect the physical interior of an actual intact source panel's derived box.",
        [
            solid._array("start"),
            solid._array("end"),
            solid._parameter("volume", "object", from_state="state.spatial_scene"),
        ],
        effects,
    )


def _centerline():
    """Derive each box only from its sole saved panel body and exact source serial."""
    frame = solid._frame("_solid_wall_point_panel", "_solid_wall_point_volume")
    frame["set"]["value"]["kind"] = "'box'"
    return [
        solid._require(
            " and ".join(
                f"destination[{a}] >= state.spatial_scene.map.bounds.min[{a}] and "
                f"destination[{a}] <= state.spatial_scene.map.bounds.max[{a}]"
                for a in range(3)
            ),
            "The actual Gaseous destination must remain inside the admitted map bounds.",
        ),
        solid._set("_solid_wall_point_bodies", "state.spatial_scene.entities"),
        solid._each(
            "_solid_wall_point_bodies",
            "_solid_wall_point_panel",
            [
                solid._when(
                    "_solid_wall_point_panel.active == true and "
                    "_solid_wall_point_panel.movement == 'blocking' and "
                    "_solid_wall_point_panel.metadata.solid_wall_source_id == _solid_wall_id and "
                    "_solid_wall_point_panel.metadata.solid_wall_serial == "
                    "_solid_wall_source.serial",
                    [
                        solid._require(
                            "_solid_wall_point_panel.footprint.kind == 'box'",
                            "Source-owned panel narrowing requires its actual admitted Box body.",
                        ),
                        frame,
                        solid._call(
                            "resolve_srd_solid_wall_point_segment",
                            {
                                "start": "{_solid_wall_mover.position}",
                                "end": "{destination}",
                                "volume": "{_solid_wall_point_volume}",
                            },
                            ("_segment_hit",),
                        ),
                        solid._require(
                            "_segment_hit != true",
                            "An intact source panel blocks even actual Gaseous passage.",
                        ),
                    ],
                )
            ],
        ),
    ]


def bind_runtime_consumers(text):
    """Compose after Force/Stone constructors and before final movement clone capture."""
    tools = solid._load_runtime(text)["tools"]
    if "check_srd_form_movement_permissions" not in tools:
        raise ValueError("Panel narrowing needs the authentic transformation permission owner.")
    movement = deepcopy(tools["check_srd_solid_wall_movement"])
    if "_solid_wall_narrow_openings" not in str(movement["effects"]):
        scene = next(
            i
            for i, effect in enumerate(movement["effects"])
            if effect.get("set", {}).get("field") == "_solid_wall_mover"
        )
        movement["effects"][scene + 1 : scene + 1] = [
            solid._set("_solid_wall_narrow_openings", False),
            solid._when(
                "teleport != true and _solid_wall_mover.metadata.participant_ref != null",
                [
                    solid._call(
                        "check_srd_form_movement_permissions",
                        {"actor_state": "{_solid_wall_mover.metadata.participant_ref}"},
                        {"_solid_wall_narrow_openings": "_form_narrow_openings"},
                    )
                ],
            ),
        ]
        replaced = 0
        for effects in list(solid._effect_lists(movement["effects"])):
            for index, effect in list(enumerate(effects)):
                if effect.get("store_as") != "_solid_wall_sweep":
                    continue
                original = effects[index : index + 3]
                if len(original) != 3 or not all("require" in e for e in original[1:]):
                    raise ValueError(
                        "Panel narrowing needs the actual sweep and rejection boundary."
                    )
                effects[index : index + 3] = [
                    solid._when("_solid_wall_narrow_openings == true", _centerline(), original)
                ]
                replaced += 1
        if replaced != 1:
            raise ValueError("Panel narrowing needs exactly one source-panel sweep boundary.")
    solid._normalize_scene(movement)
    return solid._emit(
        text,
        {
            "check_srd_solid_wall_movement": movement,
            "resolve_srd_solid_wall_point_segment": _point_segment(),
        },
    )
