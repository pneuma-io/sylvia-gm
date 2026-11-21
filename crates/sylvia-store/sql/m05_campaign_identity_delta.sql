
ALTER TABLE campaign_revisions ADD COLUMN construction_operation_id TEXT;
CREATE TABLE authority_schema_extensions (
    extension_name TEXT PRIMARY KEY,
    extension_sha256 TEXT NOT NULL,
    predecessor_migration_name TEXT NOT NULL,
    predecessor_sql_sha256 TEXT NOT NULL
);
CREATE TABLE campaign_operations (
    operation_id TEXT PRIMARY KEY,
    operation_kind TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    semantic_payload_json TEXT NOT NULL,
    semantic_payload_sha256 TEXT NOT NULL,
    result_json TEXT NOT NULL,
    basis_turn_id TEXT NOT NULL,
    basis_lock_id TEXT NOT NULL,
    basis_revision INTEGER NOT NULL CHECK (basis_revision >= 0),
    resulting_revision INTEGER NOT NULL CHECK (resulting_revision >= 0),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (campaign_id, basis_turn_id) REFERENCES turns(campaign_id, turn_id),
    FOREIGN KEY (basis_lock_id) REFERENCES campaign_locks(lock_id)
);
CREATE TABLE campaign_constructions (
    campaign_id TEXT PRIMARY KEY,
    confirmation_operation_id TEXT NOT NULL UNIQUE,
    authority_turn_id TEXT NOT NULL,
    authority_lock_id TEXT NOT NULL,
    confirmation_revision INTEGER NOT NULL CHECK (confirmation_revision = 0),
    lane TEXT CHECK (lane IN ('quick_start', 'customize')),
    canonical_input_json TEXT,
    begin_operation_id TEXT UNIQUE,
    begin_revision INTEGER CHECK (begin_revision > 0),
    canonical_plan_json TEXT,
    completion_operation_id TEXT UNIQUE,
    completion_revision INTEGER CHECK (completion_revision > 0),
    canonical_representation_sha256 TEXT,
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (confirmation_operation_id) REFERENCES campaign_operations(operation_id),
    FOREIGN KEY (begin_operation_id) REFERENCES campaign_operations(operation_id),
    FOREIGN KEY (completion_operation_id) REFERENCES campaign_operations(operation_id),
    FOREIGN KEY (campaign_id, authority_turn_id) REFERENCES turns(campaign_id, turn_id),
    FOREIGN KEY (authority_lock_id) REFERENCES campaign_locks(lock_id),
    CHECK (
        (lane IS NULL AND canonical_input_json IS NULL AND begin_operation_id IS NULL
            AND begin_revision IS NULL AND canonical_plan_json IS NULL
            AND completion_operation_id IS NULL AND completion_revision IS NULL
            AND canonical_representation_sha256 IS NULL)
        OR
        (lane IS NOT NULL AND canonical_input_json IS NOT NULL AND begin_operation_id IS NOT NULL
            AND begin_revision IS NOT NULL AND canonical_plan_json IS NULL
            AND completion_operation_id IS NULL AND completion_revision IS NULL
            AND canonical_representation_sha256 IS NULL)
        OR
        (lane IS NOT NULL AND canonical_input_json IS NOT NULL AND begin_operation_id IS NOT NULL
            AND begin_revision IS NOT NULL AND canonical_plan_json IS NOT NULL
            AND completion_operation_id IS NOT NULL AND completion_revision IS NOT NULL
            AND canonical_representation_sha256 IS NOT NULL)
    )
);
