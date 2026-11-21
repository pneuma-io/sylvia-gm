ALTER TABLE campaign_revisions ADD COLUMN lifecycle_operation_id TEXT;

CREATE TABLE campaign_lifecycle_state (
    campaign_id TEXT PRIMARY KEY,
    retained_active_writer TEXT,
    designated_session_id TEXT,
    tombstone_prior_lifecycle TEXT,
    tombstone_prior_writer TEXT,
    tombstone_prior_session_id TEXT,
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (designated_session_id) REFERENCES agent_sessions(session_id),
    FOREIGN KEY (tombstone_prior_session_id) REFERENCES agent_sessions(session_id),
    CHECK (retained_active_writer IS NULL OR retained_active_writer IN ('builder', 'dm')),
    CHECK (
        tombstone_prior_lifecycle IS NULL
        OR tombstone_prior_lifecycle IN ('active', 'archived')
    ),
    CHECK (tombstone_prior_writer IS NULL OR tombstone_prior_writer IN ('none', 'builder', 'dm'))
);

CREATE TABLE campaign_lifecycle_transitions (
    operation_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    operation_kind TEXT NOT NULL,
    basis_revision INTEGER NOT NULL,
    resulting_revision INTEGER NOT NULL,
    from_lifecycle TEXT NOT NULL,
    to_lifecycle TEXT NOT NULL,
    from_writer TEXT NOT NULL,
    to_writer TEXT NOT NULL,
    designated_session_id TEXT,
    FOREIGN KEY (operation_id) REFERENCES campaign_operations(operation_id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (designated_session_id) REFERENCES agent_sessions(session_id),
    UNIQUE (campaign_id, resulting_revision),
    CHECK (
        operation_kind IN (
            'rename_campaign', 'archive_campaign', 'restore_campaign', 'trash_campaign'
        )
    ),
    CHECK (resulting_revision = basis_revision + 1),
    CHECK (from_lifecycle IN ('active', 'archived', 'tombstoned')),
    CHECK (to_lifecycle IN ('active', 'archived', 'tombstoned')),
    CHECK (from_writer IN ('none', 'builder', 'dm')),
    CHECK (to_writer IN ('none', 'builder', 'dm'))
);

INSERT INTO campaign_lifecycle_state
    (campaign_id, retained_active_writer, designated_session_id,
     tombstone_prior_lifecycle, tombstone_prior_writer, tombstone_prior_session_id)
SELECT campaign_id,
       CASE WHEN lifecycle = 'active' THEN writer ELSE NULL END,
       NULL, NULL, NULL, NULL
FROM campaigns;