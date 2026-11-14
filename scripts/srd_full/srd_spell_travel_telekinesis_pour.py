"""Telekinesis pours actual plain water through the existing Mage Hand vial mutation."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import _tool_scene
    from .srd_spell_object_content import GEOMETRY_EXPORTS, _now
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
    )
    from .srd_timed_senses import _public, _replace_tools
else:
    from srd_scene_relations import _tool_scene
    from srd_spell_object_content import GEOMETRY_EXPORTS, _now
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
    )
    from srd_timed_senses import _public, _replace_tools


def _vial_qualification():
    """Derive an actual open plain-water vial without accepting quantities or contents verdicts."""
    return _tool_scene(
        "Qualify actual admitted plain-water vial contents for a source operation.",
        [_entity("caster_state"), _parameter("object_id")],
        [
            _call(
                "resolve_srd_object_content_geometry",
                {
                    "caster_state": "{_caster_state_id}",
                    "object_id": "{object_id}",
                    "range_feet": 60,
                    "requires_sight": True,
                },
                GEOMETRY_EXPORTS,
            ),
            _set("_utility_facts", "_object_scene.metadata.srd_object_utility"),
            _require(
                "_utility_facts != null and _utility_facts.weight_pounds >= 0",
                "Admit the actual vial's physical classification and valid weight first.",
            ),
            _require(
                "_utility_facts != null and _utility_facts.object_kind == 'vial' and "
                "_object_scene.metadata.srd_object_container != null and "
                "_object_scene.metadata.srd_object_container.is_open == true",
                "Pour actual admitted contents from an open vial.",
            ),
            _require(
                "(_object_scene.metadata.srd_created_supply == null or "
                "(_object_scene.metadata.srd_created_supply.water_gallons == 0 and "
                "_object_scene.metadata.srd_created_supply.food_pounds == 0)) and "
                "(_object_scene.metadata.srd_object_container.food_pounds == null or "
                "_object_scene.metadata.srd_object_container.food_pounds == 0)",
                "Generated supply or mixed vial contents need their actual transfer owner.",
            ),
            *_now(),
        ],
    )


def _plain_pour_node(tools):
    """Extract the existing vial guard and canonical mutation after hand-specific qualification."""
    tail = [
        _require(False, "Plain-water pouring needs the actual Mage Hand vial mutation owner."),
    ]
    if "operate_srd_mage_hand" in tools:
        clauses = [
            row
            for row in tools["operate_srd_mage_hand"]["effects"]
            if row.get("condition") == "operation == 'pour'"
        ]
        if len(clauses) != 1:
            raise ValueError("Mage Hand must expose exactly one actual pour branch.")
        body = clauses[0]["on_true"]
        boundary = [
            i
            for i, row in enumerate(body)
            if row.get("require", {})
            .get("condition", "")
            .startswith("_utility_facts.object_kind == 'vial' and ")
        ]
        if len(boundary) != 1:
            raise ValueError("Mage Hand needs its actual vial guard after contact and fact setup.")
        tail = deepcopy(body[boundary[0] :])
        if not any(row.get("spatial_mutation") for row in tail):
            raise ValueError("Vial pouring must retain the canonical scene mutation.")
    return _tool_scene(
        "Pour qualified plain water through the existing canonical vial contents mutation.",
        [_entity("caster_state"), _parameter("object_id"), _parameter("source_id")],
        [
            _call(
                "qualify_srd_plain_water_vial",
                {"caster_state": "{_caster_state_id}", "object_id": "{object_id}"},
                ["_object_scene", "_utility_facts", "_object_now"],
            ),
            *tail,
        ],
    )


def pour_tools(tools, fine):
    """Retain fine control's actual source and Magic Action, replacing only its object operation."""
    if fine is None:
        return {}
    pour = deepcopy(fine)
    pour["parameters"] = [p for p in pour["parameters"] if p["name"] != "operation"]
    pour["description"] = "Pour an actual visible unheld vial on the source's later Magic Action."
    effects = pour["effects"]
    qualification = next(
        i
        for i, row in enumerate(effects)
        if row.get("call") == "qualify_srd_telekinesis_object_access"
    )
    effects.insert(
        qualification + 1,
        _call(
            "qualify_srd_plain_water_vial",
            {"caster_state": "{_caster_state_id}", "object_id": "{object_id}"},
        ),
    )
    access = next(
        i
        for i, row in enumerate(effects)
        if row.get("call") == "operate_srd_object_access_source_node"
    )
    effects[access] = _call(
        "pour_srd_object_vial_source_node",
        {
            "caster_state": "{_caster_state_id}",
            "object_id": "{object_id}",
            "source_id": "{source_id}",
        },
        ["_force_poured"],
    )
    marker = next(row for row in effects if row.get("set", {}).get("field") == "_tk_fine_operation")
    marker["set"]["value"] = "'pour'"
    pour = _public(
        pour,
        "exploration",
        "Telekinesis pour",
        [
            ("Water poured (gallons)", "_force_poured.water_gallons"),
            ("Plane", "_force_poured.plane"),
        ],
    )
    return {
        "qualify_srd_plain_water_vial": _vial_qualification(),
        "pour_srd_object_vial_source_node": _plain_pour_node(tools),
        "pour_srd_telekinesis_vial": pour,
    }


def emit_late_pour(text):
    """Refresh only the extracted vial leaf after the real Mage Hand owner is emitted."""
    tools = _load_runtime(text)["tools"]
    name = "pour_srd_object_vial_source_node"
    if name not in tools:
        return text
    node = _plain_pour_node(tools)
    if tools[name] == node:
        return text
    return _replace_tools(text, {name: node})
