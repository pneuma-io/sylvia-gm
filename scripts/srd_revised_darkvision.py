"""Keep original SRD Darkvision and validate its real touched creature.

The original timed_senses cast, source rows, sight consumer, dismissal and clock
remain responsible for effects. This binder is for the revised variant only;
do not apply the old legacy utility binders to its Darkvision route.
"""

from copy import deepcopy

from .srd_control_behaviors import _entity, _lasting
from .srd_scene_relations import _snapshot, _spatial
from .srd_timed_actor_defenses import _call, _require, _set, _tool, _when
from .srd_timed_senses import _loop


def capture_srd_darkvision(runtime):
    """Retain and check the immutable provider before composing a variant."""
    profile = runtime["definitions"]["spells"]["darkvision"]
    header = runtime["definitions"]["spell_casting"]["darkvision"]
    assert profile == {
        "base_level": 2, "base_targets": 1, "casting_time": "action",
        "comprehend_languages": False, "concentration": False,
        "darkvision_feet": 150, "duration_rounds": 4800, "family": "timed_senses",
        "range_feet": 0, "range_kind": "touch", "requires_willing": True,
        "see_ethereal": False, "see_invisible": False, "truesight_feet": 0,
        "water_breathing": False,
    }, "Use the original SRD Darkvision source, not the deferred utility profile."
    assert header["level"] == 2 and header["casting_time"] == "Action"
    assert header["range"] == "Touch" and header["duration"] == "8 hours"
    assert header["components"] == ["verbal", "somatic", "material"]
    assert header["material"] == "a dried carrot"
    assert header["material_parts"] == [{"consumed": False, "id": "darkvision.1",
        "minimum_value_cp": 0, "quantity": 1, "scale": "fixed"}]
    assert header["focus_substitution"] is True and header["ritual"] is False
    assert header["material_consumed"] is False and header["material_has_cost"] is False
    assert header["classes"] == ["druid", "ranger", "sorcerer", "wizard"]
    return {"profile": deepcopy(profile), "header": deepcopy(header)}


def _touch_validator():
    return _tool(
        "Validate the actual willing Darkvision recipient's touch geometry.",
        [_entity("caster_state"), _entity("target_state"), _lasting()],
        [
            _require("target_state.is_alive == true and target_state.creature_kind != null "
                "and target_state.conditions != null", "Darkvision requires a living creature."),
            _call("resolve_srd_scene_geometry_relation", {
                "actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}",
                "lasting_state": "{_lasting_state_id}"},
                ("_scene_distance", "_scene_same_plane", "_scene_actor_entity", "_scene_exposure")),
            _require("_scene_distance <= 5 and _scene_same_plane == true",
                "Darkvision needs actual touch range and a clear shared plane."),
            # Exposure is visibility, not physical contact: opaque passable fog
            # cannot stop Touch; clear blocking glass can. Use admitted body
            # height and the original producer's actual recipient body samples
            # so ground and partial cover are not mistaken for Total Cover.
            # No body movement is swept; opacity never decides contact.
            _set("_darkvision_touch_from", [
                "_scene_actor_entity.position[0]", "_scene_actor_entity.position[1]",
                "_scene_actor_entity.position[2] + _scene_actor_entity.height * 0.5"]),
            *_snapshot(),
            _set("_darkvision_touch_geometry", "state.spatial_scene.geometry"),
            _set("_darkvision_touch_samples", "_scene_exposure.samples"),
            _set("_darkvision_touch_clear", False),
            _loop("_darkvision_touch_samples", "_darkvision_touch_sample", [
                _when("_darkvision_touch_clear != true", [
                    _spatial("ray", {
                        "map_id": "{state.spatial_scene.map.id}",
                        "origin": "{_darkvision_touch_from}",
                        "destination": "{_darkvision_touch_sample.point}",
                        "include": ["geometry"], "max_intersections": 1024}, "_darkvision_touch_ray"),
                    _require("_darkvision_touch_ray.truncated != true",
                        "Darkvision touch path exceeds the admitted physical ray bound."),
                    _set("_darkvision_touch_hits", "_darkvision_touch_ray.intersections"),
                    _set("_darkvision_touch_sample_clear", True),
                    _loop("_darkvision_touch_hits", "_darkvision_touch_hit", [
                        _loop("_darkvision_touch_geometry", "_darkvision_touch_blocker", [
                            _when("_darkvision_touch_hit.id == _darkvision_touch_blocker.id and "
                                "_darkvision_touch_blocker.active == true and "
                                "_darkvision_touch_blocker.movement == 'blocking' and "
                                "(_darkvision_touch_blocker.metadata.plane == null or "
                                "_darkvision_touch_blocker.metadata.plane == _scene_actor_entity.metadata.plane)", [
                                _set("_darkvision_touch_sample_clear", False),
                            ]),
                        ]),
                    ]),
                    _when("_darkvision_touch_sample_clear == true", [
                        _set("_darkvision_touch_clear", True),
                    ]),
                ]),
            ]),
            _require("_darkvision_touch_clear == true",
                "Darkvision needs an unobstructed physical touch path."),
        ],
    )


def _prelude():
    return [_when("spell == 'darkvision'", [
        _require("targets == null or len(targets) <= 1",
            "Darkvision affects exactly one creature at every slot level."),
        _set("_revised_darkvision_target", "_target_id"),
        _when("targets != null and len(targets) == 1", [
            _set("_revised_darkvision_target", "targets[0]"),
            _require("all_targets_willing == true", "The Darkvision recipient must be willing."),
        ]),
        _when("targets == null or len(targets) == 0", [
            _require("target_willing == true", "The Darkvision recipient must be willing."),
        ]),
        _call("validate_revised_srd_darkvision_touch", {
            "caster_state": "{_caster_state_id}", "target_state": "{_revised_darkvision_target}",
            "lasting_state": "{_lasting_state_id}"}),
    ])]


def bind_revised_srd_darkvision(runtime, captured):
    """Bind a small scene guard while retaining all original SRD effect owners."""
    definitions = runtime["definitions"]
    definitions["spells"]["darkvision"] = deepcopy(captured["profile"])
    definitions["spell_casting"]["darkvision"] = deepcopy(captured["header"])
    tools = runtime["tools"]
    # Do not silently restore a header while leaving the deferred legacy delivery
    # active. Main should skip that older utility adapter for this variant.
    assert "install_srd_lite_utility" not in tools, "Skip the old legacy utility binder."
    assert "cast_spell_timed_senses" in tools and "resolve_srd_timed_senses" in tools
    assert "resolve_srd_scene_geometry_relation" in tools
    tools["validate_revised_srd_darkvision_touch"] = _touch_validator()
    for name in ("cast_spell", "begin_srd_spell_action"):
        assert not any("validate_revised_srd_darkvision_touch" in str(e) for e in tools[name]["effects"]), \
            "Apply the revised Darkvision binder once."
        tools[name]["effects"][:0] = _prelude()
    # cast_spell_outside_combat and complete_srd_pending_action retain their
    # actual canonical cast call, so the guard also runs at completion.
    return ("cast_spell", "begin_srd_spell_action", "validate_revised_srd_darkvision_touch")
