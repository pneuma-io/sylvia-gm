"""Retain original SRD information owners and preflight an Arcane Eye origin.

The origin guard reads the actual caster and source's distance owner. It does
not install a sensor, advance a scene revision or create a source record.
Ordinary source casting, sight, movement, language, clock and removal remain
unchanged. This module belongs only to the revised SRD variant.
"""
from copy import deepcopy
from .srd_control_behaviors import _entity, _lasting
from .srd_information_spells import PROFILES
from .srd_scene_relations import _array
from .srd_timed_actor_defenses import _call, _parameter, _require, _tool, _when

SPELLS = ("arcane_eye", "speak_with_animals")


def capture_srd_information(runtime):
    definitions = runtime["definitions"]
    result = {}
    for spell in SPELLS:
        profile = definitions["spells"][spell]
        header = definitions["spell_casting"][spell]
        assert profile == PROFILES[spell], "Use the original SRD information profile."
        result[spell] = {"profile": deepcopy(profile), "header": deepcopy(header)}
    eye = result["arcane_eye"]["header"]
    assert eye["level"] == 4 and eye["range"] == "30 feet" and eye["ritual"] is False
    assert eye["components"] == ["verbal", "somatic", "material"]
    assert eye["material_parts"] == [{"consumed": False, "id": "arcane_eye.1",
        "minimum_value_cp": 0, "quantity": 1, "scale": "fixed"}]
    animals = result["speak_with_animals"]["header"]
    assert animals["level"] == 1 and animals["range"] == "Self" and animals["ritual"] is True
    assert animals["components"] == ["verbal", "somatic"] and animals["material_parts"] == []
    return result


def _origin_validator():
    # Repeat the original family tail's actual position/range checks before a
    # pending action can spend its budget. The retained producer continues to
    # admit the real installed proxy through the canonical spatial owner.
    return _tool("Validate the actual caster-to-origin range before an Arcane Eye casting action.",
        [_entity("caster_state"), _array("position"), _lasting()], [
            _require("len(position) == 3", "The Arcane Eye needs a three-coordinate origin."),
            _call("srd_information_scene_entity", {"subject": "{_caster_state_id}",
                "lasting_state": "{_lasting_state_id}"}, ("_info_scene_entity", "_info_position")),
            _require("_info_scene_entity.metadata.plane != null", "The caster needs its actual plane."),
            _call("measure_srd_information_distance", {"start": "{_info_position}", "end": "{position}"},
                ("_scene_distance",)),
            _require("_scene_distance <= 30", "The Arcane Eye origin is outside the actual 30-foot range."),
        ])


def _prelude():
    return [_when("spell == 'arcane_eye'", [
        _require("information_sense == 'seeing'", "Arcane Eye supplies visual information only."),
        _call("validate_revised_srd_arcane_eye_origin", {
            "caster_state": "{_caster_state_id}", "position": "{information_position}",
            "lasting_state": "{_lasting_state_id}"}),
    ])]


def _damage_guard():
    return _tool("Reject a damageable-object classification for the actual active Arcane Eye source.",
        [_parameter("object_id"), _lasting()], [
            _call("srd_information_scene_entity", {"subject": "{object_id}",
                "lasting_state": "{_lasting_state_id}"}, ("_info_scene_entity",)),
            _when("_info_scene_entity.metadata.information_proxy == 'arcane_eye' and "
                "_info_scene_entity.metadata.source_caster != null", [
                _call("collect_srd_information_record", {
                    "caster_state": "{_info_scene_entity.metadata.source_caster}", "spell": "arcane_eye",
                    "lasting_state": "{_lasting_state_id}"}, ("_info_record", "_info_active")),
                _require("_info_active != true or _info_record.proxy_id != object_id",
                    "The actual active Arcane Eye is invulnerable and cannot be a damageable object."),
            ]),
        ])


def bind_revised_srd_information(runtime, captured):
    definitions, tools = runtime["definitions"], runtime["tools"]
    assert "install_srd_lite_utility" not in tools, "Skip the deferred legacy utility binder."
    for name in ("cast_spell_information_spell", "collect_srd_information_record",
        "query_srd_information_spell", "operate_srd_information_proxy", "communicate_srd_spell",
        "observe_srd_information_proxy", "dismiss_srd_information_spell"):
        assert name in tools, "Retain the complete original information consumers: " + name
    for spell in SPELLS:
        definitions["spells"][spell] = deepcopy(captured[spell]["profile"])
        definitions["spell_casting"][spell] = deepcopy(captured[spell]["header"])
    tools["validate_revised_srd_arcane_eye_origin"] = _origin_validator()
    tools["validate_revised_srd_arcane_eye_damage_target"] = _damage_guard()
    for name in ("cast_spell", "begin_srd_spell_action"):
        assert not any("validate_revised_srd_arcane_eye_origin" in str(e) for e in tools[name]["effects"]), \
            "Apply the revised information binder once."
        tools[name]["effects"][:0] = _prelude()
    for name in ("prepare_srd_spell_object_node", "resolve_srd_spell_object_relation"):
        assert name in tools, "Retain the actual original object target owner."
        effects = deepcopy(tools[name]["effects"])
        index = next(i for i, e in enumerate(effects) if e.get("set", {}).get("field") == "_object_scene")
        effects.insert(index + 1, _call("validate_revised_srd_arcane_eye_damage_target", {
            "object_id": "{object_id}", "lasting_state": "{_lasting_state_id}"}))
        tools[name] = deepcopy(tools[name])
        tools[name]["effects"] = effects
    return ("cast_spell", "begin_srd_spell_action", "validate_revised_srd_arcane_eye_origin",
        "validate_revised_srd_arcane_eye_damage_target", "prepare_srd_spell_object_node",
        "resolve_srd_spell_object_relation")
