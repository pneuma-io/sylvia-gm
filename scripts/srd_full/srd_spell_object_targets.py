"""SRD object attacks use scene geometry and the pinned Breaking Objects tables.

Spell wrappers own source admission, casting costs and retained effects. This seam
does not turn an object into a creature or infer collapse, burning, or magical
damage adjustments. Huge objects need separately admitted Large-or-smaller sections.
"""

import re
from copy import deepcopy

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS, _snapshot, _spatial, _tool_scene
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from .srd_timed_senses import _replace_tools
else:
    from srd_scene_relations import POSITION_EXPORTS, _snapshot, _spatial, _tool_scene
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from srd_timed_senses import _replace_tools


OBJECT_RULE_PAGES = {
    177: "2157ea5b29cdb71f3d186c48c87305184ec6c32dba1035e275cb921a5e35d5ab",
    178: "faa70df6c2f825399861fcaf42152a9c23e2b68a549f2303caaa1ece5890dbc7",
}
OBJECT_SOURCE = "wotc.srd.5.2.1:rules_glossary:p178:" + OBJECT_RULE_PAGES[178]
MATERIAL_AC = {
    "cloth": 11,
    "paper": 11,
    "rope": 11,
    "crystal": 13,
    "glass": 13,
    "ice": 13,
    "wood": 15,
    "stone": 17,
    "iron": 19,
    "steel": 19,
    "mithral": 21,
    "adamantine": 23,
}
OBJECT_HP = {
    "tiny": {"fragile": 2, "resilient": 5},
    "small": {"fragile": 3, "resilient": 10},
    "medium": {"fragile": 4, "resilient": 18},
    "large": {"fragile": 5, "resilient": 27},
}
INTRINSIC_IMMUNITIES = ("poison", "psychic")
PUBLIC_TOOLS = {"prepare_srd_spell_object"}
RELATION_EXPORTS = (
    "_object_scene",
    "_object_profile",
    "_object_distance",
    "_object_can_see",
    "_object_faerie",
    "_object_cover_bonus",
    "_object_effective_armor_class",
)
ATTACK_EXPORTS = (
    "_object_attack_roll",
    "_object_attack_total",
    "_object_attack_kind",
    "_object_damage_roll",
    "_object_damage_rolled",
    "_object_damage_applied",
    "_object_hp_before",
    "_object_hp_after",
    "_object_destroyed",
    "_object_effective_armor_class",
    "_object_can_see",
    "_object_advantage",
    "_object_disadvantage",
)


def _lookup_object():
    """Resolve only an actual non-participant scene identity, preserving its geometry."""
    return [
        _call("srd_information_scene_entity", {"subject": "{object_id}"}, POSITION_EXPORTS),
        _require(
            "_info_scene_entity.id == object_id and "
            "_info_scene_entity.metadata.participant_ref == null",
            "An object target must be an actual non-creature scene ID.",
        ),
        _set("_object_scene", "_info_scene_entity"),
    ]


def _stats(material="material", size="size", durability="durability"):
    """Use the source's suggested averages; no caller supplies numeric AC or HP."""
    return [
        _set("_object_ac_table", MATERIAL_AC),
        _set("_object_hp_table", OBJECT_HP),
        _set("_object_base_ac", f"_object_ac_table[{material}]"),
        _set("_object_size_hp", f"_object_hp_table[{size}]"),
        _set("_object_max_hp", f"_object_size_hp[{durability}]"),
    ]


def _persist_profile():
    """Persist metadata through the neutral scene mutation and revision primitive."""
    return [
        *_snapshot(),
        _set(
            "_object_metadata_changes", {"srd_spell_object": "_object_profile", "is_object": True}
        ),
        _set("_object_metadata", "merge(_object_scene.metadata, _object_metadata_changes)"),
        {
            "spatial_mutation": {
                "scene": "state.spatial_scene",
                "map_id": "{state.spatial_scene.map.id}",
                "expected_revision": "{_info_snapshot.geometry_revision}",
                "target": {"kind": "entity", "id": "{object_id}"},
                "patch": {"metadata": "{_object_metadata}"},
            },
            "store_as": "_object_updated",
        },
    ]


def _validate_profile():
    """Reject unsupported or altered rule fields before rolling or mutating state."""
    return [
        _set("_object_profile", "_object_scene.metadata.srd_spell_object"),
        _set("_object_ac_table", MATERIAL_AC),
        _set("_object_hp_table", OBJECT_HP),
        _require(
            "_object_profile != null and len(_object_profile) == 9 and "
            "_object_profile.kind == 'srd_spell_object' and "
            f"_object_profile.source == {OBJECT_SOURCE!r} and "
            "_object_profile.material in _object_ac_table and "
            "_object_profile.size in _object_hp_table and "
            "(_object_profile.durability == 'fragile' or "
            "_object_profile.durability == 'resilient')",
            "The object needs its supported, source-qualified Breaking Objects profile.",
        ),
        *_stats("_object_profile.material", "_object_profile.size", "_object_profile.durability"),
        _require(
            "_object_profile.armor_class == _object_base_ac and "
            "_object_profile.max_hp == _object_max_hp and "
            "_object_profile.hp >= 0 and _object_profile.hp <= _object_max_hp and "
            "_object_profile.destroyed == (_object_profile.hp == 0)",
            "Object rule statistics must match their pinned material and size table.",
        ),
        _require("_object_profile.hp > 0", "This object section is already destroyed."),
    ]


def _object_tools(*, illumination=False, object_visibility=False, information_light=False):
    prepare = _tool_scene(
        "Classify an admitted object using SRD material AC and suggested average HP. "
        "For Huge or Gargantuan objects admit separate Large-or-smaller sections; "
        "whole-object collapse, thresholds and other magical adjustments are unsupported.",
        [
            _parameter("object_id"),
            _parameter("material", "enum", enum_values=list(MATERIAL_AC)),
            _parameter("size", "enum", enum_values=list(OBJECT_HP)),
            _parameter("durability", "enum", enum_values=["fragile", "resilient"]),
        ],
        [
            _require("combat.encounter_active != true", "Classify objects outside combat."),
            _require("combat.pending_action == null", "Finish the pending action first."),
            *_lookup_object(),
            _require(
                "_object_scene.metadata.srd_spell_object == null",
                "This object is already classified; preparation cannot reset its damage.",
            ),
            *_stats(),
            _set(
                "_object_profile",
                {
                    "kind": "'srd_spell_object'",
                    "source": repr(OBJECT_SOURCE),
                    "material": "material",
                    "size": "size",
                    "durability": "durability",
                    "armor_class": "_object_base_ac",
                    "max_hp": "_object_max_hp",
                    "hp": "_object_max_hp",
                    "destroyed": False,
                },
            ),
            *_persist_profile(),
        ],
    )
    # The public wrapper never accepts the private lasting-state binding.
    prepare_node = deepcopy(prepare)
    prepare["parameters"] = [p for p in prepare["parameters"] if p["name"] != "lasting_state"]
    prepare["effects"] = [
        _call(
            "prepare_srd_spell_object_node",
            {
                **{p["name"]: "{" + p["name"] + "}" for p in prepare["parameters"]},
                "lasting_state": "state",
            },
            ["_object_profile"],
        )
    ]
    prepare["metadata"] = {
        "category": "exploration",
        "visible_to_player": False,
        "costs_action": False,
    }

    relation = [
        *_lookup_object(),
        *_validate_profile(),
        _set("_object_invisible", "_object_scene.metadata.invisible == true"),
        _set("_object_faerie", False),
    ]
    if not object_visibility:
        relation.append(
            _require(
                "_object_scene.metadata.carried_by == null and "
                "_object_scene.metadata.worn_by == null",
                "Carried objects need their actual holder visibility and geometry helper.",
            )
        )
    if object_visibility:
        relation.extend(
            [
                _call(
                    "check_srd_illumination_object_visibility",
                    {"object_id": "{object_id}", "observer_state": "{_caster_state_id}"},
                    [
                        "_illumination_object_invisible",
                        "_illumination_object_outlined",
                        "_illumination_object_suppressed",
                        "_illumination_object_origin",
                        "_illumination_object_plane",
                        "_illumination_object_lighting",
                        "_illumination_object_holder_scene_id",
                    ],
                ),
                _require(
                    "_illumination_object_origin != null and "
                    "_illumination_object_plane != null and "
                    "_illumination_object_lighting != null",
                    "The actual holder needs its admitted origin, plane and ambient lighting.",
                ),
                _when(
                    "_object_scene.position[0] != _illumination_object_origin[0] or "
                    "_object_scene.position[1] != _illumination_object_origin[1] or "
                    "_object_scene.position[2] != _illumination_object_origin[2] or "
                    "_object_scene.metadata.plane != _illumination_object_plane or "
                    "_object_scene.metadata.lighting != _illumination_object_lighting",
                    [
                        _set(
                            "_object_holder_metadata",
                            {
                                "plane": "_illumination_object_plane",
                                "lighting": "_illumination_object_lighting",
                            },
                        ),
                        _set(
                            "_object_metadata",
                            "merge(_object_scene.metadata, _object_holder_metadata)",
                        ),
                        {
                            "spatial_mutation": {
                                "scene": "state.spatial_scene",
                                "map_id": "{state.spatial_scene.map.id}",
                                "expected_revision": "{state.spatial_scene.map.revision}",
                                "target": {"kind": "entity", "id": "{object_id}"},
                                "patch": {
                                    "position": "{_illumination_object_origin}",
                                    "metadata": "{_object_metadata}",
                                },
                            },
                            "store_as": "_object_holder_update",
                        },
                        *_lookup_object(),
                    ],
                ),
                _set("_object_faerie", "_illumination_object_outlined == true"),
                _when("_illumination_object_invisible == true", [_set("_object_invisible", True)]),
                _when(
                    "_illumination_object_suppressed == true", [_set("_object_invisible", False)]
                ),
            ]
        )
    relation.extend(
        [
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _set("_object_caster_scene", "_info_scene_entity"),
            _require(
                "_object_caster_scene.metadata.plane != null and "
                "_object_scene.metadata.plane != null and "
                "_object_caster_scene.metadata.plane == _object_scene.metadata.plane",
                "Caster and object must occupy the same admitted plane.",
            ),
            _call(
                "measure_srd_information_distance",
                {
                    "start": "{_object_caster_scene.position}",
                    "end": "{_object_scene.position}",
                },
                ["_scene_distance"],
            ),
            _set("_object_distance", "_scene_distance"),
            _require(
                "_object_distance <= max_range_feet", "The actual object is beyond spell range."
            ),
            *_snapshot(),
            _spatial(
                "exposure",
                {
                    "map_id": "{state.spatial_scene.map.id}",
                    "observer": {"point": "{_object_caster_scene.position}"},
                    "target": {"entity_id": "{object_id}"},
                    "samples": {"horizontal": 3, "vertical": 3},
                    "include": ["geometry"],
                },
                "_object_exposure",
            ),
            _require(
                "_object_exposure.visible_samples > 0", "Total Cover blocks this object attack."
            ),
            _set("_object_cover_bonus", 0),
            _when("_object_exposure.exposure <= 0.5", [_set("_object_cover_bonus", 2)]),
            _when("_object_exposure.exposure <= 0.25", [_set("_object_cover_bonus", 5)]),
            _set(
                "_object_effective_armor_class", "_object_profile.armor_class + _object_cover_bonus"
            ),
            _require(
                "_object_scene.metadata.lighting != null",
                "The object needs its actual ambient lighting.",
            ),
            _set("_object_light", "_object_scene.metadata.lighting"),
        ]
    )
    if illumination and not object_visibility:
        relation.extend(
            [
                _call(
                    "check_srd_illumination_subject",
                    {"subject": "{object_id}"},
                    [
                        "_illumination_faerie",
                        "_illumination_suppressed",
                        "_illumination_invisible",
                    ],
                ),
                _set("_object_faerie", "_illumination_faerie == true"),
                _when("_illumination_invisible == true", [_set("_object_invisible", True)]),
                _when("_illumination_suppressed == true", [_set("_object_invisible", False)]),
            ]
        )
    if information_light:
        relation.extend(
            [
                _call(
                    "resolve_srd_information_light",
                    {
                        "point": "{_object_scene.position}",
                        "plane": "{_object_scene.metadata.plane}",
                        "ambient": "{_object_light}",
                    },
                    ["_information_light"],
                ),
                _set("_object_light", "_information_light"),
            ]
        )
    relation.extend(
        [
            _set("_object_sight_distance", "ceil(_object_distance)"),
            _call(
                "resolve_srd_sight",
                {
                    "observer_state": "{_caster_state_id}",
                    "distance": "{_object_sight_distance}",
                    "lighting": "{_object_light}",
                    "obstruction": "clear",
                    "subject_invisible": "{_object_invisible}",
                },
                ["_sight_can_see"],
            ),
            _set("_object_can_see", "_sight_can_see == true"),
            _when(
                "caster_state.blindsight_feet != null and "
                "caster_state.blindsight_feet >= _object_distance",
                [_set("_object_can_see", True)],
            ),
        ]
    )
    relation_tool = _tool_scene(
        "Derive prepared object statistics, actual range, cover and source-qualified sight.",
        [
            _entity("caster_state"),
            _parameter("object_id"),
            _parameter("max_range_feet", "integer", minimum=0, maximum=100000),
        ],
        relation,
    )
    attack = _tool_scene(
        "Private spell consumer: use admitted constants, actual dice and object immunity. "
        "The owning spell wrapper pays costs and validates source-specific attack inputs.",
        [
            _entity("caster_state"),
            _parameter("object_id"),
            _parameter("attack_modifier", "integer", minimum=-100, maximum=100),
            _parameter("damage_die", "enum", enum_values=["d4", "d6", "d8", "d10", "d12"]),
            _parameter("dice_count", "integer", minimum=1, maximum=100),
            _parameter(
                "damage_type",
                "enum",
                enum_values=[
                    "acid",
                    "bludgeoning",
                    "cold",
                    "fire",
                    "force",
                    "lightning",
                    "necrotic",
                    "piercing",
                    "poison",
                    "psychic",
                    "radiant",
                    "slashing",
                    "thunder",
                ],
            ),
            _parameter("max_range_feet", "integer", minimum=0, maximum=100000),
            _parameter("advantage", "boolean", required=False, default=False),
            _parameter("disadvantage", "boolean", required=False, default=False),
        ],
        [
            _require(
                "caster_state.is_alive == true and caster_state.hp > 0",
                "A living caster is required.",
            ),
            _call(
                "resolve_srd_spell_object_relation",
                {
                    "caster_state": "{_caster_state_id}",
                    "object_id": "{object_id}",
                    "max_range_feet": "{max_range_feet}",
                },
                RELATION_EXPORTS,
            ),
            _set(
                "_object_advantage",
                "advantage == true or (_object_faerie == true and _object_can_see == true)",
            ),
            _set("_object_disadvantage", "disadvantage == true or _object_can_see != true"),
            _call(
                "d20_test",
                {
                    "kind": "attack_roll",
                    "actor_state": "{_caster_state_id}",
                    "ability_modifier": "{attack_modifier}",
                    "proficiency_bonus": 0,
                    "proficiency_factor": "none",
                    "circumstantial_modifier": 0,
                    "target_number": "{_object_effective_armor_class}",
                    "advantage": "{_object_advantage}",
                    "disadvantage": "{_object_disadvantage}",
                    "target_can_see_actor": False,
                },
                [
                    "_d20_test",
                    "_attack_outcome",
                    "_outcome",
                    "_adjusted_total",
                    "_effective_advantage",
                    "_effective_disadvantage",
                ],
            ),
            _set(
                "_object_advantage",
                "_effective_advantage == true and _effective_disadvantage != true",
            ),
            _set(
                "_object_disadvantage",
                "_effective_disadvantage == true and _effective_advantage != true",
            ),
            _set("_object_attack_roll", "_d20_test"),
            _set("_object_attack_total", "_adjusted_total"),
            _set("_object_attack_kind", "'miss'"),
            _when("_outcome == 'success'", [_set("_object_attack_kind", "'hit'")]),
            _when(
                "_attack_outcome == 'critical_hit'", [_set("_object_attack_kind", "'critical_hit'")]
            ),
            _set("_object_hp_before", "_object_profile.hp"),
            _set("_object_damage_roll", None),
            _set("_object_damage_rolled", 0),
            _set("_object_damage_applied", 0),
            _set("_object_intrinsic_immunities", list(INTRINSIC_IMMUNITIES)),
            _when(
                "_object_attack_kind == 'hit' or _object_attack_kind == 'critical_hit'",
                [
                    {
                        "dice": {
                            "expression": "{dice_count}{damage_die}",
                            "critical": "_object_attack_kind == 'critical_hit'",
                        },
                        "store_as": "_object_damage_roll",
                    },
                    _set("_object_damage_rolled", "_object_damage_roll.total"),
                    _when(
                        "damage_type not in _object_intrinsic_immunities",
                        [
                            _set(
                                "_object_damage_applied",
                                "min(_object_hp_before, _object_damage_rolled)",
                            ),
                        ],
                    ),
                ],
            ),
            _set("_object_hp_after", "_object_hp_before - _object_damage_applied"),
            _set("_object_destroyed", "_object_hp_after == 0"),
            _when(
                "_object_damage_applied > 0",
                [
                    _set(
                        "_object_damage_changes",
                        {"hp": "_object_hp_after", "destroyed": "_object_destroyed"},
                    ),
                    _set("_object_profile", "merge(_object_profile, _object_damage_changes)"),
                    *_persist_profile(),
                ],
            ),
        ],
    )
    return {
        "prepare_srd_spell_object": prepare,
        "prepare_srd_spell_object_node": prepare_node,
        "resolve_srd_spell_object_relation": relation_tool,
        "resolve_srd_spell_object_attack": attack,
    }


def extend_runtime(text):
    """Add the object seam, using actual illumination source owners when present."""
    tools = _load_runtime(text)["tools"]
    changed = _object_tools(
        illumination="check_srd_illumination_subject" in tools,
        object_visibility="check_srd_illumination_object_visibility" in tools,
        information_light="resolve_srd_information_light" in tools,
    )
    # State helpers accept simple context references, not inline expressions.
    argument_index = 0

    def normalize(effects):
        nonlocal argument_index
        result = []
        for effect in effects:
            for key in ("on_true", "on_false"):
                if key in effect:
                    effect[key] = normalize(effect[key])
            if "call" in effect:
                for name, value in effect["params"].items():
                    if (
                        isinstance(value, str)
                        and re.fullmatch(r"\{[^{}]+\}", value)
                        and not re.fullmatch(r"\{[A-Za-z_][A-Za-z0-9_.]*\}", value)
                    ):
                        argument_index += 1
                        local = f"_object_argument_{argument_index}"
                        result.append(_set(local, value[1:-1]))
                        effect["params"][name] = "{" + local + "}"
            result.append(effect)
        return result

    for tool in changed.values():
        tool["effects"] = normalize(tool["effects"])
    return _replace_tools(text, changed)
