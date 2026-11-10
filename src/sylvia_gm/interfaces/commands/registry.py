"""One Python command catalog; handler imports occur only on selected dispatch."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib import import_module
from types import MappingProxyType
from typing import Any, cast

from . import DispatchContext

Handler = Callable[[DispatchContext, Mapping[str, Any]], Any]


@dataclass(frozen=True)
class Command:
    name: str
    handler: Handler | None
    webui: bool
    diagnostics: bool


def _handler(topic: str, name: str) -> Handler:
    def invoke(ctx: DispatchContext, params: Mapping[str, Any]) -> Any:
        module = import_module(f"{__package__}.{topic}")
        handler = cast(Handler, getattr(module, name))
        return handler(ctx, params)
    return invoke


_COMMANDS = (
    Command('handshake_status', _handler('campaigns', 'handle_handshake_status'), False, True),
    Command('campaign_index', _handler('campaigns', 'handle_campaign_index'), True, True),
    Command(
        'campaign_creation_catalog', _handler('campaigns', 'handle_campaign_creation_catalog'),
        True, True,
    ),
    Command(
        'ready_campaign_catalog', _handler('campaigns', 'handle_ready_campaign_catalog'),
        True, False,
    ),
    Command(
        'customize_ready_campaign', _handler('campaigns', 'handle_customize_ready_campaign'),
        True, False,
    ),
    Command(
        'start_ready_campaign', _handler('campaigns', 'handle_customize_ready_campaign'),
        True, False,
    ),
    Command(
        'delete_reusable_library_entry', _handler('campaigns', 'handle_delete_reusable_library_entry'),
        True, True,
    ),
    Command('builder_chat_index', _handler('builder', 'handle_builder_chat_index'), True, True),
    Command('start_import_repair', _handler('builder', 'handle_start_import_repair'), True, True),
    Command('create_builder_chat', _handler('builder', 'handle_create_builder_chat'), True, True),
    Command('rename_builder_chat', _handler('builder', 'handle_rename_builder_chat'), True, True),
    Command('delete_builder_chat', _handler('builder', 'handle_delete_builder_chat'), True, True),
    Command('archive_builder_chat', _handler('builder', 'handle_archive_builder_chat'), True, True),
    Command('restore_builder_chat', _handler('builder', 'handle_archive_builder_chat'), True, True),
    Command('snapshot', _handler('campaigns', 'handle_snapshot'), False, True),
    Command('display_snapshot', _handler('campaigns', 'handle_display_snapshot'), True, True),
    Command('player_projection', _handler('campaigns', 'handle_player_projection'), True, True),
    Command(
        'player_projection_page', _handler('campaigns', 'handle_player_projection_page'),
        True, True,
    ),
    Command('campaign_panels', _handler('actions', 'handle_campaign_panels'), True, True),
    Command('action_sequence', _handler('actions', 'handle_action_sequence'), True, True),
    Command(
        'compose_action_sequence', _handler('actions', 'handle_compose_action_sequence'),
        True, True,
    ),
    Command(
        'execute_action_sequence', _handler('actions', 'handle_compose_action_sequence'),
        True, True,
    ),
    Command(
        'player_action_availability', _handler('actions', 'handle_player_action_availability'),
        True, True,
    ),
    Command(
        'execute_player_action', _handler('actions', 'handle_execute_player_action'),
        True, True,
    ),
    Command(
        'preview_player_action', _handler('actions', 'handle_execute_player_action'),
        True, True,
    ),
    Command('execute_panel_action', _handler('actions', 'handle_execute_panel_action'), True, True),
    Command('panel_action_receipt', _handler('actions', 'handle_execute_panel_action'), True, True),
    Command('preview_panel_action', _handler('actions', 'handle_execute_panel_action'), True, True),
    Command(
        'append_campaign_sequel', _handler('campaigns', 'handle_append_campaign_sequel'),
        True, True,
    ),
    Command(
        'campaign_sequel_context', _handler('campaigns', 'handle_append_campaign_sequel'),
        True, True,
    ),
    Command(
        'preview_campaign_sequel', _handler('campaigns', 'handle_append_campaign_sequel'),
        True, True,
    ),
    Command('package_candidates', _handler('packages', 'handle_package_candidates'), True, True),
    Command(
        'campaign_rules_update_options', _handler('campaigns', 'handle_campaign_rules_update_options'),
        True, False,
    ),
    Command(
        'preview_campaign_rules_update', _handler('campaigns', 'handle_preview_campaign_rules_update'),
        True, False,
    ),
    Command(
        'adopt_campaign_rules_update', _handler('campaigns', 'handle_adopt_campaign_rules_update'),
        True, False,
    ),
    Command(
        'inspect_package_candidate', _handler('packages', 'handle_inspect_package_candidate'),
        True, True,
    ),
    Command(
        'package_candidate_text', _handler('packages', 'handle_package_candidate_text'),
        True, True,
    ),
    Command(
        'compare_package_candidates', _handler('packages', 'handle_compare_package_candidates'),
        True, True,
    ),
    Command('rift_template', _handler('campaigns', 'handle_rift_template'), True, True),
    Command(
        'campaign_party_options', _handler('campaigns', 'handle_campaign_party_options'),
        True, True,
    ),
    Command('model_choices', _handler('providers', 'handle_model_choices'), True, True),
    Command('provider_settings', _handler('providers', 'handle_provider_settings'), True, True),
    Command('open_provider_setup', _handler('providers', 'handle_open_provider_setup'), True, True),
    Command('chatgpt_connection', _handler('providers', 'handle_chatgpt_connection'), False, True),
    Command(
        'admin_full_audit_status', _handler('providers', 'handle_admin_full_audit_status'),
        True, True,
    ),
    Command(
        'admin_full_audit_record', _handler('providers', 'handle_admin_full_audit_record'),
        True, True,
    ),
    Command(
        'export_admin_full_audit', _handler('providers', 'handle_export_admin_full_audit'),
        False, True,
    ),
    Command(
        'set_admin_full_audit', _handler('providers', 'handle_set_admin_full_audit'),
        True, True,
    ),
    Command(
        'detect_local_providers', _handler('providers', 'handle_detect_local_providers'),
        True, True,
    ),
    Command(
        'discover_provider_models', _handler('providers', 'handle_discover_provider_models'),
        True, True,
    ),
    Command(
        'validate_provider_model', _handler('providers', 'handle_validate_provider_model'),
        True, True,
    ),
    Command(
        'save_provider_settings', _handler('providers', 'handle_save_provider_settings'),
        True, True,
    ),
    Command(
        'remove_provider_settings', _handler('providers', 'handle_remove_provider_settings'),
        True, True,
    ),
    Command('set_default_model', _handler('providers', 'handle_set_default_model'), True, True),
    Command(
        'switch_session_model', _handler('providers', 'handle_switch_session_model'),
        True, True,
    ),
    Command(
        'switch_session_reasoning', _handler('sessions', 'handle_switch_session_reasoning'),
        True, True,
    ),
    Command(
        'inspect_provider_thinking', _handler('providers', 'handle_inspect_provider_thinking'),
        True, True,
    ),
    Command('session_context', _handler('sessions', 'handle_session_context'), True, True),
    Command('session_history_page', _handler('sessions', 'handle_session_context'), True, True),
    Command(
        'ingest_builder_sources', _handler('builder', 'handle_ingest_builder_sources'),
        False, True,
    ),
    Command('steer_session', _handler('sessions', 'handle_steer_session'), True, True),
    Command(
        'submit_session_input', _handler('sessions', 'handle_submit_session_input'),
        True, True,
    ),
    Command('interrupt_stream', _handler('sessions', 'handle_interrupt_stream'), True, True),
    Command(
        'interrupt_provider_stream', _handler('providers', 'handle_interrupt_provider_stream'),
        True, True,
    ),
    Command(
        'retryable_interruption', _handler('campaigns', 'handle_retryable_interruption'),
        True, True,
    ),
    Command(
        'manual_retry_session', _handler('sessions', 'handle_manual_retry_session'),
        True, True,
    ),
    Command('disconnect_stream', _handler('sessions', 'handle_disconnect_stream'), True, True),
    Command(
        'disconnect_provider_stream', _handler('providers', 'handle_disconnect_provider_stream'),
        True, True,
    ),
    Command('stream_heartbeat', _handler('sessions', 'handle_stream_heartbeat'), True, True),
    Command(
        'provider_stream_heartbeat', _handler('providers', 'handle_provider_stream_heartbeat'),
        True, True,
    ),
    Command(
        'narration_attempt_status', _handler('sessions', 'handle_narration_attempt_status'),
        True, True,
    ),
    Command(
        'provider_stream_status', _handler('providers', 'handle_provider_stream_status'),
        True, True,
    ),
    Command('reconnect_stream', _handler('sessions', 'handle_reconnect_stream'), True, True),
    Command(
        'reconnect_provider_stream', _handler('providers', 'handle_reconnect_provider_stream'),
        True, True,
    ),
    Command('install_package', _handler('packages', 'handle_install_package'), False, True),
    Command(
        'confirm_character_card_import', _handler('packages', 'handle_confirm_character_card_import'),
        True, True,
    ),
    Command(
        'preview_character_card_file', _handler('packages', 'handle_confirm_character_card_import'),
        False, True,
    ),
    Command(
        'install_package_file', _handler('packages', 'handle_install_package_file'),
        False, True,
    ),
    Command('start_rift_campaign', _handler('campaigns', 'handle_start_rift_campaign'), True, True),
    Command(
        'start_guided_campaign', _handler('campaigns', 'handle_start_guided_campaign'),
        True, True,
    ),
    Command(
        'create_guided_campaign_draft', _handler('campaigns', 'handle_create_guided_campaign_draft'),
        True, True,
    ),
    Command(
        'navigate_guided_campaign_draft', _handler('campaigns', 'handle_navigate_guided_campaign_draft'),
        True, True,
    ),
    Command(
    'advance_guided_campaign_draft',
    _handler('campaigns', 'handle_advance_guided_campaign_draft'), True, True),
    Command(
    'guided_campaign_contribution',
    _handler('campaigns', 'handle_guided_campaign_contribution'), True, True),
    Command(
    'save_reusable_campaign_plan',
    _handler('campaigns', 'handle_save_reusable_campaign_plan'), True, True),
    Command(
    'start_guided_creation_subflow',
    _handler('campaigns', 'handle_start_guided_creation_subflow'), True, True),
    Command(
    'return_guided_creation_subflow',
    _handler('campaigns', 'handle_return_guided_creation_subflow'), True, True),
    Command(
    'confirm_new_campaign', _handler('campaigns', 'handle_confirm_new_campaign'), True, True),
    Command(
    'begin_campaign_construction',
    _handler('campaigns', 'handle_begin_campaign_construction'), True, True),
    Command(
    'complete_campaign_construction',
    _handler('campaigns', 'handle_complete_campaign_construction'), False, True),
    Command('model_guidance', _handler('providers', 'handle_model_guidance'), True, True),
    Command(
    'finalize_campaign_construction',
    _handler('campaigns', 'handle_finalize_campaign_construction'), True, True),
    Command('start_dm_session', _handler('sessions', 'handle_start_dm_session'), True, True),
    Command('catalog', _handler('campaigns', 'handle_catalog'), True, True),
    Command('execute_action', _handler('actions', 'handle_execute_action'), False, True),
    Command(
    'resolve_mechanics_choice', _handler('actions', 'handle_resolve_mechanics_choice'), True, True),
    Command('open_turn', _handler('actions', 'handle_open_turn'), False, True),
    Command('complete_turn', _handler('actions', 'handle_complete_turn'), False, True),
    Command('stop_agent_session', _handler('sessions', 'handle_stop_agent_session'), True, True),
    Command(
    'resume_agent_session', _handler('sessions', 'handle_resume_agent_session'), True, True),
    Command('rename_campaign', _handler('campaigns', 'handle_rename_campaign'), True, True),
    Command('archive_campaign', _handler('campaigns', 'handle_archive_campaign'), True, True),
    Command(
    'restore_archived_campaign',
    _handler('campaigns', 'handle_restore_archived_campaign'), True, True),
    Command('trash_campaign', _handler('campaigns', 'handle_trash_campaign'), True, True),
    Command(
    'restore_trashed_campaign',
    _handler('campaigns', 'handle_restore_trashed_campaign'), True, True),
    Command('purge_campaign', _handler('campaigns', 'handle_purge_campaign'), True, True),
    Command('backup_database', _handler('campaigns', 'handle_backup_database'), False, True),
    Command(
    'export_campaign_plan_file',
    _handler('campaigns', 'handle_export_campaign_plan_file'), False, True),
    Command(
    'export_package_candidate',
    _handler('packages', 'handle_export_package_candidate'), False, True),
    Command(
    'export_package_candidate_file',
    _handler('packages', 'handle_export_package_candidate'), False, True),
    Command('export_campaign', _handler('campaigns', 'handle_export_campaign'), False, True),
    Command('export_campaign_file', _handler('campaigns', 'handle_export_campaign'), False, True),
    Command('recover_campaign', _handler('campaigns', 'handle_recover_campaign'), False, True),
    Command(
    'recover_campaign_file', _handler('campaigns', 'handle_recover_campaign_file'), False, True),
    Command('cancel_display_load', None, True, False),
    Command('cancel_local_report', None, False, True),
    Command('load_desktop_drafts', None, True, False),
    Command('preview_local_report', None, False, True),
    Command('record_local_fault', None, True, True),
    Command('renderer', None, False, True),
    Command('save_desktop_drafts', None, True, False),
    Command('save_local_report', None, False, True),
)
COMMAND_REGISTRY: Mapping[str, Command] = (
    MappingProxyType({command.name: command for command in _COMMANDS})
)
WEBUI_COMMANDS = frozenset(command.name for command in _COMMANDS if command.webui)
DIAGNOSTIC_OPERATIONS = frozenset(command.name for command in _COMMANDS if command.diagnostics)
