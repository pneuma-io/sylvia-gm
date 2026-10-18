"""Preserve selected spell paths while removing impossible root family arms."""

import ast
import os
import zipfile
from copy import deepcopy

import pytest
from scripts.srd_lite_family_dispatch import ROOTS, omit_unselected_family_dispatch
from scripts.srd_timed_actor_defenses import _load_runtime
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE


def possibilities(node, family):
    """Independent Python grammar proof: other facts can take either Boolean value."""
    if isinstance(node, ast.BoolOp):
        values = {isinstance(node.op, ast.And)}
        for child in node.values:
            candidates = possibilities(child, family)
            values = {
                (left and right) if isinstance(node.op, ast.And) else (left or right)
                for left in values
                for right in candidates
            }
        return values
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return {not value for value in possibilities(node.operand, family)}
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        for field, literal in ((node.left, node.comparators[0]), (node.comparators[0], node.left)):
            if (
                isinstance(field, ast.Attribute)
                and isinstance(field.value, ast.Name)
                and field.value.id == "_spell_definition"
                and field.attr == "family"
                and isinstance(literal, ast.Constant)
                and isinstance(literal.value, str)
            ):
                if isinstance(node.ops[0], ast.Eq):
                    return {family == literal.value}
                if isinstance(node.ops[0], ast.NotEq):
                    return {family != literal.value}
    return {False, True}


def specialize(effects, family):
    result = []
    for original in effects:
        row = deepcopy(original)
        condition = row.get("condition", "")
        if (
            "on_true" in row
            and "_spell_definition.family" in condition
            and possibilities(ast.parse(condition, mode="eval").body, family) == {False}
        ):
            result.extend(specialize(row.get("on_false", []), family))
            continue
        for branch in ("on_true", "on_false"):
            if branch in row:
                row[branch] = specialize(row[branch], family)
        if "for_each" in row:
            row["for_each"]["do"] = specialize(row["for_each"]["do"], family)
        result.append(row)
    return result


def test_all_96_selected_paths_profiles_headers_and_paid_owners_preserved():
    with zipfile.ZipFile(os.environ["SRD_LITE_CANDIDATE"]) as archive:
        data = _load_runtime(
            archive.read("packages/" + NAMESPACE + "/runtime/ruleset.yaml").decode()
        )
    before = deepcopy(data)
    omitted = omit_unselected_family_dispatch(data)
    assert len(before["definitions"]["spells"]) == 96
    assert omitted
    assert data["definitions"] == before["definitions"]
    for name, owner in before["tools"].items():
        if name not in ROOTS:
            assert data["tools"][name] == owner, name
    for row in omitted:
        for profile in before["definitions"]["spells"].values():
            assert possibilities(
                ast.parse(row["condition"], mode="eval").body, profile["family"]
            ) == {False}
    for name in ROOTS:
        for spell, profile in before["definitions"]["spells"].items():
            assert specialize(before["tools"][name]["effects"], profile["family"]) == specialize(
                data["tools"][name]["effects"], profile["family"]
            ), (name, spell)
    print("Impossible selected-family root branches removed:", len(omitted))


@pytest.mark.parametrize(
    ("condition", "removed"),
    [
        ("unknown == true and _spell_definition.family == 'absent'", True),
        ("unknown == true or _spell_definition.family == 'absent'", False),
        ("_spell_definition.family == 'absent' or _spell_definition.family == 'selected'", False),
        ("not (_spell_definition.family != 'absent')", True),
        ("other.family == 'absent'", False),
    ],
)
def test_unknown_facts_are_never_assumed_false(condition, removed):
    row = {"condition": condition, "on_true": [{"set": {"field": "_kept", "value": 1}}]}
    data = {
        "definitions": {"spells": {"example": {"family": "selected"}}},
        "tools": {name: {"effects": [deepcopy(row)]} for name in ROOTS},
    }
    omitted = omit_unselected_family_dispatch(data)
    assert bool(omitted) is removed
    for owner in data["tools"].values():
        assert owner["effects"] == ([] if removed else [row])
