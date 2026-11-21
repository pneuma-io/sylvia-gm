
CREATE TABLE standalone_builder_chats (
    session_id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'active', 'stopped', 'completed', 'review_required'
    )),
    display_name TEXT NOT NULL CHECK (
        length(display_name) BETWEEN 1 AND 120 AND display_name = trim(display_name)
    ),
    created_ordinal INTEGER NOT NULL UNIQUE CHECK (created_ordinal > 0),
    payload_sha256 TEXT NOT NULL
);
CREATE TABLE standalone_builder_chat_messages (
    session_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    message_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN (
        'user', 'safe_think', 'talk', 'steering', 'evidence', 'human_review'
    )),
    content_json TEXT NOT NULL,
    canonical_sequence INTEGER CHECK (canonical_sequence IS NULL),
    PRIMARY KEY (session_id, sequence),
    UNIQUE (session_id, message_id),
    FOREIGN KEY (session_id) REFERENCES standalone_builder_chats(session_id) ON DELETE CASCADE
);
