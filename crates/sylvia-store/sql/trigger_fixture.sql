
            CREATE TABLE campaigns (
                campaign_id TEXT PRIMARY KEY,
                current_lock_id TEXT
            );
            CREATE TABLE campaign_lock_memberships (
                lock_id TEXT NOT NULL,
                package_id TEXT NOT NULL
            );
            CREATE TABLE campaign_state (
                campaign_id TEXT NOT NULL,
                namespace TEXT NOT NULL,
                state_key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                PRIMARY KEY (campaign_id, namespace, state_key)
            );
            CREATE TABLE actions (
                campaign_id TEXT NOT NULL,
                action_id TEXT NOT NULL,
                package_id TEXT NOT NULL,
                PRIMARY KEY (campaign_id, action_id)
            );
            CREATE TABLE mutations (
                campaign_id TEXT NOT NULL,
                action_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                namespace TEXT NOT NULL,
                state_key TEXT NOT NULL,
                before_json TEXT NOT NULL,
                after_json TEXT NOT NULL,
                PRIMARY KEY (campaign_id, action_id, ordinal)
            );
            