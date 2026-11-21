
CREATE TABLE campaign_recovery_receipts (
    recovery_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL UNIQUE,
    semantic_payload_json TEXT NOT NULL,
    semantic_payload_sha256 TEXT NOT NULL,
    archive_sha256 TEXT NOT NULL,
    archive_byte_count INTEGER NOT NULL CHECK (archive_byte_count >= 0),
    captured_revision INTEGER NOT NULL CHECK (captured_revision >= 0),
    result_json TEXT NOT NULL,
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
);
