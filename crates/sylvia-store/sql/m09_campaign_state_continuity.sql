
ALTER TABLE campaign_revisions
ADD COLUMN continuity_operation_id TEXT REFERENCES campaign_operations(operation_id);
CREATE TABLE campaign_continuity_records (
    campaign_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    entry_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('scene', 'fact', 'entity', 'reveal', 'plan_progress')),
    status TEXT NOT NULL CHECK (status IN ('active', 'retired')),
    payload_json TEXT NOT NULL,
    basis_message_ids_json TEXT NOT NULL,
    operation_id TEXT,
    admitted_revision INTEGER NOT NULL CHECK (admitted_revision >= 0),
    PRIMARY KEY (campaign_id, sequence),
    UNIQUE (campaign_id, entry_id),
    UNIQUE (operation_id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (operation_id) REFERENCES campaign_operations(operation_id)
);
CREATE INDEX campaign_continuity_record_lookup
ON campaign_continuity_records(campaign_id, kind, record_id, sequence);
