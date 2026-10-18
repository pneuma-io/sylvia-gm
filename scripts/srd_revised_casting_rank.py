"""Use the existing source rank resolution before canonical SRD cast spending.

Pending begins already enforce this rule. The accepted direct cast computes rank
after payment and omits the corresponding require. Move those exact existing
setters, including saved NPC and free/ritual overrides, before material payment
and retain every other canonical effect in its original relative order.
"""

from copy import deepcopy


RANK_CONDITION = "_slot_level >= _spell_definition.base_level"


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _is_rank_setter(effect):
    return effect.get("set", {}).get("field") == "_slot_level"


def _is_rank_guard(effect):
    return effect.get("require", {}).get("condition") == RANK_CONDITION


def _spends(effect):
    return any(row.get("call") in {"resolve_srd_spell_materials", "spend_spell_casting_budget"}
               for row in _walk(effect))


def bind_revised_casting_rank(runtime):
    """Repair only the shared direct-cast rank gate; reuse the accepted begin gate."""
    tools = runtime["tools"]
    effects = tools["cast_spell"]["effects"]
    begin = tools["begin_srd_spell_action"]["effects"]
    source_guards = [effect for effect in begin if _is_rank_guard(effect)]
    if len(source_guards) != 1:
        raise ValueError("Review the original pending-begin spell rank rule before composing it.")
    setters = [effect for effect in effects if _is_rank_setter(effect)]
    expected = [
        {"set": {"field": "_slot_level", "value": 0}},
        *[{"set": {"field": "_slot_level", "value": rank},
           "condition": f"_cast_resource == 'spell_slot_{rank}'"} for rank in range(1, 10)],
        {"set": {"field": "_slot_level", "value": "_npc_spell.casting_level"},
         "condition": "_casting_pc_actor != true"},
        {"set": {"field": "_slot_level", "value": "_spell_definition.base_level"},
         "condition": "_feature_cast == true"},
        {"set": {"field": "_slot_level", "value": "_spell_definition.base_level"},
         "condition": "_ritual_cast == true"},
    ]
    if setters != expected:
        raise ValueError("Review the actual canonical slot-resource/NPC/feature/ritual rank resolution.")
    spending = [i for i, effect in enumerate(effects) if _spends(effect)]
    if not spending:
        raise ValueError("The canonical SRD paid owner changed.")
    guards = [i for i, effect in enumerate(effects) if _is_rank_guard(effect)]
    if len(guards) > 1:
        raise ValueError("The canonical SRD spell rank rule is duplicated.")
    setter_indices = [i for i, effect in enumerate(effects) if _is_rank_setter(effect)]
    if guards and max(setter_indices) < guards[0] < min(spending):
        return {"applied": False, "reason": "existing canonical pre-cost rank gate", "paid_owner": "cast_spell"}
    # Preserve and move the source's actual rank effects, not a guessed resource
    # table. In the accepted direct cast no other effect reads _slot_level before
    # the final setter, so resolving it earlier does not alter valid calculations.
    final_setter = max(setter_indices)
    if any("_slot_level" in str(effect) and not _is_rank_setter(effect)
           and not _is_rank_guard(effect) for effect in effects[:final_setter + 1]):
        raise ValueError("Review an intervening canonical slot-rank consumer before moving the setters.")
    remaining = [effect for effect in effects if not _is_rank_setter(effect) and not _is_rank_guard(effect)]
    join = next(i for i, effect in enumerate(remaining) if _spends(effect))
    dependencies = {"_cast_resource", "_npc_spell", "_casting_pc_actor", "_feature_cast", "_ritual_cast", "_spell_definition"}
    if any(row.get("set", {}).get("field") in dependencies
           for effect in remaining[join:] for row in _walk(effect)):
        raise ValueError("Review a rank-input mutation after the proposed pre-cost join.")
    guard = deepcopy(effects[guards[0]] if guards else source_guards[0])
    effects[:] = remaining[:join] + setters + [guard] + remaining[join:]
    return {"applied": True, "paid_owner": "cast_spell", "moved_source_rank_setters": len(setters),
            "guard_source": "begin_srd_spell_action", "guard_before_material_and_budget": True,
            "unchanged_rank_inputs": ["normalized resource", "saved NPC casting level", "feature source level", "ritual source level"],
            "new_tools_or_parameters": 0}
