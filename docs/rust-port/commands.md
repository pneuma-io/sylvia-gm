# Owner command burn-down

Python snapshot `0da1010feb5a520f917f5d69ff8553ddb174e9e7`. 125 registry commands and 12 run-loop methods.

A command is done when the Rust owner answers it and a comparison session covering it
passes against the recorded Python owner. Tick it here in the same pull request that
flips its status in the owner's command table. Handlers live in
`crates/sylvia-owner/src/commands/<topic>.rs` and call service operations exported by the
lane that owns the service; see "Command handlers" in `README.md`.

Ported: 10 of 137 (`shutdown`, `handshake_status` and the eight run-loop commands: display-load cancellation, desktop drafts, local reports and faults, `renderer`).

## Run-loop methods

| Method | Rust | Comparison session |
| --- | --- | --- |
| `shutdown` | yes | every session |
| `desktop_update_confirm_startup` | no |  |
| `desktop_update_state` | no |  |
| `desktop_update_check` | no |  |
| `desktop_update_download` | no |  |
| `desktop_update_cancel` | no |  |
| `desktop_update_defer` | no |  |
| `desktop_update_prepare` | no |  |
| `desktop_update_handoff` | no |  |
| `desktop_update_reserve` | no |  |
| `desktop_update_abort` | no |  |
| `desktop_update_shutdown` | no |  |

## Registry commands

| Command | Python handler | WebUI | Rust | Comparison session |
| --- | --- | --- | --- | --- |
| `handshake_status` | `campaigns.handle_handshake_status` | no | yes | `local-services` |
| `campaign_index` | `campaigns.handle_campaign_index` | yes | no | |
| `campaign_creation_catalog` | `campaigns.handle_campaign_creation_catalog` | yes | no | |
| `ready_campaign_catalog` | `campaigns.handle_ready_campaign_catalog` | yes | no | |
| `customize_ready_campaign` | `campaigns.handle_customize_ready_campaign` | yes | no | |
| `start_ready_campaign` | `campaigns.handle_customize_ready_campaign` | yes | no | |
| `delete_reusable_library_entry` | `campaigns.handle_delete_reusable_library_entry` | yes | no | |
| `character_chat` | `character_chat.handle_character_chat` | yes | no | |
| `send_character_chat_message` | `character_chat.handle_send_character_chat_message` | yes | no | |
| `builder_chat_index` | `builder.handle_builder_chat_index` | yes | no | |
| `start_import_repair` | `builder.handle_start_import_repair` | yes | no | |
| `create_builder_chat` | `builder.handle_create_builder_chat` | yes | no | |
| `rename_builder_chat` | `builder.handle_rename_builder_chat` | yes | no | |
| `delete_builder_chat` | `builder.handle_delete_builder_chat` | yes | no | |
| `archive_builder_chat` | `builder.handle_archive_builder_chat` | yes | no | |
| `restore_builder_chat` | `builder.handle_archive_builder_chat` | yes | no | |
| `snapshot` | `campaigns.handle_snapshot` | no | no | |
| `display_snapshot` | `campaigns.handle_display_snapshot` | yes | no | |
| `player_projection` | `campaigns.handle_player_projection` | yes | no | |
| `player_projection_page` | `campaigns.handle_player_projection_page` | yes | no | |
| `campaign_panels` | `actions.handle_campaign_panels` | yes | no | |
| `action_sequence` | `actions.handle_action_sequence` | yes | no | |
| `compose_action_sequence` | `actions.handle_compose_action_sequence` | yes | no | |
| `execute_action_sequence` | `actions.handle_compose_action_sequence` | yes | no | |
| `player_action_availability` | `actions.handle_player_action_availability` | yes | no | |
| `execute_player_action` | `actions.handle_execute_player_action` | yes | no | |
| `preview_player_action` | `actions.handle_execute_player_action` | yes | no | |
| `execute_panel_action` | `actions.handle_execute_panel_action` | yes | no | |
| `panel_action_receipt` | `actions.handle_execute_panel_action` | yes | no | |
| `preview_panel_action` | `actions.handle_execute_panel_action` | yes | no | |
| `append_campaign_sequel` | `campaigns.handle_append_campaign_sequel` | yes | no | |
| `campaign_sequel_context` | `campaigns.handle_append_campaign_sequel` | yes | no | |
| `preview_campaign_sequel` | `campaigns.handle_append_campaign_sequel` | yes | no | |
| `package_candidates` | `packages.handle_package_candidates` | yes | no | |
| `campaign_rules_update_options` | `campaigns.handle_campaign_rules_update_options` | yes | no | |
| `preview_campaign_rules_update` | `campaigns.handle_preview_campaign_rules_update` | yes | no | |
| `adopt_campaign_rules_update` | `campaigns.handle_adopt_campaign_rules_update` | yes | no | |
| `inspect_package_candidate` | `packages.handle_inspect_package_candidate` | yes | no | |
| `package_candidate_text` | `packages.handle_package_candidate_text` | yes | no | |
| `compare_package_candidates` | `packages.handle_compare_package_candidates` | yes | no | |
| `campaign_party_options` | `campaigns.handle_campaign_party_options` | yes | no | |
| `model_choices` | `providers.handle_model_choices` | yes | no | |
| `provider_settings` | `providers.handle_provider_settings` | yes | no | |
| `open_provider_setup` | `providers.handle_open_provider_setup` | yes | no | |
| `chatgpt_connection` | `providers.handle_chatgpt_connection` | no | no | |
| `claude_connection` | `providers.handle_claude_connection` | no | no | |
| `admin_full_audit_status` | `providers.handle_admin_full_audit_status` | yes | no | |
| `admin_full_audit_record` | `providers.handle_admin_full_audit_record` | yes | no | |
| `export_admin_full_audit` | `providers.handle_export_admin_full_audit` | no | no | |
| `set_admin_full_audit` | `providers.handle_set_admin_full_audit` | yes | no | |
| `detect_local_providers` | `providers.handle_detect_local_providers` | yes | no | |
| `discover_provider_models` | `providers.handle_discover_provider_models` | yes | no | |
| `validate_provider_model` | `providers.handle_validate_provider_model` | yes | no | |
| `save_provider_settings` | `providers.handle_save_provider_settings` | yes | no | |
| `remove_provider_settings` | `providers.handle_remove_provider_settings` | yes | no | |
| `set_default_model` | `providers.handle_set_default_model` | yes | no | |
| `set_chat_defaults` | `providers.handle_set_chat_defaults` | yes | no | |
| `switch_session_model` | `providers.handle_switch_session_model` | yes | no | |
| `switch_session_reasoning` | `sessions.handle_switch_session_reasoning` | yes | no | |
| `switch_session_speed` | `sessions.handle_switch_session_speed` | yes | no | |
| `inspect_provider_thinking` | `providers.handle_inspect_provider_thinking` | yes | no | |
| `session_context` | `sessions.handle_session_context` | yes | no | |
| `session_history_page` | `sessions.handle_session_context` | yes | no | |
| `ingest_builder_sources` | `builder.handle_ingest_builder_sources` | no | no | |
| `steer_session` | `sessions.handle_steer_session` | yes | no | |
| `submit_session_input` | `sessions.handle_submit_session_input` | yes | no | |
| `interrupt_stream` | `sessions.handle_interrupt_stream` | yes | no | |
| `interrupt_provider_stream` | `providers.handle_interrupt_provider_stream` | yes | no | |
| `retryable_interruption` | `campaigns.handle_retryable_interruption` | yes | no | |
| `manual_retry_session` | `sessions.handle_manual_retry_session` | yes | no | |
| `disconnect_stream` | `sessions.handle_disconnect_stream` | yes | no | |
| `disconnect_provider_stream` | `providers.handle_disconnect_provider_stream` | yes | no | |
| `stream_heartbeat` | `sessions.handle_stream_heartbeat` | yes | no | |
| `provider_stream_heartbeat` | `providers.handle_provider_stream_heartbeat` | yes | no | |
| `narration_attempt_status` | `sessions.handle_narration_attempt_status` | yes | no | |
| `provider_stream_status` | `providers.handle_provider_stream_status` | yes | no | |
| `reconnect_stream` | `sessions.handle_reconnect_stream` | yes | no | |
| `reconnect_provider_stream` | `providers.handle_reconnect_provider_stream` | yes | no | |
| `install_package` | `packages.handle_install_package` | no | no | |
| `confirm_character_card_import` | `packages.handle_confirm_character_card_import` | yes | no | |
| `preview_character_card_file` | `packages.handle_confirm_character_card_import` | no | no | |
| `install_package_file` | `packages.handle_install_package_file` | no | no | |
| `start_guided_campaign` | `campaigns.handle_start_guided_campaign` | yes | no | |
| `create_guided_campaign_draft` | `campaigns.handle_create_guided_campaign_draft` | yes | no | |
| `navigate_guided_campaign_draft` | `campaigns.handle_navigate_guided_campaign_draft` | yes | no | |
| `advance_guided_campaign_draft` | `campaigns.handle_advance_guided_campaign_draft` | yes | no | |
| `guided_campaign_contribution` | `campaigns.handle_guided_campaign_contribution` | yes | no | |
| `save_reusable_campaign_plan` | `campaigns.handle_save_reusable_campaign_plan` | yes | no | |
| `start_guided_creation_subflow` | `campaigns.handle_start_guided_creation_subflow` | yes | no | |
| `return_guided_creation_subflow` | `campaigns.handle_return_guided_creation_subflow` | yes | no | |
| `confirm_new_campaign` | `campaigns.handle_confirm_new_campaign` | yes | no | |
| `begin_campaign_construction` | `campaigns.handle_begin_campaign_construction` | yes | no | |
| `complete_campaign_construction` | `campaigns.handle_complete_campaign_construction` | no | no | |
| `model_guidance` | `providers.handle_model_guidance` | yes | no | |
| `finalize_campaign_construction` | `campaigns.handle_finalize_campaign_construction` | yes | no | |
| `start_dm_session` | `sessions.handle_start_dm_session` | yes | no | |
| `catalog` | `campaigns.handle_catalog` | yes | no | |
| `execute_action` | `actions.handle_execute_action` | no | no | |
| `resolve_mechanics_choice` | `actions.handle_resolve_mechanics_choice` | yes | no | |
| `open_turn` | `actions.handle_open_turn` | no | no | |
| `complete_turn` | `actions.handle_complete_turn` | no | no | |
| `stop_agent_session` | `sessions.handle_stop_agent_session` | yes | no | |
| `resume_agent_session` | `sessions.handle_resume_agent_session` | yes | no | |
| `rename_campaign` | `campaigns.handle_rename_campaign` | yes | no | |
| `archive_campaign` | `campaigns.handle_archive_campaign` | yes | no | |
| `restore_archived_campaign` | `campaigns.handle_restore_archived_campaign` | yes | no | |
| `trash_campaign` | `campaigns.handle_trash_campaign` | yes | no | |
| `restore_trashed_campaign` | `campaigns.handle_restore_trashed_campaign` | yes | no | |
| `purge_campaign` | `campaigns.handle_purge_campaign` | yes | no | |
| `backup_database` | `campaigns.handle_backup_database` | no | no | |
| `export_campaign_plan_file` | `campaigns.handle_export_campaign_plan_file` | no | no | |
| `export_package_candidate` | `packages.handle_export_package_candidate` | no | no | |
| `export_package_candidate_file` | `packages.handle_export_package_candidate` | no | no | |
| `export_campaign` | `campaigns.handle_export_campaign` | no | no | |
| `export_campaign_file` | `campaigns.handle_export_campaign` | no | no | |
| `recover_campaign` | `campaigns.handle_recover_campaign` | no | no | |
| `recover_campaign_file` | `campaigns.handle_recover_campaign_file` | no | no | |
| `cancel_display_load` | run loop | yes | yes | `local-services` |
| `cancel_local_report` | run loop | no | yes | `local-services` |
| `load_desktop_drafts` | run loop | yes | yes | `local-services` |
| `preview_local_report` | run loop | no | yes | `local-services` |
| `record_local_fault` | run loop | yes | yes | `local-services` |
| `renderer` | run loop | no | yes | `local-services` |
| `save_desktop_drafts` | run loop | yes | yes | `local-services` |
| `save_local_report` | run loop | no | yes | `local-services` |
