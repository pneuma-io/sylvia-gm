"""One actual cumulative save for source-owned progressive spell effects."""

if __package__:
    from .srd_control_behaviors import (
        _call,
        _entity,
        _lasting,
        _param,
        _require,
        _set,
        _tool,
        _when,
    )
else:
    from srd_control_behaviors import _call, _entity, _lasting, _param, _require, _set, _tool, _when


def progressive_save_tool():
    """Advance saved counters only; each spell owns timing, receipts, and threshold outcomes."""
    save = _call(
        "d20_test",
        {
            "actor_state": "{_target_state_id}",
            "ability": "{_progressive_source.save_ability}",
            "kind": "saving_throw",
            "ability_modifier": "{_progressive_modifier}",
            "proficiency_bonus": 0,
            "proficiency_factor": "none",
            "circumstantial_modifier": 0,
            "target_number": "{_progressive_source.save_dc}",
            "advantage": False,
            "disadvantage": False,
            "forego_save": False,
            "save_avoids_condition": "{_progressive_source.condition}",
        },
    )
    save["export_context"] = {
        "_progressive_save_roll": "_d20_test",
        "_progressive_save_outcome": "_outcome",
        "_progressive_save_total": "_adjusted_total",
    }
    return _tool(
        "Roll the source's actual saving throw and increment its cumulative success or failure.",
        [_entity("target_state"), _lasting(), _param("source_id")],
        [
            _set("_progressive_source", "lasting_state.timed_condition_sources[source_id]"),
            _require(
                "_progressive_source != null and _progressive_source.installed == true and "
                "_progressive_source.target_state_id == _target_state_id",
                "The progressive source must be installed on the saving creature.",
            ),
            _require(
                "_progressive_source.progressive_successes != null and "
                "_progressive_source.progressive_failures != null and "
                "_progressive_source.progressive_successes >= 0 and "
                "_progressive_source.progressive_successes < 3 and "
                "_progressive_source.progressive_failures >= 0 and "
                "_progressive_source.progressive_failures < 3",
                "The source needs its actual unfinished cumulative save counters.",
            ),
            _require(
                "target_state.is_alive == true and _progressive_source.save_dc != null and "
                "(_progressive_source.save_ability == 'strength' or "
                "_progressive_source.save_ability == 'dexterity' or "
                "_progressive_source.save_ability == 'constitution' or "
                "_progressive_source.save_ability == 'intelligence' or "
                "_progressive_source.save_ability == 'wisdom' or "
                "_progressive_source.save_ability == 'charisma')",
                "A living creature and a saved ability and DC are required.",
            ),
            _set(
                "_progressive_modifier",
                "target_state.saving_throw_modifiers[_progressive_source.save_ability]",
            ),
            _require(
                "_progressive_modifier != null",
                "The creature needs its actual saving-throw modifier.",
            ),
            save,
            _set("_progressive_successes", "_progressive_source.progressive_successes"),
            _set("_progressive_failures", "_progressive_source.progressive_failures"),
            _when(
                "_progressive_save_outcome == 'success'",
                [_set("_progressive_successes", "_progressive_successes + 1")],
                [_set("_progressive_failures", "_progressive_failures + 1")],
            ),
            _set(
                "lasting_state.timed_condition_sources.{source_id}.progressive_successes",
                "_progressive_successes",
            ),
            _set(
                "lasting_state.timed_condition_sources.{source_id}.progressive_failures",
                "_progressive_failures",
            ),
        ],
    )
