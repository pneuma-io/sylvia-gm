# Lane 3 ledger: Storage and authority

Python snapshot `0da1010feb5a520f917f5d69ff8553ddb174e9e7`. 32 files, 36,959 lines.

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
| `src/sylvia_gm/infrastructure/owner_lock.py` | 68 | `sylvia-store` | ported | `owner_lock` (`OwnerLock`, `authority_lock_identity`). Python and Rust owners exclude each other both ways on Windows and Linux: `tests/vectors/lock_interop.py`. |
| `src/sylvia_gm/infrastructure/owner_process_probe.py` | 118 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/participant_history.py` | 32 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/private_records_archive.py` | 104 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/safe_file.py` | 607 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/sqlite_authority.py` | 13,487 | `sylvia-store` | in progress | Schema PR (`rust/lane-3/schema`): `store` (`_connect`, `_connect_read_only`, `_migrate`, `prepare_new_campaign_storage`, `_ensure_session_schema`, `_ensure_campaign_schema`, the `_upgrade_*` steps, `campaign_schema_is_active`, `close`), `verify` (`_verify_exact_schema_identity`), `schema` (migrations 1, 2, identities). Interim: `backup_database` is a plain online copy until the safe-file PR. Not yet run at startup: `_verify_stored_authority` (comes with the package-installation family PR) and `_ensure_purge_schema` callers (archive/purge family). Vectors: `sql/schema_vectors.json`, `tests/vectors/schema_fixtures.json`. Ported as Python behaves, do not fix: `_upgrade_predecessor_campaign_schema` is unreachable, because a schema-5 database in the predecessor or probe-01 state has no lifecycle tables and `_verify_exact_schema_identity` refuses it first ("Authority schema identity does not match its version") on open and on campaign advance (fixtures `v5_predecessor`, `v5_probe01`). Do not fix either: `_ensure_session_schema` refuses schema 5, 7, 8 and 9 ("Session schema cannot migrate from this authority version"); startup advances those to 12 first, so it is not reached. |
| `src/sylvia_gm/infrastructure/sqlite_builder_launch.py` | 319 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/sqlite_campaign.py` | 733 | `sylvia-store` | pending | Schema PR: migration 5 SQL, the identity delta, `apply_lifecycle_extension`, `apply_purge_extension`. `migrate_v4_participants` refuses a database it would change (published manifests or participants) until the package-installation family PR; `verify_f5_campaign_authority` (run at startup) comes with the lifecycle family PR. |
| `src/sylvia_gm/infrastructure/sqlite_continuity.py` | 601 | `sylvia-store` | pending | Schema PR: `MIGRATION_9_SQL`; `apply_migration` refuses a database with plans or DM evidence to seed until the turns-and-actions family PR, which also brings `verify_authority` (run at startup). |
| `src/sylvia_gm/infrastructure/sqlite_draft_history.py` | 37 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/sqlite_draft_lifecycle.py` | 117 | `sylvia-store` | in progress | Schema PR: `MIGRATION_10_SQL` and `apply_migration` (all of the migration). The receipts come with the lifecycle family PR. |
| `src/sylvia_gm/infrastructure/sqlite_effort.py` | 55 | `sylvia-store` | pending | Schema PR: `MIGRATION_12_SQL`, `apply_migration`. `update_preferences` comes with the sessions-and-messages family PR. |
| `src/sylvia_gm/infrastructure/sqlite_interview_navigation.py` | 89 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/sqlite_mechanics_choice.py` | 69 | `sylvia-store` | pending | Schema PR: `MIGRATION_7_SQL`, `apply_migration`. The reservations come with the turns-and-actions family PR. |
| `src/sylvia_gm/infrastructure/sqlite_packages.py` | 1,799 | `sylvia-store` | pending | Schema PR: `MIGRATION_3_SQL`, `migrate_legacy_campaigns`, `clear_stale_read_leases`. `verify_package_authority` (run at startup) and the rest come with the package-installation family PR. |
| `src/sylvia_gm/infrastructure/sqlite_reasoning.py` | 186 | `sylvia-store` | pending | Schema PR: `MIGRATION_11_SQL`, `apply_migration`. The records come with the sessions-and-messages family PR. |
| `src/sylvia_gm/infrastructure/sqlite_recovery.py` | 777 | `sylvia-store` | pending | Schema PR: `RECOVERY_EXTENSION_SQL`, `apply_recovery_extension`. Recovery itself comes with the archive, purge and restore family PR. |
| `src/sylvia_gm/infrastructure/sqlite_sequel.py` | 227 | `sylvia-store` | pending | |
| `src/sylvia_gm/infrastructure/sqlite_session.py` | 1,104 | `sylvia-store` | pending | Schema PR: `MIGRATION_4_SQL`, `MIGRATION_8_SQL`. The rest comes with the sessions-and-messages family PR. |
| `src/sylvia_gm/infrastructure/sqlite_state_ownership.py` | 438 | `sylvia-store` | pending | Schema PR: `MIGRATION_6_SQL`, `apply_migration` with the owner backfill, `trigger_identity`, `EXPECTED_TRIGGER_IDENTITY`. Owner derivation for writes comes with the turns-and-actions family PR. |
