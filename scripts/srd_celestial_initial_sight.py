"""Include Celestial's own printed Bright Light in its initial creation intent."""

from copy import deepcopy

if __package__:
    from .srd_conjured_presences import _cylinder_faces, _lists
    from .srd_scene_relations import _array
    from .srd_spell_travel import _normalize
    from .srd_timed_actor_defenses import _load_runtime, _set, _when
    from .srd_timed_senses import _optional, _replace_tools
else:
    from scripts.srd_conjured_presences import _cylinder_faces, _lists
    from scripts.srd_scene_relations import _array
    from scripts.srd_spell_travel import _normalize
    from scripts.srd_timed_actor_defenses import _load_runtime, _set, _when
    from scripts.srd_timed_senses import _optional, _replace_tools

SIGHT = "resolve_srd_celestial_creation_subject_sight"
COLLECTOR = "collect_srd_celestial_intent_creatures"


def _creation_sight(original):
    """Retain final pure sight; add only the validated prospective Cylinder's local light."""
    tool = deepcopy(original)
    tool["description"] = (
        "Read actual initial creature sight including this prospective Celestial Cylinder's "
        "printed Bright Light; preserve existing senses and obstructions."
    )
    tool["parameters"].extend(
        [
            _array("creation_point"),
            _optional("creation_origin_face", "enum", "bottom", enum_values=["bottom", "top"]),
        ]
    )
    lighting = [
        i
        for i, effect in enumerate(tool["effects"])
        if effect.get("set")
        == {"field": "_illumination_point_lighting", "value": "_information_light"}
    ]
    if len(lighting) != 1:
        raise ValueError("Retain the one existing final point-light projection.")
    previous = tool["effects"][lighting[0] - 1]
    if previous.get("call") != "resolve_srd_information_light":
        raise ValueError("Retain the canonical actual information-light query before projection.")
    if any(
        effect.get("call") == "resolve_srd_travel_perception"
        for sequence in _lists(tool["effects"])
        for effect in sequence
    ):
        raise ValueError("Initial sight requires the final pure, lifecycle-free perception owner.")
    tool["effects"][lighting[0] : lighting[0]] = [
        *_cylinder_faces("creation_point", "creation_origin_face"),
        _when(
            "_presence_sight_body.metadata.plane == _illumination_point_plane and "
            "_presence_sight_point[2] >= _presence_cylinder_min and "
            "_presence_sight_point[2] <= _presence_cylinder_max and "
            "(_presence_sight_point[0] - creation_point[0]) ** 2 + "
            "(_presence_sight_point[1] - creation_point[1]) ** 2 <= 100 and "
            "(_information_light == 'darkness' or _information_light == 'dim')",
            [_set("_information_light", "'bright'")],
        ),
    ]
    return tool


def emit_late_initial_sight(text):
    """Capture final pure sight and bind only Celestial's validated prepayment collector."""
    tools = _load_runtime(text)["tools"]
    sight = _creation_sight(tools["resolve_srd_conjured_subject_sight"])
    collector = deepcopy(tools[COLLECTOR])
    matches = []
    for sequence in _lists(collector["effects"]):
        for effect in sequence:
            if effect.get("call") in ("resolve_srd_conjured_subject_sight", SIGHT):
                matches.append(effect)
    if len(matches) != 1:
        raise ValueError("Retain the one initial actual-creature sight decision.")
    matches[0]["call"] = SIGHT
    matches[0]["params"].update(
        creation_point="{point}", creation_origin_face="{celestial_origin_face}"
    )
    emitted = {SIGHT: sight, COLLECTOR: collector}
    _normalize(emitted)
    return _replace_tools(text, emitted)
