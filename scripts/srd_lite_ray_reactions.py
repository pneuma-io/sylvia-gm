"""UNEXECUTED draft: retain ordered Lite Scorching Rays across real hit windows.

The main owner calls bind_ray_reactions on its already qualified runtime mapping,
after bind_attacks and the Shield adapter. This module never loads a YAML graph,
reads an archive, writes candidate source, or allocates a synthetic pending event.
"""

from copy import deepcopy

from .srd_control_behaviors import _lasting, _param
from .srd_reaction_spells import _capture_names, _copy_value
from .srd_timed_actor_defenses import _call, _require, _set, _tool, _when


CURSOR = "lite_scorching_ray_cursor"
CONTINUE = "continue_srd_lite_scorching_rays"
FINISH = "finish_srd_lite_scorching_ray_hit"
ANNOTATE = "annotate_srd_lite_shield_window"
EVENT = "lasting_state.pending_srd_actions.{pending_action_id}"
LOCAL_EVENT = "state.pending_srd_actions.{_lite_ray_pending_id}"
EXPORTS = (
    "_spell_summary_rows", "_spell_detail_rows", "_spell_multi_attack_count",
    "_spell_multi_attack_targets", "_spell_multi_target_numbers", "_spell_multi_attacks",
    "_spell_multi_attack_outcomes", "_spell_multi_attack_kinds", "_spell_multi_damage",
    "_spell_multi_damage_amounts", "_spell_multi_concentration_save_dcs",
    "_spell_multi_concentration_save_outcomes", "_spell_multi_concentration_save_rolls",
    "_spell_hit_window_deferred",
)


def _lists(effects):
    """Visit effect control flow, without visiting stored payload dictionaries."""
    yield effects
    for effect in effects:
        for branch in ("on_true", "on_false"):
            if isinstance(effect.get(branch), list):
                yield from _lists(effect[branch])
        each = effect.get("for_each")
        if isinstance(each, dict):
            yield from _lists(each["do"])


def _one(items, predicate, description):
    found = [item for item in items if predicate(item)]
    if len(found) != 1:
        raise ValueError("Scorching Ray prerequisite changed: " + description)
    return found[0]


def _parameters(runtime, owner):
    """Resolve only this owner's parameter rows; never expand the whole graph."""
    declarations = runtime.get("parameter_declarations", {})
    rows = []
    for row in owner["parameters"]:
        if isinstance(row, str):
            row = declarations[row]
        if not isinstance(row, dict) or "name" not in row:
            raise ValueError("Scorching Ray parameters need admitted declarations")
        rows.append(deepcopy(row))
    return rows


def _save_parameters(parameters, field):
    effects = []
    for parameter in parameters:
        name = parameter["name"]
        if name == "lasting_state":
            continue
        expression = "_" + name + "_id" if parameter.get("resolve") in {
            "entity", "participant"
        } else name
        effects += _copy_value(field + "." + name, expression)
    return effects


def _copy_context(names, destination, source=""):
    effects = []
    for name in names:
        effects += _copy_value(destination + name, source + name)
    return effects


def _append_rows(collection, destination):
    return {"for_each": {"collection": collection, "as": "_lite_ray_row", "do": [
        {"list_push": {"list": destination, "value": "_lite_ray_row"}}
    ]}}


def bind_ray_reactions(runtime):
    """Add one ordered cursor only to staged Scorching Ray; preserve direct calls."""
    tools = runtime["tools"]
    if ANNOTATE not in tools:
        raise ValueError("Bind the actual Lite Shield event reader before Scorching Rays")
    if CONTINUE in tools or FINISH in tools:
        raise ValueError("Scorching Ray cursor must be composed exactly once")
    owner = tools["cast_spell_multi_attack_damage"]
    child = tools["resolve_spell_multi_attack_target"]
    parent_parameters = _parameters(runtime, owner)
    child_parameters = _parameters(runtime, child)

    # Locate the genuine per-ray roll and critical decision, before Mirror Image/damage.
    alive = _one(
        (effect for effects in _lists(child["effects"]) for effect in effects),
        lambda effect: effect.get("condition") == "target.is_alive == true"
        and any(e.get("set", {}).get("field") == "_spell_critical_hit"
                for e in effect.get("on_true", [])),
        "one living-target roll/critical block",
    )
    critical = _one(alive["on_true"],
                    lambda e: e.get("set", {}).get("field") == "_spell_critical_hit",
                    "critical determination")
    split = alive["on_true"].index(critical) + 1
    hit_suffix = deepcopy(alive["on_true"][split:])
    if not any(e.get("call") == "resolve_srd_mirror_image_hit"
               for effects in _lists(hit_suffix) for e in effects):
        raise ValueError("Scorching Ray no longer owns its original Mirror Image suffix")
    outer_index = child["effects"].index(alive)
    card_suffix = deepcopy(child["effects"][outer_index + 1:])
    prefix = deepcopy(child["effects"][:outer_index]) + deepcopy(alive["on_true"][:split])
    names = sorted(set(_capture_names(prefix, hit_suffix + card_suffix, {})) | {
        "_card_d20_roll", "_card_d20_total", "_card_d20_modifier",
        "_spell_attack", "_spell_attack_outcome", "_spell_attack_kind",
        "_spell_critical_hit", "_spell_condition_critical_hit", "_spell_target_armor_class",
        "_spell_damage", "_spell_damage_amount", "_spell_concentration_save_dc",
        "_spell_concentration_save_outcome", "_spell_concentration_save_roll",
        "_mirror_redirected", "_mirror_dice", "_mirror_source_id", "_mirror_remaining",
    })
    armor_start = next(i for i, e in enumerate(alive["on_true"][:split])
                       if e.get("set", {}).get("field") == "_spell_target_armor_class")
    armor_end = next(i for i, e in enumerate(alive["on_true"][:split])
                     if e.get("call") == "consume_srd_next_attack_advantage")
    armor = deepcopy(alive["on_true"][armor_start:armor_end])
    cursor_field = LOCAL_EVENT + "." + CURSOR
    save_hit = [
        _set("_lite_ray_pending_id", "caster_state.pending_srd_completion_id"),
        _require(
            "state.pending_srd_actions[_lite_ray_pending_id].owner_kind == 'spell_cast' and "
            "state.pending_srd_actions[_lite_ray_pending_id].spell == 'scorching_ray' and "
            "state.pending_srd_actions[_lite_ray_pending_id].status == 'open' and "
            "state.pending_srd_actions[_lite_ray_pending_id].actor_state_id == _caster_state_id and "
            "state.pending_srd_actions[_lite_ray_pending_id]." + CURSOR + ".awaiting_hit != true and "
            "state.pending_srd_actions[_lite_ray_pending_id]." + CURSOR + ".targets["
            "state.pending_srd_actions[_lite_ray_pending_id]." + CURSOR + ".index] == _target_id",
            "This ray must belong to its original ordered casting action.",
        ),
        _set(LOCAL_EVENT + ".kind", "'spell_attack'"),
        _set(LOCAL_EVENT + ".stage", "'spell_attack_reaction'"),
        _set(LOCAL_EVENT + ".target_state_id", "_target_id"),
        _set(LOCAL_EVENT + ".attack_outcome", "'hit'"),
        _when("_spell_critical_hit == true", [
            _set(LOCAL_EVENT + ".attack_outcome", "'critical_hit'")
        ]),
        _set(LOCAL_EVENT + ".spell_attack_parameters", {}),
        _set(LOCAL_EVENT + ".spell_attack_context", {}),
        *_save_parameters(child_parameters, LOCAL_EVENT + ".spell_attack_parameters"),
        *_copy_context(names, LOCAL_EVENT + ".spell_attack_context."),
        _set(cursor_field + ".awaiting_hit", True),
        _set("_spell_hit_window_deferred", True),
        _call(ANNOTATE, {"pending_action_id": "{_lite_ray_pending_id}", "lasting_state": "state"}),
        _call("record_srd_spell_target_result", {
            "target": "{_target_id}",
            "brief": "Attack total {_card_d20_total}; awaiting defense",
            "detail": "Saved D20 {_card_d20_roll.results}; attack total {_card_d20_total}. "
                      "Resolve this real ray's Reaction window before its damage or the next ray.",
        }, ("_spell_target_summary", "_spell_target_detail")),
        {"list_push": {"list": "_spell_summary_rows", "value": "_spell_target_summary"}},
        {"list_push": {"list": "_spell_detail_rows", "value": "_spell_target_detail"}},
    ]
    alive["on_true"][split:] = [
        _when("spell == 'scorching_ray' and caster_state.pending_srd_completion_id != null and "
              "_spell_attack_outcome == 'success'", save_hit),
        _when("_spell_hit_window_deferred != true", hit_suffix),
    ]
    child["effects"] = [
        # A dead later ray has no measured armor class; the promised export is honest null.
        _set("_spell_target_armor_class", None), _set("_spell_hit_window_deferred", False),
        *child["effects"][:outer_index + 1],
        _when("_spell_hit_window_deferred != true", card_suffix),
    ]

    # Finish only the saved ray suffix. Re-evaluate AC after Shield using its original D20.
    finish = _tool(
        "Finish the actual retained Scorching Ray hit without another attack roll or casting cost.",
        [*[p for p in child_parameters if p["name"] != "lasting_state"],
         _param("pending_action_id"), _lasting()],
        [
            _set("_lite_ray_event", "lasting_state.pending_srd_actions[pending_action_id]"),
            _require("_lite_ray_event.kind == 'spell_attack' and "
                     "_lite_ray_event.owner_kind == 'spell_cast' and "
                     "_lite_ray_event.spell == 'scorching_ray' and "
                     "_lite_ray_event.stage == 'spell_attack_reaction' and "
                     "_lite_ray_event.status == 'open' and "
                     "_lite_ray_event.actor_state_id == _caster_state_id and "
                     "_lite_ray_event.target_state_id == _target_id and "
                     "_lite_ray_event." + CURSOR + ".targets[_lite_ray_event." + CURSOR +
                     ".index] == _target_id and "
                     "_lite_ray_event." + CURSOR + ".awaiting_hit == true",
                     "Only the original saved ray can finish this hit window."),
            *_copy_context(names, "", "_lite_ray_event.spell_attack_context."),
            _set("_spell_hit_window_deferred", False),
            _set("_spell_attack_outcome", "'target_already_defeated'"),
            _set("_spell_attack_kind", None),
            _when("target.is_alive == true", [
                *armor,
                _set("_spell_attack_outcome", "'failure'"),
                _set("_spell_attack_kind", "'miss'"),
                _when("_card_d20_roll.total == 20", [
                    _set("_spell_attack_outcome", "'success'"),
                    _set("_spell_attack_kind", "'critical_hit'"),
                ]),
                _when("_card_d20_roll.total != 1 and _card_d20_total >= _spell_target_armor_class "
                      "and _spell_attack_kind != 'critical_hit'", [
                    _set("_spell_attack_outcome", "'success'"),
                    _set("_spell_attack_kind", "'hit'"),
                ]),
                _when("_spell_attack_outcome == 'success' and _spell_condition_critical_hit == true", [
                    _set("_spell_attack_kind", "'critical_hit'"),
                ]),
                _set("_spell_critical_hit", "_spell_attack_kind == 'critical_hit'"),
                *hit_suffix,
            ]),
            *card_suffix,
            _set(EVENT + "." + CURSOR + ".awaiting_hit", False),
            _set(EVENT + ".stage", "'scorching_ray_running'"),
        ],
    )
    finish["metadata"] = {"category": "internal", "visible_to_player": False, "costs_action": False}
    tools[FINISH] = finish

    # Retain the existing ordered loop body, Sanctuary losses, aggregations and finalizer.
    loop = _one(owner["effects"],
                lambda e: e.get("for_each", {}).get("collection") == "attack_targets"
                and any(x.get("call") == "resolve_spell_multi_attack_target"
                        for effects in _lists(e["for_each"]["do"]) for x in effects),
                "one ordered target-resolution loop")
    loop_index = owner["effects"].index(loop)
    finalizer = _one(owner["effects"][loop_index + 1:],
                     lambda e: e.get("for_each", {}).get("collection") == "attack_targets"
                     and any(x.get("call") == "finalize_spell_multi_attack_target"
                             for x in e["for_each"]["do"]), "final target cleanup")
    body = deepcopy(loop["for_each"]["do"])
    sanctuary = _one(body, lambda e: e.get("condition") ==
                     "_sanctuary_ray_index not in _sanctuary_lost_rays", "per-ray Sanctuary gate")
    branch = sanctuary["on_true"]
    dispatch = _one(branch, lambda e: e.get("call") == "resolve_spell_multi_attack_target",
                    "real per-ray dispatch")
    dispatch_index = branch.index(dispatch)
    append = deepcopy(branch[dispatch_index + 1:])
    dispatch["export_context"]["_spell_hit_window_deferred"] = "_spell_hit_window_deferred"
    # Preserve honest null/dead/miss rows; only a suspended hit delays append and index advance.
    branch[dispatch_index + 1:] = [
        _when("_spell_hit_window_deferred != true", append),
        _when("_spell_hit_window_deferred == true", [_set("_lite_ray_deferred", True)]),
    ]
    if body[-1].get("set", {}).get("field") != "_sanctuary_ray_index":
        raise ValueError("Scorching Ray lost its terminal ordered-index increment")
    body[-1] = _when("_lite_ray_deferred != true", [
        _set("_sanctuary_ray_index", "_sanctuary_ray_index + 1"),
        _set(EVENT + "." + CURSOR + ".index", "_sanctuary_ray_index"),
    ])
    resume_exports = {source: child_name for source, child_name in dispatch["export_context"].items()}
    resume = _call(FINISH, {
        **{p["name"]: "{_lite_ray_event.spell_attack_parameters." + p["name"] + "}"
           for p in child_parameters if p["name"] != "lasting_state"},
        "pending_action_id": "{pending_action_id}",
        "lasting_state": "{_lasting_state_id}",
    })
    resume["export_context"] = resume_exports
    aggregate_names = [name for name in EXPORTS if name != "_spell_hit_window_deferred"]
    store_aggregate = _copy_context(aggregate_names, EVENT + "." + CURSOR + ".context.")
    continuation_effects = [
        _set("_lite_ray_event", "lasting_state.pending_srd_actions[pending_action_id]"),
        _require("_lite_ray_event.owner_kind == 'spell_cast' and "
                 "_lite_ray_event.spell == 'scorching_ray' and _lite_ray_event.status == 'open' and "
                 "_lite_ray_event.actor_state_id == _caster_state_id and "
                 "_lite_ray_event." + CURSOR + " != null and "
                 "_lite_ray_event." + CURSOR + ".finalized != true",
                 "Continue only this original ordered Scorching Ray casting."),
        *_copy_context(aggregate_names, "", "_lite_ray_event." + CURSOR + ".context."),
        _set("_sanctuary_lost_rays", "_lite_ray_event." + CURSOR + ".sanctuary_lost_rays"),
        _set("_sanctuary_ray_index", "_lite_ray_event." + CURSOR + ".index"),
        _set("_lite_ray_deferred", False),
        _set("_spell_hit_window_deferred", False),
        *_copy_value("_lite_ray_completion_before", "caster_state.pending_srd_completion_id"),
    ]
    continuation_effects += [
        _when("_lite_ray_event." + CURSOR + ".awaiting_hit == true", [
            _set("_multi_ray_target", "_lite_ray_event.target_state_id"),
            resume, *append,
            _set("_sanctuary_ray_index", "_sanctuary_ray_index + 1"),
            _set(EVENT + "." + CURSOR + ".index", "_sanctuary_ray_index"),
        ]),
        _set("caster_state.pending_srd_completion_id", "pending_action_id"),
        _set("_lite_ray_visit_index", 0),
        {"for_each": {"collection": "attack_targets", "as": "_multi_ray_target", "do": [
            _when("_lite_ray_visit_index >= _sanctuary_ray_index and _lite_ray_deferred != true", body),
            _set("_lite_ray_visit_index", "_lite_ray_visit_index + 1"),
        ]}},
        # Initial cast still needs this pointer for its existing consequence-context capture.
        # A later generic completion began with null and restores that null instead.
        *_copy_value("caster_state.pending_srd_completion_id", "_lite_ray_completion_before"),
        *store_aggregate,
        _when("_lite_ray_deferred != true and _sanctuary_ray_index == len(attack_targets)", [
            deepcopy(finalizer),
            _set(EVENT + "." + CURSOR + ".finalized", True),
            _set(EVENT + ".stage", "'spell_attack_resolved'"),
        ]),
        _when("_lite_ray_deferred == true", [
            _append_rows("_nested_spell_summary_rows", "_spell_summary_rows"),
            _append_rows("_nested_spell_detail_rows", "_spell_detail_rows"),
        ]),
        _set("_spell_hit_window_deferred", "_lite_ray_deferred"),
        _set("_spell_card_summary", "_spell_summary_rows"),
    ]
    tools[CONTINUE] = _tool(
        "Resume the saved ordered rays without casting again, spending again or rerolling a saved hit.",
        [*parent_parameters, _param("pending_action_id")], continuation_effects,
    )
    tools[CONTINUE]["metadata"] = {
        "category": "internal", "visible_to_player": False, "costs_action": False,
    }

    initial_cursor = [
        _set("_lite_ray_pending_id", "caster_state.pending_srd_completion_id"),
        _require("lasting_state.pending_srd_actions[_lite_ray_pending_id].kind == 'spell_cast' and "
                 "lasting_state.pending_srd_actions[_lite_ray_pending_id].owner_kind == 'spell_cast' and "
                 "lasting_state.pending_srd_actions[_lite_ray_pending_id].actor_state_id == _caster_state_id and "
                 "lasting_state.pending_srd_actions[_lite_ray_pending_id]." + CURSOR + " == null",
                 "Start this ordered ray cursor only inside its original saved casting action."),
        _set(cursor_field, {
            "targets": "attack_targets", "index": 0, "awaiting_hit": False,
            "finalized": False, "parameters": {}, "context": {},
            "sanctuary_lost_rays": "_sanctuary_lost_rays",
        }),
        *_save_parameters(parent_parameters, cursor_field + ".parameters"),
        # Canonical arrays are otherwise created lazily by the first completed row.
        # A first-ray suspension must persist typed empty arrays, not missing/null values.
        *[_when(name + " == null", [_set(name, [])]) for name in aggregate_names
          if name != "_spell_multi_attack_count"],
        *_copy_context(aggregate_names, cursor_field + ".context."),
        _call(CONTINUE, {
            **{p["name"]: "{" + ("_" + p["name"] + "_id" if p.get("resolve") in {
                "entity", "participant"} else p["name"]) + "}"
               for p in parent_parameters if p["name"] != "lasting_state"},
            "pending_action_id": "{_lite_ray_pending_id}", "lasting_state": "{_lasting_state_id}",
        }, EXPORTS),
    ]
    # The validation/payment prefix and direct immediate-resolution sequence are retained.
    original_tail = deepcopy(owner["effects"][loop_index:])
    owner["effects"][loop_index:] = [
        _set("_lite_ray_staged", "spell == 'scorching_ray' and "
             "caster_state.pending_srd_completion_id != null"),
        _when("_lite_ray_staged == true", initial_cursor),
        _when("_lite_ray_staged != true", original_tail),
    ]

    complete = tools["complete_srd_pending_action"]
    scalar_dispatch = _one(
        (e for effects in _lists(complete["effects"]) for e in effects),
        lambda e: e.get("call") == "complete_srd_spell_attack_consequences",
        "scalar pending hit continuation",
    )
    scalar_dispatch["condition"] = "(" + scalar_dispatch["condition"] + ") and " + \
        "_pending_action." + CURSOR + " == null"
    complete["effects"].insert(complete["effects"].index(scalar_dispatch), _when(
        "_pending_action.kind == 'spell_attack' and _pending_action.status == 'open' and "
        "_pending_action.stage == 'spell_attack_reaction' and _pending_action." + CURSOR + " != null",
        [
            _call(CONTINUE, {
                **{p["name"]: "{_pending_action." + CURSOR + ".parameters." + p["name"] + "}"
                   for p in parent_parameters if p["name"] != "lasting_state"},
                "pending_action_id": "{pending_action_id}", "lasting_state": "{_lasting_state_id}",
            }, (*EXPORTS, "_spell_card_summary")),
            _set("_pending_card_summary", "_spell_summary_rows"),
            _set("_pending_card_details", "_spell_detail_rows"),
        ],
    ))
    # Existing cleanup already retains spell_attack_reaction and removes only settled frames.
    for root_name in ("cast_spell", "cast_spell_outside_combat"):
        for effects in _lists(tools[root_name]["effects"]):
            for effect in effects:
                if effect.get("call") == "cast_spell_multi_attack_damage":
                    effect["export_context"]["_spell_hit_window_deferred"] = "_spell_hit_window_deferred"
    return {"cursor_field": CURSOR, "new_internal_tools": [CONTINUE, FINISH],
            "hit_context_names": names, "scope": "staged Lite Scorching Ray only"}
