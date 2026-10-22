"""Keep Vampiric Touch's rolled hit and exact cast tail through defensive Reactions."""

import re
from copy import deepcopy

if __package__:
    from .srd_produce_flame_reactions import _lists, _pending_card
    from .srd_reaction_spells import _allocate, _copy_value, _save_parameters
    from .srd_retained_spell_attacks import SOURCE_EXPORTS
    from .srd_timed_actor_defenses import _call, _load_runtime, _parameter, _require, _set, _when
    from .srd_timed_senses import _replace_tools
else:
    from srd_produce_flame_reactions import _lists, _pending_card
    from srd_reaction_spells import _allocate, _copy_value, _save_parameters
    from srd_retained_spell_attacks import SOURCE_EXPORTS
    from srd_timed_actor_defenses import _call, _load_runtime, _parameter, _require, _set, _when
    from srd_timed_senses import _replace_tools


ATTACK = "resolve_srd_vampiric_touch_attack"
COMPLETE = "complete_srd_vampiric_touch_attack"
CAST_TAIL = "finish_srd_retained_cast_tail"
CONTEXT = "_retained_cast_tail_context"
CHOICES = "_retained_cast_tail_choices"
ROW = "_retained_cast_tail_row"
DEFERRED = "_retained_pending_deferred"
INITIAL = "_retained_initial_attack"
DEFER = "_retained_defer_hit"
MARKER = "retained_spell_reactions_v1"


def _references(value):
    """Find private expression roots, excluding attribute names and literal destination fields."""
    if isinstance(value, str):
        return set(re.findall(r"(?<![\w.])_[A-Za-z][A-Za-z0-9_]*", value))
    if isinstance(value, dict):
        return set().union(*(_references(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_references(v) for v in value)) if value else set()
    return set()


def _private_inputs(effects, assigned=()):
    """Conservatively capture values read before an unconditional local assignment."""
    defined, needed = set(assigned), set()
    for effect in effects:
        if "set" in effect:
            body = effect["set"]
            needed |= _references(body["value"]) - defined
            field = body["field"]
            needed |= _references(re.sub(r"^_[A-Za-z][A-Za-z0-9_]*$", "", field)) - defined
            if re.fullmatch(r"_[A-Za-z][A-Za-z0-9_]*", field):
                defined.add(field)
        elif "condition" in effect:
            needed |= _references(effect["condition"]) - defined
            needed |= _private_inputs(effect.get("on_true", []), defined)
            needed |= _private_inputs(effect.get("on_false", []), defined)
        elif "for_each" in effect:
            body = effect["for_each"]
            needed |= _references(body["collection"]) - defined
            needed |= _private_inputs(body["do"], defined | {body["as"]})
        else:
            needed |= (
                _references({k: v for k, v in effect.items() if k != "export_context"}) - defined
            )
            if isinstance(effect.get("export_context"), dict):
                defined |= set(effect["export_context"])
            if isinstance(effect.get("store_as"), str):
                defined.add(effect["store_as"])
    return needed


def _rows(names):
    return [{"key": repr(n), "missing": n + " == null", "value": n} for n in sorted(names)]


def _restore_rows(collection):
    return [
        {
            "for_each": {
                "collection": collection,
                "as": ROW,
                "do": [
                    {
                        "condition": ROW + ".missing == true",
                        "on_true": [_set("{" + ROW + ".key}", None)],
                        "on_false": [_set("{" + ROW + ".key}", ROW + ".value")],
                    }
                ],
            }
        }
    ]


def _forward(parameter):
    name = parameter["name"]
    return (
        "{_" + name + "_id}"
        if parameter.get("resolve") in {"entity", "participant"}
        else "{" + name + "}"
    )


def _export_call(name, params):
    return {"call": name, "params": params, "export_context": "*"}


def _tail_call(parameters, names, choices):
    return [
        _set(CONTEXT, _rows(names)),
        _set(CHOICES, [{name: name for name in choices}]),
        _export_call(
            CAST_TAIL,
            {
                **{p["name"]: _forward(p) for p in parameters},
                CONTEXT: "{" + CONTEXT + "}",
                CHOICES: "{" + CHOICES + "}",
            },
        ),
    ]


def _choice_parameters(parameters):
    """Capsule only non-null choices validated by the unchanged outer public cast."""
    return [
        p
        for p in parameters
        if not p.get("resolve")
        and not p.get("from_state")
        and not p.get("model_hidden")
        and p.get("type") in {"string", "enum", "integer", "number", "boolean", "array", "object"}
        and (p.get("required", True) or p.get("default") is not None)
    ]


def _rewrite_choices(value, choices, *, inverse=False, templates=False):
    """Qualify actual choice roots, preserving quoted literals and field attributes."""
    if isinstance(value, str):

        def template(match):
            path = match.group(1)
            if inverse and path.startswith(CHOICES + "."):
                return "{" + path.removeprefix(CHOICES + ".") + "}"
            root = path.partition(".")[0]
            if not inverse and root in choices:
                return "{" + CHOICES + "." + path + "}"
            return match.group(0)

        if templates:
            return re.sub(r"\{([A-Za-z_][A-Za-z0-9_.]*)\}", template, value)
        if inverse:
            return re.sub(
                r"(?<![\w.])" + re.escape(CHOICES) + r"\.([A-Za-z_][A-Za-z0-9_]*)",
                lambda m: m[1] if m[1] in choices else m[0],
                value,
            )
        tokens = r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|(?<![\w.])[A-Za-z_][A-Za-z0-9_]*"

        def expression(match):
            token = match.group(0)
            if token[:1] in {"'", '"'}:
                return re.sub(r"\{([A-Za-z_][A-Za-z0-9_.]*)\}", template, token)
            return CHOICES + "." + token if token in choices else token

        return re.sub(tokens, expression, value)
    if isinstance(value, list):
        return [_rewrite_choices(v, choices, inverse=inverse, templates=templates) for v in value]
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if key in {"call", "as", "store_as", "export_context", "metadata"}:
                result[key] = deepcopy(child)
            elif key == "params":
                result[key] = _rewrite_choices(child, choices, inverse=inverse, templates=True)
            else:
                result[key] = _rewrite_choices(
                    child,
                    choices,
                    inverse=inverse,
                    templates=templates or key in {"field", "error"},
                )
        return result
    return value


def _internal(original, parameters, effects):
    tool = deepcopy(original)
    tool.update(
        parameters=parameters,
        pre_conditions=[],
        effects=effects,
        metadata={
            "category": "internal",
            "visible_to_player": False,
            "costs_action": False,
            MARKER: True,
        },
    )
    return tool


def _bind_state(value):
    if isinstance(value, str):
        return re.sub(r"\bstate\.", "lasting_state.", value)
    if isinstance(value, list):
        return [_bind_state(v) for v in value]
    if isinstance(value, dict):
        return {
            k: ("state.spatial_scene" if k == "scene" else _bind_state(v)) for k, v in value.items()
        }
    return value


def _remember_exports(tool, original):
    tool.setdefault("metadata", {})[MARKER + "_exports"] = {
        e["call"]: deepcopy(e.get("export_context"))
        for ss in _lists(original["effects"])
        for e in ss
        if e.get("call")
        in {
            ATTACK,
            "cast_srd_vampiric_touch_attack",
            "cast_srd_vampiric_touch",
            "complete_srd_spell_attack_consequences",
        }
    }


def _tools(runtime):
    """Bind only the settled retained owner and its real final paid-cast continuation."""
    tools = runtime["tools"]
    if CAST_TAIL in tools:
        if tools[CAST_TAIL].get("metadata", {}).get(MARKER) and COMPLETE in tools:
            return {}
        raise ValueError("Retained defensive windows need their complete final composition.")
    original = tools[ATTACK]
    if DEFER in str(original):
        raise ValueError("Restore the settled retained owner before rebinding.")
    owner = deepcopy(original)
    split = next(
        i
        for i, e in enumerate(owner["effects"])
        if e.get("set", {}).get("field") == "_retained_hp_before"
    )
    prefix, suffix = deepcopy(owner["effects"][:split]), deepcopy(owner["effects"][split:])
    if sum(e.get("call") == "d20_test" for e in prefix) != 1:
        raise ValueError("Vampiric Touch needs one settled actual attack roll.")
    tail = []
    names = sorted(
        _private_inputs(suffix + tail)
        | set(SOURCE_EXPORTS)
        | {
            "_retained_attack",
            "_retained_attack_kind",
            "_retained_attack_total",
            "_retained_cover_bonus",
            "_condition_critical_hit",
        }
    )
    save_params = _bind_state(deepcopy(_save_parameters(original["parameters"])))
    for ss in _lists(save_params):
        for e in ss:
            if "set" in e:
                e["set"]["field"] = e["set"]["field"].replace(
                    ".parameters.", ".retained_attack_parameters."
                )
    saved = [
        {
            "condition": INITIAL + " == true",
            "on_true": [
                _set("_pending_action_id", "caster_state.pending_srd_completion_id"),
                _require(
                    "lasting_state.pending_srd_actions[_pending_action_id].kind == "
                    "'spell_cast' and "
                    "lasting_state.pending_srd_actions[_pending_action_id].actor_state_id == "
                    "_caster_state_id",
                    "This initial retained hit needs its original paid casting window.",
                ),
            ],
            "on_false": _bind_state(
                _allocate("retained_spell_attack", "_caster_state_id", "_target_id")
            ),
        },
        _set("lasting_state.pending_srd_actions.{_pending_action_id}.kind", "'spell_attack'"),
        _set(
            "lasting_state.pending_srd_actions.{_pending_action_id}.stage",
            "'spell_attack_reaction'",
        ),
        _set(
            "lasting_state.pending_srd_actions.{_pending_action_id}.target_state_id", "_target_id"
        ),
        _set("lasting_state.pending_srd_actions.{_pending_action_id}.spell", "'vampiric_touch'"),
        _set(
            "lasting_state.pending_srd_actions.{_pending_action_id}.retained_spell",
            "'vampiric_touch'",
        ),
        _set("lasting_state.pending_srd_actions.{_pending_action_id}.retained_initial", INITIAL),
        _set(
            "lasting_state.pending_srd_actions.{_pending_action_id}.attack_outcome",
            "_retained_attack_kind",
        ),
        _set(
            "lasting_state.pending_srd_actions.{_pending_action_id}.attack_roll", "_retained_attack"
        ),
        _set(
            "lasting_state.pending_srd_actions.{_pending_action_id}.attack_total",
            "_retained_attack_total",
        ),
        _set(
            "lasting_state.pending_srd_actions.{_pending_action_id}.retained_attack_parameters", {}
        ),
        _set("lasting_state.pending_srd_actions.{_pending_action_id}.retained_attack_context", {}),
        *save_params,
    ]
    for name in names:
        saved += _copy_value(
            "lasting_state.pending_srd_actions.{_pending_action_id}.retained_attack_context."
            + name,
            name,
        )
    saved += [
        _set(DEFERRED, True),
        _set("_retained_hp_before", "target.hp"),
        _set("_retained_hp_after", "target.hp"),
        _set("_retained_temp_before", "target.temp_hp"),
        _set("_retained_temp_after", "target.temp_hp"),
        _set("_retained_damage", None),
        _set("_retained_damage_rolled", 0),
        _set("_retained_damage_applied", 0),
        _set("_retained_necrotic_damage", 0),
        _set("_retained_healing_applied", 0),
        _set("_retained_caster_hp_after", "caster_state.hp"),
        _set("_retained_attack_dice", "_retained_attack.results"),
        _set("_retained_damage_dice", None),
        _set("_retained_target", "target.name"),
    ]
    owner["effects"] = [
        _set("_pending_action_id", None),
        _set(DEFERRED, False),
        *prefix,
        _when(
            DEFER
            + " == true and _retained_attack_kind != 'miss' and ("
            + INITIAL
            + " != true or caster_state.pending_srd_completion_id != null)",
            saved,
        ),
        _when(DEFERRED + " != true", suffix),
    ]
    owner["parameters"] += [
        _parameter(DEFER, "boolean", required=False, default=False, model_hidden=True),
        _parameter(INITIAL, "boolean", required=False, default=False, model_hidden=True),
    ]
    changed = {ATTACK: owner}
    for name, initial in (
        ("cast_srd_vampiric_touch_attack", True),
        ("use_srd_vampiric_touch", False),
    ):
        tool = deepcopy(tools[name])
        for ss in _lists(tool["effects"]):
            for e in ss:
                if e.get("call") == ATTACK:
                    e["params"].update({DEFER: True, INITIAL: initial})
                    e["export_context"] = "*"
        changed[name] = tool
    cast_source = deepcopy(tools["cast_srd_vampiric_touch"])
    source_split = next(
        i
        for i, e in enumerate(cast_source["effects"])
        if e.get("set", {}).get("field") == "_spell_summary_rows"
    )
    formatter = deepcopy(cast_source["effects"][source_split:])
    cast_source["effects"] = cast_source["effects"][:source_split] + [
        _when(DEFERRED + " != true", formatter)
    ]
    for ss in _lists(cast_source["effects"]):
        for e in ss:
            if e.get("call") == "cast_srd_vampiric_touch_attack":
                e["export_context"] = "*"
    changed["cast_srd_vampiric_touch"] = cast_source
    cast = deepcopy(tools["cast_spell"])
    for ss in _lists(cast["effects"]):
        for e in ss:
            if e.get("call") == "cast_srd_vampiric_touch":
                e["export_context"] = "*"
    cast_split = next(
        i
        for i, e in enumerate(cast["effects"])
        if e.get("set", {}).get("field") == "_card_slots_after"
    )
    cast_tail = deepcopy(cast["effects"][cast_split:])
    cast_names = _private_inputs(cast_tail)
    text_refs = str(cast_tail)
    parameters = [
        deepcopy(p)
        for p in cast["parameters"]
        if re.search(r"(?<![\w.])" + re.escape(p["name"]) + r"\b", text_refs)
        or p.get("resolve") in {"entity", "participant"}
    ]
    choice_names = [p["name"] for p in _choice_parameters(parameters)]
    parameters = [p for p in parameters if p["name"] not in choice_names]
    qualified_tail = _rewrite_choices(cast_tail, set(choice_names))
    if _rewrite_choices(qualified_tail, set(choice_names), inverse=True) != cast_tail:
        raise ValueError("Retained choice factoring must reconstruct the exact original cast tail.")
    changed[CAST_TAIL] = _internal(
        cast,
        parameters
        + [
            _parameter(CONTEXT, "array", items={"type": "object"}, model_hidden=True),
            _parameter(
                CHOICES,
                "array",
                items={"type": "object"},
                min_items=1,
                max_items=1,
                model_hidden=True,
            ),
        ],
        _restore_rows(CONTEXT) + [_set(CHOICES, CHOICES + "[0]")] + qualified_tail,
    )
    changed[CAST_TAIL]["metadata"][MARKER + "_choices"] = choice_names
    deferred_cast = [
        _set(
            "lasting_state.pending_srd_actions.{_pending_action_id}.retained_cast_context",
            _rows(cast_names),
        ),
        _set(
            "_spell_card_summary", [{"label": "'Attack'", "value": "'Awaiting defensive Reaction'"}]
        ),
        _set(
            "_spell_detail_rows", [{"label": "'Saved attack'", "value": "_retained_attack_total"}]
        ),
    ]
    cast["effects"] = [
        _set(DEFERRED, False),
        *cast["effects"][:cast_split],
        {
            "condition": DEFERRED + " == true",
            "on_true": deferred_cast,
            "on_false": _tail_call(parameters, cast_names, choice_names),
        },
    ]
    changed["cast_spell"] = cast
    outside = deepcopy(tools["cast_spell_outside_combat"])
    if outside["effects"] == tools["cast_spell"]["effects"]:
        outside["effects"] = deepcopy(cast["effects"])
        changed["cast_spell_outside_combat"] = outside
    elif not any(e.get("call") == "cast_spell" for e in outside["effects"]):
        raise ValueError(
            "Retained cast factoring needs the identical exploration cast or its final delegate."
        )
    complete_effects = [
        _set("_retained_event", "lasting_state.pending_srd_actions[pending_action_id]"),
        _require(
            "_retained_event.kind == 'spell_attack' and "
            "_retained_event.retained_spell == 'vampiric_touch' and "
            "_retained_event.stage == 'spell_attack_reaction' and "
            "_retained_event.status == 'open' and "
            "_retained_event.actor_state_id == _caster_state_id and "
            "_retained_event.target_state_id == _target_id",
            "Complete Vampiric Touch through its original saved hit.",
        ),
    ]
    for name in names:
        complete_effects += _copy_value(name, "_retained_event.retained_attack_context." + name)
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
        _set("_retained_armor_class", "_effective_armor_class + _retained_cover_bonus"),
        _set("_retained_attack_kind", "'miss'"),
        _when("_retained_attack.total == 20", [_set("_retained_attack_kind", "'critical_hit'")]),
        _when(
            "_retained_attack.total != 1 and _retained_attack.total != 20 and "
            "_retained_attack_total >= _retained_armor_class",
            [_set("_retained_attack_kind", "'hit'")],
        ),
        _when(
            "_retained_attack_kind == 'hit' and _condition_critical_hit == true",
            [_set("_retained_attack_kind", "'critical_hit'")],
        ),
        *suffix,
        *tail,
        _set(DEFERRED, False),
        _when(
            "_retained_event.retained_initial != true",
            _pending_card(tools["use_srd_vampiric_touch"]),
        ),
        _set(
            "lasting_state.pending_srd_actions.{pending_action_id}.stage", "'spell_attack_resolved'"
        ),
    ]
    changed[COMPLETE] = _internal(
        original,
        deepcopy(original["parameters"]) + [_parameter("pending_action_id")],
        complete_effects,
    )
    complete_params = {
        p["name"]: "{_retained_event.retained_attack_parameters." + p["name"] + "}"
        for p in original["parameters"]
        if p["name"] != "lasting_state"
    }
    complete_params["pending_action_id"] = "{pending_action_id}"
    consequence = deepcopy(tools["complete_srd_spell_attack_consequences"])
    initial_effects = [
        _set("_retained_event", "lasting_state.pending_srd_actions[pending_action_id]"),
        *_restore_rows("_retained_event.retained_cast_context"),
        _export_call(COMPLETE, complete_params),
        *formatter,
        *_tail_call(parameters, cast_names, choice_names),
    ]
    consequence["effects"] = [
        {
            "condition": (
                "lasting_state.pending_srd_actions[pending_action_id].retained_spell == "
                "'vampiric_touch'"
            ),
            "on_true": initial_effects,
            "on_false": consequence["effects"],
        }
    ]
    changed["complete_srd_spell_attack_consequences"] = consequence
    generic = deepcopy(tools["complete_srd_pending_action"])
    found = 0
    for ss in _lists(generic["effects"]):
        for i, e in enumerate(list(ss)):
            if not any(
                x.get("call") == "complete_srd_spell_attack_consequences"
                for x in e.get("on_true", [])
            ):
                continue
            if "_pending_action.kind == 'spell_attack'" not in e.get("condition", ""):
                continue
            e["condition"] += " and _pending_action.owner_kind != 'retained_spell_attack'"
            for child in e["on_true"]:
                if child.get("call") == "complete_srd_spell_attack_consequences":
                    child["export_context"] = "*"
            ss.insert(
                i,
                _when(
                    "_pending_action.kind == 'spell_attack' and "
                    "_pending_action.status == 'open' and "
                    "_pending_action.owner_kind == 'retained_spell_attack'",
                    [
                        _set("_retained_event", "_pending_action"),
                        _export_call(COMPLETE, complete_params),
                    ],
                ),
            )
            found += 1
    if found != 1:
        raise ValueError("Retained attacks need the single generic saved spell-hit dispatcher.")
    changed["complete_srd_pending_action"] = generic
    for name, tool in changed.items():
        if name in tools:
            _remember_exports(tool, tools[name])
    return changed


def bind_runtime(text):
    """Run after the exact final cast and Reaction consumers, before private trims/delegation."""
    changed = _tools(_load_runtime(text))
    if not changed:
        return text
    for name in changed:
        text = re.sub(rf"(?m)^(  {re.escape(name)}:[^\n]*)\r\n", r"\1\n", text, count=1)
    return _replace_tools(text, changed)


def restore_runtime(text):
    """Reconstruct only this final binder's exact owners before family/Reaction regeneration."""
    tools = _load_runtime(text)["tools"]
    if CAST_TAIL not in tools:
        return text
    helper = tools[CAST_TAIL]
    if not helper.get("metadata", {}).get(MARKER):
        raise ValueError("The retained cast tail is not this binder's final implementation.")
    tail_start = 2 if helper["effects"][1] == _set(CHOICES, CHOICES + "[0]") else 1
    tail = _rewrite_choices(
        helper["effects"][tail_start:],
        set(helper["metadata"].get(MARKER + "_choices", [])),
        inverse=True,
    )
    changed = {}
    for name in ("cast_spell", "cast_spell_outside_combat"):
        tool = deepcopy(tools[name])
        if not any(e.get("condition") == DEFERRED + " == true" for e in tool["effects"]):
            continue
        if tool["effects"][0] != _set(DEFERRED, False):
            raise ValueError("The retained cast prefix changed before inverse restoration.")
        final = tool["effects"][-1]
        if final.get("condition") != DEFERRED + " == true" or not any(
            e.get("call") == CAST_TAIL for e in final.get("on_false", [])
        ):
            raise ValueError("The retained cast tail changed before inverse restoration.")
        tool["effects"] = tool["effects"][1:-1] + deepcopy(tail)
        changed[name] = tool
    attack = deepcopy(tools[ATTACK])
    if attack["effects"][:2] != [_set("_pending_action_id", None), _set(DEFERRED, False)]:
        raise ValueError("Regenerate only after restoring the retained attack window.")
    final = attack["effects"][-1]
    if final.get("condition") != DEFERRED + " != true":
        raise ValueError("The retained received/healing tail changed before inverse restoration.")
    saved = attack["effects"][-2]
    if DEFER not in saved.get("condition", ""):
        raise ValueError("The retained attack split changed before inverse restoration.")
    attack["effects"] = attack["effects"][2:-2] + deepcopy(final["on_true"])
    attack["parameters"] = [p for p in attack["parameters"] if p["name"] not in {DEFER, INITIAL}]
    changed[ATTACK] = attack
    for name in (
        "cast_srd_vampiric_touch_attack",
        "use_srd_vampiric_touch",
        "cast_srd_vampiric_touch",
    ):
        tool = deepcopy(tools[name])
        if name == "cast_srd_vampiric_touch":
            last = tool["effects"][-1]
            if last.get("condition") != DEFERRED + " != true":
                raise ValueError(
                    "The retained result formatter changed before inverse restoration."
                )
            tool["effects"] = tool["effects"][:-1] + last["on_true"]
        for ss in _lists(tool["effects"]):
            for e in ss:
                if e.get("call") == ATTACK:
                    e["params"].pop(DEFER, None)
                    e["params"].pop(INITIAL, None)
        changed[name] = tool
    consequence = deepcopy(tools["complete_srd_spell_attack_consequences"])
    if len(consequence["effects"]) != 1 or "retained_spell" not in consequence["effects"][0].get(
        "condition", ""
    ):
        raise ValueError("The retained initial continuation changed before inverse restoration.")
    consequence["effects"] = consequence["effects"][0]["on_false"]
    changed["complete_srd_spell_attack_consequences"] = consequence
    generic = deepcopy(tools["complete_srd_pending_action"])
    for ss in _lists(generic["effects"]):
        ss[:] = [e for e in ss if not any(x.get("call") == COMPLETE for x in e.get("on_true", []))]
        for e in ss:
            if "condition" in e:
                e["condition"] = e["condition"].removesuffix(
                    " and _pending_action.owner_kind != 'retained_spell_attack'"
                )
    changed["complete_srd_pending_action"] = generic
    for tool in changed.values():
        exports = tool.get("metadata", {}).pop(MARKER + "_exports", {})
        for ss in _lists(tool["effects"]):
            for e in ss:
                if e.get("call") in exports:
                    if exports[e["call"]] is None:
                        e.pop("export_context", None)
                    else:
                        e["export_context"] = exports[e["call"]]
    text = _replace_tools(text, changed)
    for name in (CAST_TAIL, COMPLETE):
        text = re.sub(
            rf"(?ms)^  {re.escape(name)}:[^\n]*\n.*?"
            r"(?=^  [A-Za-z_][^\n]*:\s*|^procedures:|^definition_row_schemas:)",
            "",
            text,
            count=1,
        )
    return text
