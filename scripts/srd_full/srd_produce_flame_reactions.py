"""Retain Produce Flame's real later attack while its target can answer with Shield."""

from copy import deepcopy

if __package__:
    from .srd_produce_flame import SOURCE_EXPORTS
    from .srd_reaction_spells import _allocate, _capture_names, _copy_value, _save_parameters
    from .srd_scene_relations import _tool_scene
    from .srd_timed_actor_defenses import (
        _call,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from .srd_timed_senses import _replace_tools
else:
    from srd_produce_flame import SOURCE_EXPORTS
    from srd_reaction_spells import _allocate, _capture_names, _copy_value, _save_parameters
    from srd_scene_relations import _tool_scene
    from srd_timed_actor_defenses import (
        _call,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from srd_timed_senses import _replace_tools


PUBLIC_TOOLS = {"begin_srd_produce_flame_attack"}
HURL_NODE = "hurl_srd_produce_flame_node"
COMPLETE_NODE = "complete_srd_produce_flame_attack"


def _lists(effects):
    """Find actual effect branches without treating saved context payloads as executable."""
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if isinstance(effect.get(key), list):
                yield from _lists(effect[key])
        if "for_each" in effect:
            yield from _lists(effect["for_each"]["do"])


def _card_rows(rows):
    """Project the original public card's scalar rows into the existing pending card owner."""
    result = []
    for row in rows:
        path = row.get("value", {}).get("path", "")
        if not path.startswith("context._"):
            raise ValueError("Produce Flame continuation needs its actual private result card.")
        result.append({"label": repr(row["label"]), "value": path.removeprefix("context.")})
    return result


def _pending_card(public):
    card = public["metadata"]["result_card"]
    return [
        _set("_spell_card_summary", _card_rows(card.get("summary", []))),
        _set("_spell_detail_rows", _card_rows(card.get("details", []))),
    ]


def _tools(runtime):
    """Defer the settled hit suffix, then bind its continuation before shared cleanup."""
    tools = runtime["tools"]
    required = {
        "hurl_srd_produce_flame",
        HURL_NODE,
        "complete_srd_pending_action",
        "resolve_srd_effective_armor_class",
    }
    missing = required - tools.keys()
    if missing:
        raise ValueError("Produce Flame's saved attack needs settled owners: " + ", ".join(missing))
    original = tools[HURL_NODE]
    if any(p["name"] == "_produce_defer_hit" for p in original["parameters"]):
        if (
            tools.keys() >= PUBLIC_TOOLS
            and COMPLETE_NODE in tools
            and "_pending_action.owner_kind == 'produce_flame'"
            in str(tools["complete_srd_pending_action"]["effects"])
        ):
            return {}
        raise ValueError("Regenerate the settled hurl owner before rebinding its saved hit.")
    if "resolve_srd_mirror_image_hit" not in str(original["effects"]):
        raise ValueError("Produce Flame's final Mirror Image hit owner must bind first.")
    public = deepcopy(tools["hurl_srd_produce_flame"])
    owner = deepcopy(original)
    owner["parameters"].append(
        _parameter(
            "_produce_defer_hit", "boolean", required=False, default=False, model_hidden=True
        )
    )
    branches = [
        effect
        for effects in _lists(owner["effects"])
        for effect in effects
        if effect.get("condition") == "target != null"
        and any(e.get("call") == "d20_test" for e in effect.get("on_true", []))
    ]
    if len(branches) != 1:
        raise ValueError("Produce Flame needs its single actual creature attack branch.")
    branch = branches[0]
    split = next(
        i
        for i, e in enumerate(branch["on_true"])
        if e.get("set", {}).get("field") == "_produce_hp_before"
    )
    prefix = deepcopy(branch["on_true"][:split])
    suffix = deepcopy(branch["on_true"][split:])
    tail_index = owner["effects"].index(branch) + 1
    tail = deepcopy(owner["effects"][tail_index:])
    names = sorted(
        set(
            _capture_names(
                owner["effects"][: tail_index - 1] + prefix,
                suffix + tail,
                public["metadata"],
            )
        )
        | set(SOURCE_EXPORTS)
        | {
            "_produce_attack",
            "_produce_attack_total",
            "_produce_attack_kind",
            "_produce_cover_bonus",
            "_condition_critical_hit",
        }
    )
    saved = _allocate("produce_flame", "_caster_state_id", "_target_id") + [
        _set("state.pending_srd_actions.{_pending_action_id}.kind", "'spell_attack'"),
        _set("state.pending_srd_actions.{_pending_action_id}.stage", "'spell_attack_reaction'"),
        _set("state.pending_srd_actions.{_pending_action_id}.spell", "'produce_flame'"),
        _set(
            "state.pending_srd_actions.{_pending_action_id}.attack_outcome", "_produce_attack_kind"
        ),
        _set("state.pending_srd_actions.{_pending_action_id}.attack_roll", "_produce_attack"),
        _set(
            "state.pending_srd_actions.{_pending_action_id}.attack_total", "_produce_attack_total"
        ),
        *_save_parameters(original["parameters"]),
    ]
    for name in names:
        saved += _copy_value("state.pending_srd_actions.{_pending_action_id}.context." + name, name)
    saved += [
        _set("_produce_pending_deferred", True),
        _set("_produce_hp_before", "target.hp"),
        _set("_produce_hp_after", "target.hp"),
        _set("_produce_temp_before", "target.temp_hp"),
        _set("_produce_temp_after", "target.temp_hp"),
        _set("_produce_damage", None),
        _set("_produce_damage_rolled", 0),
        _set("_produce_damage_applied", 0),
        _set("_produce_target", "target.name"),
        _when("target.name == null", [_set("_produce_target", "_target_id")]),
    ]
    branch["on_true"] = prefix + [
        _when("_produce_defer_hit == true and _produce_attack_kind != 'miss'", saved),
        _when("_produce_pending_deferred != true", suffix),
    ]
    owner["effects"].insert(0, _set("_pending_action_id", None))
    owner["effects"].insert(1, _set("_produce_pending_deferred", False))
    owner["effects"] = _tool_scene(
        owner["description"], deepcopy(owner["parameters"]), owner["effects"]
    )["effects"]
    for effects in _lists(public["effects"]):
        for effect in effects:
            if effect.get("call") == HURL_NODE:
                effect["params"]["_produce_defer_hit"] = True
                effect["export_context"] = "*"
    public["description"] = "Hurl the retained flame and allow Shield before a real creature hit."
    public["metadata"]["result_card"].setdefault("details", []).append(
        {
            "label": "Pending defensive Reaction",
            "value": {"path": "context._pending_action_id"},
            "optional": True,
        }
    )
    complete_effects = [
        _set("_produce_event", "state.pending_srd_actions[pending_action_id]"),
        _require(
            "_produce_event.kind == 'spell_attack' and "
            "_produce_event.owner_kind == 'produce_flame' and "
            "_produce_event.stage == 'spell_attack_reaction' and "
            "_produce_event.status == 'open' and "
            "_produce_event.actor_state_id == _caster_state_id and "
            "_produce_event.target_state_id == _target_id",
            "This retained flame hit needs its original saved action.",
        ),
    ]
    for name in names:
        complete_effects += _copy_value(name, "_produce_event.context." + name)
    complete_effects += [
        _call(
            "resolve_srd_effective_armor_class",
            {
                "target_state": "{_target_id}",
                "base_armor_class": "{target.armor_class}",
                "lasting_state": "{_lasting_state_id}",
            },
            ["_effective_armor_class"],
        ),
        _set("_produce_armor_class", "_effective_armor_class + _produce_cover_bonus"),
        _set("_produce_attack_kind", "'miss'"),
        _when("_produce_attack.total == 20", [_set("_produce_attack_kind", "'critical_hit'")]),
        _when(
            "_produce_attack.total != 1 and _produce_attack.total != 20 and "
            "_produce_attack_total >= _produce_armor_class",
            [_set("_produce_attack_kind", "'hit'")],
        ),
        _when(
            "_produce_attack_kind == 'hit' and _condition_critical_hit == true",
            [_set("_produce_attack_kind", "'critical_hit'")],
        ),
        *suffix,
        *tail,
        *_pending_card(tools["hurl_srd_produce_flame"]),
        _set("state.pending_srd_actions.{pending_action_id}.stage", "'spell_attack_resolved'"),
    ]
    complete = deepcopy(original)
    complete["parameters"].append(_parameter("pending_action_id"))
    complete["pre_conditions"] = []
    complete["effects"] = _tool_scene(
        "Complete an accepted Produce Flame hit without another roll or action cost.",
        deepcopy(complete["parameters"]),
        complete_effects,
    )["effects"]
    generic = deepcopy(tools["complete_srd_pending_action"])
    found = 0
    for effects in _lists(generic["effects"]):
        for index, effect in enumerate(list(effects)):
            if effect.get("condition") != (
                "_pending_action.kind == 'spell_attack' and _pending_action.status == 'open'"
            ):
                continue
            if not any(
                e.get("call") == "complete_srd_spell_attack_consequences"
                for e in effect.get("on_true", [])
            ):
                continue
            effect["condition"] += " and _pending_action.owner_kind != 'produce_flame'"
            params = {
                p["name"]: "{_pending_action.parameters." + p["name"] + "}"
                for p in original["parameters"]
                if p["name"] != "lasting_state"
            }
            params["pending_action_id"] = "{pending_action_id}"
            call = _call(COMPLETE_NODE, params)
            call["export_context"] = "*"
            effects.insert(
                index,
                _when(
                    "_pending_action.kind == 'spell_attack' and "
                    "_pending_action.status == 'open' and "
                    "_pending_action.owner_kind == 'produce_flame'",
                    [call],
                ),
            )
            found += 1
    if found != 1:
        raise ValueError("Produce Flame needs the exact generic saved spell-hit dispatch.")
    return {
        "begin_srd_produce_flame_attack": public,
        HURL_NODE: owner,
        COMPLETE_NODE: complete,
        "complete_srd_pending_action": generic,
    }


def bind_runtime_consumers(text):
    """Bind after final Reaction and Magic Zone consumers, leaving direct hurl unchanged."""
    changed = _tools(_load_runtime(text))
    return _replace_tools(text, changed) if changed else text
