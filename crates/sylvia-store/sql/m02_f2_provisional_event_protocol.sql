
ALTER TABLE canonical_events RENAME TO canonical_events_f1;
CREATE TABLE canonical_events (
    campaign_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'think', 'talk', 'action_declared', 'tool_call', 'tool_result',
        'state_mutation', 'consequence', 'action_committed', 'action_failed'
    )),
    turn_id TEXT NOT NULL,
    action_id TEXT,
    safe_payload_json TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility IN ('internal_safe', 'player_safe')),
    attempt_id TEXT,
    attempt_ordinal INTEGER,
    PRIMARY KEY (campaign_id, sequence),
    UNIQUE (campaign_id, attempt_id, attempt_ordinal),
    FOREIGN KEY (campaign_id, action_id) REFERENCES actions(campaign_id, action_id),
    CHECK (
        (
            event_type IN ('think', 'talk')
            AND action_id IS NULL
            AND visibility = 'player_safe'
            AND attempt_id IS NOT NULL
            AND attempt_ordinal IS NOT NULL
            AND attempt_ordinal >= 0
        ) OR (
            event_type NOT IN ('think', 'talk')
            AND action_id IS NOT NULL
            AND attempt_id IS NULL
            AND attempt_ordinal IS NULL
            AND (
                (event_type IN ('tool_call', 'tool_result') AND visibility = 'player_safe')
                OR
                (event_type NOT IN ('tool_call', 'tool_result')
                    AND visibility = 'internal_safe')
            )
        )
    )
);
INSERT INTO canonical_events (
    campaign_id, sequence, event_type, turn_id, action_id, safe_payload_json,
    visibility, attempt_id, attempt_ordinal
)
SELECT campaign_id, sequence, event_type, turn_id, action_id, safe_payload_json,
       CASE WHEN event_type IN ('tool_call', 'tool_result')
            THEN 'player_safe' ELSE 'internal_safe' END,
       NULL, NULL
FROM canonical_events_f1;
DROP TABLE canonical_events_f1;
CREATE TABLE narration_interruptions (
    campaign_id TEXT NOT NULL,
    interruption_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    last_action_id TEXT,
    failed_attempt_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('retryable', 'resolved')),
    resolving_retry_id TEXT,
    resolved_sequence INTEGER,
    PRIMARY KEY (campaign_id, interruption_id),
    UNIQUE (campaign_id, failed_attempt_id),
    UNIQUE (campaign_id, resolving_retry_id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (campaign_id, turn_id) REFERENCES turns(campaign_id, turn_id),
    FOREIGN KEY (campaign_id, last_action_id) REFERENCES actions(campaign_id, action_id),
    FOREIGN KEY (campaign_id, resolved_sequence)
        REFERENCES canonical_events(campaign_id, sequence),
    CHECK (
        (status = 'retryable' AND resolved_sequence IS NULL)
        OR
        (status = 'resolved' AND resolving_retry_id IS NOT NULL
            AND resolved_sequence IS NOT NULL)
    )
);
