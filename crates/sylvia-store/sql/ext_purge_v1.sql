PRAGMA defer_foreign_keys = ON;
CREATE TEMP TABLE campaign_operations_purge_shadow AS SELECT operation_id, operation_kind, campaign_id, semantic_payload_json, semantic_payload_sha256, result_json, basis_turn_id, basis_lock_id, basis_revision, resulting_revision FROM campaign_operations;
DROP TABLE campaign_operations;
CREATE TABLE campaign_operations (
    operation_id TEXT PRIMARY KEY,
    operation_kind TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    semantic_payload_json TEXT NOT NULL,
    semantic_payload_sha256 TEXT NOT NULL,
    result_json TEXT NOT NULL,
    basis_turn_id TEXT,
    basis_lock_id TEXT,
    basis_revision INTEGER NOT NULL CHECK (basis_revision >= 0),
    resulting_revision INTEGER NOT NULL CHECK (resulting_revision >= 0),
    FOREIGN KEY (campaign_id, basis_turn_id) REFERENCES turns(campaign_id, turn_id),
    FOREIGN KEY (basis_lock_id) REFERENCES campaign_locks(lock_id),
    CHECK (
        (operation_kind = 'purge_campaign'
            AND basis_turn_id IS NULL AND basis_lock_id IS NULL)
        OR
        (operation_kind != 'purge_campaign'
            AND basis_turn_id IS NOT NULL AND basis_lock_id IS NOT NULL)
    )
);
INSERT INTO campaign_operations (operation_id, operation_kind, campaign_id, semantic_payload_json, semantic_payload_sha256, result_json, basis_turn_id, basis_lock_id, basis_revision, resulting_revision) SELECT operation_id, operation_kind, campaign_id, semantic_payload_json, semantic_payload_sha256, result_json, basis_turn_id, basis_lock_id, basis_revision, resulting_revision FROM campaign_operations_purge_shadow;
DROP TABLE campaign_operations_purge_shadow;