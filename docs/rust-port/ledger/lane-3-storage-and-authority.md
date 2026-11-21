# Lane 3 ledger: Storage and authority

Python snapshot `0da1010feb5a520f917f5d69ff8553ddb174e9e7`. 34 files, 37,421 lines.

One row per Python file this lane retires. Status is `pending`, `in progress`,
`ported` (Rust replacement merged and its comparison checks pass) or `retired`
(no Rust replacement needed; say why in Notes). Only this lane's worker edits this
file; a row moves to another lane only through the manager.

| Python file | Lines | Rust target | Status | Notes |
| --- | ---: | --- | --- | --- |
| `src/sylvia_gm/application/authority.py` | 9,922 | `sylvia-store::authority` | pending | |
| `src/sylvia_gm/application/campaign_inventory.py` | 231 | `sylvia-app::campaign_inventory` | pending | |
| `src/sylvia_gm/application/campaign_rules_update.py` | 816 | `sylvia-app::campaign_rules_update` | pending | |
| `src/sylvia_gm/application/campaign_sequels.py` | 274 | `sylvia-app::campaign_sequels` | pending | |
| `src/sylvia_gm/application/knowledge_search.py` | 227 | `sylvia-app::knowledge_search` | pending | |
| `src/sylvia_gm/application/ruleless_play.py` | 238 | `sylvia-app::ruleless_play` | pending | |
| `src/sylvia_gm/application/session.py` | 256 | `sylvia-app::session` | pending | |
| `src/sylvia_gm/infrastructure/__init__.py` | 1 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/builder_source_ingestion.py` | 571 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/builder_workspace_migration.py` | 130 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/campaign_archive.py` | 3,188 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/launch_handshake.py` | 138 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/owner_lock.py` | 68 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/owner_process_probe.py` | 118 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/package_archive.py` | 365 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/participant_history.py` | 32 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/private_records_archive.py` | 104 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/safe_file.py` | 607 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/sqlite_authority.py` | 13,487 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/sqlite_builder_launch.py` | 319 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/sqlite_campaign.py` | 733 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/sqlite_continuity.py` | 601 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/sqlite_draft_history.py` | 37 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/sqlite_draft_lifecycle.py` | 117 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/sqlite_effort.py` | 55 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/sqlite_interview_navigation.py` | 89 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/sqlite_mechanics_choice.py` | 69 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/sqlite_packages.py` | 1,799 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/sqlite_reasoning.py` | 186 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/sqlite_recovery.py` | 777 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/sqlite_sequel.py` | 227 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/sqlite_session.py` | 1,104 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/sqlite_state_ownership.py` | 438 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/zip_bounds.py` | 97 | `sylvia-store` | pending | |
