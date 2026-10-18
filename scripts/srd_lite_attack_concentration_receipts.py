"""Expose the existing staged NPC concentration save without executing it again."""

from copy import deepcopy

FIELDS = tuple("_concentration_save_" + part for part in (
    "dc", "roll", "outcome", "modifier", "total",
))
INITIALIZE = "initialize_srd_attack_concentration_receipt"
LEAF = "apply_srd_saved_hit_qualifying_damage"
PACKET = "finish_srd_creature_attack_hit_packet"
ATTACK = "complete_srd_creature_attack"
COMPLETE = "complete_srd_pending_action"


def _effects(rows):
    for effect in rows:
        yield effect
        for branch in ("on_true", "on_false"):
            yield from _effects(effect.get(branch, []))
        if "for_each" in effect:
            yield from _effects(effect["for_each"]["do"])


def _calls(tool, child):
    return [effect for effect in _effects(tool["effects"]) if effect.get("call") == child]


def _export(call, fields):
    exports = call["export_context"]
    assert isinstance(exports, dict)
    for field in fields:
        assert field not in exports or exports[field] == field
        exports[field] = field


def bind_attack_concentration_receipts(runtime):
    """Carry real save fields through the saved-hit/public completion boundaries.

    Null initializers represent no save on a miss or a nonconcentrating target.
    Existing receiver effects, one-save guards, D20 rules and state writes stay exact.
    """
    tools = runtime["tools"]
    names = (LEAF, PACKET, ATTACK, COMPLETE)
    before = {name: deepcopy(tools[name]) for name in names}
    protected = {name: deepcopy(tools[name]) for name in (
        "resolve_srd_concentration_after_damage", "execute_srd_qualified_scalar_packet",
        "apply_srd_qualifying_damage", "d20_test",
    )}
    assert INITIALIZE not in tools
    tools[INITIALIZE] = {
        "description": "Initialize only volatile concentration display fields before an attack.",
        "parameters": [],
        "pre_conditions": [],
        "effects": [{"set": {"field": field, "value": None}} for field in FIELDS],
    }
    for name in (PACKET, ATTACK):
        tools[name]["effects"].insert(0, {"call": INITIALIZE, "export_context": "*"})
    # The saved receiver already initializes dc/outcome/roll; add only its final totals.
    leaf_defaults = [{"set": {"field": field, "value": None}} for field in FIELDS[3:]]
    tools[LEAF]["effects"][:0] = deepcopy(leaf_defaults)
    leaf_calls = _calls(tools[LEAF], "resolve_srd_concentration_after_damage")
    assert len(leaf_calls) == 1
    _export(leaf_calls[0], FIELDS)
    packet_calls = _calls(tools[PACKET], LEAF)
    assert len(packet_calls) == 8
    for call in packet_calls:
        _export(call, FIELDS)
    attack_calls = _calls(tools[ATTACK], PACKET)
    assert len(attack_calls) == 1 and attack_calls[0]["export_context"] == "*"
    completion_calls = _calls(tools[COMPLETE], ATTACK)
    assert len(completion_calls) == 1
    _export(completion_calls[0], FIELDS)

    direct_rows = [
        {"label": "Concentration DC", "value": {"path": "context." + FIELDS[0]},
         "optional": True},
        {"label": "Concentration", "value": {"path": "context." + FIELDS[2]},
         "optional": True, "equation": {
             "die_sides": 20, "roll": {"path": "context." + FIELDS[1] + ".total"},
             "modifier": {"path": "context." + FIELDS[3]},
             "total": {"path": "context." + FIELDS[4]}, "optional": True,
         }},
    ]
    card = tools[ATTACK]["metadata"]["result_card"]
    assert isinstance(card["details"], list)
    card["details"].extend(deepcopy(direct_rows))
    rows = tools[COMPLETE]["effects"]
    feature_positions = [i for i, effect in enumerate(rows)
                         if effect.get("set", {}).get("field") == "_spell_featured_equations"]
    assert len(feature_positions) == 1
    feedback = {
        "condition": FIELDS[1] + " != null",
        "on_true": [
            {"list_push": {"list": "_pending_card_details", "value": {
                "label": "'Concentration DC'", "value": FIELDS[0],
            }}},
            {"list_push": {"list": "_pending_card_details", "value": {
                "label": "'Concentration'", "value": FIELDS[2], "equation": {
                    "die_sides": 20, "roll": FIELDS[1] + ".total",
                    "modifier": FIELDS[3], "total": FIELDS[4],
                },
            }}},
        ],
    }
    rows.insert(feature_positions[0], deepcopy(feedback))

    # Strip precisely these receipt additions and prove all original owners exact.
    restored = {name: deepcopy(tools[name]) for name in names}
    for name in (PACKET, ATTACK):
        assert restored[name]["effects"].pop(0) == {"call": INITIALIZE, "export_context": "*"}
    assert restored[LEAF]["effects"][:2] == leaf_defaults
    del restored[LEAF]["effects"][:2]
    for name, child in ((LEAF, "resolve_srd_concentration_after_damage"),
                        (PACKET, LEAF), (COMPLETE, ATTACK)):
        old_calls = _calls(before[name], child)
        for old, current in zip(old_calls, _calls(restored[name], child), strict=True):
            for field in FIELDS:
                if field not in old["export_context"]:
                    current["export_context"].pop(field)
    assert restored[ATTACK]["metadata"]["result_card"]["details"][-2:] == direct_rows
    del restored[ATTACK]["metadata"]["result_card"]["details"][-2:]
    assert restored[COMPLETE]["effects"].pop(feature_positions[0]) == feedback
    assert restored == before, "Only save receipt exports/defaults/card rows may change."
    assert all(tools[name] == original for name, original in protected.items())
    return [*names, INITIALIZE]
