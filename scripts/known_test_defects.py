"""Recorded baseline defects; ordinary skips never satisfy a required check."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KnownDefect:
    reason: str
    reproduction: str
    category: str = "suspected product defect"


KNOWN_DEFECTS: dict[str, KnownDefect] = {
    "tests/test_migrated_plan_lifecycle.py::test_retained_rift_legacy_reusable_copy_resolves_its_exact_available_snapshot": KnownDefect(
        "Builder reference capture rejects the available snapshot of a legacy reusable Rift Plan",
        "Save the legacy Rift Plan without a duplicate package snapshot, resolve its retained packages, then capture its Builder source references from a fresh owner.",
    ),
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
    'tests/test_display_campaign_reads.py::test_display_state_skips_history_and_paged_controls_match_complete_projection': KnownDefect(
        'Ordinary panel/action display still reads the complete engine snapshot',
        'Create the synthetic moved campaign, reject full snapshots, then request its ordinary panels and action availability.',
    ),
    'tests/test_srd_creature_space_detours.py::test_open_destination_beyond_hostile_has_a_legal_affordable_detour': KnownDefect(
        'The retained movement action does not provide the expected affordable-detour preview/confirmation contract',
        'Start the admitted Lite movement scene with a hostile body between the actor and destination; request its move-walk preview and continuation.',
    ),
    'tests/test_srd_creature_space_detours.py::test_unaffordable_destination_does_not_commit_a_partial_detour': KnownDefect(
        'The retained movement action does not provide the expected affordable-detour preview/confirmation contract',
        'Start the admitted Lite movement scene with a hostile body between the actor and destination; request its move-walk preview and continuation.',
    ),
    'tests/test_srd_creature_space_route_continuation.py::test_detour_reaction_keeps_final_destination_and_reopens_before_resuming': KnownDefect(
        'The retained movement action does not provide the expected affordable-detour preview/confirmation contract',
        'Start the admitted Lite movement scene with a hostile body between the actor and destination; request its move-walk preview and continuation.',
    ),
    'tests/test_result_presentation_completion.py::test_stable_recovery_card_keeps_actual_recovery_and_clock[1-Recovery time advanced-0]': KnownDefect(
        'Selected admitted recovery/escape/factual operations omit the expected committed result card',
        "Run the committed Lite recovery, Freedom of Movement escape and factual-result scenarios; inspect each committed receipt's result_card.",
    ),
    'tests/test_result_presentation_completion.py::test_stable_recovery_card_keeps_actual_recovery_and_clock[2-Regained 1 HP-1]': KnownDefect(
        'Selected admitted recovery/escape/factual operations omit the expected committed result card',
        "Run the committed Lite recovery, Freedom of Movement escape and factual-result scenarios; inspect each committed receipt's result_card.",
    ),
    'tests/test_result_presentation_completion.py::test_freedom_escape_card_records_actual_removed_condition_and_movement[grappled]': KnownDefect(
        'Selected admitted recovery/escape/factual operations omit the expected committed result card',
        "Run the committed Lite recovery, Freedom of Movement escape and factual-result scenarios; inspect each committed receipt's result_card.",
    ),
    'tests/test_result_presentation_completion.py::test_freedom_escape_card_records_actual_removed_condition_and_movement[restrained]': KnownDefect(
        'Selected admitted recovery/escape/factual operations omit the expected committed result card',
        "Run the committed Lite recovery, Freedom of Movement escape and factual-result scenarios; inspect each committed receipt's result_card.",
    ),
    'tests/test_result_presentation_completion.py::test_supported_factual_results_retain_exact_committed_public_values': KnownDefect(
        'Selected admitted recovery/escape/factual operations omit the expected committed result card',
        "Run the committed Lite recovery, Freedom of Movement escape and factual-result scenarios; inspect each committed receipt's result_card.",
    ),
    'tests/test_srd_lite_false_life_diagnosis.py::test_actual_false_life_rejection_diagnostic': KnownDefect(
        'The selected admitted False Life owner rolls dice instead of the authored fixed Lite pool',
        'Cast first-level False Life in the default admitted candidate while guarding against RNG calls; the fixed-pool diagnostic fails.',
    ),
    'tests/test_srd_lite_insect_zone.py::test_insects_upcast_start_first_entry_terrain_perception_expiry_and_replay': KnownDefect(
        'The selected admitted Insect Plague owner accepts an input the Lite range/component contract rejects',
        'Attempt the default admitted Insect Plague cast at 65 feet and with unavailable verbal/somatic components.',
    ),
    'tests/test_owned_package_updates.py::test_plan_adopts_complete_source_and_keeps_explicit_custom_declarations[setting]': KnownDefect(
        'An existing Setting source becomes unavailable when launching its Plan Builder after an update',
        'Install original and updated Setting sources, then launch a Plan Builder against the original saved candidate.',
    ),
    'tests/test_srd_lite_progression_cantrips_c.py::test_complete_acolyte_thaumaturgy_mending_world_consumers_cost_expiry_and_reopen': KnownDefect(
        'Mending does not retain the expected unselected object-break records in the admitted scene',
        'Record three breaks on the synthetic relic, complete the actual Mending cast for one, then inspect its remaining break metadata.',
    ),
    'tests/test_srd_lite_multicaster_persistence.py::test_independent_caster_survives_other_source_end_then_expires_and_reopens[true_strike-2]': KnownDefect(
        'The selected admitted True Strike owner lacks the expected independently retained concentration source',
        "Cast True Strike from two admitted casters, end the first source and check the other's retained source, expiry and reopen state.",
    ),
    'tests/test_srd_creature_space_review.py::test_public_preview_does_not_disclose_hidden_scene_changes[blocking]': KnownDefect(
        'Authoritative movement through a hidden blocking body spends the straight-line cost',
        'Compare plain and hidden-blocker player previews, then commit the authoritative move and inspect its actual movement debit.',
    ),
    'tests/test_srd_creature_space_review.py::test_halfling_public_preview_commit_cost_endpoint_and_reopen': KnownDefect(
        'The admitted Halfling movement owner accepts the occupied endpoint the retained rule rejects',
        "Start the declared Halfling/guard scene and request the guard's occupied position before testing passage and reopen.",
    ),
    'tests/test_gcs_message_presentation.py::test_composer_sends_one_typed_turn_and_clears_only_accepted_draft[failed_after_accept]': KnownDefect(
        'A provider failure after accepted input leaves the already accepted composer draft',
        'Have submit_session_input retain the input then fail; return that message in session_context and inspect the original draft after final repaint.',
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
