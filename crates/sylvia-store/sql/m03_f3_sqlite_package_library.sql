
ALTER TABLE package_dependencies ADD COLUMN dependency_namespace TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE campaigns ADD COLUMN current_lock_id TEXT;
ALTER TABLE actions ADD COLUMN lock_id TEXT;
ALTER TABLE campaign_revisions ADD COLUMN activation_id TEXT;

CREATE TABLE package_blobs (
    blob_sha256 TEXT PRIMARY KEY,
    byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
    chunk_count INTEGER NOT NULL CHECK (chunk_count >= 0)
);
CREATE TABLE package_blob_chunks (
    blob_sha256 TEXT NOT NULL,
    chunk_order INTEGER NOT NULL CHECK (chunk_order >= 0),
    byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
    chunk_sha256 TEXT NOT NULL,
    data BLOB NOT NULL,
    PRIMARY KEY (blob_sha256, chunk_order),
    FOREIGN KEY (blob_sha256) REFERENCES package_blobs(blob_sha256) ON DELETE CASCADE
);
CREATE TABLE package_manifests (
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('primary', 'dependency')),
    canonical_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    compatibility_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('published', 'reclaimed')),
    PRIMARY KEY (package_id, version, content_sha256),
    FOREIGN KEY (package_id, version, content_sha256)
        REFERENCES packages(package_id, version, sha256)
);
CREATE TABLE package_manifest_files (
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    file_order INTEGER NOT NULL CHECK (file_order >= 0),
    portable_path TEXT NOT NULL,
    byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
    blob_sha256 TEXT NOT NULL,
    PRIMARY KEY (package_id, version, content_sha256, file_order),
    UNIQUE (package_id, version, content_sha256, portable_path),
    FOREIGN KEY (package_id, version, content_sha256)
        REFERENCES package_manifests(package_id, version, content_sha256) ON DELETE CASCADE,
    FOREIGN KEY (blob_sha256) REFERENCES package_blobs(blob_sha256)
);
CREATE TABLE package_exact_dependencies (
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    dependency_order INTEGER NOT NULL CHECK (dependency_order >= 0),
    dependency_namespace TEXT NOT NULL,
    dependency_kind TEXT NOT NULL,
    dependency_id TEXT NOT NULL,
    dependency_version TEXT NOT NULL,
    dependency_content_sha256 TEXT NOT NULL,
    PRIMARY KEY (package_id, version, content_sha256, dependency_order),
    UNIQUE (package_id, version, content_sha256, dependency_namespace),
    FOREIGN KEY (package_id, version, content_sha256)
        REFERENCES package_manifests(package_id, version, content_sha256) ON DELETE CASCADE,
    FOREIGN KEY (dependency_id, dependency_version, dependency_content_sha256)
        REFERENCES package_manifests(package_id, version, content_sha256)
);
CREATE TABLE package_capabilities (
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    capability_order INTEGER NOT NULL CHECK (capability_order >= 0),
    capability TEXT NOT NULL,
    PRIMARY KEY (package_id, version, content_sha256, capability_order),
    UNIQUE (package_id, version, content_sha256, capability),
    FOREIGN KEY (package_id, version, content_sha256)
        REFERENCES package_manifests(package_id, version, content_sha256) ON DELETE CASCADE
);
CREATE TABLE package_tools (
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    tool_order INTEGER NOT NULL CHECK (tool_order >= 0),
    tool_name TEXT NOT NULL,
    capability TEXT NOT NULL,
    input_schema_json TEXT NOT NULL,
    state_predicates_json TEXT NOT NULL,
    turn_statuses_json TEXT NOT NULL,
    writers_json TEXT NOT NULL,
    PRIMARY KEY (package_id, version, content_sha256, tool_order),
    UNIQUE (package_id, version, content_sha256, tool_name),
    FOREIGN KEY (package_id, version, content_sha256)
        REFERENCES package_manifests(package_id, version, content_sha256) ON DELETE CASCADE
);
CREATE TABLE package_prompt_fragments (
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    surface_order INTEGER NOT NULL CHECK (surface_order >= 0),
    fragment_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    PRIMARY KEY (package_id, version, content_sha256, surface_order),
    UNIQUE (package_id, version, content_sha256, fragment_id),
    FOREIGN KEY (package_id, version, content_sha256)
        REFERENCES package_manifests(package_id, version, content_sha256) ON DELETE CASCADE
);
CREATE TABLE package_ui_surfaces (
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    surface_order INTEGER NOT NULL CHECK (surface_order >= 0),
    surface_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    PRIMARY KEY (package_id, version, content_sha256, surface_order),
    UNIQUE (package_id, version, content_sha256, surface_id),
    FOREIGN KEY (package_id, version, content_sha256)
        REFERENCES package_manifests(package_id, version, content_sha256) ON DELETE CASCADE
);
CREATE TABLE package_staging (
    install_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('staging', 'published', 'failed')),
    manifest_json TEXT NOT NULL,
    report_json TEXT NOT NULL
);
CREATE TABLE package_staging_chunks (
    install_id TEXT NOT NULL,
    file_order INTEGER NOT NULL CHECK (file_order >= 0),
    chunk_order INTEGER NOT NULL CHECK (chunk_order >= 0),
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    portable_path TEXT NOT NULL,
    chunk_sha256 TEXT NOT NULL,
    data BLOB NOT NULL,
    PRIMARY KEY (install_id, file_order, chunk_order),
    FOREIGN KEY (install_id) REFERENCES package_staging(install_id) ON DELETE CASCADE
);
CREATE TABLE package_candidates (
    candidate_id TEXT PRIMARY KEY,
    install_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('validated', 'activated', 'failed')),
    closure_sha256 TEXT NOT NULL,
    report_json TEXT NOT NULL,
    FOREIGN KEY (install_id) REFERENCES package_staging(install_id)
);
CREATE TABLE package_candidate_memberships (
    candidate_id TEXT NOT NULL,
    closure_order INTEGER NOT NULL CHECK (closure_order >= 0),
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    PRIMARY KEY (candidate_id, closure_order),
    UNIQUE (candidate_id, package_id),
    FOREIGN KEY (candidate_id) REFERENCES package_candidates(candidate_id) ON DELETE CASCADE,
    FOREIGN KEY (package_id, version, content_sha256)
        REFERENCES package_manifests(package_id, version, content_sha256)
);
CREATE TABLE campaign_locks (
    lock_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    closure_sha256 TEXT NOT NULL,
    availability TEXT NOT NULL CHECK (availability IN ('available', 'legacy_unavailable')),
    predecessor_lock_id TEXT,
    created_revision INTEGER NOT NULL CHECK (created_revision >= 0),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (predecessor_lock_id) REFERENCES campaign_locks(lock_id)
);
CREATE TABLE campaign_lock_memberships (
    lock_id TEXT NOT NULL,
    closure_order INTEGER NOT NULL CHECK (closure_order >= 0),
    package_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('primary', 'dependency')),
    PRIMARY KEY (lock_id, closure_order),
    UNIQUE (lock_id, package_id),
    FOREIGN KEY (lock_id) REFERENCES campaign_locks(lock_id) ON DELETE CASCADE,
    FOREIGN KEY (package_id, version, content_sha256)
        REFERENCES packages(package_id, version, sha256)
);
CREATE TABLE package_activations (
    activation_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    old_lock_id TEXT NOT NULL,
    new_lock_id TEXT NOT NULL,
    migration_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('committed', 'failed')),
    resulting_revision INTEGER NOT NULL,
    result_json TEXT NOT NULL,
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_id) REFERENCES package_candidates(candidate_id),
    FOREIGN KEY (old_lock_id) REFERENCES campaign_locks(lock_id),
    FOREIGN KEY (new_lock_id) REFERENCES campaign_locks(lock_id)
);
CREATE TABLE package_read_leases (
    lease_id TEXT PRIMARY KEY,
    blob_sha256 TEXT NOT NULL,
    FOREIGN KEY (blob_sha256) REFERENCES package_blobs(blob_sha256)
);
CREATE TABLE package_recovery_roots (
    root_id TEXT PRIMARY KEY,
    blob_sha256 TEXT NOT NULL,
    FOREIGN KEY (blob_sha256) REFERENCES package_blobs(blob_sha256)
);
