"""Run the deterministic change gate; installed-candidate acceptance is separate.

Only external providers are scripted in composed journeys. A missing dependency, empty
collection, skipped required check or failed process is a failed gate, never a pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if __package__:
    from .check_catalog import PRODUCT_CASE_EXCLUSIONS, qualification_cases
    from .journey_fixture_inputs import candidate_identity, prepare_candidate
    from .known_test_defects import accepted_case, case_key, report_outcomes
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.check_catalog import PRODUCT_CASE_EXCLUSIONS, qualification_cases
    from scripts.journey_fixture_inputs import candidate_identity, prepare_candidate
    from scripts.known_test_defects import accepted_case, case_key, report_outcomes

ROOT = Path(__file__).resolve().parents[1]
PYTHON_JOURNEYS = {
    "python-quality": ["tests/test_python_quality.py"],
    "launch-lite-compatibility": [
        "tests/test_launch_lite_safety.py",
        "tests/test_runtime_payload_cache.py",
        "tests/test_package_runtime_loading.py",
        "tests/test_runtime_disk_cache.py",
        "tests/test_journey_fixture_inputs.py",
        "tests/test_retained_117.py",
        "tests/test_admitted_fixture_isolation.py",
        "tests/test_interrupted_package_staging.py",
        "tests/test_reusable_participants.py",
        "tests/test_srd_lite_character_compatibility.py",
        "tests/test_srd_lite_foundation.py",
        "tests/test_srd_lite_launch_direct.py",
        "tests/test_srd_lite_healing.py",
        "tests/test_srd_lite_attacks.py",
        "tests/test_srd_lite_true_strike_progression.py",
        "tests/test_srd_lite_areas.py",
    ],
    "lite-admitted-qa": [
        "tests/test_qa_lite_death_range.py",
        "tests/test_qa_lite_victory.py",
        "tests/test_qa_lite_victory_boundaries.py",
        "tests/test_qa_lite_physical_range.py",
    ],
    "command-registry": ["tests/test_command_registry.py"],
    "campaign-play": [
        "tests/test_gm_hotfix_upgrade.py",
        "tests/test_campaign_rules_update.py",
        "tests/test_campaign_rules_update_bindings.py",
        "tests/test_campaign_rules_update_knowledge.py",
        "tests/test_action_sequences.py",
        "tests/test_action_sequence_provider.py",
        "tests/test_td_native_panel_repairs.py",
        "tests/test_td_panel_resources.py",
        "tests/test_fc_campaign_journey.py",
        "tests/test_fc_party_combat.py",
        "tests/test_fc_campaign_progress.py",
        "tests/test_migrated_plan_lifecycle.py",
        "tests/test_riichi_quick_test_plan.py",
        "tests/test_no_rules_interview.py",
        'tests/test_campaign_catalog_invalid_entries.py',
        'tests/test_campaign_chooser_previews.py',
        'tests/test_campaign_endings.py',
        'tests/test_campaign_export_sizing.py',
        'tests/test_campaign_inventory.py',
        'tests/test_campaign_knowledge_search.py',
        'tests/test_campaign_plan_draft_completion.py',
        'tests/test_gm_reference_bounds.py',
        'tests/test_plan_bundles.py',
        'tests/test_plan_draft_check.py',
        'tests/test_plan_network_admission.py',
        'tests/test_plan_party_placement.py',
        'tests/test_plan_spatial_admission.py',
        'tests/test_plan_state_admission.py',
        'tests/test_player_action_availability.py',
        'tests/test_player_action_preflight.py',
        'tests/test_player_actions.py',
        'tests/test_player_preflight_refusal.py',
        'tests/test_player_response_batches.py',
        'tests/test_private_collections.py',
        'tests/test_reusable_plans.py',
        'tests/test_rift_size_rank_contract.py',
        'tests/test_setting_character_composition.py',
        'tests/test_setting_character_parser.py',
        'tests/test_setting_character_requirements.py',
        'tests/test_setting_draft_check.py',
        'tests/test_setting_publication.py',
        'tests/test_campaign_act_markers.py',
        'tests/test_campaign_memory_context.py',
        'tests/test_campaign_plan_act_completion.py',
        'tests/test_campaign_plan_skill_examples.py',
        'tests/test_campaign_sequels.py',
        'tests/test_gcs_message_presentation.py',
        'tests/test_reusable_library_deletion.py',
    ],
    "builder-and-admission": [
        "tests/test_package_inspector_runtime.py",
        "tests/test_character_card_import_lifecycle.py",
        "tests/test_journey_pool.py",
        "tests/test_rich_character_profiles.py",
        "tests/test_rich_profile_tools.py",
        "tests/test_character_profiles.py",
        "tests/test_builder_character_profiles.py",
        "tests/test_builder_tool_budget.py",
        "tests/test_character_creation_contract.py",
        "tests/test_character_rules.py",
        "tests/test_starting_character_choices.py",
        "tests/test_shipped_lite_choice_compatibility.py",
        "tests/test_builder_character_reuse.py",
        "tests/test_required_character_completion.py",
        "tests/test_campaign_library_selection.py",
        "tests/test_alpha_f6g_builder_package_journey.py::"
        "test_ordinary_builder_publishes_candidate_then_neutral_runtime_plays_and_reopens",
        'tests/test_alpha_f3_package_activation.py',
        'tests/test_alpha_f3_package_execution.py',
        'tests/test_alpha_f3_package_publication.py',
        'tests/test_alpha_f6h_builder_source_ingestion.py',
        'tests/test_builder_activity.py',
        'tests/test_builder_archiving.py',
        'tests/test_builder_character_examples.py',
        'tests/test_builder_character_files.py',
        'tests/test_builder_content_draft_checks.py',
        'tests/test_builder_file_conflicts.py',
        'tests/test_builder_question_answers.py',
        'tests/test_builder_reference_batch.py',
        'tests/test_builder_reference_copy.py',
        'tests/test_builder_response_budget.py',
        'tests/test_builder_search_files.py',
        'tests/test_builder_skill_resources.py',
        'tests/test_builder_source_navigation.py',
        'tests/test_builder_titles.py',
        'tests/test_character_content_definition.py',
        'tests/test_character_first_run_references.py',
        'tests/test_compatible_character_copy.py',
        'tests/test_compatible_copy_rule_context.py',
        'tests/test_conditional_schema_admission.py',
        'tests/test_content_definitions.py',
        'tests/test_import_repair.py',
        'tests/test_import_repair_session.py',
        'tests/test_interview_lifecycle.py',
        'tests/test_interview_navigation.py',
        'tests/test_package_content_definitions.py',
        'tests/test_package_record_diagnostics.py',
        'tests/test_package_subformat_content_definitions.py',
        'tests/test_reference_text.py',
        'tests/test_ruleset_draft_checks.py',
        'tests/test_ruleset_expression_collections.py',
        'tests/test_ruleset_source_effects.py',
        'tests/test_ruleset_test_scenarios.py',
        'tests/test_ruleset_test_tool.py',
        'tests/test_ruleset_test_worker.py',
        'tests/test_alpha_f6f_builder_executor.py',
        'tests/test_effective_interview_content.py',
        'tests/test_import_resource_bounds.py',
        'tests/test_interview_schema_upgrade.py',
        'tests/test_starting_advancement.py',
    ],
    "mechanics-and-state": [
        "tests/test_srd_creature_preparation.py",
        "tests/test_srd_ability_checks.py",
        "tests/test_srd_second_wind.py",
        "tests/test_srd_action_budget.py",
        "tests/test_srd_equipment.py",
        "tests/test_saved_weapon_sets.py",
        "tests/test_fc07_supply_reconciliation.py",
        "tests/test_saved_weapon_sets_migration.py",
        "tests/test_equipment_inventory_actions.py",
        "tests/test_srd_opportunity_attacks.py",
        "tests/test_srd_martial_arts.py",
        "tests/test_srd_acrobatic_movement.py",
        "tests/test_srd_falling.py",
        "tests/test_srd_stunning_strike.py",
        "tests/test_srd_save_damage_features.py",
        "tests/test_srd_wholeness_of_body.py",
        "tests/test_srd_self_restoration.py",
        "tests/test_srd_disciplined_survivor.py",
        "tests/test_srd_superior_defense.py",
        "tests/test_srd_attack_budget.py",
        "tests/test_srd_attack_conditions.py",
        "tests/test_srd_combat_result_cards.py",
        "tests/test_srd_unarmed_creatures.py",
        "tests/test_srd_recovery_result_cards.py",
        "tests/test_srd_action_result_cards.py",
        "tests/test_srd_advancement_result_cards.py",
        "tests/test_srd_initiative_cards.py",
        "tests/test_srd_initiative_recovery.py",
        "tests/test_srd_lifecycle_result_cards.py",
        "tests/test_srd_spell_result_cards.py",
        "tests/test_srd_spell_secondary_effects.py",
        "tests/test_srd_attack_spell_consequences.py",
        "tests/test_srd_condition_spells.py",
        "tests/test_srd_public_spell_casting.py",
        "tests/test_srd_casting_requirements.py",
        "tests/test_srd_spell_grants.py",
        "tests/test_srd_spell_grant_uses.py",
        "tests/test_srd_spell_preparation.py",
        "tests/test_srd_spell_advancement.py",
        "tests/test_srd_spell_materials.py",
        "tests/test_srd_npc_spell_casting.py",
        "tests/test_srd_rest_lifecycle.py",
        "tests/test_runtime_initiative_results.py",
        "tests/test_runtime_template_text.py",
        "tests/test_runtime_entity_snapshots.py",
        "tests/test_runtime_optional_reference_values.py",
        "tests/test_runtime_collections.py",
        "tests/test_canonical_json.py",
        "tests/test_runtime_parameter_defaults.py",
        "tests/test_runtime_choices.py",
        "tests/test_runtime_choice_sequences.py",
        "tests/test_ruleset_design_system.py::"
        "test_opportunity_reaction_executes_from_its_immutable_pending_trigger",
        "tests/test_srd_advancement.py",
        "tests/test_campaign_character_updates.py",
        "tests/test_character_limits.py",
        "tests/test_result_cards.py",
        'tests/test_combat_inventory_guard.py',
        'tests/test_coordinate_actor_references.py',
        'tests/test_coordinate_map_ui.py',
        'tests/test_coordinate_occupied_space.py',
        'tests/test_result_card_actions.py',
        'tests/test_runtime_authoring_shape.py',
        'tests/test_runtime_content_definitions.py',
        'tests/test_runtime_entity_creation.py',
        'tests/test_runtime_state_objects.py',
        'tests/test_spatial_area_volumes.py',
        'tests/test_spatial_body_proximity.py',
        'tests/test_spatial_circle_exposure.py',
        'tests/test_spatial_circle_overlap.py',
        'tests/test_spatial_circle_routes.py',
        'tests/test_spatial_clearance.py',
        'tests/test_spatial_engine.py',
        'tests/test_spatial_interface.py',
        'tests/test_spatial_preview.py',
        'tests/test_spatial_prism_volumes.py',
        'tests/test_spatial_runtime.py',
        'tests/test_srd_composite_damage_contract.py',
        'tests/test_srd_creature_space_special_routes.py',
        'tests/test_srd_lite_allocated_target_facts.py',
        'tests/test_srd_lite_attack_projection.py',
        'tests/test_srd_lite_concentration_iteration.py',
        'tests/test_srd_lite_coordinate_cones.py',
        'tests/test_srd_lite_dispel_decision_widgets.py',
        'tests/test_srd_lite_ending_renderer.py',
        'tests/test_srd_lite_expression_modes.py',
        'tests/test_srd_lite_fire_storm_circle.py',
        'tests/test_srd_lite_guardian_body.py',
        'tests/test_srd_lite_missile_iteration.py',
        'tests/test_srd_lite_persistent_refresh_binding.py',
        'tests/test_srd_lite_plan_composition.py',
        'tests/test_srd_lite_point_area_cover.py',
        'tests/test_srd_lite_sleet_ground.py',
        'tests/test_srd_lite_sunburst_origin.py',
        'tests/test_srd_lite_thaumaturgy_exploration.py',
        'tests/test_srd_lite_ward_body.py',
        'tests/test_srd_lite_web_ring_fire.py',
        'tests/test_srd_overlay_ice_storm.py',
        'tests/test_srd_scene_relations.py',
        'tests/test_srd_starting_grants.py',
        'tests/test_tactical_grid_ui.py',
        'tests/test_display_campaign_reads.py',
        'tests/test_movement_card_composition.py',
        'tests/test_srd_contingency_child_families.py',
        'tests/test_srd_contingency_concealment_children.py',
        'tests/test_srd_contingency_mask_children.py',
        'tests/test_srd_contingency_recovery_children.py',
        'tests/test_srd_creature_space_detours.py',
        'tests/test_srd_creature_space_route_continuation.py',
        'tests/test_srd_extended_casting.py',
        'tests/test_srd_lite_area_save_caption.py',
        'tests/test_srd_lite_character_integration.py',
        'tests/test_srd_lite_combat_scene_reentry.py',
        'tests/test_srd_lite_exploration_mage_hand.py',
        'tests/test_srd_lite_false_life_diagnosis.py',
        'tests/test_srd_lite_family_dispatch.py',
        'tests/test_srd_lite_fire_storm_geometry.py',
        'tests/test_srd_lite_fire_storm_origin_placement.py',
        'tests/test_srd_lite_healing_widget_transport.py',
        'tests/test_srd_lite_insect_zone.py',
        'tests/test_srd_lite_misty_step_exploration.py',
        'tests/test_srd_lite_multicaster_persistence.py',
        'tests/test_srd_lite_offensive_acquisition_incremental.py',
        'tests/test_srd_lite_pending_area_preview.py',
        'tests/test_srd_lite_persistent_spell_map.py',
        'tests/test_srd_lite_point_area_bodies.py',
        'tests/test_srd_lite_progression_cantrips_c.py',
        'tests/test_srd_lite_staged_widget_handoff.py',
        'tests/test_srd_lite_vertical_cones.py',
        'tests/test_tactical_grid_frontend.py',
    ],
    "interruption-and-recovery": [
        "tests/test_private_session_reopen.py",
        "tests/test_fc_recovery_composition.py",
        "tests/test_session_history_pages.py",
        "tests/test_alpha_f6e_owner_session_transport.py::"
        "test_owner_manual_retry_reuses_action_and_resettles_campaign_file",
        "tests/test_alpha_f6e_owner_session_transport.py::"
        "test_retry_publication_failure_preserves_checkpoint_across_owner_restart",
        "tests/test_alpha_f6e_owner_session_transport.py::"
        "test_stop_agent_session_cancels_live_builder_provider_and_fences_output",
        "tests/test_alpha_f6e_owner_session_transport.py::"
        "test_builder_steering_reaches_the_next_provider_call_after_a_tool_boundary",
        "tests/test_alpha_f6e_owner_session_transport.py::"
        "test_builder_truncation_drops_incomplete_prose_without_automatic_retry",
        "tests/test_alpha_f5_campaign_lifecycle.py::"
        "test_probe_10_backup_database_produces_coherent_copy",
        "tests/test_alpha_f5_campaign_lifecycle.py::"
        "test_probe_11_export_produces_deterministic_self_contained_archive",
        "tests/test_alpha_f5_campaign_lifecycle.py::"
        "test_probe_12_recovery_after_purge_restores_original_id",
        "tests/test_alpha_f5_campaign_lifecycle.py::test_probe_13_recovery_collision_blocks",
        'tests/test_alpha_f5_smoke_backup.py',
        'tests/test_alpha_f6_local_transport.py',
        'tests/test_draft_lifecycle_schema.py',
        'tests/test_frontend_saved_sessions.py',
        'tests/test_gm_private_reopen.py',
        'tests/test_local_diagnostics_owner.py',
        'tests/test_package_archive_streaming.py',
        'tests/test_private_records_archive.py',
        'tests/test_private_session_reopen.py',
        'tests/test_saved_builder_sessions.py',
        'tests/test_working_context_sessions.py',
    ],
    "gm-turn": [
        "tests/test_alpha_f4_provider_session_adapter.py",
        "tests/test_alpha_f4_generic_sessions.py",
        "tests/test_provider_turn_state.py",
    ],
    "gm-request-contracts": [
        "tests/test_gm_memory_casting_hotfix.py",
    ],
    "provider-contracts": [
        "tests/test_provider_session_transport_projection.py",
        "tests/test_required_ruleset_decisions.py",
        "tests/test_gm_model_narration_blocks.py",
        "tests/test_fc_provider_routes.py",
        "tests/test_provider_continuation_metadata.py",
        "tests/test_private_provider_records.py",
        'tests/test_alpha_07_model_guidance.py',
        'tests/test_alpha_09_provider_management.py',
        'tests/test_automatic_model_defaults.py',
        'tests/test_chatgpt_auth_lifecycle.py',
        'tests/test_chatgpt_cancel_boundaries.py',
        'tests/test_chatgpt_lifecycle.py',
        'tests/test_chatgpt_model_defaults.py',
        'tests/test_compatible_reasoning_channels.py',
        'tests/test_native_gemini_provider.py',
        'tests/test_owner_reasoning_selection.py',
        'tests/test_pes104_chatgpt_capacity.py',
        'tests/test_pes82_chatgpt_connection.py',
        'tests/test_pes82_chatgpt_failures.py',
        'tests/test_pes82_chatgpt_settings.py',
        'tests/test_provider_automatic_setup.py',
        'tests/test_provider_cache.py',
        'tests/test_provider_cache_recovery.py',
        'tests/test_provider_context_capacity.py',
        'tests/test_provider_knowledge_session_callbacks.py',
        'tests/test_provider_official_spec_contracts.py',
        'tests/test_provider_recovery_reasons.py',
        'tests/test_provider_stream_failures.py',
        'tests/test_provider_transport_matrix.py',
        'tests/test_reasoning_effort.py',
        'tests/test_ruleset_test_provider.py',
        'tests/test_session_reasoning_preferences.py',
        'tests/test_alpha_admin_full_audit.py',
        'tests/test_builder_native_thinking.py',
    ],
    "bundled-content": [
        "tests/test_plan_snapshot_cache.py",
        "tests/test_bundled_exact_reuse.py",
        "tests/test_bundled_campaign_collection.py",
        "tests/test_srd_generation_boundaries.py",
        'tests/test_bundled_catalog_reuse.py',
        'tests/test_bundled_metadata_reuse.py',
        'tests/test_bundled_srd_identity.py',
    ],
    "release-integrity": [
        "tests/test_known_test_defects.py",
        "tests/test_test_inventory.py",
        "tests/test_public_fixture_packages.py",
        "tests/test_pyav_release_packaging.py",
        "tests/test_protected_provider_settings.py",
        "tests/test_portable_identity.py",
        "tests/test_fc01_release_exclusions.py",
        "tests/test_bundled_licence_notices.py",
        "tests/test_sylvia_gm_identity.py",
        "tests/test_alpha_09_release_artifact.py",
        "tests/test_supported_journey_gate.py",
        "tests/test_release_identity_gate.py",
        "tests/test_portable_release_recipe.py",
        "tests/test_build_workspace.py",
        "tests/test_ci_selection.py",
        "tests/test_ci_diagnostics.py",
        "tests/test_journey_progress.py",
        'tests/test_activity_progress.py',
        'tests/test_activity_workflow_integration.py',
        'tests/test_alpha_04_ruleset_neutral_runtime.py',
        'tests/test_alpha_09_stateless_mcp.py',
        'tests/test_alpha_f2_event_protocol.py',
        'tests/test_alpha_f5_smoke.py',
        'tests/test_alpha_f6_desktop_ownership.py',
        'tests/test_alpha_f6c_canonical_frontend.py',
        'tests/test_context_integrity.py',
        'tests/test_covered_source_delivery.py',
        'tests/test_desktop_update_atomic_admission.py',
        'tests/test_desktop_update_checkpoint.py',
        'tests/test_desktop_update_drafts.py',
        'tests/test_desktop_update_helper.py',
        'tests/test_desktop_update_helper_pipe.py',
        'tests/test_desktop_update_identity.py',
        'tests/test_desktop_update_installed.py',
        'tests/test_desktop_update_installed_download.py',
        'tests/test_desktop_update_installed_launch.py',
        'tests/test_desktop_update_installed_preservation.py',
        'tests/test_desktop_update_installed_retirement.py',
        'tests/test_desktop_update_lifecycle.py',
        'tests/test_desktop_update_manifest.py',
        'tests/test_desktop_update_owner_gate.py',
        'tests/test_desktop_update_owner_lifecycle.py',
        'tests/test_desktop_update_path_safety.py',
        'tests/test_desktop_update_payload.py',
        'tests/test_desktop_update_preparation.py',
        'tests/test_desktop_update_processes.py',
        'tests/test_desktop_update_recovery_service.py',
        'tests/test_desktop_update_replacement.py',
        'tests/test_desktop_update_restart.py',
        'tests/test_desktop_update_retry.py',
        'tests/test_desktop_update_service.py',
        'tests/test_desktop_update_signer.py',
        'tests/test_desktop_update_startup.py',
        'tests/test_desktop_update_storage.py',
        'tests/test_desktop_update_transport.py',
        'tests/test_dm_input_replay.py',
        'tests/test_early_campaign_ending.py',
        'tests/test_engine_format_reference.py',
        'tests/test_exploration_created_actors_1020.py',
        'tests/test_expression_evaluation_cache.py',
        'tests/test_followup_roll_widgets_983.py',
        'tests/test_fresh_combat_stop.py',
        'tests/test_fresh_profile_schema.py',
        'tests/test_initial_character_state.py',
        'tests/test_installed_notice_preview.py',
        'tests/test_inventory.py',
        'tests/test_isolated_participants.py',
        'tests/test_knowledge_search_pages.py',
        'tests/test_library_source_copy.py',
        'tests/test_library_sources.py',
        'tests/test_lite_player_encounter_boundary.py',
        'tests/test_local_diagnostics.py',
        'tests/test_local_dictation.py',
        'tests/test_map_card_content_definitions.py',
        'tests/test_map_segment_preview.py',
        'tests/test_mechanical_inventory.py',
        'tests/test_monster_pending_transition.py',
        'tests/test_native_monk_advancement_contract.py',
        'tests/test_nested_runtime_bindings.py',
        'tests/test_network_graph_frontend.py',
        'tests/test_network_graph_ui.py',
        'tests/test_nsis_owned_cleanup.py',
        'tests/test_nullable_schema_admission.py',
        'tests/test_overlay_movement_inert_1005.py',
        'tests/test_package_update_rejections.py',
        'tests/test_panel_starting_layout.py',
        'tests/test_party_action_control.py',
        'tests/test_pes104_request_admission.py',
        'tests/test_pes44_native_profile_contract.py',
        'tests/test_piece_table.py',
        'tests/test_preparation_player_intent.py',
        'tests/test_qa_attached_request.py',
        'tests/test_retained_area_feedback_986.py',
        'tests/test_retained_scene_area_1132.py',
        'tests/test_riichi_scoring.py',
        'tests/test_riichi_tile_display.py',
        'tests/test_setup_catalog_loading.py',
        'tests/test_shot_effect_runtime.py',
        'tests/test_shot_effects.py',
        'tests/test_side_decision_scope.py',
        'tests/test_spell_widget_projection.py',
        'tests/test_stored_manifest_reads.py',
        'tests/test_sunburst_repeat_followup.py',
        'tests/test_surface_keyed_values.py',
        'tests/test_tutorial_assets.py',
        'tests/test_webui_delivery_boundaries.py',
        'tests/test_webui_owner_outcomes.py',
        'tests/test_webui_packaging.py',
        'tests/test_webui_shutdown.py',
        'tests/test_webui_upload_bounds.py',
        'tests/test_working_context.py',
        'tests/test_alpha_04_srd_source_authority.py',
        'tests/test_alpha_f1_neutral_authority.py',
        'tests/test_desktop_update_prepare_service.py',
        'tests/test_desktop_update_progress.py',
        'tests/test_desktop_update_recovery_phase_gap.py',
        'tests/test_docs_index.py',
        'tests/test_owned_package_updates.py',
        'tests/test_public_zone_display_anchor.py',
        'tests/test_public_zone_display_geometry.py',
        'tests/test_result_presentation_completion.py',
        "tests/test_desktop_update_states.py",
        "tests/test_updater_move_observer.py",
    ],
    "generic-procedures": [
        "tests/test_runtime_procedure_failures.py",
        "tests/test_ruleset_modifier_evaluation.py",
        "tests/test_runtime_nested_rejections.py",
    ],
    "full-only-slow": [
        'tests/test_alpha_04_ruleset_neutral_package.py',
        'tests/test_alpha_05_rift_journey.py',
        'tests/test_alpha_f6d_owner_process.py',
        'tests/test_alpha_manual_campaign_plan.py',
        'tests/test_background_onboarding_lifecycle.py',
        'tests/test_builder_library.py',
        'tests/test_builder_references.py',
        'tests/test_builder_session_migration.py',
        'tests/test_builder_source_selection.py',
        'tests/test_bundled_riichi.py',
        'tests/test_bundled_starter_characters.py',
        'tests/test_content_inheritance.py',
        'tests/test_desktop_update_crash_recovery.py',
        'tests/test_desktop_update_recovery.py',
        'tests/test_false_life_native_provenance.py',
        'tests/test_guided_campaign_draft.py',
        'tests/test_integrated_worker_seams.py',
        'tests/test_lite_frontend_projections.py',
        'tests/test_lite_panel_combat.py',
        'tests/test_lite_public_first_aid.py',
        'tests/test_native_followups.py',
        'tests/test_plan_snapshot_publication.py',
        'tests/test_public_lite_descendants.py',
        'tests/test_ready_campaigns.py',
        'tests/test_riichi_mahjong.py',
        'tests/test_riichi_progression.py',
        'tests/test_srd_creature_space_elevation_routes.py',
        'tests/test_srd_creature_space_first_leg.py',
        'tests/test_srd_creature_space_numeric_boundaries.py',
        'tests/test_srd_creature_space_owner.py',
        'tests/test_srd_creature_space_passages.py',
        'tests/test_srd_creature_space_review.py',
        'tests/test_srd_creature_space_terrain_routes.py',
        'tests/test_srd_lite_attack_geometry.py',
        'tests/test_srd_lite_defense_boundaries.py',
        'tests/test_srd_lite_defenses.py',
        'tests/test_srd_lite_false_life.py',
        'tests/test_srd_lite_mage_alias.py',
        'tests/test_srd_lite_missiles.py',
        'tests/test_srd_lite_mobility_support.py',
        'tests/test_srd_lite_offensive_lifecycle_incremental.py',
        'tests/test_srd_lite_offensive_scaling_incremental.py',
        'tests/test_srd_lite_ray_reactions.py',
        'tests/test_srd_lite_reaction_grants.py',
        'tests/test_srd_lite_spare_dying_incremental.py',
        'tests/test_srd_lite_travel_support.py',
        'tests/test_srd_lite_utility_support.py',
        'tests/test_srd_movement_preview_route.py',
        'tests/test_srd_reaction_spells.py',
        'tests/test_srd_reaction_windows.py',
        'tests/test_srd_size_rank_contract.py',
        'tests/test_srd_spell_object_content.py',
        'tests/test_srd_spell_wave3_information.py',
        'tests/test_starter_builder_copy.py',
        'tests/test_starting_advancement_flow.py',
        'tests/test_starting_advancement_session.py',
        'tests/test_webui.py',
        'tests/test_webui_private_proxy.py',
        'tests/test_weekend_candidate.py',
    ],
}
FULL_ONLY_JOURNEYS = frozenset({"full-only-slow"})

UI_JOURNEY = [
    "tests/frontend_td_retained_layout.browser.cjs",
    "tests/frontend_campaign_rules_update.browser.cjs",
    "tests/frontend_character_equipment.test.cjs",
    "tests/frontend_character_selection.test.cjs",
    "tests/frontend_character_inspection.browser.cjs",
    "tests/frontend_game_controls.test.cjs",
    "tests/frontend_saved_choice_commit.test.cjs",
    "tests/frontend_map_initial_camera.test.cjs",
    "tests/frontend_map_initial_camera.browser.cjs",
    "tests/frontend_map_camera.browser.cjs",
    "tests/frontend_character_card_import.browser.cjs",
    "tests/frontend_campaign_endings.test.cjs",
    "tests/frontend_act_markers.test.cjs",
    "tests/frontend_agent_activity.test.cjs",
    "tests/frontend_saved_sessions.test.cjs",
    "tests/frontend_result_card_actions.test.cjs",
    "tests/frontend_result_migration.test.cjs",
    "tests/frontend_player_actions.test.cjs",
    "tests/frontend_action_sequence_bridge.test.cjs",
    "tests/frontend_default_queue.test.cjs",
    "tests/frontend_mechanics_choices.test.cjs",
    "tests/frontend_party_panel_actions.test.cjs",
    "tests/frontend_result_migration.browser.cjs",
    "tests/frontend_shell.browser.cjs",
    "tests/frontend_tutorials.browser.cjs",
    "tests/frontend_saved_equipment.browser.cjs",
    'tests/frontend_activity_lifecycle.test.cjs',
    'tests/frontend_audit_paging.test.cjs',
    'tests/frontend_backend_map_preview.test.cjs',
    'tests/frontend_builder_questions.browser.cjs',
    'tests/frontend_campaign_party.test.cjs',
    'tests/frontend_campaign_sequel.browser.cjs',
    'tests/frontend_campaign_start.test.cjs',
    'tests/frontend_character_target.test.cjs',
    'tests/frontend_compact_queue.test.cjs',
    'tests/frontend_content_security.browser.cjs',
    'tests/frontend_coordinate_zone_legend.cjs',
    'tests/frontend_desktop_drafts.test.cjs',
    'tests/frontend_desktop_startup.test.cjs',
    'tests/frontend_desktop_updates.browser.cjs',
    'tests/frontend_desktop_updates.test.cjs',
    'tests/frontend_dictation.browser.cjs',
    'tests/frontend_dictation.test.cjs',
    'tests/frontend_local_diagnostics.test.cjs',
    'tests/frontend_map_presentation.test.cjs',
    'tests/frontend_network_actor_selection.test.cjs',
    'tests/frontend_panel_host.test.cjs',
    'tests/frontend_panel_workspace.test.cjs',
    'tests/frontend_piece_table.test.cjs',
    'tests/frontend_ready_campaigns.test.cjs',
    'tests/frontend_result_coverage.test.cjs',
    'tests/frontend_shared_queue.test.cjs',
    'tests/frontend_shot_playback.test.cjs',
    'tests/frontend_spell_roll_occurrences.cjs',
    'tests/frontend_support_report.test.cjs',
    'tests/frontend_update_recovery.test.cjs',
    'tests/frontend_upload_outcomes.test.cjs',
    'tests/frontend_weapon_card.test.cjs',
    'tests/frontend_webui_bridge.test.cjs',
    'tests/frontend_webui_mobile.test.cjs',
    'tests/frontend_model_settings.browser.cjs',
    'tests/frontend_panel_startup.browser.cjs',
    'tests/frontend_shell_layout.browser.cjs',
    'tests/frontend_starter_characters.browser.cjs',
    'tests/frontend_support_report.browser.cjs',
    'tests/frontend_tile_actions.browser.cjs',
    'tests/frontend_tile_table.browser.cjs',
    'tests/frontend_update_recovery.browser.cjs',
    'tests/frontend_webui_navigation.browser.cjs',
    'tests/frontend_campaign_plan_browser.browser.cjs',
    'tests/frontend_map_header.browser.cjs',
    'tests/frontend_panel_drag.browser.cjs',
    'tests/frontend_settings_layout.browser.cjs',
    'tests/frontend_home.browser.cjs',
    'tests/frontend_rig_motion.browser.cjs',
    'tests/frontend_suite_order_categories.browser.cjs',
    'tests/frontend_suite_roster_strip.browser.cjs',
    'tests/frontend_weapon_reload.browser.cjs',
    'tests/frontend_weapon_reload.test.cjs',
    'tests/frontend_weapon_reload_queue.test.cjs',
]


def source_identity() -> dict[str, object]:
    """Bind evidence to every relevant source/test/input byte, including untracked fixes."""
    files = {}
    for folder in (
        "src",
        "scripts",
        "tests",
        "rulesets",
        "campaign-settings",
        "campaign-plans",
        "config",
        "docs",
        "licenses",
        ".github",
    ):
        for path in sorted((ROOT / folder).rglob("*")):
            if path.is_file() and not any(
                part in ("__pycache__", "node_modules", ".pytest_cache") for part in path.parts
            ):
                files[path.relative_to(ROOT).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
    for path in sorted((ROOT / "src-tauri").rglob("*")):
        if path.is_file() and not any(
            part in ("target", "binaries", "node_modules", "__pycache__")
            for part in path.relative_to(ROOT).parts
        ):
            files[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in (
        "pyproject.toml",
        "conftest.py",
        "uv.lock",
        "README.md",
        "AGENTS.md",
        "CONTEXT.md",
        "LICENSE",
        "COPYRIGHT",
        "LICENSING-STATUS.md",
        "SYLVIA-ADDITIONAL-PERMISSIONS.md",
        "dependency-notices.txt",
    ):
        if (ROOT / name).is_file():
            files[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    head = None
    if (ROOT / ".git").exists():
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    selected_candidate = candidate_identity(os.environ)
    digest = hashlib.sha256(json.dumps(
        {"files": files, "srd_lite_candidate": selected_candidate},
        sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    expected = os.environ.get("SYLVIA_EXPECTED_REVISION")
    if expected and head != expected:
        raise ValueError("Checked-out revision differs from required current workflow revision")
    return {"sha256": digest, "head": head, "files": files,
            "srd_lite_candidate": selected_candidate}


def _remaining_timeout(deadline):
    return None if deadline is None else max(0.001, deadline - time.monotonic())


def _parallel_pytest_cases(name, destination, testdata, environment, nodeids, count, deadline=None):
    """Run every collected case once, with separate mutable data and an optional CI deadline."""
    assignments = [nodeids[index::count] for index in range(count)]

    def run_shard(index, selected):
        prefix = destination / f"{name}-shard-{index}"
        xml = prefix.with_suffix(".xml")
        log = prefix.with_suffix(".log")
        data = testdata.with_name(testdata.name + f"-shard-{index}")
        if data.exists() or not data.resolve().is_relative_to(testdata.parent.resolve()):
            raise ValueError("Parallel test data must be a fresh contained directory")
        args = [
            "-s",
            "-o",
            f"cache_dir={destination / 'pytest-cache' / str(index)}",
            f"--junitxml={xml}",
            f"--basetemp={data}",
            *selected,
        ]
        argument_file = prefix.with_suffix(".arguments.json")
        argument_file.write_text(json.dumps(args), encoding="utf-8")
        # An argument file avoids Windows command-line limits without shell expansion.
        launcher = (
            "import json,sys,pytest; "
            "raise SystemExit(pytest.main(json.load(open(sys.argv[1],encoding='utf-8'))))"
        )
        try:
            with log.open("w", encoding="utf-8") as stream:
                result = subprocess.run(
                    [sys.executable, "-c", launcher, str(argument_file)],
                    cwd=ROOT,
                    env=environment,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=_remaining_timeout(deadline),
                    check=False,
                )
            return result.returncode == 0, xml, log
        except (OSError, subprocess.TimeoutExpired) as error:
            with log.open("a", encoding="utf-8") as stream:
                stream.write(f"\n{type(error).__name__}: {error}")
            return False, xml, log

    with ThreadPoolExecutor(max_workers=count) as executor:
        futures = [
            executor.submit(run_shard, index, selected)
            for index, selected in enumerate(assignments)
            if selected
        ]
        results = [future.result() for future in futures]
    merged = ET.Element("testsuites")
    passed = bool(results)
    with (destination / f"{name}.log").open("w", encoding="utf-8") as stream:
        for successful, xml, log in results:
            passed &= successful
            stream.write(log.read_text(encoding="utf-8"))
            if not xml.is_file():
                passed = False
                continue
            try:
                report = ET.parse(xml).getroot()
                for suite in report.findall("testsuite"):
                    merged.append(suite)
            except ET.ParseError as error:
                stream.write(f"\nMalformed shard report: {error}")
                passed = False
    ET.ElementTree(merged).write(destination / f"{name}.xml", encoding="utf-8")
    return passed


def _run(
    name: str, destination: Path, *, case_workers: int = 1, deadline=None,
    include_historical=False,
) -> tuple[bool, float]:
    """Keep complete synthetic test diagnostics and reject incomplete required coverage."""
    xml = destination / f"{name}.xml"
    testdata = destination / "attempts" / name
    if name in ("lite-admitted-qa", "launch-lite-compatibility", "builder-and-admission"):
        # The lossless fixture archival helper requires this owned QA parent.
        # A fresh diagnostics destination names a distinct contained test run.
        run_id = hashlib.sha256(str(destination.resolve()).encode()).hexdigest()[:12]
        owned_attempts = (destination / "attempts").resolve()
        testdata = owned_attempts / ("gate-" + run_id) / name
        if not testdata.resolve().is_relative_to(owned_attempts) or testdata.exists():
            raise ValueError("Admitted QA test directory is not a fresh contained target")
        testdata.parent.mkdir(parents=True, exist_ok=True)
    testdata.parent.mkdir(parents=True, exist_ok=True)
    if name == "ui-controls":
        node = shutil.which("node")
        if node is None:
            print("FAIL ui-controls: Node.js is required", flush=True)
            (destination / f"{name}.log").write_text("Node.js is required", encoding="utf-8")
            return False, 0.0
        # Concurrent Edge startups timed out while the Python pool was busy.
        # Keep the original per-test limits and all cases; bound browser starts.
        command = [node, "--test", "--test-concurrency=2", "--test-reporter=tap", *UI_JOURNEY]
    else:
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-s",
            "-o",
            f"cache_dir={destination / 'pytest-cache' / name}",
            f"--junitxml={xml}",
            f"--basetemp={testdata}",
            *PYTHON_JOURNEYS[name],
        ]
        if os.environ.get("SYLVIA_RETAIN_SUCCESSFUL_FIXTURES") == "0":
            command.extend(["-o", "tmp_path_retention_policy=failed"])
        if not include_historical:
            command.extend(f"--deselect={nodeid}" for nodeid in PRODUCT_CASE_EXCLUSIONS)
    started = time.monotonic()
    print(f"RUN  {name}", flush=True)
    child_environment = dict(os.environ)
    child_environment["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "src"), str(ROOT), child_environment.get("PYTHONPATH", "")]
    )
    child_environment.pop("PYTEST_ADDOPTS", None)
    child_environment["PYTHONUNBUFFERED"] = "1"
    child_environment["SYLVIA_QA_FIXTURE_ROOT"] = str(destination.resolve())
    child_environment["SYLVIA_RUNTIME_CACHE_DIR"] = str(destination.resolve() / "runtime-cache")
    # Keep archive spools and browser profiles in this run's owned data volume.
    temporary = destination / "temporary" / name
    temporary.mkdir(parents=True, exist_ok=True)
    for variable in ("TEMP", "TMP", "TMPDIR"):
        child_environment[variable] = str(temporary.resolve())
    if name == "ui-controls":
        child_environment["SYLVIA_TEST_PYTHON"] = sys.executable
    expected_cases = set()
    collected_nodeids = []
    try:
        if name != "ui-controls":
            collection = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "--collect-only",
                    "-q",
                    "-o",
                    "addopts=",
                    *PYTHON_JOURNEYS[name],
                ],
                cwd=ROOT,
                env=child_environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_remaining_timeout(deadline),
                check=False,
            )
            for line in collection.stdout.splitlines():
                if line.startswith("tests/") and "::" in line:
                    collected_nodeids.append(line)
            if len(set(collected_nodeids)) != len(collected_nodeids):
                raise ValueError("Required collection contains duplicate cases")
            collected_nodeids, excluded = qualification_cases(
                collected_nodeids, include_historical=include_historical,
            )
            expected_cases = {tuple(case_key(nodeid).split("::", 1)) for nodeid in collected_nodeids}
            (destination / f"{name}-excluded-cases.json").write_text(
                json.dumps(excluded, indent=2), encoding="utf-8",
            )
            (destination / f"{name}-required-cases.json").write_text(
                json.dumps(sorted(expected_cases), indent=2),
                encoding="utf-8",
            )
            if collection.returncode != 0 or not expected_cases:
                raise ValueError(
                    "Required test collection failed or contained no checks: "
                    + (collection.stdout + collection.stderr)[-4000:]
                )
        # Stream owned diagnostics as checks execute; a timeout must not discard
        # the passed/failed cases and traces that led to it.
        if case_workers > 1 and name != "ui-controls":
            passed = _parallel_pytest_cases(
                name,
                destination,
                testdata,
                child_environment,
                collected_nodeids,
                case_workers,
                deadline,
            )
            output = (destination / f"{name}.log").read_text(encoding="utf-8")
        else:
            with (destination / f"{name}.log").open("w", encoding="utf-8") as stream:
                result = subprocess.run(
                    command,
                    cwd=ROOT,
                    env=child_environment,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=_remaining_timeout(deadline),
                    check=False,
                )
            output = (destination / f"{name}.log").read_text(encoding="utf-8")
            output += (result.stdout or "") + (result.stderr or "")
            passed = result.returncode == 0
        if name == "ui-controls":
            passed &= bool(re.search(r"^# tests [1-9]\d*\s*$", output, re.MULTILINE))
            passed &= not bool(
                re.search(r"^# (cancelled|skipped|todo) [1-9]", output, re.MULTILINE)
            )
        elif xml.exists():
            report = ET.parse(xml).getroot()
            actual_cases = [
                (case.get("classname"), case.get("name")) for case in report.findall(".//testcase")
            ]
            passed &= bool(actual_cases) and len(actual_cases) == len(expected_cases)
            passed &= set(actual_cases) == expected_cases
            passed &= all(accepted_case(case) for case in report.findall(".//testcase"))
        else:
            passed = False
    except (OSError, subprocess.TimeoutExpired, ET.ParseError, ValueError) as error:
        passed = False
        existing = destination / f"{name}.log"
        output = existing.read_text(encoding="utf-8") if existing.exists() else ""
        output += f"\n{type(error).__name__}: {error}"
    (destination / f"{name}.log").write_text(output, encoding="utf-8")
    elapsed = time.monotonic() - started
    print(f"{'PASS' if passed else 'FAIL'} {name} ({elapsed:.1f}s)", flush=True)
    if not passed:
        print(output[-16000:], flush=True)
    return bool(passed), elapsed


def _run_guarded(
    name: str, destination: Path, *, case_workers: int = 1, deadline=None,
    include_historical=False,
) -> tuple[bool, float]:
    """Preserve an early setup failure as required-group evidence rather than lose the summary."""
    started = time.monotonic()
    try:
        return _run(name, destination, case_workers=case_workers, deadline=deadline,
                    include_historical=include_historical)
    except (OSError, ValueError, MemoryError) as error:
        log = destination / f"{name}.log"
        prior = log.read_text(encoding="utf-8") if log.exists() else ""
        log.write_text(prior + f"\n{type(error).__name__}: {error}", encoding="utf-8")
        elapsed = time.monotonic() - started
        print(f"FAIL {name} setup ({elapsed:.1f}s): {type(error).__name__}: {error}", flush=True)
        return False, elapsed


def main() -> int:
    """The default is the complete change gate; selections are explicitly partial checks."""
    global PYTHON_JOURNEYS, UI_JOURNEY
    names = [*PYTHON_JOURNEYS, "ui-controls"]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ci",
        action="store_true",
        help=(
            "Use the 1800-second whole-run budget for CI; development runs have no wall-time cutoff"
        ),
    )
    parser.add_argument(
        "--journey",
        action="append",
        choices=names,
        help="Run a selected group for diagnosis; this is not the full gate",
    )
    parser.add_argument(
        "--list", action="store_true", help="List the actual checks without running"
    )
    parser.add_argument(
        "--jobs",
        type=int,
        choices=(1, 2, 3, 4),
        default=2,
        help="Independent groups (default: 2); long groups use bounded isolated case workers",
    )
    parser.add_argument(
        "--case-workers",
        type=int,
        choices=(1, 2, 3, 4),
        default=1,
        help="Workers per long group (default 1); complete runs use jobs times this value",
    )
    parser.add_argument(
        "--scheduler",
        choices=("module", "case"),
        default="case",
        help="Balance measured cases (default); optional module fixture affinity",
    )
    parser.add_argument(
        "--selection-manifest",
        type=Path,
        help="Source-bound affected PR selection; never whole-product acceptance",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Empty destination for synthetic diagnostics, timings and XML reports",
    )
    args = parser.parse_args()
    if args.ci and args.journey:
        parser.error("--ci requires the complete suite")
    if args.selection_manifest and args.journey:
        parser.error("A selection manifest cannot be combined with an ad hoc journey")
    selected = list(dict.fromkeys(args.journey or names))
    if args.list:
        for name in selected:
            print(
                name
                + ":\n  "
                + "\n  ".join(
                    UI_JOURNEY if name == "ui-controls" else PYTHON_JOURNEYS[name],
                )
            )
        return 0
    destination = (args.output_dir or Path(tempfile.mkdtemp(prefix="sylvia-journeys-"))).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        parser.error("--output-dir must be empty; previous results must not mix with this run")
    print(f"Synthetic test diagnostics: {destination}", flush=True)
    (destination / "summary.json").write_text(
        json.dumps(
            {
                "status": "running",
                "passed": False,
                "complete_gate": False,
                "reason": "No completed result yet",
            }
        ),
        encoding="utf-8",
    )
    candidate = prepare_candidate(os.environ, ROOT, destination)
    (destination / "srd-candidate-input.json").write_text(
        json.dumps(candidate, indent=2), encoding="utf-8"
    )
    initial_identity = source_identity()
    (destination / "source-identity.json").write_text(
        json.dumps(initial_identity, indent=2), encoding="utf-8"
    )
    selection = None
    if args.selection_manifest:
        if __package__:
            from .select_checks import load_selection
        else:
            from select_checks import load_selection
        selection = load_selection(
            args.selection_manifest, revision=initial_identity["head"], root=ROOT
        )
        if args.ci and selection["mode"] != "full":
            parser.error("The future post-launch budget requires explicit full qualification")
        PYTHON_JOURNEYS, UI_JOURNEY = selection["python"], selection["ui"]
        selected = [*PYTHON_JOURNEYS, *(["ui-controls"] if UI_JOURNEY else [])]
        (destination / "selection.json").write_text(
            json.dumps(selection, indent=2), encoding="utf-8"
        )
    started = time.monotonic()
    time_budget = 1800 if args.ci else None
    deadline = None if time_budget is None else started + time_budget
    include_historical = bool(selection and selection.get("qualification_kind") == "historical")

    def run_group(name, output, *, case_workers=1):
        return _run_guarded(name, output, case_workers=case_workers, deadline=deadline,
                            include_historical=include_historical)

    # Each group runs in its own process and pytest temporary directory; only immutable
    # repository inputs are shared. Keep concurrency bounded on the Windows CI runner.
    if args.journey is None:
        from journey_pool import run_balanced_gate

        results = run_balanced_gate(
            ROOT,
            destination,
            PYTHON_JOURNEYS,
            selected,
            min(10, args.jobs * args.case_workers),
            started,
            run_group,
            deadline=deadline,
            scheduler=args.scheduler,
            include_historical=include_historical,
        )
    else:
        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            # Start the longest group immediately; short groups must not delay its critical path.
            execution_order = sorted(
                selected,
                key=lambda name: (
                    0 if name == "mechanics-and-state" else 1 if name == "campaign-play" else 2
                ),
            )
            pending = {
                name: executor.submit(
                    run_group,
                    name,
                    destination,
                    case_workers=args.case_workers
                    if name in ("campaign-play", "mechanics-and-state", "launch-lite-compatibility")
                    else 1,
                )
                for name in execution_order
            }
            results = {name: future.result() for name, future in pending.items()}
    elapsed = time.monotonic() - started
    failed = [name for name, (passed, _) in results.items() if not passed]
    missing_groups = sorted(set(selected) - set(results))
    unexpected_groups = sorted(set(results) - set(selected))
    failed.extend(f"missing group: {name}" for name in missing_groups)
    failed.extend(f"unexpected group: {name}" for name in unexpected_groups)
    within_time_budget = time_budget is None or elapsed <= time_budget
    unchanged_source = source_identity() == initial_identity
    if not unchanged_source:
        failed.append("source changed during checks")
    python_cases = []
    for name in results:
        report_path = destination / f"{name}.xml"
        if report_path.is_file():
            try:
                python_cases.extend(ET.parse(report_path).getroot().iter("testcase"))
            except ET.ParseError:
                pass  # The owning group already fails malformed or missing reports.
    case_counts, expected_failures = report_outcomes(python_cases)
    excluded_cases = [case for path in sorted(destination.glob("*-excluded-cases.json"))
                      for case in json.loads(path.read_text(encoding="utf-8"))]
    summary = {
        "python_case_counts": case_counts,
        "expected_failures": expected_failures,
        "qualification_kind": "historical" if include_historical else "product",
        "historical_check": selection.get("check") if include_historical else None,
        "excluded_cases": excluded_cases,
        "status": "completed",
        "passed": not failed and within_time_budget,
        "complete_gate": args.journey is None
        and (selection is None or selection["mode"] == "full"),
        "gate_kind": (
            "historical checkpoint" if include_historical
            else "affected PR" if selection and selection["mode"] == "affected"
            else "selected product checks" if args.journey
            else "complete deterministic"
        ),
        "missing_groups": missing_groups,
        "unexpected_groups": unexpected_groups,
        "qualification_mode": "ci" if args.ci else "development",
        "time_budget_seconds": time_budget,
        "within_time_budget": within_time_budget,
        "elapsed_seconds": round(elapsed, 2),
        "jobs": args.jobs,
        "case_workers": {
            name: args.case_workers
            if name in ("mechanics-and-state", "campaign-play", "launch-lite-compatibility") else 1
            for name in selected if name != "ui-controls"
        }
        if args.journey
        else {},
        "execution_mode": "selected groups" if args.journey else "shared balanced pool",
        "group_timing_basis": (
            "Explicit wall_seconds or case_worker_seconds; Python case times include "
            "setup/call/teardown, not concurrent group wall time"
        ),
        "scheduler": args.scheduler,
        "total_case_workers": min(10, args.jobs * args.case_workers) if not args.journey else None,
        "groups": {
            name: {
                "passed": passed,
                "wall_seconds"
                if args.journey or name == "ui-controls"
                else "case_worker_seconds": round(seconds, 2),
            }
            for name, (passed, seconds) in results.items()
        },
        "installed_acceptance_run": False,
        "source_sha256": initial_identity["sha256"],
        "revision": initial_identity["head"],
        "unchanged_source": unchanged_source,
    }
    (destination / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    label = (
        "Affected PR checks"
        if selection and selection["mode"] == "affected"
        else "Selected checks"
        if args.journey
        else "CI qualification"
        if args.ci
        else "Development qualification"
    )
    failure_labels = failed + ([] if within_time_budget else ["time budget"])
    if expected_failures:
        print(f"Known expected failures: {len(expected_failures)} (reported separately from passes)")
    print(
        f"{label}: {'FAIL: ' + ', '.join(failure_labels) if failure_labels else 'PASS'}",
        flush=True,
    )
    print(
        f"Test execution: {elapsed:.1f}s; includes process setup, excludes dependency installation"
    )
    if not within_time_budget:
        print(f"FAIL time budget: complete gate exceeds {time_budget}s", flush=True)
    print("Installed app and genuine-provider acceptance: NOT RUN by this command", flush=True)
    return 1 if failed or not within_time_budget else 0


if __name__ == "__main__":
    raise SystemExit(main())
