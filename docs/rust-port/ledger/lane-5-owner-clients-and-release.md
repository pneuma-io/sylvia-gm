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
| `src/sylvia_gm/application/desktop_update/controller.py` | 383 | `sylvia-owner::desktop_update::controller` | ported | `controller_vectors` (check, download, cancel, defer, prepare, arm shutdown and restore over a scripted transport, every refusal); one blocking task per operation |
| `src/sylvia_gm/application/desktop_update/discovery.py` | 113 | `sylvia-owner::desktop_update::discovery` | ported | `update_vectors` (release index selection and the retained current manifest, over a scripted transport) |
| `src/sylvia_gm/application/desktop_update/drafts.py` | 102 | `sylvia-owner::desktop_update::drafts` | ported | `local-services` |
| `src/sylvia_gm/application/desktop_update/helper.py` | 433 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/identity.py` | 39 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/installed.py` | 410 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/launch.py` | 71 | `sylvia-owner::desktop_update::launch` | ported | `controller_vectors` (`from_launch` and `for_recovery` on 20 contexts, Windows) |
| `src/sylvia_gm/application/desktop_update/manifest.py` | 458 | `sylvia-owner::desktop_update::manifest` | ported | `update_vectors` (SemVer and its order, strict base64, Ed25519 as OpenSSL 3.0 verifies it, release locations through `desktop_update::url`, payload paths, every manifest refusal), `local-services` |
| `src/sylvia_gm/application/desktop_update/payload.py` | 139 | `sylvia-owner::desktop_update::payload` | in progress | `verify_artifact` (`update_vectors`); `extract_portable` comes with preparation |
| `src/sylvia_gm/application/desktop_update/preparation.py` | 337 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/processes.py` | 171 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/progress.py` | 313 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/recovery.py` | 309 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/recovery_service.py` | 197 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/replacement.py` | 418 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/restart.py` | 368 | `sylvia-owner::desktop_update` | pending | |
| `src/sylvia_gm/application/desktop_update/runtime.py` | 63 | `sylvia-owner::desktop_update::runtime` | in progress | `child_environment` (the engineering-override refusal; the PyInstaller `PATH` clean-up has no Rust counterpart). `system_child_libraries` comes with the helper |
| `src/sylvia_gm/application/desktop_update/service.py` | 334 | `sylvia-owner::desktop_update::service` | in progress | `desktop-update` (the shipped build: no trusted key), `controller_vectors` (a pinned test key over a scripted transport). Pending: `prepare`, `handoff` and `arm_shutdown` past their checks before preparation, which need `preparation.py` |
| `src/sylvia_gm/application/desktop_update/startup.py` | 221 | `sylvia-owner::desktop_update::startup` | in progress | `desktop-update`: an ordinary start (refused after an interrupted apply, a missing database path or an unreadable receipt) and a restart request with no retained update. Pending: a restart request past its checks, `AuthorizedStartup.confirm`, which need `preparation.py` and `restart.py` |
| `src/sylvia_gm/application/desktop_update/storage.py` | 404 | `sylvia-owner::desktop_update::storage` | ported | `controller_vectors`, `update_vectors`, `desktop-update`, `local-services`; `UPDATE_MOVES` kept as data, as Python's tests use it |
| `src/sylvia_gm/application/desktop_update/transport.py` | 374 | `sylvia-owner::desktop_update::transport` | in progress | `update_vectors`: `official_request`, the redirect and final-location rules, `content_length`, `metadata_bytes`, `download_artifact` over a `Transport` trait. Pending: `HttpsTransport` (the client, its cancellation and error mapping), with the controller |
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
| `src/sylvia_gm/interfaces/commands/builder.py` | 169 | `sylvia-owner::commands::builder` | in progress | `handle_builder_chat_index`, `handle_rename_builder_chat`, `handle_delete_builder_chat` (on lane 3's `StoreHandle` operations and lanes). A rename and a delete of an existing chat wait for `create_builder_chat` in the owner |
| `src/sylvia_gm/interfaces/commands/campaigns.py` | 1,118 | `sylvia-owner::commands::campaigns` | in progress | `handle_handshake_status`, `handle_campaign_index` |
| `src/sylvia_gm/interfaces/commands/character_chat.py` | 112 | `sylvia-owner` | pending | |
| `src/sylvia_gm/interfaces/commands/packages.py` | 127 | `sylvia-owner` | pending | |
| `src/sylvia_gm/interfaces/commands/providers.py` | 322 | `sylvia-owner` | pending | |
| `src/sylvia_gm/interfaces/commands/registry.py` | 354 | `sylvia-owner` | pending | |
| `src/sylvia_gm/interfaces/commands/sessions.py` | 1,416 | `sylvia-owner` | pending | |
| `src/sylvia_gm/interfaces/local_api.py` | 820 | `sylvia-owner` | in progress | `handshake_status` only |
| `src/sylvia_gm/interfaces/local_owner.py` | 1,444 | `sylvia-owner` | pending | |
| `src/sylvia_gm/interfaces/owner_process.py` | 2,245 | `sylvia-owner::run` | in progress | run loop: execution model, local services, diagnostics on replies, provider-work reservation, the provider error path (lane 4 `provider_error_reply`); launch opens and migrates the store (lane 3 `StoreHandle`). The desktop update (`desktop-update`): the startup check before data opens and again before migrations, every `desktop_update_*` method, the reservation and both gates. Pending: bundled package installs, Builder migrations, the recovery-only owner (`run_recovery`) and confirming an update restart (both need update preparation) |
| `src/sylvia_gm/interfaces/ruleset_test_worker.py` | 96 | `sylvia-owner` | pending | |
| `src/sylvia_gm/interfaces/webui.py` | 847 | `sylvia-owner::webui` | in progress | Ported whole. `webui-guard` and `webui-remote` match the Python host on every response. Not yet proven by a session: an export with its download and a successful upload, which wait for the owner's export and install commands; `tests/stop_resume_owner_contract.cjs` against the Rust host waits for the turn commands |
| `src/sylvia_gm/local_dictation.py` | 346 | `sylvia-owner` | pending | |
| `src/sylvia_gm/local_dictation_process.py` | 195 | `sylvia-owner` | pending | |
| `src/sylvia_gm/main.py` | 67 | `sylvia-owner` (`sylvia-gm` binary) | in progress | The CLI: `--version`, `--webui` and its options. It starts `sylvia-gm-owner` from its own folder; packaging decides the final layout |
| `src/sylvia_gm/portable_artifact.py` | 219 | `sylvia-owner` | pending | |
| `src/sylvia_gm/release_artifact.py` | 826 | `sylvia-owner` | pending | |
