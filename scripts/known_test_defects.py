"""Recorded baseline defects; ordinary skips never satisfy a required check."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KnownDefect:
    reason: str
    reproduction: str
    category: str = "suspected product defect"


KNOWN_DEFECTS: dict[str, KnownDefect] = {
    "tests/test_effective_interview_content.py::test_setting_selection_retains_its_rules_and_builder_sources_after_catalog_refresh": KnownDefect(
        "Refreshing the catalog rejects an explicitly retained older Setting selection",
        "Choose the original Setting in an interview, refresh the catalog to its updated identity, then capture the Builder launch references.",
    ),
    "tests/test_campaign_act_markers.py::test_markdown_only_plans_and_builder_chats_have_no_invented_acts": KnownDefect(
        "An unstructured new campaign history read queries an absent continuity table",
        "Create the ordinary markdown-only Builder-to-DM handoff, then read its history page.",
    ),
    "tests/test_campaign_plan_skill_examples.py::test_builder_retrieves_a_complete_valid_example_from_its_advertised_references": KnownDefect(
        "The advertised worked Plan reference has stale package bindings",
        "Load the Plan skill's combat/multi-act examples and compare/admit their bindings against the bundled source closure.",
    ),
    'tests/test_owned_package_updates.py::test_plan_adopts_complete_source_and_keeps_explicit_custom_declarations[setting]': KnownDefect(
        'An existing Setting source becomes unavailable when launching its Plan Builder after an update',
        'Install original and updated Setting sources, then launch a Plan Builder against the original saved candidate.',
    ),
    'tests/test_content_inheritance.py::test_plan_retains_its_copies_after_new_source_ruleset_is_installed': KnownDefect(
        "The Builder's advertised Plan example fails its declared content schema",
        'Build the advertised Plan example against its retained synthetic source Setting, then validate before saving its inherited copies.',
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_defense_boundaries.py::test_actual_guiding_bolt_shield_or_decline_reuses_scalar_roll_and_reopens[False]': KnownDefect(
        'The actual Guiding Bolt attack lacks retained target-AC evidence for the Shield boundary',
        'Cast Guiding Bolt against the admitted Wizard, inspect the saved scalar attack, then attempt the selected Shield/decline and reopen continuation.',
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_defense_boundaries.py::test_actual_guiding_bolt_shield_or_decline_reuses_scalar_roll_and_reopens[True]': KnownDefect(
        'The actual Guiding Bolt attack lacks retained target-AC evidence for the Shield boundary',
        'Cast Guiding Bolt against the admitted Wizard, inspect the saved scalar attack, then attempt the selected Shield/decline and reopen continuation.',
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_defense_boundaries.py::test_real_shield_survives_encounter_exit_then_expires_on_free_time_clock': KnownDefect(
        'The retained out-of-encounter Shield continuation is absent from the canonical tool catalog',
        "Complete the case's actual Shield window, exit the encounter and advance free time through the public MCP route before retry/reopen checks.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_defense_boundaries.py::test_scalar_cover_classification_survives_to_actual_ac_and_shield_boundary[half-18-False]': KnownDefect(
        'The actual scalar spell attack loses retained cover or target-AC evidence',
        'Resolve Guiding Bolt against the admitted Wizard with the selected actual cover geometry and inspect its saved cover/AC before the Shield boundary.',
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_defense_boundaries.py::test_scalar_cover_classification_survives_to_actual_ac_and_shield_boundary[none-16-False]': KnownDefect(
        'The actual scalar spell attack loses retained cover or target-AC evidence',
        'Resolve Guiding Bolt against the admitted Wizard with the selected actual cover geometry and inspect its saved cover/AC before the Shield boundary.',
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_defense_boundaries.py::test_scalar_cover_classification_survives_to_actual_ac_and_shield_boundary[three_quarters-21-True]': KnownDefect(
        'The actual scalar spell attack loses retained cover or target-AC evidence',
        'Resolve Guiding Bolt against the admitted Wizard with the selected actual cover geometry and inspect its saved cover/AC before the Shield boundary.',
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_defenses.py::test_shield_actual_split_magic_missile_allocation_keeps_other_recipient_damage': KnownDefect(
        'The admitted Shield flow lacks the retained saved-hit eligibility or recipient contract',
        "Execute the case's actual incoming attack or split missiles against the admitted Wizard and inspect the saved Shield eligibility/recipient before response and replay.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_defenses.py::test_shield_condition_critical_uses_saved_ordinary_roll_but_keeps_caster_legality': KnownDefect(
        'The admitted Shield flow lacks the retained saved-hit eligibility or recipient contract',
        "Execute the case's actual incoming attack or split missiles against the admitted Wizard and inspect the saved Shield eligibility/recipient before response and replay.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_defenses.py::test_shield_decline_resumes_original_saved_hit_without_payment': KnownDefect(
        'The admitted Shield flow lacks the retained saved-hit eligibility or recipient contract',
        "Execute the case's actual incoming attack or split missiles against the admitted Wizard and inspect the saved Shield eligibility/recipient before response and replay.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_defenses.py::test_shield_real_saved_hit_gate_same_roll_payment_and_reopen[5-0-False]': KnownDefect(
        'The admitted Shield flow lacks the retained saved-hit eligibility or recipient contract',
        "Execute the case's actual incoming attack or split missiles against the admitted Wizard and inspect the saved Shield eligibility/recipient before response and replay.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_defenses.py::test_shield_real_saved_hit_gate_same_roll_payment_and_reopen[8-0-False]': KnownDefect(
        'The admitted Shield flow lacks the retained saved-hit eligibility or recipient contract',
        "Execute the case's actual incoming attack or split missiles against the admitted Wizard and inspect the saved Shield eligibility/recipient before response and replay.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_defenses.py::test_shield_real_saved_hit_gate_same_roll_payment_and_reopen[8-30-False]': KnownDefect(
        'The admitted Shield flow lacks the retained saved-hit eligibility or recipient contract',
        "Execute the case's actual incoming attack or split missiles against the admitted Wizard and inspect the saved Shield eligibility/recipient before response and replay.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_defenses.py::test_shield_real_saved_hit_gate_same_roll_payment_and_reopen[8-6-True]': KnownDefect(
        'The admitted Shield flow lacks the retained saved-hit eligibility or recipient contract',
        "Execute the case's actual incoming attack or split missiles against the admitted Wizard and inspect the saved Shield eligibility/recipient before response and replay.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_missiles.py::test_missile_actual_range_and_bad_allocation_are_atomic': KnownDefect(
        'The admitted Magic Missile behavior differs from the retained per-dart/range contract',
        "Use the real public Magic Missile route with the case's split allocation, resistance, defeated recipient or invalid-range input; inspect atomicity and dart results.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_ray_reactions.py::test_actual_critical_ray_and_dead_later_target_skip_preserve_saved_rolls': KnownDefect(
        'The admitted ray cast does not retain the expected saved action/reaction window',
        "Begin the real ray attack against the admitted hostile Wizard and inspect its saved pending action before the case's Shield or critical-hit continuation.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_ray_reactions.py::test_actual_ray_shield_changes_saved_hit_then_resumes_order_without_repayment': KnownDefect(
        'The admitted ray cast does not retain the expected saved action/reaction window',
        "Begin the real ray attack against the admitted hostile Wizard and inspect its saved pending action before the case's Shield or critical-hit continuation.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_ray_reactions.py::test_hostile_wizard_natural_twenty_is_a_real_window_but_shield_cannot_convert_it': KnownDefect(
        'The admitted ray cast does not retain the expected saved action/reaction window',
        "Begin the real ray attack against the admitted hostile Wizard and inspect its saved pending action before the case's Shield or critical-hit continuation.",
        category='suspected retained contract discrepancy',
    ),
}


def case_key(nodeid):
    """Use pytest's JUnit classname/name spelling, including classes and parameters."""
    unparameterized, bracket, parameters = nodeid.partition("[")
    path, *parts = unparameterized.split("::")
    classname = path.removesuffix(".py").replace("/", ".")
    if len(parts) > 1:
        classname += "." + ".".join(parts[:-1])
    return classname + "::" + parts[-1] + bracket + parameters


def failure_reason(nodeid):
    return f"Known defect: {KNOWN_DEFECTS[nodeid].reason}"


def accepted_case(case):
    """Accept only a real strict-xfail outcome with its registered exact identity/reason."""
    if case.find("failure") is not None or case.find("error") is not None:
        return False
    skipped = case.find("skipped")
    if skipped is None:
        return True
    if skipped.get("type") != "pytest.xfail":
        return False
    key = case.get("classname", "") + "::" + case.get("name", "")
    return any(
        key == case_key(nodeid) and skipped.get("message") == failure_reason(nodeid)
        for nodeid in KNOWN_DEFECTS
    )


def report_outcomes(cases):
    """Keep expected failures separate from passes in the local gate receipt."""
    counts = dict.fromkeys(("passed", "failed", "errors", "expected_failures", "unexpected_skips"), 0)
    expected = []
    for case in cases:
        if case.find("failure") is not None:
            counts["failed"] += 1
        elif case.find("error") is not None:
            counts["errors"] += 1
        elif case.find("skipped") is not None:
            if accepted_case(case):
                counts["expected_failures"] += 1
                key = case.get("classname", "") + "::" + case.get("name", "")
                nodeid = next(nodeid for nodeid in KNOWN_DEFECTS if case_key(nodeid) == key)
                expected.append({
                    "nodeid": nodeid, "reason": KNOWN_DEFECTS[nodeid].reason,
                    "category": KNOWN_DEFECTS[nodeid].category,
                })
            else:
                counts["unexpected_skips"] += 1
        else:
            counts["passed"] += 1
    return counts, expected
