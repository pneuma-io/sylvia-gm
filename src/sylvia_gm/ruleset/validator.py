"""Cross-reference validation for ruleset files.

Runs after Pydantic schema validation. Checks consistency between
feature toggles, ability references, duplicate names, and more.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sylvia_gm.ruleset.schema import ValidationIssue

if TYPE_CHECKING:
    from sylvia_gm.ruleset.schema import RulesetFile


VALID_CLEAR_KEYWORDS: set[str] = {
    "long_rest",
    "short_rest",
    "save_each_turn",
    "next_turn",
    "magic",
    "fulfill_need",
    "permanent",
    "instant",
    "damage",
}


def validate_ruleset_consistency(ruleset: RulesetFile) -> list[ValidationIssue]:
    """Run all cross-reference validation checks.

    Returns a list of ValidationIssue objects. An empty list = no issues.
    """
    issues: list[ValidationIssue] = []
    active_abbrs = set(ruleset.active_abilities())
    all_abbrs = set(ruleset.abilities.keys())
    condition_names = {c.name.lower() for c in ruleset.conditions}

    # 1. Skills reference active abilities (M2)
    _check_skill_ability_refs(ruleset, all_abbrs, active_abbrs, issues)

    # 2. No duplicate skill names (M1)
    _check_duplicate_skills(ruleset, issues)

    # 3. Modifier formula syntax check when a package declares one
    _check_modifier_formulas(ruleset, issues)

    # 4. Per-condition cleared_by references (M5)
    _check_condition_cleared_by_refs(ruleset, condition_names, issues)

    # 5. Duplicate condition names (M5)
    _check_duplicate_conditions(ruleset, issues)

    # 6. T1.4 passthrough presence check (informational)
    _check_t1_4_passthrough(ruleset, issues)

    return issues


# ── Individual Check Functions ────────────────────────────────────────────────


def _check_skill_ability_refs(
    ruleset: RulesetFile,
    all_abbrs: set[str],
    active_abbrs: set[str],
    issues: list[ValidationIssue],
) -> None:
    for i, skill in enumerate(ruleset.skills):
        if skill.ability is None:
            continue
        if skill.ability not in all_abbrs:
            issues.append(
                ValidationIssue(
                    severity="error",
                    location=f"skills[{i}]",
                    message=(
                        f"Skill '{skill.name}' references ability '{skill.ability}' "
                        f"which is not defined. Defined abilities: {sorted(all_abbrs)}"
                    ),
                )
            )
        elif skill.ability not in active_abbrs:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    location=f"skills[{i}]",
                    message=(
                        f"Skill '{skill.name}' references ability '{skill.ability}' "
                        f"which is marked active: false."
                    ),
                )
            )


def _check_duplicate_skills(ruleset: RulesetFile, issues: list[ValidationIssue]) -> None:
    seen: dict[str, int] = {}
    for i, skill in enumerate(ruleset.skills):
        lower = skill.name.lower()
        if lower in seen:
            issues.append(
                ValidationIssue(
                    severity="error",
                    location=f"skills[{i}]",
                    message=(f"Duplicate skill name '{skill.name}' (also at index {seen[lower]})."),
                )
            )
        else:
            seen[lower] = i


def _check_modifier_formulas(ruleset: RulesetFile, issues: list[ValidationIssue]) -> None:
    for key, cfg in ruleset.abilities.items():
        if cfg.modifier_formula is None:
            continue
        try:
            from sylvia_gm.core.expression_eval import safe_eval  # noqa: PLC0415
        except ImportError:
            # T1.4 evaluator not yet built; skip syntax check
            continue

        try:
            safe_eval(cfg.modifier_formula, {"score": 10})
        except Exception as e:
            issues.append(
                ValidationIssue(
                    severity="error",
                    location=f"abilities.{key}.modifier_formula",
                    message=(f"Invalid modifier formula '{cfg.modifier_formula}': {e}"),
                )
            )


def _check_condition_cleared_by_refs(
    ruleset: RulesetFile,
    condition_names: set[str],
    issues: list[ValidationIssue],
) -> None:
    for cond in ruleset.conditions:
        for clear in cond.cleared_by:
            is_known = clear.lower() in VALID_CLEAR_KEYWORDS or clear.lower() in condition_names
            if not is_known:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        location=f"conditions.{cond.name}.cleared_by",
                        message=(
                            f"Clear mechanism '{clear}' is not a recognized keyword, "
                            f"condition, or spell. It may reference external content."
                        ),
                    )
                )


def _check_duplicate_conditions(ruleset: RulesetFile, issues: list[ValidationIssue]) -> None:
    seen: dict[str, int] = {}
    for i, cond in enumerate(ruleset.conditions):
        lower = cond.name.lower()
        if lower in seen:
            issues.append(
                ValidationIssue(
                    severity="error",
                    location=f"conditions[{i}]",
                    message=(f"Duplicate condition '{cond.name}' (also at index {seen[lower]})."),
                )
            )
        else:
            seen[lower] = i


def _check_t1_4_passthrough(ruleset: RulesetFile, issues: list[ValidationIssue]) -> None:
    if ruleset.tools is not None:
        issues.append(
            ValidationIssue(
                severity="info",
                location="tools",
                message=(
                    f"T1.4 executable layer 'tools' present "
                    f"({len(ruleset.tools)} entries). "
                    f"T1.2 performs no semantic validation on executable layer."
                ),
            )
        )
    if ruleset.procedures is not None:
        issues.append(
            ValidationIssue(
                severity="info",
                location="procedures",
                message=(
                    f"T1.4 executable layer 'procedures' present "
                    f"({len(ruleset.procedures)} entries)."
                ),
            )
        )
