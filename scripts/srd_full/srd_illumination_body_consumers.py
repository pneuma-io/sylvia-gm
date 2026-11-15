"""Qualify admitted object exposure samples through actual source light and obscurement."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS
    from .srd_timed_actor_defenses import _call, _load_runtime, _parameter, _require, _set, _when
    from .srd_timed_senses import _replace_tools
else:
    from srd_scene_relations import POSITION_EXPORTS
    from srd_timed_actor_defenses import _call, _load_runtime, _parameter, _require, _set, _when
    from srd_timed_senses import _replace_tools


def _lists(effects):
    """Visit executable branches without traversing saved scalar payloads."""
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if isinstance(effect.get(key), list):
                yield from _lists(effect[key])
        if "for_each" in effect:
            yield from _lists(effect["for_each"]["do"])


def _tools(runtime):
    """Reuse the settled point owner, retaining the neutral engine's Cover authority."""
    tools = runtime["tools"]
    relation = deepcopy(tools["resolve_srd_spell_object_relation"])
    point = deepcopy(tools["resolve_srd_illumination_point_sight"])
    if any(p["name"] == "source_object_id" for p in point["parameters"]):
        if "_illumination_body_sample" not in str(relation["effects"]):
            raise ValueError(
                "Regenerate settled object and point owners before rebinding body sight."
            )
        return {}
    point["parameters"].append(
        _parameter("source_object_id", required=False, default="", model_hidden=True)
    )
    ambient_index = next(
        i
        for i, e in enumerate(point["effects"])
        if e.get("set", {}).get("field") == "_illumination_point_ambient"
    )
    point["effects"].insert(ambient_index + 1, _set("_illumination_body_invisible", False))
    point["effects"].insert(
        ambient_index + 2,
        _when(
            "source_object_id != ''",
            [
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{source_object_id}"},
                    POSITION_EXPORTS,
                ),
                _require(
                    "_info_scene_entity.metadata.is_object == true and "
                    "_info_scene_entity.metadata.plane == _illumination_point_plane and "
                    "_info_scene_entity.metadata.lighting != null",
                    "Body sight needs its actual same-plane object and admitted ambient lighting.",
                ),
                _set("_illumination_point_ambient", "_info_scene_entity.metadata.lighting"),
                _call(
                    "check_srd_illumination_object_visibility",
                    {"object_id": "{source_object_id}", "observer_state": "{_observer_state_id}"},
                    ["_illumination_object_invisible", "_illumination_object_suppressed"],
                ),
                _set(
                    "_illumination_body_invisible",
                    "_illumination_object_invisible == true and "
                    "_illumination_object_suppressed != true",
                ),
            ],
        ),
    )
    sight_calls = 0
    for effects in _lists(point["effects"]):
        for effect in effects:
            if effect.get("condition") == "_illumination_point_zone.metadata.lighting != null":
                effect["condition"] += " and source_object_id == ''"
            if effect.get("call") == "resolve_srd_sight":
                effect["params"]["subject_invisible"] = "{_illumination_body_invisible}"
                sight_calls += 1
    if sight_calls != 1:
        raise ValueError("Body sight needs its single actual point-sight consumer.")
    old = [
        e for e in relation["effects"] if "resolve_srd_illumination_object_obscurement" in str(e)
    ]
    if len(old) != 1:
        raise ValueError("Body sight must follow the accepted source-qualified object owner.")
    index = relation["effects"].index(old[0])
    relation["effects"][index] = _when(
        "lasting_state.has_spell_environments == true or "
        "lasting_state.has_illumination_spells == true or "
        "len(lasting_state.spatial_scene.zones) > 0",
        [
            _set("_object_can_see", False),
            _set("_illumination_body_samples", "_object_exposure.samples"),
            {
                "for_each": {
                    "collection": "_illumination_body_samples",
                    "as": "_illumination_body_sample",
                    "do": [
                        _when(
                            "_illumination_body_sample.visible == true and _object_can_see != true",
                            [
                                _call(
                                    "resolve_srd_illumination_point_sight",
                                    {
                                        "observer_state": "{_caster_state_id}",
                                        "point": "{_illumination_body_sample.point}",
                                        "source_object_id": "{object_id}",
                                        "lasting_state": "{_lasting_state_id}",
                                    },
                                    ["_illumination_point_can_see"],
                                ),
                                _when(
                                    "_illumination_point_can_see == true",
                                    [_set("_object_can_see", True)],
                                ),
                            ],
                        ),
                    ],
                },
            },
        ],
    )
    return {
        "resolve_srd_spell_object_relation": relation,
        "resolve_srd_illumination_point_sight": point,
    }


def bind_runtime_consumers(text):
    """Run after final object/Fog/point owners and the accepted outline anchor binder."""
    changed = _tools(_load_runtime(text))
    return _replace_tools(text, changed) if changed else text
