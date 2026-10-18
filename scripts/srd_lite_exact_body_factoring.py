"""Share identical selected and retained spell blocks without changing their decisions."""

import hashlib
import json
import re
from copy import deepcopy

from .srd_retained_spell_reactions import CHOICES, _forward, _restore_rows, _rewrite_choices

CONTEXT = "_srd_factored_context"
CHOICE_PREFIX = "_srd_factored_choice_"
MARKER = "srd_lite_exact_body_factoring_v1"

COMBAT_BUDGET_FIELDS = (
    "action_available",
    "action_surge_available",
    "action_surge_used",
    "action_taken",
    "action_used_this_turn",
    "active_actor",
    "active_actor_at_zero_hp",
    "active_actor_available_bonus_actions",
    "active_actor_can_act",
    "active_actor_form_restricted",
    "active_actor_form_wind_only_actions",
    "active_actor_kind",
    "active_actor_prepared_spells",
    "active_actor_spell_slow",
    "active_actor_stable",
    "attack_available",
    "attacks_remaining",
    "bonus_action_used_this_turn",
    "current_actor",
    "dashes_taken",
    "disengaged_actor",
    "encounter_active",
    "first_surprised",
    "group_order",
    "haste_action_available",
    "haste_action_granted",
    "haste_action_selected",
    "initiative_first_actor",
    "initiative_first_total",
    "initiative_second_actor",
    "initiative_second_total",
    "insect_multiattack_active",
    "last_action_source",
    "last_end_reason",
    "lasting_epoch_round",
    "lasting_epoch_tick",
    "light_attack_followup_available",
    "monk_bonus_strike_available",
    "monk_focus_action_available",
    "movement_mode",
    "movement_remaining",
    "movement_spent",
    "multiattack_remaining",
    "normal_action_spent",
    "opportunity_attack_pending",
    "order",
    "order_first",
    "order_second",
    "pending_reaction",
    "phase",
    "round",
    "second_surprised",
    "step_wind_companion",
    "transformation_magic_allowed",
    "turn_active",
    "turn_index",
)
ACTOR_BUDGET_FIELDS = (
    "bonus_action_available",
    "spell_slot_cast_this_turn",
    "light_extra_attack_used",
    "light_attack_source_weapon",
    "light_attack_source_weapons",
    "light_unrestricted_source_weapons",
    "flurry_attacks_remaining",
)

# These fingerprints qualify exact common native statements, not spell identities.
GROUPS = [
    {
        "helper": "project_srd_lite_shared_transformation",
        "description": "transformation projection",
        "rows": 34,
        "digest": "ac2a41690fcdc2708868f3982fd621346ffbab4b1c4f89b55ad2d4afc5fe1e7c",
        "owners": ["project_srd_transformation", "retained_spell_query_project_srd_transformation"],
    },
    {
        "helper": "resolve_srd_lite_shared_recovery_sources",
        "description": "recovery source continuation",
        "rows": 19,
        "digest": "bb53ac2870e24825fa76aba3d2fac121ca6669249f563437c63186a4c0a56895",
        "owners": ["cast_srd_recovery", "release_srd_contingent_recovery"],
    },
    {
        "helper": "qualify_srd_lite_shared_travel_body",
        "description": "travel body qualification",
        "rows": 31,
        "digest": "a6fb17ce0216a0e94435a71b5f0546ebb781d419fadcab58e959fdb959a56a2e",
        "owners": [
            "move_srd_travel_subject_node",
            "return_srd_mansion_subject_node",
            "return_srd_rope_trick_subject_node",
            "qualify_srd_lite_steed_teleport",
        ],
    },
    {
        "helper": "qualify_srd_lite_shared_movement_body",
        "description": "ordinary movement preflight",
        "rows": 34,
        "digest": "ad8010b726fb3ac2d5d9566b58b5dee4062a1aecea68c84edb4aefde4a651994",
        "owners": [
            "move_srd_scene_creature_environment_segment",
            "qualify_srd_celestial_scene_route",
            "move_srd_courier_chronological_segment",
        ],
    },
    {
        "helper": "restore_srd_lite_shared_suspended_budget",
        "description": "suspended combat budget",
        "rows": 63,
        "digest": "ba93a0d21089e1425e7e02bc855253e3948fbd59d1f1040b08e1bcea800ba808",
        "owners": [
            "restore_srd_time_stop_budget",
            "retained_spell_query_restore_srd_time_stop_budget",
        ],
    },
    {
        "helper": "resolve_srd_lite_shared_cylinder_body_segment",
        "description": "physical cylinder and body segment intersection",
        "rows": 5,
        "digest": "03c70db63bbb71a4f55d9be3fbd5bb98fd447e4c83500b4d9e771c0a884ba340",
        "owners": [
            "resolve_srd_environment_cylinder_body_segment",
            "resolve_srd_celestial_body_segment",
            "resolve_srd_celestial_intent_body_segment",
        ],
    },
    {
        "helper": "resolve_srd_lite_shared_creature_space_segment",
        "description": "physical creature-space sphere and body segment intersection",
        "rows": 7,
        "digest": "9c0042c7d805ff81dfa9dedfab09b61effccc4908018f6012b3aabd8f8c09089",
        "owners": [
            "resolve_srd_environment_body_segment",
            "resolve_srd_truth_zone_segment",
            "retained_spell_query_resolve_srd_environment_body_segment",
        ],
    },
]


def _digest(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _lists(value):
    if isinstance(value, list):
        yield value
        for child in value:
            yield from _lists(child)
    elif isinstance(value, dict):
        for child in value.values():
            yield from _lists(child)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)


def _rename_choices(value, choices, *, inverse=False):
    """Use the established literal-aware rewriter, with finite private choice roots."""
    if inverse:

        def prepare(text):
            return re.sub(
                r"(?<![\w.])" + re.escape(CHOICE_PREFIX) + r"([A-Za-z_][A-Za-z0-9_]*)",
                lambda match: CHOICES + "." + match[1] if match[1] in choices else match[0],
                text,
            )
    else:

        def prepare(text):
            return re.sub(
                re.escape(CHOICES) + r"\.([A-Za-z_][A-Za-z0-9_]*)",
                lambda match: CHOICE_PREFIX + match[1] if match[1] in choices else match[0],
                text,
            )

    def rewrite(item):
        if isinstance(item, str):
            return prepare(item)
        if isinstance(item, list):
            return [rewrite(child) for child in item]
        if isinstance(item, dict):
            return {key: rewrite(child) for key, child in item.items()}
        return item

    return (
        _rewrite_choices(rewrite(value), choices, inverse=True)
        if inverse
        else rewrite(_rewrite_choices(value, choices))
    )


def _locate(tool, group):
    matches = []
    size = group["rows"]
    for effects in _lists(tool["effects"]):
        for offset in range(len(effects) - size + 1):
            body = effects[offset : offset + size]
            if _digest(body) == group["digest"]:
                matches.append((effects, offset, body))
    if len(matches) != 1:
        raise ValueError(f"Preserve one exact native body for {group['helper']}: {len(matches)}")
    return matches[0]


def _used_parameters(tool, body):
    strings = list(_strings(body))
    return [
        parameter
        for parameter in tool["parameters"]
        if any(
            re.search(r"(?<![\w.])" + re.escape(parameter["name"]) + r"\b", text)
            for text in strings
        )
    ]


def _scope_inputs(effects, assigned=()):
    """Capture reads from conditional operations before any guaranteed local write."""
    defined, needed = set(assigned), set()
    for effect in effects:
        condition = effect.get("condition")
        if condition is not None:
            needed |= _references(condition) - defined
        if "on_true" in effect or "on_false" in effect:
            needed |= _scope_inputs(effect.get("on_true", []), defined)
            needed |= _scope_inputs(effect.get("on_false", []), defined)
            continue
        if "for_each" in effect:
            loop = effect["for_each"]
            needed |= _references(loop["collection"]) - defined
            needed |= _scope_inputs(loop["do"], defined | {loop["as"]})
            continue
        if "set" in effect:
            setter = effect["set"]
            field = setter["field"]
            needed |= _references(setter["value"]) - defined
            needed |= _references(re.sub(r"^_[A-Za-z][A-Za-z0-9_]*$", "", field)) - defined
            if condition is None and re.fullmatch(r"_[A-Za-z][A-Za-z0-9_]*", field):
                defined.add(field)
            continue
        needed |= (
            _references(
                {
                    key: value
                    for key, value in effect.items()
                    if key not in {"export_context", "store_as", "as"}
                }
            )
            - defined
        )
        if condition is None:
            if isinstance(effect.get("store_as"), str):
                defined.add(effect["store_as"])
            if isinstance(effect.get("export_context"), dict):
                defined |= set(effect["export_context"])
    return needed


def _references(value):
    """Read private expression roots while keeping quoted literals opaque."""
    if isinstance(value, str):
        roots = set()
        tokens = r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|(?<![\w.])_[A-Za-z][A-Za-z0-9_]*"
        for match in re.finditer(tokens, value):
            token = match[0]
            if token[:1] in {"'", '"'}:
                roots.update(re.findall(r"\{(_[A-Za-z][A-Za-z0-9_]*)[.\[} ]", token))
            else:
                roots.add(token)
        return roots
    if isinstance(value, list):
        return set().union(*(_references(child) for child in value)) if value else set()
    if isinstance(value, dict):
        return set().union(*(_references(child) for child in value.values())) if value else set()
    return set()


def _restore_context():
    """Keep the retained transport primitive, with a distinct local iterator name."""
    rows = _restore_rows(CONTEXT)

    def rename(value):
        if isinstance(value, str):
            return value.replace("_retained_cast_tail_row", "_srd_factored_row")
        if isinstance(value, list):
            return [rename(child) for child in value]
        if isinstance(value, dict):
            return {key: rename(child) for key, child in value.items()}
        return value

    return rename(rows)


def _budget_rows():
    """Describe only the original ordered 56 combat and seven actor restoration fields."""
    rows = []
    for fields, target, source in (
        (COMBAT_BUDGET_FIELDS, "combat", "suspended_combat"),
        (ACTOR_BUDGET_FIELDS, "caster_state", "suspended_actor_budget"),
    ):
        for field in fields:
            reference = f"lasting_state.time_stop_source.{source}.{field}"
            rows.append(
                {
                    "condition": reference + " != null",
                    "on_true": [{"set": {"field": target + "." + field, "value": reference}}],
                    "on_false": [{"set": {"field": target + "." + field, "value": None}}],
                }
            )
    return rows


def _budget_loops():
    """Use finite local collections and preserve each original null/absent field branch."""
    rows = []
    for fields, target, source in (
        (COMBAT_BUDGET_FIELDS, "combat", "suspended_combat"),
        (ACTOR_BUDGET_FIELDS, "caster_state", "suspended_actor_budget"),
    ):
        reference = "lasting_state.time_stop_source." + source
        rows.extend(
            [
                {
                    "set": {
                        "field": "_srd_budget_fields",
                        "value": [repr(field) for field in fields],
                    }
                },
                {
                    "for_each": {
                        "collection": "_srd_budget_fields",
                        "as": "_srd_budget_field",
                        "do": [
                            {
                                "condition": f"{reference} != null and "
                                f"_srd_budget_field in {reference} and "
                                f"{reference}[_srd_budget_field] != null",
                                "on_true": [
                                    {
                                        "set": {
                                            "field": target + ".{_srd_budget_field}",
                                            "value": reference + "[_srd_budget_field]",
                                        }
                                    }
                                ],
                                "on_false": [
                                    {
                                        "set": {
                                            "field": target + ".{_srd_budget_field}",
                                            "value": None,
                                        }
                                    }
                                ],
                            },
                        ],
                    }
                },
            ]
        )
    return rows


def _expand_budget_loops(rows):
    """Reject altered allowlists, targets, source paths or branch shapes before expansion."""
    if rows != _budget_loops():
        raise ValueError("Preserve the exact finite suspended-budget loops")
    return _budget_rows()


def bind_srd_lite_exact_body_factoring(runtime):
    """Atomically replace only qualified identical blocks with a private native call."""
    tools = runtime["tools"]
    if all(tools.get(group["helper"], {}).get("metadata", {}).get(MARKER) for group in GROUPS):
        return runtime
    if any(group["helper"] in tools for group in GROUPS):
        raise ValueError("Preserve a complete exact-body factoring owner")
    replacements, helpers = {}, {}
    for group in GROUPS:
        originals = {name: tools[name] for name in group["owners"]}
        located = {name: _locate(tool, group) for name, tool in originals.items()}
        body = next(iter(located.values()))[2]
        private = _scope_inputs(body)
        used = {name: _used_parameters(tool, body) for name, tool in originals.items()}
        entity_parameters = {}
        choices = set()
        for parameters in used.values():
            for parameter in parameters:
                name = parameter["name"]
                if parameter.get("resolve") or parameter.get("from_state"):
                    if name in entity_parameters and entity_parameters[name] != parameter:
                        raise ValueError(f"Preserve exact shared entity declaration: {name}")
                    entity_parameters[name] = deepcopy(parameter)
                else:
                    choices.add(name)
        rewritten = _rename_choices(body, choices)
        if _rename_choices(rewritten, choices, inverse=True) != body:
            raise ValueError("Preserve exact original common-body expressions")
        if group["helper"] == "restore_srd_lite_shared_suspended_budget":
            if rewritten != _budget_rows():
                raise ValueError("Preserve original suspended-budget fields and branches")
            rewritten = _budget_loops()
            if _expand_budget_loops(rewritten) != body:
                raise ValueError("Preserve exact suspended-budget inverse expansion")
        capsule = [
            {"key": repr(name), "missing": name + " == null", "value": name}
            for name in sorted(private)
        ]
        capsule += [
            {"key": repr(CHOICE_PREFIX + name), "missing": name + " == null", "value": name}
            for name in sorted(choices)
        ]
        helper = {
            "description": "Execute the unchanged shared native " + group["description"] + ".",
            "parameters": list(entity_parameters.values())
            + [
                {
                    "name": CONTEXT,
                    "type": "array",
                    "required": True,
                    "model_hidden": True,
                    "items": {"type": "object"},
                    "min_items": len(capsule),
                    "max_items": len(capsule),
                }
            ],
            "pre_conditions": [],
            "effects": _restore_context() + rewritten,
            "metadata": {
                "category": "internal",
                "visible_to_player": False,
                "costs_action": False,
                MARKER: True,
            },
        }
        helpers[group["helper"]] = helper
        for name, original in originals.items():
            changed = replacements.setdefault(name, deepcopy(original))
            effects, offset, exact = _locate(changed, group)
            if exact != body:
                raise ValueError("Preserve byte-equivalent original common statements")
            effects[offset : offset + group["rows"]] = [
                {"set": {"field": CONTEXT, "value": deepcopy(capsule)}},
                {
                    "call": group["helper"],
                    "params": {
                        **{
                            name: _forward(parameter)
                            for name, parameter in entity_parameters.items()
                        },
                        CONTEXT: "{" + CONTEXT + "}",
                    },
                    "export_context": "*",
                },
            ]
    tools.update(replacements)
    tools.update(helpers)
    return runtime
