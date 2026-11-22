# Lane 4 ledger: Providers and agents

Python snapshot `0da1010feb5a520f917f5d69ff8553ddb174e9e7`. 111 files, 48,215 lines.

One row per Python file this lane retires. Status is `pending`, `in progress`,
`ported` (Rust replacement merged and its comparison checks pass) or `retired`
(no Rust replacement needed; say why in Notes). Only this lane's worker edits this
file; a row moves to another lane only through the manager.

Note for the agent-layer work: `TokenUsage` counts are `PyValue` (Python keeps a complete response's counts as the SDK held them, `None` included). Every consumer that computes with or compares a count must call the helper Python's code applies there (`PyValue::int_or_zero` for `int(x or 0)`, `PyValue::as_int`), so it fails where Python fails.

| Python file | Lines | Rust target | Status | Notes |
| --- | ---: | --- | --- | --- |
| `src/sylvia_gm/agent/__init__.py` | 49 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_activity.py` | 114 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_actor_decisions.py` | 318 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_argument_contracts.py` | 258 | `sylvia-agent` | in progress | `argument_contracts` (P2): `CONTRACT_KEY`, `ARGUMENTS_TOOL_NAME`, `ArgumentContract` (`canonical`, `selected`), `argument_contract`, `envelope_schema` and `ARGUMENTS_TOOL` (exported to `provider_session/tool_guidance.json`). Remaining, with the turn loop: `selected_schema`, `unwrap_arguments` (needs a Draft 2020-12 validator) and `read_tool_arguments`. Vectors: `tests/tool_projection_vectors.rs`. |
| `src/sylvia_gm/agent/_builder_question_answers.py` | 127 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_builder_reference_copy_tool.py` | 98 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_builder_session.py` | 2,709 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_builder_source_tool.py` | 75 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_builder_title.py` | 64 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_campaign_memory.py` | 97 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_character_draft_tool.py` | 99 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_character_profile_tools.py` | 350 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_character_reuse_tool.py` | 106 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_content_definition_tool.py` | 140 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_context_ledger.py` | 1,991 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_fixed_tool_projection.py` | 124 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_history.py` | 62 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_import_repair_tools.py` | 73 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_inference_identity.py` | 18 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_library_source_copy_tool.py` | 156 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_library_source_tool.py` | 146 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_mode.py` | 13 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_native_continuation.py` | 151 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_participant_file_tool.py` | 82 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_plan_draft_check_tool.py` | 50 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_provider_resolver.py` | 636 | `sylvia-agent` | ported | `provider_resolver`: `_configured_provider_models`, `ConfiguredProviderResolver` (`model_choices`, `__call__`). The ChatGPT saved-model refresh and local discovery are injected `RuntimeHooks`, and providers come from the registry's `ProviderFactory`; the owner passes pending ones until those services are ported. Vectors: `tests/resolver_vectors.rs` |
| `src/sylvia_gm/agent/_provider_runtime.py` | 38 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_provider_session.py` | 4,258 | `sylvia-agent` | in progress | `provider_session::tools` (P2): `_tool_alias`, `_package_tool_alias`, `_resolve_provider_tool_alias`, `_tool_description`, `_model_tool_schema` and `_provider_tools`; the two long provider-only argument schemas are exported to `tool_guidance.json` by `generate_tool_guidance.py`. Vectors: `tests/tool_projection_vectors.rs` (every SRD Lite tool through the catalog's model projection, the application tools, synthetic refusals). Next: the opening preflight (P1, P3, P4). |
| `src/sylvia_gm/agent/_provider_turn_decisions.py` | 250 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_provider_turn_phases.py` | 3,156 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_provider_turn_state.py` | 185 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_request_data.py` | 129 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_ruleset_draft_check_tool.py` | 75 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_ruleset_test_coverage.py` | 93 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_ruleset_test_provider.py` | 153 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_ruleset_test_runner.py` | 348 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_ruleset_test_tool.py` | 70 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_runner.py` | 2,549 | `sylvia-agent` | pending | Port without the `store` (`StateAPI`) and `reveal_guard` parameters and every `DM_SESSION` branch: no live caller in the 0.4.1 snapshot. The constructor refuses `DM_SESSION` before any of that code runs, and its two callers (`agent/_builder_session.py`) pass `BUILDER_FREEFORM`. If the Rust mode type can still hold `DM_SESSION`, construction refuses it with Python's `ValueError` text; if it cannot, this row will say the mode is not representable. |
| `src/sylvia_gm/agent/_session_loop.py` | 1,953 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_skills.py` | 59 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_stateless_mcp.py` | 2,828 | `sylvia-agent` | in progress | `stateless_mcp::ModelMechanicsTool` (P2), for lane 3's catalog work. The rest (the MCP listing and calls, `_model_input_schema`, the action-sequence and Campaign File tools) is pending. |
| `src/sylvia_gm/agent/_tool_exposure.py` | 209 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_tool_loader.py` | 464 | `sylvia-agent` | pending | |
| `src/sylvia_gm/agent/_working_context.py` | 766 | `sylvia-agent` | pending | |
| `src/sylvia_gm/application/character_chat.py` | 697 | `sylvia-app::character_chat` | pending | |
| `src/sylvia_gm/config/__init__.py` | 23 | `sylvia-provider::config` | ported | `config` (module root and re-exports). |
| `src/sylvia_gm/config/_credential_protection.py` | 291 | `sylvia-provider::config` | ported | `config::protection`: the envelope over a `Seal`; Windows DPAPI in `protection/windows.rs` (the approved FFI module), the Linux key file and AES-256-GCM in `protection/linux.rs`. Vectors: `tests/config_vectors.rs` (envelopes through a shared fake seal), AES-GCM bytes for fixed keys and nonces against Python's `AESGCM`; DPAPI opened both ways against Python with disposable values (`dpapi_envelopes_open_on_both_sides`, opt-in). |
| `src/sylvia_gm/config/_env.py` | 101 | `sylvia-provider::config` | ported | `config::env` over an `Environment` snapshot (Windows names upper-cased and matched without case, as `os.environ`). Vectors: `tests/config_vectors.rs` |
| `src/sylvia_gm/config/_host_browser.py` | 94 | `sylvia-provider::config` | ported | `config::host_browser`: `host_environment` and the `xdg-open` path exactly; Windows `ShellExecuteW` as `os.startfile` (approved FFI module `host_browser/windows.rs`). The `webbrowser` registry outside the AppImage is a differences row. Vectors: `tests/settings_vectors.rs` |
| `src/sylvia_gm/config/_merge.py` | 29 | `sylvia-provider::config` | ported | `config::merge::deep_merge`. Vectors: `tests/config_vectors.rs` |
| `src/sylvia_gm/config/_resolver.py` | 68 | `sylvia-provider::config` | ported | `config::resolver`. Vectors: `tests/config_vectors.rs` |
| `src/sylvia_gm/config/_user_settings.py` | 103 | `sylvia-provider::config` | ported | `config::user_settings`: read with migration, atomic write (mode 0600 temporary file, sync, rename), the reentrant `CONFIG_LOCK`. Vectors: `tests/config_vectors.rs` |
| `src/sylvia_gm/config/chatgpt_settings.py` | 169 | `sylvia-provider::config` | pending | |
| `src/sylvia_gm/config/claude_settings.py` | 293 | `sylvia-provider::config` | pending | |
| `src/sylvia_gm/config/loader.py` | 293 | `sylvia-provider::config` | ported | `config::loader`: `load_config` as every caller runs it (no `.env`, credentials sealed; python-dotenv not ported, as nothing reaches it), pydantic's lax validation, `provider_credentials`. The shipped defaults' path is the caller's (`_DEFAULT_CONFIG_PATH`). Vectors: `tests/config_vectors.rs` (66 cases) |
| `src/sylvia_gm/config/provider_settings.py` | 2,250 | `sylvia-provider::config` | ported | Read-only part ported (C2) in `config::provider_settings`: `provider_settings`, `open_provider_setup`, `require_configured_model`, the session speed and model-once readers, `_discovery_model`, and the presets (`config::presets`, generated by `src/generate_tables.py`). Vectors: `tests/settings_vectors.rs`. The writes (C4) in `config::provider_writes`: `save_provider_settings`, `remove_provider_settings`, `set_default_model_settings`, `set_chat_defaults_settings`, `set_session_speed_settings`, `set_session_model_once_settings`, `clear_session_model_once_settings`, `refresh_subscription_reasoning`. Vectors: `tests/writes_vectors.rs`. Discovery (D1) in `config::discovery`: `discover_provider_models` (the bounded `/models` catalogue with Anthropic's pagination, the Featherless plan, LM Studio and Ollama capacity probes, search and paging) and `detect_local_providers`, over a per-step client (`network_clients`). Vectors: `tests/discovery_vectors.rs`. Validation (D2) in `config::validation`: `validate_provider_model`, the bounded `/models` read and one streamed tool-calling probe per transport, read with httpx's `iter_lines` rules. Vectors: `tests/validation_vectors.rs`. |
| `src/sylvia_gm/provider/__init__.py` | 144 | `sylvia-provider` | pending | |
| `src/sylvia_gm/provider/_admin_audit.py` | 417 | `sylvia-provider` | pending | |
| `src/sylvia_gm/provider/_anthropic.py` | 1,145 | `sylvia-provider` | pending | |
| `src/sylvia_gm/provider/_base.py` | 519 | `sylvia-provider` | ported | `types`, `provider` (trait, `FakeProvider`). `closing_provider_stream` and `release_provider_idle` become dropping the stream and the trait's default `release_idle`. `TimeoutConfig.to_httpx_timeout` lands with the HTTP client. Vectors: `tests/base_vectors.rs` |
| `src/sylvia_gm/provider/_chat_metadata.py` | 93 | `sylvia-provider` | ported | `chat::ChatCompletionMetadata`, reading SDK models with `construct` semantics (`pyobj`). Vectors: `tests/chat_vectors.rs` |
| `src/sylvia_gm/provider/_chatgpt.py` | 1,522 | `sylvia-provider` | pending | |
| `src/sylvia_gm/provider/_chatgpt_account.py` | 177 | `sylvia-provider` | pending | |
| `src/sylvia_gm/provider/_chatgpt_model_facts.py` | 61 | `sylvia-provider` | pending | |
| `src/sylvia_gm/provider/_claude.py` | 1,601 | `sylvia-provider` | pending | |
| `src/sylvia_gm/provider/_claude_install.py` | 387 | `sylvia-provider` | pending | |
| `src/sylvia_gm/provider/_claude_kept.py` | 365 | `sylvia-provider` | pending | |
| `src/sylvia_gm/provider/_claude_native.py` | 484 | `sylvia-provider` | pending | |
| `src/sylvia_gm/provider/_claude_runtime.py` | 453 | `sylvia-provider` | pending | |
| `src/sylvia_gm/provider/_codex_rpc.py` | 427 | `sylvia-provider` | pending | |
| `src/sylvia_gm/provider/_context_capacity.py` | 150 | `sylvia-provider` | ported | `context_capacity`. Vectors: `tests/settings_vectors.rs` |
| `src/sylvia_gm/provider/_context_fit.py` | 408 | `sylvia-provider` | ported | `context_fit`: `RequestTokenMeasurement`, `ContextFittingProvider` (`admit_request`, the fitted `generate`/`generate_stream`), `GM_MODE_MINIMUM_CONTEXT_TOKENS`, and owner_process's `_context_fitted_provider` as `context_fitted_provider` (lane 5's `owner_process.py` row notes it). The capabilities it finds by attribute are default methods on `Provider` (`hooks`, `wrapped`, `request_representation`, `record_context_fit`, `set_audit_context`; `provider::capability` walks the wrappers). `count_request_tokens` is not ported: no provider in the 0.4.1 snapshot defines it, so its branch is unreachable. Vectors: `tests/context_fit_vectors.rs` |
| `src/sylvia_gm/provider/_diagnostics.py` | 135 | `sylvia-provider` | pending | |
| `src/sylvia_gm/provider/_exceptions.py` | 179 | `sylvia-provider` | ported | `error` (one type, `ErrorKind` walks the class tree for `isinstance`), owner sentences in `reply::provider_error_reply` from the owner's own handler. Vectors: `tests/base_vectors.rs` |
| `src/sylvia_gm/provider/_factory.py` | 98 | `sylvia-provider` | in progress | `factory::DefaultProviderFactory`: Python's checks for every transport, retry defaults, inference URLs, and Chat Completions as an `OpenAICompatProvider` over its own `NetworkTransport` (the owner's factory). Responses, Anthropic Messages, Gemini and the Claude and ChatGPT runtimes answer `rust_port_pending` until their adapters are ported. Vectors: `tests/factory_vectors.rs` |
| `src/sylvia_gm/provider/_gemini.py` | 418 | `sylvia-provider` | pending | |
| `src/sylvia_gm/provider/_hooks.py` | 642 | `sylvia-provider` | in progress | `hooks`: `HookContext`, `ProviderHook`, `HookManager` (registry order, failing hooks logged). Remaining with their consumers: the built-in hooks, task-scoped hooks (`scoped`, `late_usage_listeners`, used by the admin audit). A hook's error context holds the converted `ProviderError` where Python passes the raw exception (only the never-enabled `DumpHook` reads more) |
| `src/sylvia_gm/provider/_lifecycle.py` | 75 | `sylvia-provider` | pending | |
| `src/sylvia_gm/provider/_model_facts.py` | 277 | `sylvia-provider` | in progress | `model_facts`, its tables generated by `src/generate_tables.py`. Remaining: `uses_gm_narration_hint`, which reads `ModelInfo` (C3). |
| `src/sylvia_gm/provider/_models.py` | 235 | `sylvia-provider` | ported | `catalog::build_catalog`. Vectors: `tests/model_settings_vectors.rs` |
| `src/sylvia_gm/provider/_native_output.py` | 87 | `sylvia-provider` | in progress | `chat::split_chat_content` and `sse::DoneTracker` ported (vectors: `tests/chat_vectors.rs`); `native_output` and `responses_finish` come with the Responses transport |
| `src/sylvia_gm/provider/_openai_compat.py` | 1,339 | `sylvia-provider` | in progress | Chat Completions ported: the transport in `chat`, the SDK's SSE reading in `sse`, and `OpenAICompatProvider` in `openai_compat` over the `http` seam (URL via `httpx_url`, header order, retries, pool recycling, hooks, timeouts, which failures fire `on_error` or escape). Vectors: `tests/chat_vectors.rs`, `tests/provider_vectors.rs` (the real Python provider through an httpx mock). The network client is `net::NetworkTransport` (reqwest, HTTP/1.1, httpx's proxy rules in `proxy` including the Windows registry (read through lane 5's `sylvia_base::winreg`), `SSL_CERT_FILE`/`SSL_CERT_DIR`, cookie jar, redirects, timeouts; `tests/proxy_vectors.rs`, `tests/net_harness.rs`). Remaining: `ResponsesTransport`. Admin audit note for later: Python stores `_plain(tool_call_delta)`, which for Chat Completions is the pydantic repr string of a `ChoiceDeltaToolCall` |
| `src/sylvia_gm/provider/_output_policy.py` | 26 | `sylvia-provider` | ported | `catalog::output_allowance`. Vectors: `tests/model_settings_vectors.rs` |
| `src/sylvia_gm/provider/_protocol_routes.py` | 63 | `sylvia-provider` | ported | `routes::{is_opencode_inference, inference_base_url, model_protocol}`. Vectors: `tests/provider_vectors.rs`, `tests/settings_vectors.rs` |
| `src/sylvia_gm/provider/_reasoning.py` | 379 | `sylvia-provider` | ported | `reasoning`: `reasoning_values`, `configure_reasoning`, `mode_reasoning_default`, `ReasoningSelection`, `select_reasoning`, `TurnReasoningProvider` and `bind_session_reasoning` (given the session's role and effort preferences). Vectors: `tests/model_settings_vectors.rs`, `tests/context_fit_vectors.rs` |
| `src/sylvia_gm/provider/_registry.py` | 393 | `sylvia-provider` | ported | `registry::ProviderRegistry` over a `ProviderFactory`. `ModelDiscovery`, `discover_models`, `configure_providers`, `list_providers`, `get_recommended` and `model_count` are not ported: no live caller in the 0.4.1 snapshot. Vectors: `tests/model_settings_vectors.rs` |
| `src/sylvia_gm/provider/_request_metadata.py` | 139 | `sylvia-provider` | ported | `request_metadata`: the OpenCode headers and `SessionBoundRequestMetadataProvider`, with its `provider_request_representation`. Vectors: `tests/model_settings_vectors.rs`, `tests/context_fit_vectors.rs` |
| `src/sylvia_gm/provider/_retry.py` | 113 | `sylvia-provider` | ported | `retry::{build_delay, retry_with_backoff}`; jitter is random in both. Warnings go to stderr as Python's `logging.lastResort` prints them. Vectors: `tests/base_vectors.rs` |
| `src/sylvia_gm/provider/_retry_after.py` | 25 | `sylvia-provider` | ported | `retry::retry_after_seconds` with `email.utils.parsedate_to_datetime` ported; numbers through `sylvia_base::pystr::parse_float`/`parse_int` (Unicode digits included). Not ported: a date token starting with a non-decimal digit that `str.isdigit` accepts (superscripts), which fails as a date in both. Vectors: `tests/base_vectors.rs` |
| `src/sylvia_gm/provider/_speed.py` | 178 | `sylvia-provider` | ported | `speed`. Vectors: `tests/model_settings_vectors.rs` |
| `src/sylvia_gm/provider/_stream_health.py` | 76 | `sylvia-provider` | ported | `stream_health::StreamHealthMonitor`. Vectors: `tests/base_vectors.rs` |
| `src/sylvia_gm/provider/_token_estimate.py` | 52 | `sylvia-provider` | ported | `token_estimate`: `TokenEstimateHook` and `token_estimator` (found again through `ProviderHook::as_any`). Vectors: `tests/context_fit_vectors.rs` |
| `src/sylvia_gm/provider/_tool_delta.py` | 32 | `sylvia-provider` | ported | `tool_delta::parse_tool_call_delta` over ordered JSON fragments (SDK objects and dicts read the same; checked in the generator). Vectors: `tests/base_vectors.rs` |
| `src/sylvia_gm/provider/_usage_tracker.py` | 171 | `sylvia-provider` | pending | |
| `src/sylvia_gm/session/__init__.py` | 57 | `sylvia-agent` | pending | |
| `src/sylvia_gm/session/_context.py` | 302 | `sylvia-agent` | pending | Its `StateAPI` import is type-checking only and is dropped (no live caller in the 0.4.1 snapshot). |
| `src/sylvia_gm/session/_context_compressor.py` | 508 | `sylvia-agent` | pending | |
| `src/sylvia_gm/session/_divergence.py` | 235 | `sylvia-agent` | pending | |
| `src/sylvia_gm/session/_history.py` | 8 | `sylvia-agent` | pending | |
| `src/sylvia_gm/session/_narrative_scanner.py` | 234 | `sylvia-agent` | pending | |
| `src/sylvia_gm/session/_npc_dialogue.py` | 210 | `sylvia-agent` | pending | Its `StateAPI` import is type-checking only and is dropped (no live caller in the 0.4.1 snapshot). |
| `src/sylvia_gm/session/_prompt_assembler.py` | 1,133 | `sylvia-agent` | pending | |
| `src/sylvia_gm/session/_reveal_guard.py` | 223 | none | retired | Retired: no live caller in the 0.4.1 snapshot. Its only user is `AgentRunner`, whose `reveal_guard` is always `None`. |
| `src/sylvia_gm/session/_token_budget.py` | 442 | `sylvia-agent` | pending | |
| `src/sylvia_gm/tools/__init__.py` | 22 | `sylvia-agent` | pending | |
| `src/sylvia_gm/tools/_builder_handlers.py` | 504 | `sylvia-agent` | pending | `tool_create_campaign_folder` is not ported: no live caller in the 0.4.1 snapshot (`create_campaign_folder` is never registered). Builder prompt text that names it stays byte-exact. |
| `src/sylvia_gm/tools/_converter.py` | 65 | `sylvia-agent` | pending | |
| `src/sylvia_gm/tools/_dm_campaign_handlers.py` | 252 | `sylvia-agent` | pending | |
| `src/sylvia_gm/tools/_registry.py` | 254 | `sylvia-agent` | pending | |
| `src/sylvia_gm/tools/_spec.py` | 126 | `sylvia-agent` | pending | |
| `src/sylvia_gm/tools/_toolsets.py` | 55 | `sylvia-agent` | pending | |
