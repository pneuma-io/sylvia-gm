"""Bind independently authored legacy healing profiles to retained engine owners."""

from .srd_control_behaviors import _entity, _lasting, _param
from .srd_timed_actor_defenses import _call, _require, _tool, _when

HEALING = {"cure_wounds", "healing_word"}


def bind_healing(runtime):
    """Keep scene eligibility authoritative while reusing actual HP and casting owners."""
    for name in HEALING:
        runtime["definitions"]["spells"][name].update(base_dice=1, dice_per_slot_level=1)
        runtime["definitions"]["spell_casting"][name]["school"] = "evocation"
    runtime["tools"]["validate_srd_lite_healing_target"] = _tool(
        "Qualify a living healing target against saved creature and scene state.",
        [_entity("caster_state"), _entity("target_state"), _lasting(), _param("spell")],
        [
            _require(
                "target_state.is_alive == true and "
                "(target_state.creature_kind == 'character' or target_state.creature_kind == 'monster')",
                "Ordinary healing needs a living creature; it cannot resurrect the dead.",
            ),
            _require(
                "target_state.creature_type != 'undead' and target_state.creature_type != 'construct'",
                "This healing spell cannot affect undead or constructs.",
            ),
            _call(
                "resolve_srd_scene_geometry_relation",
                {"actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
                ("_scene_distance", "_scene_same_plane", "_scene_line_of_sight", "_info_scene_entity"),
            ),
            _require(
                "_info_scene_entity.metadata.creature_type != null and "
                "_info_scene_entity.metadata.creature_type != 'undead' and "
                "_info_scene_entity.metadata.creature_type != 'construct'",
                "The saved scene must identify a creature eligible for this healing spell.",
            ),
            _require(
                "_scene_same_plane == true and _scene_line_of_sight == true and "
                "((spell == 'cure_wounds' and _scene_distance <= 5) or "
                "(spell == 'healing_word' and _scene_distance <= 60))",
                "The actual healing target is outside the spell's range or clear path.",
            ),
            _when(
                "spell == 'healing_word'",
                [
                    _call("resolve_srd_scene_relation_node", {
                        "actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}",
                        "lasting_state": "{_lasting_state_id}",
                    }, ("_scene_can_see",)),
                    _require("_scene_can_see == true", "Healing Word needs actual sight of its target."),
                ],
            ),
        ],
    )
    for name in ("cast_spell", "begin_srd_spell_action"):
        runtime["tools"][name]["effects"].insert(
            0,
            _when(
                "spell == 'cure_wounds' or spell == 'healing_word'",
                [_call("validate_srd_lite_healing_target", {
                    "caster_state": "{_caster_state_id}", "target_state": "{_target_id}",
                    "lasting_state": "{_lasting_state_id}", "spell": "{spell}",
                })],
            ),
        )
