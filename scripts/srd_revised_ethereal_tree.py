"""Narrow retained-SRD repairs for real plane attacks and Tree Stride consumers.

Retain all original paid casts, movement, dice, geometry and concentration owners.
This module composes into an expanded original runtime before normal admission.
"""

from copy import deepcopy


def _set(field, value):
    return {"set": {"field": field, "value": value}}


def _call(name, params, exports=()):
    row = {"call": name, "params": params}
    if exports:
        row["export_context"] = {key: key for key in exports}
    return row


def _require(condition, error):
    return {"require": {"condition": condition, "error": error}}


def _when(condition, effects):
    return {"condition": condition, "on_true": effects}


def _entity(name):
    return {"name": name, "type": "string", "resolve": "entity", "required": True}


def _lasting():
    return {"name": "lasting_state", "type": "string", "resolve": "entity",
            "required": False, "default": "state"}


def _tool(description, parameters, effects):
    return {"description": description, "parameters": parameters,
            "pre_conditions": [], "effects": effects,
            "metadata": {"category": "internal", "visible_to_player": False,
                         "costs_action": False}}


def attack_plane_tool():
    return _tool("Validate actual admitted attack planes before budgets or rolls.",
        [_entity("actor_state"), _entity("target_state"), _lasting()], [
            _require("lasting_state.spatial_scene != null or (actor_state.travel_ethereal == null and target_state.travel_ethereal == null)",
                     "An Etherealness attack needs the actual admitted planes."),
            _when("lasting_state.spatial_scene != null", [
                _call("resolve_srd_scene_geometry_relation", {
                    "actor_state": "{_actor_state_id}", "target_state": "{_target_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                }, ("_scene_same_plane",)),
                _require("_scene_same_plane == true", "An attack cannot affect a target on another plane."),
            ]),
        ])


def tree_stride_refresh_tool():
    return _tool("Refresh a registered Tree Stride through canonical concentration cleanup.",
        [_entity("caster_state"), _lasting()], [
            _set("_revised_tree_active", False),
            _when("caster_state.tree_stride != null and caster_state.concentration_spell_id == 'tree_stride' and caster_state.concentration_effect_family == 'travel_spell'", [
                # The retained Tree record is the deadline authority. The saved
                # registry is populated by paid casts composed under this binder.
                _set("caster_state.concentration_expires_tick", "caster_state.tree_stride.expires_tick"),
                _call("refresh_srd_concentration_source", {
                    "source_state": "{_caster_state_id}", "target_state": "{_caster_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                }, ("_source_concentration_active",)),
                _set("_revised_tree_active", "_source_concentration_active"),
            ]),
        ])


def tree_stride_clock_effects():
    return [_when("lasting_state.revised_tree_stride_caster_ids != null", [
        _set("_revised_tree_casters", "lasting_state.revised_tree_stride_caster_ids"),
        _set("_revised_tree_remaining", []),
        {"for_each": {"collection": "_revised_tree_casters", "as": "_revised_tree_caster", "do": [
            _call("refresh_revised_srd_tree_stride", {
                "caster_state": "{_revised_tree_caster}", "lasting_state": "{_lasting_state_id}",
            }, ("_revised_tree_active",)),
            {"list_push": {"list": "_revised_tree_remaining", "value": "_revised_tree_caster"},
             "condition": "_revised_tree_active == true"},
        ]}},
        _set("lasting_state.revised_tree_stride_caster_ids", "_revised_tree_remaining"),
    ])]


def tree_stride_card_effects():
    return [
        _set("_revised_tree_count", "len(_travel_tree_locations)"),
        _set("_revised_tree_rows", []),
        {"for_each": {"collection": "_travel_tree_locations", "as": "_revised_tree_location", "do": [
            {"list_push": {"list": "_revised_tree_rows", "value": {
                "label": "_revised_tree_location.subject", "value": "_revised_tree_location.position"}},
             "condition": "len(_revised_tree_rows) < 128"},
        ]}},
    ]


ATTACK_PATHS = {
    "resolve_srd_settled_weapon_attack": ("_actor_state_id", "_target_id"),
    "resolve_adjudicated_creature_attack": ("_actor_id", "_target_id"),
    "resolve_opportunity_attack": ("_actor_state_id", "_target_state_id"),
    "resolve_unarmed_opportunity_attack": ("_actor_state_id", "_target_state_id"),
    "resolve_srd_unarmed_effect": ("_actor_state_id", "_target_state_id"),
    "begin_srd_character_attack": ("_actor_state_id", "_target_id"),
    "begin_srd_creature_attack": ("_actor_id", "_target_id"),
    "complete_srd_character_attack": ("_actor_state_id", "_target_id"),
    "complete_srd_creature_attack": ("_actor_id", "_target_id"),
}


def bind_revised_ethereal_tree(runtime):
    """Insert scoped checks/presentation/lifetime hooks; replace no paid owner."""
    tools = runtime["tools"]
    required = set(ATTACK_PATHS) | {"resolve_srd_scene_geometry_relation",
        "refresh_srd_concentration_source", "end_srd_concentration_node",
        "advance_srd_lasting_clock", "sync_srd_group_lasting_clock",
        "cast_spell_travel_spell", "traverse_srd_tree_stride"}
    missing = required - tools.keys()
    if missing:
        raise ValueError("Retain original SRD consumers before repair: " + ", ".join(sorted(missing)))
    if "validate_revised_srd_attack_plane" in tools or "refresh_revised_srd_tree_stride" in tools:
        raise ValueError("The Etherealness/Tree Stride repair was already composed.")
    for name, (actor, target) in ATTACK_PATHS.items():
        params = {p["name"] for p in tools[name]["parameters"]}
        if actor[1:-3] not in params or target[1:-3] not in params or "lasting_state" not in params:
            raise ValueError("Review actual resolved attack identities for " + name)
    branches = [row for row in tools["cast_spell_travel_spell"]["effects"]
                if row.get("condition") == "spell == 'tree_stride'"]
    if len(branches) != 1:
        raise ValueError("Review the retained Tree Stride cast branch.")
    branch = branches[0]["on_true"]
    owners = [row for row in branch if row.get("set", {}).get("field") == "caster_state.tree_stride"]
    if len(owners) != 1 or owners[0]["set"]["value"]["expires_tick"] != "lasting_state.lasting_tick + 20":
        raise ValueError("Preserve the actual retained ten-round Tree Stride owner.")
    public = tools["traverse_srd_tree_stride"]
    joins = [i for i, row in enumerate(public["effects"]) if row.get("call") == "traverse_srd_tree_stride_node"]
    if len(joins) != 1 or public["effects"][joins[0]].get("export_context", {}).get("_travel_tree_locations") != "_travel_tree_locations":
        raise ValueError("Retain the real Tree Stride geometry and location export.")
    card = public["metadata"]["result_card"]
    if card["summary"][-1] != {"label": "Known trees", "value": {"path": "context._travel_tree_locations"}}:
        raise ValueError("Review the actual original Tree Stride card contract.")
    tools["validate_revised_srd_attack_plane"] = attack_plane_tool()
    tools["refresh_revised_srd_tree_stride"] = tree_stride_refresh_tool()
    for name, (actor, target) in ATTACK_PATHS.items():
        tools[name]["effects"].insert(0, _call("validate_revised_srd_attack_plane", {
            "actor_state": "{" + actor + "}", "target_state": "{" + target + "}",
            "lasting_state": "{_lasting_state_id}",
        }))
    branch.extend([
        _set("caster_state.concentration_expires_tick", "caster_state.tree_stride.expires_tick"),
        _set("lasting_state.revised_tree_stride_caster_ids", []),
    ])
    # Initialize only a previously absent registry, preserving other casters.
    branch[-1]["condition"] = "lasting_state.revised_tree_stride_caster_ids == null"
    branch.extend([
        _set("_revised_tree_registered", "lasting_state.revised_tree_stride_caster_ids"),
        {"list_push": {"list": "_revised_tree_registered", "value": "_caster_state_id"},
         "condition": "_caster_state_id not in _revised_tree_registered"},
        _set("lasting_state.revised_tree_stride_caster_ids", "_revised_tree_registered"),
    ])
    tools["advance_srd_lasting_clock"]["effects"].extend(tree_stride_clock_effects())
    # The real multi-actor clock writes its fractional round tick directly.
    # Its lifecycle hook is separate from ordinary pair/exploration advance.
    tools["sync_srd_group_lasting_clock"]["effects"].extend(tree_stride_clock_effects())
    public["effects"][joins[0] + 1:joins[0] + 1] = tree_stride_card_effects()
    revised_card = deepcopy(card)
    revised_card["summary"][-1]["value"] = {"path": "context._revised_tree_count"}
    revised_card["details"] = {"path": "context._revised_tree_rows"}
    revised_card["details_label"] = "Known tree locations (up to 128)"
    public["metadata"]["result_card"] = revised_card
    return {"retained_paid_cast_and_movement": True, "attack_paths": list(ATTACK_PATHS),
            "tree_duration_ticks": 20, "tree_cleanup_owner": "end_srd_concentration_node",
            "card_row_limit": 128, "new_internal_tools": 2}
