//! The owner's command table.
//!
//! Written once from `interfaces/commands/registry.py` at snapshot `0da1010feb5a520f917f5d69ff8553ddb174e9e7` by
//! `docs/rust-port/tools/port_ledger.py`, and maintained by hand since. When a command is
//! ported, add its handler in `commands/<topic>.rs`, change its status here to `Ported` and
//! tick it in `docs/rust-port/commands.md`.

/// Whether the Rust owner answers a command itself yet.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    /// Still answered only by the Python owner.
    Pending,
    /// Answered by the Rust owner and covered by a comparison session.
    Ported,
}

/// One owner command.
#[derive(Debug, Clone, Copy)]
pub struct Command {
    /// The `method` of the request.
    pub name: &'static str,
    /// Python handler module under `interfaces/commands`, empty for run-loop methods.
    pub topic: &'static str,
    /// May be called through the WebUI host.
    pub webui: bool,
    /// Recorded by local diagnostics.
    pub diagnostics: bool,
    /// Port status.
    pub status: Status,
}

const fn pending(name: &'static str, topic: &'static str, webui: bool, diagnostics: bool) -> Command {
    Command { name, topic, webui, diagnostics, status: Status::Pending }
}

const fn ported(name: &'static str, topic: &'static str, webui: bool, diagnostics: bool) -> Command {
    Command { name, topic, webui, diagnostics, status: Status::Ported }
}

/// Every command in `COMMAND_REGISTRY`, in Python's declaration order.
pub const REGISTRY: &[Command] = &[
    ported("handshake_status", "campaigns", false, true),
    pending("campaign_index", "campaigns", true, true),
    pending("campaign_creation_catalog", "campaigns", true, true),
    pending("ready_campaign_catalog", "campaigns", true, false),
    pending("customize_ready_campaign", "campaigns", true, false),
    pending("start_ready_campaign", "campaigns", true, false),
    pending("delete_reusable_library_entry", "campaigns", true, true),
    pending("character_chat", "character_chat", true, true),
    pending("send_character_chat_message", "character_chat", true, true),
    pending("builder_chat_index", "builder", true, true),
    pending("start_import_repair", "builder", true, true),
    pending("create_builder_chat", "builder", true, true),
    pending("rename_builder_chat", "builder", true, true),
    pending("delete_builder_chat", "builder", true, true),
    pending("archive_builder_chat", "builder", true, true),
    pending("restore_builder_chat", "builder", true, true),
    pending("snapshot", "campaigns", false, true),
    pending("display_snapshot", "campaigns", true, true),
    pending("player_projection", "campaigns", true, true),
    pending("player_projection_page", "campaigns", true, true),
    pending("campaign_panels", "actions", true, true),
    pending("action_sequence", "actions", true, true),
    pending("compose_action_sequence", "actions", true, true),
    pending("execute_action_sequence", "actions", true, true),
    pending("player_action_availability", "actions", true, true),
    pending("execute_player_action", "actions", true, true),
    pending("preview_player_action", "actions", true, true),
    pending("execute_panel_action", "actions", true, true),
    pending("panel_action_receipt", "actions", true, true),
    pending("preview_panel_action", "actions", true, true),
    pending("append_campaign_sequel", "campaigns", true, true),
    pending("campaign_sequel_context", "campaigns", true, true),
    pending("preview_campaign_sequel", "campaigns", true, true),
    pending("package_candidates", "packages", true, true),
    pending("campaign_rules_update_options", "campaigns", true, false),
    pending("preview_campaign_rules_update", "campaigns", true, false),
    pending("adopt_campaign_rules_update", "campaigns", true, false),
    pending("inspect_package_candidate", "packages", true, true),
    pending("package_candidate_text", "packages", true, true),
    pending("compare_package_candidates", "packages", true, true),
    pending("campaign_party_options", "campaigns", true, true),
    pending("model_choices", "providers", true, true),
    pending("provider_settings", "providers", true, true),
    pending("open_provider_setup", "providers", true, true),
    pending("chatgpt_connection", "providers", false, true),
    pending("claude_connection", "providers", false, true),
    pending("admin_full_audit_status", "providers", true, true),
    pending("admin_full_audit_record", "providers", true, true),
    pending("export_admin_full_audit", "providers", false, true),
    pending("set_admin_full_audit", "providers", true, true),
    pending("detect_local_providers", "providers", true, true),
    pending("discover_provider_models", "providers", true, true),
    pending("validate_provider_model", "providers", true, true),
    pending("save_provider_settings", "providers", true, true),
    pending("remove_provider_settings", "providers", true, true),
    pending("set_default_model", "providers", true, true),
    pending("set_chat_defaults", "providers", true, true),
    pending("switch_session_model", "providers", true, true),
    pending("switch_session_reasoning", "sessions", true, true),
    pending("switch_session_speed", "sessions", true, true),
    pending("inspect_provider_thinking", "providers", true, true),
    pending("session_context", "sessions", true, true),
    pending("session_history_page", "sessions", true, true),
    pending("ingest_builder_sources", "builder", false, true),
    pending("steer_session", "sessions", true, true),
    pending("submit_session_input", "sessions", true, true),
    pending("interrupt_stream", "sessions", true, true),
    pending("interrupt_provider_stream", "providers", true, true),
    pending("retryable_interruption", "campaigns", true, true),
    pending("manual_retry_session", "sessions", true, true),
    pending("disconnect_stream", "sessions", true, true),
    pending("disconnect_provider_stream", "providers", true, true),
    pending("stream_heartbeat", "sessions", true, true),
    pending("provider_stream_heartbeat", "providers", true, true),
    pending("narration_attempt_status", "sessions", true, true),
    pending("provider_stream_status", "providers", true, true),
    pending("reconnect_stream", "sessions", true, true),
    pending("reconnect_provider_stream", "providers", true, true),
    pending("install_package", "packages", false, true),
    pending("confirm_character_card_import", "packages", true, true),
    pending("preview_character_card_file", "packages", false, true),
    pending("install_package_file", "packages", false, true),
    pending("start_guided_campaign", "campaigns", true, true),
    pending("create_guided_campaign_draft", "campaigns", true, true),
    pending("navigate_guided_campaign_draft", "campaigns", true, true),
    pending("advance_guided_campaign_draft", "campaigns", true, true),
    pending("guided_campaign_contribution", "campaigns", true, true),
    pending("save_reusable_campaign_plan", "campaigns", true, true),
    pending("start_guided_creation_subflow", "campaigns", true, true),
    pending("return_guided_creation_subflow", "campaigns", true, true),
    pending("confirm_new_campaign", "campaigns", true, true),
    pending("begin_campaign_construction", "campaigns", true, true),
    pending("complete_campaign_construction", "campaigns", false, true),
    pending("model_guidance", "providers", true, true),
    pending("finalize_campaign_construction", "campaigns", true, true),
    pending("start_dm_session", "sessions", true, true),
    pending("catalog", "campaigns", true, true),
    pending("execute_action", "actions", false, true),
    pending("resolve_mechanics_choice", "actions", true, true),
    pending("open_turn", "actions", false, true),
    pending("complete_turn", "actions", false, true),
    pending("stop_agent_session", "sessions", true, true),
    pending("resume_agent_session", "sessions", true, true),
    pending("rename_campaign", "campaigns", true, true),
    pending("archive_campaign", "campaigns", true, true),
    pending("restore_archived_campaign", "campaigns", true, true),
    pending("trash_campaign", "campaigns", true, true),
    pending("restore_trashed_campaign", "campaigns", true, true),
    pending("purge_campaign", "campaigns", true, true),
    pending("backup_database", "campaigns", false, true),
    pending("export_campaign_plan_file", "campaigns", false, true),
    pending("export_package_candidate", "packages", false, true),
    pending("export_package_candidate_file", "packages", false, true),
    pending("export_campaign", "campaigns", false, true),
    pending("export_campaign_file", "campaigns", false, true),
    pending("recover_campaign", "campaigns", false, true),
    pending("recover_campaign_file", "campaigns", false, true),
    ported("cancel_display_load", "", true, false),
    ported("cancel_local_report", "", false, true),
    ported("load_desktop_drafts", "", true, false),
    ported("preview_local_report", "", false, true),
    ported("record_local_fault", "", true, true),
    ported("renderer", "", false, true),
    ported("save_desktop_drafts", "", true, false),
    ported("save_local_report", "", false, true),
];

/// Methods `owner_process.run` answers itself before consulting the registry.
pub const RUN_LOOP_METHODS: &[&str] = &[
    "shutdown",
    "desktop_update_confirm_startup",
    "desktop_update_state",
    "desktop_update_check",
    "desktop_update_download",
    "desktop_update_cancel",
    "desktop_update_defer",
    "desktop_update_prepare",
    "desktop_update_handoff",
    "desktop_update_reserve",
    "desktop_update_abort",
    "desktop_update_shutdown",
];

/// Look a registry command up by name.
pub fn find(name: &str) -> Option<&'static Command> {
    REGISTRY.iter().find(|command| command.name == name)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn table_matches_the_snapshot_registry() {
        assert_eq!(REGISTRY.len(), 125);
        let mut names: Vec<&str> = REGISTRY.iter().map(|command| command.name).collect();
        names.sort_unstable();
        names.dedup();
        assert_eq!(names.len(), 125, "command names are unique");
        assert_eq!(REGISTRY.iter().filter(|c| c.webui).count(), 100);
        assert_eq!(REGISTRY.iter().filter(|c| c.diagnostics).count(), 116);
    }
}
