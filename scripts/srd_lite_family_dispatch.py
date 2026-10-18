"""Omit root dispatch arms impossible for the closed Lite catalogue."""

from copy import deepcopy

from sylvia_gm.runtime._expression_parser import (
    BinaryOpNode,
    FieldAccessNode,
    LiteralNode,
    UnaryOpNode,
    parse_expression,
)

ROOTS = ("cast_spell", "begin_srd_spell_action", "finish_srd_retained_cast_tail")
FAMILY_PATH = [("_spell_definition", None), ("family", None)]


def _truth(node, family):
    """Resolve only family comparisons and Boolean composition; other facts stay unknown."""
    if isinstance(node, LiteralNode) and isinstance(node.value, bool):
        return node.value
    if isinstance(node, UnaryOpNode) and node.operator == "not":
        child = _truth(node.operand, family)
        return None if child is None else not child
    if not isinstance(node, BinaryOpNode):
        return None
    if node.operator in ("==", "!="):
        for field, literal in ((node.left, node.right), (node.right, node.left)):
            if (
                isinstance(field, FieldAccessNode)
                and field.segments == FAMILY_PATH
                and isinstance(literal, LiteralNode)
                and isinstance(literal.value, str)
            ):
                equal = family == literal.value
                return equal if node.operator == "==" else not equal
        return None
    left, right = _truth(node.left, family), _truth(node.right, family)
    if node.operator == "and":
        if left is False or right is False:
            return False
        return True if left is True and right is True else None
    if node.operator == "or":
        if left is True or right is True:
            return True
        return False if left is False and right is False else None
    return None


def omit_unselected_family_dispatch(runtime):
    """Prune only provably false family branches from the three selected-catalogue roots."""
    families = {row["family"] for row in runtime["definitions"]["spells"].values()}
    if not families:
        raise ValueError("Family pruning requires a nonempty closed spell catalogue")
    omitted = []

    def prune(effects, owner):
        retained = []
        for original in effects:
            row = deepcopy(original)
            condition = row.get("condition", "")
            if (
                "on_true" in row
                and isinstance(condition, str)
                and "_spell_definition.family" in condition
            ):
                parsed = parse_expression(condition)
                if all(_truth(parsed, family) is False for family in families):
                    omitted.append({"owner": owner, "condition": condition})
                    retained.extend(prune(row.get("on_false", []), owner))
                    continue
            for branch in ("on_true", "on_false"):
                if branch in row:
                    row[branch] = prune(row[branch], owner)
            if "for_each" in row:
                row["for_each"]["do"] = prune(row["for_each"]["do"], owner)
            retained.append(row)
        return retained

    for name in ROOTS:
        runtime["tools"][name]["effects"] = prune(runtime["tools"][name]["effects"], name)
    return omitted
