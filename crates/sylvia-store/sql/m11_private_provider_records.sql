CREATE TABLE provider_private_records (
    session_id TEXT NOT NULL,
    agent_session_id TEXT,
    builder_session_id TEXT,
    attempt_id TEXT NOT NULL,
    response_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    basis_message_id TEXT NOT NULL,
    source_order INTEGER NOT NULL CHECK (source_order >= 0),
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('thinking', 'continuation')),
    payload TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('partial', 'completed', 'interrupted')),
    PRIMARY KEY (session_id, attempt_id, source_order),
    FOREIGN KEY (agent_session_id) REFERENCES agent_sessions(session_id) ON DELETE CASCADE,
    FOREIGN KEY (builder_session_id) REFERENCES standalone_builder_chats(session_id)
        ON DELETE CASCADE,
    CHECK (
        (agent_session_id = session_id AND builder_session_id IS NULL)
        OR (builder_session_id = session_id AND agent_session_id IS NULL)
    ),
    CHECK (agent_session_id IS NOT NULL OR builder_session_id IS NOT NULL)
);