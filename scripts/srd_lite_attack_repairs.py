"""SRD Lite 0.3.26 Magic Missile and Shield repairs (issues 163 and 165).

The SRD Lite package owner composes ATTACK_REPAIRS into its runtime update with the other
0.3.26 spell repairs. Each repair edits its owning tools and is idempotent, so rerunning the
update against an already repaired runtime changes nothing.
"""

from .srd_control_behaviors import (
    _call,
    _each,
    _entity,
    _lasting,
    _param,
    _push,
    _set,
    _tool,
    _when,
)
from .srd_lite_profiles import lists
from .srd_lite_ray_reactions import bind_ray_reactions

MISSILES = "cast_spell_automatic_force_projectiles"
DART = "apply_srd_lite_force_dart"


def _dart_tool():
    """One Magic Missile dart: its own damage, mitigation and Shield check, or no damage if dead."""
    return _tool(
        "Apply one rolled Magic Missile dart to its own target and record its result row.",
        [
            _entity("target"),
            _param("index", "integer"),
            _param("face", "integer"),
            _param("amount", "integer"),
            _param("flat", "integer"),
            _param("repeat", "boolean"),
            "param_damage_type_38a60b911799493b",
            "param_source_state_id_565a8b9e227b2551",
            "param_witness_state_ids_1dc147c145211b0d",
            _lasting(),
        ],
        [
            _set("_dart_hp_lost", 0),
            _set("_dart_temp_lost", 0),
            # A Ray of Enfeeblement penalty can take a die below zero; it reduces the flat +1.
            _set("_dart_amount", "max(0, amount)"),
            _set("_dart_flat", "flat + min(0, amount)"),
            _set("_dart_alive_before", "target.is_alive == true"),
            _set("_dart_rest_window", "target.short_rest_hit_dice_available"),
            _when("_dart_alive_before == true", [
                {
                    "call": "apply_srd_magic_missile_damage",
                    "params": {
                        "target": "{_target_id}",
                        "amount": "{_dart_amount}",
                        "adjustment": "{_dart_flat}",
                        "damage_type": "{damage_type}",
                        "source_state_id": "{source_state_id}",
                        "witness_state_ids": "{witness_state_ids}",
                    },
                    "export_context": {
                        "_dart_hp_lost": "_hp_lost",
                        "_dart_temp_lost": "_temp_hp_absorbed",
                    },
                },
            ]),
            # The darts strike simultaneously, so only a target's first dart interrupts its rest.
            # A later dart must not take back the short-rest Hit Dice that interruption granted.
            _when("repeat == true and _dart_rest_window == true and "
                  "target.short_rest_hit_dice_available != true", [
                _set("target.short_rest_hit_dice_available", True),
            ]),
            _set("_dart_damage", "_dart_hp_lost + _dart_temp_lost"),
            _set("_dart_alive_text", "'false'"),
            _when("_dart_alive_before == true", [_set("_dart_alive_text", "'true'")]),
            _call("record_srd_spell_target_result", {
                "target": "{_target_id}",
                "brief": "Dart {index}: damage {_dart_damage}",
                "detail": "Dart {index}. d4: {face}; {_dart_amount} + {_dart_flat} force before "
                          "mitigation. Damage {_dart_damage}. "
                          "HP lost {_dart_hp_lost}; temporary HP lost {_dart_temp_lost}. "
                          "Target alive before dart: {_dart_alive_text}.",
            }, ("_spell_target_summary", "_spell_target_detail")),
        ],
    )


def _dart(target):
    """Apply the current dart; a target already killed by an earlier dart takes nothing."""
    return [
        _set("_projectile_repeat", target + " in _projectile_struck"),
        _when("_projectile_repeat != true", [_push("_projectile_struck", target)]),
        _set("_projectile_face", "_spell_damage.results[_projectile_index]"),
        _set("_projectile_amount", "_ray_projectile_results[_projectile_index]"),
        _set("_projectile_index", "_projectile_index + 1"),
        _call(DART, {
            "target": "{" + target + "}",
            "index": "{_projectile_index}",
            "face": "{_projectile_face}",
            "amount": "{_projectile_amount}",
            "flat": "{flat_damage_per_projectile}",
            "repeat": "{_projectile_repeat}",
            "damage_type": "{damage_type}",
            "source_state_id": "{_caster_state_id}",
            "witness_state_ids": "{witness_state_ids}",
            "lasting_state": "{_lasting_state_id}",
        }, ("_spell_target_summary", "_spell_target_detail")),
    ]


def repair_magic_missile_darts(runtime):
    """Issue 165: each dart rolls its own face and takes resistance, Shield and death alone.

    SRD 5.2.1 Magic Missile: each dart deals 1d4 + 1 Force damage to its target. The package
    summed a target's darts and applied mitigation once, and a dart aimed at a creature an
    earlier dart killed failed the whole cast. Each dart is now its own damage instance with
    its own card row, and a dart at a dead creature deals nothing.
    """
    tools = runtime["tools"]
    tools[DART] = _dart_tool()
    owner = tools[MISSILES]
    (branch,) = [
        effect for effects in lists(owner["effects"]) for effect in effects
        if effect.get("condition") == "projectile_targets == null"
    ]
    single = branch["on_true"]
    if any(effect.get("call") == DART for effects in lists(single) for effect in effects):
        return
    (apply,) = [e for e in single if e.get("call") == "apply_srd_magic_missile_damage"]
    # One target keeps its single card row; its darts still apply one at a time.
    single[single.index(apply):single.index(apply) + 1] = [
        _set("_projectile_index", 0),
        _set("_projectile_struck", []),
        _each("_ray_projectile_results", "_single_dart_amount", _dart("_target_id")),
    ]
    branch["on_false"] = [
        _set("_projectile_index", 0),
        _set("_projectile_struck", []),
        _each("projectile_targets", "_projectile_target", [
            *_dart("_projectile_target"),
            _push("_spell_summary_rows", "_spell_target_summary"),
            _push("_spell_detail_rows", "_spell_target_detail"),
        ]),
    ]


def _capture_row(name):
    return {"key": f"'{name}'", "missing": f"{name} == null", "value": name}


def repair_held_spell_attack_keeps_target_ac(runtime):
    """Issue 163: a held spell-attack hit saves the target AC it was measured against.

    The Reaction window kept the attack roll, total and cover but not the armor class, so the
    saved evidence could not show why the attack hit or what Shield's +5 would face.
    """
    found = 0
    for tool in runtime["tools"].values():
        for effects in lists(tool.get("effects", [])):
            for effect in effects:
                body = effect.get("set", {})
                rows = body.get("value")
                if body.get("field") != "_retained_nullable_capture_rows":
                    continue
                keys = {row.get("key") for row in rows if isinstance(row, dict)}
                if not {"'_card_d20_total'", "'_spell_attack'"} <= keys:
                    continue
                found += 1
                if "'_spell_target_armor_class'" not in keys:
                    rows.append(_capture_row("_spell_target_armor_class"))
    if not found:
        raise ValueError("Held spell-attack context captures were not found")


def repair_scorching_ray_hit_windows(runtime):
    """Issue 163: each staged Scorching Ray hit opens its own Reaction window, in ray order.

    A staged Scorching Ray resolved every ray at once, so its target never had a hit to answer
    with Shield. A hit now holds the cast with its saved roll; completing the window finishes
    that ray against the target's current AC and continues with the next ray.
    """
    bind_ray_reactions(runtime)


ATTACK_REPAIRS = (
    repair_magic_missile_darts,
    repair_held_spell_attack_keeps_target_ac,
    repair_scorching_ray_hit_windows,
)
