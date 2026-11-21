
CREATE TABLE agent_sessions (
    session_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('builder', 'dm')),
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'active', 'stopped', 'completed', 'review_required', 'handoff_closed'
    )),
    parent_session_id TEXT,
    selected_skill TEXT CHECK (selected_skill IN (
        'BUILD RULESET', 'BUILD CAMPAIGN SETTING', 'BUILD CAMPAIGN PLAN'
    )),
    scope_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (parent_session_id) REFERENCES agent_sessions(session_id),
    CHECK (
        (role = 'builder' AND selected_skill IS NOT NULL)
        OR (role = 'dm' AND selected_skill IS NULL)
    )
);
CREATE TABLE agent_session_messages (
    session_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    message_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN (
        'user', 'safe_think', 'talk', 'steering', 'evidence',
        'canonical_reference', 'human_review'
    )),
    content_json TEXT NOT NULL,
    canonical_sequence INTEGER,
    PRIMARY KEY (session_id, sequence),
    UNIQUE (session_id, message_id),
    FOREIGN KEY (session_id) REFERENCES agent_sessions(session_id) ON DELETE CASCADE,
    CHECK (
        (kind = 'canonical_reference' AND canonical_sequence IS NOT NULL)
        OR (kind != 'canonical_reference' AND canonical_sequence IS NULL)
    )
);
CREATE TABLE campaign_plans (
    plan_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    package_lock_id TEXT NOT NULL,
    source_session_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    content_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    validation_outcome TEXT NOT NULL CHECK (validation_outcome = 'valid'),
    acceptance_status TEXT NOT NULL CHECK (acceptance_status = 'accepted'),
    plan_revision INTEGER NOT NULL CHECK (plan_revision > 0),
    resulting_campaign_revision INTEGER NOT NULL CHECK (resulting_campaign_revision > 0),
    payload_sha256 TEXT NOT NULL,
    UNIQUE (campaign_id, plan_revision),
    UNIQUE (campaign_id, resulting_campaign_revision),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (package_lock_id) REFERENCES campaign_locks(lock_id),
    FOREIGN KEY (source_session_id) REFERENCES agent_sessions(session_id)
);
ALTER TABLE campaign_revisions ADD COLUMN plan_id TEXT;
ALTER TABLE campaign_revisions ADD COLUMN handoff_id TEXT;
CREATE TABLE builder_dm_handoffs (
    handoff_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL UNIQUE,
    payload_sha256 TEXT NOT NULL,
    builder_session_id TEXT NOT NULL,
    dm_session_id TEXT NOT NULL UNIQUE,
    package_lock_id TEXT NOT NULL,
    package_candidate_id TEXT,
    plan_id TEXT NOT NULL,
    plan_revision INTEGER NOT NULL CHECK (plan_revision > 0),
    plan_content_sha256 TEXT NOT NULL,
    expected_revision INTEGER NOT NULL CHECK (expected_revision >= 0),
    resulting_campaign_revision INTEGER NOT NULL CHECK (
        resulting_campaign_revision = expected_revision + 1
    ),
    chronology_ordinal INTEGER NOT NULL CHECK (chronology_ordinal = 1),
    completed_turn_id TEXT NOT NULL,
    safe_payload_json TEXT NOT NULL,
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (builder_session_id) REFERENCES agent_sessions(session_id),
    FOREIGN KEY (dm_session_id) REFERENCES agent_sessions(session_id),
    FOREIGN KEY (package_lock_id) REFERENCES campaign_locks(lock_id),
    FOREIGN KEY (package_candidate_id) REFERENCES package_candidates(candidate_id),
    FOREIGN KEY (plan_id) REFERENCES campaign_plans(plan_id),
    FOREIGN KEY (campaign_id, completed_turn_id) REFERENCES turns(campaign_id, turn_id)
);
