"""Strict connection to the existing native ordinary receiver and final HP receipts."""

import json
from dataclasses import dataclass

if __package__:
    from .srd_spell_summoning import _choose, _walk
    from .srd_timed_actor_defenses import _set, _when
    from .srd_warding_bond_damage import _receiving_start
else:
    from srd_spell_summoning import _choose, _walk
    from srd_timed_actor_defenses import _set, _when
    from srd_warding_bond_damage import _receiving_start

MONSTER_ZERO = "target.hp == 0 and target.creature_kind == 'monster'"
GUARDED_MONSTER_ZERO = "(" + MONSTER_ZERO + ") and target.astral_projection_role != 'form'"
MASSIVE_ZERO = (
    "target.hp == 0 and target.creature_kind == 'character' and _remaining_damage >= target.max_hp"
)
GUARDED_MASSIVE_ZERO = "(" + MASSIVE_ZERO + ") and target.astral_projection_role != 'form'"


@dataclass
class NativeReceivingBoundary:
    """References into one passed packet; callers preserve its enclosing branches and tail."""

    ordinary: list
    hp_before_index: int
    completion_index: int
    monster_zero: dict
    still_zero: list
    massive_zero: dict
    presentation: dict
    wrapped: bool
    receiving_effects: list


def _one(rows, predicate, label):
    selected = [(index, row) for index, row in enumerate(rows) if predicate(row)]
    if len(selected) != 1:
        raise ValueError("Native receiver needs one exact " + label + ".")
    return selected[0]


# Proposed exact current scalar Magic Jar wrapper; parent review pending.
_JAR_SCALAR_CONDITION = (
    "lasting_state.has_magic_jar_sources == true and "
    "(target.magic_jar_role == 'host_soul' or "
    "target.magic_jar_role == 'possessed' or "
    "target.magic_jar_role == 'container')"
)
_JAR_SCALAR_TRUE = json.loads(r"""[
  {
    "call": "resolve_srd_magic_jar_target",
    "params": {
      "target_state": "{target}"
    },
    "export_context": {
      "_magic_jar_target_id": "_magic_jar_target_id"
    }
  },
  {
    "call": "apply_srd_magic_jar_occupied_damage",
    "params": {
      "target": "{_magic_jar_target_id}",
      "amount": "{amount}",
      "adjustment": "{adjustment}",
      "damage_type": "{damage_type}",
      "critical_hit": "{critical_hit}",
      "source_state_id": "{source_state_id}",
      "witness_state_ids": "{witness_state_ids}",
      "lasting_state": "{_lasting_state_id}",
      "time_stop_state": "{_time_stop_state_id}"
    },
    "export_context": {
      "_concentration_save_dc": "_concentration_save_dc",
      "_concentration_save_outcome": "_concentration_save_outcome",
      "_concentration_save_roll": "_concentration_save_roll",
      "_conditions_after": "_conditions_after",
      "_death_failures_after": "_death_failures_after",
      "_effective_damage": "_effective_damage",
      "_hp_after": "_hp_after",
      "_hp_lost": "_hp_lost",
      "_is_alive_after": "_is_alive_after",
      "_stable_after": "_stable_after",
      "_stable_recovery_hours_remaining_after": "_stable_recovery_hours_remaining_after",
      "_temp_hp_absorbed": "_temp_hp_absorbed",
      "_temp_hp_after": "_temp_hp_after"
    }
  }
]""")


def native_receiving_scope(effects):
    """Locate the genuine ordinary branch through only the exact named Jar wrapper."""
    if effects and effects[0].get("condition") == _JAR_SCALAR_CONDITION:
        guard = effects[0]
        if (
            set(guard) != {"condition", "on_true", "on_false"}
            or guard["on_true"] != _JAR_SCALAR_TRUE
        ):
            raise ValueError("Native receiver rejects a changed Magic Jar scalar wrapper.")
        scope = guard["on_false"]
        _receiving_start(scope)  # Existing strict complete flat/TP shape check.
        return scope
    _receiving_start(effects)
    return effects


def native_receiving_boundary(effects, *, native_fist=False):
    """Select only known complete flat/canonical or True Polymorph receiving shapes."""
    original_effects = effects
    effects = native_receiving_scope(effects)
    start = _receiving_start(effects)
    wrapped = effects[start].get("set", {}).get("field") == "_tp_received_object"
    ordinary = effects[start + 1]["on_false"] if wrapped else effects
    before, snapshot = _one(
        ordinary,
        lambda row: row.get("set", {}).get("field") == "_hp_before",
        "ordinary HP-before initializer",
    )
    if snapshot != _set("_hp_before", "target.hp"):
        raise ValueError("Native receiver must retain the genuine HP-before expression.")
    zero_index, zero = _one(
        ordinary,
        lambda row: row.get("condition") in (MONSTER_ZERO, GUARDED_MONSTER_ZERO),
        "ordinary monster-zero boundary",
    )
    massive_conditions = (MASSIVE_ZERO, GUARDED_MASSIVE_ZERO)
    if native_fist:
        initializers = [
            row for row in effects if row.get("set", {}).get("field") == "_mummy_fist_dust"
        ]
        if initializers != [_set("_mummy_fist_dust", False)]:
            raise ValueError("Native Fist needs its exact Dust initializer.")
        massive_conditions = tuple(
            value + " and _mummy_fist_dust != true" for value in massive_conditions
        )
    _, massive = _one(
        ordinary,
        lambda row: row.get("condition") in massive_conditions,
        "massive-damage boundary",
    )
    if before >= zero_index or set(zero) != {"condition", "on_true"}:
        raise ValueError("Native receiver must preserve the ordered ordinary zero branch.")
    damage_index, damage = _one(
        ordinary,
        lambda row: "apply_damage" in row,
        "ordinary HP damage application",
    )
    hp_damage_index, hp_damage = _one(
        ordinary,
        lambda row: row.get("set", {}).get("field") == "_hp_damage",
        "post-temporary-HP damage receipt",
    )
    if (
        not before < hp_damage_index < damage_index < zero_index
        or hp_damage != _set("_hp_damage", "_effective_damage - _temp_hp_absorbed")
        or damage != {"apply_damage": {"target": "target", "amount": "_hp_damage"}}
    ):
        raise ValueError("Native receiver must retain its genuine defended HP damage order.")
    _, still_zero = _one(
        zero["on_true"],
        lambda row: row.get("condition") == "target.hp == 0",
        "still-zero suffix after Fortitude",
    )
    if set(still_zero) != {"condition", "on_true"}:
        raise ValueError("Native receiver must preserve the complete still-zero death suffix.")
    death = still_zero["on_true"]
    death_stamp = [
        index for index, row in enumerate(death) if row.get("call") == "record_srd_actual_death"
    ]
    dead = {"apply_condition": {"target": "target", "condition": "dead"}}
    if len(death_stamp) != 1 or dead not in death or death.index(dead) >= death_stamp[0]:
        raise ValueError("Native receiver needs the actual dead condition and ordered death stamp.")
    if wrapped:
        presentation = _when(
            "_tp_received_object != true",
            [
                _choose(
                    "_tp_native_ended == true",
                    [_set("_hp_after", "_tp_native_hp_after")],
                    [_set("_hp_after", "target.hp")],
                )
            ],
        )
        matches = [(index, row) for index, row in enumerate(effects) if row == presentation]
        if len(matches) != 1 or matches[0][0] <= start + 1:
            raise ValueError("Native receiver needs the exact guarded final HP-after choice.")
        index, presentation = matches[0]
        ended, initializer = _one(
            effects,
            lambda row: row.get("set", {}).get("field") == "_tp_native_ended",
            "native retirement initializer",
        )
        if initializer != _set("_tp_native_ended", False) or not start + 1 < ended < index:
            raise ValueError("Native receiver must retain retirement before final HP receipts.")
        # Native maximum loss/rise belongs after this whole ordinary receive, before
        # outer retirement and presentation. The object branch remains untouched.
        completion = len(ordinary)
    else:
        candidates = []
        for index, row in enumerate(effects):
            if row.get("set", {}).get("field") == "_hp_after":
                if row != _set("_hp_after", "target.hp"):
                    raise ValueError("Native receiver needs the genuine flat HP-after expression.")
                candidates.append((index, row))
            elif row.get("condition") == "render_damage_snapshot == true":
                if set(row) != {"condition", "on_true"}:
                    raise ValueError(
                        "Native receiver needs the exact canonical presentation choice."
                    )
                snapshots = [
                    e for e in row["on_true"] if e.get("set", {}).get("field") == "_hp_after"
                ]
                if snapshots != [_set("_hp_after", "target.hp")]:
                    raise ValueError("Native receiver needs one canonical HP-after receipt.")
                candidates.append((index, row))
        if len(candidates) != 1 or candidates[0][0] <= zero_index:
            raise ValueError("Native receiver needs one ordered final HP presentation boundary.")
        completion, presentation = candidates[0]
    # Walking is an ambiguity veto, never an anchor-selection fallback.
    writes = [row.get("set", {}).get("field") for row in _walk(original_effects)]
    if writes.count("_hp_before") != 1 or writes.count("_hp_after") != (2 if wrapped else 1):
        raise ValueError("Native receiver rejects extra or missing HP receipt writes.")
    return NativeReceivingBoundary(
        ordinary, before, completion, zero, death, massive, presentation, wrapped, effects
    )
