# Owner command burn-down

Python snapshot `0da1010feb5a520f917f5d69ff8553ddb174e9e7`. 125 registry commands and 12 run-loop methods.

A command is done when the Rust owner answers it and a comparison session covering it
passes against the recorded Python owner. Tick it here in the same pull request that
flips its status in the owner's command table. Handlers live in
`crates/sylvia-owner/src/commands/<topic>.rs` and call service operations exported by the
lane that owns the service; see "Command handlers" in `README.md`.

Ported: 98 of 137 (`shutdown`, `handshake_status`, `campaign_index`, `campaign_creation_catalog`, `campaign_party_options`, `create_guided_campaign_draft`, `advance_guided_campaign_draft`, `start_guided_campaign`, `finalize_campaign_construction`, `rename_campaign`, `archive_campaign`, `restore_archived_campaign`, `trash_campaign`, `restore_trashed_campaign`, `purge_campaign`, `export_campaign`, `export_campaign_file`, `recover_campaign`, `recover_campaign_file`, `export_package_candidate`, `export_package_candidate_file`, `install_package`, `install_package_file`, `catalog`, `backup_database`, `open_provider_setup`, `admin_full_audit_status`, `admin_full_audit_record`, `export_admin_full_audit`, `set_admin_full_audit`, `builder_chat_index`, `rename_builder_chat`, `delete_builder_chat`, `create_builder_chat`, `resume_agent_session`, `switch_session_reasoning`, `switch_session_speed`, `steer_session`, `session_context`, `session_history_page`, `interrupt_stream`, `disconnect_stream`, `stream_heartbeat`, `narration_attempt_status`, `reconnect_stream`, `submit_session_input`, `switch_session_model`, `start_dm_session`, `manual_retry_session`, `stop_agent_session`, `package_candidates`, `inspect_package_candidate`, `package_candidate_text`, `compare_package_candidates`, `snapshot`, `display_snapshot`, `execute_action`, `resolve_mechanics_choice`, `open_turn`, `complete_turn`, `retryable_interruption`, `begin_campaign_construction`, `execute_player_action`, `preview_player_action`, `confirm_new_campaign`, `complete_campaign_construction`, `export_campaign_plan_file`, `campaign_panels`, `provider_settings`, `claude_connection`, `model_choices`, `save_provider_settings`, `remove_provider_settings`, `set_default_model`, `set_chat_defaults`, `detect_local_providers`, `discover_provider_models`, `validate_provider_model`, `chatgpt_connection`, the eight run-loop commands for display-load cancellation, desktop drafts, local reports and faults and `renderer`, and the eleven `desktop_update_*` methods).

## Run-loop methods

| Method | Rust | Comparison session |
| --- | --- | --- |
| `shutdown` | yes | every session |
| `desktop_update_confirm_startup` | yes | `desktop-update` (as shipped; past their checks they need update preparation) |
| `desktop_update_state` | yes | `desktop-update` |
| `desktop_update_check` | yes | `desktop-update` |
| `desktop_update_download` | yes | `desktop-update` |
| `desktop_update_cancel` | yes | `desktop-update` |
| `desktop_update_defer` | yes | `desktop-update` |
| `desktop_update_prepare` | yes | `desktop-update` (as shipped; past their checks they need update preparation) |
| `desktop_update_handoff` | yes | `desktop-update` (as shipped; past their checks they need update preparation) |
| `desktop_update_reserve` | yes | `desktop-update` |
| `desktop_update_abort` | yes | `desktop-update` |
| `desktop_update_shutdown` | yes | `desktop-update` |

## Registry commands

| Command | Python handler | WebUI | Rust | Comparison session |
| --- | --- | --- | --- | --- |
| `handshake_status` | `campaigns.handle_handshake_status` | no | yes | `local-services` |
| `campaign_index` | `campaigns.handle_campaign_index` | yes | yes | `startup`, `ruleless-turn` |
| `campaign_creation_catalog` | `campaigns.handle_campaign_creation_catalog` | yes | yes | `startup` |
| `ready_campaign_catalog` | `campaigns.handle_ready_campaign_catalog` | yes | yes | `ready-campaigns` |
| `customize_ready_campaign` | `campaigns.handle_customize_ready_campaign` | yes | yes | `ready-campaigns` |
| `start_ready_campaign` | `campaigns.handle_customize_ready_campaign` | yes | yes | `ready-campaigns` |
| `delete_reusable_library_entry` | `campaigns.handle_delete_reusable_library_entry` | yes | no | |
| `character_chat` | `character_chat.handle_character_chat` | yes | no | |
| `send_character_chat_message` | `character_chat.handle_send_character_chat_message` | yes | no | |
| `builder_chat_index` | `builder.handle_builder_chat_index` | yes | yes | `startup` |
| `start_import_repair` | `builder.handle_start_import_repair` | yes | yes | `import-repair` |
| `create_builder_chat` | `builder.handle_create_builder_chat` | yes | yes | `builder-chat-create` |
| `rename_builder_chat` | `builder.handle_rename_builder_chat` | yes | yes | `builder-chat-edit` (refusals); reply encoding against lane 3's Python run |
| `delete_builder_chat` | `builder.handle_delete_builder_chat` | yes | yes | `builder-chat-edit` (refusals); reply encoding against lane 3's Python run |
| `archive_builder_chat` | `builder.handle_archive_builder_chat` | yes | no | |
| `restore_builder_chat` | `builder.handle_archive_builder_chat` | yes | no | |
| `snapshot` | `campaigns.handle_snapshot` | no | yes | `campaign-snapshots` |
| `display_snapshot` | `campaigns.handle_display_snapshot` | yes | yes | `campaign-snapshots`, `startup` |
| `player_projection` | `campaigns.handle_player_projection` | yes | no | |
| `player_projection_page` | `campaigns.handle_player_projection_page` | yes | no | |
| `campaign_panels` | `actions.handle_campaign_panels` | yes | yes | `campaign-panels` |
| `action_sequence` | `actions.handle_action_sequence` | yes | no | |
| `compose_action_sequence` | `actions.handle_compose_action_sequence` | yes | no | |
| `execute_action_sequence` | `actions.handle_compose_action_sequence` | yes | no | |
| `player_action_availability` | `actions.handle_player_action_availability` | yes | no | |
| `execute_player_action` | `actions.handle_execute_player_action` | yes | yes | `player-action-refusals` (refusals; no shipped result card offers a control) |
| `preview_player_action` | `actions.handle_execute_player_action` | yes | yes | `player-action-refusals` (refusals; no shipped result card offers a control) |
| `execute_panel_action` | `actions.handle_execute_panel_action` | yes | no | |
| `panel_action_receipt` | `actions.handle_execute_panel_action` | yes | no | |
| `preview_panel_action` | `actions.handle_execute_panel_action` | yes | no | |
| `append_campaign_sequel` | `campaigns.handle_append_campaign_sequel` | yes | no | |
| `campaign_sequel_context` | `campaigns.handle_append_campaign_sequel` | yes | no | |
| `preview_campaign_sequel` | `campaigns.handle_append_campaign_sequel` | yes | no | |
| `package_candidates` | `packages.handle_package_candidates` | yes | yes | `package-reads`, `startup` |
| `campaign_rules_update_options` | `campaigns.handle_campaign_rules_update_options` | yes | no | |
| `preview_campaign_rules_update` | `campaigns.handle_preview_campaign_rules_update` | yes | no | |
| `adopt_campaign_rules_update` | `campaigns.handle_adopt_campaign_rules_update` | yes | no | |
| `inspect_package_candidate` | `packages.handle_inspect_package_candidate` | yes | yes | `package-reads` |
| `package_candidate_text` | `packages.handle_package_candidate_text` | yes | yes | `package-reads` |
| `compare_package_candidates` | `packages.handle_compare_package_candidates` | yes | yes | `package-reads` |
| `campaign_party_options` | `campaigns.handle_campaign_party_options` | yes | yes | `srd-mechanics-turn` (step 2), over lane 1's `selection_vectors` |
| `model_choices` | `providers.handle_model_choices` | yes | yes | `startup` |
| `provider_settings` | `providers.handle_provider_settings` | yes | yes | `startup` |
| `open_provider_setup` | `providers.handle_open_provider_setup` | yes | yes | `admin-commands` |
| `chatgpt_connection` | `providers.handle_chatgpt_connection` | no | yes | `account-connections` |
| `claude_connection` | `providers.handle_claude_connection` | no | yes | `account-connections` |
| `admin_full_audit_status` | `providers.handle_admin_full_audit_status` | yes | yes | `admin-commands`, `admin-srd` |
| `admin_full_audit_record` | `providers.handle_admin_full_audit_record` | yes | yes | `admin-commands`, `admin-srd` |
| `export_admin_full_audit` | `providers.handle_export_admin_full_audit` | no | yes | `admin-commands`, `admin-srd` |
| `set_admin_full_audit` | `providers.handle_set_admin_full_audit` | yes | yes | `admin-commands`, `admin-srd` |
| `detect_local_providers` | `providers.handle_detect_local_providers` | yes | yes | none yet; lane 4's `discovery_vectors` (Python MockTransport exchanges) |
| `discover_provider_models` | `providers.handle_discover_provider_models` | yes | yes | none yet; lane 4's `discovery_vectors` (Python MockTransport exchanges) |
| `validate_provider_model` | `providers.handle_validate_provider_model` | yes | yes | none yet; lane 4's `validation_vectors` (Python's streamed probe bodies) |
| `save_provider_settings` | `providers.handle_save_provider_settings` | yes | yes | `provider-settings` |
| `remove_provider_settings` | `providers.handle_remove_provider_settings` | yes | yes | `provider-settings` |
| `set_default_model` | `providers.handle_set_default_model` | yes | yes | `provider-settings` |
| `set_chat_defaults` | `providers.handle_set_chat_defaults` | yes | yes | `provider-settings` |
| `switch_session_model` | `providers.handle_switch_session_model` | yes | yes | `submit-refusals` |
| `switch_session_reasoning` | `sessions.handle_switch_session_reasoning` | yes | yes | `session-switches` |
| `switch_session_speed` | `sessions.handle_switch_session_speed` | yes | yes | `session-switches` |
| `inspect_provider_thinking` | `providers.handle_inspect_provider_thinking` | yes | no | |
| `session_context` | `sessions.handle_session_context` | yes | yes | `session-reads` |
| `session_history_page` | `sessions.handle_session_context` | yes | yes | `session-reads` |
| `ingest_builder_sources` | `builder.handle_ingest_builder_sources` | no | no | |
| `steer_session` | `sessions.handle_steer_session` | yes | yes | `session-switches` |
| `submit_session_input` | `sessions.handle_submit_session_input` | yes | yes | `submit-refusals` (before the loop; the run waits for lane 4's T5a) |
| `interrupt_stream` | `sessions.handle_interrupt_stream` | yes | yes | `session-reads` |
| `interrupt_provider_stream` | `providers.handle_interrupt_provider_stream` | yes | no | |
| `retryable_interruption` | `campaigns.handle_retryable_interruption` | yes | yes | `action-commands` (with no interruption), `fail-retry`, `reopen` and `opening-retry` (after a failed turn or opening) |
| `manual_retry_session` | `sessions.handle_manual_retry_session` | yes | yes | `turn-refusals` (before the loop), `fail-retry`, `reopen` and `opening-retry` (the Retry and its replay) |
| `disconnect_stream` | `sessions.handle_disconnect_stream` | yes | yes | `session-reads` |
| `disconnect_provider_stream` | `providers.handle_disconnect_provider_stream` | yes | no | |
| `stream_heartbeat` | `sessions.handle_stream_heartbeat` | yes | yes | `session-reads` |
| `provider_stream_heartbeat` | `providers.handle_provider_stream_heartbeat` | yes | no | |
| `narration_attempt_status` | `sessions.handle_narration_attempt_status` | yes | yes | `session-reads` |
| `provider_stream_status` | `providers.handle_provider_stream_status` | yes | no | |
| `reconnect_stream` | `sessions.handle_reconnect_stream` | yes | yes | `session-reads` |
| `reconnect_provider_stream` | `providers.handle_reconnect_provider_stream` | yes | no | |
| `install_package` | `packages.handle_install_package` | no | yes | `package-install` |
| `confirm_character_card_import` | `packages.handle_confirm_character_card_import` | yes | yes | `card-import` |
| `preview_character_card_file` | `packages.handle_confirm_character_card_import` | no | yes | `card-import` |
| `install_package_file` | `packages.handle_install_package_file` | no | yes | `package-install`, `webui-package-upload` |
| `start_guided_campaign` | `campaigns.handle_start_guided_campaign` | yes | yes | `ruleless-turn`, `stop-redirect`, `fail-retry`, `reopen` (No Ruleset; an installed Ruleset is proven by `sylvia-app/tests/guided.rs`), `srd-mechanics-turn` (a saved Plan) |
| `create_guided_campaign_draft` | `campaigns.handle_create_guided_campaign_draft` | yes | yes | `ruleless-turn`, `stop-redirect`, `fail-retry`, `reopen` (step 2 or 3) |
| `navigate_guided_campaign_draft` | `campaigns.handle_navigate_guided_campaign_draft` | yes | no | |
| `advance_guided_campaign_draft` | `campaigns.handle_advance_guided_campaign_draft` | yes | yes | `ruleless-turn`, `stop-redirect`, `fail-retry`, `reopen` (No Ruleset answers; Contribution steps not yet) |
| `guided_campaign_contribution` | `campaigns.handle_guided_campaign_contribution` | yes | no | |
| `save_reusable_campaign_plan` | `campaigns.handle_save_reusable_campaign_plan` | yes | no | |
| `start_guided_creation_subflow` | `campaigns.handle_start_guided_creation_subflow` | yes | no | |
| `return_guided_creation_subflow` | `campaigns.handle_return_guided_creation_subflow` | yes | no | |
| `confirm_new_campaign` | `campaigns.handle_confirm_new_campaign` | yes | yes | `campaign-construction` |
| `begin_campaign_construction` | `campaigns.handle_begin_campaign_construction` | yes | yes | `action-commands` (refusals), `campaign-construction` (beginning, replay and conflict) |
| `complete_campaign_construction` | `campaigns.handle_complete_campaign_construction` | no | yes | `campaign-construction` |
| `model_guidance` | `providers.handle_model_guidance` | yes | no | |
| `finalize_campaign_construction` | `campaigns.handle_finalize_campaign_construction` | yes | yes | `fail-retry`, `reopen`, `stop-redirect` and `ruleless-turn` (step 8 matches; each session stops later, at `start_dm_session`); the application operation by the guided `finalize_runs` |
| `start_dm_session` | `sessions.handle_start_dm_session` | yes | yes | `turn-refusals` (before the loop; the opening turn waits for lane 4's T5a) |
| `catalog` | `campaigns.handle_catalog` | yes | yes | `admin-commands`, `admin-srd` |
| `execute_action` | `actions.handle_execute_action` | no | yes | `action-commands` |
| `resolve_mechanics_choice` | `actions.handle_resolve_mechanics_choice` | yes | yes | `action-commands` (with no pending choice; a real choice needs a turn) |
| `open_turn` | `actions.handle_open_turn` | no | yes | `action-commands` |
| `complete_turn` | `actions.handle_complete_turn` | no | yes | `action-commands` |
| `stop_agent_session` | `sessions.handle_stop_agent_session` | yes | yes | `turn-refusals` (before the loop; a DM session's `complete_turn` waits for lane 3's N2) |
| `resume_agent_session` | `sessions.handle_resume_agent_session` | yes | yes | `builder-chat-create` |
| `rename_campaign` | `campaigns.handle_rename_campaign` | yes | yes | none yet; lane 3's Python run through `sylvia-app/tests/lifecycle.rs` and the owner's dispatch (`commands/campaigns.rs`) |
| `archive_campaign` | `campaigns.handle_archive_campaign` | yes | yes | none yet; lane 3's Python run through `sylvia-app/tests/lifecycle.rs` and the owner's dispatch (`commands/campaigns.rs`) |
| `restore_archived_campaign` | `campaigns.handle_restore_archived_campaign` | yes | yes | none yet; lane 3's Python run through `sylvia-app/tests/lifecycle.rs` and the owner's dispatch (`commands/campaigns.rs`) |
| `trash_campaign` | `campaigns.handle_trash_campaign` | yes | yes | none yet; lane 3's Python run through `sylvia-app/tests/lifecycle.rs` and the owner's dispatch (`commands/campaigns.rs`) |
| `restore_trashed_campaign` | `campaigns.handle_restore_trashed_campaign` | yes | yes | none yet; lane 3's Python run through `sylvia-app/tests/lifecycle.rs` and the owner's dispatch (`commands/campaigns.rs`) |
| `purge_campaign` | `campaigns.handle_purge_campaign` | yes | yes | paired run through owner dispatch (lane 3's Python run of `handle_purge_campaign`, `commands/campaigns.rs`); launch reconciliation by `tests/purge_reconcile.rs` |
| `backup_database` | `campaigns.handle_backup_database` | no | yes | `admin-commands`, `admin-srd` |
| `export_campaign_plan_file` | `campaigns.handle_export_campaign_plan_file` | no | yes | `plan-export` |
| `export_package_candidate` | `packages.handle_export_package_candidate` | no | yes | paired run through owner dispatch (lane 3's Python run of `handle_export_package_candidate`, `commands/packages.rs`); the operation by `sylvia-app/tests/package_export.rs` |
| `export_package_candidate_file` | `packages.handle_export_package_candidate` | no | yes | paired run through owner dispatch (lane 3's Python run of `handle_export_package_candidate`, `commands/packages.rs`); the operation by `sylvia-app/tests/package_export.rs` |
| `export_campaign` | `campaigns.handle_export_campaign` | no | yes | paired run through owner dispatch (lane 3's Python run of `handle_export_campaign`, `commands/campaigns.rs`); the store's export by `sylvia-store/tests/export_writer.rs` |
| `export_campaign_file` | `campaigns.handle_export_campaign` | no | yes | paired run through owner dispatch (lane 3's Python run of `handle_export_campaign`, `commands/campaigns.rs`); the store's export by `sylvia-store/tests/export_writer.rs` |
| `recover_campaign` | `campaigns.handle_recover_campaign` | no | yes | paired run through owner dispatch (lane 3's Python run of `handle_recover_campaign` and `handle_recover_campaign_file`, `commands/campaigns.rs`) on three storage roots, with each final database |
| `recover_campaign_file` | `campaigns.handle_recover_campaign_file` | no | yes | paired run through owner dispatch (lane 3's Python run of `handle_recover_campaign` and `handle_recover_campaign_file`, `commands/campaigns.rs`) on three storage roots, with each final database |
| `cancel_display_load` | run loop | yes | yes | `local-services` |
| `cancel_local_report` | run loop | no | yes | `local-services` |
| `load_desktop_drafts` | run loop | yes | yes | `local-services` |
| `preview_local_report` | run loop | no | yes | `local-services` |
| `record_local_fault` | run loop | yes | yes | `local-services` |
| `renderer` | run loop | no | yes | `local-services` |
| `save_desktop_drafts` | run loop | yes | yes | `local-services` |
| `save_local_report` | run loop | no | yes | `local-services` |
