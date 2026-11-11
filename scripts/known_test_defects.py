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
    'tests/test_gcs_message_presentation.py::test_composer_sends_one_typed_turn_and_clears_only_accepted_draft[failed_after_accept]': KnownDefect(
        'A provider failure after accepted input leaves the already accepted composer draft',
        'Have submit_session_input retain the input then fail; return that message in session_context and inspect the original draft after final repaint.',
    ),
    'tests/test_alpha_05_rift_journey.py::test_rift_quick_start_customize_mcp_and_reopen': KnownDefect(
        'The real Rift provider request lacks the encounter actor context expected by the retained journey',
        'Create the actual Rift owner, commit its opening, prepare the carried weapon through the declared control, then request combat; the scripted provider cannot find Riftwolf in the real request. Context-versus-fixture attribution remains open.',
        category='unresolved context or fixture discrepancy',
    ),
    'tests/test_alpha_manual_campaign_plan.py::test_creation_catalog_keeps_current_bundle_over_stale_equal_version_closure': KnownDefect(
        'The creation catalog prefers an older same-version bundled Plan closure to current source',
        'Present current source and its stale equal-version bundled Plan closure, then inspect which Ruleset identity the campaign creation catalog selects.',
        category='suspected retained contract discrepancy',
    ),
    'tests/test_content_inheritance.py::test_plan_retains_its_copies_after_new_source_ruleset_is_installed': KnownDefect(
        "The Builder's advertised Plan example fails its declared content schema",
        'Build the advertised Plan example against its retained synthetic source Setting, then validate before saving its inherited copies.',
        category='suspected retained contract discrepancy',
    ),
    'tests/test_false_life_native_provenance.py::test_actual_overlap_pool_winner_retirement_and_reopen[sorcerer-wizard-spell_slot_1-sorcerer-7]': KnownDefect(
        'The admitted False Life behavior lacks the retained fixed-pool/source-retirement contract',
        "Use the public False Life cast and the case's pool overlap, attack, rest or source-replacement flow; inspect its saved source and reopen state.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_false_life_native_provenance.py::test_actual_overlap_pool_winner_retirement_and_reopen[sorcerer-wizard-spell_slot_2-wizard-0]': KnownDefect(
        'The admitted False Life behavior lacks the retained fixed-pool/source-retirement contract',
        "Use the public False Life cast and the case's pool overlap, attack, rest or source-replacement flow; inspect its saved source and reopen state.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_false_life_native_provenance.py::test_actual_overlap_pool_winner_retirement_and_reopen[wizard-sorcerer-spell_slot_1-wizard-0]': KnownDefect(
        'The admitted False Life behavior lacks the retained fixed-pool/source-retirement contract',
        "Use the public False Life cast and the case's pool overlap, attack, rest or source-replacement flow; inspect its saved source and reopen state.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_false_life_native_provenance.py::test_public_attack_exhaustion_clears_source_immediately': KnownDefect(
        'The admitted False Life behavior lacks the retained fixed-pool/source-retirement contract',
        "Use the public False Life cast and the case's pool overlap, attack, rest or source-replacement flow; inspect its saved source and reopen state.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_false_life_native_provenance.py::test_public_earned_long_rest_clears_source': KnownDefect(
        'The admitted False Life behavior lacks the retained fixed-pool/source-retirement contract',
        "Use the public False Life cast and the case's pool overlap, attack, rest or source-replacement flow; inspect its saved source and reopen state.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_false_life_native_provenance.py::test_public_focus_replacement_clears_source_before_wizard_retirement': KnownDefect(
        'The admitted False Life behavior lacks the retained fixed-pool/source-retirement contract',
        "Use the public False Life cast and the case's pool overlap, attack, rest or source-replacement flow; inspect its saved source and reopen state.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_integrated_worker_seams.py::test_area_damage_card_exposes_the_actual_concentration_roll': KnownDefect(
        'An area-damage card emits two concentration rows for one actual saving throw',
        'Damage the real concentrating caster with the admitted area action and inspect its actual result-card concentration rows.',
        category='suspected retained contract discrepancy',
    ),
    'tests/test_public_lite_descendants.py::test_every_public_lite_plan_selects_current_exact_mechanics': KnownDefect(
        'Bundled public Plans retain an older same-version Ruleset closure than current source',
        "Compare each bundled Plan's exact Ruleset closure with the current public Lite package and its selected mechanics.",
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
    'tests/test_srd_lite_defense_boundaries.py::test_mage_actual_plane_and_blocked_touch_path_before_payment[clear-begin_srd_spell_action]': KnownDefect(
        'A valid Mage Armor effect lacks the retained until-Long-Rest lifetime metadata',
        "Cast against the actual same-plane willing recipient through the selected route and inspect the saved effect's lifetime before reopen.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_defense_boundaries.py::test_mage_actual_plane_and_blocked_touch_path_before_payment[clear-cast_spell_outside_combat]': KnownDefect(
        'A valid Mage Armor effect lacks the retained until-Long-Rest lifetime metadata',
        "Cast against the actual same-plane willing recipient through the selected route and inspect the saved effect's lifetime before reopen.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_defense_boundaries.py::test_mage_actual_plane_and_blocked_touch_path_before_payment[other_plane-cast_spell_outside_combat]': KnownDefect(
        'A free-exploration Mage Armor cast accepts an actual plane/path obstruction',
        "Admit the case's other-plane recipient or intervening wall, make a false clear-path/touch claim, then attempt the public free-exploration Mage Armor cast.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_defense_boundaries.py::test_mage_actual_plane_and_blocked_touch_path_before_payment[wall-cast_spell_outside_combat]': KnownDefect(
        'A free-exploration Mage Armor cast accepts an actual plane/path obstruction',
        "Admit the case's other-plane recipient or intervening wall, make a false clear-path/touch claim, then attempt the public free-exploration Mage Armor cast.",
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
    'tests/test_srd_lite_defenses.py::test_mage_actual_formula_paid_cost_no_roll_contact_and_reopen[1]': KnownDefect(
        'The admitted Mage Armor effect lacks the retained preparation-source or until-Long-Rest lifetime contract',
        "Cast valid Mage Armor with carried Materials and contact facts, then inspect its saved source/lifetime before the case's equip, unprepare, rest or reopen continuation.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_defenses.py::test_mage_actual_formula_paid_cost_no_roll_contact_and_reopen[3]': KnownDefect(
        'The admitted Mage Armor effect lacks the retained preparation-source or until-Long-Rest lifetime contract',
        "Cast valid Mage Armor with carried Materials and contact facts, then inspect its saved source/lifetime before the case's equip, unprepare, rest or reopen continuation.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_defenses.py::test_mage_elapsed_time_short_failed_rest_and_successful_long_rest': KnownDefect(
        'The admitted Mage Armor effect lacks the retained preparation-source or until-Long-Rest lifetime contract',
        "Cast valid Mage Armor with carried Materials and contact facts, then inspect its saved source/lifetime before the case's equip, unprepare, rest or reopen continuation.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_defenses.py::test_mage_real_armor_equip_permanently_retires_and_old_retry_cannot_retire_new_cast': KnownDefect(
        'The admitted Mage Armor effect lacks the retained preparation-source or until-Long-Rest lifetime contract',
        "Cast valid Mage Armor with carried Materials and contact facts, then inspect its saved source/lifetime before the case's equip, unprepare, rest or reopen continuation.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_defenses.py::test_mage_real_far_target_rejects_before_spend_even_with_touch_claim[cast_spell_outside_combat]': KnownDefect(
        'A free-exploration Mage Armor cast accepts a false touch claim for an actual distant recipient',
        'Place the validated companion 30 feet away, claim distance zero/touch true, then attempt the public free-exploration Mage Armor cast and compare payment/state.',
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_defenses.py::test_mage_same_caster_two_real_grants_do_not_lose_independent_ownership': KnownDefect(
        'The admitted Mage Armor effect lacks the retained preparation-source or until-Long-Rest lifetime contract',
        "Cast valid Mage Armor with carried Materials and contact facts, then inspect its saved source/lifetime before the case's equip, unprepare, rest or reopen continuation.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_defenses.py::test_mage_trained_shield_and_stronger_real_unarmored_base_feed_attack[False]': KnownDefect(
        'The admitted Mage Armor effect lacks the retained preparation-source or until-Long-Rest lifetime contract',
        "Cast valid Mage Armor with carried Materials and contact facts, then inspect its saved source/lifetime before the case's equip, unprepare, rest or reopen continuation.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_defenses.py::test_mage_trained_shield_and_stronger_real_unarmored_base_feed_attack[True]': KnownDefect(
        'The admitted Mage Armor effect lacks the retained preparation-source or until-Long-Rest lifetime contract',
        "Cast valid Mage Armor with carried Materials and contact facts, then inspect its saved source/lifetime before the case's equip, unprepare, rest or reopen continuation.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_defenses.py::test_mage_two_casters_exact_unprepare_and_free_dismiss_replay': KnownDefect(
        'The admitted Mage Armor effect lacks the retained preparation-source or until-Long-Rest lifetime contract',
        "Cast valid Mage Armor with carried Materials and contact facts, then inspect its saved source/lifetime before the case's equip, unprepare, rest or reopen continuation.",
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
    'tests/test_srd_lite_false_life.py::test_fixed_pool_does_not_roll_or_stack_and_retries_persist[1-0-7]': KnownDefect(
        'The admitted False Life behavior differs from the retained fixed-pool/rest contract',
        "Cast False Life through the actual admitted owner, then execute the case's dice guard, attack or earned-rest step and inspect its pool/source.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_false_life.py::test_fixed_pool_does_not_roll_or_stack_and_retries_persist[1-20-20]': KnownDefect(
        'The admitted False Life behavior differs from the retained fixed-pool/rest contract',
        "Cast False Life through the actual admitted owner, then execute the case's dice guard, attack or earned-rest step and inspect its pool/source.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_false_life.py::test_fixed_pool_does_not_roll_or_stack_and_retries_persist[3-0-17]': KnownDefect(
        'The admitted False Life behavior differs from the retained fixed-pool/rest contract',
        "Cast False Life through the actual admitted owner, then execute the case's dice guard, attack or earned-rest step and inspect its pool/source.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_false_life.py::test_genuine_long_rest_clears_the_fixed_pool': KnownDefect(
        'The admitted False Life behavior differs from the retained fixed-pool/rest contract',
        "Cast False Life through the actual admitted owner, then execute the case's dice guard, attack or earned-rest step and inspect its pool/source.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_false_life.py::test_real_creature_attack_consumes_false_life_pool_before_hp': KnownDefect(
        'The admitted False Life behavior differs from the retained fixed-pool/rest contract',
        "Cast False Life through the actual admitted owner, then execute the case's dice guard, attack or earned-rest step and inspect its pool/source.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_false_life.py::test_short_rest_preparation_clears_only_false_life_owned_pool[False]': KnownDefect(
        'The admitted False Life behavior differs from the retained fixed-pool/rest contract',
        "Cast False Life through the actual admitted owner, then execute the case's dice guard, attack or earned-rest step and inspect its pool/source.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_false_life.py::test_short_rest_preparation_clears_only_false_life_owned_pool[True]': KnownDefect(
        'The admitted False Life behavior differs from the retained fixed-pool/rest contract',
        "Cast False Life through the actual admitted owner, then execute the case's dice guard, attack or earned-rest step and inspect its pool/source.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_mage_alias.py::test_mage_advertised_alias_has_canonical_saved_source_and_once_only_cost[mage_armor-begin_srd_spell_action]': KnownDefect(
        'The admitted Mage Armor alias lacks the retained preparation-source/lifetime contract',
        'Cast the named Mage Armor alias through the selected public route, reopen/retry it, and inspect its canonical source and lifetime metadata.',
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_mage_alias.py::test_mage_advertised_alias_has_canonical_saved_source_and_once_only_cost[mage_armor-cast_spell]': KnownDefect(
        'The admitted Mage Armor alias lacks the retained preparation-source/lifetime contract',
        'Cast the named Mage Armor alias through the selected public route, reopen/retry it, and inspect its canonical source and lifetime metadata.',
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_mage_alias.py::test_mage_advertised_alias_has_canonical_saved_source_and_once_only_cost[mage_armor-cast_spell_outside_combat]': KnownDefect(
        'The admitted Mage Armor alias lacks the retained preparation-source/lifetime contract',
        'Cast the named Mage Armor alias through the selected public route, reopen/retry it, and inspect its canonical source and lifetime metadata.',
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_mage_alias.py::test_mage_advertised_alias_has_canonical_saved_source_and_once_only_cost[mage_armour-begin_srd_spell_action]': KnownDefect(
        'The admitted Mage Armor alias lacks the retained preparation-source/lifetime contract',
        'Cast the named Mage Armor alias through the selected public route, reopen/retry it, and inspect its canonical source and lifetime metadata.',
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_mage_alias.py::test_mage_advertised_alias_has_canonical_saved_source_and_once_only_cost[mage_armour-cast_spell]': KnownDefect(
        'The admitted Mage Armor alias lacks the retained preparation-source/lifetime contract',
        'Cast the named Mage Armor alias through the selected public route, reopen/retry it, and inspect its canonical source and lifetime metadata.',
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_mage_alias.py::test_mage_advertised_alias_has_canonical_saved_source_and_once_only_cost[mage_armour-cast_spell_outside_combat]': KnownDefect(
        'The admitted Mage Armor alias lacks the retained preparation-source/lifetime contract',
        'Cast the named Mage Armor alias through the selected public route, reopen/retry it, and inspect its canonical source and lifetime metadata.',
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_mage_alias.py::test_mage_alias_keeps_actual_armor_target_rejection_atomic': KnownDefect(
        'A free-exploration Mage Armor alias accepts the actually armored target',
        'Admit the caster wearing leather, cast the public mage_armour alias and require atomic rejection without a retained effect.',
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_missiles.py::test_later_allocated_darts_do_not_rollback_a_target_killed_by_the_first': KnownDefect(
        'The admitted Magic Missile behavior differs from the retained per-dart/range contract',
        "Use the real public Magic Missile route with the case's split allocation, resistance, defeated recipient or invalid-range input; inspect atomicity and dart results.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_missiles.py::test_missile_actual_range_and_bad_allocation_are_atomic': KnownDefect(
        'The admitted Magic Missile behavior differs from the retained per-dart/range contract',
        "Use the real public Magic Missile route with the case's split allocation, resistance, defeated recipient or invalid-range input; inspect atomicity and dart results.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_missiles.py::test_split_force_darts_roll_each_face_and_apply_resistance_per_dart[1]': KnownDefect(
        'The admitted Magic Missile behavior differs from the retained per-dart/range contract',
        "Use the real public Magic Missile route with the case's split allocation, resistance, defeated recipient or invalid-range input; inspect atomicity and dart results.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_missiles.py::test_split_force_darts_roll_each_face_and_apply_resistance_per_dart[3]': KnownDefect(
        'The admitted Magic Missile behavior differs from the retained per-dart/range contract',
        "Use the real public Magic Missile route with the case's split allocation, resistance, defeated recipient or invalid-range input; inspect atomicity and dart results.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_mobility_support.py::test_actual_fall_death_retires_only_the_dead_longstrider_recipient': KnownDefect(
        'The admitted mobility spell behavior differs from the retained Lite cost/source/lifetime contract',
        "Cast the selected Longstrider or Enhance Leap through the public owner and exercise the case's movement, payment, dismissal, preparation or earned-rest boundary.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_mobility_support.py::test_enhance_leap_alias_actual_touch_nonstacking_and_finite_expiry_survive_replay': KnownDefect(
        'The admitted mobility spell behavior differs from the retained Lite cost/source/lifetime contract',
        "Cast the selected Longstrider or Enhance Leap through the public owner and exercise the case's movement, payment, dismissal, preparation or earned-rest boundary.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_mobility_support.py::test_enhance_leap_triples_ordinary_jump_reach_and_spends_actual_distance': KnownDefect(
        'The admitted mobility spell behavior differs from the retained Lite cost/source/lifetime contract',
        "Cast the selected Longstrider or Enhance Leap through the public owner and exercise the case's movement, payment, dismissal, preparation or earned-rest boundary.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_mobility_support.py::test_exact_longstrider_dismissal_preserves_self_and_guards_unresolved_recipient_rest': KnownDefect(
        'The admitted mobility spell behavior differs from the retained Lite cost/source/lifetime contract',
        "Cast the selected Longstrider or Enhance Leap through the public owner and exercise the case's movement, payment, dismissal, preparation or earned-rest boundary.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_mobility_support.py::test_free_exploration_longstrider_level_three_keeps_targets_cost_card_and_retry': KnownDefect(
        'The admitted mobility spell behavior differs from the retained Lite cost/source/lifetime contract',
        "Cast the selected Longstrider or Enhance Leap through the public owner and exercise the case's movement, payment, dismissal, preparation or earned-rest boundary.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_mobility_support.py::test_free_longstrider_shape_uses_saved_capacity_without_spending_exhausted_slots': KnownDefect(
        'The admitted mobility spell behavior differs from the retained Lite cost/source/lifetime contract',
        "Cast the selected Longstrider or Enhance Leap through the public owner and exercise the case's movement, payment, dismissal, preparation or earned-rest boundary.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_mobility_support.py::test_paid_longstrider_rejects_invalid_begin_and_completes_once_after_reopen': KnownDefect(
        'The admitted mobility spell behavior differs from the retained Lite cost/source/lifetime contract',
        "Cast the selected Longstrider or Enhance Leap through the public owner and exercise the case's movement, payment, dismissal, preparation or earned-rest boundary.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_mobility_support.py::test_self_longstrider_expires_only_when_an_earned_long_rest_completes': KnownDefect(
        'The admitted mobility spell behavior differs from the retained Lite cost/source/lifetime contract',
        "Cast the selected Longstrider or Enhance Leap through the public owner and exercise the case's movement, payment, dismissal, preparation or earned-rest boundary.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_mobility_support.py::test_short_rest_wizard_replacement_ends_longstrider_despite_other_prepared_grant': KnownDefect(
        'The admitted mobility spell behavior differs from the retained Lite cost/source/lifetime contract',
        "Cast the selected Longstrider or Enhance Leap through the public owner and exercise the case's movement, payment, dismissal, preparation or earned-rest boundary.",
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
    'tests/test_srd_lite_travel_support.py::test_misty_step_exact_scene_geometry_paid_bonus_pending_retry_and_reopen': KnownDefect(
        'The admitted Misty Step route rejects a retained geometry premise with different validation',
        "Exercise Misty Step with the case's admitted scene and horizontal/downhill/upward or pending-cast inputs; inspect the actual refusal before its paid/reopen continuation.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_travel_support.py::test_misty_step_horizontal_downhill_extension_upward_range_and_no_upcast_gain': KnownDefect(
        'The admitted Misty Step route rejects a retained geometry premise with different validation',
        "Exercise Misty Step with the case's admitted scene and horizontal/downhill/upward or pending-cast inputs; inspect the actual refusal before its paid/reopen continuation.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_utility_support.py::test_animals_combat_paid_pending_cast_speech_action_and_real_rest': KnownDefect(
        'The admitted utility spell behavior differs from retained self/range/source/lifetime boundaries',
        "Cast Darkvision or Speak with Animals through the actual admitted owner using the case's target boundary, paid cast, death, unprepare or earned-rest flow.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_utility_support.py::test_animals_exploration_ritual_actual_beast_dialogue_nonstacking_and_unprepare': KnownDefect(
        'A self-only Speak with Animals cast accepts another selected recipient',
        'Without an active grant, choose guard instead of the caster as the public Speak with Animals target and require atomic rejection before the ritual continuation.',
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_utility_support.py::test_darkvision_paid_pending_cast_real_sight_range_replay_and_unprepare': KnownDefect(
        'The admitted utility spell behavior differs from retained self/range/source/lifetime boundaries',
        "Cast Darkvision or Speak with Animals through the actual admitted owner using the case's target boundary, paid cast, death, unprepare or earned-rest flow.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_utility_support.py::test_darkvision_receiving_creature_actual_death_removes_saved_grant': KnownDefect(
        'The admitted utility spell behavior differs from retained self/range/source/lifetime boundaries',
        "Cast Darkvision or Speak with Animals through the actual admitted owner using the case's target boundary, paid cast, death, unprepare or earned-rest flow.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_srd_lite_utility_support.py::test_darkvision_upcast_nonstacking_native_sense_preserved_and_genuine_rest': KnownDefect(
        'The admitted utility spell behavior differs from retained self/range/source/lifetime boundaries',
        "Cast Darkvision or Speak with Animals through the actual admitted owner using the case's target boundary, paid cast, death, unprepare or earned-rest flow.",
        category='suspected retained contract discrepancy',
    ),
    'tests/test_integrated_worker_seams.py::test_guardian_entry_damage_does_not_repeat_on_exit_or_reentry': KnownDefect(
        "Ending a turn after creating Guardian of Faith rejects its saved actor as absent",
        "Cast Guardian of Faith at an admitted position, start combat, then end the caster turn. The occupied-space resolver calls query_srd_creature_space_pair and rejects faith_guardian as missing before the entry-damage assertions.",
        "suspected retained contract discrepancy",
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
