"""Qualify ward fog and stair Web on each actual sight ray, with observer exemptions."""

if __package__:
    from .srd_control_behaviors import _each
    from .srd_guards_wards import _exempt, _sources
    from .srd_guards_wards_web import POOL
    from .srd_scene_relations import _array
    from .srd_spell_barriers import _tool
    from .srd_spell_magic_zones import _when
    from .srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _set
    from .srd_timed_senses import _replace_tools
else:
    from srd_control_behaviors import _each
    from srd_guards_wards import _exempt, _sources
    from srd_guards_wards_web import POOL
    from srd_scene_relations import _array
    from srd_spell_barriers import _tool
    from srd_spell_magic_zones import _when
    from srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _set
    from srd_timed_senses import _replace_tools


EXPORTS = ("_guards_wards_heavily_obscured", "_guards_wards_lightly_obscured")


def _box_ray():
    """Use the established slab interval, excluding face tangency and endpoint-only contact."""
    return _tool(
        "Qualify a positive interior sight interval, or an actual interior point, in a ward Box.",
        [_array("start"), _array("end"), _parameter("volume", "object")],
        [
            _set("_guards_ray_inside", False),
            _call(
                "srd_information_segment_interval",
                {"start": "{start}", "end": "{end}", "volume": "{volume}"},
                ("_segment_hit", "_segment_entry", "_segment_exit"),
            ),
            _when(
                "_segment_hit == true and _segment_exit > _segment_entry",
                [
                    _set("_guards_ray_mid", "(_segment_entry + _segment_exit) * 0.5"),
                    _set(
                        "_guards_ray_point",
                        [
                            f"start[{a}] + (end[{a}] - start[{a}]) * _guards_ray_mid"
                            for a in range(3)
                        ],
                    ),
                    _set(
                        "_guards_ray_inside",
                        " and ".join(
                            f"_guards_ray_point[{a}] > volume.min[{a}] and "
                            f"_guards_ray_point[{a}] < volume.max[{a}]"
                            for a in range(3)
                        ),
                    ),
                ],
            ),
        ],
    )


def _ray(volume, destination):
    return [
        _call(
            "resolve_srd_guards_wards_box_ray",
            {"start": "{start}", "end": "{end}", "volume": volume},
            ("_guards_ray_inside",),
        ),
        _when("_guards_ray_inside == true", [_set(destination, True)]),
    ]


def _obscuration():
    return _tool(
        "Read actual active ward fog and Web crossed by this observer's genuine sight ray.",
        [_array("start"), _array("end"), _parameter("plane"), _entity("observer_state")],
        [
            *[_set(export, False) for export in EXPORTS],
            *_sources(
                [
                    _when(
                        "_guards_wards_heavily_obscured != true and plane == _guards_source.plane "
                        "and _guards_source.groups.corridors == true and "
                        f"{_exempt('_observer_state_id')} != true",
                        [
                            _set("_guards_fog_boxes", "_guards_source.corridors"),
                            _each(
                                "_guards_fog_boxes",
                                "_guards_fog_box",
                                [
                                    _when(
                                        "_guards_wards_heavily_obscured != true",
                                        [
                                            _set(
                                                "_guards_fog_volume",
                                                {
                                                    "kind": "box",
                                                    "min": "_guards_fog_box.min",
                                                    "max": "_guards_fog_box.max",
                                                },
                                            ),
                                            *_ray("{_guards_fog_volume}", EXPORTS[0]),
                                        ],
                                    )
                                ],
                            ),
                        ],
                    )
                ]
            ),
            _when(
                "state.has_guarded_web_sections == true",
                [
                    _set("_guards_sight_section_ids", "state.guarded_web_section_ids"),
                    _each(
                        "_guards_sight_section_ids",
                        "_guards_sight_section_id",
                        [
                            _set(
                                "_guards_sight_section", f"state.{POOL}[_guards_sight_section_id]"
                            ),
                            _when(
                                "_guards_wards_lightly_obscured != true and "
                                "_guards_sight_section.active == true and "
                                "plane == _guards_sight_section.plane",
                                [
                                    _call(
                                        "qualify_srd_guards_wards_web_exemption",
                                        {
                                            "target_state": "{_observer_state_id}",
                                            "area_id": "{_guards_sight_section_id}",
                                        },
                                        ("_sphere_source_current", "_ward_web_exempt"),
                                    ),
                                    _when(
                                        "_sphere_source_current == true and "
                                        "_ward_web_exempt != true",
                                        _ray("{_guards_sight_section.volume}", EXPORTS[1]),
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def extend_runtime(text):
    """Emit pure queries after the genuine parent and Web provider; sight owners bind them later."""
    tools = _load_runtime(text)["tools"]
    required = {"srd_information_segment_interval", "qualify_srd_guards_wards_web_exemption"}
    if missing := required - tools.keys():
        raise SystemExit(f"Ward sight queries need actual owner tools: {sorted(missing)}")
    changed = {
        "resolve_srd_guards_wards_box_ray": _box_ray(),
        "resolve_srd_guards_wards_obscuration": _obscuration(),
    }
    # Normalize the shared emitter's insertion separator once when these tools are new.
    return _replace_tools(_replace_tools(text, changed), changed)
