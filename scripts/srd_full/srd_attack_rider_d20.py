"""Join live Hex, Hunter's Mark and Shining sources to the native roll owners."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_runtime_fragments import _ToolFragments
    from .srd_timed_actor_defenses import _call, _load_runtime, _RuntimeDumper, _set, _when
else:
    from srd_runtime_fragments import _ToolFragments
    from srd_timed_actor_defenses import _call, _load_runtime, _RuntimeDumper, _set, _when


OWNERS = ("d20_test", "ability_check", "search_srd_action", "resolve_srd_attack_conditions")
CHECK_EXPORTS = ("_rider_check_disadvantage", "_rider_finding_advantage")
FINDING_PARAMETER = {
    "name": "finding_target",
    "type": "string",
    "resolve": "entity",
    "required": False,
    "default": None,
    "description": "Actual creature this Wisdom Perception or Survival check attempts to find.",
}
CHECK_GROUP = "kind == 'ability_check' or kind == 'attack_roll'"


def _walk_lists(effects):
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false", "do"):
            if isinstance(effect.get(key), list):
                yield from _walk_lists(effect[key])


def _one(items, message):
    if len(items) != 1:
        raise ValueError(message)
    return items[0]


def _check_group(tool):
    """Use the normalized native group, which saves skip before any rider work."""
    group = _one(
        [
            effect
            for rows in _walk_lists(tool["effects"])
            for effect in rows
            if effect.get("condition") == CHECK_GROUP
            and any(
                "'blinded' in actor_state.conditions" in child.get("condition", "")
                for child in effect.get("on_true", [])
            )
        ],
        "Rider D20 binding requires the one normalized native attack/check group.",
    )
    owner = _one(
        [effect for effect in tool["effects"] if group in effect.get("on_true", [])],
        "Rider D20 binding requires the native group under its real actor-state guard.",
    )
    if owner.get("condition") != "actor_state != null":
        raise ValueError("Rider D20 binding cannot replace the actual actor-state guard.")
    effective = _one(
        [
            effect
            for effect in tool["effects"]
            if effect.get("set", {}).get("field") == "_effective_advantage"
        ],
        "Rider D20 binding requires the original effective Advantage owner.",
    )
    if tool["effects"].index(owner) >= tool["effects"].index(effective):
        raise ValueError("Rider source flags must precede the effective roll flags.")
    return group["on_true"]


def _check_hook():
    return _when(
        "kind == 'ability_check' and ability != null and "
        "(actor_state.attack_rider_source_ids != null or "
        "actor_state.attack_rider_owned_ids != null)",
        [
            _set("_rider_native_skill", "''"),
            _when("skill != null", [_set("_rider_native_skill", "skill")]),
            _set("_rider_native_subject", "''"),
            _when(
                "finding_target != null",
                [_set("_rider_native_subject", "_finding_target_id")],
            ),
            _call(
                "check_srd_attack_rider_d20",
                {
                    "actor_state": "{_actor_state_id}",
                    "kind": "ability_check",
                    "ability": "{ability}",
                    "skill": "{_rider_native_skill}",
                    "subject_state_id": "{_rider_native_subject}",
                },
                CHECK_EXPORTS,
            ),
            _set(
                "_condition_advantage",
                "_condition_advantage == true or _rider_finding_advantage == true",
            ),
            _set(
                "_condition_disadvantage",
                "_condition_disadvantage == true or _rider_check_disadvantage == true",
            ),
        ],
    )


def _check_additions():
    return [_set(name, False) for name in CHECK_EXPORTS] + [_check_hook()]


def _attack_additions():
    return [
        _set("_rider_attack_advantage", False),
        _when(
            "target_state.attack_rider_source_ids != null",
            [
                _call(
                    "check_srd_shining_attack_target",
                    {"target_state": "{_target_state_id}"},
                    ("_rider_attack_advantage",),
                ),
                _set(
                    "_condition_advantage",
                    "_condition_advantage == true or _rider_attack_advantage == true",
                ),
            ],
        ),
    ]


def _finding_preconditions(name):
    predicate = "ability == 'wisdom' and " if name != "search_srd_action" else ""
    if name == "d20_test":
        predicate = "kind == 'ability_check' and " + predicate
    rows = [
        {
            "condition": "finding_target == null or ("
            + predicate
            + "(skill == 'perception' or skill == 'survival'))",
            "error": "A finding target is intent for a Wisdom Perception or Survival check only.",
        },
        {
            "condition": "finding_target == null or finding_target.creature_kind == 'character' "
            "or finding_target.creature_kind == 'monster'",
            "error": "Find an actual creature, using its mechanical entity identity.",
        },
    ]
    if name == "d20_test":
        rows.append(
            {
                "condition": "finding_target == null or actor_state != null",
                "error": "A finding check requires its actual checking creature's entity state.",
            }
        )
    if name == "search_srd_action":
        rows.append(
            {
                "condition": "target_state == null or finding_target == null or "
                "_target_state_id == _finding_target_id",
                "error": "Search's Hidden target and finding target must be the same creature.",
            }
        )
    return rows


def _finding_prelude(name):
    effects = [
        _set("_rider_native_finding_id", None),
        _when(
            "finding_target != null",
            [_set("_rider_native_finding_id", "_finding_target_id")],
        ),
    ]
    if name == "search_srd_action":
        effects.append(
            _when(
                "target_state != null",
                [_set("_rider_native_finding_id", "_target_state_id")],
            )
        )
    return effects


def _roll_call(tool):
    return _one(
        [effect for effect in tool["effects"] if effect.get("call") == "d20_test"],
        "Rider check forwarding requires the native owner's one original D20 call.",
    )


def _remove_exact(rows, additions):
    for addition in additions:
        matches = [row for row in rows if row == addition]
        if len(matches) > 1:
            raise ValueError("Duplicated rider D20 additions are not an admitted parent.")
        if matches:
            rows.remove(matches[0])


def restore_tools(tools):
    """Remove only this binder's exact additions before native family regeneration."""
    changed = {name: deepcopy(tools[name]) for name in OWNERS}
    d20 = changed["d20_test"]
    _remove_exact(_check_group(d20), _check_additions())
    attack = changed["resolve_srd_attack_conditions"]
    _remove_exact(attack["effects"], _attack_additions())
    for name in OWNERS[:3]:
        tool = changed[name]
        parameters = [p for p in tool["parameters"] if p.get("name") == "finding_target"]
        if parameters and parameters != [FINDING_PARAMETER]:
            raise ValueError("The rider finding-target declaration changed outside its owner.")
        tool["parameters"] = [p for p in tool["parameters"] if p.get("name") != "finding_target"]
        _remove_exact(tool.setdefault("pre_conditions", []), _finding_preconditions(name))
        if name != "d20_test":
            _remove_exact(tool["effects"], _finding_prelude(name))
            call = _roll_call(tool)
            if "finding_target" in call["params"]:
                if call["params"]["finding_target"] != "{_rider_native_finding_id}":
                    raise ValueError(
                        "The native finding-target forwarding changed outside its owner."
                    )
                del call["params"]["finding_target"]
                for field in CHECK_EXPORTS:
                    if call.get("export_context", {}).get(field) != field:
                        raise ValueError("The native rider flag export changed outside its owner.")
                    del call["export_context"][field]
    for name, helper in (
        ("d20_test", "check_srd_attack_rider_d20"),
        ("resolve_srd_attack_conditions", "check_srd_shining_attack_target"),
    ):
        if any(
            effect.get("call") == helper
            for rows in _walk_lists(changed[name]["effects"])
            for effect in rows
        ):
            raise ValueError("Unknown rider D20 source hook; preserve it for owner review.")
    for tool in changed.values():
        if "_rider_native_" in str(tool):
            raise ValueError("Unknown rider native context; preserve it for owner review.")
    return changed


def bind_tools(tools):
    """Bind native checks and shared attacks without adding dice, actions or save effects."""
    changed = restore_tools(tools)
    for name in OWNERS[:3]:
        tool = changed[name]
        tool["parameters"].append(deepcopy(FINDING_PARAMETER))
        tool["pre_conditions"].extend(_finding_preconditions(name))
    _check_group(changed["d20_test"]).extend(_check_additions())
    changed["resolve_srd_attack_conditions"]["effects"].extend(_attack_additions())
    for name in ("ability_check", "search_srd_action"):
        tool = changed[name]
        call = _roll_call(tool)
        index = tool["effects"].index(call)
        tool["effects"][index:index] = _finding_prelude(name)
        call["params"]["finding_target"] = "{_rider_native_finding_id}"
        call.setdefault("export_context", {}).update({field: field for field in CHECK_EXPORTS})
    return changed


def _emit(text, tools):
    fragments = _ToolFragments(text, any_suffix=True)
    for name, tool in tools.items():
        rendered = yaml.dump({name: tool}, Dumper=_RuntimeDumper, sort_keys=False, width=100)
        rendered = "".join(
            "  " + line if line.strip() else line for line in rendered.splitlines(True)
        )
        prior = fragments.prior(name)
        anchor = re.search(r"      result_card: &(\w+)", prior)
        if anchor:
            rendered = rendered.replace(
                "      result_card:\n", f"      result_card: &{anchor[1]}\n", 1
            )
        fragments.replace(name, rendered)
    return fragments.render()


def bind_runtime_consumers(text):
    """Run after final native D20 normalization and before Reaction owner capture."""
    tools = _load_runtime(text)["tools"]
    for helper in ("check_srd_attack_rider_d20", "check_srd_shining_attack_target"):
        if helper not in tools:
            raise ValueError("Emit the actual rider source helpers before binding native rolls.")
    return _emit(text, bind_tools(tools))


def restore_runtime(text):
    return _emit(text, restore_tools(_load_runtime(text)["tools"]))
