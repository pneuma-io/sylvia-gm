
ALTER TABLE participants ADD COLUMN package_sha256 TEXT;
ALTER TABLE participants ADD COLUMN schema_id TEXT;
ALTER TABLE participants ADD COLUMN schema_sha256 TEXT;
ALTER TABLE participants ADD COLUMN validation_status TEXT NOT NULL DEFAULT 'quarantined'
    CHECK (validation_status IN ('validated', 'quarantined'));

CREATE TABLE package_participant_artifacts (
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    artifact_order INTEGER NOT NULL CHECK (artifact_order >= 0),
    kind TEXT NOT NULL,
    schema_id TEXT NOT NULL,
    schema_sha256 TEXT NOT NULL,
    schema_json TEXT NOT NULL,
    PRIMARY KEY (package_id, version, content_sha256, artifact_order),
    UNIQUE (package_id, version, content_sha256, kind),
    FOREIGN KEY (package_id, version, content_sha256)
        REFERENCES package_manifests(package_id, version, content_sha256) ON DELETE CASCADE
);
CREATE TABLE participant_quarantines (
    campaign_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    PRIMARY KEY (campaign_id, participant_id),
    FOREIGN KEY (campaign_id, participant_id)
        REFERENCES participants(campaign_id, participant_id) ON DELETE CASCADE
);
CREATE TABLE participant_adaptations (
    operation_id TEXT PRIMARY KEY,
    payload_sha256 TEXT NOT NULL,
    source_campaign_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_package_id TEXT NOT NULL,
    source_package_version TEXT NOT NULL,
    source_package_sha256 TEXT,
    source_schema_id TEXT,
    source_schema_sha256 TEXT,
    source_data_sha256 TEXT NOT NULL,
    destination_kind TEXT NOT NULL,
    destination_package_id TEXT NOT NULL,
    destination_package_version TEXT NOT NULL,
    destination_package_sha256 TEXT NOT NULL,
    destination_schema_id TEXT NOT NULL,
    destination_schema_sha256 TEXT NOT NULL,
    data_json TEXT NOT NULL,
    FOREIGN KEY (
        destination_package_id, destination_package_version,
        destination_package_sha256, destination_kind
    ) REFERENCES package_participant_artifacts(
        package_id, version, content_sha256, kind
    )
);
ALTER TABLE participants ADD COLUMN adaptation_id TEXT
    REFERENCES participant_adaptations(operation_id);
