# Lane 5 ledger: Owner, clients and release

Python snapshot `0da1010feb5a520f917f5d69ff8553ddb174e9e7`. 53 files, 18,055 lines.

One row per Python file this lane retires. Status is `pending`, `in progress`,
`ported` (Rust replacement merged and its comparison checks pass) or `retired`
(no Rust replacement needed; say why in Notes). Only this lane's worker edits this
file; a row moves to another lane only through the manager.

| Python file | Lines | Rust target | Status | Notes |
| --- | ---: | --- | --- | --- |
| `src/sylvia_gm/__init__.py` | 6 | `sylvia-owner` | pending | |
| `src/sylvia_gm/__main__.py` | 6 | `sylvia-owner` | pending | |
| `src/sylvia_gm/application/desktop_update/__init__.py` | 1 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/checkpoint.py` | 418 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/controller.py` | 383 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/discovery.py` | 113 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/drafts.py` | 102 | `sylvia-owner::desktop_update::drafts` | ported | `local-services` |
| `src/sylvia_gm/application/desktop_update/helper.py` | 433 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/identity.py` | 39 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/installed.py` | 410 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/launch.py` | 71 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/manifest.py` | 458 | `sylvia-owner::desktop_update::manifest` | in progress | `bounded_json`, `canonical_bytes`, `MANIFEST_LIMIT` only |
| `src/sylvia_gm/application/desktop_update/payload.py` | 139 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/preparation.py` | 337 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/processes.py` | 171 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/progress.py` | 313 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/recovery.py` | 309 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/recovery_service.py` | 197 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/replacement.py` | 418 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/restart.py` | 368 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/runtime.py` | 63 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/service.py` | 334 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/startup.py` | 221 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/storage.py` | 404 | `sylvia-owner::desktop_update::storage` | in progress | `operation_id`, `directory_identity`, `checked_file`, `atomic_receipt` only |
| `src/sylvia_gm/application/desktop_update/transport.py` | 374 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/launch_compatibility.py` | 31 | `sylvia-app::launch_compatibility` | pending | |
| `src/sylvia_gm/build_workspace.py` | 67 | `sylvia-owner` | pending | |
| `src/sylvia_gm/core/__init__.py` | 1 | `sylvia-owner` | pending | |
| `src/sylvia_gm/covered_source_delivery.py` | 116 | `sylvia-owner` | pending | |
| `src/sylvia_gm/interfaces/__init__.py` | 22 | `sylvia-owner` | pending | |
| `src/sylvia_gm/interfaces/_chat_start.py` | 143 | `sylvia-owner` | pending | |
| `src/sylvia_gm/interfaces/_display_reads.py` | 61 | `sylvia-owner::display_reads` | ported | `local-services` |
| `src/sylvia_gm/interfaces/_local_diagnostics.py` | 438 | `sylvia-owner::diagnostics` | ported | `local-services` (replies and report content; `runtime.jsonl` itself is volatile, covered by unit tests); Python quirk kept: `record()` returns the owner session id, not a record id, once closed |
| `src/sylvia_gm/interfaces/_session_reasoning.py` | 177 | `sylvia-owner` | pending | |
| `src/sylvia_gm/interfaces/commands/__init__.py` | 31 | `sylvia-owner` | pending | |
| `src/sylvia_gm/interfaces/commands/actions.py` | 157 | `sylvia-owner` | pending | |
| `src/sylvia_gm/interfaces/commands/builder.py` | 169 | `sylvia-owner::commands::builder` | in progress | `handle_builder_chat_index` |
| `src/sylvia_gm/interfaces/commands/campaigns.py` | 1,118 | `sylvia-owner::commands::campaigns` | in progress | `handle_handshake_status`, `handle_campaign_index` |
| `src/sylvia_gm/interfaces/commands/character_chat.py` | 112 | `sylvia-owner` | pending | |
| `src/sylvia_gm/interfaces/commands/packages.py` | 127 | `sylvia-owner` | pending | |
| `src/sylvia_gm/interfaces/commands/providers.py` | 322 | `sylvia-owner` | pending | |
| `src/sylvia_gm/interfaces/commands/registry.py` | 354 | `sylvia-owner` | pending | |
| `src/sylvia_gm/interfaces/commands/sessions.py` | 1,416 | `sylvia-owner` | pending | |
| `src/sylvia_gm/interfaces/local_api.py` | 820 | `sylvia-owner` | in progress | `handshake_status` only |
| `src/sylvia_gm/interfaces/local_owner.py` | 1,444 | `sylvia-owner` | pending | |
| `src/sylvia_gm/interfaces/owner_process.py` | 2,245 | `sylvia-owner::run` | in progress | run loop: execution model, local services, diagnostics on replies, update gates (state from the desktop update port), provider-work reservation, the provider error path (lane 4 `provider_error_reply`); launch opens and migrates the store (lane 3 `StoreHandle`). Pending: `acquire_owner` and internal handshake, bundled package installs, Builder migrations, desktop update methods |
| `src/sylvia_gm/interfaces/ruleset_test_worker.py` | 96 | `sylvia-owner` | pending | |
| `src/sylvia_gm/interfaces/webui.py` | 847 | `sylvia-owner` | pending | |
| `src/sylvia_gm/local_dictation.py` | 346 | `sylvia-owner` | pending | |
| `src/sylvia_gm/local_dictation_process.py` | 195 | `sylvia-owner` | pending | |
| `src/sylvia_gm/main.py` | 67 | `sylvia-owner` | pending | |
| `src/sylvia_gm/portable_artifact.py` | 219 | `sylvia-owner` | pending | |
| `src/sylvia_gm/release_artifact.py` | 826 | `sylvia-owner` | pending | |
