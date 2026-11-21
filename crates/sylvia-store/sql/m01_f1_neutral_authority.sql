
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_name TEXT NOT NULL UNIQUE,
    sql_sha256 TEXT NOT NULL
);
CREATE TABLE packages (
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    capabilities_json TEXT NOT NULL,
    PRIMARY KEY (package_id, version, sha256)
);
CREATE TABLE package_dependencies (
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    dependency_order INTEGER NOT NULL CHECK (dependency_order >= 0),
    dependency_id TEXT NOT NULL,
    dependency_version TEXT NOT NULL,
    dependency_sha256 TEXT NOT NULL,
    dependency_kind TEXT NOT NULL,
    PRIMARY KEY (package_id, version, sha256, dependency_order),
    UNIQUE (package_id, version, sha256, dependency_id),
    FOREIGN KEY (package_id, version, sha256)
        REFERENCES packages(package_id, version, sha256),
    FOREIGN KEY (dependency_id, dependency_version, dependency_sha256)
        REFERENCES packages(package_id, version, sha256)
);
CREATE TABLE campaigns (
    campaign_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('active', 'archived', 'tombstoned')),
    writer TEXT NOT NULL CHECK (writer IN ('builder', 'dm', 'none')),
    revision INTEGER NOT NULL CHECK (revision >= 0),
    lock_hash TEXT NOT NULL
);
CREATE TABLE campaign_package_memberships (
    campaign_id TEXT NOT NULL,
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('primary', 'dependency')),
    load_order INTEGER NOT NULL CHECK (load_order >= 0),
    PRIMARY KEY (campaign_id, package_id, version, sha256),
    UNIQUE (campaign_id, load_order),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (package_id, version, sha256)
        REFERENCES packages(package_id, version, sha256)
);
CREATE TABLE participants (
    campaign_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    package_id TEXT NOT NULL,
    package_version TEXT NOT NULL,
    data_json TEXT NOT NULL,
    PRIMARY KEY (campaign_id, participant_id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE
);
CREATE TABLE campaign_state (
    campaign_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    state_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    PRIMARY KEY (campaign_id, namespace, state_key),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE
);
CREATE TABLE turns (
    campaign_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'completed')),
    created_revision INTEGER NOT NULL,
    PRIMARY KEY (campaign_id, turn_id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE
);
CREATE TABLE actions (
    campaign_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    expected_revision INTEGER NOT NULL,
    resulting_revision INTEGER NOT NULL,
    writer TEXT NOT NULL,
    package_id TEXT NOT NULL,
    package_version TEXT NOT NULL,
    package_sha256 TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('committed', 'failed')),
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    result_json TEXT NOT NULL,
    PRIMARY KEY (campaign_id, action_id),
    FOREIGN KEY (campaign_id, turn_id) REFERENCES turns(campaign_id, turn_id),
    FOREIGN KEY (package_id, package_version, package_sha256)
        REFERENCES packages(package_id, version, sha256),
    FOREIGN KEY (campaign_id, package_id, package_version, package_sha256)
        REFERENCES campaign_package_memberships(campaign_id, package_id, version, sha256)
);
CREATE TABLE canonical_events (
    campaign_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'action_declared', 'tool_call', 'tool_result', 'state_mutation',
        'consequence', 'action_committed', 'action_failed'
    )),
    turn_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    safe_payload_json TEXT NOT NULL,
    PRIMARY KEY (campaign_id, sequence),
    FOREIGN KEY (campaign_id, action_id) REFERENCES actions(campaign_id, action_id)
);
CREATE TABLE mutations (
    campaign_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    namespace TEXT NOT NULL,
    state_key TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    PRIMARY KEY (campaign_id, action_id, ordinal),
    FOREIGN KEY (campaign_id, action_id) REFERENCES actions(campaign_id, action_id)
);
CREATE TABLE consequences (
    campaign_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (campaign_id, action_id, ordinal),
    FOREIGN KEY (campaign_id, action_id) REFERENCES actions(campaign_id, action_id)
);
CREATE TABLE idempotency_records (
    campaign_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    action_id TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT NOT NULL,
    resulting_revision INTEGER NOT NULL,
    PRIMARY KEY (campaign_id, idempotency_key),
    FOREIGN KEY (campaign_id, action_id) REFERENCES actions(campaign_id, action_id)
);
CREATE TABLE campaign_revisions (
    campaign_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    action_id TEXT,
    PRIMARY KEY (campaign_id, revision),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (campaign_id, action_id) REFERENCES actions(campaign_id, action_id)
);
